"""resolve_ports must treat a pin to a player slot BEYOND the game's port count
as a true no-op: the pinned pad returns to normal resolution instead of being
claimed-yet-never-placed (which removed it from routing entirely).

Regression for the finding "resolve_ports claims out-of-range pinned devices,
removing them from routing entirely" (2026-07-15 review #16). Before the fix,
resolve_ports seeded `claimed` from ALL pins (incl. out-of-range) but seeded
`out` only from in-range pins, so an out-of-range pin's pad was skipped by both
token resolution AND the fallback rescue -> an in-range port could land on
RetroArch's "N/A" despite a usable pad connected.

The tests run resolve_pins -> resolve_ports end to end (the real claim seeding),
xport="" so is_xarcade short-circuits and the fakes need no sysfs phys.

Run:  python3 -m unittest tests.test_routing_pin_out_of_range -v
"""
from __future__ import annotations

import unittest

from lib import routing
from lib.devices import pin_id
from tests._fakes import dev

# Distinct-model pads so a NAME/family token picks each unambiguously.
DS = ("054c:0ce6", "DualSense Wireless Controller")   # family "DualSense"
BIT = ("2dc8:6101", "8BitDo Pro 2")                    # family "8BitDo"


def _ds(path="/dev/input/event3"):
    return dev(DS[0], path, DS[1])


def _bit(path="/dev/input/event5"):
    return dev(BIT[0], path, BIT[1])


def _resolve(pins, devs, ports):
    """policy pins -> resolve_pins -> resolve_ports, mirroring the router."""
    pinned, claimed = routing.resolve_pins(pins, devs)
    return routing.resolve_ports(ports, devs, preassigned=pinned,
                                 preclaimed=claimed, xport="")


class OutOfRangePinIsANoOp(unittest.TestCase):
    def test_only_pad_pinned_out_of_range_still_reaches_p1(self):
        # The worst case: the ONLY connected pad is pinned to P3 and a 2-player
        # game launches. The pad must NOT vanish - it rescues onto P1.
        bit = _bit()
        out = _resolve({"3": pin_id(bit)}, [bit], [["8BitDo", "DualSense"]] * 2)
        self.assertIs(out.get(1), bit, "out-of-range pin must not delete the pad")

    def test_out_of_range_pin_frees_pad_for_a_lower_port(self):
        # DualSense + 8BitDo, 8BitDo pinned to P3 on a 2-player game: DualSense
        # takes P1 by token, and the released 8BitDo fills P2.
        ds, bit = _ds(), _bit()
        out = _resolve({"3": pin_id(bit)}, [ds, bit],
                       [["DualSense", "8BitDo"]] * 2)
        self.assertIs(out.get(1), ds)
        self.assertIs(out.get(2), bit)

    def test_in_range_pin_still_lands_and_claims(self):
        # Guard against over-fixing: a normal in-range pin (P2) must still be
        # honored and still claim its pad so it can't double-assign.
        ds, bit = _ds(), _bit()
        out = _resolve({"2": pin_id(bit)}, [ds, bit],
                       [["DualSense", "8BitDo"]] * 2)
        self.assertIs(out.get(2), bit, "in-range pin must be honored")
        self.assertIs(out.get(1), ds)

    def test_out_of_range_pin_does_not_suppress_fallback(self):
        # A lone pad pinned to P4 on a 1-player game: it must fall back onto P1
        # (the pin neither places it nor blocks the rescue).
        bit = _bit()
        out = _resolve({"4": pin_id(bit)}, [bit], [["DualSense", "Xbox"]])
        self.assertIs(out.get(1), bit)


if __name__ == "__main__":
    unittest.main()
