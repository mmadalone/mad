"""Structural tests for the Citron (Switch, Yuzu fork) grouped section tree.

Citron's config menu is a NESTED tree (the canonical Switch-emu layout, memory
switch-emu-menu-scheme): five top-level rows

    System (group)  Video (group)  Input (group)  Audio (leaf)  Per-game (menu)

built by standalones_cmds._citron_sections via the same kind:"group" sub-chooser
pattern _pcsx2_sections uses. These tests lock in:
  • the five top-level rows, in order,
  • each group's leaf pages, in order, with their (kind, arg),
  • that Audio opens directly (a plain settings leaf) and Per-game is the game-first
    media+info browser menu,
  • NO page was lost in the flat -> grouped move (every former top-level (kind, arg)
    is still reachable) -- guards memory restructure-preserve-existing-pages.

Run:  python3 -m unittest tests.test_citron_sections -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import standalones_cmds


def _sections():
    # Faithful: dispatch through _sections_for using the real Citron member dict.
    return standalones_cmds._sections_for(standalones_cmds._EMUS["citron"])


def _leaf_pairs(rows):
    """(label, kind, arg) triples for a list of section rows."""
    return [(r["label"], r["kind"], r.get("arg")) for r in rows]


class TopLevel(unittest.TestCase):
    def test_five_top_level_rows_in_order(self):
        labels = [r["label"] for r in _sections()]
        self.assertEqual(labels, ["System", "Video", "Input", "Audio", "Per-game"])

    def test_system_video_input_are_groups(self):
        by = {r["label"]: r for r in _sections()}
        for name in ("System", "Video", "Input"):
            self.assertEqual(by[name]["kind"], "group", f"{name} should be a group row")
            self.assertIsInstance(by[name].get("sections"), list)
            self.assertTrue(by[name]["sections"], f"{name} group must have sub-rows")

    def test_audio_opens_directly(self):
        by = {r["label"]: r for r in _sections()}
        self.assertEqual(by["Audio"]["kind"], "settings")
        self.assertEqual(by["Audio"]["arg"], "citron_audio")

    def test_pergame_is_media_browser_menu(self):
        by = {r["label"]: r for r in _sections()}
        self.assertEqual(by["Per-game"]["kind"], "settings_pergame_menu")
        self.assertEqual(by["Per-game"]["arg"], "citron")
        # its game-first leaves are carried for the picker's on-select sub-menu
        self.assertTrue(by["Per-game"].get("sections"))


class Groups(unittest.TestCase):
    def setUp(self):
        self.by = {r["label"]: r for r in _sections()}

    def test_system_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [
                ("General", "settings", "citron_general"),
                ("CPU", "settings", "citron_cpu"),
                ("System", "settings", "citron_system"),
                ("Dock detection", "settings", "citron_dock"),
            ],
        )

    def test_video_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [
                ("Graphics", "settings", "citron_gfx"),
                ("Graphics (Adv)", "settings", "citron_gfxadv"),
            ],
        )

    def test_input_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Input"]["sections"]),
            [
                ("Controllers", "pads_map", "citron"),
                ("Input mapping", "input_map", "citron"),
                ("Hotkeys", "input_map", "citron_hk"),
            ],
        )


class NoPageLost(unittest.TestCase):
    # Every page that existed as a top-level row in the OLD flat tree must still be
    # reachable (as a group child, or a top-level leaf/menu) in the grouped tree.
    OLD_FLAT = {
        ("settings", "citron_general"),
        ("settings", "citron_system"),
        ("settings", "citron_cpu"),
        ("settings", "citron_gfx"),
        ("settings", "citron_gfxadv"),
        ("settings", "citron_audio"),
        ("input_map", "citron"),
        ("pads_map", "citron"),
        ("input_map", "citron_hk"),
        ("settings", "citron_dock"),
        ("settings_pergame_menu", "citron"),
    }

    def test_all_former_pages_reachable(self):
        reachable = set()
        for r in _sections():
            reachable.add((r["kind"], r.get("arg")))
            if r["kind"] == "group":
                for sub in r["sections"]:
                    reachable.add((sub["kind"], sub.get("arg")))
        missing = self.OLD_FLAT - reachable
        self.assertFalse(missing, f"pages dropped in the reorg: {missing}")


class Pergame(unittest.TestCase):
    # The per-game sub-menu (pick a game -> these rows, with the picked titleid injected by
    # the browser). Same grouping as the top level; single-page rows open directly.
    def setUp(self):
        self.rows = standalones_cmds._citron_pergame_row("Citron")["sections"]
        self.by = {r["label"]: r for r in self.rows}

    def test_six_rows_in_order(self):
        self.assertEqual([r["label"] for r in self.rows],
                         ["System", "Video", "Audio", "Input", "Add-Ons", "Cheats"])

    def test_system_group_leaves(self):
        self.assertEqual(self.by["System"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [
                ("System", "pergame_settings", "citron_pg_system"),
                ("CPU", "pergame_settings", "citron_pg_cpu"),
                ("Linux", "pergame_settings", "citron_pg_linux"),
            ],
        )

    def test_video_group_leaves(self):
        self.assertEqual(self.by["Video"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [
                ("Graphics", "pergame_settings", "citron_pg_gfx"),
                ("Adv. Graphics", "pergame_settings", "citron_pg_gfxadv"),
            ],
        )

    def test_singles_open_directly(self):
        for name, arg in (("Audio", "citron_pg_audio"), ("Input", "citron_pg_input"),
                          ("Add-Ons", "citron_addons"), ("Cheats", "citron_cheats")):
            self.assertEqual(self.by[name]["kind"], "pergame_settings", name)
            self.assertEqual(self.by[name]["arg"], arg, name)

    def test_all_former_pergame_pages_reachable(self):
        old = {
            ("pergame_settings", "citron_addons"),
            ("pergame_settings", "citron_cheats"),
            ("pergame_settings", "citron_pg_system"),
            ("pergame_settings", "citron_pg_cpu"),
            ("pergame_settings", "citron_pg_gfx"),
            ("pergame_settings", "citron_pg_gfxadv"),
            ("pergame_settings", "citron_pg_audio"),
            ("pergame_settings", "citron_pg_input"),
            ("pergame_settings", "citron_pg_linux"),
        }
        reachable = set()
        for r in self.rows:
            if r["kind"] == "group":
                for sub in r["sections"]:
                    reachable.add((sub["kind"], sub.get("arg")))
            else:
                reachable.add((r["kind"], r.get("arg")))
        self.assertFalse(old - reachable, f"pergame pages dropped: {old - reachable}")


if __name__ == "__main__":
    unittest.main()
