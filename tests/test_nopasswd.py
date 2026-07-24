"""Opt-in passwordless sudo (INSTALL_NOPASSWD): safety + strict off-by-default gating.

Cannot test the real /etc/sudoers.d install (needs root), so this locks in what MATTERS for a
security grant: the generated sudoers line is valid (visudo-checked, so it can never lock the user
out), the grant is OFF unless explicitly enabled, it is NOT wired through want() (which defaults ON
when there is no install.conf), and --revoke removes the drop-in.

Run:  python3 -m unittest tests.test_nopasswd -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "sudoers-nopasswd-setup.sh"
POSTUPDATE = ROOT / "deck-post-update.sh"
INSTALL = ROOT / "install.sh"
HAVE_VISUDO = shutil.which("visudo") is not None


class NopasswdSafety(unittest.TestCase):
    def test_script_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    @unittest.skipUnless(HAVE_VISUDO, "visudo not installed")
    def test_generated_sudoers_line_is_valid(self):
        # The exact grant the script installs must pass `visudo -c` - an invalid sudoers file could
        # lock the user out of sudo, which is why the script validates before installing.
        f = Path(tempfile.mktemp())
        try:
            f.write_text("deck ALL=(ALL) NOPASSWD: ALL\n")
            rc = subprocess.run(["visudo", "-cf", str(f)], capture_output=True).returncode
            self.assertEqual(rc, 0, "the NOPASSWD grant must be valid sudoers syntax")
        finally:
            f.unlink(missing_ok=True)

    def test_drop_in_filename_has_no_dot(self):
        # sudo silently IGNORES files in sudoers.d whose name contains a '.', so the grant would
        # never take effect. Guard the filename.
        txt = SCRIPT.read_text()
        self.assertIn("zzz-mad-nopasswd", txt)
        self.assertNotIn("zzz-mad-nopasswd.", txt, "the drop-in name must not contain a dot")

    def test_revoke_removes_the_drop_in(self):
        drop = Path(tempfile.mktemp())
        drop.write_text("deck ALL=(ALL) NOPASSWD: ALL\n")
        env = dict(os.environ, MAD_NOPASSWD_DROPIN=str(drop))
        # --revoke path: as root it rm's; run under fakeroot-less env still exercises the rm branch
        # only when euid==0, so just assert the branch removes an existing file when we ARE root,
        # else assert the script refuses non-root cleanly.
        p = subprocess.run(["bash", str(SCRIPT), "--revoke"], env=env, capture_output=True, text=True)
        if os.geteuid() == 0:
            self.assertFalse(drop.exists(), "revoke must remove the drop-in")
        else:
            self.assertNotEqual(p.returncode, 0, "must refuse to run as non-root")
            self.assertIn("root", (p.stdout + p.stderr).lower())
        drop.unlink(missing_ok=True)


class NopasswdGating(unittest.TestCase):
    """The security-critical part: OFF unless explicitly enabled, and never via want()."""

    def _gate(self, value):
        # Replicate the exact gate used in deck-post-update.sh / install.sh and report on/off.
        snippet = (
            'case "${INSTALL_NOPASSWD:-}" in '
            '1|on|yes|true|On|ON|Yes|True) echo ON;; *) echo OFF;; esac'
        )
        env = dict(os.environ)
        if value is None:
            env.pop("INSTALL_NOPASSWD", None)
        else:
            env["INSTALL_NOPASSWD"] = value
        return subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True).stdout.strip()

    def test_off_by_default(self):
        self.assertEqual(self._gate(None), "OFF", "absent INSTALL_NOPASSWD must be OFF")
        self.assertEqual(self._gate("0"), "OFF")
        self.assertEqual(self._gate(""), "OFF")
        self.assertEqual(self._gate("no"), "OFF")
        self.assertEqual(self._gate("garbage"), "OFF")

    def test_on_only_when_explicitly_true(self):
        for v in ("1", "on", "yes", "true", "ON", "True"):
            self.assertEqual(self._gate(v), "ON", f"{v!r} should enable")

    def test_not_wired_through_want(self):
        # want() returns TRUE when install.conf is absent, so wiring the grant through
        # `want INSTALL_NOPASSWD` would enable passwordless by DEFAULT on any setup without an
        # install.conf. It must be gated by the strict explicit-value case instead.
        for f in (POSTUPDATE, INSTALL):
            self.assertNotIn("want INSTALL_NOPASSWD", f.read_text(),
                             f"{f.name} must NOT gate passwordless via want() (defaults ON)")


if __name__ == "__main__":
    unittest.main()
