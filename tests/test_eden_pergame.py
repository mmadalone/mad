r"""eden_pg_*.* PER-GAME settings via the shared Yuzu-fork engine (yuzu_pergame): create-on-demand
custom/<TID>.ini (no need to open the game's Properties in Eden first), the
\use_global/\default/value override triple, clear-to-inherit ("Inherit global" at index 0), and
bool rendered 3-way Inherit/Off/On. Eden keeps its OWN GROUPS (enum indices differ from Citron's).
Driven through the registered eden_pg_system / eden_pg_gfx RPC pages (eden_pergame.py).
Run: python3 -m unittest tests.test_eden_pergame -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import eden_cmds, eden_pergame  # noqa: F401  (import registers eden_pg_* methods)
from lib.madsrv import rpc

_TID = "0100000000abcd00"


class EdenPerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._custom = eden_cmds._CUSTOM
        eden_cmds._CUSTOM = self.d
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        eden_cmds._CUSTOM = self._custom
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, ns, verb, **params):
        return rpc._METHODS[f"{ns}.{verb}"][0](params)

    def _row(self, ns, key):
        g = self._call(ns, "get", titleid=_TID)
        return [r for grp in g["groups"] for r in grp["settings"] if r["key"] == key][0]

    def test_fresh_game_is_create_on_demand_all_inherit(self):
        g = self._call("eden_pg_system", "get", titleid=_TID)
        self.assertTrue(g["exists"])                                   # NOT exists:false (no "open Properties")
        self.assertFalse(eden_cmds.pergame_path(_TID).is_file())       # GET creates nothing
        row = self._row("eden_pg_system", "use_docked_mode")           # docked_mode bool rendered 3-way
        self.assertEqual(row["options"][0], "Inherit global")
        self.assertEqual(row["value"], 0)

    def test_set_writes_override_triple(self):
        self._call("eden_pg_system", "set", titleid=_TID, key="use_docked_mode", value=2)  # On
        t = eden_cmds.pergame_path(_TID).read_text()
        self.assertIn("use_docked_mode\\use_global = false", t)
        self.assertIn("use_docked_mode\\default = false", t)
        self.assertIn("use_docked_mode = 1", t)                        # docked stored 1/0
        self.assertEqual(self._row("eden_pg_system", "use_docked_mode")["value"], 2)

    def test_enum_override_and_inherit_clear(self):
        self._call("eden_pg_gfx", "set", titleid=_TID, key="scaling_filter", value=2)   # 1-based (0=Inherit)
        self.assertEqual(self._row("eden_pg_gfx", "scaling_filter")["value"], 2)
        # clear back to inherit
        self._call("eden_pg_gfx", "set", titleid=_TID, key="scaling_filter", value=0)
        t = eden_cmds.pergame_path(_TID).read_text() if eden_cmds.pergame_path(_TID).is_file() else ""
        self.assertNotIn("scaling_filter\\use_global", t)
        self.assertEqual(self._row("eden_pg_gfx", "scaling_filter")["value"], 0)

    def test_summary_reflects_override(self):
        self.assertEqual(eden_cmds._summary(_TID), "")
        self._call("eden_pg_system", "set", titleid=_TID, key="use_docked_mode", value=2)
        self.assertEqual(eden_cmds._summary(_TID), "Custom: settings")

    def test_bad_titleid_rejected(self):
        with self.assertRaises(rpc.RpcError):
            self._call("eden_pg_system", "get", titleid="../evil")


if __name__ == "__main__":
    unittest.main()
