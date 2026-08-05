"""Wii U (Cemu): the PLAYER tier in front of the pad-type tier.

Which pad is which player was already solved -- cemu_seat has always fed [pins] + [systems.wiiu.pins]
through routing.resolve_pins. What was missing is which PROFILE a given player gets: that was looked
up by pad TYPE, so two DualSense could not differ. A player row now wins, and the type map stays as
the fallback so an untouched Deck resolves exactly as before.

Keys are p1..p4 in the SAME profile_map.<context> table as the family rows (family_profiles.lookup
is key-agnostic), which is what lib/ryujinx_profiles already does.

Run: python3 -m unittest tests.test_cemu_player_tier -v
"""
from __future__ import annotations

import unittest

from lib import cemu_profiles


def _cfg(docked=None, handheld=None, mirror=False):
    cfg = {"profile_map": {"docked": dict(docked or {}), "handheld": dict(handheld or {})}}
    if mirror:
        cfg["handheld_mirrors_docked"] = True
    return cfg


class PlayerTier(unittest.TestCase):
    def test_seat_key_shape_matches_ryujinx(self):
        from lib import ryujinx_profiles
        self.assertEqual(cemu_profiles.seat_key(2), "p2")
        self.assertEqual(cemu_profiles.seat_key(2), ryujinx_profiles.seat_key(2))

    def test_a_player_pick_beats_the_pad_type(self):
        cfg = _cfg(docked={"DualSense": "DS type", "p2": "DS for player two"})
        self.assertEqual(cemu_profiles.resolve(None, cfg, "DualSense", "docked", seat=2),
                         "DS for player two")
        # the OTHER DualSense, on a player with no row, still gets the type profile
        self.assertEqual(cemu_profiles.resolve(None, cfg, "DualSense", "docked", seat=1), "DS type")

    def test_two_identical_pads_can_finally_differ(self):
        cfg = _cfg(docked={"DualSense": "shared", "p1": "mine", "p2": "theirs"})
        got = [cemu_profiles.resolve(None, cfg, "DualSense", "docked", seat=s) for s in (1, 2)]
        self.assertEqual(got, ["mine", "theirs"])

    def test_no_seat_resolves_exactly_as_before(self):
        cfg = _cfg(docked={"DualSense": "DS type", "p1": "ignored"})
        self.assertEqual(cemu_profiles.resolve(None, cfg, "DualSense", "docked"), "DS type")

    def test_per_game_player_beats_global_player(self):
        cfg = _cfg(docked={"p1": "global"})
        pg = {"docked": {"p1": "just this game"}}
        self.assertEqual(cemu_profiles.resolve(pg, cfg, "DualSense", "docked", seat=1),
                         "just this game")

    def test_per_game_player_beats_a_per_game_type_pick(self):
        cfg = _cfg(docked={})
        pg = {"docked": {"DualSense": "by type", "p1": "by player"}}
        self.assertEqual(cemu_profiles.resolve(pg, cfg, "DualSense", "docked", seat=1), "by player")

    def test_the_whole_ladder_in_order(self):
        # SCOPE is the outer axis, then player-before-type inside a scope. Scope has to be outer
        # because "per-game beats all-games" is what every per-game page in MAD promises: the other
        # way round, a global player row would quietly beat a pick made for one game.
        def r(pg, glob):
            return cemu_profiles.resolve(pg, _cfg(docked=glob), "DualSense", "docked", seat=1)

        glob = {"p1": "global player", "DualSense": "global type"}
        self.assertEqual(r({"docked": {"p1": "game player", "DualSense": "game type"}}, glob),
                         "game player")
        self.assertEqual(r({"docked": {"DualSense": "game type"}}, glob), "game type")
        self.assertEqual(r(None, glob), "global player")
        self.assertEqual(r(None, {"DualSense": "global type"}), "global type")
        self.assertIsNone(r(None, {}))

    def test_handheld_mirror_applies_to_players_too(self):
        cfg = _cfg(docked={"p1": "docked pick"}, handheld={}, mirror=True)
        self.assertEqual(cemu_profiles.resolve(None, cfg, "DualSense", "handheld", seat=1),
                         "docked pick")
        # and without the opt-in, handheld never inherits
        self.assertIsNone(cemu_profiles.resolve(None, _cfg(docked={"p1": "x"}), "DualSense",
                                                "handheld", seat=1))

    def test_a_player_pick_is_never_bumped_to_its_sibling(self):
        # The "<base> 2" bump exists so two pads of ONE TYPE get distinct device-bound profiles.
        # A player row already names one seat, so bumping it would load "DualSense 2" for a row
        # that plainly says "DualSense 1".
        cfg = _cfg(docked={"p2": "DualSense 1"})
        got = cemu_profiles.resolve_nth(None, cfg, "DualSense", "docked", 1, None, seat=2)
        self.assertEqual(got, "DualSense 1")

    def test_the_type_tier_is_still_bumped(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "DualSense 2.xml").write_text("x", encoding="utf-8")
            cfg = _cfg(docked={"DualSense": "DualSense 1"})
            self.assertEqual(
                cemu_profiles.resolve_nth(None, cfg, "DualSense", "docked", 1, d, seat=3),
                "DualSense 2")

    def test_husked_policy_degrades_rather_than_raising(self):
        for bad in ({"profile_map": "nope"}, {"profile_map": {"docked": "nope"}},
                    {"profile_map": {"docked": {"p1": 7}}}, {}):
            self.assertIsNone(cemu_profiles.resolve(None, bad, None, "docked", seat=1), bad)


class Pages(unittest.TestCase):
    def test_both_pages_offer_four_players_over_the_same_store(self):
        from lib.madsrv import cemu_input_cmds as cin
        self.assertEqual(cin._seat_keys(), ["p1", "p2", "p3", "p4"])
        # the page keys the rows by player but stores them in the family map's slice, so nothing
        # needed a second table or a second write path
        self.assertEqual([cemu_profiles.seat_key(s) for s in cin._SEATS], cin._seat_keys())


if __name__ == "__main__":
    unittest.main()
