"""citron_pg_input.* — per-game Input Profiles: 8 player selectors over input/*.ini, and the
BAKING (a named profile writes player_N_profile_name AND copies the profile's inline bindings +
\\default twins, so the player doesn't boot to keyboard), plus 'Use global' clearing the player."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil, citron_games
from lib.madsrv import citron_pg_input_cmds as pi
from lib.madsrv import rpc

_TID = "0100F2C0115B6000"
_G = "0500000000000000000000000000BBBB"
_PROFILE = (
    "[Controls]\n"
    f'button_a\\default=false\nbutton_a="engine:sdl,port:0,guid:{_G},button:1"\n'
    f'button_b\\default=false\nbutton_b="engine:sdl,port:0,guid:{_G},button:0"\n'
)


class CitronPgInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.custom = self.d / "custom"
        self.custom.mkdir()
        self.inp = self.d / "input"
        self.inp.mkdir()
        (self.inp / "DS4 P6.ini").write_text(_PROFILE, newline="")
        (self.inp / "Deck P1.ini").write_text(_PROFILE, newline="")
        self._oc = citron_games._CUSTOM
        citron_games._CUSTOM = self.custom
        self._oi = pi._INPUT_DIR
        pi._INPUT_DIR = self.inp
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        citron_games._CUSTOM = self._oc
        pi._INPUT_DIR = self._oi
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self):
        return rpc._METHODS["citron_pg_input.get"][0]({"titleid": _TID})

    def _set(self, key, value):
        return rpc._METHODS["citron_pg_input.set"][0]({"titleid": _TID, "key": key, "value": value})

    def _custom(self):
        return cfgutil.read_text(self.custom / f"{_TID}.ini") or ""

    def _cread(self, key):
        return cfgutil.ini_read(self._custom(), "Controls", key)

    def test_registered(self):
        self.assertIn("citron_pg_input.get", rpc._METHODS)
        self.assertIn("citron_pg_input.set", rpc._METHODS)

    def test_get_eight_players_all_global(self):
        rows = self._get()["groups"][0]["settings"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["options"][0], "Use global input configuration")
        self.assertIn("DS4 P6", rows[0]["options"])
        self.assertTrue(all(r["value"] == 0 for r in rows))   # all inherit global initially

    def test_select_profile_bakes_bindings(self):
        opts = self._get()["groups"][0]["settings"][0]["options"]
        idx = opts.index("DS4 P6")
        self._set("player_0", idx)
        # the name is written AND the profile's bindings are baked inline (+ \default twins)
        self.assertEqual(self._cread("player_0_profile_name"), '"DS4 P6"')
        self.assertEqual(self._cread("player_0_profile_name\\default"), "false")
        self.assertIn("button:1", self._cread("player_0_button_a"))
        self.assertEqual(self._cread("player_0_button_a\\default"), "false")
        self.assertEqual(self._cread("player_0_connected"), "true")   # else the pin boots disconnected
        self.assertEqual(self._cread("player_0_type"), "0")
        self.assertEqual(self._get()["groups"][0]["settings"][0]["value"], idx)

    def test_use_global_clears_player(self):
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self._set("player_0", 0)                          # back to Use global
        self.assertIsNone(self._cread("player_0_profile_name"))
        self.assertIsNone(self._cread("player_0_button_a"))
        self.assertEqual(self._get()["groups"][0]["settings"][0]["value"], 0)

    def test_other_players_untouched(self):
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self.assertIsNone(self._cread("player_1_button_a"))   # player 2 still global

    def test_bad_player_rejected(self):
        with self.assertRaises(rpc.RpcError):
            self._set("player_9", 1)


if __name__ == "__main__":
    unittest.main()
