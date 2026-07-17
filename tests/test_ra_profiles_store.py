"""P3.1 -- the ra_profiles editor STORE layer: pure dict transforms + a TOML round-trip.

These functions never touch the filesystem; the ra_profiles_cmds backend does the
localpolicy.load -> mutate -> localpolicy.dump round-trip. The two rules they encode are both
forced by routing.deep_merge (override-only, never remove): a base-seeded profile/map row can be
SHADOWED but not deleted, and unassign writes "" rather than removing a row.
"""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from lib import localpolicy, ra_profiles, routing

# A stand-in for the shipped base policy (what controller-policy.toml seeds).
BASE = {
    "ra_profiles": {
        "Gamepad": {"hotkeys": {"modifier": "l3", "rewind": "l2", "fast_forward": "r2",
                                "slowmotion": "r", "menu": "start", "quit": ""}},
        "Deck": {"hotkeys": {"modifier": "l3", "rewind": "l2", "fast_forward": "r2",
                             "slowmotion": "r", "menu": "select", "quit": "start"}},
    },
    "ra_profile_map": {"DualSense": "Gamepad", "Steam Deck": "Deck", "X-Arcade": "Arcade"},
}


def merged_with(local):
    # deep_merge MUTATES its first arg, so hand it a deepcopy (else BASE leaks across calls).
    return routing.deep_merge(copy.deepcopy(BASE), local)


class Names(unittest.TestCase):
    def test_valid_names(self):
        self.assertTrue(ra_profiles.valid_profile_name("Gamepad"))
        self.assertTrue(ra_profiles.valid_profile_name("My Pad!"))
        self.assertTrue(ra_profiles.valid_profile_name("  trimmed  "))   # stripped, still non-empty

    def test_invalid_names(self):
        self.assertFalse(ra_profiles.valid_profile_name(""))
        self.assertFalse(ra_profiles.valid_profile_name("   "))
        self.assertFalse(ra_profiles.valid_profile_name("x" * 41))
        self.assertFalse(ra_profiles.valid_profile_name("bad\nname"))    # control char
        self.assertFalse(ra_profiles.valid_profile_name("tab\tname"))
        self.assertFalse(ra_profiles.valid_profile_name("del\x7fname"))  # DEL: unescaped -> wipe risk


class ListAndClassify(unittest.TestCase):
    def test_list_profiles_sorted_case_insensitive(self):
        merged = merged_with({"ra_profiles": {"apex": {"hotkeys": {}}}})
        self.assertEqual(ra_profiles.list_profiles(merged), ["apex", "Deck", "Gamepad"])

    def test_list_profiles_missing_table(self):
        self.assertEqual(ra_profiles.list_profiles({}), [])

    def test_is_shipped(self):
        self.assertTrue(ra_profiles.is_shipped(BASE, "Gamepad"))
        self.assertFalse(ra_profiles.is_shipped(BASE, "MyPad"))
        self.assertFalse(ra_profiles.is_shipped({}, "Gamepad"))


class Create(unittest.TestCase):
    def test_create_adds_empty_profile_with_all_hotkey_fields(self):
        local = {}
        name = ra_profiles.create_profile(local, "MyPad", merged_with({}))
        self.assertEqual(name, "MyPad")
        hk = local["ra_profiles"]["MyPad"]["hotkeys"]
        self.assertEqual(set(hk), {f for f, _ in ra_profiles.HOTKEYS})
        self.assertTrue(all(v == "" for v in hk.values()))

    def test_create_trims_the_name(self):
        local = {}
        self.assertEqual(ra_profiles.create_profile(local, "  Spaced  ", merged_with({})), "Spaced")
        self.assertIn("Spaced", local["ra_profiles"])

    def test_create_rejects_duplicate_of_a_shipped_name(self):
        with self.assertRaises(ValueError):
            ra_profiles.create_profile({}, "Gamepad", merged_with({}))

    def test_create_rejects_duplicate_of_a_local_name(self):
        local = {"ra_profiles": {"Mine": {"hotkeys": {}}}}
        with self.assertRaises(ValueError):
            ra_profiles.create_profile(local, "Mine", merged_with(local))

    def test_create_rejects_invalid_name(self):
        with self.assertRaises(ValueError):
            ra_profiles.create_profile({}, "  ", merged_with({}))


