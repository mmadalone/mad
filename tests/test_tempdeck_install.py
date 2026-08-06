"""install.sh's temp-deck section: the fan-control SUDO GRANT must be opt-in only.

The grant installs a NOPASSWD sudoers rule (/etc/sudoers.d/zz-deck-fan) plus a
root-owned helper. want() returns TRUE when install.conf is absent -- the legacy
"do everything" default -- so gating this on a bare `want` would hand the grant to
every legacy install that never asked for it. It therefore requires an explicit,
recorded opt-in, mirroring why INSTALL_NOPASSWD is not routed through want() either.

Everything runs under --dry-run, which mutates nothing.

Run:  python3 -m unittest tests.test_tempdeck_install -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
GRANT_LINE = "--install-fan-helper"
OFF_LINE = "temp-deck fan control OFF"


class TempDeckGrantOptIn(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "ES-DE").mkdir()
        self.sandbox = self.home / "_fake-system"
        self.sandbox.mkdir()
        self.mad = self.home / "Emulation" / "tools" / "launchers"
        (self.mad / "lib").mkdir(parents=True)
        # the real gate implementation, so want() behaves exactly as it does live
        shutil.copy(ROOT / "lib" / "install-conf.sh", self.mad / "lib" / "install-conf.sh")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _conf(self, text: str | None):
        if text is None:
            (self.mad / "install.conf").unlink(missing_ok=True)
        else:
            (self.mad / "install.conf").write_text(text)

    def _run(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MAD_INSTALL_CONF"] = str(self.mad / "install.conf")
        # The fan-helper system paths are ABSOLUTE, so a fake $HOME cannot isolate them.
        # Without these the tests would read this machine's real /etc/sudoers.d and every
        # assertion would depend on whether the developer happens to have fan control on.
        env["MAD_TD_SUDOERS"] = str(self.sandbox / "zz-deck-fan")
        env["MAD_TD_HELPER"] = str(self.sandbox / "deck-fan-ctl")
        return subprocess.run(["bash", str(INSTALL), "--dry-run"],
                              capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_no_install_conf_never_grants(self):
        # THE SECURITY CASE: legacy install, no recorded choice -> no grant.
        self._conf(None)
        out = self._run().stdout
        self.assertIn(OFF_LINE, out)
        self.assertNotIn(GRANT_LINE, out)

    def test_opt_in_enables_the_grant(self):
        self._conf("INSTALL_TEMPDECK_FAN=1\n")
        out = self._run().stdout
        self.assertIn(GRANT_LINE, out)
        self.assertNotIn(OFF_LINE, out)

    def test_explicit_off_does_not_grant(self):
        self._conf("INSTALL_TEMPDECK_FAN=0\n")
        out = self._run().stdout
        self.assertIn(OFF_LINE, out)
        self.assertNotIn(GRANT_LINE, out)

    def test_conf_without_the_key_does_not_grant(self):
        # An install.conf written before this component existed must not opt in.
        self._conf("INSTALL_THEME=1\nINSTALL_SINDEN=1\n")
        out = self._run().stdout
        self.assertIn(OFF_LINE, out)
        self.assertNotIn(GRANT_LINE, out)

    def test_express_does_not_grant(self):
        # --express takes defaults with no UI; the default for this one is OFF.
        self._conf(None)
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MAD_INSTALL_CONF"] = str(self.mad / "install.conf")
        r = subprocess.run(["bash", str(INSTALL), "--dry-run", "--express"],
                           capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertNotIn(GRANT_LINE, r.stdout)


class GrantIsRevocable(TempDeckGrantOptIn):
    """Unticking the component must actually REVOKE, not silently leave the rule live.

    Found in review: the picker tick was one-way. Turning it off wrote
    INSTALL_TEMPDECK_FAN=0, took the else branch and printed "no sudo grant" while
    /etc/sudoers.d/zz-deck-fan was still on disk, and deck-post-update.sh kept
    reinstalling it after every SteamOS update because it keys on the $HOME marker
    alone. The only revocation control the user had was a no-op that reported success.
    """

    def _pretend_installed(self):
        marker = self.home / ".config" / "temp-deck" / "fan-helper-installed"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("installed\n")
        (self.sandbox / "zz-deck-fan").write_text("fake sudoers rule\n")
        tool = self.home / "bin" / "temp-deck.py"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("#!/usr/bin/env python3\n")
        tool.chmod(0o755)

    # Under --dry-run an ACTION appears as a "[dry-run] ..." line, while advice is just
    # warn text. That is the only reliable way to tell "revoked" from "mentioned".
    REVOKE_ACTION = "[dry-run] ~/bin/temp-deck.py --uninstall-fan-helper"

    def test_explicit_off_with_a_grant_present_revokes(self):
        self._pretend_installed()
        self._conf("INSTALL_TEMPDECK_FAN=0\n")
        out = self._run().stdout
        self.assertIn(self.REVOKE_ACTION, out, "an explicit OFF must actually revoke")
        self.assertNotIn(OFF_LINE, out,
                         "must not claim there is no grant while one is installed")

    def test_absent_key_with_a_grant_present_warns_but_does_not_revoke(self):
        # Not a decision, so do not silently tear down a working setup - but say so.
        self._pretend_installed()
        self._conf("INSTALL_THEME=1\n")
        out = self._run().stdout
        self.assertIn("not selected", out)
        self.assertNotIn(self.REVOKE_ACTION, out, "an absent key is not consent to revoke")
        self.assertNotIn(OFF_LINE, out)

    def test_no_grant_present_still_reports_cleanly(self):
        self._conf("INSTALL_TEMPDECK_FAN=0\n")
        out = self._run().stdout
        self.assertIn(OFF_LINE, out)
        self.assertNotIn(self.REVOKE_ACTION, out)


class PickerPersistsTheKey(unittest.TestCase):
    def test_picker_writes_tempdeck_key_defaulting_off(self):
        home = Path(tempfile.mkdtemp())
        try:
            mad = home / "mad"
            shutil.copytree(ROOT / "lib", mad / "lib")
            conf = mad / "install.conf"
            script = (f'. "{mad}/lib/install-picker.sh"\n'
                      f'MAD_PICKER_NOUI=1 MAD_INSTALL_CONF="{conf}" '
                      f'mad_run_picker "{mad}" 0 auto\n')
            r = subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = conf.read_text()
            self.assertIn("INSTALL_TEMPDECK_FAN=0", text.replace('"', ''),
                          "the picker must persist the key, defaulting OFF")
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
