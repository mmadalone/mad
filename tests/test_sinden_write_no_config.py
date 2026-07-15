"""sinden.set_keys / camera.save must fail CLEANLY when the Sinden config is absent (a partial
install with no LightgunMono.exe.config): a structured RpcError('ENOENT'), not a bare
FileNotFoundError from set_many that rpc.py wraps into an EINTERNAL stderr traceback. The sibling
reader sinden_cfg.get() already guards its read with except OSError; the write path did not.

Regression for the 2026-07-15 review finding #34.
Run:  python3 -m unittest tests.test_sinden_write_no_config -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import sinden_cfg
from lib.madsrv import sinden_cmds
from lib.madsrv.rpc import RpcError


class SindenWriteNoConfig(unittest.TestCase):
    def setUp(self):
        self._saved = sinden_cfg.CONFIG

    def tearDown(self):
        sinden_cfg.CONFIG = self._saved

    def _absent(self):
        sinden_cfg.CONFIG = Path(tempfile.mkdtemp()) / "LightgunMono.exe.config"  # never created

    def test_set_keys_missing_config_raises_clean_enoent(self):
        self._absent()
        with self.assertRaises(RpcError) as cm:
            sinden_cmds._set_keys({"pairs": {"AutoFireDelay": "10"}})
        self.assertEqual(cm.exception.code, "ENOENT")

    def test_camera_save_missing_config_raises_clean_enoent(self):
        self._absent()
        with self.assertRaises(RpcError) as cm:
            sinden_cmds._camera_save({})
        self.assertEqual(cm.exception.code, "ENOENT")

    def test_require_config_no_raise_when_present(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "LightgunMono.exe.config"
        cfg.write_text("<configuration/>\n")
        sinden_cfg.CONFIG = cfg
        sinden_cmds._require_config()   # must NOT raise when the config exists


if __name__ == "__main__":
    unittest.main()
