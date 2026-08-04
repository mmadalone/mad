"""
Tests for the PCSX2 docked/handheld input-profile core: lib/pcsx2_profiles (stem
resolution chain) + pcsx2_cfg.apply_profile_bodies (transient [PadN] body injection
that composes with assign_devices' SDL-index repointing). Pure given (store entry,
backend cfg, ini/profile files): no hardware, temp files only.

Run:  python3 -m unittest tests.test_pcsx2_profiles -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib import inifile, pcsx2_cfg, pcsx2_profiles, switch_bind
from tests._fakes import sd

DS5 = "054c:0ce6"
DS4 = "054c:09cc"

INI = """[Pad1]
Type = DualShock2
Cross = SDL-9/FaceSouth
Square = SDL-9/FaceWest
LargeMotor = SDL-9/LargeMotor

[Pad2]
Type = DualShock2
Cross = SDL-9/FaceEast

[Hotkeys]
TogglePause = Keyboard/Space
"""

# PCSX2 writes bare profiles: [Pad1] only, no Type line (the real Steamdeck.ini shape).
PROFILE_BARE = """[Pad1]
Cross = SDL-0/FaceNorth
Square = SDL-0/FaceSouth
LargeMotor = SDL-0/LargeMotor
"""

PROFILE_TWO = """[Pad1]
Type = DualShock2
Cross = SDL-3/FaceNorth

[Pad2]
Type = DualShock2
Cross = SDL-3/FaceWest

