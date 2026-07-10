"""Handheld Deck-pad fallback for the standalone launch binder (switch_bind._resolve_pads).

In Game Mode the Deck's built-in pad surfaces to SDL only as the Steam virtual 28de:11ff, which
pads_cmds._real_pads drops as a phantom -- so a handheld launch with nothing else connected used
to bind NO pad. _resolve_pads now re-admits the ONE Deck pad (pads_cmds.deck_virtual_pad) as
Player 1 when handheld AND no external pad is present; docked, or with an external pad, behaviour
is unchanged. The downstream pads_cmds helpers are stubbed so these tests assert the injection
logic alone. Run: python3 -m unittest tests.test_switch_handheld_deckpad -v
"""
from __future__ import annotations

import os
import unittest

from lib import switch_bind
from lib.madsrv import pads_cmds
from tests._fakes import sd

DECK = "28de:11ff"       # the Steam virtual Deck pad (what Game Mode SDL shows)
HC = "28de:1205"         # handheld_class = the Deck's CANONICAL id (evdev / outside gamescope)
DS5 = "054c:0ce6"        # a real external pad


class HandheldDeckFallback(unittest.TestCase):
    def setUp(self):
        # Isolate _resolve_pads' downstream: identity stubs so the return value == the `real`
        # list after the Deck-injection step (the only thing under test here). _handheld_class
        # returns the CANONICAL 1205 (as the shipped policy does), NOT the injected pad's 11ff --
        # so these also prove the deck_forms filter treats the 11ff Deck as the fallback.
        self._orig = {}
        for name, fn in (
            ("_supported", lambda emu, pads: list(pads)),
            ("_ordered", lambda emu, pads, allpads=None, *, order=None: list(pads)),
            ("_handheld_class", lambda emu: HC),
            ("managed_players", lambda emu: 8),
        ):
            self._orig[name] = getattr(pads_cmds, name)
            setattr(pads_cmds, name, fn)
        self._apply_pins = switch_bind._apply_pins
        switch_bind._apply_pins = lambda emu, ordered, quiet=False: ordered
        import lib.policy as policy
        self._lm = policy.load_merged
        policy.load_merged = lambda: {"handheld": {"enabled": True}}     # on-the-go enabled
        # Default scenario: no external pad, a Deck pad available.
        self._real = pads_cmds._real_pads
        self._deck = pads_cmds.deck_virtual_pad
        pads_cmds._real_pads = lambda *a, **k: []
        pads_cmds.deck_virtual_pad = lambda *a, **k: sd(0, DECK, "gdeck", "Steam Deck Controller")
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(pads_cmds, name, fn)
        switch_bind._apply_pins = self._apply_pins
        pads_cmds._real_pads = self._real
        pads_cmds.deck_virtual_pad = self._deck
        import lib.policy as policy
        policy.load_merged = self._lm
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def test_handheld_no_external_binds_deck(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        chosen = switch_bind._resolve_pads("eden", quiet=True)
        self.assertEqual([d.vidpid for d in chosen], [DECK])       # the fix: Deck is Player 1

    def test_docked_no_external_binds_nothing(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertEqual(switch_bind._resolve_pads("eden", quiet=True), [])   # unchanged: no Deck

    def test_handheld_external_pad_preferred(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        pads_cmds._real_pads = lambda *a, **k: [sd(0, DS5, "gds", "DualSense")]
        chosen = switch_bind._resolve_pads("eden", quiet=True)
        self.assertEqual([d.vidpid for d in chosen], [DS5])        # external wins, Deck not added

    def test_handheld_no_deck_present_binds_nothing(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        pads_cmds.deck_virtual_pad = lambda *a, **k: None
        self.assertEqual(switch_bind._resolve_pads("eden", quiet=True), [])   # graceful: no phantom

    def test_feature_disabled_handheld_binds_nothing(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        import lib.policy as policy
        policy.load_merged = lambda: {"handheld": {"enabled": False}}         # on-the-go off
        self.assertEqual(switch_bind._resolve_pads("eden", quiet=True), [])   # legacy behaviour


class DeckVirtualPadSelection(unittest.TestCase):
    """deck_virtual_pad picks the ONE live Deck out of a possible 11ff phantom pool."""

    def setUp(self):
        self._sdl = pads_cmds.sdl_devices

    def tearDown(self):
        pads_cmds.sdl_devices = self._sdl

    def _set(self, devs):
        pads_cmds.sdl_devices = lambda *a, **k: devs

    def test_none_when_absent(self):
        self._set([sd(0, DS5, "g", "DualSense")])
        self.assertIsNone(pads_cmds.deck_virtual_pad())

    def test_prefers_steam_deck_named(self):
        self._set([sd(0, DECK, "g0", "Microsoft X-Box 360 pad 0"),
                   sd(1, DECK, "g1", "Steam Deck Controller")])
        self.assertEqual(pads_cmds.deck_virtual_pad().name, "Steam Deck Controller")

    def test_prefers_assigned_slot_when_name_misses(self):
        # SDL2 names every 11ff "Microsoft X-Box 360 pad N"; pick the one Steam gave a player slot
        # (player_index >= 0), not a dead ghost at index 0.
        from lib.devices import SdlDevice
        self._set([SdlDevice(0, DECK, "g0", "Microsoft X-Box 360 pad 0", -1),
                   SdlDevice(1, DECK, "g1", "Microsoft X-Box 360 pad 1", 0)])
        self.assertEqual(pads_cmds.deck_virtual_pad().index, 1)


if __name__ == "__main__":
    unittest.main()
