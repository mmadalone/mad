"""rom_folder.entries()/skipped() -- the live top-level "what's actually in this ROM
folder right now" read, independent of ES-DE's gamelist and any emulator cache.

Covers: extension filtering; case-insensitive matching; top-level-only (no recursion);
"dir as file" folder layout; non-matching files excluded; a ':' stem skipped and
discoverable; an empty extension list short-circuits to {}; a missing directory returns
{} without raising; and that stem keys agree with what es_gamelist.py would key the same
filename under (Path(path).stem, lowercased).

Run:  python3 -m unittest tests.test_rom_folder -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import rom_folder


class Entries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rom_root = self.tmp / "ROMs"
        self.rom_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entries(self, system: str, exts):
        d = self.rom_root / system
        d.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(rom_folder.es_collections, "rom_root", lambda: self.rom_root), \
             mock.patch.object(rom_folder.es_systems, "extensions", lambda s: exts):
            return rom_folder.entries(system)

    def _skipped(self, system: str, exts):
        d = self.rom_root / system
        d.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(rom_folder.es_collections, "rom_root", lambda: self.rom_root), \
             mock.patch.object(rom_folder.es_systems, "extensions", lambda s: exts):
            return rom_folder.skipped(system)

    def test_rom_dir_reuses_es_collections_rom_root(self):
        with mock.patch.object(rom_folder.es_collections, "rom_root", lambda: self.rom_root):
            self.assertEqual(rom_folder.rom_dir("ps2"), self.rom_root / "ps2")

    def test_extension_filtering(self):
        d = self.rom_root / "ps2"
        d.mkdir()
        (d / "Game1.iso").write_text("x")
        (d / "Game2.chd").write_text("x")
        (d / "notes.txt").write_text("x")               # not a rom extension -- excluded
        got = self._entries("ps2", (".iso", ".chd"))
        self.assertEqual(set(got), {"game1", "game2"})
        self.assertNotIn("notes", got)

    def test_case_insensitive_matching(self):
        d = self.rom_root / "ps2"
        d.mkdir()
        (d / "Game.ISO").write_text("x")                # upper-case suffix on disk
        got = self._entries("ps2", (".iso",))            # lower-case in the extension list
        self.assertIn("game", got)
        self.assertEqual(got["game"]["kind"], "file")

    def test_top_level_only_no_recursion(self):
        d = self.rom_root / "ps2"
        (d / "subcore").mkdir(parents=True)
        (d / "subcore" / "Hidden.iso").write_text("x")   # a per-core subfolder rom -- must not be listed
        (d / "Visible.iso").write_text("x")
        got = self._entries("ps2", (".iso",))
        self.assertEqual(set(got), {"visible"})

    def test_directory_as_file_layout(self):
        d = self.rom_root / "ps3"
        (d / "Asura's Wrath [BLUS30721].ps3").mkdir(parents=True)   # ES-DE's dir-as-game layout
        got = self._entries("ps3", (".ps3",))
        self.assertIn("asura's wrath [blus30721]", got)
        row = got["asura's wrath [blus30721]"]
        self.assertEqual(row["kind"], "folder")
        self.assertEqual(row["stem"], "Asura's Wrath [BLUS30721]")

    def test_folder_name_with_unrelated_dots_not_over_truncated(self):
        d = self.rom_root / "ps3"
        (d / "Game v1.2.ps3").mkdir(parents=True)
        got = self._entries("ps3", (".ps3",))
        row = got["game v1.2"]
        self.assertEqual(row["stem"], "Game v1.2")       # only the trailing .ps3 stripped

    def test_nonmatching_file_excluded(self):
        d = self.rom_root / "ps2"
        d.mkdir()
        (d / "metadata.txt").write_text("x")
        got = self._entries("ps2", (".iso", ".chd"))
        self.assertEqual(got, {})

    def test_colon_stem_skipped_and_discoverable(self):
        d = self.rom_root / "arcade"
        d.mkdir()
        (d / "Ghosts'n Goblins: Reloaded.zip").write_text("x")
        (d / "Normal Game.zip").write_text("x")
        got = self._entries("arcade", (".zip",))
        self.assertNotIn("ghosts'n goblins: reloaded", got)
        self.assertIn("normal game", got)
        skip = self._skipped("arcade", (".zip",))
        self.assertIn("Ghosts'n Goblins: Reloaded", skip)

    def test_empty_extension_list_returns_empty_dict(self):
        d = self.rom_root / "unknownsys"
        d.mkdir()
        (d / "Game.iso").write_text("x")
        got = self._entries("unknownsys", ())            # folder truth disabled for this system
        self.assertEqual(got, {})

    def test_missing_directory_returns_empty_dict_no_raise(self):
        with mock.patch.object(rom_folder.es_collections, "rom_root", lambda: self.rom_root), \
             mock.patch.object(rom_folder.es_systems, "extensions", lambda s: (".iso",)):
            got = rom_folder.entries("does-not-exist")    # never even mkdir'd
        self.assertEqual(got, {})

    def test_stem_keys_match_es_gamelist_convention(self):
        # es_gamelist.titles()/records() key by Path(<gamelist path>).stem, lowercased.
        # Prove the two would agree on the same filename.
        d = self.rom_root / "ps2"
        d.mkdir()
        (d / "X-Men vs. Street Fighter.iso").write_text("x")
        got = self._entries("ps2", (".iso",))
        expected_key = Path("X-Men vs. Street Fighter.iso").stem.lower()
        self.assertIn(expected_key, got)
        self.assertEqual(got[expected_key]["stem"], "X-Men vs. Street Fighter")

    def test_collision_first_wins_by_sorted_filename_order(self):
        d = self.rom_root / "ps2"
        d.mkdir()
        # Two names that collide on stem_lower once lowercased; sorted() orders "Game.iso"
        # before "game.iso" (uppercase 'G' < lowercase 'g' in ASCII), so "Game.iso" must win.
        (d / "game.iso").write_text("x")
        (d / "Game.iso").write_text("x")
        got = self._entries("ps2", (".iso",))
        self.assertEqual(len(got), 1)
        self.assertEqual(got["game"]["stem"], "Game")

    def test_absolute_path_string(self):
        d = self.rom_root / "ps2"
        d.mkdir()
        (d / "Game.iso").write_text("x")
        got = self._entries("ps2", (".iso",))
        self.assertTrue(Path(got["game"]["path"]).is_absolute())
        self.assertEqual(got["game"]["path"], str(d / "Game.iso"))


if __name__ == "__main__":
    unittest.main()
