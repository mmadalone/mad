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
        for nm, ty in (("DualSense 1", "Wii U Pro Controller"),
                       ("WiiU Pro 1", "Wii U Pro Controller"),
                       ("Steamdeck", "Wii U GamePad"),
                       ("DualSense 1 + Steamdeck", "Wii U Pro Controller")):
            (self.d / f"{nm}.xml").write_text(f"<emulated_controller><type>{ty}</type></emulated_controller>")
        for s in range(3):
            (self.d / f"controller{s}.xml").write_text("<emulated_controller/>")   # active slots -> excluded
        self.local: dict = {}
        ci._buf.reset()
        self._patches = [
            mock.patch.object(ci, "load_merged", self._merged),
            mock.patch.object(ci.localpolicy, "load", lambda which: copy.deepcopy(self.local)),
            mock.patch.object(ci.localpolicy, "dump",
                              lambda which, data: self.local.clear() or self.local.update(copy.deepcopy(data))),
            mock.patch.object(ci.policy_settings_cmds, "warn_descriptor", self._warn_desc),
            mock.patch.object(ci.policy_settings_cmds, "_sysflags_set", self._set_warn),
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
            "config_dir": str(bc.get("config_dir", self.d)),
            "seating_enabled": bool(bc.get("seating_enabled", False)),
            "handheld_mirrors_docked": bool(bc.get("handheld_mirrors_docked", False)),
            "profile_map": {"docked": dict(pm.get("docked", {})), "handheld": dict(pm.get("handheld", {}))}}}}

    def _m(self, name):
        return rpc._METHODS[name][0]

    def _pm(self, context):
        return self.local.get("backends", {}).get("cemu", {}).get("profile_map", {}).get(context, {})

    # Hermetic X-Arcade warn: read/write self.local["systems"]["wiiu"] instead of the real policy, so the
    # relocated docked-only "Startup warnings" group is deterministic (warn_* defaults ON).
    def _warn_desc(self, system):
        val = self.local.get("systems", {}).get(system, {}).get("warn_when_only_xarcade", True)
        return {"label": "Warn when only the X-Arcade is present", "system": system,
                "flag": "warn_when_only_xarcade", "value": bool(val)}

    def _set_warn(self, system, params):
        v = str(params.get("value")).strip().lower() in ("1", "true", "yes", "on")
        self.local.setdefault("systems", {}).setdefault(system, {})[params["key"]] = v
        return {"key": params["key"], "value": v}

    # ── list ──────────────────────────────────────────────────────────────────
    def test_get_lists_families_and_profiles(self):
        r = self._m("cemu_input_docked.get")({})
        self.assertEqual([g["title"] for g in r["groups"]],
                         ["Family input", "Docked map", "Profiles folder", "Startup warnings"])
        rows = {row["label"]: row for row in r["groups"][1]["settings"]}
        self.assertIn("DualSense", rows)
        self.assertIn("Steam Deck", rows)
        self.assertNotIn("X-Arcade", rows)   # dead row filtered (family_of returns "Xbox" for the cab)
        # An EXTERNAL family (Controller 2..5, a Pro player slot) is offered ONLY Pro-Controller-type
        # profiles -- a GamePad-type profile there would be an invalid 2nd GamePad.
        ds = rows["DualSense"]["options"]
        self.assertEqual(ds[0], "(leave resting)")
        self.assertIn("DualSense 1", ds)                  # Pro type
        self.assertIn("WiiU Pro 1", ds)                   # Pro type
        self.assertIn("DualSense 1 + Steamdeck", ds)      # Pro type
        self.assertNotIn("Steamdeck", ds)                 # GamePad type -> NOT offered to a player slot
        self.assertFalse(any(o.startswith("controller") for o in ds))   # slot files excluded
        # The "Steam Deck" family IS Controller 1 (the Wii U GamePad): only GamePad-type profiles.
        sd = rows["Steam Deck"]["options"]
        self.assertIn("Steamdeck", sd)
        self.assertNotIn("DualSense 1", sd)

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

    # ── relocated docked-only knobs: config_dir + X-Arcade warn (moved off "Controllers") ─────
    def test_config_dir_persists(self):
        r = self._m("cemu_input_docked.get")({})
        folder = next(g for g in r["groups"] if g["title"] == "Profiles folder")["settings"][0]
        self.assertEqual(folder["key"], "config_dir")
        # options = current dir + the two presets; index 1 = the first preset.
        self._m("cemu_input_docked.set")({"key": "config_dir", "value": "1"})
        self._m("cemu_input_docked.save")({})
        self.assertEqual(self.local["backends"]["cemu"]["config_dir"],
                         "~/.config/Cemu/controllerProfiles")   # CONFIG_PRESETS[("cemu","config_dir")][0]

    def test_warn_persists_via_sysflags(self):
        r = self._m("cemu_input_docked.get")({})
        warn = next(g for g in r["groups"] if g["title"] == "Startup warnings")["settings"][0]
        self.assertEqual(warn["key"], "warn_xarcade")
        self.assertTrue(warn["value"])                          # warn_* defaults ON
        self._m("cemu_input_docked.set")({"key": "warn_xarcade", "value": "0"})
        self._m("cemu_input_docked.save")({})
        # written through _sysflags_set to the [systems.wiiu] flag, NOT the backend table
        self.assertFalse(self.local["systems"]["wiiu"]["warn_when_only_xarcade"])
        self.assertNotIn("warn_xarcade", self.local.get("backends", {}).get("cemu", {}))

    def test_handheld_has_no_docked_only_groups(self):
        r = self._m("cemu_input_handheld.get")({})
        titles = [g["title"] for g in r["groups"]]
        self.assertNotIn("Profiles folder", titles)             # config_dir + warn are docked-only
        self.assertNotIn("Startup warnings", titles)
        fam = next(g for g in r["groups"] if g["title"] == "Family input")["settings"]
        self.assertIn("handheld_mirrors_docked", [s["key"] for s in fam])   # but the mirror toggle is here

    # ── Part 2: handheld "same as docked" ─────────────────────────────────────────
    def test_mirror_toggle_persists(self):
        self._m("cemu_input_handheld.set")({"key": "handheld_mirrors_docked", "value": "1"})
        self._m("cemu_input_handheld.save")({})
        self.assertTrue(self.local["backends"]["cemu"]["handheld_mirrors_docked"])
        self._m("cemu_input_handheld.set")({"key": "handheld_mirrors_docked", "value": "0"})
        self._m("cemu_input_handheld.save")({})
        self.assertNotIn("handheld_mirrors_docked", self.local["backends"]["cemu"])   # off = key removed

    def test_mirror_hint_shows_docked_value(self):
        # docked DualSense -> "DualSense 1"; handheld UNSET + mirror ON shows the fallback target in slot 0
        # (display-only: value stays 0 = unset).
        self.local = {"backends": {"cemu": {"handheld_mirrors_docked": True,
                      "profile_map": {"docked": {"DualSense": "DualSense 1"}, "handheld": {}}}}}
        ci._buf.reset()
        r = self._m("cemu_input_handheld.get")({})
        ds = next(x for x in r["groups"][1]["settings"] if x["label"] == "DualSense")
        self.assertEqual(ds["value"], 0)                        # still unset
        self.assertEqual(ds["options"][0], "(from docked: DualSense 1)")
        # flag OFF -> plain unset label, no hint
        self.local["backends"]["cemu"]["handheld_mirrors_docked"] = False
        ci._buf.reset()
        r2 = self._m("cemu_input_handheld.get")({})
        ds2 = next(x for x in r2["groups"][1]["settings"] if x["label"] == "DualSense")
        self.assertEqual(ds2["options"][0], "(leave resting)")

    # ── "Copy my handheld map here" (docked page only) ───────────────────────
    def _fill(self, value="1"):
        return self._m("cemu_input_docked.set")({"key": "fill_from_handheld", "value": value})

    def _docked_rows(self):
        r = self._m("cemu_input_docked.get")({})
        return {x["label"]: x for g in r["groups"] for x in g["settings"]}

    def _set_handheld(self, mapping):
        self.local.setdefault("backends", {}).setdefault("cemu", {}) \
            .setdefault("profile_map", {})["handheld"] = dict(mapping)
        ci._buf.reset()

    def test_fill_row_only_shows_when_a_handheld_map_exists(self):
        self.assertNotIn("Copy my handheld map here",
                         {x.get("label") for x in self._docked_rows().values()})
        self._set_handheld({"DualSense": "DualSense 1"})
        row = self._docked_rows()["Copy my handheld map here"]
        # An ENUM, not an action: the panel drops an action row with no "rpc", and an action's
        # callback never sets dirty, so a staged fill on this buffered page could not be saved.
        self.assertEqual(row["type"], "enum")
        self.assertEqual(row["value"], 0)
        self.assertEqual(len(row["options"]), 2)

    def test_fill_index_zero_is_a_no_op(self):
        self._set_handheld({"DualSense": "DualSense 1"})
        self._fill(value="0")
        self.assertEqual(self._docked_rows()["DualSense"]["value"], 0)
        self._m("cemu_input_docked.save")({})
        self.assertEqual(self._pm("docked"), {})

    def test_fill_skips_a_type_the_row_itself_would_not_offer(self):
        # "WiiU Pro 1" is Pro-typed: fine for DualSense, INVALID for the Steam Deck row (Controller
        # 1 must be a Wii U GamePad). A looser gate would stage it into a row whose options exclude
        # it -- the row would read unset while _flush's value diff still wrote it.
        self._set_handheld({"Steam Deck": "WiiU Pro 1"})
        self._fill()
        self.assertEqual(self._docked_rows()["Steam Deck"]["value"], 0)
        self._m("cemu_input_docked.save")({})
        self.assertEqual(self._pm("docked"), {})

    def test_fill_copies_unset_families_and_stages_the_change(self):
        self._set_handheld({"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"})
        self._fill()
        rows = self._docked_rows()
        self.assertEqual(rows["DualSense"]["options"][rows["DualSense"]["value"]], "DualSense 1")
        self.assertEqual(rows["Steam Deck"]["options"][rows["Steam Deck"]["value"]], "Steamdeck")
        self.assertEqual(self._pm("docked"), {})            # STAGED, not written yet
        self._m("cemu_input_docked.save")({})
        self.assertEqual(self._pm("docked"),
                         {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"})

    def test_fill_never_overwrites_a_family_already_set(self):
        self.local.setdefault("backends", {}).setdefault("cemu", {}) \
            .setdefault("profile_map", {})["docked"] = {"DualSense": "WiiU Pro 1"}
        self._set_handheld({"DualSense": "DualSense 1"})
        self._fill()
        rows = self._docked_rows()
        self.assertEqual(rows["DualSense"]["options"][rows["DualSense"]["value"]], "WiiU Pro 1")

    def test_fill_skips_a_profile_of_the_wrong_emulated_type(self):
        # "Steamdeck" is a Wii U GamePad profile: valid for the Steam Deck row (Controller 1),
        # never for an external family, which must be a Pro controller.
        self._set_handheld({"DualSense": "Steamdeck"})
        self._fill()
        rows = self._docked_rows()
        self.assertEqual(rows["DualSense"]["value"], 0)     # left unset
        self.assertEqual(self._pm("docked"), {})

    def test_fill_skips_a_profile_that_is_gone_from_disk(self):
        self._set_handheld({"DualSense": "Deleted Profile"})
        self._fill()
        self.assertEqual(self._docked_rows()["DualSense"]["value"], 0)

    def test_fill_is_cancellable_like_any_staged_edit(self):
        self._set_handheld({"DualSense": "DualSense 1"})
        self._fill()
        self._m("cemu_input_docked.cancel")({})
        self.assertEqual(self._docked_rows()["DualSense"]["value"], 0)
        self.assertEqual(self._pm("docked"), {})

    def test_fill_is_not_offered_on_the_handheld_page(self):
        self._set_handheld({"DualSense": "DualSense 1"})
        r = self._m("cemu_input_handheld.get")({})
        labels = {x.get("label") for g in r["groups"] for x in g["settings"]}
        self.assertNotIn("Copy my handheld map here", labels)


if __name__ == "__main__":
    unittest.main()
