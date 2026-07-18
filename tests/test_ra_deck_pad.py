"""lib/ra_deck_pad.py -- the relocated Deck-pad sdl2 gameplay table.

_GAMEPAD moved here from the retired ra_handheld_input rail; it is the single source for
ra_profiles.SDL_SEMANTIC_TABLE. Guard its shape so a future edit can't silently drift the sdl2
number space (a wrong value = a mis-bound or unbound handheld control).
Run: python3 -m unittest tests.test_ra_deck_pad -v
"""
from __future__ import annotations

import unittest

from lib import ra_deck_pad, ra_profiles


class RaDeckPad(unittest.TestCase):
    def test_table_shape(self):
        g = ra_deck_pad._GAMEPAD
        self.assertEqual(len(g), 24)
        self.assertTrue(all(k.startswith("input_player1_") for k in g))
        for v in g.values():                        # every value is a button index or signed axis token
            self.assertRegex(v, r"^[+-]?\d+$")

    def test_kernel_flip_sensitive_dpad_and_triggers(self):
        # sdl2 d-pad is buttons 11-14 (hats are dead under sdl2); L2/R2 are analog axes +4/+5.
        g = ra_deck_pad._GAMEPAD
        self.assertEqual(g["input_player1_left_btn"], "13")
        self.assertEqual(g["input_player1_right_btn"], "14")
        self.assertEqual(g["input_player1_l2_axis"], "+4")
        self.assertEqual(g["input_player1_r2_axis"], "+5")
        self.assertEqual(g["input_player1_a_btn"], "0")

    def test_it_is_the_source_of_the_sdl2_semantic_table(self):
        # ra_profiles re-keys _GAMEPAD (strip input_player1_) into SDL_SEMANTIC_TABLE; the two must
        # stay in lockstep so there is one source of truth for the sdl2 number space.
        derived = {k[len("input_player1_"):]: v for k, v in ra_deck_pad._GAMEPAD.items()}
        self.assertEqual(ra_profiles.SDL_SEMANTIC_TABLE, derived)


if __name__ == "__main__":
    unittest.main()
