"""HIDE_DECK_PAD_WHEN_EXTERNAL toggle + the OpenBOR Deck-pad-leak fix in lib/sdl_filter.

The bug it guards: OpenBOR runs under Proton, and winebus EXEMPTS Steam's virtual Deck
pad (28de:11ff) from the _EXCEPT whitelist — the whitelist wins for ordinary pads, but
28de:11ff walks straight past it, so only the SDL *blocklist*
(SDL_GAMECONTROLLER_IGNORE_DEVICES = ignore_nonplayers) can hide it. joypads() DROPS
28de:11ff from enumeration (Device.is_steam_virtual), so it never reached the blocklist
and leaked into OpenBOR, shifting X-Arcade P1 -> P3. With the toggle ON (the default)
and an external player pad present, BOTH Deck classes are forced onto the blocklist
regardless of enumeration.

We patch the device scan to a fixed set (pure filter logic, no hardware) and point
install.conf at a temp file via $MAD_INSTALL_CONF. See _Base._present_set: BOTH seams get
patched, because the two X-Arcade-aware functions read _scan and everything else reads
_present_classes.

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
        self._scan = sdl_filter._scan
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
        sdl_filter._scan = self._scan
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

    def _present_set(self, classes, xa_ruled_out=False):
        """Pin the device scan. BOTH seams, deliberately: the blocklist and the strict
        whitelist read _scan (classes + the X-Arcade evidence), everything else still reads
        _present_classes, and patching only one leaves the other running against the live
        machine while the test looks like it controls the input.

        xa_ruled_out=True means every connected 045e:02a1 positively named itself something
        other than an X-Arcade (a real Microsoft receiver). False means no evidence, which
        must never be read as "the cabinet is away"."""
        s = set(classes)
        sdl_filter._present_classes = lambda: s
        sdl_filter._scan = lambda: (s, xa_ruled_out)

    def _flag(self, value):
        with open(self._conf_path, "w") as f:
            f.write(f"HIDE_DECK_PAD_WHEN_EXTERNAL={value}\n")

    def _flag_absent_file(self):
        os.environ["MAD_INSTALL_CONF"] = self._conf_path + ".does-not-exist"

    def _ctx(self, value):
        """docked | handheld. deck_state reads MAD_FORCE_CONTEXT first, so this pins the
        PHYSICAL state _undocked() resolves as well as the toggle's context."""
        os.environ["MAD_FORCE_CONTEXT"] = value


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
        # UNDOCKED with no player pad present -> the Deck/virtual pad is the only controller.
        # It must NOT be blocked even with the flag ON, or handheld play is dead.
        self._ctx("handheld")
        self._present_set([DECK_VIRT])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(OPENBOR_PADS, DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertNotIn("0x28de/0x1205", bl)


class NothingListedIsConnected(_Base):
    """The X-Arcade identity split, and what a DOCKED launch does with no listed pad.

    Reported live 2026-08-13: an Xbox 360 Wireless Receiver took Player 1 for Daphne with
    only "X-Arcade" ticked, because the token was expanded to a bare 045e:02a1 for the
    "these are the players" set while _present_classes resolved it port-aware. The DualSense
    was blocked and the pad nobody listed inherited the game.
    """

    DAPHNE = ["x-arcade"]          # [backends.hypseus] as shipped: the cabinet only

    def test_a_non_cabinet_xbox_pad_is_blocked_like_any_other_unlisted_pad(self):
        self._ctx("docked")
        # The receiver NAMES ITSELF ("Xbox 360 Wireless Receiver for Windows"), which is the
        # only thing that separates it from the cabinet -- see sdl_filter._scan.
        self._present_set([XARCADE, "054c:0ce6"], xa_ruled_out=True)
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        self.assertIn("0x045e/0x02a1", bl, "the Xbox pad must not inherit the cabinet's slot")
        self.assertIn("0x054c/0x0ce6", bl)

    def test_the_real_cabinet_is_still_never_blocked(self):
        self._ctx("docked")
        self._present_set(["x-arcade", XARCADE, "054c:0ce6"])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        self.assertNotIn("0x045e/0x02a1", bl)
        self.assertIn("0x054c/0x0ce6", bl)             # unlisted pads still hidden

    def test_docked_with_nothing_listed_connected_hides_the_deck_too(self):
        # The user's decision: docked, no X-Arcade -> nothing plays at all.
        self._ctx("docked")
        self._present_set([XARCADE, "054c:0ce6"], xa_ruled_out=True)
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        for vp in ("0x045e/0x02a1", "0x054c/0x0ce6", "0x28de/0x11ff", "0x28de/0x1205"):
            self.assertIn(vp, bl)

    def test_undocked_with_nothing_listed_connected_keeps_the_deck(self):
        # The half that must NOT change: in your hands, the Deck's own pad is the controller.
        self._ctx("handheld")
        self._present_set([XARCADE, "054c:0ce6"], xa_ruled_out=True)
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertIn("0x045e/0x02a1", bl)             # still not a player
        self.assertIn("0x054c/0x0ce6", bl)

    def test_a_backend_listing_no_player_families_keeps_its_own_pad(self):
        # NOT "left alone": with an empty pad_classes every present pad is still blocked, as
        # it always was. What the `pad_classes and` guard preserves is narrower -- the Deck's
        # own never-enumerated pad, which the docked rule would otherwise hide as well.
        self._ctx("docked")
        self._present_set(["054c:0ce6"])
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers([], DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertNotIn("0x28de/0x1205", bl)

    def test_a_deck_class_listed_as_a_player_is_never_hidden_by_this_rule(self):
        # [backends.openbor] lists 28de:11ff as a player family on purpose.
        self._ctx("docked")
        self._present_set(["054c:09cc"])               # a DS4, not listed below
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(["x-arcade", DECK_VIRT], DECK_VIRT)
        self.assertNotIn("0x28de/0x11ff", bl)
        self.assertIn("0x054c/0x09cc", bl)

    def test_an_unidentified_cabinet_is_never_blocked_by_ITSELF(self):
        """THE REGRESSION THIS REPLACED (caught in review, never shipped).

        There is no [hardware] section in the shipped policy, so xarcade_port is unset until
        you press "Identify X-Arcade" -- and Preview's CLEAR button removes it again. With
        the token refused whenever it did not RESOLVE, a cabinet that was plugged in, ticked,
        and working blocked itself, the docked rule then hid the Deck classes too, and Daphne
        launched with zero controllers. Measured: blocklist went from '' to
        '0x045e/0x02a1,0x28de/0x11ff,0x28de/0x1205'.

        No evidence is not evidence of absence. Only a positive "I am a Microsoft receiver"
        rules the cabinet out.
        """
        self._ctx("docked")
        self._present_set([XARCADE], xa_ruled_out=False)     # cabinet present, never identified
        self._flag("0")                     # isolate from the hide-the-Deck TOGGLE
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        self.assertEqual(bl, "", "a listed, connected cabinet blocked itself, then everything else")
        self.assertEqual(sdl_filter.keep_first_present(self.DAPHNE, DECK_VIRT),
                         "0x045e/0x02a1", "and the whitelist must expose it")
        # With the toggle ON the Deck classes are hidden as they always were -- that is the
        # pre-existing "an external player pad is present" rule, not the new docked one.
        self._flag("1")
        bl = sdl_filter.ignore_nonplayers(self.DAPHNE, DECK_VIRT)
        self.assertNotIn("0x045e/0x02a1", bl)
        self.assertIn("0x28de/0x11ff", bl)

    def test_docked_nothing_listed_hides_pads_DETERMINISTICALLY(self):
        """The whitelist has to say "allow nothing" out loud.

        hypseus-pin.sh only exports _EXCEPT when the string is non-empty, so returning ""
        leaves SDL unfiltered and the blocklist -- a snapshot taken ~1.2 s before the game
        starts -- is the only guard. A Bluetooth pad finishing its connect inside that window
        played anyway, so "nothing plays" depended on when your DualSense happened to wake.
        """
        self._ctx("docked")
        self._present_set([XARCADE, "054c:0ce6"], xa_ruled_out=True)
        self._flag("1")
        self.assertEqual(sdl_filter.keep_first_present(self.DAPHNE, DECK_VIRT),
                         sdl_filter.MATCH_NOTHING)
        self.assertNotEqual(sdl_filter.MATCH_NOTHING, "", "must not read as 'no opinion'")

    def test_a_backend_with_no_pad_list_never_gets_the_match_nothing_whitelist(self):
        self._ctx("docked")
        self._present_set(["054c:0ce6"])
        self._flag("1")
        self.assertEqual(sdl_filter.keep_first_present([], DECK_VIRT), "")

    def test_the_whitelist_agrees_with_the_blocklist_in_both_contexts(self):
        # keep_first_present is daphne's _EXCEPT list. It must not offer a fallback the
        # blocklist has just hidden, or the two disagree on the same launch.
        self._present_set([DECK_VIRT])
        self._flag("1")
        self._ctx("docked")
        self.assertEqual(sdl_filter.keep_first_present(self.DAPHNE, DECK_VIRT),
                         sdl_filter.MATCH_NOTHING)
        self._ctx("handheld")
        self.assertEqual(sdl_filter.keep_first_present(self.DAPHNE, DECK_VIRT),
                         "0x28de/0x11ff")


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
        # (The separate PHYSICAL dock gate does, hence the explicit undocked context.)
        self._ctx("handheld")
        self._present_set([DECK_VIRT])
        self._flag("1")
        wl = sdl_filter.keep_first_present(OPENBOR_PADS, DECK_VIRT)
        self.assertEqual(wl, "0x28de/0x11ff")


if __name__ == "__main__":
    unittest.main()
