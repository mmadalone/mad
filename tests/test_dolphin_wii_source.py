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
            "resolve": ws.dolphin_wii_tdb._resolve,
        }
        ws._FILE = self.wn
        ws._BACKUP = self.tmp / "WiimoteNew.ini.cc-backup"
        self.ran: list[str] = []
        ws._run_tool = lambda mode, logger: self.ran.append(mode)
        ws._be = lambda: {}
        ws._be_wii = lambda: {}
        ws.dolphin_wii_tdb._resolve = lambda rom: None    # force_cc off by default (no real dolphin-tool)
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
        ws.dolphin_wii_tdb._resolve = self._save["resolve"]
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
        # An emulated body in the gun slots without a backup -> rebuild from gun profiles.
        emu_body = ("[Wiimote1]\nSource = 1\nExtension = Classic\nClassic/Buttons/A = X\n"
                    "[Wiimote2]\nSource = 1\nClassic/Buttons/A = Y\n[BalanceBoard]\nSource = 0\n")
        self.wn.write_text(emu_body)
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: True
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        w1 = self.wn.read_text().split("[Wiimote2]")[0]
        self.assertIn("Device = evdev/0/Sinden", w1)         # rebuilt from the gun profile
        self.assertNotIn("Classic/", w1)                     # contamination gone
        # REVIEW FIX: the rebuild is TRANSIENT — the pre-rebuild body (which may be a
        # LEGITIMATE hand-authored setup, e.g. a resting Nunchuk personality) is snapshotted
        # and comes back on restore, never destroyed.
        self.assertTrue(ws._BACKUP.is_file())
        ws.restore(_LOG)
        self.assertEqual(self.wn.read_text(), emu_body)

    def test_rebuild_preserves_legit_nunchuk_resting_body(self):
        # THE review scenario: the user's hand-authored Wiimote+Nunchuk resting body (the
        # exact current on-disk state of this rig) must survive a gun-collection launch.
        nunchuk_rest = ("[Wiimote1]\nSource = 1\nDevice = SDL/0/Steam Deck Controller\n"
                        "Extension = Nunchuk\nNunchuk/Buttons/C = X\n"
                        "[Wiimote2]\nSource = 0\n[BalanceBoard]\nSource = 0\n")
        self.wn.write_text(nunchuk_rest)
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: True
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        self.assertIn("Device = evdev/0/Sinden",
                      self.wn.read_text().split("[Wiimote2]")[0])    # guns work for the game
        ws.restore(_LOG)                                             # game-end hook
        self.assertEqual(self.wn.read_text(), nunchuk_rest)          # user data intact
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

    # ---- no bar, NOT GameTDB-CC but per-game force_cc -> classic (data-gap games, both contexts) ----
    def test_no_bar_force_cc_docked_applies(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: False                            # GameTDB has no CC record
        ws._is_docked = lambda: True
        ws.dolphin_wii_tdb._resolve = lambda rom: "WR5PEY"
        ws._be_wii = lambda: {"pergame": {"WR5PEY": {"force_cc": True}}}
        dolphin_wii_pads.assign_text = lambda text: (text.replace("BUTTONS1", "CC-P1"), [(1, "Deck")])
        self.assertEqual(ws.apply("/ROMs/wii/rcr.wad", _LOG), "classic")
        self.assertIn("CC-P1", self.wn.read_text())
        self.assertTrue(ws._BACKUP.is_file())                         # transient snapshot taken

    def test_no_bar_force_cc_handheld_applies(self):
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: False
        ws._is_docked = lambda: False
        ws.dolphin_wii_tdb._resolve = lambda rom: "WR5PEY"
        ws._be_wii = lambda: {"pergame": {"WR5PEY": {"force_cc": True}},
                              "undocked_profile": "Steamdeck = classic controller"}
        self.assertEqual(ws.apply("/ROMs/wii/rcr.wad", _LOG), "classic")
        self.assertIn("Device = SDL/0/Steamdeck", self.wn.read_text())   # Deck profile on Wiimote1

    def test_force_cc_reads_pergame_flag(self):
        ws.dolphin_wii_tdb._resolve = lambda rom: "WR5PEY"
        ws._be_wii = lambda: {"pergame": {"WR5PEY": {"force_cc": False}}}
        self.assertFalse(ws.force_cc("/x"))                           # flag explicitly false
        ws._be_wii = lambda: {"pergame": {"OTHER0": {"force_cc": True}}}
        self.assertFalse(ws.force_cc("/x"))                           # this game not in the table
        ws._be_wii = lambda: {"pergame": {"WR5PEY": {"force_cc": True}}}
        self.assertTrue(ws.force_cc("/x"))                            # flagged -> forced
        ws.dolphin_wii_tdb._resolve = lambda rom: None
        self.assertFalse(ws.force_cc("/x"))                           # unresolvable -> False

    def test_bar_present_wins_over_force_cc(self):
        ws.devices.dolphinbar_present = lambda: True                  # a bar is only checked before force_cc
        ws.devices.dolphinbar_wiimotes = lambda: 1
        ws.dolphin_wii_tdb._resolve = lambda rom: "WR5PEY"
        ws._be_wii = lambda: {"pergame": {"WR5PEY": {"force_cc": True}}}
        self.assertEqual(ws.apply("/ROMs/wii/rcr.wad", _LOG), "real")   # bar wins over the force flag

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
        _orig_tool = ws._TOOL
        self.addCleanup(lambda: setattr(ws, "_TOOL", _orig_tool))   # never leak the temp path
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


class ProfileLadder(unittest.TestCase):
    """The no-bar PER-SEAT profile ladder (multi-seat 2026-08-04): players 1-4 each resolve
    per-game pick -> style default (curated sideways beats derived nunchuk; docked CC without
    an explicit P1 keeps the pads rail; handheld CC seats via the pickers) -> legacy fallback.
    Sinden picks (seat 1 only) reroute through the FULL sinden mode."""

    GID = "SMNE01"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wn = self.tmp / "WiimoteNew.ini"
        self.wn.write_text(_RESTING)
        tdb = ws.dolphin_wii_tdb
        self._save = {
            "FILE": ws._FILE, "BACKUP": ws._BACKUP, "present": ws.devices.dolphinbar_present,
            "lg": ws._is_lightgun, "cc": ws._cc_capable, "docked": ws._is_docked,
            "run": ws._run_tool, "be": ws._be, "be_wii": ws._be_wii,
            "pbody": ws.dolphin_wii_profiles.profile_body, "resolve": tdb._resolve,
            "sideways": tdb.is_sideways, "nunchuk": tdb.is_nunchuk,
            "assign": dolphin_wii_pads.assign_text,
        }
        ws._FILE = self.wn
        ws._BACKUP = self.tmp / "WiimoteNew.ini.cc-backup"
        self.ran: list[str] = []
        ws._run_tool = lambda mode, logger: self.ran.append(mode)
        ws.devices.dolphinbar_present = lambda: False
        ws._is_lightgun = lambda rom: False
        ws._cc_capable = lambda rom: False
        ws._is_docked = lambda: True
        ws._be = lambda: {}
        self.be_wii: dict = {}
        ws._be_wii = lambda: self.be_wii
        tdb._resolve = lambda rom: self.GID
        tdb.is_sideways = lambda gid: False
        tdb.is_nunchuk = lambda gid: False
        ws.dolphin_wii_profiles.profile_body = lambda n: (
            None if n == "Ghost"
            else "Device = evdev/0/Sinden\nIR/Up = X\n" if n.startswith("Sinden")
            else f"Device = SDL/0/{n}\nBUTTONS = {n}\n")

    def tearDown(self):
        tdb = ws.dolphin_wii_tdb
        ws._FILE = self._save["FILE"]; ws._BACKUP = self._save["BACKUP"]
        ws.devices.dolphinbar_present = self._save["present"]
        ws._is_lightgun = self._save["lg"]; ws._cc_capable = self._save["cc"]
        ws._is_docked = self._save["docked"]; ws._run_tool = self._save["run"]
        ws._be = self._save["be"]; ws._be_wii = self._save["be_wii"]
        ws.dolphin_wii_profiles.profile_body = self._save["pbody"]
        tdb._resolve = self._save["resolve"]
        tdb.is_sideways = self._save["sideways"]; tdb.is_nunchuk = self._save["nunchuk"]
        dolphin_wii_pads.assign_text = self._save["assign"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _w1(self):
        return self.wn.read_text().split("[Wiimote2]")[0]

    def _slot(self, n: int) -> str:
        txt = self.wn.read_text()
        nxt = f"[Wiimote{n + 1}]" if n < 4 else "[BalanceBoard]"
        return txt.split(f"[Wiimote{n}]")[1].split(nxt)[0]

    def test_explicit_pergame_docked_seats_p1_only(self):
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "MyProf"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = MyProf", self._w1())
        self.assertIn("Source = 1", self._w1())
        rest = self.wn.read_text().split("[Wiimote2]")[1]
        self.assertEqual(rest.count("Source = 0"), 4)         # W2-4 off + BalanceBoard untouched
        self.assertTrue(ws._BACKUP.is_file())                 # transient rail engaged

    def test_context_picks_the_right_pergame_key(self):
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "DockP",
                                              "handheld_profile": "HandP"}}}
        ws._is_docked = lambda: False
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = HandP", self._w1())

    def test_pergame_beats_style_default(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "StyleP",
                       "pergame": {self.GID: {"docked_profile": "GameP"}}}
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = GameP", self._w1())

    def test_sideways_style_uses_its_default_and_beats_nunchuk(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        ws.dolphin_wii_tdb.is_nunchuk = lambda gid: True      # NSMBW case: GameTDB lists nunchuk too
        self.be_wii = {"docked_profile_sideways": "SideP", "docked_profile_nunchuk": "NunP"}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = SideP", self._w1())

    def test_nunchuk_bucket_uses_its_default(self):
        ws.dolphin_wii_tdb.is_nunchuk = lambda gid: True
        self.be_wii = {"undocked_profile_nunchuk": "NunP"}
        ws._is_docked = lambda: False
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = NunP", self._w1())

    def test_other_bucket_has_no_global_rung(self):
        # The "Other games" rows were dropped 2026-08-04: a pointer/unknown game with the
        # old (now inert) *_other key stored falls to legacy real; only a per-game pick seats.
        ws._is_docked = lambda: False
        self.be_wii = {"undocked_profile_other": "OtherP"}    # inert legacy value
        self.assertEqual(ws.apply("/ROMs/wii/y.rvz", _LOG), "real")
        self.assertEqual(self.wn.read_text(), _RESTING)
        self.be_wii = {"undocked_profile_other": "OtherP",
                       "pergame": {self.GID: {"handheld_profile": "GameP"}}}
        self.assertEqual(ws.apply("/ROMs/wii/y.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = GameP", self._w1())          # the per-game escape hatch still works

    def test_cc_style_keeps_the_old_rail(self):
        ws._cc_capable = lambda rom: True
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True     # cc wins over sideways detection
        seen = {}
        def fake_assign(text, assign=None):
            seen["called"] = True
            return text, [(1, "CCprof")]
        dolphin_wii_pads.assign_text = fake_assign
        self.be_wii = {"docked_profile_sideways": "SideP"}    # must NOT be consulted for a CC game
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertTrue(seen.get("called"))                   # the docked CC rail ran
        self.assertNotIn("BUTTONS = SideP", self.wn.read_text())

    def test_unset_rung_falls_to_real(self):
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "real")
        self.assertEqual(self.ran, ["real"])
        self.assertEqual(self.wn.read_text(), _RESTING)       # nothing written, no backup
        self.assertFalse(ws._BACKUP.is_file())

    def test_sinden_pick_applies_full_gun_mode_transiently(self):
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "Sinden Lightgun P1"}}}
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        self.assertEqual(self.ran, ["sinden"])                # the FULL sinden tool, not a body copy
        self.assertEqual(self.wn.read_text(), _RESTING)       # body untouched (no contamination)
        # REVIEW FIX: the pick is TRANSIENT — a snapshot exists, so the game-end restore
        # reverts the tool's Source flips instead of leaving the resting config sinden'd.
        self.assertTrue(ws._BACKUP.is_file())
        self.assertEqual(ws._BACKUP.read_bytes().decode(), _RESTING)

    def test_sinden_pick_gates_the_driver(self):
        # REVIEW FIX: sinden_pick() is what controller-router's lightgun-rom consults so the
        # game-start hook starts the gun DRIVER for a picked (non-collection) game.
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "Sinden Lightgun P1"}}}
        self.assertTrue(ws.sinden_pick("/ROMs/wii/gun.rvz"))
        self.assertFalse(ws.sinden_pick("/ROMs/gc/gun.rvz"))          # not a wii rom path
        ws.devices.dolphinbar_present = lambda: True
        self.assertFalse(ws.sinden_pick("/ROMs/wii/gun.rvz"))         # bar: ladder never runs
        ws.devices.dolphinbar_present = lambda: False
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "MyProf"}}}
        self.assertFalse(ws.sinden_pick("/ROMs/wii/gun.rvz"))         # non-Sinden pick

    def test_stale_explicit_falls_through_to_style_default(self):
        # REVIEW FIX: a stale per-game pick must not mask a still-valid style default.
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "StyleP",
                       "pergame": {self.GID: {"docked_profile": "Ghost"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = StyleP", self._w1())

    def test_missing_profile_file_falls_back(self):
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "Ghost"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "real")   # warn + legacy fallback
        self.assertEqual(self.wn.read_text(), _RESTING)

    def test_stale_pick_does_not_suppress_the_bar_warning(self):
        # REVIEW FIX: profile_override must mirror the validated ladder — a stale pick
        # means the launch falls to `real`, so the no-DolphinBar warning must still fire.
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "Ghost"}}}
        self.assertFalse(ws.profile_override("/ROMs/wii/x.rvz"))

    def test_bar_present_ignores_profiles(self):
        ws.devices.dolphinbar_present = lambda: True
        ws.devices.dolphinbar_wiimotes = lambda: 1
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways_p2": "TwoP",   # seat keys: also ignored
                       "pergame": {self.GID: {"docked_profile": "MyProf"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "real")
        self.assertEqual(self.wn.read_text(), _RESTING)

    def test_style_export_tokens(self):
        # The PUBLIC style() the UI labels delegate to: cc > sideways > nunchuk > other.
        ws._cc_capable = lambda rom: True
        self.assertEqual(ws.style(self.GID), "cc")
        ws._cc_capable = lambda rom: False
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        ws.dolphin_wii_tdb.is_nunchuk = lambda gid: True
        self.assertEqual(ws.style(self.GID), "sideways")       # curated beats derived
        ws.dolphin_wii_tdb.is_sideways = lambda gid: False
        self.assertEqual(ws.style(self.GID), "nunchuk")
        ws.dolphin_wii_tdb.is_nunchuk = lambda gid: False
        self.assertEqual(ws.style(self.GID), "other")
        self.be_wii = {"pergame": {self.GID: {"force_cc": True}}}
        self.assertEqual(ws.style(self.GID), "cc")             # force_cc wins like the decider

    def test_sinden_collection_beats_pergame_pick(self):
        ws._is_lightgun = lambda rom: True
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "MyProf"}}}
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        self.assertEqual(self.wn.read_text(), _RESTING)

    def test_unresolvable_gid_falls_to_real(self):
        # No GameID (homebrew): the per-game rung can't match and the style detectors are
        # never consulted (they need a gid), so with "Other" dropped nothing seats.
        ws.dolphin_wii_tdb._resolve = lambda rom: None
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True     # would match, but no gid to ask with
        self.be_wii = {"docked_profile_sideways": "SideP",
                       "pergame": {self.GID: {"docked_profile": "GameP"}}}
        self.assertEqual(ws.apply("/ROMs/wii/homebrew.iso", _LOG), "real")
        self.assertEqual(self.wn.read_text(), _RESTING)

    def test_plan_reports_context_and_seat_maps(self):
        self.be_wii = {"docked_profile_sideways": "SideP",
                       "docked_profile_sideways_p2": "SideP2",
                       "undocked_profile": "CCHand", "undocked_profile_p2": "CCHand2"}
        p = ws.plan()
        self.assertEqual(p["mode"], "docked")
        self.assertEqual(p["styles"]["sideways"], {1: "SideP", 2: "SideP2"})
        self.assertEqual(p["styles"]["nunchuk"], {})
        self.assertNotIn("other", p["styles"])                # the dropped style never appears
        self.assertEqual(p["cc"], "pads-to-players order")
        ws._is_docked = lambda: False
        p = ws.plan()
        self.assertEqual(p["mode"], "handheld")
        self.assertEqual(p["cc"], {1: "CCHand", 2: "CCHand2"})
        self.be_wii = {}
        p = ws.plan()
        self.assertEqual(p["cc"], {1: ws._HANDHELD_DEFAULT})  # seat 1 keeps the Deck default

    def test_profile_override_signal(self):
        self.assertFalse(ws.profile_override("/r.rvz"))                       # nothing set
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "SideP"}
        self.assertTrue(ws.profile_override("/r.rvz"))                        # style default set
        self.be_wii = {"docked_profile_sideways_p2": "TwoP"}
        self.assertTrue(ws.profile_override("/r.rvz"))                        # a P2-only seat counts
        ws._cc_capable = lambda rom: True
        self.assertFalse(ws.profile_override("/r.rvz"))                       # CC handled elsewhere
        ws._cc_capable = lambda rom: False
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "GameP"}}}
        self.assertTrue(ws.profile_override("/r.rvz"))                        # per-game pick

    # ---- multi-seat (players 1-4, both contexts) ----
    def test_full_four_seats_docked(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "OneP", "docked_profile_sideways_p2": "TwoP",
                       "docked_profile_sideways_p3": "ThreeP", "docked_profile_sideways_p4": "FourP"}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        for n, name in ((1, "OneP"), (2, "TwoP"), (3, "ThreeP"), (4, "FourP")):
            self.assertIn(f"BUTTONS = {name}", self._slot(n))
            self.assertIn("Source = 1", self._slot(n))
        self.assertEqual(self.wn.read_text().count("Source = 0"), 1)   # BalanceBoard only
        self.assertTrue(ws._BACKUP.is_file())
        ws.restore(_LOG)
        self.assertEqual(self.wn.read_text(), _RESTING)

    def test_sparse_seats_disable_the_gaps(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "OneP", "docked_profile_sideways_p3": "ThreeP"}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = OneP", self._slot(1))
        self.assertIn("BUTTONS = ThreeP", self._slot(3))
        self.assertIn("Source = 0", self._slot(2))
        self.assertIn("Source = 0", self._slot(4))
        self.assertEqual(self.wn.read_text().count("Source = 0"), 3)   # W2, W4, BalanceBoard

    def test_p2_without_p1_is_legitimate(self):
        ws.dolphin_wii_tdb.is_nunchuk = lambda gid: True
        self.be_wii = {"docked_profile_nunchuk_p2": "TwoP"}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("Source = 0", self._slot(1))            # P1 deliberately unset -> slot off
        self.assertIn("BUTTONS = TwoP", self._slot(2))

    def test_pergame_beats_global_per_seat(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "OneP", "docked_profile_sideways_p2": "TwoP",
                       "pergame": {self.GID: {"docked_profile_p2": "GameTwo"}}}
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = OneP", self._slot(1))        # P1 from the global
        self.assertIn("BUTTONS = GameTwo", self._slot(2))     # P2 from the per-game pick

    def test_duplicate_stem_drops_the_later_seat(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "SameP", "docked_profile_sideways_p2": "SameP",
                       "docked_profile_sideways_p3": "ThreeP"}
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = SameP", self._slot(1))
        self.assertIn("Source = 0", self._slot(2))            # duplicate dropped, slot off
        self.assertIn("BUTTONS = ThreeP", self._slot(3))      # later distinct seat unaffected

    def test_sinden_on_seat2_falls_through_to_style_default(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "OneP", "docked_profile_sideways_p2": "TwoP",
                       "pergame": {self.GID: {"docked_profile_p2": "Sinden Lightgun P1"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = TwoP", self._slot(2))        # gun stem skipped, style default seats
        self.assertEqual(self.ran, [])                        # never rerouted to the sinden tool

    def test_p1_sinden_wins_and_ignores_other_seats(self):
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "Sinden Lightgun P1",
                                              "docked_profile_p2": "TwoP"}}}
        self.assertEqual(ws.apply("/ROMs/wii/gun.rvz", _LOG), "sinden")
        self.assertEqual(self.ran, ["sinden"])
        self.assertEqual(self.wn.read_text(), _RESTING)       # gun mode: no body copies at all
        self.assertTrue(ws._BACKUP.is_file())

    def test_stale_seat_falls_through_per_seat(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "OneP", "docked_profile_sideways_p2": "TwoP",
                       "pergame": {self.GID: {"docked_profile_p2": "Ghost"}}}
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = TwoP", self._slot(2))        # stale per-game -> style default
        self.be_wii = {"docked_profile_sideways": "OneP",
                       "pergame": {self.GID: {"docked_profile_p2": "Ghost"}}}
        self.wn.write_text(_RESTING)
        ws._BACKUP.unlink()
        ws.apply("/ROMs/wii/x.rvz", _LOG)
        self.assertIn("BUTTONS = OneP", self._slot(1))        # P1 still seats
        self.assertIn("Source = 0", self._slot(2))            # stale P2 with no fallback -> off

    def test_all_seats_stale_falls_to_real(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "Ghost", "docked_profile_sideways_p2": "Ghost"}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "real")
        self.assertEqual(self.wn.read_text(), _RESTING)
        self.assertFalse(ws._BACKUP.is_file())

    def test_handheld_seats_from_handheld_keys_only(self):
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        ws._is_docked = lambda: False
        self.be_wii = {"undocked_profile_sideways": "HandOne",
                       "undocked_profile_sideways_p2": "HandTwo",
                       "docked_profile_sideways_p3": "DockThree"}   # docked key: must be ignored
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = HandOne", self._slot(1))
        self.assertIn("BUTTONS = HandTwo", self._slot(2))     # handheld multiplayer (external pad)
        self.assertIn("Source = 0", self._slot(3))            # the docked P3 key never applies

    def test_handheld_cc_seats_default_p1_plus_external_p2(self):
        ws._cc_capable = lambda rom: True
        ws._is_docked = lambda: False
        self.be_wii = {"undocked_profile_p2": "PadTwo"}
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertIn("BUTTONS = Steamdeck", self._slot(1))   # built-in Deck default preserved
        self.assertIn("BUTTONS = PadTwo", self._slot(2))
        self.assertIn("Source = 0", self._slot(3))

    def test_docked_cc_without_explicit_p1_ignores_seat_rows(self):
        ws._cc_capable = lambda rom: True
        seen = {}
        def fake_assign(text, assign=None):
            seen["called"] = True
            return text.replace("BUTTONS1", "CC-P1"), [(1, "Pad")]
        dolphin_wii_pads.assign_text = fake_assign
        self.be_wii = {"pergame": {self.GID: {"docked_profile_p2": "TwoP"}}}   # P2 without P1
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertTrue(seen.get("called"))                   # the pads rail owned the launch
        self.assertNotIn("BUTTONS = TwoP", self.wn.read_text())

    def test_handheld_cc_sinden_default_takes_full_gun_mode(self):
        # REVIEW FIX: a Sinden stem hand-edited into the global undocked_profile reaches
        # seat 1 through the handheld-Classic rung (rung "cc"); it must STILL reroute to
        # the full sinden mode (sinden_pick() gates the gun driver on this resolution — a
        # body copy here would start the driver against a non-gun config).
        ws._cc_capable = lambda rom: True
        ws._is_docked = lambda: False
        self.be_wii = {"undocked_profile": "Sinden Lightgun P1"}
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "sinden")
        self.assertEqual(self.ran, ["sinden"])
        self.assertEqual(self.wn.read_text(), _RESTING)       # no body copy
        self.assertTrue(ws._BACKUP.is_file())                 # transient
        self.assertTrue(ws.sinden_pick("/ROMs/wii/cc.rvz"))   # lockstep with the driver gate

    def test_explicit_pergame_dup_beats_inherited_global_seat(self):
        # REVIEW FIX: "a per-game pick wins" must hold per STEM: the same profile inherited
        # on Player 1 and explicitly picked on Player 2 seats as Player 2, not Player 1.
        ws.dolphin_wii_tdb.is_sideways = lambda gid: True
        self.be_wii = {"docked_profile_sideways": "SameP",
                       "pergame": {self.GID: {"docked_profile_p2": "SameP"}}}
        self.assertEqual(ws.apply("/ROMs/wii/x.rvz", _LOG), "classic")
        self.assertIn("Source = 0", self._slot(1))            # inherited seat dropped
        self.assertIn("BUTTONS = SameP", self._slot(2))       # explicit pick kept

    def test_docked_cc_with_explicit_p1_uses_the_seat_writer(self):
        ws._cc_capable = lambda rom: True
        seen = {}
        def fake_assign(text, assign=None):
            seen["called"] = True
            return text, [(1, "Pad")]
        dolphin_wii_pads.assign_text = fake_assign
        self.be_wii = {"pergame": {self.GID: {"docked_profile": "GameOne",
                                              "docked_profile_p2": "GameTwo"}}}
        self.assertEqual(ws.apply("/ROMs/wii/cc.rvz", _LOG), "classic")
        self.assertFalse(seen.get("called"))                  # explicit P1: pads rail bypassed
        self.assertIn("BUTTONS = GameOne", self._slot(1))
        self.assertIn("BUTTONS = GameTwo", self._slot(2))


if __name__ == "__main__":
    unittest.main()
