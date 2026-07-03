"""citron_pg_*.* — per-game inherit-aware settings: create-on-demand custom/<TID>.ini, the
\\use_global/\\default/value override triple, clear-to-inherit (Inherit global at index 0), and
bool rendered as 3-way Inherit/Off/On. Reuses the citron_settings descriptor groups."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil, citron_games
from lib.madsrv import citron_pergame as pg
from lib.madsrv import rpc

_TID = "0100000000010000"


class CitronPerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._orig_custom = citron_games._CUSTOM
        citron_games._CUSTOM = self.d
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        citron_games._CUSTOM = self._orig_custom
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self, ns):
        return rpc._METHODS[f"{ns}.get"][0]({"titleid": _TID})

    def _set(self, ns, key, value):
        return rpc._METHODS[f"{ns}.set"][0]({"titleid": _TID, "key": key, "value": value})

    def _rows(self, ns):
        return {s["key"]: s for g in self._get(ns)["groups"] for s in g["settings"]}

    def _ini(self):
        return cfgutil.read_text(self.d / f"{_TID}.ini") or ""

    def _disk(self, sec, key):
        return cfgutil.ini_read(self._ini(), sec, key)

    def test_all_pages_registered(self):
        for ns in pg.PG_PAGES:
            for verb in ("get", "set"):
                self.assertIn(f"{ns}.{verb}", rpc._METHODS)

    def test_fresh_game_is_all_inherit(self):
        p = self._get("citron_pg_gfx")
        self.assertTrue(p["exists"])                     # create-on-demand
        row = self._rows("citron_pg_gfx")["resolution_setup"]
        self.assertEqual(row["options"][0], "Inherit global")
        self.assertEqual(row["value"], 0)                # inherit

    def test_enum_override_writes_triple(self):
        # inherit-view index 5 = the 5th real option (_RESOLUTION[4] = 1.5x) -> stored "4".
        self._set("citron_pg_gfx", "resolution_setup", 5)
        self.assertEqual(self._disk("Renderer", "resolution_setup"), "4")
        self.assertEqual(self._disk("Renderer", "resolution_setup\\use_global"), "false")
        self.assertEqual(self._disk("Renderer", "resolution_setup\\default"), "false")
        self.assertEqual(self._rows("citron_pg_gfx")["resolution_setup"]["value"], 5)

    def test_clear_to_inherit_removes_triple(self):
        self._set("citron_pg_gfx", "resolution_setup", 5)
        self._set("citron_pg_gfx", "resolution_setup", 0)     # Inherit global
        self.assertIsNone(self._disk("Renderer", "resolution_setup"))
        self.assertIsNone(self._disk("Renderer", "resolution_setup\\use_global"))
        self.assertEqual(self._rows("citron_pg_gfx")["resolution_setup"]["value"], 0)

    def test_bool_three_way(self):
        row = self._rows("citron_pg_system")["use_multi_core"]
        self.assertEqual(row["options"], ["Inherit global", "Off", "On"])
        self.assertEqual(row["value"], 0)
        self._set("citron_pg_system", "use_multi_core", 2)    # On
        self.assertEqual(self._disk("Core", "use_multi_core"), "true")
        self.assertEqual(self._disk("Core", "use_multi_core\\use_global"), "false")
        self.assertEqual(self._rows("citron_pg_system")["use_multi_core"]["value"], 2)
        self._set("citron_pg_system", "use_multi_core", 0)    # back to inherit
        self.assertIsNone(self._disk("Core", "use_multi_core"))

    def test_docked_override_uses_one_zero(self):
        # use_docked_mode stores 1/0 (not true/false); On = index 2 -> "1".
        self._set("citron_pg_system", "use_docked_mode", 2)
        self.assertEqual(self._disk("System", "use_docked_mode"), "1")
        self._set("citron_pg_system", "use_docked_mode", 1)   # Off -> "0"
        self.assertEqual(self._disk("System", "use_docked_mode"), "0")

    def test_bad_titleid_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["citron_pg_gfx.get"][0]({"titleid": "../etc/passwd"})


if __name__ == "__main__":
    unittest.main()
