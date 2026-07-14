"""Structural tests for the Dolphin ("Wii / GameCube") grouped section tree.

standalones_cmds._dolphin_sections builds the Citron-style layout:

    System (group)  Video (group -> Graphics group -> 4 tabs)  Input (group)  Audio (leaf)

These lock in:
  * the four top-level rows, in order,
  * the 3-level Video -> Graphics -> {General, Enhancements, Hacks, Advanced} nesting,
  * Input -> {GameCube (button mapping), Wii (pads->players + Controller options), Hotkeys},
  * the Wii controller leaves preserved VERBATIM (gamepad/dolphin + the sysflags_wii flag leaf),
  * every settings page reachable (no page lost).

tile_flag_sections is stubbed so the wii "Controller options" leaf is deterministic
regardless of the host's SYSFLAGS.

Run:  python3 -m unittest tests.test_dolphin_sections -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import policy_settings_cmds, standalones_cmds

_WII_FLAG_LEAF = {"label": "Controller options", "sublabel": "DolphinBar / Sinden gun / hands-off",
                  "kind": "settings", "arg": "sysflags_wii", "title": "Wii / GameCube controller options"}
_GC_FLAG_LEAF = {"label": "Warn when only the X-Arcade is present", "sublabel": "",
                 "kind": "toggle", "arg": "sysflags_gc",
                 "key": "warn_when_only_xarcade", "value": True}


def _tile():
    return next(x for x in standalones_cmds.STANDALONES if x["key"] == "dolphin")


def _leaf_pairs(rows):
    return [(r["label"], r["kind"], r.get("arg")) for r in rows]


class DolphinTree(unittest.TestCase):
    def setUp(self):
        self._orig = policy_settings_cmds.tile_flag_sections
        policy_settings_cmds.tile_flag_sections = lambda syss, label: (
            [dict(_WII_FLAG_LEAF)] if "wii" in syss
            else [dict(_GC_FLAG_LEAF)] if "gc" in syss else [])
        self.rows = standalones_cmds._sections_for(_tile(), ["wii", "gc"])
        self.by = {r["label"]: r for r in self.rows}

    def tearDown(self):
        policy_settings_cmds.tile_flag_sections = self._orig

    def test_tile_renamed(self):
        self.assertEqual(_tile()["label"], "Wii / GameCube")
        self.assertNotIn("settings_ns", _tile())          # bespoke tree bypasses the default path

    def test_top_level_rows_in_order(self):
        self.assertEqual([r["label"] for r in self.rows],
                         ["System", "Video", "Input", "Audio", "Per-game"])

    def test_pergame_group(self):
        pg = self.by["Per-game"]
        self.assertEqual(pg["kind"], "group")
        menus = pg["sections"]
        self.assertEqual([(m["label"], m["kind"], m["arg"]) for m in menus], [
            ("GameCube games", "settings_pergame_menu", "dolphinpg_gc"),
            ("Wii games", "settings_pergame_menu", "dolphinpg_wii"),
        ])
        subs = menus[0]["sections"]
        self.assertEqual([s["label"] for s in subs], ["General", "Graphics", "AR codes", "Gecko codes"])
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
        # GameCube = per-button remap + pads->players (profiles) + dock/handheld + gc X-Arcade warning
        self.assertEqual(_leaf_pairs(inp_by["GameCube"]["sections"]), [
            ("Button mapping", "input_map", "dolphin"),
            ("Pads → players", "pads_map", "dolphin_gc"),
            ("Dock / handheld", "settings", "dolphin_gc_dock"),
            ("Warn when only the X-Arcade is present", "toggle", "sysflags_gc"),  # inline switch
        ])
        # Wii = the preserved router leaf + the NEW Classic Controller pads->players + the flag leaf
        self.assertEqual(_leaf_pairs(inp_by["Wii"]["sections"]), [
            ("Wii Remotes → players", "gamepad", "dolphin"),
            ("Classic Controller pads", "pads_map", "dolphin_wii"),
            ("Controller options", "settings", "sysflags_wii"),
        ])
        # Hotkeys = mappable input-map page
        self.assertEqual((inp_by["Hotkeys"]["kind"], inp_by["Hotkeys"]["arg"]),
                         ("input_map", "dolphin_hk"))

    def test_gc_only_user_has_no_wii_flag_leaf(self):
        # A GameCube-only tile (no Wii games) must NOT show the Wii DolphinBar/Sinden page,
        # but SHOULD still show the gc X-Arcade warning.
        rows = standalones_cmds._sections_for(_tile(), ["gc"])
        args = set()

        def gather(rs):
            for r in rs:
                args.add(r.get("arg"))
                if r.get("sections"):
                    gather(r["sections"])

        gather(rows)
        self.assertNotIn("sysflags_wii", args)
        self.assertIn("sysflags_gc", args)

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
            ("input_map", "dolphin"), ("gamepad", "dolphin"),
            ("pads_map", "dolphin_gc"),
            ("settings", "dolphin_gc_dock"),
            ("settings", "sysflags_wii"), ("toggle", "sysflags_gc"),
            # per-game: the two browsers + every per-game leaf
            ("settings_pergame_menu", "dolphinpg_gc"), ("settings_pergame_menu", "dolphinpg_wii"),
            ("pergame_settings", "dolphin_pg_general"),
            ("pergame_settings", "dolphin_pg_gfx_general"), ("pergame_settings", "dolphin_pg_gfx_enh"),
            ("pergame_settings", "dolphin_pg_gfx_hacks"), ("pergame_settings", "dolphin_pg_gfx_adv"),
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
        from lib.madsrv import (dolphin_gc_input_cmds, dolphin_hotkeys_cmds,  # noqa: F401
                                dolphin_settings)
        from lib.madsrv.rpc import _METHODS  # registry
        for ns in dolphin_settings.PAGES:
            self.assertIn(f"{ns}.get", _METHODS, ns)
            self.assertIn(f"{ns}.set", _METHODS, ns)
        for m in ("dolphin.input_get", "dolphin.input_set", "dolphin.input_clear",
                  "dolphin.input_save", "dolphin.input_cancel",
                  "dolphin_hk.input_get", "dolphin_hk.input_set",
                  "dolphin_hk.input_clear", "dolphin_hk.input_save", "dolphin_hk.input_cancel"):
            self.assertIn(m, _METHODS, m)


if __name__ == "__main__":
    unittest.main()
