"""Cemu (Wii U) family x context input-assignment page (lib/madsrv/cemu_input_cmds.py).

Two buffered "settings" namespaces (cemu_input_docked / cemu_input_handheld). Proves: the page
lists families with the profile options (slot files excluded), an assignment saves the ONE net-changed
family key under [backends.cemu.profile_map.<context>], "(leave resting)" clears it, the seating
master toggle persists, and the two namespaces write disjoint context slices.

Run:  python3 -m unittest tests.test_cemu_input -v
"""
from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib.madsrv import cemu_input_cmds as ci
from lib.madsrv import rpc


class CemuInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        for nm in ("DualSense 1", "WiiU Pro 1", "Steamdeck", "DualSense 1 + Steamdeck"):
            (self.d / f"{nm}.xml").write_text("<emulated_controller/>")
        for s in range(3):
            (self.d / f"controller{s}.xml").write_text("<emulated_controller/>")   # active slots -> excluded
        self.local: dict = {}
        ci._buf.reset()
        self._patches = [
            mock.patch.object(ci, "load_merged", self._merged),
            mock.patch.object(ci.localpolicy, "load", lambda which: copy.deepcopy(self.local)),
            mock.patch.object(ci.localpolicy, "dump",
                              lambda which, data: self.local.clear() or self.local.update(copy.deepcopy(data))),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        ci._buf.reset()
        shutil.rmtree(self.d, ignore_errors=True)

    def _merged(self):
        bc = self.local.get("backends", {}).get("cemu", {})
        pm = bc.get("profile_map", {})
        return {"backends": {"cemu": {
            "config_dir": str(self.d),
            "seating_enabled": bool(bc.get("seating_enabled", False)),
            "profile_map": {"docked": dict(pm.get("docked", {})), "handheld": dict(pm.get("handheld", {}))}}}}

    def _m(self, name):
        return rpc._METHODS[name][0]

    def _pm(self, context):
        return self.local.get("backends", {}).get("cemu", {}).get("profile_map", {}).get(context, {})

    # ── list ──────────────────────────────────────────────────────────────────
    def test_get_lists_families_and_profiles(self):
        r = self._m("cemu_input_docked.get")({})
        self.assertEqual([g["title"] for g in r["groups"]], ["Family input", "Docked map"])
        fam = {row["label"] for row in r["groups"][1]["settings"]}
        self.assertIn("DualSense", fam)
        self.assertIn("Steam Deck", fam)
        self.assertNotIn("X-Arcade", fam)   # dead row filtered (family_of returns "Xbox" for the cab)
        opts = r["groups"][1]["settings"][0]["options"]
        self.assertEqual(opts[0], "(leave resting)")
        self.assertIn("DualSense 1", opts)
        self.assertFalse(any(o.startswith("controller") for o in opts))   # slot files excluded

    # ── assign / save ───────────────────────────────────────────────────────────
    def test_assign_and_save_one_key(self):
        r = self._m("cemu_input_docked.get")({})
        ds = next(x for x in r["groups"][1]["settings"] if x["label"] == "DualSense")
        idx = ds["options"].index("DualSense 1")
        self.assertEqual(self._m("cemu_input_docked.set")({"key": "family:DualSense", "value": str(idx)}),
                         {"dirty": True})
        self._m("cemu_input_docked.save")({})
        self.assertEqual(self._pm("docked"), {"DualSense": "DualSense 1"})   # ONLY the changed family

    def test_seating_toggle_persists(self):
        self._m("cemu_input_docked.set")({"key": "seating_enabled", "value": "1"})
        self._m("cemu_input_docked.save")({})
        self.assertTrue(self.local["backends"]["cemu"]["seating_enabled"])

    def test_unset_clears_key(self):
        self.local = {"backends": {"cemu": {"profile_map": {"docked": {"DualSense": "DualSense 1"}}}}}
        ci._buf.reset()
        r = self._m("cemu_input_docked.get")({})
        ds = next(x for x in r["groups"][1]["settings"] if x["label"] == "DualSense")
        self.assertEqual(ds["options"][ds["value"]], "DualSense 1")          # shows the current assignment
        self._m("cemu_input_docked.set")({"key": "family:DualSense", "value": "0"})   # (leave resting)
        self._m("cemu_input_docked.save")({})
        self.assertNotIn("DualSense", self._pm("docked"))                    # key removed

    def test_namespaces_write_disjoint_slices(self):
        r = self._m("cemu_input_handheld.get")({})
        ds = next(x for x in r["groups"][1]["settings"] if x["label"] == "DualSense")
        idx = ds["options"].index("DualSense 1 + Steamdeck")
        self._m("cemu_input_handheld.set")({"key": "family:DualSense", "value": str(idx)})
        self._m("cemu_input_handheld.save")({})
        self.assertEqual(self._pm("handheld"), {"DualSense": "DualSense 1 + Steamdeck"})
        self.assertEqual(self._pm("docked"), {})                            # docked untouched

    def test_stale_assignment_still_renders(self):
        # a family assigned to a profile whose file was deleted must still show itself, not read as unset
        self.local = {"backends": {"cemu": {"profile_map": {"docked": {"Xbox": "Gone Profile"}}}}}
        ci._buf.reset()
        r = self._m("cemu_input_docked.get")({})
        xb = next(x for x in r["groups"][1]["settings"] if x["label"] == "Xbox")
        self.assertEqual(xb["options"][xb["value"]], "Gone Profile")


if __name__ == "__main__":
    unittest.main()
