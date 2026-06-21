"""Tests for the install.conf single source of truth: lib/install-conf.sh (shell want())
and lib/install_conf.py (the Python twin). Verifies they AGREE and the invariant
"absent install.conf => do everything". Run: python3 -m unittest tests.test_install_conf -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / "lib" / "install-conf.sh"


def _shell_want(key, conf_path) -> bool:
    """True iff the shell `want KEY` exits 0, with MAD_INSTALL_CONF=conf_path."""
    r = subprocess.run(["bash", "-c", f'. "{SHELL}"; want {key}'],
                       env={**os.environ, "MAD_INSTALL_CONF": str(conf_path)},
                       capture_output=True, text=True)
    return r.returncode == 0


class ShellWant(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".conf"); os.close(fd)
        self.tc = Path(p)
        self.tc.write_text("INSTALL_THEME=1\nINSTALL_SAMBA=0\nINSTALL_SUSPEND=auto\n"
                           "ON_T=on\nYES_T=yes\nTRUE_T=true\nNO_T=no\nOFF_T=off\nEMPTY_T=\n")

    def tearDown(self):
        self.tc.unlink(missing_ok=True)

    def test_truthy(self):
        for k in ("INSTALL_THEME", "INSTALL_SUSPEND", "ON_T", "YES_T", "TRUE_T"):
            self.assertTrue(_shell_want(k, self.tc), k)

    def test_falsy(self):
        for k in ("INSTALL_SAMBA", "NO_T", "OFF_T", "EMPTY_T", "MISSING_KEY"):
            self.assertFalse(_shell_want(k, self.tc), k)

    def test_absent_conf_does_everything(self):
        missing = self.tc.parent / "definitely-absent-xyz.conf"
        for k in ("INSTALL_SAMBA", "ANYTHING"):
            self.assertTrue(_shell_want(k, missing), k)


class PyConf(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from lib import install_conf as ic
        self.ic = ic
        fd, p = tempfile.mkstemp(suffix=".conf"); os.close(fd)
        self.tc = Path(p)
        self.tc.write_text("# c\nINSTALL_THEME=1\nINSTALL_SAMBA=0\n"
                           "INSTALL_SUSPEND=auto\nFORCE_SHOW_XARCADE=1\n")

    def tearDown(self):
        self.tc.unlink(missing_ok=True)

    def test_load_and_want_parity(self):
        c = self.ic.load(self.tc)
        self.assertEqual(c["INSTALL_THEME"], "1")
        self.assertTrue(self.ic.want("INSTALL_THEME", c))
        self.assertTrue(self.ic.want("INSTALL_SUSPEND", c))   # auto counts as yes
        self.assertFalse(self.ic.want("INSTALL_SAMBA", c))
        self.assertFalse(self.ic.want("MISSING", c))

    def test_absent_file_true(self):
        os.environ["MAD_INSTALL_CONF"] = str(self.tc.parent / "absent-xyz.conf")
        try:
            self.assertTrue(self.ic.want("INSTALL_SAMBA"))   # no file => do everything
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_set_value_preserves(self):
        self.ic.set_value("INSTALL_SAMBA", "1", self.tc)            # update in place
        self.ic.set_value("FORCE_HIDE_BEZELPROJECT", "1", self.tc)  # append
        t = self.tc.read_text()
        self.assertIn("# c", t)                       # comment kept
        self.assertIn("FORCE_SHOW_XARCADE=1", t)      # other key kept
        self.assertIn("INSTALL_SAMBA=1", t)
        self.assertNotIn("INSTALL_SAMBA=0", t)
        self.assertIn("FORCE_HIDE_BEZELPROJECT=1", t)


if __name__ == "__main__":
    unittest.main()
