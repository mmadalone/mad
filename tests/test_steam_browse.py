"""The Valve Steam tile in the granular Games browse (lib/madsrv/granular_cmds).

Pins: the tile appears only when non-Steam shortcuts exist and counts ONLY them
(never the 80+ launchers of the whole steam system); a dead shortcut renders as
has_rom=false (the browser's existing red missing treatment); the per-system "All"
scope for steam expands to the non-Steam stems with the fixed asset allowlist; and
scope=all stays steam-free (TOOL_SYSTEMS keeps steam out of _game_systems).

Run: python3 -m unittest tests.test_steam_browse -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import steam_shortcuts
from lib.madsrv import granular_cmds as gc


def _two_games(tmp: Path) -> dict:
    alive = tmp / "Punisher.sh"
    alive.write_text("exec steam steam://rungameid/1\n")
    dead = tmp / "Manhunt.sh"
    dead.write_text("exec steam steam://rungameid/2\n")
    return {"Punisher": {"appid": 4108, "rgid": 1, "alive": True,
                         "name": "The Punisher", "sh": str(alive)},
            "Manhunt": {"appid": 4109, "rgid": 2, "alive": False,
                        "name": "Manhunt", "sh": str(dead)}}


class SteamTile(unittest.TestCase):
    def _systems(self, games: dict) -> list:
        with mock.patch.object(gc, "_game_systems", return_value=[]), \
             mock.patch.object(steam_shortcuts, "nonsteam_games", return_value=games):
            return gc._live_roms_systems()

    def test_tile_counts_only_nonsteam_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._systems(_two_games(Path(tmp)))
        self.assertEqual([r["key"] for r in rows], ["steam"])
        self.assertEqual(rows[0]["count"], 2)                # 2 shortcuts, not 82 launchers
        self.assertEqual(rows[0]["label"], "Valve Steam")    # es_systems.short_name("steam")

    def test_no_shortcuts_no_tile(self):
        self.assertEqual(self._systems({}), [])

    def test_a_shortcuts_error_never_breaks_the_browse(self):
        with mock.patch.object(gc, "_game_systems", return_value=[]), \
             mock.patch.object(steam_shortcuts, "nonsteam_games",
                               side_effect=OSError("vdf unreadable")):
            self.assertEqual(gc._live_roms_systems(), [])


class SteamItems(unittest.TestCase):
    def test_dead_shortcut_renders_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = _two_games(Path(tmp))
            with mock.patch.object(steam_shortcuts, "nonsteam_games", return_value=games), \
                 mock.patch.object(gc.game_files, "cover_path", return_value=None):
                rows = gc._live_roms_items("steam")
        self.assertEqual([r["stem"] for r in rows], ["Manhunt", "Punisher"])  # name-sorted
        by = {r["stem"]: r for r in rows}
        self.assertFalse(by["Manhunt"]["has_rom"])            # dead => red missing treatment
        self.assertTrue(by["Punisher"]["has_rom"])
        self.assertEqual(by["Punisher"]["id"], "steam:Punisher")
        self.assertGreater(by["Punisher"]["size"], 0)         # launcher size only (cheap)


class SteamScope(unittest.TestCase):
    def test_steam_system_all_expands_to_nonsteam_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = _two_games(Path(tmp))
            with mock.patch.object(steam_shortcuts, "nonsteam_games", return_value=games):
                got = gc._games_for_scope("system", "steam")
        self.assertEqual([g["stem"] for g in got], ["Manhunt", "Punisher"])
        for g in got:
            self.assertEqual(g["system"], "steam")
            # the fixed allowlist: launcher+media+saves(+states, absent for steam) -
            # NEVER the heavy prefix/gamedir keys, so "All" cannot balloon.
            self.assertEqual(g["keys"], list(gc._ALL_ASSET_KEYS))

    def test_scope_all_includes_the_nonsteam_games(self):
        # scope=all promises EVERY game on this Deck (that is what the confirm says, and
        # the Valve Steam tile sits in the same grid), so the shortcut games ride along
        # with the fixed allowlist - launcher+media+saves, never the heavy prefix keys.
        with tempfile.TemporaryDirectory() as tmp:
            games = _two_games(Path(tmp))
            with mock.patch.object(gc, "_game_systems", return_value=["snes"]), \
                 mock.patch.object(gc.es_gamelist, "visible_records",
                                   return_value={"mario": {"stem": "Mario"}}), \
                 mock.patch.object(steam_shortcuts, "nonsteam_games", return_value=games):
                got = gc._games_for_scope("all", None)
        self.assertEqual([(g["system"], g["stem"]) for g in got],
                         [("snes", "Mario"), ("steam", "Manhunt"), ("steam", "Punisher")])
        for g in got:
            self.assertEqual(g["keys"], list(gc._ALL_ASSET_KEYS))
            self.assertNotIn("prefix", g["keys"])

    def test_scope_all_survives_a_steam_side_error(self):
        with mock.patch.object(gc, "_game_systems", return_value=["snes"]), \
             mock.patch.object(gc.es_gamelist, "visible_records",
                               return_value={"mario": {"stem": "Mario"}}), \
             mock.patch.object(steam_shortcuts, "nonsteam_games",
                               side_effect=OSError("vdf unreadable")):
            got = gc._games_for_scope("all", None)
        self.assertEqual([(g["system"], g["stem"]) for g in got], [("snes", "Mario")])


if __name__ == "__main__":
    unittest.main()
