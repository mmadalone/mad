"""Tests for the launch-time GameCube multi-seat port swap (lib/dolphin_gc_dock).
(The old dolphin_gc_dock_cmds "Dock / handheld" settings page was retired 2026-08-04 —
its picker became Player 1 of the dolphin_gc_hh_profiles page, tested in
tests.test_dolphin_profile_cmds.)

Run:  python3 -m unittest tests.test_dolphin_gc_dock -v
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_gc_dock as dk
from lib import dolphin_gc_pads
from lib.madsrv import cfgutil

_LOG = logging.getLogger("test")


class LaunchBinder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gc = self.tmp / "GCPadNew.ini"
        self.gc.write_text("[GCPad1]\nDevice = SDL/0/Real\nButtons/A = EAST\n"
                           "[GCPad2]\nDevice = evdev/1/X\nButtons/A = SOUTH\n")
        self._save = (dk._FILE, dk._BACKUP, dk._be, dk._is_docked,
                      dk.dolphin_profiles.profile_body, dolphin_gc_pads.assign_text,
                      dolphin_gc_pads.plan_assignment)
        dk._FILE = self.gc
        dk._BACKUP = self.tmp / "GCPadNew.ini.dock-backup"
        dk.dolphin_profiles.profile_body = lambda name: "Device = SDL/0/Deck\nButtons/A = `Button S`\n"
        # plan_assignment is what dk.plan() consults, so it must be stubbed too or apply() would
        # do a REAL SDL walk against whatever pads happen to be plugged into the dev box.
        # assign_text stays REAL (pure text): the single writer path now uses it in BOTH contexts.
        dolphin_gc_pads.plan_assignment = lambda: []            # default: nothing connected

    def tearDown(self):
        (dk._FILE, dk._BACKUP, dk._be, dk._is_docked,
         dk.dolphin_profiles.profile_body, dolphin_gc_pads.assign_text,
         dolphin_gc_pads.plan_assignment) = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dev(self, sec):
        return cfgutil.ini_read(self.gc.read_text(), sec, "Device")

    def _handheld(self, profile="Steamdeck", on=True):
        # `on` sets the RETIRED dock_autodetect flag — kept in fixtures to prove it is inert.
        dk._is_docked = lambda: False
        dk._be = lambda: {"dock_autodetect": on, "undocked_profile": profile}

    def test_handheld_swaps_and_restores(self):
        self._handheld()
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Deck")     # undocked profile applied to P1
        self.assertTrue(dk._BACKUP.is_file())
        self.assertEqual(self._dev("GCPad2"), "evdev/1/X")      # only GCPad1 touched
        dk.restore(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")     # reverted to resting
        self.assertFalse(dk._BACKUP.is_file())

    def test_docked_no_swap(self):
        self._handheld()
        dk._is_docked = lambda: True
        dk.apply(_LOG)                                          # no pads assignment -> untouched
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")

    # ---- per-game profile overrides (2026-08-04; [backends.dolphin_gc.pergame.<GameID>]) ----
    def _pergame(self, docked: bool, key: str, profile="GameProf"):
        dk._is_docked = lambda: docked
        dk._be = lambda: {"dock_autodetect": True, "undocked_profile": "GlobalProf",
                          "pergame": {"GALE01": {key: profile}}}
        self._gid_save = dk._gameid
        dk._gameid = lambda rom: ("GALE01" if rom else None)
        self.addCleanup(lambda: setattr(dk, "_gameid", self._gid_save))
        dk.dolphin_profiles.profile_body = (
            lambda name: f"Device = SDL/0/{name}\nButtons/A = X\n")

    def test_pergame_handheld_beats_global(self):
        self._pergame(docked=False, key="handheld_profile")
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GameProf")
        self.assertEqual(self._dev("GCPad2"), "evdev/1/X")      # ports 2+ untouched
        self.assertTrue(dk._BACKUP.is_file())

    def test_pergame_docked_overrides_its_port_pads_fill_the_rest(self):
        # Multi-seat 2026-08-04: a per-game pick overrides ONLY its own port; unset ports
        # keep the normal docked setup (the pads priority still fills them).
        self._pergame(docked=True, key="docked_profile")
        dolphin_gc_pads.plan_assignment = lambda: [(1, "RailProf"), (2, "RailProf2")]
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GameProf")  # per-game wins for port 1
        self.assertEqual(self._dev("GCPad2"), "SDL/0/RailProf2")  # pads priority fills port 2

    def test_pergame_ignored_without_rom(self):
        self._pergame(docked=False, key="handheld_profile")
        dk.apply(_LOG)                                           # rom-less (old callers / preview)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GlobalProf")

    def test_autodetect_flag_is_inert(self):
        # The dock_autodetect toggle was RETIRED with the multi-seat rework (Miquel's call
        # 2026-08-04): a set row seats, '(none)' means no swap. A stored False stays inert.
        self._pergame(docked=False, key="handheld_profile")
        be = dk._be()
        be["dock_autodetect"] = False
        dk._be = lambda: be
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GameProf")  # per-game seat still applies
        dk.restore(_LOG)
        dk._be = lambda: {"dock_autodetect": False, "undocked_profile": "GlobalProf"}
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GlobalProf")  # global seat too

    def test_stale_pergame_pick_falls_through(self):
        # REVIEW FIX: a per-game pick whose profile file is gone must not mask the
        # still-valid global undocked profile (handheld) / pads rail (docked).
        self._pergame(docked=False, key="handheld_profile", profile="GhostProf")
        dk.dolphin_profiles.profile_body = (
            lambda name: None if name == "GhostProf"
            else f"Device = SDL/0/{name}\nButtons/A = X\n")
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GlobalProf")   # fell through to global

    def test_no_pergame_key_keeps_global_behavior(self):
        dk._is_docked = lambda: False
        dk._be = lambda: {"dock_autodetect": True, "undocked_profile": "GlobalProf",
                          "pergame": {"GALE01": {"hhres": "2x"}}}   # unrelated per-game keys only
        self._gid_save = dk._gameid
        dk._gameid = lambda rom: "GALE01"
        self.addCleanup(lambda: setattr(dk, "_gameid", self._gid_save))
        dk.dolphin_profiles.profile_body = (
            lambda name: f"Device = SDL/0/{name}\nButtons/A = X\n")
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/GlobalProf")

    def test_docked_applies_pads_priority(self):
        # docked + a pads->players assignment -> apply it (transient) + snapshot + restore
        self._handheld()
        dk._is_docked = lambda: True
        dolphin_gc_pads.plan_assignment = lambda: [(1, "GC WiiU 1")]
        # apply() resolves the plan ONCE and threads it in; assert it hands assign_text exactly
        # that plan rather than letting it re-resolve (two resolutions could disagree if a pad
        # connects between them, which is the drift this whole change exists to remove).
        seen = []
        def _fake(text, assign=None):
            seen.append(assign)
            return text.replace("Device = SDL/0/Real", "Device = SDL/0/WiiU"), [(1, "GC WiiU 1")]
        dolphin_gc_pads.assign_text = _fake
        dk.apply(_LOG)
        self.assertEqual(seen, [[(1, "GC WiiU 1")]])            # threaded through, not re-resolved
        self.assertEqual(self._dev("GCPad1"), "SDL/0/WiiU")     # profile assigned to P1
        self.assertTrue(dk._BACKUP.is_file())
        self.assertEqual(self._dev("GCPad2"), "evdev/1/X")      # other ports untouched
        dk.restore(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")     # reverted after the game

    # --- plan(): the ONE decision apply() acts on and MAD's Preview renders. Preview used to
    # re-derive its own answer from backends.dolphin_gc.pad_classes -- a key that does not exist --
    # so every gc row read "(no player pad -> unchanged)" while the router happily assigned pads.
    # These pin the decision itself, so the two surfaces cannot drift apart again.

    def test_plan_docked_reports_the_pad_assignment(self):
        self._handheld()
        dk._is_docked = lambda: True
        dolphin_gc_pads.plan_assignment = lambda: [(1, "GC WiiU 1"), (2, "GC Dualsense 1")]
        self.assertEqual(dk.plan(), {"mode": "docked",
                                     "assign": [(1, "GC WiiU 1"), (2, "GC Dualsense 1")],
                                     "note": ""})

    def test_plan_docked_empty_says_why(self):
        self._handheld()
        dk._is_docked = lambda: True                 # nothing connected -> normal mapping
        p = dk.plan()
        self.assertEqual((p["mode"], p["assign"]), ("docked", []))
        self.assertTrue(p["note"])                   # an empty answer must explain itself

    def test_plan_handheld_reports_the_undocked_profile(self):
        self._handheld()                             # undocked_profile="Steamdeck", autodetect on
        self.assertEqual(dk.plan(), {"mode": "handheld", "assign": [(1, "Steamdeck")], "note": ""})

    def test_plan_handheld_autodetect_off_still_seats(self):
        self._handheld(on=False)                     # retired flag: plan ignores it
        self.assertEqual(dk.plan(), {"mode": "handheld", "assign": [(1, "Steamdeck")], "note": ""})

    def test_plan_handheld_no_profile(self):
        self._handheld(profile="")
        p = dk.plan()
        self.assertEqual((p["mode"], p["assign"]), ("handheld", []))
        self.assertTrue(p["note"])

    def test_plan_follows_dock_state(self):
        # The whole point: the SAME rig gives a DIFFERENT answer docked vs handheld. Preview was
        # byte-identical in both states, which is how it shipped a confidently wrong picture.
        self._handheld()
        dolphin_gc_pads.plan_assignment = lambda: [(1, "GC WiiU 1")]
        handheld = dk.plan()
        dk._is_docked = lambda: True
        docked = dk.plan()
        self.assertNotEqual(handheld["assign"], docked["assign"])
        self.assertEqual(handheld["assign"], [(1, "Steamdeck")])
        self.assertEqual(docked["assign"], [(1, "GC WiiU 1")])

    def test_no_profile_no_swap(self):
        self._handheld(profile="")
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")

    # ---- multi-seat (ports 1-4, both contexts; 2026-08-04) ----
    def _named_bodies(self):
        dk.dolphin_profiles.profile_body = (
            lambda name: None if name.startswith("Ghost")
            else f"Device = SDL/0/{name}\nButtons/A = X\n")

    def test_handheld_seats_port2_from_global_key(self):
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile": "HandOne", "undocked_profile_p2": "HandTwo"}
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/HandOne")
        self.assertEqual(self._dev("GCPad2"), "SDL/0/HandTwo")   # handheld multiplayer seat

    def test_handheld_unset_port_stays_resting(self):
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile_p2": "HandTwo"}      # port 1 deliberately unset
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")      # untouched, NOT disabled
        self.assertEqual(self._dev("GCPad2"), "SDL/0/HandTwo")

    def test_pergame_port2_beats_global_port2(self):
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile": "HandOne", "undocked_profile_p2": "HandTwo",
                          "pergame": {"GALE01": {"handheld_profile_p2": "GameTwo"}}}
        self._gid_save = dk._gameid
        dk._gameid = lambda rom: "GALE01"
        self.addCleanup(lambda: setattr(dk, "_gameid", self._gid_save))
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/HandOne")
        self.assertEqual(self._dev("GCPad2"), "SDL/0/GameTwo")

    def test_duplicate_profile_falls_through_per_port(self):
        # The same stem on two ports would mirror one pad; the later port falls to its next
        # rung (here: the global P2 seat) instead of duplicating.
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile": "SameProf", "undocked_profile_p2": "HandTwo",
                          "pergame": {"GALE01": {"handheld_profile_p2": "SameProf"}}}
        self._gid_save = dk._gameid
        dk._gameid = lambda rom: "GALE01"
        self.addCleanup(lambda: setattr(dk, "_gameid", self._gid_save))
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad1"), "SDL/0/SameProf")
        self.assertEqual(self._dev("GCPad2"), "SDL/0/HandTwo")   # dup dropped -> global rung

    def test_stale_seat_falls_through_per_port(self):
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile": "HandOne", "undocked_profile_p2": "HandTwo",
                          "pergame": {"GALE01": {"handheld_profile_p2": "GhostProf"}}}
        self._gid_save = dk._gameid
        dk._gameid = lambda rom: "GALE01"
        self.addCleanup(lambda: setattr(dk, "_gameid", self._gid_save))
        dk.apply(_LOG, rom="/roms/gc/melee.rvz")
        self.assertEqual(self._dev("GCPad2"), "SDL/0/HandTwo")   # stale per-game -> global rung

    def test_plan_reports_multi_seat_maps(self):
        self._named_bodies()
        dk._is_docked = lambda: False
        dk._be = lambda: {"undocked_profile": "HandOne", "undocked_profile_p3": "HandThree"}
        self.assertEqual(dk.plan(), {"mode": "handheld",
                                     "assign": [(1, "HandOne"), (3, "HandThree")], "note": ""})

    def test_docked_reverts_leftover_swap(self):
        self._handheld()
        dk.apply(_LOG)                                          # swap + snapshot
        dk._is_docked = lambda: True                 # now docked
        dk.apply(_LOG)                                          # -> reverts the leftover swap
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")
        self.assertFalse(dk._BACKUP.is_file())

    def test_restore_noop_without_backup(self):
        self.assertFalse(dk.restore(_LOG))                     # nothing to restore

    def test_restore_failure_leaves_config_untouched(self):
        # if restore() can't consume a surviving backup, apply() must NOT swap (never clobber the
        # good resting snapshot with a transient).
        self.gc.write_text("[GCPad1]\nDevice = SDL/0/Resting\n")
        good = b"[GCPad1]\nDevice = SDL/0/TRUE-RESTING\n"
        dk._BACKUP.write_bytes(good)                           # a surviving snapshot
        self._handheld()
        orig = dk.restore
        dk.restore = lambda logger=None: False                 # simulate restore failure (leaves backup)
        self.addCleanup(lambda: setattr(dk, "restore", orig))
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Resting")     # config untouched
        self.assertEqual(dk._BACKUP.read_bytes(), good)            # good snapshot NOT clobbered

    def test_is_docked_uses_deck_state(self):
        # The gate now reads the REAL dock signal (deck_state), not pad presence -- so a Bluetooth
        # pad while undocked no longer reads as docked. Fail-safe to docked on any error.
        _orig = dk.deck_state.is_docked
        self.addCleanup(lambda: setattr(dk.deck_state, "is_docked", _orig))
        dk.deck_state.is_docked = lambda force=None: False
        self.assertFalse(dk._is_docked())                      # deck_state: handheld -> handheld
        dk.deck_state.is_docked = lambda force=None: True
        self.assertTrue(dk._is_docked())                       # deck_state: docked -> docked

        def _boom(force=None):
            raise RuntimeError("sysfs read failed")
        dk.deck_state.is_docked = _boom
        self.assertTrue(dk._is_docked())                       # error -> fail-safe docked

    def test_atomic_restore_leaves_full_file(self):
        # restore() must not truncate GCPadNew.ini (atomic temp+replace)
        self._handheld()
        dk.apply(_LOG)
        dk.restore(_LOG)
        self.assertIn("[GCPad1]", self.gc.read_text())
        self.assertIn("[GCPad2]", self.gc.read_text())
        self.assertFalse((self.tmp / "GCPadNew.ini.dock-tmp").exists())     # temp cleaned up


if __name__ == "__main__":
    unittest.main()
