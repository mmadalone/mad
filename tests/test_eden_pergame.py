r"""eden.* PER-GAME settings via the shared Yuzu-fork engine (yuzu_pergame): create-on-demand
custom/<TID>.ini (no need to open the game's Properties in Eden first), the
\use_global/\default/value override triple, clear-to-inherit ("Inherit global" at index 0), and
bool rendered 3-way Inherit/Off/On. Eden keeps its OWN GROUPS (enum indices differ from Citron's).
Run: python3 -m unittest tests.test_eden_pergame -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import eden_cmds

_TID = "0100000000abcd00"


class EdenPerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._custom = eden_cmds._CUSTOM
        eden_cmds._CUSTOM = self.d
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False

    def tearDown(self):
        eden_cmds._CUSTOM = self._custom
        proc_guard.emulator_running = self._run
        shutil.rmtree(self.d, ignore_errors=True)

    def _row(self, key):
        g = eden_cmds._pergame_get(_TID)
        return [r for grp in g["groups"] for r in grp["settings"] if r["key"] == key][0]

    def test_fresh_game_is_create_on_demand_all_inherit(self):
        g = eden_cmds._pergame_get(_TID)
        self.assertTrue(g["exists"])                                   # NOT exists:false (no "open Properties")
        self.assertFalse(eden_cmds._pergame_path(_TID).is_file())      # GET creates nothing
        # docked_mode bool rendered 3-way, inheriting (index 0)
        self.assertEqual(self._row("use_docked_mode")["options"][0], "Inherit global")
        self.assertEqual(self._row("use_docked_mode")["value"], 0)

    def test_set_writes_override_triple(self):
        eden_cmds._pergame_set({"titleid": _TID, "key": "use_docked_mode", "value": 2})  # On
        t = eden_cmds._pergame_path(_TID).read_text()
        self.assertIn("use_docked_mode\\use_global = false", t)
        self.assertIn("use_docked_mode\\default = false", t)
        self.assertIn("use_docked_mode = 1", t)                        # docked stored 1/0
        self.assertEqual(self._row("use_docked_mode")["value"], 2)

    def test_enum_override_and_inherit_clear(self):
        eden_cmds._pergame_set({"titleid": _TID, "key": "scaling_filter", "value": 2})   # 1-based (0=Inherit)
        self.assertEqual(self._row("scaling_filter")["value"], 2)
        # clear back to inherit
        eden_cmds._pergame_set({"titleid": _TID, "key": "scaling_filter", "value": 0})
        t = eden_cmds._pergame_path(_TID).read_text() if eden_cmds._pergame_path(_TID).is_file() else ""
        self.assertNotIn("scaling_filter\\use_global", t)
        self.assertEqual(self._row("scaling_filter")["value"], 0)

    def test_summary_reflects_override(self):
        self.assertEqual(eden_cmds._summary(_TID), "")
        eden_cmds._pergame_set({"titleid": _TID, "key": "use_docked_mode", "value": 2})
        self.assertEqual(eden_cmds._summary(_TID), "Custom: settings")

    def test_bad_titleid_rejected(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            eden_cmds._get({"titleid": "../evil"})


if __name__ == "__main__":
    unittest.main()
