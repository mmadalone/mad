"""ryujinx_cfg.assign_devices — the SDL3-correct device assignment: per-GUID DUPLICATE RANK ids (not
the raw SDL enumeration index), the SDL name-CRC zeroed, backend PRESERVED (not forced SDL2), and
player_input_assignments kept in lockstep. Guards the multi-pad routing regression the Phase-A
review surfaced. Run: python3 -m unittest tests.test_ryujinx_cfg -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import ryujinx_cfg as rc
from lib.madsrv import ryujinx_json
from tests._fakes import sd

# 32-hex SDL GUIDs. Bytes 2-3 (chars 4-7) are the name-CRC and MUST be zeroed -> DECK maps to the
# live Deck id. Distinct vids in the guid so they are different models.
DECK = "0300abcdde2800000512000000026800"          # -> 00000003-28de-0000-0512-000000026800
DS = "030000004c050000cc09000000006800"
WIIU = "030000007e05000009200000000068 00".replace(" ", "")


class GuidAndRank(unittest.TestCase):
    def test_guid_string_zeroes_crc_matches_live(self):
        self.assertEqual(rc._guid_string(DECK), "00000003-28de-0000-0512-000000026800")
        # a DIFFERENT CRC in bytes 2-3 yields the SAME guid string (CRC zeroed)
        self.assertEqual(rc._guid_string("03001234" + DECK[8:]), rc._guid_string(DECK))

    def test_distinct_models_both_rank_zero(self):
        deck = sd(3, "28de:1205", DECK, "Deck")
        ds = sd(5, "054c:0ce6", DS, "DualSense")
        ids = rc._rank_ids([ds, deck])                      # order-independent
        self.assertTrue(ids[id(deck)].startswith("0-"))
        self.assertTrue(ids[id(ds)].startswith("0-"))       # NOT "5-" (the old SDL-index bug)

    def test_identical_models_rank_by_index(self):
        a = sd(4, "057e:2009", WIIU, "A")
        b = sd(2, "057e:2009", WIIU, "B")
        ids = rc._rank_ids([a, b])
        self.assertTrue(ids[id(b)].startswith("0-"))        # lower SDL index -> rank 0
        self.assertTrue(ids[id(a)].startswith("1-"))

    def test_bad_guid_length_raises(self):
        with self.assertRaises(ValueError):
            rc._guid_string("dead")


class AssignDevices(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "Config.json"
        self._c = ryujinx_json.CONFIG
        ryujinx_json.CONFIG = self.cfg
        import lib.staterev as sr
        self._b = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        ryujinx_json.CONFIG = self._c
        import lib.staterev as sr
        sr.bump = self._b
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, data):
        self.cfg.write_text(json.dumps(data))

    def _read(self):
        return json.loads(self.cfg.read_text())

    def test_preserves_backend_and_buttons(self):
        self._write({"input_config": [
            {"player_index": "Player1", "id": "old", "backend": "GamepadSDL3",
             "left_joycon": {"button_l": "X"}},
            {"player_index": "Handheld", "id": "old", "backend": "GamepadSDL2"}]})
        rc.assign_devices([sd(3, "28de:1205", DECK, "Deck")], config_path=self.cfg)
        d = self._read()
        self.assertEqual(d["input_config"][0]["id"], "0-00000003-28de-0000-0512-000000026800")
        self.assertEqual(d["input_config"][0]["backend"], "GamepadSDL3")     # preserved, NOT forced SDL2
        self.assertEqual(d["input_config"][0]["left_joycon"], {"button_l": "X"})   # buttons kept
        self.assertEqual(d["input_config"][1]["id"], d["input_config"][0]["id"])    # Handheld follows P1
        self.assertEqual(d["input_config"][1]["backend"], "GamepadSDL2")            # preserved

    def test_creates_player2_clone(self):
        self._write({"input_config": [
            {"player_index": "Player1", "id": "old", "backend": "GamepadSDL3",
             "left_joycon": {"button_a": "B"}}]})
        rc.assign_devices([sd(3, "28de:1205", DECK, "D"), sd(5, "054c:0ce6", DS, "S")],
                          config_path=self.cfg)
        p2 = next(e for e in self._read()["input_config"] if e["player_index"] == "Player2")
        self.assertTrue(p2["id"].startswith("0-"))          # distinct model -> rank 0
        self.assertEqual(p2["backend"], "GamepadSDL3")       # cloned P1's backend
        self.assertEqual(p2["left_joycon"], {"button_a": "B"})

    def test_pia_synced_in_lockstep(self):
        self._write({
            "input_config": [{"player_index": "Player1", "id": "old", "backend": "GamepadSDL3"}],
            "player_input_assignments": [{"player_index": "Player1", "enable_dynamic_input_swap": True,
                                          "devices": [{"type": "Controller", "id": "stale",
                                                       "profile_name": "foo"}]}]})
        rc.assign_devices([sd(3, "28de:1205", DECK, "D"), sd(5, "054c:0ce6", DS, "S")],
                          config_path=self.cfg)
        d = self._read()
        pia = {p["player_index"]: p for p in d["player_input_assignments"]}
        self.assertEqual(pia["Player1"]["devices"][0]["id"], d["input_config"][0]["id"])   # synced
        self.assertFalse(pia["Player1"]["enable_dynamic_input_swap"])                       # reset false
        self.assertIn("Player2", pia)                        # upserted for the new player

    def test_pia_not_introduced_when_absent(self):
        self._write({"input_config": [{"player_index": "Player1", "id": "old",
                                       "backend": "GamepadSDL3"}]})
        rc.assign_devices([sd(3, "28de:1205", DECK, "D")], config_path=self.cfg)
        self.assertNotIn("player_input_assignments", self._read())   # never add PIA to a config lacking it

    def test_no_player1_raises(self):
        self._write({"input_config": [{"player_index": "Player2", "id": "x",
                                       "backend": "GamepadSDL3"}]})
        with self.assertRaises(ValueError):
            rc.assign_devices([sd(3, "28de:1205", DECK, "D")], config_path=self.cfg)


if __name__ == "__main__":
    unittest.main()
