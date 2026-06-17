"""
Tests for the Standalones launch-binder writers — currently pcsx2_cfg.assign_devices
(the explicit ordered pads -> [Pad1..N] writer the launch wrapper calls). Pure given
(players, PCSX2.ini): no hardware, runs against a temp copy of the fixture.

Run:  python3 -m unittest tests.test_standalone_bind -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import inifile, pcsx2_cfg, switch_bind
from tests._fakes import sd

FIX = Path(__file__).parent / "fixtures" / "pcsx2" / "PCSX2.ini"

DS5 = "054c:0ce6"
DS4 = "054c:09cc"


def _pad(text, n):
    return inifile.section_body(text, f"Pad{n}") or ""


class Pcsx2AssignDevices(unittest.TestCase):
    def _run(self, players, manage=2):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            shutil.copy2(FIX, ini)
            pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=manage)
            return ini.read_text(encoding="utf-8")

    def test_order_maps_to_pads_by_sdl_index(self):
        # players in priority order -> Pad1=first pad's SDL index, Pad2=second.
        text = self._run([sd(1, DS5, "g1", "DualSense"), sd(2, DS4, "g2", "DualShock4")])
        self.assertIn("SDL-1/", _pad(text, 1))
        self.assertIn("SDL-2/", _pad(text, 2))
        self.assertIn("Type = DualShock2", _pad(text, 1))

    def test_order_is_respected(self):
        # reversed priority -> reversed pad indices.
        text = self._run([sd(2, DS4, "g2", "DualShock4"), sd(1, DS5, "g1", "DualSense")])
        self.assertIn("SDL-2/", _pad(text, 1))
        self.assertIn("SDL-1/", _pad(text, 2))

    def test_one_pad_disables_the_rest(self):
        text = self._run([sd(3, DS5, "g1", "DualSense")])
        self.assertIn("SDL-3/", _pad(text, 1))
        self.assertEqual(_pad(text, 2).strip(), "Type = None")

    def test_unrelated_sections_preserved(self):
        text = self._run([sd(1, DS5, "g1", "DualSense")])
        self.assertIn("TogglePause = Keyboard/Space", text)  # [Hotkeys] untouched

    def test_missing_ini_raises(self):
        with self.assertRaises(FileNotFoundError):
            pcsx2_cfg.assign_devices([sd(0, DS5, "g", "x")], ini_path="/nonexistent/PCSX2.ini")


class StandaloneBindModel(unittest.TestCase):
    """Single-context standalones (PCSX2) bind PERSISTENTLY — no snapshot/restore.
    Only the TRANSIENT Switch emulators revert input on exit (dual-context fix)."""

    def test_pcsx2_is_not_transient(self):
        self.assertNotIn("pcsx2", switch_bind._TRANSIENT)
        self.assertEqual(switch_bind._TRANSIENT, {"ryujinx", "eden"})

    def test_no_sidecar_restore_path_for_pcsx2(self):
        # PCSX2.ini is not in the restore sweep, so restore_all never touches it.
        self.assertNotIn(switch_bind._PCSX2_INI, list(switch_bind._known_configs()))


if __name__ == "__main__":
    unittest.main()
