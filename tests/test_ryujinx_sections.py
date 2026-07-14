"""Structural tests for the Ryujinx (Switch, Ryubing) grouped section tree.

Ryujinx's config menu is the canonical Switch-emu nested tree (memory switch-emu-menu-scheme),
built by standalones_cmds._ryujinx_sections via the same kind:"group" sub-chooser pattern Citron
uses. These lock in:
  * the five top-level rows, in order,
  * each group's leaf pages, in order, with their (kind, arg),
  * that Audio opens directly and Per-game is the game-first media-browser menu,
  * the per-game sub-tree (System{System,CPU} / Video{Graphics,Adv} / Audio / Add-Ons / Cheats),
  * NO page lost onboarding from the old flat tile (the input/pads pages stay reachable; the single
    flat Settings page is INTENTIONALLY split into the granular ryujinx_* pages) -- guards memory
    restructure-preserve-existing-pages,
  * every settings/per-game arg maps to a registered method (tree <-> backend, no orphan rows).

Run:  python3 -m unittest tests.test_ryujinx_sections -v
"""
from __future__ import annotations

import unittest

# Import the modules that register the tree's methods so rpc._METHODS is populated (the real
# backend imports them all; this mirrors that so the orphan-leaf check sees every page).
from lib.madsrv import (  # noqa: F401
    ryujinx_addons_cmds, ryujinx_cheats_cmds, ryujinx_dock_cmds, ryujinx_hotkeys_cmds,
    ryujinx_pergame, ryujinx_settings, standalones_cmds)
from lib.madsrv import rpc


def _sections():
    return standalones_cmds._sections_for(standalones_cmds._EMUS["ryujinx"])


def _leaf_pairs(rows):
    return [(r["label"], r["kind"], r.get("arg")) for r in rows]


class TopLevel(unittest.TestCase):
    def test_five_top_level_rows_in_order(self):
        labels = [r["label"] for r in _sections()]
        self.assertEqual(labels, ["System", "Video", "Audio", "Input", "Per-game"])

    def test_system_video_input_are_groups(self):
        by = {r["label"]: r for r in _sections()}
        for name in ("System", "Video", "Input"):
            self.assertEqual(by[name]["kind"], "group", f"{name} should be a group row")
            self.assertTrue(by[name].get("sections"), f"{name} group must have sub-rows")

    def test_audio_opens_directly(self):
        by = {r["label"]: r for r in _sections()}
        self.assertEqual(by["Audio"]["kind"], "settings")
        self.assertEqual(by["Audio"]["arg"], "ryujinx_audio")

    def test_pergame_is_media_browser_menu(self):
        by = {r["label"]: r for r in _sections()}
        self.assertEqual(by["Per-game"]["kind"], "settings_pergame_menu")
        self.assertEqual(by["Per-game"]["arg"], "ryujinx")
        self.assertTrue(by["Per-game"].get("sections"))     # game-first leaves for the picker


class Groups(unittest.TestCase):
    def setUp(self):
        self.by = {r["label"]: r for r in _sections()}

    def test_system_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [
                ("System", "settings", "ryujinx_system"),
                ("CPU", "settings", "ryujinx_cpu"),
                ("Dock detection", "settings", "ryujinx_dock"),
            ],
        )

    def test_video_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [
                ("Graphics", "settings", "ryujinx_gfx"),
                ("Adv. Graphics", "settings", "ryujinx_gfxadv"),
            ],
        )

    def test_input_group_leaves(self):
        self.assertEqual(
            _leaf_pairs(self.by["Input"]["sections"]),
            [
                ("Controllers", "pads_map", "ryujinx"),
                ("Input mapping", "input_map", "ryujinx"),
                ("Hotkeys", "settings", "ryujinx_hk"),
            ],
        )


class Pergame(unittest.TestCase):
    # The per-game sub-menu (pick a game -> these rows, with the picked titleid injected by the
    # browser). Same grouping as the top level; single-page rows open directly.
    def setUp(self):
        self.rows = standalones_cmds._ryujinx_pergame_row("Ryujinx")["sections"]
        self.by = {r["label"]: r for r in self.rows}

    def test_five_rows_in_order(self):
        # No per-game Input row (removed): device -> player is owned by global pads -> players.
        self.assertEqual([r["label"] for r in self.rows],
                         ["System", "Video", "Audio", "Add-Ons", "Cheats"])

    def test_system_group_leaves(self):
        self.assertEqual(self.by["System"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["System"]["sections"]),
            [("System", "pergame_settings", "ryujinx_pg_system"),
             ("CPU", "pergame_settings", "ryujinx_pg_cpu")],
        )

    def test_video_group_leaves(self):
        self.assertEqual(self.by["Video"]["kind"], "group")
        self.assertEqual(
            _leaf_pairs(self.by["Video"]["sections"]),
            [("Graphics", "pergame_settings", "ryujinx_pg_gfx"),
             ("Adv. Graphics", "pergame_settings", "ryujinx_pg_gfxadv")],
        )

    def test_singles_open_directly(self):
        for name, arg in (("Audio", "ryujinx_pg_audio"),
                          ("Add-Ons", "ryujinx_addons"), ("Cheats", "ryujinx_cheats")):
            self.assertEqual(self.by[name]["kind"], "pergame_settings", name)
            self.assertEqual(self.by[name]["arg"], arg, name)

    def test_no_per_game_input_row(self):
        # Per-game Input was removed: a Ryujinx profile is a device+mapping pin that MAD's bake +
        # launch router did not honor cleanly; device -> player is owned by global pads -> players.
        self.assertNotIn("Input", self.by)


class NoOrphansAndNoPageLost(unittest.TestCase):
    # The input/pads/per-game pages the OLD flat Ryujinx tile produced must stay reachable (the old
    # single settings page is INTENTIONALLY split -> asserted via granular coverage).
    STILL_REACHABLE = {
        ("input_map", "ryujinx"),
        ("pads_map", "ryujinx"),
        ("settings_pergame_menu", "ryujinx"),
    }

    def _top_reachable(self):
        reachable = set()
        for r in _sections():
            reachable.add((r["kind"], r.get("arg")))
            if r["kind"] == "group":
                for sub in r["sections"]:
                    reachable.add((sub["kind"], sub.get("arg")))
        return reachable

    def test_former_pages_reachable(self):
        self.assertFalse(self.STILL_REACHABLE - self._top_reachable())

    def test_granular_settings_pages_reachable(self):
        reachable = self._top_reachable()
        for ns in ryujinx_settings.PAGES:
            self.assertIn(("settings", ns), reachable, ns)

    def test_no_orphan_leaves(self):
        # Every ryujinx-namespaced settings/pergame leaf must be a registered method (a typo'd arg
        # would ship a dead row that errors on open).
        def check(kind_arg_pairs):
            for kind, arg in kind_arg_pairs:
                if kind in ("settings", "pergame_settings") and (arg or "").startswith("ryujinx"):
                    self.assertIn(f"{arg}.get", rpc._METHODS, arg)

        check(self._top_reachable())
        # per-game leaves
        pg = set()
        for r in standalones_cmds._ryujinx_pergame_row("Ryujinx")["sections"]:
            subs = r["sections"] if r["kind"] == "group" else [r]
            for sub in subs:
                pg.add((sub["kind"], sub.get("arg")))
        check(pg)


if __name__ == "__main__":
    unittest.main()
