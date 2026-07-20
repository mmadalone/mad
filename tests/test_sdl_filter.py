"""HIDE_DECK_PAD_WHEN_EXTERNAL toggle + the OpenBOR Deck-pad-leak fix in lib/sdl_filter.

The bug it guards: OpenBOR runs under Proton, and winebus EXEMPTS Steam's virtual Deck
pad (28de:11ff) from the _EXCEPT whitelist — the whitelist wins for ordinary pads, but
28de:11ff walks straight past it, so only the SDL *blocklist*
(SDL_GAMECONTROLLER_IGNORE_DEVICES = ignore_nonplayers) can hide it. joypads() DROPS
28de:11ff from enumeration (Device.is_steam_virtual), so it never reached the blocklist
and leaked into OpenBOR, shifting X-Arcade P1 -> P3. With the toggle ON (the default)
and an external player pad present, BOTH Deck classes are forced onto the blocklist
regardless of enumeration.

We patch _present_classes() to a fixed set (pure filter logic, no hardware) and point
install.conf at a temp file via $MAD_INSTALL_CONF.

Run:  python3 -m unittest tests.test_sdl_filter -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from lib import sdl_filter

OPENBOR_PADS = ["045e:02a1", "054c:0ce6", "054c:09cc", "2dc8:2810"]
XARCADE = "045e:02a1"
DS4 = "054c:09cc"
DECK = "28de:1205"        # real Steam Deck controller (raw evdev)
DECK_VIRT = "28de:11ff"   # Steam's virtual gamepad (dropped by joypads())


class _Base(unittest.TestCase):
    def setUp(self):
        self._present = sdl_filter._present_classes
        self._saved_conf = os.environ.get("MAD_INSTALL_CONF")
        fd, self._conf_path = tempfile.mkstemp(suffix=".conf")
        os.close(fd)
        os.environ["MAD_INSTALL_CONF"] = self._conf_path
        # These cases exercise the DOCKED toggle (HIDE_DECK_PAD_WHEN_EXTERNAL). Pin the context so
        # the result never depends on the test host's display state (the handheld axis -- a separate
        # key -- is covered by tests/test_sdl_filter_context.py).
        self._saved_ctx = os.environ.get("MAD_FORCE_CONTEXT")
        os.environ["MAD_FORCE_CONTEXT"] = "docked"

    def tearDown(self):
        sdl_filter._present_classes = self._present
        if self._saved_ctx is None:
            os.environ.pop("MAD_FORCE_CONTEXT", None)
        else:
            os.environ["MAD_FORCE_CONTEXT"] = self._saved_ctx
        if self._saved_conf is None:
            os.environ.pop("MAD_INSTALL_CONF", None)
        else:
            os.environ["MAD_INSTALL_CONF"] = self._saved_conf
        try:
            os.unlink(self._conf_path)
        except OSError:
            pass

    def _present_set(self, classes):
        s = set(classes)
        sdl_filter._present_classes = lambda: s

    def _flag(self, value):
        with open(self._conf_path, "w") as f:
            f.write(f"HIDE_DECK_PAD_WHEN_EXTERNAL={value}\n")

    def _flag_absent_file(self):
        os.environ["MAD_INSTALL_CONF"] = self._conf_path + ".does-not-exist"


class IgnoreNonplayersBlocklist(_Base):
    """The OpenBOR/winebus blocklist — the actual fix path."""

    def test_flag_on_external_present_hides_both_deck_classes(self):
        self._present_set([XARCADE, DS4, DECK])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertIn("0x28de/0x11ff", bl)
        self.assertIn("0x28de/0x1205", bl)
        self.assertNotIn("0x045e/0x02a1", bl)   # X-Arcade is a player -> never blocked
        self.assertNotIn("0x054c/0x09cc", bl)   # DS4 is a player -> never blocked

    def test_virtual_pad_blocked_even_when_not_enumerated(self):
        # The real-world case: joypads() drops 28de:11ff so `present` has only the
        # X-Arcade, yet the fix must STILL add 11ff (and 1205) to the blocklist.
        self._present_set([XARCADE])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertIn("0x28de/0x11ff", bl)
        self.assertIn("0x28de/0x1205", bl)

    def test_flag_off_no_deck_classes_added(self):
        # Real-world enumeration: joypads() drops the virtual pad and the real Deck pad
        # exposes no joystick under Steam-Input-OFF, so `present` is just the X-Arcade.
        # With the flag OFF the blocklist must stay empty (today's leaky behavior) — it is
        # the TOGGLE, not the baseline, that injects the Deck classes.
        self._present_set([XARCADE])
        self._flag("0")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertNotIn("0x28de/0x1205", bl)

    def test_flag_off_still_blocks_enumerated_nonplayer_deck(self):
        # If the real Deck pad IS enumerated as a present non-player joypad, the BASELINE
        # blocklist hides it (flag-independent) — that is correct and unchanged.
        self._present_set([XARCADE, DECK])
        self._flag("0")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertIn("0x28de/0x1205", bl)      # baseline non-player hide
        self.assertNotIn("0x28de/0x11ff", bl)   # virtual NOT enumerated -> only the toggle adds it

    def test_absent_key_defaults_on(self):
        self._present_set([XARCADE])
        self._flag_absent_file()
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertIn("0x28de/0x11ff", bl)
        self.assertIn("0x28de/0x1205", bl)

    def test_solo_handheld_keeps_deck(self):
        # No external player pad present -> the Deck/virtual pad is the only controller.
        # It must NOT be blocked even with the flag ON, or handheld play is dead.
        self._present_set([DECK_VIRT])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertNotIn("0x28de/0x1205", bl)


class KeepExceptWhitelist(_Base):
    """Native-SDL whitelist (Supermodel/Hypseus)."""

    def test_strips_deck_from_keep_extra_when_external(self):
        self._present_set(["054c:0ce6", DECK])
        self._flag("1")
        wl = sdl_filter.keep_except_list(["054c:0ce6"], DECK, keep_extra=[DECK])
        self.assertIn("0x054c/0x0ce6", wl)
        self.assertNotIn("0x28de/0x1205", wl)   # Deck dropped even though in keep_extra

    def test_keep_extra_deck_survives_when_flag_off(self):
        self._present_set(["054c:0ce6", DECK])
        self._flag("0")
        wl = sdl_filter.keep_except_list(["054c:0ce6"], DECK, keep_extra=[DECK])
        self.assertIn("0x28de/0x1205", wl)

    def test_handheld_kept_when_solo(self):
        self._present_set([DECK])
        self._flag("1")
        wl = sdl_filter.keep_except_list(["054c:0ce6"], DECK)
        self.assertEqual(wl, "0x28de/0x1205")   # solo -> handheld fallback kept


class KeepFirstPresentWhitelist(_Base):
    """Strict priority whitelist (hypseus/daphne's _EXCEPT path) — must be UNAFFECTED."""

    def test_player_present_wins_not_deck(self):
        self._present_set([XARCADE, DECK])
        self._flag("1")
        wl = sdl_filter.keep_first_present(OPENBOR_PADS, DECK_VIRT)
        self.assertEqual(wl, "0x045e/0x02a1")

    def test_solo_returns_handheld(self):
        # The toggle deliberately does NOT touch this path: solo handheld must keep its pad.
        self._present_set([DECK_VIRT])
        self._flag("1")
        wl = sdl_filter.keep_first_present(OPENBOR_PADS, DECK_VIRT)
        self.assertEqual(wl, "0x28de/0x11ff")


if __name__ == "__main__":
    unittest.main()
