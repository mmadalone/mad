"""Per-game steam asset groups (game_files._steam_game_assets + resolve_game_assets).

Pins: the group set for a Proton shortcut (saves subset / full prefix / external game
dir), pfx.lock exclusion via child-enumeration, the Lutris note-only shape, the
heavy-key gate (no prefix/gamedir resolve unless asked - the per-system "All" path),
and the launcher relabel on the generic ROM group.

Run: python3 -m unittest tests.test_steam_game_assets -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import game_files, steam_shortcuts as ss

APPID = 4108777888


def _tree(tmp: Path):
    """A realistic compatdata/<appid> + an external game dir under a fake $HOME."""
    home = tmp / "home"
    cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
    (cd / "pfx/drive_c/users/steamuser/Documents").mkdir(parents=True)
    (cd / "pfx/drive_c/users/steamuser/Documents/save.dat").write_text("save")
    (cd / "pfx/drive_c/users/steamuser/AppData").mkdir(parents=True)
    (cd / "config_info").write_text("proton 9")
    (cd / "version").write_text("9")
    (cd / "pfx.lock").write_text("")                    # must NEVER be backed up
    gd = home / "games" / "OutRun2006"
    (gd / "data").mkdir(parents=True)
    (gd / "OR2006.exe").write_text("exe")
    return home, cd, gd


def _patched(home, cd, gd, lutris=False, games=None):
    games = games if games is not None else {
        "Punisher": {"appid": APPID, "rgid": 1, "alive": True,
                     "name": "The Punisher", "sh": "/x/Punisher.sh"}}
    return (mock.patch.object(ss, "nonsteam_games", return_value=games),
            mock.patch.object(ss, "is_lutris", return_value=lutris),
            mock.patch.object(ss, "compatdata_dir", return_value=cd),
            mock.patch.object(ss, "game_dir", return_value=gd),
            mock.patch.object(ss, "home", return_value=home),
            mock.patch.object(ss, "lutris_game_id", return_value=None))


class SteamGameAssets(unittest.TestCase):
    def _groups(self, home, cd, gd, heavy=True, lutris=False, stem="Punisher"):
        patches = _patched(home, cd, gd, lutris=lutris)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return game_files._steam_game_assets(stem, _size, heavy=heavy)

    def test_proton_game_full_group_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            groups = self._groups(home, cd, gd)
        by = {g["key"]: g for g in groups}
        self.assertEqual(set(by), {"saves", "prefix", "gamedir"})
        # saves: only the PRESENT steamuser homes, rel'd inside the prefix namespace
        self.assertTrue(by["saves"]["present"])
        self.assertEqual(
            sorted(f["rel"] for f in by["saves"]["files"]),
            [f"steam/compatdata/{APPID}/pfx/drive_c/users/steamuser/AppData",
             f"steam/compatdata/{APPID}/pfx/drive_c/users/steamuser/Documents"])
        # prefix: every compatdata child EXCEPT pfx.lock
        rels = sorted(f["rel"] for f in by["prefix"]["files"])
        self.assertEqual(rels, [f"steam/compatdata/{APPID}/config_info",
                                f"steam/compatdata/{APPID}/pfx",
                                f"steam/compatdata/{APPID}/version"])
        self.assertNotIn(f"steam/compatdata/{APPID}/pfx.lock", rels)
        # gamedir: one folder row, $HOME-relative rel
        self.assertEqual(by["gamedir"]["files"][0]["rel"],
                         "steam/gamedir/games/OutRun2006")
        self.assertEqual(by["gamedir"]["files"][0]["kind"], "folder")
        for g in groups:
            self.assertEqual(g["category"], "steam")

    def test_heavy_gate_skips_prefix_and_gamedir(self):
        # The per-system "All" path (rom+media+saves+states): no multi-GB walks.
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            groups = self._groups(home, cd, gd, heavy=False)
        self.assertEqual([g["key"] for g in groups], ["saves"])

    def test_note_only_when_there_is_no_prefix(self):
        # The note stands in for "nothing steam-side to back up", which is decided by the
        # PREFIX being absent - not by the exe name.
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            shutil.rmtree(cd)                           # Lutris game: no compatdata
            groups = self._groups(home, cd, gd, lutris=True)
        (note,) = groups
        self.assertEqual(note["key"], "note")
        self.assertFalse(note["present"])               # grey, untickable
        self.assertIn("Lutris", note["label"])

    def test_a_lutris_shortcut_WITH_a_prefix_still_gets_its_groups(self):
        # THE BUG: short-circuiting on is_lutris hid a real Proton prefix (and its saves)
        # from backup entirely. Some flatpak/Lutris-launched shortcuts do own a prefix.
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            groups = self._groups(home, cd, gd, lutris=True)
        self.assertEqual({g["key"] for g in groups}, {"saves", "prefix", "gamedir"})

    def test_no_prefix_and_not_lutris_says_so_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            shutil.rmtree(cd)
            groups = self._groups(home, cd, gd, lutris=False)
        (note,) = groups
        self.assertEqual(note["key"], "note")
        self.assertIn("No Proton prefix", note["label"])

    def test_steam_proper_stem_gets_nothing_steam_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, gd = _tree(Path(tmp))
            groups = self._groups(home, cd, gd, stem="Huntdown")
        self.assertEqual(groups, [])

    def test_dead_shortcut_prefix_is_still_rescuable(self):
        # The launcher survives, Steam lost the shortcut: saves+prefix still resolve
        # (backup keeps working); only gamedir needs the live shortcut.
        with tempfile.TemporaryDirectory() as tmp:
            home, cd, _gd = _tree(Path(tmp))
            games = {"Punisher": {"appid": APPID, "rgid": 1, "alive": False,
                                  "name": "The Punisher", "sh": "/x/Punisher.sh"}}
            patches = _patched(home, cd, None, games=games)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                groups = game_files._steam_game_assets("Punisher", _size)
        self.assertEqual({g["key"] for g in groups}, {"saves", "prefix"})


class LutrisGameAssets(unittest.TestCase):
    """A Lutris-launched shortcut resolves its LUTRIS wine prefix (saves / full prefix /
    game dir), same tickable shape as the Proton branch, rel'd under the
    steam/lutrisprefix and steam/gamedir namespaces."""

    def _groups(self, home, pfx, gd, heavy=True, shared=0):
        from lib import lutris_games as lg
        games = {"Deadpool": {"appid": APPID, "rgid": 1, "alive": True,
                              "name": "Deadpool", "sh": "/x/Deadpool.sh"}}
        cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)  # absent
        with mock.patch.object(ss, "nonsteam_games", return_value=games), \
             mock.patch.object(ss, "is_lutris", return_value=True), \
             mock.patch.object(ss, "compatdata_dir", return_value=cd), \
             mock.patch.object(ss, "home", return_value=home), \
             mock.patch.object(ss, "lutris_game_id", return_value=117), \
             mock.patch.object(lg, "prefix_for", return_value=pfx), \
             mock.patch.object(lg, "game_dir_for", return_value=gd), \
             mock.patch.object(lg, "config_path", return_value=self._cfg), \
             mock.patch.object(lg, "shared_prefix_count", return_value=shared):
            return game_files._steam_game_assets("Deadpool", _size, heavy=heavy)

    def _lutris_tree(self, tmp: Path):
        home = tmp / "home"
        cfgdir = home / "lutris-data/games"
        cfgdir.mkdir(parents=True)
        self._cfg = cfgdir / "deadpool-1.yml"
        self._cfg.write_text("game:\n  prefix: /x\n")
        pfx = home / "Games" / "tf"
        (pfx / "drive_c/users/deck/Documents").mkdir(parents=True)
        (pfx / "drive_c/users/deck/Documents/save.dat").write_text("s")
        (pfx / "drive_c/users/steamuser/AppData").mkdir(parents=True)
        (pfx / "drive_c/users/Public").mkdir(parents=True)
        gd = home / "games" / "Deadpool"
        (gd / "Binaries").mkdir(parents=True)
        return home, pfx.resolve(), gd.resolve()

    def test_lutris_full_group_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, pfx, gd = self._lutris_tree(Path(tmp))
            groups = self._groups(home, pfx, gd, shared=3)
        by = {g["key"]: g for g in groups}
        self.assertEqual(set(by), {"saves", "lutriscfg", "prefix", "gamedir"})
        # saves: every user home except Public, under the lutrisprefix namespace
        self.assertEqual(
            sorted(f["rel"] for f in by["saves"]["files"]),
            ["steam/lutrisprefix/Games/tf/drive_c/users/deck/Documents",
             "steam/lutrisprefix/Games/tf/drive_c/users/steamuser/AppData"])
        # prefix: ONE folder row = the whole prefix, shared count in the label
        (row,) = by["prefix"]["files"]
        self.assertEqual(row["rel"], "steam/lutrisprefix/Games/tf")
        self.assertEqual(by["prefix"]["label"], "Wine prefix (Lutris)")
        self.assertIn("SHARED with 3", by["prefix"]["detail"])
        # game dir rides the shared steam/gamedir namespace
        self.assertEqual(by["gamedir"]["files"][0]["rel"], "steam/gamedir/games/Deadpool")

    def test_heavy_gate_applies_to_lutris_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, pfx, gd = self._lutris_tree(Path(tmp))
            groups = self._groups(home, pfx, gd, heavy=False)
        # the tiny Lutris config rides the cheap set (it is bytes, not a prefix walk)
        self.assertEqual([g["key"] for g in groups], ["saves", "lutriscfg"])
        by = {g["key"]: g for g in groups}
        self.assertEqual(by["lutriscfg"]["files"][0]["rel"],
                         "steam/lutriscfg/deadpool-1.yml")

    def test_lutris_without_a_live_prefix_degrades_to_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _pfx, gd = self._lutris_tree(Path(tmp))
            groups = self._groups(home, None, gd)
        (note,) = groups
        self.assertEqual(note["key"], "note")
        self.assertIn("Lutris", note["label"])

    def test_a_leftover_proton_prefix_does_not_shadow_the_live_lutris_data(self):
        # Real on this Deck: games tried under Proton before moving to Lutris keep a
        # stale compatdata prefix. The LIVE saves are the Lutris ones; the leftover is
        # offered as its own clearly-labeled group, never as "the" prefix.
        with tempfile.TemporaryDirectory() as tmp:
            home, pfx, gd = self._lutris_tree(Path(tmp))
            cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            (cd / "pfx/drive_c/users/steamuser/Documents").mkdir(parents=True)
            (cd / "version").write_text("9")
            (cd / "pfx.lock").write_text("")
            groups = self._groups(home, pfx, gd)
        by = {g["key"]: g for g in groups}
        self.assertEqual(set(by), {"saves", "lutriscfg", "prefix", "gamedir", "prefix-proton"})
        # saves = the LUTRIS prefix's, not compatdata's
        for f in by["saves"]["files"]:
            self.assertTrue(f["rel"].startswith("steam/lutrisprefix/"), f["rel"])
        # the leftover restores via the normal compatdata namespace, pfx.lock excluded
        rels = [f["rel"] for f in by["prefix-proton"]["files"]]
        self.assertIn(f"steam/compatdata/{APPID}/pfx", rels)
        self.assertNotIn(f"steam/compatdata/{APPID}/pfx.lock", rels)
        self.assertIn("leftover", by["prefix-proton"]["detail"])


class ResolveIntegration(unittest.TestCase):
    def test_rom_group_is_relabelled_for_steam(self):
        # The generic ROM group carries the launcher; steam relabels it so the tick
        # list says what it actually is. (Nonsense stem: group present=False, label
        # still applied.)
        with mock.patch.object(ss, "nonsteam_games", return_value={}):
            groups = game_files.resolve_game_assets("steam", "NoSuchGame__")
        rom = next(g for g in groups if g["key"] == "rom")
        self.assertEqual(rom["label"], "Launcher & gamelist entry")


def _size(path) -> int:
    """Cheap deterministic size stub (the real _path_size walks)."""
    import os
    try:
        if os.path.isdir(path):
            return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) or 1
        return os.path.getsize(path)
    except OSError:
        return 0


if __name__ == "__main__":
    unittest.main()
