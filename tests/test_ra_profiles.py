"""lib/ra_profiles.py — the semantic resolver behind RetroArch input profiles.

A profile stores NAMES ("l3", "select", "left"), never numbers; the seated pad's base map turns
them into that pad's numbers at launch. These tests pin the resolution against the REAL base maps
of Miquel's two pads, captured live 2026-07-17, so "one Gamepad profile is correct for every pad"
is proven rather than asserted.

The numbers below are measurements, not fixtures-by-convenience:
  X-Arcade  045e:02a1  select_btn=6 start_btn=7 r_btn=5 left_btn=h0left  (no l3/r3: no sticks)
  DualSense 054c:0ce6  l3_btn=11 l2_axis=+2 r2_axis=+5 r_btn=5 start_btn=9
and they reproduce the exact global cfg this feature replaces: the Arcade profile resolves back to
6/7/5, which is why it is a faithful re-expression of a WORKING scheme and not a repair.

Run:  python3 -m unittest tests.test_ra_profiles -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib import ra_profiles as rp
from tests._fakes import FakeDevice

# --- REAL base maps, read off this rig with device_binds.binds_for() on 2026-07-17 ---
XARCADE_BASE = {
    "a_btn": "0", "b_btn": "1", "x_btn": "2", "y_btn": "3",
    "l_btn": "4", "r_btn": "5", "select_btn": "6", "start_btn": "7",
    "up_btn": "h0up", "down_btn": "h0down", "left_btn": "h0left", "right_btn": "h0right",
    "l2_axis": "+2", "r2_axis": "+5",
}
DUALSENSE_BASE = {
    "a_btn": "1", "b_btn": "0", "x_btn": "2", "y_btn": "3",
    "l_btn": "4", "r_btn": "5", "select_btn": "8", "start_btn": "9",
    "up_btn": "h0up", "down_btn": "h0down", "left_btn": "h0left", "right_btn": "h0right",
    "l2_axis": "+2", "r2_axis": "+5", "l3_btn": "11", "r3_btn": "12",
}

# The seeded profiles from the plan.
ARCADE = {"hotkeys": {"modifier": "select", "rewind": "left", "fast_forward": "right",
                      "slowmotion": "r", "menu": "start", "quit": "mbtn:3"}}
GAMEPAD = {"hotkeys": {"modifier": "l3", "rewind": "l2", "fast_forward": "r2",
                       "slowmotion": "r", "menu": "start", "quit": ""}}


def _dev(vid=0x054c, pid=0x0ce6, name="DualSense Wireless Controller"):
    return FakeDevice(vid=vid, pid=pid, path="/dev/input/event0", name=name)


class Tokens(unittest.TestCase):
    def test_a_button_name(self):
        self.assertEqual(rp.resolve_token("select", XARCADE_BASE),
                         {"btn": "6", "axis": "nul", "mbtn": "nul"})

    def test_a_hat_token_goes_in_btn_not_axis(self):
        # "h0up" in an _axis key is SILENTLY DISCARDED by RetroArch's parser (it fails the +/- test
        # and leaves the bind untouched), so a hat must ride _btn. This is also what makes the
        # X-Arcade's d-pad kernel-proof: h0left is direction-explicit, unlike the rank 13 it
        # replaces, which the 6.16 xpad change already moved once.
        self.assertEqual(rp.resolve_token("left", XARCADE_BASE),
                         {"btn": "h0left", "axis": "nul", "mbtn": "nul"})

    def test_an_analog_trigger_falls_through_to_axis(self):
        # The DualSense's autoconfig has NO l2_btn (upstream deliberately prefers the axis so L2
        # polls analog), so the button lookup must fall through rather than give up.
        self.assertEqual(rp.resolve_token("l2", DUALSENSE_BASE),
                         {"btn": "nul", "axis": "+2", "mbtn": "nul"})

    def test_empty_means_deliberately_unbound(self):
        self.assertEqual(rp.resolve_token("", DUALSENSE_BASE),
                         {"btn": "nul", "axis": "nul", "mbtn": "nul"})

    def test_a_control_this_pad_lacks_is_unresolvable_not_a_guess(self):
        # The X-Arcade has no thumbsticks, so "l3" cannot resolve. None -> the caller writes
        # nothing and logs; inventing a number here would bind an arbitrary button.
        self.assertIsNone(rp.resolve_token("l3", XARCADE_BASE))
        self.assertEqual(rp.resolve_token("l3", DUALSENSE_BASE),
                         {"btn": "11", "axis": "nul", "mbtn": "nul"})

    def test_mbtn_escape(self):
        # The X-Arcade trackball's red button: the live cfg's input_exit_emulator_mbtn = "3".
        self.assertEqual(rp.resolve_token("mbtn:3", XARCADE_BASE),
                         {"btn": "nul", "axis": "nul", "mbtn": "3"})

    def test_raw_escapes(self):
        self.assertEqual(rp.resolve_token("btn:9", {}), {"btn": "9", "axis": "nul", "mbtn": "nul"})
        self.assertEqual(rp.resolve_token("axis:+4", {}), {"btn": "nul", "axis": "+4", "mbtn": "nul"})

    def test_a_garbage_escape_is_refused_never_coerced(self):
        # RetroArch's own parser is NOT fail-safe: input_config_parse_joy_axis checks only
        # "length>=2 and leads with +/-", then strtol(base 0) whose failure is indistinguishable
        # from success -- "+abc" silently binds axis 0 and sets valid=true. Catch it here.
        for bad in ("axis:+abc", "axis:2", "axis:+", "axis:", "btn:xyz", "mbtn:x", "axis:+-2"):
            self.assertIsNone(rp.resolve_token(bad, {}), bad)

    def test_a_nul_in_the_base_map_is_not_a_bind(self):
        # The X-Arcade's autoconfig carries l3_btn = "nul" (MAD's sentinel writes it explicitly).
        self.assertIsNone(rp.resolve_token("l3", {"l3_btn": "nul"}))

    def test_a_corrupt_base_value_is_refused(self):
        self.assertIsNone(rp.resolve_token("select", {"select_btn": "not-a-button"}))
        self.assertIsNone(rp.resolve_token("l2", {"l2_axis": "garbage"}))


class Hotkeys(unittest.TestCase):
    def test_arcade_reproduces_the_working_global_cfg(self):
        # THE PROOF that the Arcade profile is a faithful re-expression: it resolves back to the
        # exact numbers the cabinet uses today (6/7/5), except left/right resolve through hat
        # tokens instead of the raw 13/14 -- the kernel-proof win.
        got = rp.hotkey_lines(ARCADE["hotkeys"], XARCADE_BASE)
        self.assertEqual(got["input_enable_hotkey_btn"], "6")        # Select, as the live cfg
        self.assertEqual(got["input_menu_toggle_btn"], "7")          # Start
        self.assertEqual(got["input_toggle_slowmotion_btn"], "5")    # R
        self.assertEqual(got["input_rewind_btn"], "h0left")          # was the raw 13
        self.assertEqual(got["input_hold_fast_forward_btn"], "h0right")
        self.assertEqual(got["input_exit_emulator_mbtn"], "3")       # the trackball red button

    def test_gamepad_on_a_dualsense_is_miquels_spec(self):
        got = rp.hotkey_lines(GAMEPAD["hotkeys"], DUALSENSE_BASE)
        self.assertEqual(got["input_enable_hotkey_btn"], "11")       # L3
        self.assertEqual(got["input_rewind_axis"], "+2")             # LT
        self.assertEqual(got["input_hold_fast_forward_axis"], "+5")  # RT
        self.assertEqual(got["input_toggle_slowmotion_btn"], "5")    # RB
        self.assertEqual(got["input_menu_toggle_btn"], "9")          # Start/Options

    def test_one_gamepad_profile_serves_pads_with_different_numbers(self):
        # The whole point of storing names. Same profile, two pads, two correct answers.
        eightbitdo = dict(DUALSENSE_BASE, l3_btn="13", r_btn="7", start_btn="11")
        a = rp.hotkey_lines(GAMEPAD["hotkeys"], DUALSENSE_BASE)
        b = rp.hotkey_lines(GAMEPAD["hotkeys"], eightbitdo)
        self.assertEqual(a["input_enable_hotkey_btn"], "11")
        self.assertEqual(b["input_enable_hotkey_btn"], "13")
        self.assertEqual(b["input_toggle_slowmotion_btn"], "7")

    def test_every_variant_is_written_so_a_stale_one_cannot_fire(self):
        got = rp.hotkey_lines({"rewind": "l2"}, DUALSENSE_BASE)
        self.assertEqual(got["input_rewind_axis"], "+2")
        self.assertEqual(got["input_rewind_btn"], "nul")     # clears the X-Arcade's stale "13"
        self.assertEqual(got["input_rewind_mbtn"], "nul")

    def test_hotkey_keys_carry_no_player_prefix(self):
        # Meta binds exist for user 0 ONLY (input_config_get_prefix returns "input" for meta, and
        # only when user == 0). input_player2_menu_toggle_btn is not a thing RetroArch reads.
        for k in rp.hotkey_lines(GAMEPAD["hotkeys"], DUALSENSE_BASE):
            self.assertFalse(k.startswith("input_player"), k)

    def test_an_unresolvable_hotkey_is_left_unbound_and_logged(self):
        log = mock.Mock()
        # rewind=l2 is fine here; only slowmotion cannot resolve on a pad with no R.
        got = rp.hotkey_lines({"modifier": "select", "slowmotion": "r3"},
                              {"select_btn": "6"}, logger=log)
        self.assertEqual(got["input_toggle_slowmotion_btn"], "nul")
        self.assertEqual(got["input_enable_hotkey_btn"], "6")        # the modifier still binds
        self.assertTrue(log.warning.called)

    def test_a_modifier_that_cannot_resolve_voids_the_whole_set(self):
        # THE FOOT-GUN, found by resolving the seeded Gamepad profile against the LIVE 8BitDo
        # FC30 II: no sticks, no triggers, so l3/l2/r2 do not resolve -- but slowmotion (r) and
        # menu (start) DID. Verified in v1.22.2 input_driver.c: the block that raises
        # INP_FLAG_BLOCK_HOTKEY is gated on CHECK_INPUT_DRIVER_BLOCK_HOTKEY, true only when the
        # enable-hotkey bind is SET. Unbound modifier + bound menu = menu fires UNGATED, so Start
        # would open the menu every press, mid-game.
        fc30 = {"a_btn": "1", "b_btn": "0", "r_btn": "7", "start_btn": "11", "select_btn": "10"}
        log = mock.Mock()
        got = rp.hotkey_lines(GAMEPAD["hotkeys"], fc30, logger=log)
        self.assertEqual(got["input_toggle_slowmotion_btn"], "nul")   # would have been "7"
        self.assertEqual(got["input_menu_toggle_btn"], "nul")         # would have been "11"
        self.assertTrue(all(v == "nul" for v in got.values()))
        self.assertTrue(log.warning.called)

    def test_an_empty_modifier_is_a_deliberate_ungated_scheme(self):
        # "" is the user saying "no modifier, hotkeys always live". Honour it; only an ASKED-FOR
        # modifier that the pad cannot give voids the set.
        got = rp.hotkey_lines({"modifier": "", "menu": "start"}, DUALSENSE_BASE)
        self.assertEqual(got["input_menu_toggle_btn"], "9")
        self.assertEqual(got["input_enable_hotkey_btn"], "nul")

    def test_an_axis_modifier_is_refused_and_that_voids_the_set(self):
        # RetroArch's "menu toggle bypasses enable_hotkey" escape hatch is joykey-ONLY and ignores
        # joyaxis, so an axis-only modifier lets menu-toggle fire UNMODIFIED.
        # The two guards COMPOSE, and they must: refusing the axis modifier leaves NO modifier, and
        # a bound menu with no modifier fires ungated on every press. So the whole set goes.
        log = mock.Mock()
        got = rp.hotkey_lines({"modifier": "l2", "menu": "start"}, DUALSENSE_BASE, logger=log)
        self.assertEqual(got["input_enable_hotkey_axis"], "nul")
        self.assertEqual(got["input_enable_hotkey_btn"], "nul")
        self.assertEqual(got["input_menu_toggle_btn"], "nul")        # NOT "9": ungated is worse
        self.assertTrue(all(v == "nul" for v in got.values()))
        self.assertTrue(log.warning.called)


class Drivers(unittest.TestCase):
    def test_udev_uses_the_pads_own_autoconfig(self):
        with mock.patch.object(rp.device_binds, "binds_for", return_value=dict(XARCADE_BASE)):
            self.assertEqual(rp.base_map(_dev(), "udev"), XARCADE_BASE)

    def test_sdl2_uses_the_fixed_semantic_table(self):
        got = rp.base_map(_dev(), "sdl2")
        self.assertEqual(got["a_btn"], "0")
        self.assertEqual(got["l3_btn"], "7")       # SDL_CONTROLLER_BUTTON_LEFTSTICK
        self.assertEqual(got["r_btn"], "10")       # RIGHTSHOULDER
        self.assertEqual(got["l2_axis"], "+4")     # TRIGGERLEFT
        self.assertEqual(got["left_btn"], "13")    # DPAD_LEFT: a BUTTON, not a hat

    def test_the_two_number_spaces_are_not_interchangeable(self):
        # sdl2 sets num_hats = 0 for a recognised pad, so the d-pad is ONLY buttons 11-14. Under
        # udev the same pad exposes a real hat. Never share one table between them.
        sdl = rp.base_map(_dev(), "sdl2")
        self.assertEqual(sdl["left_btn"], "13")
        self.assertEqual(XARCADE_BASE["left_btn"], "h0left")
        self.assertNotEqual(sdl["l3_btn"], DUALSENSE_BASE["l3_btn"])   # 7 vs 11, same pad

    def test_the_same_profile_resolves_per_driver(self):
        sdl = rp.hotkey_lines(GAMEPAD["hotkeys"], rp.base_map(_dev(), "sdl2"))
        udev = rp.hotkey_lines(GAMEPAD["hotkeys"], DUALSENSE_BASE)
        self.assertEqual(sdl["input_enable_hotkey_btn"], "7")     # L3 under sdl2
        self.assertEqual(udev["input_enable_hotkey_btn"], "11")   # L3 under udev
        self.assertEqual(sdl["input_rewind_axis"], "+4")          # LT under sdl2
        self.assertEqual(udev["input_rewind_axis"], "+2")         # LT under udev

    def test_an_unknown_driver_writes_nothing(self):
        # Never guess a number space. dinput/xinput/hid are a table entry away; until then, refuse.
        self.assertIsNone(rp.base_map(_dev(), "dinput"))
        self.assertIsNone(rp.base_map(_dev(), ""))
        log = mock.Mock()
        self.assertEqual(rp.resolve_for(_dev(), "dinput", GAMEPAD, logger=log), {})
        self.assertTrue(log.warning.called)

    def test_a_udev_pad_with_no_autoconfig_writes_nothing(self):
        with mock.patch.object(rp.device_binds, "binds_for", return_value=None):
            self.assertIsNone(rp.base_map(_dev(), "udev"))


class ProfileMap(unittest.TestCase):
    POLICY = {"ra_profile_map": {"X-Arcade": "Arcade", "DualSense": "Gamepad"},
              "ra_profiles": {"Gamepad": GAMEPAD, "Arcade": ARCADE}}

    def test_global_map(self):
        self.assertEqual(rp.profile_name_for(self.POLICY, "DualSense"), "Gamepad")
        self.assertEqual(rp.profile_name_for(self.POLICY, "X-Arcade"), "Arcade")

    def test_an_unmapped_family_is_none(self):
        self.assertIsNone(rp.profile_name_for(self.POLICY, "8BitDo"))
        self.assertIsNone(rp.profile_name_for(self.POLICY, ""))

    def test_the_entry_tier_wins(self):
        ent = {"ra_profile_map": {"DualSense": "NES-DS"}}
        self.assertEqual(rp.profile_name_for(self.POLICY, "DualSense", ent), "NES-DS")

    def test_the_global_tier_is_cascaded_explicitly_per_family(self):
        # THE LANDMINE. resolve_policy does NOT merge top-level tables, so a system that overrides
        # only DualSense must still get the global X-Arcade mapping. This exact bug shipped once:
        # the global warn toggles were inert because _xarcade_warn read only sys_entry.
        ent = {"ra_profile_map": {"DualSense": "NES-DS"}}
        self.assertEqual(rp.profile_name_for(self.POLICY, "X-Arcade", ent), "Arcade")

    def test_a_hand_edited_husk_never_raises(self):
        for pol in ({"ra_profile_map": "nonsense"}, {}, {"ra_profile_map": None}):
            self.assertIsNone(rp.profile_name_for(pol, "DualSense"))

    def test_a_husk_entry_tier_falls_through_to_global(self):
        # A malformed per-system table must not SUPPRESS the global mapping: the tier is ignored,
        # not treated as an override to nothing. (I first asserted None here, which would have
        # demanded a hand-edit silently unbind a working global profile.)
        self.assertEqual(rp.profile_name_for(self.POLICY, "DualSense", {"ra_profile_map": "junk"}),
                         "Gamepad")

    def test_get_profile(self):
        self.assertEqual(rp.get_profile(self.POLICY, "Gamepad"), GAMEPAD)
        self.assertIsNone(rp.get_profile(self.POLICY, "Nope"))
        self.assertIsNone(rp.get_profile({"ra_profiles": "husk"}, "Gamepad"))


class ResolveFor(unittest.TestCase):
    def _resolve(self, profile, base=None, **kw):
        with mock.patch.object(rp.device_binds, "binds_for",
                               return_value=dict(base or DUALSENSE_BASE)):
            return rp.resolve_for(_dev(), "udev", profile, **kw)

    def test_gameplay_binds_ride_the_port(self):
        got = self._resolve(GAMEPAD)
        self.assertEqual(got["input_player1_a_btn"], "1")
        self.assertEqual(got["input_player1_left_btn"], "h0left")

    def test_gameplay_remaps_by_semantic_token(self):
        # A driven by the physical B button, device-agnostic: a_btn <- "b" resolves to base["b_btn"].
        got = self._resolve(dict(GAMEPAD, gameplay={"a_btn": "b"}))
        self.assertEqual(got["input_player1_a_btn"], DUALSENSE_BASE["b_btn"])   # "0"
        self.assertEqual(got["input_player1_b_btn"], "0")     # b_btn itself untouched

    def test_empty_gameplay_leaves_the_full_base_map(self):
        got = self._resolve(dict(GAMEPAD, gameplay={}))
        self.assertEqual(got["input_player1_a_btn"], DUALSENSE_BASE["a_btn"])   # "1"
        self.assertEqual(got["input_player1_left_btn"], "h0left")

    def test_a_cross_variant_gameplay_remap_keeps_base_never_nul(self):
        # LOAD-BEARING: a_btn <- l2 resolves to an AXIS, so the btn variant is "nul"; writing it would
        # UNBIND the A button. Keep the base bind instead, and warn.
        log = mock.Mock()
        got = self._resolve(dict(GAMEPAD, gameplay={"a_btn": "l2"}), logger=log)
        self.assertEqual(got["input_player1_a_btn"], DUALSENSE_BASE["a_btn"])   # "1", not "nul"
        self.assertNotEqual(got["input_player1_a_btn"], "nul")
        self.assertTrue(log.warning.called)

    def test_a_hat_token_gameplay_remap_stays_a_live_hat(self):
        # Remap Up to the physical LEFT on a hat pad -> "h0left", a live hat, NOT a frozen rank -- so
        # the X-Arcade d-pad survives a kernel renumber even when remapped (guardrail 2).
        got = self._resolve(dict(GAMEPAD, gameplay={"up_btn": "left"}), base=XARCADE_BASE)
        self.assertEqual(got["input_player1_up_btn"], "h0left")

    def test_an_unknown_gameplay_control_is_dropped_by_the_whitelist(self):
        # A control outside the editable set (paddle1_btn) is silently ignored -- only whitelisted
        # controls are read, so a hand-edited stray key can never write a bogus key.
        got = self._resolve(dict(GAMEPAD, gameplay={"paddle1_btn": "b"}))
        self.assertNotIn("input_player1_paddle1_btn", got)

    def test_a_trigger_row_resolves_to_the_axis_variant(self):
        # The ONLY path that selects got["axis"]: l2_axis <- r2 writes the DualSense's r2 axis onto L2,
        # device-agnostic. Mirror of the button case, but the axis branch of the variant ternary.
        got = self._resolve(dict(GAMEPAD, gameplay={"l2_axis": "r2"}))
        self.assertEqual(got["input_player1_l2_axis"], DUALSENSE_BASE["r2_axis"])   # "+5"
        self.assertEqual(got["input_player1_r2_axis"], DUALSENSE_BASE["r2_axis"])   # r2_axis untouched

    def test_a_reverse_cross_variant_trigger_remap_keeps_base_never_nul(self):
        # Mirror of the button guard on the AXIS side: l2_axis <- "a" resolves to a BUTTON, so the axis
        # variant is "nul"; keep the base bind, never write a button rank into the _axis key.
        log = mock.Mock()
        got = self._resolve(dict(GAMEPAD, gameplay={"l2_axis": "a"}), logger=log)
        self.assertEqual(got["input_player1_l2_axis"], DUALSENSE_BASE["l2_axis"])   # "+2", not "nul"
        self.assertNotEqual(got["input_player1_l2_axis"], "nul")
        self.assertTrue(log.warning.called)

    def test_a_gameplay_token_the_pad_lacks_keeps_base_never_nul(self):
        # resolve_token returns None (the X-Arcade has no L3): a DIFFERENT keep-base path than the
        # cross-variant "nul" case (here v is None, not "nul"). Must keep the base bind and warn.
        log = mock.Mock()
        got = self._resolve(dict(GAMEPAD, gameplay={"a_btn": "l3"}), base=XARCADE_BASE, logger=log)
        self.assertEqual(got["input_player1_a_btn"], XARCADE_BASE["a_btn"])   # "0", kept
        self.assertNotEqual(got["input_player1_a_btn"], "nul")
        self.assertTrue(log.warning.called)

    def test_a_whitelisted_control_the_pad_lacks_is_skipped(self):
        # l3_btn IS editable, but the X-Arcade base has no l3_btn -> the `suffix not in eff` arm skips
        # it entirely (never emitted), distinct from an unresolvable token or a non-whitelisted key.
        got = self._resolve(dict(GAMEPAD, gameplay={"l3_btn": "a"}), base=XARCADE_BASE)
        self.assertNotIn("input_player1_l3_btn", got)

    def test_settings_are_opt_in(self):
        self.assertNotIn("input_player1_analog_dpad_mode", self._resolve(GAMEPAD))
        got = self._resolve(dict(GAMEPAD, settings={"analog_dpad_mode": "1",
                                                    "libretro_device": "1"}))
        self.assertEqual(got["input_player1_analog_dpad_mode"], "1")
        self.assertEqual(got["input_libretro_device_p1"], "1")

    def test_hotkeys_only_on_p1(self):
        # RetroArch polls hotkeys on ONE port and meta binds are user-0 only, so writing them for
        # P2 would be noise at best.
        p1 = self._resolve(GAMEPAD, port=1)
        p2 = self._resolve(GAMEPAD, port=2)
        self.assertIn("input_enable_hotkey_btn", p1)
        self.assertNotIn("input_enable_hotkey_btn", p2)
        self.assertEqual(p2["input_player2_a_btn"], "1")      # gameplay binds still ride P2

    def test_a_profile_with_no_hotkeys_still_writes_gameplay(self):
        got = self._resolve({"hotkeys": {}})
        self.assertEqual(got["input_player1_a_btn"], "1")
        self.assertEqual(got["input_enable_hotkey_btn"], "nul")

    def test_a_husk_profile_never_raises(self):
        got = self._resolve({"hotkeys": "junk", "gameplay": "junk", "settings": "junk",
                             "lightgun": "junk"})
        self.assertEqual(got["input_player1_a_btn"], "1")


class Lightgun(unittest.TestCase):
    """gun_* binds are RAW (mouse buttons / keyboard keys), emitted per-port ONLY where set, so the
    working global cfg is inherited by default. mouse_index is router-only, never in resolve_for."""

    def _resolve(self, profile, **kw):
        with mock.patch.object(rp.device_binds, "binds_for", return_value=dict(DUALSENSE_BASE)):
            return rp.resolve_for(_dev(), "udev", profile, **kw)

    def test_gun_variants_forms(self):
        self.assertIsNone(rp._gun_variants(""))                       # unset -> inherit
        self.assertEqual(rp._gun_variants("mbtn:2"), {"": "nul", "btn": "nul", "axis": "nul", "mbtn": "2"})
        self.assertEqual(rp._gun_variants("z"),      {"": "z",   "btn": "nul", "axis": "nul", "mbtn": "nul"})
        self.assertEqual(rp._gun_variants("up"),     {"": "up",  "btn": "nul", "axis": "nul", "mbtn": "nul"})
        self.assertEqual(rp._gun_variants("btn:5"),  {"": "nul", "btn": "5",   "axis": "nul", "mbtn": "nul"})
        self.assertIsNone(rp._gun_variants("mbtn:bad"))               # garbage escape refused

    def test_emits_only_the_binds_set(self):
        got = self._resolve({"lightgun": {"trigger": "mbtn:1", "aux_a": "z"}})
        # trigger -> the mbtn variant set, the other three nul'd so a stale variant can't also fire
        self.assertEqual(got["input_player1_gun_trigger_mbtn"], "1")
        self.assertEqual(got["input_player1_gun_trigger"], "nul")
        self.assertEqual(got["input_player1_gun_trigger_btn"], "nul")
        self.assertEqual(got["input_player1_gun_trigger_axis"], "nul")
        # aux_a -> a keyboard key in the bare form
        self.assertEqual(got["input_player1_gun_aux_a"], "z")
        self.assertEqual(got["input_player1_gun_aux_a_mbtn"], "nul")
        # a bind NOT set is not emitted at all -> the working global cfg value is inherited
        self.assertNotIn("input_player1_gun_aux_b", got)
        self.assertNotIn("input_player1_gun_dpad_up", got)

    def test_no_lightgun_table_emits_no_gun_keys(self):
        self.assertFalse([k for k in self._resolve(GAMEPAD) if "_gun_" in k])

    def test_gun_binds_are_per_port(self):
        got = self._resolve({"lightgun": {"trigger": "mbtn:1"}}, port=2)
        self.assertEqual(got["input_player2_gun_trigger_mbtn"], "1")
        self.assertNotIn("input_player1_gun_trigger_mbtn", got)

    def test_mouse_index_is_never_emitted(self):
        # mouse_index is the router's job (auto-detect wins); a per-family index in resolve_for output
        # would clobber the per-device auto-detected one.
        got = self._resolve({"lightgun": {"trigger": "mbtn:1", "mouse_index": "5"}})
        self.assertFalse([k for k in got if "mouse_index" in k])

    def test_manual_mouse_index_helper(self):
        self.assertEqual(rp.manual_mouse_index({"lightgun": {"mouse_index": "3"}}), 3)
        self.assertIsNone(rp.manual_mouse_index({"lightgun": {"mouse_index": ""}}))
        self.assertIsNone(rp.manual_mouse_index({"lightgun": {}}))
        self.assertIsNone(rp.manual_mouse_index({}))
        self.assertIsNone(rp.manual_mouse_index({"lightgun": {"mouse_index": "junk"}}))


if __name__ == "__main__":
    unittest.main()
