"""
Smoke tests for install.sh's EmuDeck-OPTIONAL flow, via `--dry-run` against a
throwaway $HOME. Dry-run mutates nothing (every action is `run`-wrapped or
DRY_RUN-gated), so these are safe + headless.

Verifies the gate's three branches:
  * no EmuDeck            -> STANDALONE (seeds skeleton + custom_systems), no die
  * EmuDeck/ES-DE present -> uses it as-is, no standalone steps (backward compat)
  * --standalone          -> forces standalone even when EmuDeck is present

Run:  python3 -m unittest tests.test_install_standalone -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "install.sh"


def _run(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(["bash", str(INSTALL), "--dry-run", *args],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


class InstallStandalone(unittest.TestCase):
    def test_no_emudeck_goes_standalone(self):
        home = Path(tempfile.mkdtemp())            # empty: no ~/Emulation, no ~/ES-DE
        r = _run(home)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        out = r.stdout
        self.assertIn("Seeding ES-DE config (standalone)", out)
        self.assertIn("seed minimal custom_systems", out)
        self.assertIn("ALMOST DONE", out)                       # ran to the end
        self.assertNotIn("using it as-is", out)
        self.assertNotIn("set up EmuDeck first", out)           # no die

    def test_emudeck_present_uses_it(self):
        home = Path(tempfile.mkdtemp())
        (home / "Emulation").mkdir()
        (home / "ES-DE").mkdir()
        r = _run(home)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        out = r.stdout
        self.assertIn("using it as-is", out)
        self.assertNotIn("Seeding ES-DE config (standalone)", out)
        self.assertNotIn("seed minimal custom_systems", out)

    def test_force_standalone_flag(self):
        home = Path(tempfile.mkdtemp())
        (home / "Emulation").mkdir()
        (home / "ES-DE").mkdir()
        r = _run(home, "--standalone")
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        out = r.stdout
        self.assertIn("ignoring EmuDeck", out)
        self.assertIn("Seeding ES-DE config (standalone)", out)


if __name__ == "__main__":
    unittest.main()
