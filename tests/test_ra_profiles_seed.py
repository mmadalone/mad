"""The SHIPPED [ra_profiles] / [ra_profile_map] seed in controller-policy.toml.

This is the commit that turns the profile rail ON, so these tests guard the two ways a seed can be
wrong in a way nothing else would notice:

  * a family pointed at a profile its pads CANNOT express. RetroArch only gates hotkeys when the
    enable-hotkey bind is SET, so an unresolvable modifier means every other hotkey fires UNGATED
    -- Start would open the menu mid-game. ra_profiles voids the whole set rather than ship that,
    which is safe but USELESS: the pad silently gets no hotkeys at all. Nothing fails, nothing
    logs at the user, the buttons just do nothing. That is what VoidedSets below is for, and it is
    the mistake the plan actually made once (8BitDo -> Gamepad: the FC30 has no sticks and no
    triggers, so l3/l2/r2 do not resolve).
  * the global [ra_profile_map] being INERT. resolve_policy does NOT merge top-level tables, and
    this exact bug already shipped once -- the global X-Arcade warn toggles did nothing at launch
    because the reader only looked at sys_entry.

NOTHING HERE READS THE RIG. Two separate leaks had to be closed, and each one shipped red to CI
once (dc7f421, then fa67819):

  * POLICY. _seed() parses the tracked controller-policy.toml straight off disk, never
    policy.load_merged(), which deep-merges the gitignored controller-policy.local.toml.
  * THE UDEV BASE MAP. device_binds._AUTOCONF_DIR points at the RetroArch flatpak's autoconfig
    directory, which exists only on the rig. On CI binds_for() returns None, resolve_for()
    correctly writes nothing, and every udev assertion below collapsed -- the tests were really
    asserting that RetroArch was installed. So they now run against real copies of those files
    in tests/fixtures/ra-autoconfig/ (see its README for why copies and not stubs).

The general lesson, which cost two red pushes: `git archive HEAD` into a temp tree catches the
first leak and NOT the second, because it still runs against the real $HOME. See
ci-vs-deck-environment-gap.

Run: python3 -m unittest tests.test_ra_profiles_seed -v
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path
from unittest import mock

from lib import device_binds, policy, ra_profiles
from tests._fakes import dev

# Real autoconfig files, copied off this rig. Pointing device_binds here makes the udev base map
# the same on CI as on the Deck, which is the only way these assertions mean anything in both.
FIXTURE_AUTOCONF = Path(__file__).resolve().parent / "fixtures" / "ra-autoconfig" / "udev"


def setUpModule():
    global _patch
    assert FIXTURE_AUTOCONF.is_dir(), f"missing autoconfig fixtures at {FIXTURE_AUTOCONF}"
    _patch = mock.patch.object(device_binds, "_AUTOCONF_DIR", FIXTURE_AUTOCONF)
    _patch.start()


def tearDownModule():
    _patch.stop()

# One representative pad per family, with the name each really enumerates under (the name matters:
# binds_for() finds a pad's autoconfig by it). The X-Arcade needs a `phys` on the configured USB
# port or is_xarcade() short-circuits and it reads as a plain Xbox pad -- that is not a detail, it
# is the difference between the Arcade profile and a VOIDED Gamepad one (proven in XArcadeIdentity).
XPORT = "1.1"
PADS = {
    "X-Arcade":       dev("045e:02a1", "/dev/input/event22", "Xbox 360 Wireless Receiver"),
    "DualSense":      dev("054c:0ce6", "/dev/input/event27", "DualSense Wireless Controller"),
    "DualShock 4":    dev("054c:09cc", "/dev/input/event30", "Wireless Controller"),
    "8BitDo":         dev("2dc8:2810", "/dev/input/event31", "8Bitdo FC30 II"),
    "8BitDo Pro":     dev("2dc8:3820", "/dev/input/event32", "8Bitdo NES30 Pro"),
    "Wii Remote Pro": dev("057e:0330", "/dev/input/event33", "Nintendo Wii Remote Pro Controller"),
    "Steam Deck":     dev("28de:11ff", "/dev/input/event10", "Microsoft X-Box 360 pad 0"),
}
PADS["X-Arcade"].phys = "usb-0000:04:00.3-1.1/input0"
# The Deck's pad only ever exists on the handheld rail, which is sdl2. Under udev it has no
# autoconfig, so it resolves to nothing at all (by design: fail inert, never guess).
DRIVER = {"Steam Deck": "sdl2"}


def _seed():
    """The SHIPPED policy: controller-policy.toml alone, parsed straight off disk.

    NOT policy.load_merged(), which deep-merges the gitignored controller-policy.local.toml on
    top. That file exists only on Miquel's rig, so a test reading merged policy asserts which
    machine it runs on -- how dc7f421 passed here and failed CI."""
    with policy.POLICY.open("rb") as f:
        p = tomllib.load(f)
    return p, (p.get("ra_profiles") or {}), (p.get("ra_profile_map") or {})


class SeedShape(unittest.TestCase):
    def test_the_seed_is_present_in_the_shipped_policy(self):
        _p, profs, pmap = _seed()
        self.assertTrue(profs, "[ra_profiles] is missing from controller-policy.toml")
        self.assertTrue(pmap, "[ra_profile_map] is missing from controller-policy.toml")

    def test_every_mapped_profile_exists(self):
        _p, profs, pmap = _seed()
        for fam, name in pmap.items():
            self.assertIn(name, profs, f"{fam!r} maps to profile {name!r}, which is not defined")

    def test_every_known_family_has_a_profile(self):
        """A family in the priority UI with no profile silently keeps the stale global hotkeys --
        the original bug. mad_config.KNOWN_FAMILIES is the list the UI offers."""
        from lib.mad_config import KNOWN_FAMILIES
        _p, _profs, pmap = _seed()
        for fam in KNOWN_FAMILIES:
            self.assertIn(fam, pmap, f"family {fam!r} is offered by the UI but has no profile")

    def test_the_global_map_cascades(self):
        """profile_name_for must find the GLOBAL map. resolve_policy does not merge top-level
        tables, so reading only sys_entry would make the whole seed inert -- exactly how the
        global warn toggles shipped dead."""
        p, _profs, pmap = _seed()
        for fam, name in pmap.items():
            self.assertEqual(ra_profiles.profile_name_for(p, fam, {"category": "console"}), name,
                             f"the global map does not reach {fam!r}")


class VoidedSets(unittest.TestCase):
    """THE test of this commit: no family may be pointed at a profile its pads cannot express."""

    def _resolve(self, fam):
        p, profs, pmap = _seed()
        prof = profs[pmap[fam]]
        return ra_profiles.resolve_for(PADS[fam], DRIVER.get(fam, "udev"), prof, port=1)

    def test_no_family_resolves_to_a_voided_hotkey_set(self):
        _p, _profs, pmap = _seed()
        for fam in pmap:
            if fam not in PADS:
                continue
            with self.subTest(family=fam):
                out = self._resolve(fam)
                self.assertTrue(out, f"{fam}: no base map, so the profile writes nothing")
                bound = [b for _f, b in ra_profiles.HOTKEYS
                         if any(out.get(f"{b}_{k}", "nul") != "nul" for k in ("btn", "axis", "mbtn"))]
                self.assertIn("input_enable_hotkey", bound,
                              f"{fam}: the MODIFIER does not resolve, so ra_profiles voided the "
                              "whole set -- this family gets NO hotkeys at all")
                self.assertGreaterEqual(len(bound), 4, f"{fam}: only {bound} resolved")

    def test_8bitdo_is_not_on_the_gamepad_profile(self):
        """The mistake the plan made. An FC30 has no sticks and no triggers; the Gamepad profile
        asks for l3/l2/r2, so the modifier dies and the set voids. Guarded by name because the
        harm is silent -- it fails SAFE, which is why it survived a review."""
        _p, _profs, pmap = _seed()
        self.assertNotEqual(pmap.get("8BitDo"), "Gamepad")
        gamepad = _seed()[1]["Gamepad"]
        out = ra_profiles.resolve_for(PADS["8BitDo"], "udev", gamepad, port=1)
        self.assertEqual(out.get("input_enable_hotkey_btn"), "nul",
                         "an FC30 CAN take l3 now? re-check whether Retro is still the right call")


class XArcadeIdentity(unittest.TestCase):
    """The cabinet is a port-identified special case, and the whole Arcade profile hangs off it."""

    def test_the_cabinet_gets_arcade_only_via_its_usb_port(self):
        from lib import routing
        xa = PADS["X-Arcade"]
        self.assertEqual(routing.family_token_of(xa, XPORT), "X-Arcade")
        # Unset [hardware].xarcade_port and the same device reads as a plain Xbox pad -> Gamepad.
        self.assertEqual(routing.family_token_of(xa, ""), "Xbox")

    def test_a_misidentified_cabinet_degrades_to_no_hotkeys_not_wrong_ones(self):
        """If xarcade_port is ever unset the stick falls to Gamepad, which it cannot express. That
        must void (no hotkeys), never half-bind (ungated hotkeys)."""
        gamepad = _seed()[1]["Gamepad"]
        out = ra_profiles.resolve_for(PADS["X-Arcade"], "udev", gamepad, port=1)
        for _f, b in ra_profiles.HOTKEYS:
            for k in ("btn", "axis", "mbtn"):
                self.assertEqual(out.get(f"{b}_{k}"), "nul",
                                 f"{b}_{k} bound on a mis-identified cabinet: a PARTIAL set fires "
                                 "ungated")


class ArcadePreservesTheDeployedScheme(unittest.TestCase):
    """Docked is the main rig. The Arcade profile must re-express the CURRENT global cfg, not
    change it: modifier/menu/slow-mo keep their exact numbers, and only rewind/fast-forward move
    -- from the raw 13/14 to direction-explicit hat tokens, which is the point (kernel 6.16 already
    moved those ranks once; see xarcade-dpad-kernel-flip-2026-07-17)."""

    def test_arcade_resolves_to_the_cabinets_live_numbers(self):
        prof = _seed()[1]["Arcade"]
        out = ra_profiles.resolve_for(PADS["X-Arcade"], "udev", prof, port=1)
        self.assertEqual(out["input_enable_hotkey_btn"], "6")        # Select, unchanged
        self.assertEqual(out["input_menu_toggle_btn"], "7")          # Start, unchanged
        self.assertEqual(out["input_toggle_slowmotion_btn"], "5")    # R, unchanged
        self.assertEqual(out["input_rewind_btn"], "h0left")          # was the raw 13
        self.assertEqual(out["input_hold_fast_forward_btn"], "h0right")   # was the raw 14
        self.assertEqual(out["input_exit_emulator_mbtn"], "3")       # trackball red button


class DeckPreservesTheHandheldScheme(unittest.TestCase):
    """The Deck profile supersedes [handheld.retroarch]'s six knobs, so it must reproduce them.
    Fed an EXPLICIT scheme, never the rig's: the deployed local.toml had slow-mo on L2 by
    accident, Miquel confirmed it was a slip on 2026-07-17, and the override was removed so the
    repo default (R2) is what both rails now mean."""

    def test_deck_row_reproduces_rail_a_from_the_shipped_scheme(self):
        from lib import ra_handheld_input as rhi
        base, _profs, _pmap = _seed()
        ra = dict((base.get("handheld") or {}).get("retroarch") or {})
        self.assertTrue(ra, "[handheld.retroarch] vanished from the shipped policy")
        old = rhi._handheld_values(ra)
        new = ra_profiles.resolve_for(PADS["Steam Deck"], "sdl2", _seed()[1]["Deck"], port=1)
        for _field, key, _dflt in rhi._SCHEME:
            self.assertEqual(new[key], str(old[key]),
                             f"{key}: the Deck profile no longer reproduces [handheld.retroarch], "
                             "so folding in the handheld rail would CHANGE the scheme")

    def test_slowmotion_is_r2_the_shipped_default(self):
        new = ra_profiles.resolve_for(PADS["Steam Deck"], "sdl2", _seed()[1]["Deck"], port=1)
        self.assertEqual(new["input_toggle_slowmotion_axis"], "+5")   # R2. "+4" would be L2.


if __name__ == "__main__":
    unittest.main()
