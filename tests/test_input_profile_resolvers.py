"""The three input-profile resolver leaves: lib/family_profiles (shared family x context map),
lib/yuzu_profiles (Eden/Citron, family-keyed), lib/ryujinx_profiles (per player), plus the new
per-game tier in lib/cemu_profiles.

These are pure policy + filesystem readers on the launch hot path, so the contract under test is:
an unset / husked / stale pick always degrades to "leave it resting", never raises.

Run:  python3 -m unittest tests.test_input_profile_resolvers -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import cemu_profiles, family_profiles, ryujinx_profiles, yuzu_profiles
from tests._fakes import sd

# guids only matter to the writer; the resolvers key on vidpid + injected family
_G = "03000000000000000000000000000000"


def _pad(vidpid, name=""):
    return sd(0, vidpid, _G, name)


# ── the shared family x context map ──────────────────────────────────────────────
class FamilyMap(unittest.TestCase):
    CFG = {"profile_map": {"docked": {"DualSense": "DS 1", "Xbox": "  "},
                           "handheld": {"Steam Deck": "Deck"}}}

    def test_lookup_and_blank_is_unset(self):
        self.assertEqual(family_profiles.resolve(self.CFG, "DualSense", "docked"), "DS 1")
        self.assertIsNone(family_profiles.resolve(self.CFG, "Xbox", "docked"))     # blank -> unset
        self.assertIsNone(family_profiles.resolve(self.CFG, "Nope", "docked"))
        self.assertIsNone(family_profiles.resolve(self.CFG, None, "docked"))

    def test_husks_degrade_never_raise(self):
        for cfg in ({}, {"profile_map": "nope"}, {"profile_map": {"docked": 7}},
                    {"profile_map": {"docked": {"DualSense": 7}}}, None, "nope"):
            self.assertIsNone(family_profiles.resolve(cfg, "DualSense", "docked"))

    def test_handheld_never_inherits_docked_by_default(self):
        self.assertIsNone(family_profiles.resolve(self.CFG, "DualSense", "handheld"))

    def test_mirror_is_opt_in_one_way_and_per_family(self):
        cfg = dict(self.CFG, handheld_mirrors_docked=True)
        self.assertEqual(family_profiles.resolve(cfg, "DualSense", "handheld"), "DS 1")  # inherited
        self.assertEqual(family_profiles.resolve(cfg, "Steam Deck", "handheld"), "Deck")  # explicit wins
        # one-way: a handheld-only family must NOT leak into docked
        self.assertIsNone(family_profiles.resolve(cfg, "Steam Deck", "docked"))

    def test_valid_stem_rejects_traversal(self):
        for bad in ("", "  ", ".hidden", "a/b", "a\\b", "../x", 7, None):
            self.assertFalse(family_profiles.valid_stem(bad), bad)
        self.assertTrue(family_profiles.valid_stem("DS 1"))


class NthBump(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "DS 1.ini").write_text("x", encoding="utf-8")
        (self.d / "DS 2.ini").write_text("x", encoding="utf-8")
        (self.d / "Solo.ini").write_text("x", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_bumps_only_when_the_derived_file_exists(self):
        self.assertEqual(family_profiles.nth("DS 1", 0, self.d, ".ini"), "DS 1")
        self.assertEqual(family_profiles.nth("DS 1", 1, self.d, ".ini"), "DS 2")
        self.assertEqual(family_profiles.nth("DS 1", 2, self.d, ".ini"), "DS 1")   # DS 3 absent
        self.assertEqual(family_profiles.nth("Solo", 1, self.d, ".ini"), "Solo")   # no trailing num
        self.assertIsNone(family_profiles.nth(None, 1, self.d, ".ini"))

    def test_list_stems_skips_dotfiles(self):
        (self.d / ".hidden.ini").write_text("x", encoding="utf-8")
        self.assertEqual(family_profiles.list_stems(self.d, ".ini"), ["DS 1", "DS 2", "Solo"])
        self.assertEqual(family_profiles.list_stems(self.d / "gone", ".ini"), [])


# ── Eden / Citron: family -> (vidpid, port ordinal) ──────────────────────────────
class YuzuProfileKeys(unittest.TestCase):
    DS, DS4A, DS4B, DECK = "054c:0ce6", "054c:05c4", "054c:09cc", "28de:1205"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        for s in ("DS 1", "DS 2", "DS4 1", "DS4 2"):
            (self.d / f"{s}.ini").write_text("x", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.cfg = {"profile_map": {"docked": {"DualSense": "DS 1", "DualShock 4": "DS4 1"}}}

    def _fam(self, d):
        return {self.DS: "DualSense", self.DS4A: "DualShock 4",
                self.DS4B: "DualShock 4"}.get(d.vidpid)

    def test_two_identical_pads_get_distinct_profiles_and_ordinals(self):
        keys = yuzu_profiles.profile_keys([_pad(self.DS), _pad(self.DS)],
                                          self.cfg, "docked", self.d, self._fam)
        self.assertEqual(keys, {(self.DS, 0): "DS 1", (self.DS, 1): "DS 2"})

    def test_one_family_spanning_two_pids_still_bumps_the_family_ordinal(self):
        # THE case the two counters exist for: a DS4 v1 and a DS4 v2 are family ordinals 0 and 1,
        # but each is vid:pid ordinal 0 -- so the keys differ from the stems' numbering.
        keys = yuzu_profiles.profile_keys([_pad(self.DS4A), _pad(self.DS4B)],
                                          self.cfg, "docked", self.d, self._fam)
        self.assertEqual(keys, {(self.DS4A, 0): "DS4 1", (self.DS4B, 0): "DS4 2"})

    def test_unknown_family_pad_still_bumps_the_vidpid_counter(self):
        # A pad with no family must NOT desync the per-vid:pid ordinals assign_devices computes.
        # The stranger MUST share the DualSense vid:pid, or this cannot fail: the counter is keyed
        # by vid:pid, so a differently-identified pad (e.g. the Deck) leaves it untouched either way.
        # routing.family_of also reads the NAME, so same-pid pads legitimately classify differently.
        stranger = sd(0, self.DS, _G, "Some Unrecognised Sony Thing")

        def fam(d):
            return None if d.name.startswith("Some Unrecognised") else self._fam(d)

        keys = yuzu_profiles.profile_keys([_pad(self.DS), stranger, _pad(self.DS)],
                                          self.cfg, "docked", self.d, fam)
        # ordinals 0 and 2 -- the stranger consumed ordinal 1. If the bump moved after the
        # family guard, this would be {0: "DS 1", 1: "DS 2"} and the launch would mis-seat.
        self.assertEqual(keys, {(self.DS, 0): "DS 1", (self.DS, 2): "DS 2"})

    def test_different_pid_pads_keep_separate_counters(self):
        keys = yuzu_profiles.profile_keys([_pad(self.DS), _pad(self.DS4A), _pad(self.DS)],
                                          self.cfg, "docked", self.d, self._fam)
        self.assertEqual(keys, {(self.DS, 0): "DS 1", (self.DS4A, 0): "DS4 1",
                                (self.DS, 1): "DS 2"})

    def test_unset_context_yields_no_picks(self):
        self.assertEqual(yuzu_profiles.profile_keys([_pad(self.DS)], self.cfg, "handheld",
                                                    self.d, self._fam), {})

    def test_family_of_raising_is_survivable(self):
        def boom(_d):
            raise RuntimeError("no")
        self.assertEqual(yuzu_profiles.profile_keys([_pad(self.DS)], self.cfg, "docked",
                                                    self.d, boom), {})

    def test_empty_and_none_pads(self):
        for pads in ([], None):
            self.assertEqual(yuzu_profiles.profile_keys(pads, self.cfg, "docked",
                                                        self.d, self._fam), {})

    def test_input_dir_derives_from_template_profile(self):
        self.assertEqual(yuzu_profiles.input_dir("eden", {"template_profile": "/x/y/input/T.ini"}),
                         Path("/x/y/input"))
        self.assertEqual(yuzu_profiles.input_dir("citron", {}),
                         Path("~/.config/citron/input").expanduser())


# ── Ryujinx: per player, mapping-only bake ───────────────────────────────────────
class RyujinxSeats(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self._write("Pro 1", "ProController")
        self._write("Pro 2", "ProController")
        self._write("Joy", "JoyconPair")
        self._write("Handheld", "Handheld")
        (self.d / "broken.json").write_text("{not json", encoding="utf-8")
        self.cfg = {"profile_map": {"docked": {"p1": "Pro 1", "p2": "Joy", "p3": "gone"}}}

    def _write(self, stem, ctype):
        (self.d / f"{stem}.json").write_text(json.dumps({
            "id": "0-DEADBEEF", "backend": "GamepadSDL3", "player_index": "Player7",
            "name": "Some Pad", "controller_type": ctype,
            "left_joycon": {"button_a": "A"}, "right_joycon": {"button_b": "B"},
            "left_joycon_stick": {"joystick": "Left", "invert_stick_x": True},
            "right_joycon_stick": {"joystick": "Right"}, "motion": {"enable_motion": True},
            "rumble": {"enable_rumble": False}, "led": {"enable_led": True}, "version": 1,
            "deadzone_left": 0.1, "deadzone_right": 0.1, "range_left": 1.0, "range_right": 1.0,
            "trigger_threshold": 0.5,
        }), encoding="utf-8")

    def test_mapping_never_carries_device_identity(self):
        m = ryujinx_profiles.seat_mapping(self.d / "Pro 1.json", "Player1")
        for k in ("id", "backend", "player_index", "name"):
            self.assertNotIn(k, m)
        self.assertEqual(m["left_joycon"], {"button_a": "A"})
        # the stick objects carry source + invert, which is why the old page's 6 selectors go away
        self.assertEqual(m["left_joycon_stick"], {"joystick": "Left", "invert_stick_x": True})

    def test_controller_type_is_baked_when_it_suits_the_slot(self):
        self.assertEqual(
            ryujinx_profiles.seat_mapping(self.d / "Joy.json", "Player2")["controller_type"],
            "JoyconPair")
        self.assertEqual(
            ryujinx_profiles.seat_mapping(self.d / "Handheld.json", "Handheld")["controller_type"],
            "Handheld")

    def test_the_type_clamp_works_in_BOTH_directions(self):
        # Handheld must not land on a player slot ...
        self.assertNotIn("controller_type",
                         ryujinx_profiles.seat_mapping(self.d / "Handheld.json", "Player1"))
        # ... and a normal type must not land on the Handheld slot. This is the MAIN path: every
        # profile a user authors is ProController, and seat_mappings mirrors P1 onto Handheld, so a
        # one-way clamp rewrote that slot's type on every ordinary pick.
        for stem in ("Pro 1", "Joy"):
            self.assertNotIn("controller_type",
                             ryujinx_profiles.seat_mapping(self.d / f"{stem}.json", "Handheld"),
                             stem)

    def test_handheld_mirror_carries_the_mapping_but_not_the_type(self):
        out = ryujinx_profiles.seat_mappings(self.cfg, "docked", 1, self.d)
        self.assertEqual(out["Handheld"]["left_joycon"], out["Player1"]["left_joycon"])
        self.assertNotIn("controller_type", out["Handheld"])   # the resting "Handheld" type survives
        self.assertEqual(out["Player1"]["controller_type"], "ProController")

    def test_unreadable_profile_is_none(self):
        self.assertIsNone(ryujinx_profiles.seat_mapping(self.d / "broken.json", "Player1"))
        self.assertIsNone(ryujinx_profiles.seat_mapping(self.d / "absent.json", "Player1"))

    def test_seat_mappings_caps_at_the_pad_count(self):
        out = ryujinx_profiles.seat_mappings(self.cfg, "docked", 1, self.d)
        self.assertEqual(set(out), {"Player1", "Handheld"})       # p2 picked but no 2nd pad
        out = ryujinx_profiles.seat_mappings(self.cfg, "docked", 2, self.d)
        self.assertEqual(set(out), {"Player1", "Player2", "Handheld"})

    def test_handheld_slot_mirrors_player_one(self):
        out = ryujinx_profiles.seat_mappings(self.cfg, "docked", 2, self.d)
        self.assertEqual(out["Handheld"]["left_joycon"], out["Player1"]["left_joycon"])

    def test_stale_pick_leaves_that_seat_resting(self):
        out = ryujinx_profiles.seat_mappings(self.cfg, "docked", 4, self.d)
        self.assertNotIn("Player3", out)                          # "gone" has no file
        self.assertNotIn("Player4", out)                          # unset

    def test_unset_context_seats_nothing(self):
        self.assertEqual(ryujinx_profiles.seat_mappings(self.cfg, "handheld", 4, self.d), {})

    def test_profile_path_guards_traversal(self):
        # A REAL file one level up, so the guard is the only thing making this pass. Asserting on a
        # path that does not exist proves nothing: the unguarded code returns None for that anyway.
        (self.d.parent / "escape.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(ryujinx_profiles.profile_path(self.d, "../escape"))
        self.assertIsNone(ryujinx_profiles.profile_path(self.d, "absent"))
        self.assertIsNotNone(ryujinx_profiles.profile_path(self.d, "Pro 1"))

    def test_seat_and_player_keys(self):
        self.assertEqual(ryujinx_profiles.seat_key(2), "p2")
        self.assertEqual(ryujinx_profiles.player_index(2), "Player2")


# ── Cemu: the new per-game tier ─────────────────────────────────────────────────
class CemuPerGame(unittest.TestCase):
    TID = "0005000010111100"
    CFG = {"profile_map": {"docked": {"DualSense": "DualSense 1", "Xbox": "Xbox 1"},
                           "handheld": {"DualSense": "Deck DS"}},
           "pergame": {TID: {"docked": {"DualSense": "DualSense 2"},
                             "handheld": {"Xbox": "Xbox HH"}}}}

    def _slice(self, tid=TID):
        return cemu_profiles.pergame_slice(self.CFG, tid)

    def test_pergame_overrides_global_in_the_same_context(self):
        self.assertEqual(cemu_profiles.resolve(self._slice(), self.CFG, "DualSense", "docked"),
                         "DualSense 2")

    def test_unset_pergame_family_falls_back_to_global(self):
        self.assertEqual(cemu_profiles.resolve(self._slice(), self.CFG, "Xbox", "docked"), "Xbox 1")

    def test_unknown_title_degrades_to_global(self):
        for tid in (None, "", "dead" * 4):
            self.assertEqual(cemu_profiles.resolve(self._slice(tid), self.CFG, "DualSense", "docked"),
                             "DualSense 1")

    def test_titleid_is_case_insensitive(self):
        self.assertEqual(self._slice(self.TID.upper()), self.CFG["pergame"][self.TID])

    def test_husked_pergame_table_degrades(self):
        for cfg in ({"pergame": "nope"}, {"pergame": {self.TID: "nope"}}, {}):
            self.assertEqual(cemu_profiles.pergame_slice(cfg, self.TID), {})

    def test_handheld_still_never_inherits_docked_without_the_flag(self):
        # a family set ONLY docked, in neither the global nor the per-game handheld slice
        cfg = {"profile_map": {"docked": {"Xbox": "Xbox global"}}}
        self.assertIsNone(cemu_profiles.resolve({"docked": {"Xbox": "Xbox pergame"}},
                                                cfg, "Xbox", "handheld"))

    def test_pergame_handheld_pick_wins_on_its_own(self):
        # the CFG fixture sets Xbox for handheld ONLY per-game -- it must resolve without any mirror
        self.assertEqual(cemu_profiles.resolve(self._slice(), self.CFG, "Xbox", "handheld"),
                         "Xbox HH")

    def test_explicit_handheld_wins_over_the_mirror(self):
        cfg = dict(self.CFG, handheld_mirrors_docked=True)
        self.assertEqual(cemu_profiles.resolve(self._slice(), cfg, "DualSense", "handheld"),
                         "Deck DS")                      # global handheld entry, not the docked one

    def test_mirror_prefers_pergame_docked_over_global_docked(self):
        # With the mirror on and the whole handheld chain missing, the docked fallback must respect
        # specificity: a PER-GAME docked pick beats the GLOBAL docked pick for the same family.
        cfg = {"profile_map": {"docked": {"Xbox": "Xbox global"}}, "handheld_mirrors_docked": True}
        self.assertEqual(cemu_profiles.resolve({"docked": {"Xbox": "Xbox pergame"}},
                                               cfg, "Xbox", "handheld"), "Xbox pergame")
        # ...and with no per-game entry it still reaches the global docked one
        self.assertEqual(cemu_profiles.resolve({}, cfg, "Xbox", "handheld"), "Xbox global")

    def test_resolve_nth_derives_from_the_pergame_base(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "DualSense 2.xml").write_text("x", encoding="utf-8")
        (d / "DualSense 3.xml").write_text("x", encoding="utf-8")
        self.assertEqual(
            cemu_profiles.resolve_nth(self._slice(), self.CFG, "DualSense", "docked", 1, d),
            "DualSense 3")                                # bumped from the PER-GAME base, not global

    def test_global_entry_points_are_unchanged(self):
        self.assertEqual(cemu_profiles.profile_for(self.CFG, "DualSense", "docked"), "DualSense 1")
        self.assertIsNone(cemu_profiles.profile_for(self.CFG, "Nope", "docked"))


if __name__ == "__main__":
    unittest.main()
