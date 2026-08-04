"""Tests for the Wii Classic-Controller profile helper (lib/dolphin_wii_profiles).

Run:  python3 -m unittest tests.test_dolphin_wii_profiles -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_wii_profiles as wp

_CC = ("[Profile]\nDevice = SDL/0/DualSense Wireless Controller\nExtension = Classic\n"
       "Classic/Buttons/A = `Button E`\n")
_NUNCHUK = ("[Profile]\nDevice = SDL/0/Steam Deck Controller\nExtension = Nunchuk\n"
            "Buttons/A = `Button S`\nNunchuk/Buttons/C = `Button E`\n")
_SINDEN = "[Profile]\nDevice = evdev/0/SindenLightgun Mouse (Smoothed P1)\nIR/Up = `Full Axis 1-`\n"


class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._dir = wp._DIR
        wp._DIR = self.tmp
        (self.tmp / "DS 1 = classic controller.ini").write_text(_CC)
        (self.tmp / "Steamdeck = classic controller.ini").write_text(
            _CC.replace("DualSense Wireless Controller", "Steam Deck Controller"))
        (self.tmp / "Steamdeck = Wiimote + Nunchuk.ini").write_text(_NUNCHUK)
        (self.tmp / "Sinden Lightgun P1.ini").write_text(_SINDEN)          # excluded by default
        (self.tmp / "Wii_classic_controller.ini").write_text(_CC)          # excluded (Wii_*) always
        (self.tmp / "NoExt.ini").write_text("[Profile]\nDevice = SDL/0/X\n")   # no Extension line
        (self.tmp / "NotAProfile.ini").write_text("[Core]\nx = 1\n")       # no [Profile] -> never listed

    def tearDown(self):
        wp._DIR = self._dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_lists_only_classic_excluding_sinden_and_wii(self):
        # The legacy callers (CC rail) must be byte-identical: Classic-only, no Sinden/Wii_*.
        self.assertEqual(set(wp.list_profiles()),
                         {"DS 1 = classic controller", "Steamdeck = classic controller"})

    def test_picker_lists_all_extensions(self):
        # THE BUG FIX: the pickers list every user profile — the Nunchuk one included.
        got = set(wp.list_profiles(None, include_sinden=True))
        self.assertEqual(got, {"DS 1 = classic controller", "Steamdeck = classic controller",
                               "Steamdeck = Wiimote + Nunchuk", "Sinden Lightgun P1", "NoExt"})
        # Wii_* stock set + non-profile files stay hidden in every mode.
        self.assertNotIn("Wii_classic_controller", got)
        self.assertNotIn("NotAProfile", got)

    def test_extension_filter_and_sinden_flag_are_independent(self):
        self.assertEqual(wp.list_profiles("Nunchuk"), ["Steamdeck = Wiimote + Nunchuk"])
        got = set(wp.list_profiles(None))                       # all extensions, Sinden hidden
        self.assertIn("Steamdeck = Wiimote + Nunchuk", got)
        self.assertNotIn("Sinden Lightgun P1", got)

    def test_is_sinden_and_extension_probe(self):
        self.assertTrue(wp.is_sinden("Sinden Lightgun P1"))
        self.assertFalse(wp.is_sinden("Steamdeck = Wiimote + Nunchuk"))
        self.assertEqual(wp.profile_extension("Steamdeck = Wiimote + Nunchuk"), "Nunchuk")
        self.assertEqual(wp.profile_extension("DS 1 = classic controller"), "Classic")
        self.assertEqual(wp.profile_extension("NoExt"), "None")

    def test_handheld_default_constant(self):
        self.assertEqual(wp.HANDHELD_DEFAULT, "Steamdeck = classic controller")

    def test_profile_device_strips_source_index(self):
        self.assertEqual(wp.profile_device("DS 1 = classic controller"),
                         "DualSense Wireless Controller")

    def test_apply_cc_body_injects_source_1_keeps_extension_replaces_old(self):
        text = "[Wiimote1]\nSource = 1\nDevice = old\nButtons/A = STALE\n[Wiimote2]\nSource = 0\n"
        body = wp.profile_body("DS 1 = classic controller")
        out = wp.apply_cc_body(text, "Wiimote1", body)
        self.assertIn("[Wiimote1]\nSource = 1\nDevice = SDL/0/DualSense", out)   # Source injected first
        self.assertIn("Extension = Classic", out)
        w1 = out.split("[Wiimote2]")[0]
        self.assertNotIn("STALE", w1)                    # old body fully replaced
        self.assertIn("[Wiimote2]\nSource = 0\n", out)   # other slot untouched

    def test_apply_cc_body_absent_section_returns_none(self):
        self.assertIsNone(wp.apply_cc_body("[Wiimote2]\nSource = 0\n", "Wiimote1", "x\n"))

    def test_disable_slot_sets_source_0_and_drops_mappings(self):
        text = ("[Wiimote1]\nSource = 1\nClassic/Buttons/A = Y\n"
                "[BalanceBoard]\nSource = 0\n")
        out = wp.disable_slot(text, "Wiimote1")
        self.assertIn("[Wiimote1]\nSource = 0\n", out)
        self.assertNotIn("Classic/Buttons/A", out.split("[BalanceBoard]")[0])
        self.assertIn("[BalanceBoard]\nSource = 0\n", out)

    def test_disable_slot_absent_section_is_noop(self):
        t = "[Wiimote2]\nSource = 1\n"
        self.assertEqual(wp.disable_slot(t, "Wiimote1"), t)


if __name__ == "__main__":
    unittest.main()
