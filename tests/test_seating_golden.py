"""DOCKED SEATING GOLDEN — routing.resolve_ports must not move.

Miquel, 2026-07-17: "in handheld mode only: yes, i need the deck orderable too, not in docked
mode, just to be clear about this cause we fought this yesterday for a few hours to get proper
player seating in docked mode."

Docked player seating is hard-won and settled. The RA-profiles work extends routing.family_of
(adding Steam Deck + Wii Remote Pro by vid:pid) so ONE canonical matcher can serve both `ports`
resolution and the profile map. family_of feeds resolve_ports's token matching, so that change can
silently re-seat pads. This file is the tripwire: it pins the resolution for a FIXED fleet across
every configured system. If it moves, STOP and re-read — do not update the expectation.

HERMETIC BY CONSTRUCTION, and deliberately so:
  * the fleet is a FIXTURE, not the live rig, so this reproduces off-Deck and in CI (a golden keyed
    on whatever is plugged in measures the developer's desk, see the ci-vs-deck-environment-gap
    memory);
  * the policy is a FIXTURE too, so a MAD UI edit to controller-policy.local.toml cannot turn this
    red for an unrelated reason;
  * FakeDevice DERIVES is_steam_virtual/is_sinden/is_mad_virtual from vid:pid (tests/_fakes.py).
    The first draft of this baseline used the old flat-False defaults and produced a 28de:11ff pad
    that resolve_ports seated on arcade P4 — a device the real router can never see, because it
    excludes those phantoms. The fixture was lying and the golden would have canonised it.

Run:  python3 -m unittest tests.test_seating_golden -v
"""
from __future__ import annotations

import unittest

from lib.routing import resolve_pins, resolve_ports, reserve_value
from tests._fakes import FakeDevice

XPORT = "1.1"                                     # [hardware].xarcade_port on this rig
XA_PHYS = "usb-0000:04:00.3-1.1/input0"           # both X-Arcade halves sit at that port


def _pad(vid, pid, path, name, phys=""):
    return FakeDevice(vid=vid, pid=pid, path=path, name=name, phys=phys)


# Miquel's real fleet as of 2026-07-17, pinned as a fixture.
FLEET = [
    _pad(0x045e, 0x02a1, "/dev/input/event22", "Xbox 360 Wireless Receiver", XA_PHYS),
    _pad(0x045e, 0x02a1, "/dev/input/event23", "Xbox 360 Wireless Receiver", XA_PHYS),
    _pad(0x054c, 0x09cc, "/dev/input/event27", "Wireless Controller", "usb-x-1/input0"),
    _pad(0x054c, 0x0ce6, "/dev/input/event264", "DualSense Wireless Controller", "usb-x-2/input0"),
    _pad(0x057e, 0x0330, "/dev/input/event259", "Nintendo Wii Remote Pro Controller", "usb-x-3/input0"),
    _pad(0x2dc8, 0x3820, "/dev/input/event272", "8Bitdo NES30 Pro", "usb-x-4/input0"),
    _pad(0x28de, 0x11ff, "/dev/input/event10", "Microsoft X-Box 360 pad 0"),      # Steam phantom
    _pad(0x28de, 0x1205, "/dev/input/event6", "Valve Software Steam Deck Controller", "usb-v/input0"),
]

ARCADE = [["X-Arcade", "8BitDo", "DualSense", "Xbox"],
          ["X-Arcade", "8BitDo", "DualSense", "Xbox"],
          ["DualSense", "8BitDo", "Xbox", "Steam Deck"],
          ["DualSense", "8BitDo", "Xbox", "Steam Deck"]]
CONSOLE = [["DualSense", "8BitDo", "Xbox", "X-Arcade"],
           ["DualSense", "8BitDo", "Xbox", "X-Arcade"]]
NES = [["8BitDo", "DualSense", "Xbox", "X-Arcade"],
       ["8BitDo", "DualSense", "Xbox", "X-Arcade"]]
