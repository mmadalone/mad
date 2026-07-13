"""Tests for the Wii Remote source decider + Classic-Controller launch rail (lib/dolphin_wii_source).

Run:  python3 -m unittest tests.test_dolphin_wii_source -v
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_wii_pads
from lib import dolphin_wii_source as ws

_LOG = logging.getLogger("test")

_RESTING = ("[Wiimote1]\nSource = 1\nDevice = evdev/0/Sinden P1\nBUTTONS1\n"
            "[Wiimote2]\nSource = 1\nDevice = evdev/0/Sinden P2\nBUTTONS2\n"
            "[Wiimote3]\nSource = 0\n[Wiimote4]\nSource = 0\n[BalanceBoard]\nSource = 0\n")


class Decision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wn = self.tmp / "WiimoteNew.ini"
        self.wn.write_text(_RESTING)
        self._save = {
            "FILE": ws._FILE, "BACKUP": ws._BACKUP, "present": ws.devices.dolphinbar_present,
            "wm": ws.devices.dolphinbar_wiimotes, "lg": ws._is_lightgun, "cc": ws._cc_capable,
            "docked": ws._is_docked, "run": ws._run_tool, "be": ws._be, "be_wii": ws._be_wii,
            "pbody": ws.dolphin_wii_profiles.profile_body, "assign": dolphin_wii_pads.assign_text,
        }
        ws._FILE = self.wn
        ws._BACKUP = self.tmp / "WiimoteNew.ini.cc-backup"
        self.ran: list[str] = []
        ws._run_tool = lambda mode, logger: self.ran.append(mode)
        ws._be = lambda: {}
        ws._be_wii = lambda: {}
        # gun profiles (no Classic) for Sinden names; CC bodies otherwise
        ws.dolphin_wii_profiles.profile_body = lambda n: (
            "Device = evdev/0/Sinden\nIR/Up = X\n" if n.startswith("Sinden")
            else f"Device = SDL/0/{n}\nExtension = Classic\nClassic/Buttons/A = `Button E`\n")

    def tearDown(self):
        ws._FILE = self._save["FILE"]; ws._BACKUP = self._save["BACKUP"]
        ws.devices.dolphinbar_present = self._save["present"]
        ws.devices.dolphinbar_wiimotes = self._save["wm"]
        ws._is_lightgun = self._save["lg"]; ws._cc_capable = self._save["cc"]
        ws._is_docked = self._save["docked"]; ws._run_tool = self._save["run"]
        ws._be = self._save["be"]; ws._be_wii = self._save["be_wii"]
        ws.dolphin_wii_profiles.profile_body = self._save["pbody"]
        dolphin_wii_pads.assign_text = self._save["assign"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- DolphinBar present -> real / real2 (covers the "lightgun too" rule) ----
    def test_bar_present_one_remote_is_real(self):
        ws.devices.dolphinbar_present = lambda: True
        ws.devices.dolphinbar_wiimotes = lambda: 1
        ws._is_lightgun = lambda rom: True            # even a lightgun game: bar present -> real
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "real")
        self.assertEqual(self.ran, ["real"])

    def test_bar_present_two_remotes_is_real2(self):
        ws.devices.dolphinbar_present = lambda: True
        ws.devices.dolphinbar_wiimotes = lambda: 2
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "real2")
        self.assertEqual(self.ran, ["real2"])

    # ---- no bar, lightgun -> sinden (Source flip; body preserved by the sweep) ----
    def test_no_bar_lightgun_is_sinden_flip_only(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: True
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        self.assertEqual(self.ran, ["sinden"])
        self.assertEqual(self.wn.read_text(), _RESTING)     # rich gun body untouched (no contamination)

    def test_no_bar_lightgun_rebuilds_gun_slots_if_cc_contaminated(self):
        # A CC body somehow survived into the gun slots without a backup -> rebuild from gun profiles.
        self.wn.write_text("[Wiimote1]\nSource = 1\nExtension = Classic\nClassic/Buttons/A = X\n"
                           "[Wiimote2]\nSource = 1\nClassic/Buttons/A = Y\n[BalanceBoard]\nSource = 0\n")
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: True
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        w1 = self.wn.read_text().split("[Wiimote2]")[0]
        self.assertIn("Device = evdev/0/Sinden", w1)         # rebuilt from the gun profile
        self.assertNotIn("Classic/", w1)                     # contamination gone
        self.assertEqual(self.ran, ["sinden"])

    # ---- no bar, CC-capable, docked -> pads->players (transient) ----
    def test_no_bar_cc_docked_applies_pads_and_snapshots(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: True
        ws._is_docked = lambda: True
        dolphin_wii_pads.assign_text = lambda text: (text.replace("BUTTONS1", "CC-P1"), [(1, "DS4 1")])
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertIn("CC-P1", self.wn.read_text())
        self.assertTrue(ws._BACKUP.is_file())                # transient snapshot taken
        ws.restore(_LOG)
        self.assertEqual(self.wn.read_text(), _RESTING)      # reverted after the game
        self.assertFalse(ws._BACKUP.is_file())

    def test_no_bar_cc_docked_no_match_leaves_resting(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: True
        ws._is_docked = lambda: True
        dolphin_wii_pads.assign_text = lambda text: (text, [])       # nothing connected
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertEqual(self.wn.read_text(), _RESTING)              # untouched
        self.assertFalse(ws._BACKUP.is_file())                       # no snapshot without a swap

    # ---- no bar, CC-capable, handheld -> the Deck profile on Wiimote1, 2..4 off ----
    def test_no_bar_cc_handheld_loads_deck_profile(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: True
        ws._is_docked = lambda: False
        ws._be_wii = lambda: {"undocked_profile": "Steamdeck = classic controller"}
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        txt = self.wn.read_text()
        self.assertIn("[Wiimote1]\nSource = 1\nDevice = SDL/0/Steamdeck", txt)
        self.assertIn("Extension = Classic", txt)
        self.assertIn("[Wiimote2]\nSource = 0\n", txt)       # 2..4 disabled
        self.assertIn("[Wiimote3]\nSource = 0\n", txt)
        self.assertTrue(ws._BACKUP.is_file())
        ws.restore(_LOG)
        self.assertEqual(self.wn.read_text(), _RESTING)

    # ---- no bar, not lightgun, not CC -> real (today's behavior) ----
    def test_no_bar_non_cc_is_real(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: False
        self.assertEqual(ws.apply("/ROMs/wii/plain.rvz", _LOG), "real")
        self.assertEqual(self.ran, ["real"])

    # ---- crash-orphan sweep + surviving-backup guard ----
    def test_crash_orphan_swept_before_decision(self):
        # a leftover .cc-backup (crashed CC game) is restored FIRST, before the new decision applies.
        good = "[Wiimote1]\nSource = 1\nTRUE-RESTING\n[BalanceBoard]\nSource = 0\n"
        ws._BACKUP.write_text(good)
        self.wn.write_text("[Wiimote1]\nSource = 1\nExtension = Classic\nClassic/Buttons/A = X\n"
                           "[BalanceBoard]\nSource = 0\n")          # contaminated live file
        ws.devices.dolphinbar_present = lambda: True
        ws.devices.dolphinbar_wiimotes = lambda: 1
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertEqual(self.wn.read_text(), good)                # swept back to the pre-CC resting
        self.assertFalse(ws._BACKUP.is_file())

    def test_surviving_backup_guard_skips(self):
        ws._BACKUP.write_text("snapshot")
        orig = ws.restore
        ws.restore = lambda logger=None: False                     # restore fails, leaves the backup
        self.addCleanup(lambda: setattr(ws, "restore", orig))
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: True
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "skip")
        self.assertEqual(self.wn.read_text(), _RESTING)            # never applied over a surviving backup

    def test_restore_noop_without_backup(self):
        self.assertFalse(ws.restore(_LOG))

    def test_tool_stdout_never_leaks_to_process_stdout(self):
        # REGRESSION: the game-start hook captures this process's stdout as the chosen mode. The
        # Source tool prints a banner to stdout; _run_tool MUST capture it so only print(mode) leaks.
        # Checked at the FD level (a mocked _run_tool would hide it) by redirecting fd 1 to a file.
        import os
        tool = self.tmp / "faketool.sh"
        tool.write_text("#!/usr/bin/env bash\necho \"Dolphin Wii mode = '$1'\"\necho '  [Wiimote1] -> Source = 2'\n")
        tool.chmod(0o755)
        ws._TOOL = tool
        ws._run_tool = self._save["run"]                 # use the REAL _run_tool (real subprocess)
        ws.devices.dolphinbar_present = lambda: True
        ws.devices.dolphinbar_wiimotes = lambda: 1
        cap = self.tmp / "fd1.txt"
        saved = os.dup(1)
        fd = os.open(str(cap), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        os.dup2(fd, 1)
        try:
            mode = ws.apply("/ROMs/wii/x.rvz", _LOG)     # real bar-present -> real -> real _run_tool
        finally:
            os.dup2(saved, 1); os.close(saved); os.close(fd)
        self.assertEqual(mode, "real")
        self.assertEqual(cap.read_text(), "")            # tool banner captured, NOT leaked to fd 1


if __name__ == "__main__":
    unittest.main()