class DeleteAndReset(unittest.TestCase):
    def test_delete_removes_profile_and_its_map_rows_only(self):
        local = {"ra_profiles": {"Mine": {"hotkeys": {}}},
                 "ra_profile_map": {"Xbox": "Mine", "DualSense": "Gamepad"}}
        ra_profiles.delete_profile(local, "Mine")
        self.assertNotIn("Mine", local["ra_profiles"])
        self.assertNotIn("Xbox", local["ra_profile_map"])          # pointed at Mine -> gone
        self.assertEqual(local["ra_profile_map"]["DualSense"], "Gamepad")   # untouched

    def test_reset_drops_local_shadow_only(self):
        # Editing a shipped profile shadows it in local; reset drops that shadow so merged reverts.
        local = {"ra_profiles": {"Gamepad": {"hotkeys": {"slowmotion": "l2"}}}}
        self.assertEqual(merged_with(local)["ra_profiles"]["Gamepad"]["hotkeys"]["slowmotion"], "l2")
        ra_profiles.reset_profile(local, "Gamepad")
        self.assertNotIn("Gamepad", local.get("ra_profiles", {}))
        self.assertEqual(merged_with(local)["ra_profiles"]["Gamepad"]["hotkeys"]["slowmotion"], "r")


class EditHotkeysAndSettings(unittest.TestCase):
    def test_set_hotkeys_writes_tokens(self):
        local = {}
        ra_profiles.set_hotkeys(local, "Gamepad", {"slowmotion": "l2", "quit": "start"})
        hk = local["ra_profiles"]["Gamepad"]["hotkeys"]
        self.assertEqual(hk["slowmotion"], "l2")
        self.assertEqual(hk["quit"], "start")

    def test_set_hotkeys_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            ra_profiles.set_hotkeys({}, "Gamepad", {"bogus": "l2"})

    def test_editing_a_shipped_hotkey_shadows_it_in_merged(self):
        local = {}
        ra_profiles.set_hotkeys(local, "Gamepad", {"slowmotion": "l2"})
        # deep_merge overrides ONE key, base keeps the rest
        gm = merged_with(local)["ra_profiles"]["Gamepad"]["hotkeys"]
        self.assertEqual(gm["slowmotion"], "l2")
        self.assertEqual(gm["rewind"], "l2")     # from base, untouched

    def test_set_setting_and_clear(self):
        local = {}
        ra_profiles.set_setting(local, "MyPad", "analog_dpad_mode", "1")
        self.assertEqual(local["ra_profiles"]["MyPad"]["settings"]["analog_dpad_mode"], "1")
        ra_profiles.set_setting(local, "MyPad", "analog_dpad_mode", "")
        self.assertNotIn("analog_dpad_mode", local["ra_profiles"]["MyPad"]["settings"])

    def test_set_setting_rejects_libretro_device(self):
        with self.assertRaises(ValueError):
            ra_profiles.set_setting({}, "MyPad", "libretro_device", "1")


class FamilyAssignment(unittest.TestCase):
    def test_assign_writes_the_map(self):
        local = {}
        ra_profiles.assign_family(local, "Xbox", "Gamepad")
        self.assertEqual(local["ra_profile_map"]["Xbox"], "Gamepad")

    def test_unassign_writes_empty_not_pop(self):
        # A base-seeded row (DualSense=Gamepad) cannot be removed; "" shadows it to "no profile".
        local = {}
        ra_profiles.unassign_family(local, "DualSense")
        self.assertEqual(local["ra_profile_map"]["DualSense"], "")
        merged = merged_with(local)
        self.assertEqual(merged["ra_profile_map"]["DualSense"], "")
        # and profile_name_for treats "" as no profile
        self.assertIsNone(ra_profiles.profile_name_for(merged, "DualSense"))


class TomlRoundTrip(unittest.TestCase):
    """The emitted local.toml must be valid TOML and reload to the same shape -- including a name
    that needs quoting (the localpolicy emitter has silently wiped overrides on this before)."""

    def test_round_trip_through_localpolicy(self):
        local = {}
        ra_profiles.create_profile(local, "My Pad!", merged_with({}))
        ra_profiles.set_hotkeys(local, "My Pad!", {"modifier": "l3", "slowmotion": "r"})
        ra_profiles.set_setting(local, "My Pad!", "analog_dpad_mode", "1")
        ra_profiles.assign_family(local, "X-Arcade", "My Pad!")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "controller-policy.local.toml"
            localpolicy.dump(p, local)
            back = localpolicy.load(p)
        self.assertEqual(back["ra_profiles"]["My Pad!"]["hotkeys"]["modifier"], "l3")
        self.assertEqual(back["ra_profiles"]["My Pad!"]["settings"]["analog_dpad_mode"], "1")
        self.assertEqual(back["ra_profile_map"]["X-Arcade"], "My Pad!")


if __name__ == "__main__":
    unittest.main()
