"""Ryujinx GLOBAL named-profile input picker (ryujinx.selector_set key=profile). Picking a saved
profile bakes ONLY its mapping subtree into the player's global input_config entry, preserving the
slot's device identity (id/backend/player_index).

NOTE: the PER-GAME input page (ryujinx_pg_input.*) was REMOVED. A Ryujinx profile is a
device+mapping PIN; MAD's bake copied only the mapping and left the slot's (cloned) device, and the
launch router reassigned devices by the global pads->players order anyway -- so 'pick DS for P1'
never bound the DS. Device -> player is owned by the global Controllers -> pads -> players routing.
Run: python3 -m unittest tests.test_ryujinx_pg_input -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard, staterev
from lib.madsrv import rpc, ryujinx_json
from lib.madsrv import ryujinx_cmds as rc
from lib.madsrv import ryujinx_input_cmds  # noqa: F401  (register methods)
from lib.madsrv.rpc import RpcError

TID = "0100abcd0000f000"
GLOBAL = {"input_config": [{"player_index": "Player1", "id": "0-real", "backend": "GamepadSDL3",
                            "left_joycon": {"button_l": "L"}, "right_joycon": {"button_a": "A"}}],
          "use_input_global_config": False}
PROFILE = {"left_joycon": {"button_l": "CustomL"}, "right_joycon": {"button_a": "CustomA"},
           "left_joycon_stick": {"joystick": "Left"}, "id": "9-x", "backend": "GamepadSDL3",
           "player_index": "Player5", "controller_type": "JoyconPair"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "Config.json"
        self.cfg.write_text(json.dumps(GLOBAL))
        (self.d / "profiles" / "controller").mkdir(parents=True)
        (self.d / "profiles" / "controller" / "WiiU Pro 1.json").write_text(json.dumps(PROFILE))
        self._c, self._g = ryujinx_json.CONFIG, rc._GAMES_DIR
        ryujinx_json.CONFIG = self.cfg
        rc._GAMES_DIR = self.d / "games"
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda n: False
        self._bump = staterev.bump
        staterev.bump = lambda n: None
        ryujinx_input_cmds._buf.reset()   # fresh buffer per case (module-level singleton)

    def tearDown(self):
        ryujinx_json.CONFIG, rc._GAMES_DIR = self._c, self._g
        proc_guard.emulator_running = self._run
        staterev.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)


class GlobalProfilePicker(_Base):
    def _sel(self, **p):
        # Picking a profile is buffered now: selector_set stages the bake, input_save commits it.
        r = rpc._METHODS["ryujinx.selector_set"][0]({"player": "Player1", "key": "profile", **p})
        rpc._METHODS["ryujinx.input_save"][0]({})
        return r

    def test_profile_listed(self):
        g = rpc._METHODS["ryujinx.input_get"][0]({"player": "Player1"})
        sel = next(s for s in g["selectors"] if s["key"] == "profile")
        self.assertEqual([o["value"] for o in sel["options"]], ["Default", "WiiU Pro 1"])

    def test_bake_preserves_identity(self):
        self._sel(value="WiiU Pro 1")
        p1 = json.loads(self.cfg.read_text())["input_config"][0]
        self.assertEqual(p1["left_joycon"]["button_l"], "CustomL")   # mapping baked
        self.assertEqual(p1["id"], "0-real")                         # device identity preserved
        self.assertEqual(p1["player_index"], "Player1")
        self.assertNotIn("controller_type", p1)                      # identity fields NOT copied

    def test_default_is_noop(self):
        self.assertEqual(self._sel(value="Default")["value"], "Default")

    def test_unknown_profile_rejected(self):
        with self.assertRaises(RpcError):
            self._sel(value="Nope")

    def test_pergame_input_method_unregistered(self):
        # The per-game input page was removed -> its RPC methods must no longer be registered.
        self.assertNotIn("ryujinx_pg_input.get", rpc._METHODS)
        self.assertNotIn("ryujinx_pg_input.set", rpc._METHODS)


if __name__ == "__main__":
    unittest.main()