DEFAULTS = [["DualSense", "DualShock 4", "Wii Remote Pro", "X-Arcade", "8BitDo", "Xbox", "Steam Deck"],
            ["DualSense", "DualShock 4", "Wii Remote Pro", "X-Arcade", "8BitDo", "Xbox", "Steam Deck"]]

XARC = "045e:02a1 Xbox 360 Wireless Receiver"
DS5 = "054c:0ce6 DualSense Wireless Controller"
DS4 = "054c:09cc Wireless Controller"
WIIU = "057e:0330 Nintendo Wii Remote Pro Controller"
BITDO = "2dc8:3820 8Bitdo NES30 Pro"

# MEASURED from the CURRENT code, against the exact port lists above, before family_of was touched.
# (The first draft of this dict was captured against the LIVE merged policy while the tests fed
# these hardcoded lists, so three expectations were values from a different input. Measure the
# thing you actually test.)
GOLDEN = {
    "arcade": {1: XARC, 2: XARC, 3: DS5, 4: BITDO},
    "console": {1: DS5, 2: BITDO},
    "nes": {1: BITDO, 2: DS5},
    "defaults": {1: DS5, 2: DS4},
}


class DockedSeating(unittest.TestCase):
    def _seat(self, ports):
        pinned, claimed = resolve_pins({}, FLEET)
        got = resolve_ports(ports, FLEET, preassigned=pinned, preclaimed=claimed, xport=XPORT)
        return {p: reserve_value(d) for p, d in got.items()}

    def test_arcade(self):
        self.assertEqual(self._seat(ARCADE), GOLDEN["arcade"])

    def test_console(self):
        self.assertEqual(self._seat(CONSOLE), GOLDEN["console"])

    def test_nes(self):
        self.assertEqual(self._seat(NES), GOLDEN["nes"])

    def test_defaults(self):
        self.assertEqual(self._seat(DEFAULTS), GOLDEN["defaults"])

    def test_the_steam_virtual_phantom_is_never_seated(self):
        # 28de:11ff is Steam's phantom pool, not a pad the user can route. resolve_ports excludes
        # it from BOTH token matching and the fallback rescue. Every port list above offers a
        # "Steam Deck" token, so a regression here would show up as the phantom taking a port.
        for ports in (ARCADE, CONSOLE, NES, DEFAULTS):
            for val in self._seat(ports).values():
                self.assertNotIn("28de:11ff", val)

    def _seat_strict(self, ports):
        """Token matching with the fallback rescue OFF -- what a token DOES match, not what the
        rescue hands it afterwards."""
        pinned, claimed = resolve_pins({}, FLEET)
        got = resolve_ports(ports, FLEET, preassigned=pinned, preclaimed=claimed,
                            xport=XPORT, with_fallback=False)
        return {p: reserve_value(d) for p, d in got.items()}

    def test_the_xarcade_owns_its_token_and_xbox_does_not_steal_it(self):
        # 045e:02a1 is byte-identical to a real Xbox 360 pad; only the USB port tells them apart.
        # The "X-Arcade" token takes both halves; an "Xbox" token must NOT MATCH the cab.
        self.assertEqual(self._seat([["X-Arcade"], ["X-Arcade"]]), {1: XARC, 2: XARC})
        self.assertEqual(self._seat_strict([["Xbox"]]), {})   # the only 045e pads ARE the X-Arcade
        # ...but with the rescue ON the cab IS still seated, because a port that matched no token
        # takes the next unclaimed real pad rather than landing on RetroArch's "N/A". That is
        # deliberate (routing.py's fallback comment), and asserting {} here without with_fallback=False
        # was my error, not the code's.
        self.assertEqual(self._seat([["Xbox"]]), {1: XARC})

    def test_wii_remote_pro_and_steam_deck_tokens_still_resolve(self):
        # These two resolve by NAME substring today (family_of returns None for them), which is
        # exactly what the P0a change alters. Pin the OUTCOME so the mechanism can change under it.
        self.assertEqual(self._seat([["Wii Remote Pro"]]), {1: WIIU})
        self.assertEqual(self._seat([["Steam Deck"]]),
                         {1: "28de:1205 Valve Software Steam Deck Controller"})


if __name__ == "__main__":
    unittest.main()
