"""
Focused unit tests for the shared pad_assign pipeline — the collision policy and
the two pcsx2-quirk flags — independent of any backend's file I/O. The golden
suite (test_golden) proves end-to-end byte parity; these pin the helper's
contract directly, especially the xemu spare-count fix.

Encoders are trivial here: ``encode_pin`` returns the pin object verbatim, so a
test controls slot values directly.
"""
from __future__ import annotations

import unittest
from collections import Counter

from lib.pad_assign import assign_slots
from tests._fakes import sd

A, B, H = "aaaa:0001", "bbbb:0002", "28de:1205"   # two pad classes + handheld


def _sdl(*specs):
    """specs = (vidpid, guid); index = position."""
    return [sd(i, vp, g, vp) for i, (vp, g) in enumerate(specs)]


def _run(sdl, pins, *, unit_count=lambda v: 1, base_index=1,
         filter_pins_at_resolve=True, dedup_pins=False, manage=2):
    """assign_slots with identity encoders (value == the chosen guid / pin obj)."""
    return assign_slots(
        sdl, manage, pins, [object()],
        pad_classes=[A, B], handheld=H,
        encode_auto=lambda d, rank: d.guid,
        encode_pin=lambda pdev, s, e: pdev,
        unit_count=unit_count, base_index=base_index,
        filter_pins_at_resolve=filter_pins_at_resolve, dedup_pins=dedup_pins)


class CollisionSpareCount(unittest.TestCase):
    def test_value_membership_drops_auto_collider(self):
        # auto P1='gA', P2='gB'; pin P2='gA' -> P1 (non-pinned, value gA) dropped.
        sdl = _sdl((A, "gA"), (B, "gB"))
        got = _run(sdl, {2: "gA"})
        self.assertEqual(got, {2: "gA"})

    def test_spare_count_two_units_keeps_other_port(self):
        # two units of class 'g' present; pin port2='g' -> auto port1='g' KEPT.
        sdl = _sdl((A, "g"), (A, "g"))
        units = Counter(d.guid for d in sdl)            # units['g'] == 2
        got = _run(sdl, {2: "g"}, unit_count=lambda v: units[v])
        self.assertEqual(got, {1: "g", 2: "g"})

    def test_spare_count_one_unit_drops_other_port(self):
        # one unit of class 'g'; pin port2='g' -> auto port1='g' DROPPED (the fix).
        sdl = _sdl((A, "g"), (B, "gB"))                 # only one 'g'
        units = Counter(d.guid for d in sdl)            # units['g'] == 1
        got = _run(sdl, {2: "g"}, unit_count=lambda v: units[v])
        self.assertEqual(got, {2: "g"})


class PinQuirkFlags(unittest.TestCase):
    def test_dedup_pins_interleaved_keeps_higher_slot(self):
        sdl = _sdl((A, "gA"), (B, "gB"))
        # both ports pinned to the same value -> dedup keeps only the higher slot.
        self.assertEqual(_run(sdl, {1: "x", 2: "x"}, dedup_pins=True), {2: "x"})
        # batch (default) keeps both pinned slots.
        self.assertEqual(_run(sdl, {1: "x", 2: "x"}, dedup_pins=False), {1: "x", 2: "x"})

    def test_filter_pins_at_resolve_gates_handheld(self):
        sdl = _sdl((H, "gDeck"))                          # only the Deck present
        # over-manage pin, no PS pads: filter=False (pcsx2) suppresses handheld.
        self.assertEqual(_run(sdl, {5: "x"}, filter_pins_at_resolve=False), {})
        # filter=True (xemu/eden/rpcs3): the out-of-range pin is ignored, Deck binds.
        self.assertEqual(_run(sdl, {5: "x"}, filter_pins_at_resolve=True), {1: "gDeck"})


class HandheldAndEmpty(unittest.TestCase):
    def test_handheld_none_when_no_deck(self):
        # ps empty (class not in pad_classes) and no handheld -> None (untouched).
        self.assertIsNone(_run(_sdl(("zzzz:9999", "gZ")), {}))

    def test_base_index_zero(self):
        sdl = _sdl((A, "gA"), (B, "gB"))
        self.assertEqual(_run(sdl, {}, base_index=0), {0: "gA", 1: "gB"})


if __name__ == "__main__":
    unittest.main()
