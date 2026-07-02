"""Tests for racontrollers.get (lib.madsrv.backends_cmds) — the RetroArch hub's
new Controllers section editor. Mirrors priority.get's order/nports/
require_sinden composition (global scope reads [defaults] instead of a
systems/collections entry), adds the two global X-Arcade warn toggles, and
reports which controller families are currently connected. Pure logic — no
real SDL/evdev: devices/policy/merged are monkeypatched via mock.patch.object
on backends_cmds (it does `from ..policy import load_merged` etc. at module
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

    def test_global_warn_toggles_present_with_bool_values(self):
        r = self._get({"scope": "global"})
        toggles = {t["key"]: t for t in r["toggles"]}
        self.assertIn("warn_when_no_xarcade", toggles)
        self.assertIn("warn_when_only_xarcade", toggles)
        for t in r["toggles"]:
            self.assertIsInstance(t["value"], bool)
            self.assertIn("label", t)
        # value reflects [defaults] override / documented default-ON
        self.assertIs(toggles["warn_when_no_xarcade"]["value"], False)
        self.assertIs(toggles["warn_when_only_xarcade"]["value"], True)

    def test_default_on_when_defaults_missing_key(self):
        merged = {"defaults": {}, "systems": {}, "collections": {}}
        r = self._get({"scope": "global"}, merged=merged)
        toggles = {t["key"]: t["value"] for t in r["toggles"]}
        self.assertTrue(toggles["warn_when_no_xarcade"])
        self.assertTrue(toggles["warn_when_only_xarcade"])

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


if __name__ == "__main__":
    unittest.main()
