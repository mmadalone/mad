"""resolve_pins half-swap (item ⑥): a `port:` pin assigns the EXACT matching evdev
node, so either X-Arcade half — the two differ ONLY by the USB interface suffix in
the pin_id (:1.0 vs :1.1) — can be promoted to any player. This is the basis for
the OpenBOR P1/P2 split: `controller-router.py pin-node <system> <player>` resolves
the pinned half to its device node for SDL_JOYSTICK_DEVICE.

pin_id()/pin_kind() derive from sysfs, so we fake them off a per-device attribute
to exercise the pure resolve_pins logic with no hardware.

Run:  python3 -m unittest tests.test_xarcade_pins -v
"""
from __future__ import annotations

import unittest

from lib import routing
from tests._fakes import FakeDevice

P1_PIN = "port:045e:02a1:usb-xhci-hcd.2.auto-1.1/input0:1.0"   # iface 00 → P1 half
P2_PIN = "port:045e:02a1:usb-xhci-hcd.2.auto-1.1/input0:1.1"   # iface 01 → P2 half


def _half(path: str, pin: str) -> FakeDevice:
    d = FakeDevice(vid=0x045e, pid=0x02a1, path=path, name="Xbox 360 Wireless Receiver")
    d._pin = pin
    return d


class ResolvePinsHalfSwap(unittest.TestCase):
    def setUp(self):
        self._pin_id, self._pin_kind = routing.pin_id, routing.pin_kind
        routing.pin_id = lambda d: getattr(d, "_pin", "")
        routing.pin_kind = lambda key: "port"

    def tearDown(self):
        routing.pin_id, routing.pin_kind = self._pin_id, self._pin_kind

    def test_pin_p1_to_the_other_half(self):
        p1 = _half("/dev/input/event27", P1_PIN)
        p2 = _half("/dev/input/event29", P2_PIN)
        # Pin player 1 to the :1.1 (normally-P2) half — it must win player 1.
        pinned, claimed = routing.resolve_pins({"1": P2_PIN}, [p1, p2])
        self.assertIs(pinned.get(1), p2)
        self.assertEqual(pinned.get(1).path, "/dev/input/event29")
        self.assertIn("/dev/input/event29", claimed)

    def test_pin_both_halves_independently(self):
        p1 = _half("/dev/input/event27", P1_PIN)
        p2 = _half("/dev/input/event29", P2_PIN)
        pinned, _ = routing.resolve_pins({"1": P1_PIN, "2": P2_PIN}, [p1, p2])
        self.assertIs(pinned.get(1), p1)
        self.assertIs(pinned.get(2), p2)

    def test_only_pinned_player_resolves(self):
        p1 = _half("/dev/input/event27", P1_PIN)
        p2 = _half("/dev/input/event29", P2_PIN)
        pinned, _ = routing.resolve_pins({"1": P1_PIN}, [p1, p2])
        self.assertEqual(set(pinned), {1})


if __name__ == "__main__":
    unittest.main()