[Pad]
UseProfileHotkeyBindings = false
"""


def _write(d: Path, name: str, text: str) -> Path:
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


class ResolutionChain(unittest.TestCase):
    CFG = {"profile_docked": "DS4", "profile_handheld": "Steamdeck"}

    def test_pergame_beats_global(self):
        entry = {"profiles": {"docked": "Custom"}}
        self.assertEqual(pcsx2_profiles.resolve(entry, self.CFG, "docked"), "Custom")

    def test_unset_pergame_falls_to_global(self):
        self.assertEqual(pcsx2_profiles.resolve({}, self.CFG, "docked"), "DS4")
        self.assertEqual(pcsx2_profiles.resolve(None, self.CFG, "handheld"), "Steamdeck")

    def test_handheld_never_inherits_docked(self):
        # docked set at BOTH layers, handheld unset everywhere -> None, not the docked pick.
        cfg = {"profile_docked": "DS4"}
        entry = {"profiles": {"docked": "Custom"}}
        self.assertIsNone(pcsx2_profiles.resolve(entry, cfg, "handheld"))

    def test_husk_tolerance(self):
        self.assertIsNone(pcsx2_profiles.resolve({"profiles": "junk"}, {}, "docked"))
        self.assertIsNone(pcsx2_profiles.resolve({"profiles": {"docked": 7}}, {}, "docked"))
        self.assertIsNone(pcsx2_profiles.resolve({}, {"profile_docked": "  "}, "docked"))
        self.assertIsNone(pcsx2_profiles.resolve({}, "junk", "docked"))

    def test_pathy_stems_rejected(self):
        for bad in ("../etc/passwd", "a/b", "a\\b", ".hidden", ""):
            self.assertFalse(pcsx2_profiles.valid_stem(bad), bad)
            self.assertIsNone(pcsx2_profiles.resolve({}, {"profile_docked": bad}, "docked"))
        self.assertTrue(pcsx2_profiles.valid_stem("DS4"))
        self.assertTrue(pcsx2_profiles.valid_stem("Steamdeck 2"))

    def test_profiles_dir_derived_from_config_file(self):
        d = pcsx2_profiles.profiles_dir({"config_file": "/tmp/pcsx2/inis/PCSX2.ini"})
        self.assertEqual(d, Path("/tmp/pcsx2/inputprofiles"))
        default = pcsx2_profiles.profiles_dir({})
        self.assertTrue(str(default).endswith(".config/PCSX2/inputprofiles"))

    def test_profile_path_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write(d, "DS4.ini", PROFILE_BARE)
            self.assertIsNotNone(pcsx2_profiles.profile_path(d, "DS4"))
            self.assertIsNone(pcsx2_profiles.profile_path(d, "Gone"))
            self.assertIsNone(pcsx2_profiles.profile_path(d, "../DS4"))
            self.assertEqual(pcsx2_profiles.list_stems(d), ["DS4"])


class ApplyProfileBodies(unittest.TestCase):
    def _apply(self, profile_text: str, nplayers: int, ini_text: str = INI):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ini = _write(d, "PCSX2.ini", ini_text)
            prof = _write(d, "prof.ini", profile_text)
            applied = pcsx2_cfg.apply_profile_bodies(ini, prof, nplayers)
            return applied, ini.read_text(encoding="utf-8")

    def test_bare_profile_gains_type_and_lands_in_pad1(self):
        applied, text = self._apply(PROFILE_BARE, 1)
        self.assertEqual(applied, [("Pad1", "Pad1")])
        body = inifile.section_body(text, "Pad1")
        self.assertIn("Type = DualShock2", body)      # normalized in (the _slot_template gate)
        self.assertIn("Cross = SDL-0/FaceNorth", body)
        self.assertNotIn("SDL-9/FaceSouth", body)     # old layout replaced

    def test_uncovered_player_keeps_global_block(self):
        applied, text = self._apply(PROFILE_BARE, 2)  # profile has no [Pad2]
        self.assertEqual(applied, [("Pad1", "Pad1")])
        self.assertIn("Cross = SDL-9/FaceEast", inifile.section_body(text, "Pad2"))

    def test_multitap_authored_profile_maps_players_by_canonical_order(self):
        # REVIEW FIX: a 4-player profile authored in PCSX2's UI with MultitapPort1 stores
        # players 2-4 at [Pad3..Pad5] with a [Pad2] husk. The canonical player-order scan
        # (Pad1,Pad3,Pad4,Pad5,Pad2,...) must read them as players 1-4, skip the husk, and
        # land them in the LAUNCH slots for 4 players (Pad1,Pad3,Pad4,Pad5).
        prof = ("[Pad1]\nType = DualShock2\nCross = SDL-0/P1src\n\n"
                "[Pad2]\nType = None\n\n"
                "[Pad3]\nType = DualShock2\nCross = SDL-0/P2src\n\n"
                "[Pad4]\nType = DualShock2\nCross = SDL-0/P3src\n\n"
                "[Pad5]\nType = DualShock2\nCross = SDL-0/P4src\n")
        applied, text = self._apply(prof, 4)
        self.assertEqual(applied, [("Pad1", "Pad1"), ("Pad3", "Pad3"),
                                   ("Pad4", "Pad4"), ("Pad5", "Pad5")])
        for slot, src in (("Pad1", "P1src"), ("Pad3", "P2src"),
                          ("Pad4", "P3src"), ("Pad5", "P4src")):
            self.assertIn(f"Cross = SDL-0/{src}", inifile.section_body(text, slot) or "")

    def test_two_player_profile_maps_by_player(self):
        applied, text = self._apply(PROFILE_TWO, 2)
        self.assertEqual(applied, [("Pad1", "Pad1"), ("Pad2", "Pad2")])
        self.assertIn("Cross = SDL-3/FaceWest", inifile.section_body(text, "Pad2"))
        # [Pad] inside the profile is ignored (owned elsewhere).
        self.assertNotIn("UseProfileHotkeyBindings", text)

    def test_multitap_puts_player2_in_pad3(self):
        applied, text = self._apply(PROFILE_TWO, 3)   # 3 players -> slots 1,3,4
        self.assertIn(("Pad2", "Pad3"), applied)
        self.assertIn("Cross = SDL-3/FaceWest", inifile.section_body(text, "Pad3"))

    def test_type_none_husk_skipped(self):
        applied, text = self._apply("[Pad1]\nType = None\nCross = SDL-0/FaceSouth\n", 1)
        self.assertEqual(applied, [])
        self.assertIn("Cross = SDL-9/FaceSouth", inifile.section_body(text, "Pad1"))

    def test_no_sdl_binds_skipped(self):
        applied, text = self._apply("[Pad1]\nCross = Keyboard/Return\n", 1)
        self.assertEqual(applied, [])
        self.assertIn("SDL-9/FaceSouth", text)        # ini untouched

    def test_missing_profile_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ini = _write(d, "PCSX2.ini", INI)
            applied = pcsx2_cfg.apply_profile_bodies(ini, d / "gone.ini", 1)
            self.assertEqual(applied, [])
            self.assertEqual(ini.read_text(encoding="utf-8"), INI)

    def test_hotkeys_untouched(self):
        _applied, text = self._apply(PROFILE_TWO, 2)
        self.assertIn("TogglePause = Keyboard/Space", text)


class ComposesWithAssignDevices(unittest.TestCase):
    """End-to-end: injection -> assign_devices repoints the profile's SDL sources to the
    calibrated pad indexes (the whole reason profiles are copied, not InputProfileName'd)."""

    def test_profile_layout_survives_with_repointed_index(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ini = _write(d, "PCSX2.ini", INI)
            prof = _write(d, "prof.ini", PROFILE_BARE)
            pcsx2_cfg.apply_profile_bodies(ini, prof, 1)
            pcsx2_cfg.assign_devices([sd(5, DS5, "g1", "DualSense")],
                                     ini_path=str(ini), manage=8)
            body = inifile.section_body(ini.read_text(encoding="utf-8"), "Pad1")
            self.assertIn("Cross = SDL-5/FaceNorth", body)   # profile layout, calibrated index
            self.assertIn("LargeMotor = SDL-5/LargeMotor", body)
            self.assertNotIn("SDL-0/", body)

    def test_two_players_each_get_their_profile_section(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ini = _write(d, "PCSX2.ini", INI)
            prof = _write(d, "prof.ini", PROFILE_TWO)
            pcsx2_cfg.apply_profile_bodies(ini, prof, 2)
            pcsx2_cfg.assign_devices([sd(4, DS5, "g1", "DualSense"), sd(7, DS4, "g2", "DS4")],
                                     ini_path=str(ini), manage=8)
            text = ini.read_text(encoding="utf-8")
            self.assertIn("Cross = SDL-4/FaceNorth", inifile.section_body(text, "Pad1"))
            self.assertIn("Cross = SDL-7/FaceWest", inifile.section_body(text, "Pad2"))


class SidecarRestoreRoundTrip(unittest.TestCase):
    """snapshot -> inject profile -> assign_devices -> restore_target reverts the [Pad*]
    sections to their resting values (the transient guarantee the whole feature rides on)."""

    def test_injection_is_fully_reverted(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ini = _write(d, "PCSX2.ini", INI)
            prof = _write(d, "prof.ini", PROFILE_BARE)
            side = switch_bind._sidecar(ini)
            side.write_text(json.dumps(
                {"emu": "pcsx2", "input": switch_bind._snapshot("pcsx2", ini)}),
                encoding="utf-8")
            pcsx2_cfg.apply_profile_bodies(ini, prof, 1)
            pcsx2_cfg.assign_devices([sd(5, DS5, "g1", "DualSense")],
                                     ini_path=str(ini), manage=8)
            self.assertIn("SDL-5/FaceNorth", ini.read_text(encoding="utf-8"))
            switch_bind.restore_target(ini)
            text = ini.read_text(encoding="utf-8")
            self.assertFalse(side.exists())
            for section, body in (("Pad1", "Cross = SDL-9/FaceSouth"),
                                  ("Pad2", "Cross = SDL-9/FaceEast")):
                self.assertIn(body, inifile.section_body(text, section) or "")
            self.assertNotIn("SDL-5/", text)
            self.assertNotIn("SDL-0/", text)
            self.assertIn("TogglePause = Keyboard/Space", text)


if __name__ == "__main__":
    unittest.main()
