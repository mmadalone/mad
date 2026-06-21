"""Tests for the component picker's NON-INTERACTIVE path (lib/install-picker.sh
mad_run_picker with MAD_PICKER_NOUI=1): a fresh install writes the defaults; a reconfigure
preserves existing values AND the panel's FORCE_* keys. (The whiptail UI itself needs
on-device eyes.) Run: python3 -m unittest tests.test_install_picker -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pick(conf_path, standalone="0", suspend="auto") -> str:
    subprocess.run(
        ["bash", "-c",
         f'. "{ROOT}/lib/install-picker.sh"; mad_run_picker "{ROOT}" {standalone} {suspend}'],
        env={**os.environ, "MAD_PICKER_NOUI": "1", "MAD_INSTALL_CONF": str(conf_path)},
        capture_output=True, text=True, check=True)
    return conf_path.read_text()


class Picker(unittest.TestCase):
    def test_fresh_defaults(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        t = _pick(conf)
        self.assertIn("MAD_STANDALONE=0", t)
        self.assertIn("INSTALL_THEME=1", t)
        self.assertIn("INSTALL_SINDEN=1", t)     # star feature default-on
        self.assertIn("INSTALL_SAMBA=0", t)
        self.assertIn("INSTALL_SUSPEND=auto", t)

    def test_reconfigure_preserves_existing_and_force_keys(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        conf.write_text("INSTALL_SAMBA=1\nFORCE_SHOW_XARCADE=1\n")
        t = _pick(conf, standalone="1", suspend="off")
        self.assertIn("INSTALL_SAMBA=1", t)        # existing value kept (not reset to default 0)
        self.assertIn("FORCE_SHOW_XARCADE=1", t)   # panel FORCE_* key preserved
        self.assertIn("MAD_STANDALONE=1", t)
        self.assertIn("INSTALL_SUSPEND=off", t)


if __name__ == "__main__":
    unittest.main()
