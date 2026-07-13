"""Tests for the Wii Classic-Controller launch assigner (lib/dolphin_wii_pads).

Run:  python3 -m unittest tests.test_dolphin_wii_pads -v
"""
from __future__ import annotations

import unittest
from collections import Counter

from lib import dolphin_wii_pads as wpad
from lib import dolphin_wii_profiles
from lib.madsrv import dolphin_wii_pads_cmds as prefs

_DS4 = "054c:09cc"
_DS = "054c:0ce6"


class Assign(unittest.TestCase):
    def setUp(self):
        self._orig = (prefs.priority, prefs.hands_off, prefs.docked_default,
                      wpad._connected_index, dolphin_wii_profiles.profile_body,
                      dolphin_wii_profiles.profile_device)
        prefs.hands_off = lambda: False
        prefs.priority = lambda: []
        prefs.docked_default = lambda: ["DS4 1", "DS 1"]
        wpad._connected_index = lambda: (Counter({_DS4: 1, _DS: 1}),
                                         {"PS4 Controller": _DS4, "DualSense": _DS})
        dolphin_wii_profiles.profile_device = lambda n: {
            "DS4 1": "PS4 Controller", "DS4 2": "PS4 Controller", "DS 1": "DualSense"}.get(n)
        dolphin_wii_profiles.profile_body = lambda n: f"Device = X/{n}\nExtension = Classic\n"

    def tearDown(self):
        (prefs.priority, prefs.hands_off, prefs.docked_default,
         wpad._connected_index, dolphin_wii_profiles.profile_body,
         dolphin_wii_profiles.profile_device) = self._orig

    def test_priority_walk_fills_connected_slots(self):
        prefs.priority = lambda: ["DS4 1", "DS 1"]
        self.assertEqual(wpad.plan_assignment(), [(1, "DS4 1"), (2, "DS 1")])

    def test_docked_default_used_when_no_priority(self):
        prefs.priority = lambda: []
        self.assertEqual(wpad.plan_assignment(), [(1, "DS4 1"), (2, "DS 1")])

    def test_hands_off_returns_empty(self):
        prefs.hands_off = lambda: True
        prefs.priority = lambda: ["DS4 1"]
        self.assertEqual(wpad.plan_assignment(), [])

    def test_disconnected_profile_skipped(self):
        wpad._connected_index = lambda: (Counter({_DS4: 1}), {"PS4 Controller": _DS4})
        prefs.priority = lambda: ["DS 1", "DS4 1"]        # DS disconnected -> DS4 becomes P1
        self.assertEqual(wpad.plan_assignment(), [(1, "DS4 1")])

    def test_assign_text_fills_and_disables_unused(self):
        prefs.priority = lambda: ["DS4 1", "DS 1"]
        text = ("[Wiimote1]\nSource = 1\nGUN1\n[Wiimote2]\nSource = 1\nGUN2\n"
                "[Wiimote3]\nSource = 1\nSTALE3\n[Wiimote4]\nSource = 0\n[BalanceBoard]\nSource = 0\n")
        out, applied = wpad.assign_text(text)
        self.assertEqual(applied, [(1, "DS4 1"), (2, "DS 1")])
        self.assertIn("[Wiimote1]\nSource = 1\nDevice = X/DS4 1", out)
        self.assertIn("[Wiimote2]\nSource = 1\nDevice = X/DS 1", out)
        self.assertIn("[Wiimote3]\nSource = 0\n", out)          # unused slot turned OFF
        self.assertNotIn("STALE3", out)                         # its stale body dropped
        self.assertIn("[Wiimote4]\nSource = 0\n", out)
        self.assertIn("[BalanceBoard]\nSource = 0\n", out)      # never touched

    def test_assign_text_no_match_leaves_text_untouched(self):
        prefs.priority = lambda: ["DS4 1"]
        wpad._connected_index = lambda: (Counter(), {})         # nothing connected
        text = "[Wiimote1]\nSource = 1\nGUN1\n"
        out, applied = wpad.assign_text(text)
        self.assertEqual(applied, [])
        self.assertEqual(out, text)                             # no reshaping when nothing placed


if __name__ == "__main__":
    unittest.main()
