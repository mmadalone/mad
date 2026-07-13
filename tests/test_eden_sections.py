"""Structural tests for the Eden (Switch, Yuzu fork) grouped section tree.

Eden's config menu is a NESTED tree (canonical Switch-emu layout, memory switch-emu-menu-scheme):
five top-level rows

    System (group)  Video (group)  Input (group)  Audio (leaf)  Per-game (menu)

built by standalones_cmds._eden_sections. Unlike Citron (already split into per-tab pages before it
was grouped), Eden's reorg is BOTH a split (the old single flat "Settings"/eden page -> seven eden_*
pages, with an added GPU extensions page) AND a grouping. So the no-page-lost guard checks the
FUNCTIONAL surfaces: the leaves relocated VERBATIM from the flat tile (input_map/pads_map/eden_dock/
per-game menu) plus that every granular global page exists.

Run:  python3 -m unittest tests.test_eden_sections -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import standalones_cmds


def _sections():
    # Faithful: dispatch through _sections_for using the real Eden member dict.
    return standalones_cmds._sections_for(standalones_cmds._EMUS["eden"])


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
        self.assertEqual(by["Audio"]["arg"], "eden_audio")

    def test_pergame_is_media_browser_menu(self):
        by = {r["label"]: r for r in _sections()}
        self.assertEqual(by["Per-game"]["kind"], "settings_pergame_menu")
        self.assertEqual(by["Per-game"]["arg"], "eden")
        self.assertTrue(by["Per-game"].get("sections"))


class Groups(unittest.TestCase):
    def setUp(self):
        self.by = {r["label"]: r for r in _sections()}

    def test_system_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [
                ("General", "settings", "eden_general"),
                ("CPU", "settings", "eden_cpu"),
                ("System", "settings", "eden_system"),
                ("Dock detection", "settings", "eden_dock"),
            ],
        )

    def test_video_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [
                ("Graphics", "settings", "eden_gfx"),
                ("Adv. Graphics", "settings", "eden_gfxadv"),
                ("GPU extensions", "settings", "eden_gfxext"),
            ],
        )

    def test_input_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Input"]["sections"]),
            [
                ("Controllers", "pads_map", "eden"),
                ("Input mapping", "input_map", "eden"),
                ("Hotkeys", "input_map", "eden_hk"),
            ],
        )


class NoPageLost(unittest.TestCase):
    # Eden's reorg SPLIT the old single flat "Settings"/eden page into seven granular pages AND
    # grouped everything. Guard: (a) the leaves relocated VERBATIM from the old flat tile (same
    # kind+arg) are still reachable, and (b) every granular global page exists.
    RELOCATED = {
        ("input_map", "eden"),                 # old flat "Input mapping"
        ("pads_map", "eden"),                  # old flat "Controllers"
        ("settings", "eden_dock"),             # old flat "Dock detection"
        ("settings_pergame_menu", "eden"),     # old flat "Per-game settings" -> the browser menu
    }
    GRANULAR = {
        ("settings", "eden_general"), ("settings", "eden_cpu"), ("settings", "eden_system"),
        ("settings", "eden_gfx"), ("settings", "eden_gfxadv"), ("settings", "eden_gfxext"),
        ("settings", "eden_audio"),
    }

    def test_relocated_and_granular_pages_reachable(self):
        reachable = set()
        for r in _sections():
            reachable.add((r["kind"], r.get("arg")))
            if r["kind"] == "group":
                for sub in r["sections"]:
                    reachable.add((sub["kind"], sub.get("arg")))
        missing = (self.RELOCATED | self.GRANULAR) - reachable
        self.assertFalse(missing, f"pages dropped in the reorg: {missing}")


class Pergame(unittest.TestCase):
    # The per-game sub-menu (pick a game -> these rows, with the picked titleid injected by the
    # browser). Same grouping as the top level; single-page rows open directly.
    def setUp(self):
        self.rows = standalones_cmds._eden_pergame_row("Eden")["sections"]
        self.by = {r["label"]: r for r in self.rows}

    def test_six_rows_in_order(self):
        self.assertEqual([r["label"] for r in self.rows],
                         ["System", "Video", "Audio", "Input", "Add-Ons", "Cheats"])

    def test_system_group_leaves(self):
        self.assertEqual(self.by["System"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [
                ("System", "pergame_settings", "eden_pg_system"),
                ("CPU", "pergame_settings", "eden_pg_cpu"),
                ("GameMode", "pergame_settings", "eden_pg_linux"),
            ],
        )

    def test_video_group_leaves(self):
        self.assertEqual(self.by["Video"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [
                ("Graphics", "pergame_settings", "eden_pg_gfx"),
                ("Adv. Graphics", "pergame_settings", "eden_pg_gfxadv"),
                ("GPU extensions", "pergame_settings", "eden_pg_gfxext"),
            ],
        )

    def test_singles_open_directly(self):
        for name, arg in (("Audio", "eden_pg_audio"), ("Input", "eden_pg_input"),
                          ("Add-Ons", "eden_addons"), ("Cheats", "eden_cheats")):
            self.assertEqual(self.by[name]["kind"], "pergame_settings", name)
            self.assertEqual(self.by[name]["arg"], arg, name)


if __name__ == "__main__":
    unittest.main()
