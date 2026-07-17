"""Tests for the GameCube dock/handheld setting (dolphin_gc_dock_cmds) + the launch-time
undocked-profile swap (lib/dolphin_gc_dock).

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
from lib.madsrv import dolphin_gc_dock_cmds as dc

_LOG = logging.getLogger("test")


class DockSettings(unittest.TestCase):
    def setUp(self):
        self.store: dict = {}
        self._orig = (dc._be, dc._set_pref, dc.dolphin_profiles.list_profiles)
        dc._be = lambda: dict(self.store)
        dc._set_pref = lambda k, v: self.store.__setitem__(k, v)
        dc.dolphin_profiles.list_profiles = lambda: ["Steamdeck", "GC_base"]

    def tearDown(self):
        dc._be, dc._set_pref, dc.dolphin_profiles.list_profiles = self._orig

    def test_default_autodetect_on(self):
        s = dc._get({})["groups"][0]["settings"]
        self.assertEqual((s[0]["key"], s[0]["value"]), ("dock_autodetect", True))

    def test_toggle_autodetect_off(self):
        dc._set({"key": "dock_autodetect", "value": "0"})
        self.assertFalse(self.store["dock_autodetect"])

    def test_pick_undocked_profile(self):
        # options = ["(none)", "Steamdeck", "GC_base"]; index 1 -> "Steamdeck"
        dc._set({"key": "undocked_profile", "value": 1})
        self.assertEqual(self.store["undocked_profile"], "Steamdeck")
        self.store = {"undocked_profile": "Steamdeck"}
        enum = dc._get({})["groups"][0]["settings"][1]
        self.assertEqual(enum["options"][enum["value"]], "Steamdeck")

    def test_pick_none_clears(self):
        dc._set({"key": "undocked_profile", "value": 0})
        self.assertEqual(self.store["undocked_profile"], "")

    def test_bad_key_rejected(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            dc._set({"key": "nope", "value": 1})


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
        dolphin_gc_pads.plan_assignment = lambda: []            # default: nothing connected
        dolphin_gc_pads.assign_text = lambda text, assign=None: (text, [])

    def tearDown(self):
        (dk._FILE, dk._BACKUP, dk._be, dk._is_docked,
         dk.dolphin_profiles.profile_body, dolphin_gc_pads.assign_text,
         dolphin_gc_pads.plan_assignment) = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dev(self, sec):
        return cfgutil.ini_read(self.gc.read_text(), sec, "Device")

    def _handheld(self, profile="Steamdeck", on=True):
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

    def test_plan_handheld_autodetect_off(self):
        self._handheld(on=False)
        p = dk.plan()
        self.assertEqual((p["mode"], p["assign"]), ("handheld", []))
        self.assertIn("auto-detect off", p["note"])

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

    def test_autodetect_off_no_swap(self):
        self._handheld(on=False)
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")

    def test_no_profile_no_swap(self):
        self._handheld(profile="")
        dk.apply(_LOG)
        self.assertEqual(self._dev("GCPad1"), "SDL/0/Real")

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
