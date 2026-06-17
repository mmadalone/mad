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

import json

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


class Pcsx2BindRestoreRoundtrip(unittest.TestCase):
    """The launch wrapper snapshots [Pad*] -> binds MAD's order -> restores on exit;
    the restore must return the [Pad*] sections to their pre-bind bytes."""

    def test_snapshot_bind_restore_returns_original(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            shutil.copy2(FIX, ini)
            original = ini.read_text(encoding="utf-8")

            # What bind() stashes in the sidecar before writing MAD's order.
            snap = switch_bind._snapshot("pcsx2", ini)
            side = switch_bind._sidecar(ini)
            side.write_text(json.dumps({"emu": "pcsx2", "input": snap}), encoding="utf-8")

            pcsx2_cfg.assign_devices([sd(1, DS5, "g", "DualSense")], ini_path=str(ini))
            self.assertIn("SDL-1/", _pad(ini.read_text(encoding="utf-8"), 1))  # changed

            switch_bind.restore_target(ini)   # game-end restore
            restored = ini.read_text(encoding="utf-8")
            self.assertEqual(_pad(restored, 1), _pad(original, 1))
            self.assertEqual(_pad(restored, 2), _pad(original, 2))
            self.assertIn("TogglePause = Keyboard/Space", restored)  # [Hotkeys] kept
            self.assertFalse(side.exists())   # sidecar consumed


if __name__ == "__main__":
    unittest.main()
