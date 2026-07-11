"""Handheld-only Daphne/Hypseus Deck input swap rail (lib/daphne_input.py).

The Deck default re-values coin/start to the Deck's SDL buttons; the handheld swap replaces the shared
hypinput.ini with the Deck map and reverts on game-end (docked map untouched); a crash orphan heals.
Run: python3 -m unittest tests.test_daphne_input -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import daphne_input as di, hypinput

_DOCKED = ("[KEYBOARD]\nKEY_COIN1 = SDLK_5 0 7\nKEY_START1 = SDLK_1 0 8\n"
           "KEY_BUTTON1 = SDLK_a 0 1\nEND\n")


class DaphneInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.glob = self.d / "hypinput.ini"
        self.glob.write_text(_DOCKED)
        self.deck = self.d / "hypinput.deck.ini"
        self.rail = self.d / "hypinput.ini.docked-rail"
        self._p = [mock.patch.object(hypinput, "GLOBAL_INI", self.glob),
                   mock.patch.object(di, "DECK_INI", self.deck),
                   mock.patch.object(di, "_RAIL", self.rail)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def _apply(self, handheld):
        with mock.patch.object(di, "_handheld", lambda: handheld):
            di.apply()

    def test_deck_default_fixes_coin_start(self):
        hi = hypinput.parse(di.deck_default_text())
        self.assertEqual(hi.button_value("COIN1"), 5)     # Select on the Deck (was 7 = X-Arcade)
        self.assertEqual(hi.button_value("START1"), 7)    # Start on the Deck (was 8)
        self.assertEqual(hi.button_value("BUTTON1"), 1)   # A -- already correct on both

    def test_handheld_swap_and_revert(self):
        docked = self.glob.read_text()
        self._apply(True)
        self.assertEqual(hypinput.load(self.glob).button_value("COIN1"), 5)   # Deck map is live
        self.assertTrue(self.rail.exists())                                   # docked map backed up
        di.sweep()
        self.assertEqual(self.glob.read_text(), docked)                       # docked restored exactly
        self.assertFalse(self.rail.exists())

    def test_docked_noop(self):
        docked = self.glob.read_text()
        self._apply(False)
        self.assertEqual(self.glob.read_text(), docked)
        self.assertFalse(self.rail.exists())

    def test_crash_orphan_self_heal(self):
        docked = self.glob.read_text()
        self._apply(True)                                 # crash: deck map live + rail present
        self.assertNotEqual(self.glob.read_text(), docked)
        self._apply(True)                                 # next launch sweeps the orphan first, re-applies
        self.assertTrue(self.rail.exists())
        di.sweep()
        self.assertEqual(self.glob.read_text(), docked)   # docked never corrupted by the churn

    def test_corrupt_rail_never_promoted(self):
        # a torn/empty rail (a crashed non-atomic backup) must be DROPPED, never written over the
        # intact docked map -- else sweep would destroy the docked X-Arcade config.
        docked = self.glob.read_text()
        self.rail.write_text("")                          # 0-byte torn rail
        di.sweep()
        self.assertEqual(self.glob.read_text(), docked)   # docked map untouched
        self.assertFalse(self.rail.exists())              # bad rail discarded
        self.rail.write_text("garbage no envelope")       # non-empty but not a hypinput
        di.sweep()
        self.assertEqual(self.glob.read_text(), docked)
        self.assertFalse(self.rail.exists())

    def test_apply_skips_broken_global(self):
        self.glob.write_text("garbage")                   # a broken global -> never back up / swap it
        self._apply(True)
        self.assertEqual(self.glob.read_text(), "garbage")
        self.assertFalse(self.rail.exists())

    def test_deck_default_has_deck_banner(self):
        text = di.deck_default_text()
        self.assertIn("# Steam Deck buttons:  A=1 B=2 X=3 Y=4  Select=5 Start=7", text)
        self.assertNotIn("X-Arcade buttons", text)        # the misleading banner is replaced

    def test_edit_persists_and_reloads(self):
        hi = di.load_deck()
        hi.set_button("BUTTON1", 2)                       # A -> B
        di.save_deck(hi)
        self.assertEqual(di.load_deck().button_value("BUTTON1"), 2)


if __name__ == "__main__":
    unittest.main()
