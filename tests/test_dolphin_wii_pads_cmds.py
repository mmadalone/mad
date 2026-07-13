"""Tests for the Wii Classic-Controller pads->players page backend (dolphin_wii_pads_cmds) and its
delegation from pads_cmds.

Run:  python3 -m unittest tests.test_dolphin_wii_pads_cmds -v
"""
from __future__ import annotations

import unittest

from lib import dolphin_wii_profiles
from lib.madsrv import dolphin_wii_pads_cmds as pc
from lib.madsrv import pads_cmds


class Backend(unittest.TestCase):
    def setUp(self):
        self.store: dict = {}
        self._orig = (pc._be, pc._set_pref, pc._connected_names,
                      dolphin_wii_profiles.list_profiles, dolphin_wii_profiles.profile_device,
                      pc.proc_guard.emulator_running)
        pc._be = lambda: dict(self.store)
        pc._set_pref = lambda k, v: (self.store.__setitem__(k, v) if v not in (None, [], False)
                                     else self.store.pop(k, None))
        pc._connected_names = lambda: {"DualSense Wireless Controller"}
        dolphin_wii_profiles.list_profiles = lambda: [
            "DS 1 = classic controller", "WiiU Pro 1 = classic controller",
            "Steamdeck = classic controller"]
        dolphin_wii_profiles.profile_device = lambda n: {
            "DS 1 = classic controller": "DualSense Wireless Controller",
            "WiiU Pro 1 = classic controller": "Nintendo Wii Remote Pro Controller",
            "Steamdeck = classic controller": "Steam Deck Controller"}.get(n)
        pc.proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        (pc._be, pc._set_pref, pc._connected_names,
         dolphin_wii_profiles.list_profiles, dolphin_wii_profiles.profile_device,
         pc.proc_guard.emulator_running) = self._orig

    def test_docked_list_excludes_handheld_steamdeck(self):
        r = pc._pads_get({"emu": "dolphin_wii"})
        ids = {p["id"] for p in r["pads"]}
        self.assertEqual(ids, {"DS 1 = classic controller", "WiiU Pro 1 = classic controller"})
        self.assertNotIn("Steamdeck = classic controller", ids)     # handheld profile kept out of docked
        self.assertEqual((r["emu"], r["players"]), ("dolphin_wii", 4))

    def test_connected_flag_and_dot(self):
        rows = {p["id"]: p for p in pc._pads_get({"emu": "dolphin_wii"})["pads"]}
        self.assertTrue(rows["DS 1 = classic controller"]["connected"])
        self.assertIn("●", rows["DS 1 = classic controller"]["label"])
        self.assertFalse(rows["WiiU Pro 1 = classic controller"]["connected"])

    def test_set_rejects_handheld_and_unknown(self):
        pc._pads_set({"emu": "dolphin_wii",
                      "order": ["WiiU Pro 1 = classic controller",
                                "Steamdeck = classic controller", "bogus"]})
        self.assertEqual(self.store["pads_priority"], ["WiiU Pro 1 = classic controller"])

    def test_docked_default_is_the_docked_profiles(self):
        self.assertEqual(set(pc.docked_default()),
                         {"DS 1 = classic controller", "WiiU Pro 1 = classic controller"})

    def test_hands_off_roundtrip(self):
        pc._pads_hands_off({"emu": "dolphin_wii", "value": True})
        self.assertTrue(self.store["pads_hands_off"])
        self.assertTrue(pc.hands_off())


class Delegation(unittest.TestCase):
    def test_pads_get_delegates_dolphin_wii(self):
        _o = pc._pads_get
        pc._pads_get = lambda params: {"delegated": params.get("emu")}
        try:
            self.assertEqual(pads_cmds._pads_get({"emu": "dolphin_wii"}), {"delegated": "dolphin_wii"})
        finally:
            pc._pads_get = _o

    def test_pads_set_and_hands_off_delegate(self):
        _g, _h = pc._pads_set, pc._pads_hands_off
        pc._pads_set = lambda params: {"set": params.get("emu")}
        pc._pads_hands_off = lambda params: {"ho": params.get("emu")}
        try:
            self.assertEqual(pads_cmds._pads_set({"emu": "dolphin_wii"}), {"set": "dolphin_wii"})
            self.assertEqual(pads_cmds._pads_hands_off({"emu": "dolphin_wii"}), {"ho": "dolphin_wii"})
        finally:
            pc._pads_set, pc._pads_hands_off = _g, _h


if __name__ == "__main__":
    unittest.main()
