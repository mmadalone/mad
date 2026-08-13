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
        # No type anywhere (neither this per-game file nor a global qt-config.ini exists in
        # this fixture) -> the bake carries none forward. Was hardcoded to "0" (Pro
        # Controller), which silently downgraded any other type on every bake (audit
        # phase-5 site 4) -- see test_select_profile_preserves_existing_type /
        # test_select_profile_inherits_type_from_global below for the cases where a type
        # DOES exist and must survive.
        self.assertIsNone(self._cread("player_0_type"))
        self.assertEqual(self._get()["groups"][0]["settings"][0]["value"], idx)

    def test_select_profile_preserves_existing_type(self):
        # A player already carrying an EXPLICIT non-default type (set by the user in
        # Citron's own Controls dialog, e.g. GameCube=5) must survive a profile bake
        # untouched - this is the bug the review reproduced: type was being hardcoded back
        # to "0".
        pg = citron_games.pergame_path(_TID)
        pg.write_text(
            "[Controls]\nplayer_0_type\\default=false\nplayer_0_type=5\n", newline="")
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self.assertEqual(self._cread("player_0_type"), "5")
        self.assertEqual(self._cread("player_0_type\\default"), "false")

    def test_select_profile_inherits_type_from_global(self):
        # No per-game type yet (first-ever pick on this game), but the GLOBAL qt-config.ini
        # has one for this player -> the bake must carry that one forward (what the player
        # inherits today), not silently reset it to Pro Controller.
        (self.d / "qt-config.ini").write_text(
            "[Controls]\nplayer_0_type\\default=false\nplayer_0_type=4\n", newline="")
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self.assertEqual(self._cread("player_0_type"), "4")
        self.assertEqual(self._cread("player_0_type\\default"), "false")

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

    def test_use_global_on_fresh_game_is_noop(self):
        # Use-global on a game with NO per-game ini must be a no-op, NOT create an empty-[Controls] file.
        # (Mirrors tests/test_eden_pg_input.py - the fix was born in the Eden clone and backported.)
        self._set("player_1", 0)
        self.assertFalse(citron_games.pergame_path(_TID).is_file())

    def test_use_global_cycle_drops_empty_controls_keeps_other_sections(self):
        # Baking a profile then reverting the ONLY override must not leave a header-only
        # [Controls] behind - and must preserve the file's other (settings) sections.
        pg = citron_games.pergame_path(_TID)
        pg.write_text("[System]\nuse_multi_core = false\n", newline="")
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self._set("player_0", 0)
        text = self._custom()
        from lib import inifile
        self.assertIsNone(inifile.section_body(text, "Controls"))
        self.assertIn("use_multi_core = false", text)
        self.assertTrue(pg.is_file())                     # NEVER delete the file

    def test_use_global_cycle_on_controls_only_file_leaves_file(self):
        # Same cycle on a fresh game (file born from the bake): the emptied [Controls]
        # goes away but the file itself stays (house rule: no deletions).
        opts = self._get()["groups"][0]["settings"][0]["options"]
        self._set("player_0", opts.index("DS4 P6"))
        self._set("player_0", 0)
        pg = citron_games.pergame_path(_TID)
        self.assertTrue(pg.is_file())
        from lib import inifile
        self.assertIsNone(inifile.section_body(self._custom(), "Controls"))

    def test_bad_player_rejected(self):
        with self.assertRaises(rpc.RpcError):
            self._set("player_9", 1)


if __name__ == "__main__":
    unittest.main()
