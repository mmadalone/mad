"""Tests for lib/pacman-helpers.sh mad_pacman_install: the readonly -> keyring -> pacman ->
readonly sequence, the 'archlinux holo' keyring fix, -S vs --refresh -Sy, and the EXIT trap
that re-locks the immutable root EVEN when pacman fails. Uses stubbed privileged commands
(no real pacman/sudo). Run: python3 -m unittest tests.test_pacman_helper -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib" / "pacman-helpers.sh"


def _run(call, pacman_rc=0):
    d = Path(tempfile.mkdtemp())
    log = d / "calls.log"
    (d / "sudo").write_text('#!/bin/bash\nexec "$@"\n')
    (d / "steamos-readonly").write_text(f'#!/bin/bash\necho "readonly $1" >> "{log}"\n')
    # --list-keys prints NOTHING to stdout => helper treats the keyring as empty => init+populate.
    (d / "pacman-key").write_text(f'#!/bin/bash\necho "pacman-key $*" >> "{log}"\n')
    (d / "pacman").write_text(f'#!/bin/bash\necho "pacman $*" >> "{log}"\nexit {pacman_rc}\n')
    for f in ("sudo", "steamos-readonly", "pacman-key", "pacman"):
        (d / f).chmod(0o755)
    r = subprocess.run(["bash", "-c", f'. "{HELPER}"; {call}'],
                       env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}"},
                       capture_output=True, text=True)
    return r.returncode, (log.read_text() if log.exists() else "")


class PacmanHelper(unittest.TestCase):
    def test_default_sequence(self):
        rc, log = _run("mad_pacman_install python-evdev tk")
        self.assertEqual(rc, 0, log)
        lines = log.splitlines()
        self.assertEqual(lines[0], "readonly disable")
        self.assertEqual(lines[-1], "readonly enable")
        self.assertIn("pacman-key --populate archlinux holo", log)   # the fix (not holo-only)
        self.assertIn("pacman -S --needed --noconfirm python-evdev tk", log)
        self.assertNotIn("pacman -Sy", log)

    def test_refresh_uses_sy(self):
        rc, log = _run("mad_pacman_install --refresh samba")
        self.assertEqual(rc, 0, log)
        self.assertIn("pacman -Sy --needed --noconfirm samba", log)

    def test_trap_relocks_on_pacman_failure(self):
        rc, log = _run("mad_pacman_install foo", pacman_rc=1)
        self.assertNotEqual(rc, 0)
        self.assertEqual(log.splitlines()[-1], "readonly enable")    # re-locked despite failure

    def test_noop_when_no_packages(self):
        rc, log = _run("mad_pacman_install")
        self.assertEqual(rc, 0)
        self.assertEqual(log, "")


if __name__ == "__main__":
    unittest.main()
