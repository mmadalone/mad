"""Switch input-profile picks at LAUNCH: lib/switch_bind resolves this context's picks and threads
them into the writers.

Eden/Citron are keyed per controller FAMILY (their bindings embed the pad guid), Ryujinx per PLAYER
(it discards a profile's device identity on load). Both must degrade to "leave it resting" on any
problem -- a profile issue may never block the pad bind.

Run:  python3 -m unittest tests.test_switch_input_profiles -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import switch_bind as sb
from tests._fakes import sd

_DS_GUID = "030000004c050000e60c000000006800"      # DualSense 054c:0ce6
_WP_GUID = "050000007e0500003003000001000000"      # Wii U Pro  057e:0330
_DECK_GUID = "030079f6de280000ff11000001000000"    # Steam Deck 28de:1205


def _ds(i=0):
    return sd(i, "054c:0ce6", _DS_GUID, "DualSense Wireless Controller")


def _wp(i=1):
    return sd(i, "057e:0330", _WP_GUID, "Nintendo Wii Remote Pro Controller")


class _Ctx(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self._saved = os.environ.get("MAD_FORCE_CONTEXT")
        os.environ["MAD_FORCE_CONTEXT"] = "docked"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("MAD_FORCE_CONTEXT", None)
        else:
            os.environ["MAD_FORCE_CONTEXT"] = self._saved

    def _ctx(self, c):
        os.environ["MAD_FORCE_CONTEXT"] = c

    def _pol(self, emu, be):
        return mock.patch("lib.policy.load_merged", lambda: {"backends": {emu: be}})


# ── Eden / Citron: family-keyed ─────────────────────────────────────────────────
class YuzuPicks(_Ctx):
    def setUp(self):
        super().setUp()
        self.inp = self.d / "input"
        self.inp.mkdir()
        for s in ("DS 1", "DS 2", "WiiU Pro 1"):
            (self.inp / f"{s}.ini").write_text("[Controls]\nbutton_a=x\n", encoding="utf-8")
        self.be = {"template_profile": str(self.inp / "T.ini"),
                   "profile_map": {"docked": {"DualSense": "DS 1", "Wii Remote Pro": "WiiU Pro 1"},
                                   "handheld": {"DualSense": "DS 2"}}}

    def test_docked_picks_the_docked_map(self):
        with self._pol("eden", self.be):
            self.assertEqual(sb._yuzu_profile_picks("eden", [_ds(), _wp()]),
                             {("054c:0ce6", 0): "DS 1", ("057e:0330", 0): "WiiU Pro 1"})

    def test_handheld_is_independent_and_never_inherits_docked(self):
        self._ctx("handheld")
        with self._pol("eden", self.be):
            picks = sb._yuzu_profile_picks("eden", [_ds(), _wp()])
        self.assertEqual(picks, {("054c:0ce6", 0): "DS 2"})     # Wii U Pro unset handheld -> resting

    def test_second_same_family_pad_derives_the_next_profile(self):
        with self._pol("eden", self.be):
            self.assertEqual(sb._yuzu_profile_picks("eden", [_ds(0), _ds(1)]),
                             {("054c:0ce6", 0): "DS 1", ("054c:0ce6", 1): "DS 2"})

    def test_citron_reads_its_own_backend_table(self):
        with self._pol("citron", self.be):
            self.assertEqual(sb._yuzu_profile_picks("citron", [_ds()]), {("054c:0ce6", 0): "DS 1"})

    def test_no_map_means_no_picks(self):
        with self._pol("eden", {"template_profile": str(self.inp / "T.ini")}):
            self.assertEqual(sb._yuzu_profile_picks("eden", [_ds()]), {})

    def test_policy_failure_degrades_to_none_not_an_exception(self):
        def boom():
            raise RuntimeError("policy unreadable")
        with mock.patch("lib.policy.load_merged", boom):
            self.assertEqual(sb._yuzu_profile_picks("eden", [_ds()]), {})

    def test_resolver_failure_degrades_to_none(self):
        with self._pol("eden", self.be), \
             mock.patch("lib.yuzu_profiles.profile_keys", side_effect=RuntimeError("x")):
            self.assertIsNone(sb._yuzu_profile_picks("eden", [_ds()]))


# ── Ryujinx: player-keyed ───────────────────────────────────────────────────────
class RyujinxPicks(_Ctx):
    def setUp(self):
        super().setUp()
        self.pdir = self.d / "profiles" / "controller"
        self.pdir.mkdir(parents=True)
        for stem, ct in (("Pro 1", "ProController"), ("Pro 2", "ProController")):
            (self.pdir / f"{stem}.json").write_text(json.dumps({
                "id": "0-DEAD", "backend": "GamepadSDL3", "player_index": "Player7",
                "controller_type": ct, "left_joycon": {"button_a": stem}}), encoding="utf-8")
        self.be = {"config_file": str(self.d / "Config.json"),
                   "profile_map": {"docked": {"p1": "Pro 1", "p2": "Pro 2"},
                                   "handheld": {"p1": "Pro 2"}}}

    def test_docked_seats_each_player_and_mirrors_handheld_slot(self):
        with self._pol("ryujinx", self.be):
            picks = sb._ryujinx_profile_picks([_ds(), _wp()])
        self.assertEqual(set(picks), {"Player1", "Player2", "Handheld"})
        self.assertEqual(picks["Player1"]["left_joycon"], {"button_a": "Pro 1"})
        self.assertEqual(picks["Player2"]["left_joycon"], {"button_a": "Pro 2"})
        self.assertEqual(picks["Handheld"]["left_joycon"], {"button_a": "Pro 1"})
        for m in picks.values():                       # never carries device identity
            self.assertNotIn("id", m)
            self.assertNotIn("player_index", m)

    def test_handheld_context_is_independent(self):
        self._ctx("handheld")
        with self._pol("ryujinx", self.be):
            picks = sb._ryujinx_profile_picks([_ds(), _wp()])
        self.assertEqual(set(picks), {"Player1", "Handheld"})   # p2 unset handheld
        self.assertEqual(picks["Player1"]["left_joycon"], {"button_a": "Pro 2"})

    def test_picks_are_capped_at_the_pad_count(self):
        with self._pol("ryujinx", self.be):
            picks = sb._ryujinx_profile_picks([_ds()])
        self.assertEqual(set(picks), {"Player1", "Handheld"})

    def test_resolver_failure_degrades_to_none(self):
        with self._pol("ryujinx", self.be), \
             mock.patch("lib.ryujinx_profiles.seat_mappings", side_effect=RuntimeError("x")):
            self.assertIsNone(sb._ryujinx_profile_picks([_ds()]))


# ── the writer threading ────────────────────────────────────────────────────────
class WriteThreading(unittest.TestCase):
    """_write must forward `profiles` to every emulator that accepts it, and to nobody else."""

    def test_forwarded_to_the_three_switch_writers(self):
        seen = {}

        def cap(name):
            def _f(pads, **kw):
                seen[name] = kw.get("profiles")
                return {}
            return _f

        with mock.patch("lib.switch_bind.ryujinx_cfg.assign_devices", cap("ryujinx")), \
             mock.patch("lib.switch_bind.eden_cfg.assign_devices", cap("yuzu")):
            sb._write("ryujinx", Path("/x"), [_ds()], profiles={"Player1": {"a": 1}})
            self.assertEqual(seen["ryujinx"], {"Player1": {"a": 1}})
            sb._write("eden", Path("/x"), [_ds()], profiles={("054c:0ce6", 0): "DS 1"})
            self.assertEqual(seen["yuzu"], {("054c:0ce6", 0): "DS 1"})
            sb._write("citron", Path("/x"), [_ds()], profiles={("054c:0ce6", 0): "DS 2"})
            self.assertEqual(seen["yuzu"], {("054c:0ce6", 0): "DS 2"})

    def test_default_is_none_so_other_emulators_are_untouched(self):
        seen = {}

        def cap(pads, **kw):
            seen.update(kw)
            return {}

        with mock.patch("lib.switch_bind.pcsx2_cfg.assign_devices", cap):
            sb._write("pcsx2", Path("/x"), [_ds()])
        self.assertNotIn("profiles", seen)          # PCSX2's writer never sees the kwarg


class FamilyShim(unittest.TestCase):
    """_sdl_family bridges devices.SdlDevice (vidpid + name) to routing.family_of (vid/pid/name),
    which is the ONE family matcher -- this must never grow a second table."""

    def test_known_pads(self):
        self.assertEqual(sb._sdl_family(_ds()), "DualSense")
        self.assertEqual(sb._sdl_family(_wp()), "Wii Remote Pro")
        self.assertEqual(sb._sdl_family(sd(0, "28de:1205", _DECK_GUID, "Steam Deck")), "Steam Deck")

    def test_malformed_and_unknown_degrade_to_none(self):
        self.assertIsNone(sb._sdl_family(sd(0, "nope", _DS_GUID, "x")))
        self.assertIsNone(sb._sdl_family(sd(0, "", _DS_GUID, "x")))
        self.assertIsNone(sb._sdl_family(sd(0, "dead:beef", _DS_GUID, "Unknown Pad")))


if __name__ == "__main__":
    unittest.main()
