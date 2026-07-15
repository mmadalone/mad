"""routing.only_xarcade_present() decides whether the console-game "this needs a regular
gamepad, plug one in" nudge fires: True when the ONLY connected real pads are the X-Arcade
(and its Steam-virtual shadow). The MAD Wii Nav bridge pad (4d41:0001) is a uinput navigation
helper, not a real alternative controller, so it must be excluded from that test -- exactly as
every other selector in routing.py excludes is_mad_virtual. Otherwise, docked with a DolphinBar,
that always-open nav pad silently suppresses the warning.

Regression for the 2026-07-15 review finding #15.
Run:  python3 -m unittest tests.test_only_xarcade_present -v
"""
from __future__ import annotations

import unittest

from lib import routing
from tests._fakes import FakeDevice

_XPORT = "1.1"   # must equal devices.port_of(the X-Arcade fake's phys)


def _xarcade():
    # X-Arcade in Xbox mode: 045e at the identified port (phys must yield port_of == _XPORT).
    return FakeDevice(vid=0x045e, pid=0x02a1, path="/dev/input/event6",
                      name="Xbox 360 Wireless Receiver", phys="usb-xhci-hcd-1.1/input0")


def _steam_virtual():
    return FakeDevice(vid=0x28de, pid=0x11ff, path="/dev/input/event2",
                      name="Microsoft X-Box 360 pad 0")


def _wii_nav():
    return FakeDevice(vid=0x4d41, pid=0x0001, path="/dev/input/event9",
                      name="MAD Wii Nav", is_mad_virtual=True)


def _real_pad():
    return FakeDevice(vid=0x054c, pid=0x0ce6, path="/dev/input/event3", name="DualSense")


class OnlyXarcadePresent(unittest.TestCase):
    def test_xarcade_plus_wii_nav_still_only_xarcade(self):
        # The bug: the Wii Nav pad counted as a real alternative pad and defeated the all(),
        # suppressing the warning. It must now be ignored -> still "only X-Arcade".
        devs = [_xarcade(), _wii_nav()]
        self.assertTrue(routing.only_xarcade_present(devs, _XPORT))

    def test_xarcade_and_its_steam_shadow_and_wii_nav(self):
        devs = [_xarcade(), _steam_virtual(), _wii_nav()]
        self.assertTrue(routing.only_xarcade_present(devs, _XPORT))

    def test_a_real_pad_still_defeats_the_warning(self):
        # Guard against over-fixing: a genuine gamepad must still make it NOT only-X-Arcade.
        devs = [_xarcade(), _real_pad()]
        self.assertFalse(routing.only_xarcade_present(devs, _XPORT))

    def test_only_wii_nav_is_not_only_xarcade(self):
        # With just the nav pad (no X-Arcade), the filtered set is empty -> False (no warning).
        self.assertFalse(routing.only_xarcade_present([_wii_nav()], _XPORT))


if __name__ == "__main__":
    unittest.main()
