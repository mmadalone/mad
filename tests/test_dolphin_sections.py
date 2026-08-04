"""Structural tests for the Dolphin ("Wii / GameCube") grouped section tree.

standalones_cmds._dolphin_sections builds the Citron-style layout:

    System (group)  Video (group -> Graphics group -> 4 tabs)  Audio (leaf)  Input (group)

These lock in:
  * the four top-level rows, in order,
  * the 3-level Video -> Graphics -> {General, Enhancements, Hacks, Advanced} nesting,
  * Input -> {GameCube (pads_map), Wii (remotes + CC order + per-STYLE Player 1-4 seat
    pages + the flag leaf renamed "Lightgun games"), Hotkeys},
  * every settings page reachable (no page lost).

tile_flag_sections is stubbed so the wii flag leaf is deterministic regardless of the
host's SYSFLAGS; _dolphin_sections renames the STUB too (post-processing by arg), which is
exactly the production behavior — the shared tile_flag_sections stays untouched.

Run:  python3 -m unittest tests.test_dolphin_sections -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import policy_settings_cmds, standalones_cmds

_WII_FLAG_LEAF = {"label": "Controller options", "sublabel": "DolphinBar / Sinden gun / hands-off",
                  "kind": "settings", "arg": "sysflags_wii", "title": "Wii / GameCube controller options"}


def _tile():
    return next(x for x in standalones_cmds.STANDALONES if x["key"] == "dolphin")


def _leaf_pairs(rows):
    return [(r["label"], r["kind"], r.get("arg")) for r in rows]


class DolphinTree(unittest.TestCase):
    def setUp(self):
        self._orig = policy_settings_cmds.tile_flag_sections
        # gc no longer emits an inline flag chip here (its X-Arcade warn moved to the
        # Pads-to-players page); only wii's multi-flag "Controller options" page is emitted.
        policy_settings_cmds.tile_flag_sections = lambda syss, label: (
            [dict(_WII_FLAG_LEAF)] if "wii" in syss else [])
        self.rows = standalones_cmds._sections_for(_tile(), ["wii", "gc"])
        self.by = {r["label"]: r for r in self.rows}

    def tearDown(self):
        policy_settings_cmds.tile_flag_sections = self._orig

    def test_tile_renamed(self):
        self.assertEqual(_tile()["label"], "Wii / GameCube")
        self.assertNotIn("settings_ns", _tile())          # bespoke tree bypasses the default path

    def test_top_level_rows_in_order(self):
        self.assertEqual([r["label"] for r in self.rows],
                         ["System", "Video", "Audio", "Input", "Per-game"])

    def test_pergame_group(self):
        pg = self.by["Per-game"]
        self.assertEqual(pg["kind"], "group")
        menus = pg["sections"]
        self.assertEqual([(m["label"], m["kind"], m["arg"]) for m in menus], [
            ("GameCube games", "settings_pergame_menu", "dolphinpg_gc"),
            ("Wii games", "settings_pergame_menu", "dolphinpg_wii"),
        ])
        subs = menus[0]["sections"]
        self.assertEqual([s["label"] for s in subs],
                         ["General", "Graphics", "Input profiles", "AR codes", "Gecko codes"])
        prof_gc = next(s for s in subs if s["label"] == "Input profiles")
        self.assertEqual((prof_gc["kind"], prof_gc["arg"]),
                         ("pergame_settings", "dolphin_gc_pg_profiles"))
        prof_wii = next(s for s in menus[1]["sections"] if s["label"] == "Input profiles")
        self.assertEqual((prof_wii["kind"], prof_wii["arg"]),
                         ("pergame_settings", "dolphin_wii_pg_profiles"))
        ar = next(s for s in subs if s["label"] == "AR codes")
        self.assertEqual((ar["kind"], ar["arg"], ar.get("key")),
                         ("pergame_settings", "dolphin_ar", "dolphin_ar"))     # `key` drives the hide
        gfx = next(s for s in subs if s["label"] == "Graphics")
        self.assertEqual([t["arg"] for t in gfx["sections"]],
                         ["dolphin_pg_gfx_general", "dolphin_pg_gfx_enh",
                          "dolphin_pg_gfx_hacks", "dolphin_pg_gfx_adv"])

    def test_system_group_leaves(self):
        self.assertEqual(self.by["System"]["kind"], "group")
        self.assertEqual(_leaf_pairs(self.by["System"]["sections"]), [
            ("General", "settings", "dolphin_general"),
            ("GameCube", "settings", "dolphin_gc"),
            ("Wii", "settings", "dolphin_wii"),
            ("Advanced", "settings", "dolphin_advanced"),
        ])

    def test_video_collapsed_to_four_tabs(self):
        # Video's single "Graphics" child is collapsed away (standing rule): Video opens the
        # four tabs directly, no redundant intermediate submenu.
        video = self.by["Video"]
        self.assertEqual(video["kind"], "group")
        self.assertEqual(_leaf_pairs(video["sections"]), [
            ("General", "settings", "dolphin_gfx_general"),
            ("Enhancements", "settings", "dolphin_gfx_enh"),
            ("Hacks", "settings", "dolphin_gfx_hacks"),
            ("Advanced", "settings", "dolphin_gfx_adv"),
        ])

    def test_input_group_gamecube_and_wii(self):
        inp = self.by["Input"]
        self.assertEqual(inp["kind"], "group")
        inp_by = {r["label"]: r for r in inp["sections"]}
        self.assertEqual([r["label"] for r in inp["sections"]], ["GameCube", "Wii", "Hotkeys"])
        # GameCube = ONE leaf now (the editors were phased out 2026-08-04; Dock/handheld moved
        # to On-the-go -> GameCube -> Settings), so the group COLLAPSES: the "GameCube" row
        # opens Pads-to-players directly (standing rule mad-collapse-single-child-groups).
        self.assertEqual((inp_by["GameCube"]["kind"], inp_by["GameCube"]["arg"]),
                         ("pads_map", "dolphin_gc"))
        # Wii = router leaf + CC order + the per-STYLE Player 1-4 seat pages + the flag
        # leaf renamed "Lightgun games" (arg stays sysflags_wii: same DolphinBar/Sinden/
        # hands-off content, post-processed here so shared code keeps its generic label).
        self.assertEqual(_leaf_pairs(inp_by["Wii"]["sections"]), [
            ("Wii Remotes to players", "gamepad", "dolphin"),
            ("Classic controller order", "pads_map", "dolphin_wii"),
            ("Sideways games", "settings", "dolphin_wii_dock_sideways"),
            ("Nunchuk games", "settings", "dolphin_wii_dock_nunchuk"),
            ("Lightgun games", "settings", "sysflags_wii"),
        ])
        # Hotkeys = mappable input-map page
        self.assertEqual((inp_by["Hotkeys"]["kind"], inp_by["Hotkeys"]["arg"]),
                         ("input_map", "dolphin_hk"))

    def test_gc_only_user_has_no_wii_flag_leaf(self):
        # A GameCube-only tile (no Wii games) must NOT show the Wii DolphinBar/Sinden page.
        # The gc X-Arcade warn is also absent from the section tree now (it moved to the
        # Pads-to-players page), so neither sysflags_ leaf appears here.
        rows = standalones_cmds._sections_for(_tile(), ["gc"])
        args = set()

        def gather(rs):
            for r in rs:
                args.add(r.get("arg"))
                if r.get("sections"):
                    gather(r["sections"])

        gather(rows)
        self.assertNotIn("sysflags_wii", args)
        self.assertNotIn("sysflags_gc", args)

    def test_audio_leaf(self):
        self.assertEqual((self.by["Audio"]["kind"], self.by["Audio"]["arg"]),
                         ("settings", "dolphin_audio"))

    def test_all_settings_pages_reachable(self):
        want = {
            ("settings", "dolphin_general"), ("settings", "dolphin_gc"),
            ("settings", "dolphin_wii"), ("settings", "dolphin_advanced"),
            ("settings", "dolphin_gfx_general"), ("settings", "dolphin_gfx_enh"),
            ("settings", "dolphin_gfx_hacks"), ("settings", "dolphin_gfx_adv"),
            ("settings", "dolphin_audio"), ("input_map", "dolphin_hk"),
            ("gamepad", "dolphin"),
            ("pads_map", "dolphin_gc"),
            # The Button-mapping editors, the grab-bag Docked-profiles page and the gc
            # Dock/handheld row are all GONE; the per-STYLE seat pages replace them.
            ("settings", "dolphin_wii_dock_sideways"),
            ("settings", "dolphin_wii_dock_nunchuk"),
            ("settings", "sysflags_wii"),   # gc warn (sysflags_gc) now on the pads page, not here
            # per-game: the two browsers + every per-game leaf
            ("settings_pergame_menu", "dolphinpg_gc"), ("settings_pergame_menu", "dolphinpg_wii"),
            ("pergame_settings", "dolphin_pg_general"),
            ("pergame_settings", "dolphin_pg_gfx_general"), ("pergame_settings", "dolphin_pg_gfx_enh"),
            ("pergame_settings", "dolphin_pg_gfx_hacks"), ("pergame_settings", "dolphin_pg_gfx_adv"),
            ("pergame_settings", "dolphin_wii_pg_profiles"),
            ("pergame_settings", "dolphin_gc_pg_profiles"),
            ("pergame_settings", "dolphin_ar"), ("pergame_settings", "dolphin_gecko"),
        }
        reachable = set()

        def walk(rows):
            for r in rows:
                reachable.add((r["kind"], r.get("arg")))
                if r.get("sections"):
                    walk(r["sections"])
        walk(self.rows)
        self.assertFalse(want - reachable, f"pages unreachable: {want - reachable}")


class Registration(unittest.TestCase):
    def test_settings_namespaces_registered(self):
        # Import the backend modules (mad-backend does this in production; here we
        # trigger their @method registration explicitly so the test is self-contained).
        from lib.madsrv import (dolphin_hotkeys_cmds, dolphin_profile_cmds,  # noqa: F401
                                dolphin_settings)
        from lib.madsrv.rpc import _METHODS  # registry
        for ns in dolphin_settings.PAGES:
            self.assertIn(f"{ns}.get", _METHODS, ns)
            self.assertIn(f"{ns}.set", _METHODS, ns)
        for m in ("dolphin_hk.input_get", "dolphin_hk.input_set",
                  "dolphin_hk.input_clear", "dolphin_hk.input_save", "dolphin_hk.input_cancel",
                  "dolphin_wii_dock_sideways.get", "dolphin_wii_dock_sideways.set",
                  "dolphin_wii_dock_nunchuk.get", "dolphin_wii_dock_nunchuk.set",
                  "dolphin_wii_hh_classic.get", "dolphin_wii_hh_classic.set",
                  "dolphin_wii_hh_sideways.get", "dolphin_wii_hh_sideways.set",
                  "dolphin_wii_hh_nunchuk.get", "dolphin_wii_hh_nunchuk.set",
                  "dolphin_gc_hh_profiles.get", "dolphin_gc_hh_profiles.set",
                  "dolphin_wii_pg_profiles.get", "dolphin_gc_pg_profiles.get",
                  "dolphin_gc_hh.get", "dolphin_gc_hh.games"):
            self.assertIn(m, _METHODS, m)
        # The phased-out editors + the retired grab-bag pages must not resurface.
        for m in ("dolphin.input_get", "dolphin_wii.input_get",
                  "dolphin_wii_dock.get", "dolphin_wii_dock_hh.get", "dolphin_gc_dock.get"):
            self.assertNotIn(m, _METHODS, m)


if __name__ == "__main__":
    unittest.main()
