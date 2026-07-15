"""Regression tests for gamepads.list Steam Deck handling across dock states.

Covers the handheld bug (2026-07-15): in Game Mode Steam grabs the physical Deck
pad (28de:1205 -> kbd/mouse, no face buttons) and the usable pad is the Steam-Input
virtual "Steam Deck (SI)" (28de:11ff). The old filter dropped every Valve pid != 1205,
so the Deck vanished from the gamepad tester in handheld. It must now appear (via the
primary 11ff, labelled "Steam Deck") when the physical pad isn't usable, and must NOT
double-list when the physical pad IS usable (Desktop).

Run:  python3 -m unittest tests.test_gamepads_list_deck -v
"""
from __future__ import annotations

import types
import unittest
from unittest import mock

from lib.madsrv import tester_cmds as tc


def dev(vid, pid, name, path, *, face=True, uniq="", phys="", mad_virtual=False):
    return types.SimpleNamespace(vid=vid, pid=pid, name=name, path=path,
                                 uniq=uniq, phys=phys, has_face_btn=face,
                                 is_mad_virtual=mad_virtual)


def run_list(devs):
    with mock.patch.object(tc.dv, "enumerate_devices", return_value=devs), \
         mock.patch.object(tc.dv, "port_of", return_value=None), \
         mock.patch.object(tc, "_db_slots", return_value=[]), \
         mock.patch.object(tc, "_xport", return_value=""), \
         mock.patch.object(tc, "resolve_art", side_effect=lambda c: (c[0] if c else "")):
        tc._active["stream"] = None
        return tc._gamepads_list({})["pads"]


# The physical Deck pad as it appears in Desktop mode (real joystick, face buttons).
DECK_1205_REAL = dev(0x28de, 0x1205, "Valve Software Steam Deck Controller",
                     "/dev/input/event6", face=True, uniq="MFCB50200812")
# In Game Mode the same 1205 shows up as kbd/mouse nodes with no face buttons.
DECK_1205_LIZARD = dev(0x28de, 0x1205, "Valve Software Steam Deck Controller",
                       "/dev/input/event6", face=False, uniq="MFCB50200812")
# The Steam-Input virtual pad (the usable Deck pad in Game Mode).
DECK_11FF = dev(0x28de, 0x11ff, "Microsoft X-Box 360 pad 0", "/dev/input/event10",
                face=True)


class GamepadsListDeck(unittest.TestCase):
    def _keys(self, pads):
        return [(p["name"], p["profile"]["key"]) for p in pads]

    def test_desktop_shows_physical_deck_only(self):
        pads = run_list([DECK_1205_REAL])
        self.assertEqual(self._keys(pads), [("Valve Software Steam Deck Controller", "steamdeck")])

    def test_handheld_shows_the_steam_input_virtual_as_steam_deck(self):
        pads = run_list([DECK_1205_LIZARD, DECK_11FF])
        # The physical (lizard) node is dropped; the 11ff virtual appears as "Steam Deck".
        self.assertEqual(len(pads), 1)
        p = pads[0]
        self.assertEqual(p["name"], "Steam Deck")
        self.assertEqual(p["profile"]["key"], "steamdeck")
        self.assertEqual(p["path"], "/dev/input/event10")
        self.assertEqual(p["idtail"], "handheld (Steam Input)")

    def test_no_double_listing_when_physical_deck_is_usable(self):
        # If BOTH a usable 1205 and an 11ff phantom exist, show only the physical one.
        pads = run_list([DECK_1205_REAL, DECK_11FF])
        self.assertEqual(len(pads), 1)
        self.assertEqual(pads[0]["path"], "/dev/input/event6")

    def test_handheld_pool_admits_only_the_primary_phantom(self):
        # Steam can expose a pool of 11ff phantoms; only the primary ("pad 0") shows.
        pad1 = dev(0x28de, 0x11ff, "Microsoft X-Box 360 pad 1", "/dev/input/event12")
        pads = run_list([DECK_1205_LIZARD, DECK_11FF, pad1])
        self.assertEqual(len(pads), 1)
        self.assertEqual(pads[0]["path"], "/dev/input/event10")  # "pad 0"

    def test_handheld_deck_coexists_with_an_external_pad(self):
        # An external Xbox pad in Game Mode must still list alongside the Deck.
        xbox = dev(0x045e, 0x028e, "Xbox 360 Controller", "/dev/input/event20")
        pads = run_list([DECK_1205_LIZARD, DECK_11FF, xbox])
        names = sorted(p["name"] for p in pads)
        self.assertIn("Steam Deck", names)
        self.assertIn("Xbox 360 Controller", names)
        self.assertEqual(len(pads), 2)

    def test_mad_wii_nav_virtual_still_excluded(self):
        navpad = dev(0x4d41, 0x0001, "MAD Wii Nav", "/dev/input/event30", mad_virtual=True)
        pads = run_list([DECK_1205_LIZARD, DECK_11FF, navpad])
        self.assertEqual([p["name"] for p in pads], ["Steam Deck"])


if __name__ == "__main__":
    unittest.main()
