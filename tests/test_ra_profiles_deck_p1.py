"""Handheld: the Deck's own pad drives P1 through the RA PROFILE rail (controller-router._setup).

Handheld with no external pad, RetroArch seats the Deck on P1 by its own sdl2 enumeration -- it is
the only pad there. The router cannot RESERVE it (routing.resolve_ports excludes the Steam virtual
pad), so _setup resolves the Deck's family profile into `extra` and mints NO reservation. Two
things had to change for that to work at all, and both are pinned here:

  * the guard before write_override was `if not port_names and not mouse_indices` -- with no
    reservation it returned BEFORE the writer, so the profile was silently dropped on exactly the
    launch it exists for;
  * the Deck branch itself, which must fire ONLY handheld and ONLY when nothing holds P1.

WHY THIS FILE EXISTS AT ALL. Before it, NO test called _setup. Deleting the guard outright left all
2414 tests green, and tests/test_seating_golden.py -- the docked-seating guard -- imports only
resolve_pins/resolve_ports/reserve_value, so it is structurally blind to every line this change
touches. The golden staying green is necessary and NOT sufficient; DockedNegative below is the
sufficiency, and it is the test that fails if the gate is ever loosened to `if ra_driver:`
(planned_joypad_driver returns "udev" DOCKED, which is truthy).

Run: python3 -m unittest tests.test_ra_profiles_deck_p1 -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._fakes import dev

ROOT = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cr = _load("controller_router_deckp1", "controller-router.py")

_CTX = SimpleNamespace(system="snes", rom_basename="Game", collection=None,
                       policy_key="snes")

# The live Deck pad, as enumerate_devices() reports it on this rig (28de:11ff, event10). The NAME
# is Steam's, not Valve's: routing.family_of checks 28de BEFORE its "x-box" catch-all precisely
# because of this string.
DECK = dev("28de:11ff", "/dev/input/event10", "Microsoft X-Box 360 pad 0")
# The lizard-mode keyboard/mouse nodes. Same family by vid, NOT joypads, and they enumerate FIRST
# -- which is why the Deck predicate is is_steam_virtual and not family_of(d) == "Steam Deck".
LIZARD = dev("28de:1205", "/dev/input/event6", "Valve Software Steam Deck Controller")
LIZARD.is_joypad = False
XARCADE = dev("045e:02a1", "/dev/input/event22", "Xbox 360 Wireless Receiver")
DS5 = dev("054c:0ce6", "/dev/input/event27", "DualSense Wireless Controller")

# The Deck seed row: Miquel's DEPLOYED handheld scheme, re-expressed semantically. slowmotion is
# "l2" and NOT "r2" -- see SeedRowPreservesDeployment below.
DECK_HOTKEYS = {"modifier": "l3", "rewind": "l", "fast_forward": "r",
                "slowmotion": "l2", "menu": "select", "quit": "start"}
SEEDED = {"ra_profiles": {"Deck": {"hotkeys": dict(DECK_HOTKEYS)}},
          "ra_profile_map": {"Steam Deck": "Deck"}}


class _SetupHarness(unittest.TestCase):
    """Drives the REAL _setup with the real routing/resolver, faking only the edges: the device
    enumeration, the dock state, the driver decision, and the writer (captured, never written)."""

    def setUp(self):
        self.written = mock.MagicMock(return_value=[Path("/tmp/fake/Game.cfg")])
        self._patches = [
            mock.patch.object(cr, "write_override", self.written),
            mock.patch.object(cr, "core_dirs_for_system",
                              mock.MagicMock(return_value=[Path("/tmp/fake")])),
            mock.patch.object(cr, "ra_mouse_hotkey_bound", mock.MagicMock(return_value=False)),
            mock.patch.object(cr, "_xarcade_warn", mock.MagicMock(return_value=0)),
            mock.patch.object(cr, "_ra_on_the_go", mock.MagicMock(return_value="sdl2")),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _run(self, devs, policy, *, handheld=True, ports=None):
        """Run _setup and return (exit_code, write_override kwargs-or-None)."""
        sys_entry = {"category": "console",
                     "ports": ports if ports is not None else [["DualSense", "X-Arcade"],
                                                               ["DualSense", "X-Arcade"]]}
        with mock.patch.object(cr, "load_policy", mock.MagicMock(return_value=policy)), \
             mock.patch.object(cr, "xarcade_port", mock.MagicMock(return_value="")), \
             mock.patch.object(cr, "resolve_policy", mock.MagicMock(return_value=sys_entry)), \
             mock.patch.object(cr, "enumerate_devices", mock.MagicMock(return_value=devs)), \
             mock.patch.object(cr, "_handheld_active", mock.MagicMock(return_value=handheld)):
            rc = cr._setup(_CTX, mock.MagicMock())
        if not self.written.called:
            return rc, None
        args = self.written.call_args.args
        # write_override(system, rom, port_names, mouse_indices or None, port_binds or None,
        #                extra or None) -- the `or None` means an EMPTY dict arrives as None, so
        # normalise back to {} here and let each test say what it means.
        return rc, {"port_names": args[2], "mouse_indices": args[3] or {},
                    "port_binds": args[4] or {}, "extra": args[5] or {}}


class DeckAsP1(_SetupHarness):
    def test_handheld_no_external_pad_writes_the_profile(self):
        # T1. THE test the suite lacked: with no reservation, the old guard returned before
        # write_override. Revert :491 to the two-term form and this goes red.
        rc, call = self._run([LIZARD, DECK], SEEDED)
        self.assertEqual(rc, 0)
        self.assertIsNotNone(call, "write_override was never called: the guard swallowed the "
                                   "profile on the exact launch it exists for")
        self.assertFalse(call["port_names"], "the Deck must never be RESERVED")
        extra = call["extra"]
        self.assertEqual(extra["input_player1_a_btn"], "0")       # sdl2 base map reached P1
        self.assertEqual(extra["input_enable_hotkey_btn"], "7")   # l3, the modifier

    def test_no_reservation_is_minted(self):
        # T4. The Deck rides `extra` only. Seat it in port_devs instead and port_names gains
        # {1: "28de:11ff ..."} -> red.
        _rc, call = self._run([LIZARD, DECK], SEEDED)
        self.assertEqual(call["port_names"], {})
        self.assertNotIn(1, call["port_names"])

    def test_deck_branch_reports_the_pad_it_actually_used(self):
        # The predicate is is_steam_virtual, NOT family_token_of(d) == "Steam Deck": 28de:1205
        # answers to that family too and enumerates FIRST, but its nodes are the lizard-mode
        # keyboard/mouse (is_joypad False), not the pad Steam feeds to games.
        #
        # BE HONEST ABOUT WHAT THIS CAN PROVE. Under sdl2 the two choices write IDENTICAL bytes,
        # because BASE_MAPS["sdl2"] is `lambda d: dict(SDL_SEMANTIC_TABLE)` and ignores the device
        # entirely (ra_profiles.py:85) -- the same property that makes Steam's phantom-pad pool
        # harmless here. So no assertion on `extra` can distinguish them, and one that claimed to
        # would be measuring nothing. What DOES differ is the router.log line, and that is not
        # cosmetic: with no display, router.log is the only channel that says which pad was bound,
        # and a line naming a keyboard node as the gamepad is a debugging dead end.
        log = mock.MagicMock()
        with mock.patch.object(cr, "load_policy", mock.MagicMock(return_value=SEEDED)), \
             mock.patch.object(cr, "xarcade_port", mock.MagicMock(return_value="")), \
             mock.patch.object(cr, "resolve_policy", mock.MagicMock(
                 return_value={"category": "console", "ports": [["DualSense"], ["DualSense"]]})), \
             mock.patch.object(cr, "enumerate_devices",
                               mock.MagicMock(return_value=[LIZARD, DECK])), \
             mock.patch.object(cr, "_handheld_active", mock.MagicMock(return_value=True)):
            cr._setup(_CTX, log)
        said = " ".join(str(c.args[0]) for c in log.info.call_args_list)
        self.assertIn(DECK.name, said, "router.log does not name the pad the Deck branch used")
        self.assertNotIn(LIZARD.name, said,
                         "the Deck branch picked the lizard-mode keyboard node, not the pad")

    def test_p1_occupied_by_an_external_pad_leaves_the_deck_alone(self):
        # T5. Drop the `1 not in port_devs` condition and the Deck clobbers the seated pad's P1
        # keys via extra.update -> red.
        #
        # The two families MUST map to DIFFERENT profiles for this to measure anything: under
        # sdl2 every pad resolves through the same table, so if both pointed at "Deck" the clobber
        # would be byte-identical to the correct answer and the test would pass with the guard
        # deleted. Ask the DualSense for a modifier the Deck's profile does not use.
        pol = {"ra_profiles": {"Deck": {"hotkeys": dict(DECK_HOTKEYS)},
                               "Pad": {"hotkeys": {**DECK_HOTKEYS, "modifier": "start"}}},
               "ra_profile_map": {"Steam Deck": "Deck", "DualSense": "Pad"}}
        _rc, call = self._run([DS5, DECK], pol)
        self.assertEqual(call["port_names"], {1: cr.reserve_value(DS5)})
        self.assertEqual(call["extra"]["input_enable_hotkey_btn"], "6",
                         "P1's hotkeys came from the Deck (l3 -> 7) instead of the seated "
                         "DualSense (start -> 6): the Deck branch fired on an occupied port")

    def test_no_deck_present_is_a_clean_skip(self):
        rc, call = self._run([], SEEDED)
        self.assertEqual(rc, 0)
        self.assertIsNone(call, "nothing to write and nothing was written")


class NoOpUntilSeeded(_SetupHarness):
    """The invariant that makes the P2 commit shippable without an on-screen test: with no
    [ra_profiles] / [ra_profile_map] in policy -- i.e. this rig today -- the rail is INERT and the
    relaxed guard fires on exactly the same inputs as the old one."""

    def test_unseeded_handheld_writes_nothing(self):
        # T2. Let the branch resolve against an absent profile (e.g. drop the `dprof is None`
        # check and pass {}) and resolve_for returns the 24 base keys + 18 hotkey nuls -> extra
        # non-empty -> write_override fires -> red.
        rc, call = self._run([LIZARD, DECK], {})
        self.assertEqual(rc, 0)
        self.assertIsNone(call, "nothing is seeded, so this launch must be byte-identical to "
                                "the pre-P2 behaviour")

    def test_family_mapped_to_a_missing_profile_writes_nothing(self):
        rc, call = self._run([LIZARD, DECK], {"ra_profile_map": {"Steam Deck": "Nope"}})
        self.assertEqual(rc, 0)
        self.assertIsNone(call)


class DockedNegative(_SetupHarness):
    """THE landmine. Docked seating is hard-won and the golden cannot see this code at all."""

    def test_docked_never_lets_the_deck_touch_p1(self):
        # T3. Change the gate from _handheld_active(policy) to `if ra_driver:` and this goes RED
        # while tests/test_seating_golden.py stays GREEN -- docked ra_driver is "udev", truthy,
        # so the Deck's binds would land on top of the X-Arcade's P1. That asymmetry is the whole
        # point of this test.
        # The cabinet is fed as family "Xbox", not "X-Arcade": xport is "" here, so is_xarcade()
        # short-circuits and family_of(045e:02a1) answers "Xbox" -- the same shape
        # tests/test_sony_split.py uses, and it keeps the fixture free of sysfs `phys`.
        pol = {**SEEDED, "ra_profile_map": {"Steam Deck": "Deck"}}
        rc, call = self._run([LIZARD, DECK, XARCADE], pol, handheld=False,
                             ports=[["Xbox"], ["Xbox"]])
        self.assertEqual(rc, 0)
        self.assertIsNotNone(call, "the docked X-Arcade launch stopped writing its reservation")
        self.assertEqual(call["port_names"], {1: cr.reserve_value(XARCADE)},
                         "docked seating moved")
        self.assertNotIn("input_enable_hotkey_btn", call["extra"],
                         "the Deck's hotkeys reached a DOCKED launch")
        self.assertNotIn("input_player1_l3_btn", call["extra"],
                         "the Deck's sdl2 binds reached a DOCKED launch")

    def test_docked_with_no_pads_at_all_writes_nothing(self):
        rc, call = self._run([LIZARD, DECK], SEEDED, handheld=False)
        self.assertEqual(rc, 0)
        self.assertIsNone(call)


class DriverKeying(_SetupHarness):
    def test_udev_handheld_fails_inert_never_wrong(self):
        # T7. The Deck's virtual pad has no udev autoconfig, so binds_for -> None -> resolve_for
        # returns {} and we write NOTHING rather than a guessed number space. Give BASE_MAPS
        # ["udev"] a fallback to SDL_SEMANTIC_TABLE and sdl2 numbers get written under the udev
        # driver -> red.
        with mock.patch.object(cr, "_ra_on_the_go", mock.MagicMock(return_value="udev")):
            rc, call = self._run([LIZARD, DECK], SEEDED)
        self.assertEqual(rc, 0)
        self.assertIsNone(call)

    def test_not_an_ra_launch_writes_no_profile(self):
        # _ra_on_the_go returns None for a standalone (launched_core() is None).
        with mock.patch.object(cr, "_ra_on_the_go", mock.MagicMock(return_value=None)):
            rc, call = self._run([LIZARD, DECK], SEEDED)
        self.assertEqual(rc, 0)
        self.assertIsNone(call)


class SeedRowPreservesDeployment(unittest.TestCase):
    """The Deck seed row must reproduce, key for key, what the OLD global rail
    (lib/ra_handheld_input) writes handheld from the LIVE merged policy. The approved plan's seed
    table said slowmotion = "r2"; the deployed value is "+4" = L2, set through MAD's own GUI in
    controller-policy.local.toml over the repo's "+5" default. Seeding r2 would silently move
    Miquel's slow-motion from the left trigger to the right one."""

    def test_deck_row_resolves_to_the_deployed_numbers(self):
        # T6. Flip slowmotion to "r2" -> "+5" -> red. This is the test that would have caught the
        # plan's error before it shipped.
        from lib import ra_profiles
        out = ra_profiles.resolve_for(DECK, "sdl2", {"hotkeys": dict(DECK_HOTKEYS)}, port=1)
        self.assertEqual(out["input_enable_hotkey_btn"], "7")           # L3
        self.assertEqual(out["input_rewind_btn"], "9")                  # L1
        self.assertEqual(out["input_hold_fast_forward_btn"], "10")      # R1
        self.assertEqual(out["input_menu_toggle_btn"], "4")             # Select
        self.assertEqual(out["input_exit_emulator_btn"], "6")           # Start
        self.assertEqual(out["input_toggle_slowmotion_axis"], "+4")     # L2, NOT R2 ("+5")

    def test_row_matches_what_the_old_global_rail_would_write(self):
        """Byte-equality against rail A, from the live merged policy -- so the migration is
        provably faithful rather than asserted to be. Guards the drift in BOTH directions: if
        someone edits [handheld.retroarch] and not the seed row (or the reverse), this fires."""
        from lib import policy, ra_handheld_input as rhi, ra_profiles
        old = rhi._handheld_values(rhi._ra_cfg())
        new = ra_profiles.resolve_for(DECK, "sdl2", {"hotkeys": dict(DECK_HOTKEYS)}, port=1)
        merged = policy.load_merged()
        if not (merged.get("handheld", {}).get("retroarch")):
            self.skipTest("no [handheld.retroarch] in the merged policy on this box")
        for _field, key, _dflt in rhi._SCHEME:
            self.assertEqual(new[key], str(old[key]),
                             f"{key}: the Deck profile and the old rail disagree -- the seed row "
                             "no longer reproduces the deployed scheme")


if __name__ == "__main__":
    unittest.main()
