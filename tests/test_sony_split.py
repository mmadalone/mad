"""DualShock 4 is a SEPARATE controller family from DualSense (both vendor 054c).

Two things are proven here:
  * routing.family_of maps Sony product ids to the right family (DS4 pids ->
    "DualShock 4", DS5/Edge/unknown 054c -> "DualSense"), plus 8BitDo / Xbox.
  * routing.resolve_ports matches a priority token by family id, not only by
    device name — so a DS4 that enumerates with the generic name
    "Wireless Controller" is picked by the "DualShock 4" token, and a DS5 and a
    DS4 connected together land on the players the rule orders them into.

resolve_ports is fed xport="" (no X-Arcade identified) so is_xarcade()
short-circuits and the fakes need no sysfs `phys`.

Run:  python3 -m unittest tests.test_sony_split -v
"""
from __future__ import annotations

import unittest

from lib import routing
from tests._fakes import dev

# Real-world names from the router log: the DS4 reports the GENERIC name, the
# DS5 carries "DualSense". The split must work regardless of these names.
DS5 = ("054c:0ce6", "DualSense Wireless Controller")
DS4 = ("054c:09cc", "Wireless Controller")
XARCADE = ("045e:02a1", "Xbox 360 Wireless Receiver")

# The order saved for the `racing` collection after dragging DualShock 4 under
# DualSense (both ports get the same list; the cascade splits them).
ORDER = ["DualSense", "DualShock 4", "Xbox", "Wii Remote Pro",
         "8BitDo", "Steam Deck", "X-Arcade"]


def _ds5(path="/dev/input/event10"):
    return dev(DS5[0], path, DS5[1])


def _ds4(path="/dev/input/event11"):
    return dev(DS4[0], path, DS4[1])


def _xarcade(path="/dev/input/event20"):
    return dev(XARCADE[0], path, XARCADE[1])


class FamilyOf(unittest.TestCase):
    def test_dualsense(self):
        self.assertEqual(routing.family_of(_ds5()), "DualSense")
        self.assertEqual(routing.family_of(dev("054c:0df2", "/d/e0")), "DualSense")  # Edge

    def test_dualshock4(self):
        self.assertEqual(routing.family_of(_ds4()), "DualShock 4")
        self.assertEqual(routing.family_of(dev("054c:05c4", "/d/e1")), "DualShock 4")  # v1
        self.assertEqual(routing.family_of(dev("054c:0ba0", "/d/e2")), "DualShock 4")  # adapter

    def test_unknown_sony_defaults_to_dualsense(self):
        self.assertEqual(routing.family_of(dev("054c:0268", "/d/e3")), "DualSense")  # DS3

    def test_other_families(self):
        self.assertEqual(routing.family_of(_xarcade()), "Xbox")
        self.assertEqual(routing.family_of(dev("2dc8:6101", "/d/e4", "8BitDo Pro 2")),
                         "8BitDo")
        self.assertIsNone(routing.family_of(dev("dead:beef", "/d/e5", "Mystery Pad")))


class ResolvePortsSonySplit(unittest.TestCase):
    def _reserved(self, ports, devs):
        out = routing.resolve_ports(ports, devs, xport="")
        return {p: d.path for p, d in out.items()}

    def test_ds5_and_ds4_order_dualsense_first(self):
        ds5, ds4 = _ds5(), _ds4()
        out = routing.resolve_ports([list(ORDER), list(ORDER)], [ds5, ds4], xport="")
        self.assertIs(out.get(1), ds5)   # DualSense above DualShock 4 -> P1
        self.assertIs(out.get(2), ds4)

    def test_ds5_and_ds4_order_dualshock_first(self):
        ds5, ds4 = _ds5(), _ds4()
        rev = ["DualShock 4", "DualSense", "Xbox", "X-Arcade"]
        out = routing.resolve_ports([list(rev), list(rev)], [ds5, ds4], xport="")
        self.assertIs(out.get(1), ds4)   # flip the order -> DS4 is P1
        self.assertIs(out.get(2), ds5)

    def test_lone_ds4_beats_xarcade_for_p1(self):
        # The bug from the on-device test: a DS4 ("Wireless Controller") + the
        # X-Arcade. With the family token, the DS4 wins P1 instead of the stick.
        ds4 = _ds4()
        xa1, xa2 = _xarcade("/dev/input/event20"), _xarcade("/dev/input/event21")
        out = routing.resolve_ports([list(ORDER), list(ORDER)], [ds4, xa1, xa2], xport="")
        self.assertIs(out.get(1), ds4)
        self.assertEqual(out.get(2).vid, 0x045e)   # P2 falls through to the X-Arcade

    def test_dualsense_token_does_not_grab_a_ds4(self):
        # Separation: a rule that lists only "DualSense" must NOT match a DS4 by
        # that token (the DS4 still gets rescued onto an empty port by fallback).
        ds4 = _ds4()
        only_ds = [["DualSense", "Xbox"], ["DualSense", "Xbox"]]
        out = routing.resolve_ports(only_ds, [ds4], xport="")
        # Not matched by the DualSense token, but fallback rescues the lone pad.
        self.assertIs(out.get(1), ds4)
        self.assertEqual(routing._family_token(ds4), "dualshock 4")


if __name__ == "__main__":
    unittest.main()
