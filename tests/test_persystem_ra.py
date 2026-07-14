"""Per-system RetroArch options (RA-hub "Per-system settings" section):
retroarch_settings._rasys_get / _rasys_set. Backed by lib.ra_options; writes go
to config/<Core>/<system>.cfg via retroarch_cfg.set_system_option (mocked here).
Run: python3 -m unittest tests.test_persystem_ra -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.madsrv import retroarch_settings as rs   # noqa: E402
from lib.madsrv.rpc import RpcError               # noqa: E402
from tests._ci import skip_on_ci                  # noqa: E402


class PerSystemRaGet(unittest.TestCase):
    def _get(self, system, cur=None):
        with mock.patch.object(rs.retroarch_cfg, "get_system_option",
                               side_effect=(cur or (lambda s, k: None))), \
             mock.patch.object(rs.retroarch_cfg, "core_dirs_for_system", return_value=["/x"]), \
             mock.patch.object(rs.proc_guard, "retroarch_running", return_value=False):
            return rs._rasys_get(system)

    def test_get_shape_all_bool(self):
        g = self._get("snes")
        self.assertTrue(g["exists"])
        self.assertFalse(g["running"])
        settings = [it for grp in g["groups"] for it in grp["settings"]]
        # snes gets the 3 universal options (no n64 glcore fix)
        self.assertEqual([it["key"] for it in settings],
                         ["bilinear", "integer_scale", "rewind"])
        self.assertTrue(all(it["type"] == "bool" and it["value"] is False for it in settings))

    def test_n64_includes_glcore_fix(self):
        keys = [it["key"] for grp in self._get("n64")["groups"] for it in grp["settings"]]
        self.assertIn("n64_menu_text", keys)

    def test_value_true_when_option_matches_on(self):
        g = self._get("snes", cur=lambda s, k: "true" if k == "video_smooth" else None)
        bil = next(it for grp in g["groups"] for it in grp["settings"] if it["key"] == "bilinear")
        self.assertIs(bil["value"], True)


class PerSystemRaSet(unittest.TestCase):
    def test_set_enable_writes_on_value(self):
        calls = []
        with mock.patch.object(rs.proc_guard, "retroarch_running", return_value=False), \
             mock.patch.object(rs.retroarch_cfg, "set_system_option",
                               side_effect=lambda s, k, v: calls.append((s, k, v))), \
             mock.patch.object(rs.retroarch_cfg, "get_system_option", return_value="true"):
            r = rs._rasys_set("snes", {"key": "bilinear", "value": "1"})
        self.assertEqual(calls, [("snes", "video_smooth", "true")])
        self.assertIs(r["value"], True)

    def test_set_disable_clears_key(self):
        calls = []
        with mock.patch.object(rs.proc_guard, "retroarch_running", return_value=False), \
             mock.patch.object(rs.retroarch_cfg, "set_system_option",
                               side_effect=lambda s, k, v: calls.append((s, k, v))), \
             mock.patch.object(rs.retroarch_cfg, "get_system_option", return_value=None):
            r = rs._rasys_set("snes", {"key": "bilinear", "value": "0"})
        self.assertEqual(calls, [("snes", "video_smooth", None)])   # None clears the key
        self.assertIs(r["value"], False)

    def test_set_refused_while_ra_running(self):
        with mock.patch.object(rs.proc_guard, "retroarch_running", return_value=True):
            with self.assertRaises(RpcError) as cm:
                rs._rasys_set("snes", {"key": "bilinear", "value": "1"})
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_set_unknown_option_raises(self):
        with mock.patch.object(rs.proc_guard, "retroarch_running", return_value=False):
            with self.assertRaises(RpcError):
                rs._rasys_set("snes", {"key": "bogus", "value": "1"})

    @skip_on_ci  # asserts against RA systems present on the live Deck
    def test_registration_present(self):
        from lib.madsrv.rpc import _METHODS
        rasys = [m for m in _METHODS if m.startswith("rasys_") and m.endswith(".get")]
        self.assertTrue(rasys)   # non-standalone systems exist on this device


if __name__ == "__main__":
    unittest.main()
