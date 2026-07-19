"""Tests for racontrollers.get + racontrollers.scopes (lib.madsrv.backends_cmds)
— the RetroArch hub's Controllers section. racontrollers.get mirrors
priority.get's order/nports/require_sinden composition (global scope reads
[defaults] instead of a systems/collections entry); its "toggles" field is
always [] now (the X-Arcade warn toggles moved to priority.get's per-system
"warn" field, see tests/test_priority.py). racontrollers.scopes lists every
PRESENT system/collection (configured ∪ available) for the Controllers
subpage picker. Pure logic — no real SDL/evdev: devices/policy/merged/
es_systems/es_collections are monkeypatched via mock.patch.object on
backends_cmds (it does `from ..policy import load_merged` etc. at module
level, so the source module must be patched where it's USED, not where it's
defined — same idiom the module's other RPCs rely on).

Run: python3 -m unittest tests.test_racontrollers -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib.madsrv import backends_cmds as bc
from lib.madsrv.rpc import RpcError
from tests._fakes import FakeDevice

XPORT = "1.2"
XARCADE_PHYS = "usb-xhci-hcd.0-1.2/input0"


def _dev(vid, pid, name="", phys="", path="/dev/input/event0"):
    return FakeDevice(vid=vid, pid=pid, path=path, name=name, phys=phys)


class RaControllersGet(unittest.TestCase):
    MERGED = {"defaults": {"warn_when_no_xarcade": False},
              "systems": {}, "collections": {}}

    def _get(self, params, merged=None, devs=None, policy=None):
        merged = self.MERGED if merged is None else merged
        devs = [] if devs is None else devs
        policy = {} if policy is None else policy
        with mock.patch.object(bc, "load_merged", return_value=merged), \
             mock.patch.object(bc, "load_policy", return_value=policy), \
             mock.patch.object(bc.dv, "enumerate_devices", return_value=devs), \
             mock.patch.object(bc.dv, "joypads", side_effect=lambda ds: ds):
            return bc._racontrollers_get(params)

    # ── global scope shape ──────────────────────────────────────────────────
    def test_global_scope_documented_keys(self):
        r = self._get({"scope": "global"})
        for key in ("scope", "name", "order", "nports", "require_sinden",
                    "connected_families", "toggles"):
            self.assertIn(key, r)
        self.assertEqual(r["scope"], "global")
        self.assertEqual(r["name"], "")
        self.assertIsInstance(r["order"], list)
        self.assertTrue(r["order"])                      # KNOWN_FAMILIES always populates it
        self.assertEqual(r["nports"], 2)
        self.assertFalse(r["require_sinden"])
        self.assertEqual(r["connected_families"], [])

    def test_global_toggles_now_empty(self):
        # The X-Arcade presence warnings moved OFF the global root and onto
        # each system's own priority.get "warn" field (RetroArch-hub
        # Controllers restructure) — the global scope no longer renders them,
        # regardless of what [defaults] holds.
        r = self._get({"scope": "global"})
        self.assertEqual(r["toggles"], [])

    def test_toggles_empty_even_when_defaults_missing_keys(self):
        merged = {"defaults": {}, "systems": {}, "collections": {}}
        r = self._get({"scope": "global"}, merged=merged)
        self.assertEqual(r["toggles"], [])

    # ── scope validation ─────────────────────────────────────────────────────
    def test_invalid_scope_raises_einval(self):
        with self.assertRaises(RpcError):
            self._get({"scope": "bogus"})

    def test_missing_scope_defaults_to_global(self):
        r = self._get({})
        self.assertEqual(r["scope"], "global")

    # ── system/collection scope ─────────────────────────────────────────────
    def test_system_scope_has_no_toggles_and_reads_ports(self):
        merged = {"defaults": {}, "systems": {"nes": {"ports": [["Xbox", "8BitDo"]]}},
                  "collections": {}}
        r = self._get({"scope": "system", "name": "nes"}, merged=merged)
        self.assertEqual(r["toggles"], [])
        self.assertEqual(r["order"][0], "Xbox")
        self.assertEqual(r["order"][1], "8BitDo")

    def test_collection_scope_reads_require_sinden(self):
        merged = {"defaults": {}, "systems": {},
                  "collections": {"lightgun": {"ports": [["Xbox"]],
                                               "require_sinden": True}}}
        r = self._get({"scope": "collection", "name": "lightgun"}, merged=merged)
        self.assertTrue(r["require_sinden"])
        self.assertEqual(r["toggles"], [])

    def test_global_flat_defaults_ports_composes_order(self):
        # A hand-authored FLAT [defaults].ports (the router also accepts this form,
        # not only list-of-lists). The P1 family must be the first FAMILY, not the
        # first CHARACTER of the "DualSense" string (finding: existing[0] was read
        # as a str, iterating characters and dropping the intended P1).
        merged = {"defaults": {"ports": ["DualSense", "Xbox"]},
                  "systems": {}, "collections": {}}
        r = self._get({"scope": "global"}, merged=merged)
        self.assertEqual(r["order"][:2], ["DualSense", "Xbox"])

    # ── connected_families ───────────────────────────────────────────────────
    def test_connected_families_classifies_xarcade(self):
        devs = [_dev(0x045e, 0x02a1, name="Xbox 360 Wireless Receiver",
                     phys=XARCADE_PHYS)]
        policy = {"hardware": {"xarcade_port": XPORT}}
        r = self._get({"scope": "global"}, devs=devs, policy=policy)
        self.assertEqual(r["connected_families"], ["X-Arcade"])

    def test_unidentified_045e_is_not_xarcade(self):
        # same vid:pid but xarcade_port unset/mismatched -> falls back to family_of
        # (Xbox), never assumed to be the X-Arcade (routing.is_xarcade contract)
        devs = [_dev(0x045e, 0x02a1, name="Xbox 360 Wireless Receiver",
                     phys=XARCADE_PHYS)]
        r = self._get({"scope": "global"}, devs=devs, policy={})
        self.assertEqual(r["connected_families"], ["Xbox"])

    def test_connected_families_classifies_steam_deck(self):
        devs = [_dev(0x28de, 0x1205, name="Steam Deck Controller")]
        r = self._get({"scope": "global"}, devs=devs)
        self.assertEqual(r["connected_families"], ["Steam Deck"])

    def test_connected_families_dedupes_and_preserves_first_seen_order(self):
        devs = [_dev(0x054c, 0x0ce6, name="DualSense Wireless Controller"),
                _dev(0x054c, 0x0ce6, name="DualSense Wireless Controller"),
                _dev(0x28de, 0x1205, name="Steam Deck Controller")]
        r = self._get({"scope": "global"}, devs=devs)
        self.assertEqual(r["connected_families"], ["DualSense", "Steam Deck"])

    def test_unclassifiable_pad_is_skipped(self):
        devs = [_dev(0x1234, 0x5678, name="Unknown Pad")]
        r = self._get({"scope": "global"}, devs=devs)
        self.assertEqual(r["connected_families"], [])


class RaControllersScopes(unittest.TestCase):
    """racontrollers.scopes: the union of priority.list's configured +
    available systems/collections, each with an effective p1 (priority.get's
    order composition, so a scope with NO `ports` rule still gets a real
    family instead of "(empty)")."""

    def _scopes(self, merged, sysxml, gamelist=(), standalone=(), collections=()):
        def has_gamelist(s):
            return s in gamelist

        def visible_records(s):
            # A gamelist-backed system has visible games (the scopes predicate now
            # requires both, dropping emptied stubs); derive from the same set so
            # the test never reads the real ES-DE tree for the games check.
            return {"g": {}} if s in gamelist else {}

        def default_command(s, systems=None):
            return f"cmd:{s}"

        def is_standalone(cmd):
            return cmd.split(":", 1)[1] in standalone

        with mock.patch.object(bc, "load_merged", return_value=merged), \
             mock.patch.object(bc.es_systems, "load_systems", return_value=sysxml), \
             mock.patch.object(bc.es_systems, "_has_gamelist", side_effect=has_gamelist), \
             mock.patch.object(bc.es_gamelist, "visible_records", side_effect=visible_records), \
             mock.patch.object(bc.es_systems, "default_command", side_effect=default_command), \
             mock.patch.object(bc.es_systems, "is_standalone", side_effect=is_standalone), \
             mock.patch.object(bc.es_collections, "enabled_collections",
                               return_value=tuple(collections)), \
             mock.patch.object(bc, "console_art",
                               side_effect=lambda name: f"/art/{name}.png"), \
             mock.patch.object(bc, "resolve_art", return_value="/art/fallback.png"):
            return bc._racontrollers_scopes({})

    def test_documented_shape_matches_priority_list_tile_fields(self):
        merged = {"systems": {}, "collections": {}}
        r = self._scopes(merged, {"nes": []}, gamelist={"nes"})
        self.assertEqual(set(r.keys()), {"systems", "collections"})
        self.assertEqual(set(r["systems"][0].keys()), {"name", "p1", "art"})

    def test_includes_system_with_no_ports_rule(self):
        # "psx" has NO [systems.psx] entry at all — priority.list's own
        # "configured" bucket would drop it; scopes must still list it, with a
        # real family (not "(empty)") as p1.
        merged = {"systems": {}, "collections": {}}
        r = self._scopes(merged, {"psx": []}, gamelist={"psx"})
        self.assertEqual([s["name"] for s in r["systems"]], ["psx"])
        self.assertEqual(r["systems"][0]["p1"], "8BitDo")   # KNOWN_FAMILIES[0]
        self.assertEqual(r["systems"][0]["art"], "/art/psx.png")

    def test_configured_system_p1_uses_its_own_order(self):
        merged = {"systems": {"nes": {"ports": [["Xbox", "8BitDo"]]}},
                  "collections": {}}
        r = self._scopes(merged, {"nes": []}, gamelist={"nes"})
        self.assertEqual(r["systems"][0]["p1"], "Xbox")

    def test_excludes_standalone_and_no_gamelist_systems(self):
        merged = {"systems": {}, "collections": {}}
        r = self._scopes(merged, {"nes": [], "switch": [], "unplayed": []},
                         gamelist={"nes", "switch"}, standalone={"switch"})
        # switch is standalone (excluded), unplayed has no gamelist (excluded)
        self.assertEqual([s["name"] for s in r["systems"]], ["nes"])

    def test_systems_sorted_alphabetically(self):
        merged = {"systems": {}, "collections": {}}
        r = self._scopes(merged, {"snes": [], "genesis": [], "arcade": []},
                         gamelist={"snes", "genesis", "arcade"})
        self.assertEqual([s["name"] for s in r["systems"]],
                         ["arcade", "genesis", "snes"])

    def test_collections_union_configured_and_available(self):
        merged = {"systems": {},
                  "collections": {"favorites": {"ports": [["DualSense"]]}}}
        r = self._scopes(merged, {}, collections=["favorites", "lightgun-picks"])
        names = [c["name"] for c in r["collections"]]
        self.assertEqual(names, ["favorites", "lightgun-picks"])
        self.assertEqual(set(r["collections"][0].keys()),
                         {"name", "p1", "art", "lightgun"})  # lightgun -> [lightgun] tile marker
        self.assertEqual(r["collections"][0]["p1"], "DualSense")
        # unconfigured collection ("lightgun-picks") still gets an effective p1
        self.assertEqual(r["collections"][1]["p1"], "8BitDo")

    def test_lightgun_collection_gets_console_art_when_available(self):
        merged = {"systems": {},
                  "collections": {"lightgun": {"require_sinden": True}}}
        r = self._scopes(merged, {}, collections=["lightgun"])
        # console_art() is mocked to always resolve, so it wins over the gun
        # fallback — proves the "or" chain still prefers console_art first.
        self.assertEqual(r["collections"][0]["art"], "/art/lightgun.png")


if __name__ == "__main__":
    unittest.main()
