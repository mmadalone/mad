"""es_gamelist <desc> parsing + media_for() glob (RetroArch-hub gameview data).

records()/record() add the game's <desc> to the name; media_for(system, stem) globs
ES-DE's downloaded_media/<system>/<subdir>/<stem>.* for each media kind. Pure: a temp
media tree + a temp gamelist, MAD_MEDIA_ROOT pointed at the temp tree.

Run:  python3 -m unittest tests.test_es_gamelist_media -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import es_gamelist, es_systems

GAMELIST = ('<?xml version="1.0"?>\n<gameList>\n'
            '  <game>\n'
            '    <path>./sonic.md</path>\n'
            '    <name>Sonic the Hedgehog</name>\n'
            '    <desc>A speedy platformer.</desc>\n'
            '  </game>\n'
            '  <game>\n'
            '    <path>./nodesc.md</path>\n'
            '    <name>No Desc Game</name>\n'
            '  </game>\n'
            '</gameList>\n')


class EsGamelistDesc(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._saved = es_systems.GAMELISTS
        es_systems.GAMELISTS = self.dir
        es_gamelist.records.cache_clear()
        d = self.dir / "genesis"
        d.mkdir(parents=True)
        (d / "gamelist.xml").write_text(GAMELIST, encoding="utf-8")

    def tearDown(self):
        es_systems.GAMELISTS = self._saved
        es_gamelist.records.cache_clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_desc_parsed(self):
        r = es_gamelist.record("genesis", "sonic")
        self.assertEqual(r["name"], "Sonic the Hedgehog")
        self.assertEqual(r["desc"], "A speedy platformer.")

    def test_missing_desc_is_empty_string(self):
        r = es_gamelist.record("genesis", "nodesc")
        self.assertEqual(r["name"], "No Desc Game")
        self.assertEqual(r["desc"], "")

    def test_records_case_insensitive_and_full_map(self):
        recs = es_gamelist.records("genesis")
        self.assertEqual(set(recs), {"sonic", "nodesc"})
        self.assertEqual(es_gamelist.record("genesis", "SONIC")["desc"],
                         "A speedy platformer.")

    def test_unknown_game_and_system_return_empty(self):
        self.assertEqual(es_gamelist.record("genesis", "missing"), {})
        self.assertEqual(es_gamelist.records("nonexistent"), {})


class MediaFor(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.media = self.dir / "downloaded_media"
        self._saved_env = os.environ.get("MAD_MEDIA_ROOT")
        os.environ["MAD_MEDIA_ROOT"] = str(self.media)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("MAD_MEDIA_ROOT", None)
        else:
            os.environ["MAD_MEDIA_ROOT"] = self._saved_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def _touch(self, system, sub, filename):
        p = self.media / system / sub / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    def test_finds_present_kinds_and_nones_missing(self):
        cover = self._touch("genesis", "covers", "sonic.png")
        marquee = self._touch("genesis", "marquees", "sonic.png")
        video = self._touch("genesis", "videos", "sonic.mp4")
        box = self._touch("genesis", "3dboxes", "sonic.png")   # kind "box3d"
        m = es_gamelist.media_for("genesis", "sonic")
        self.assertEqual(m["covers"], str(cover))
        self.assertEqual(m["marquees"], str(marquee))
        self.assertEqual(m["videos"], str(video))
        self.assertEqual(m["box3d"], str(box))                 # box3d -> 3dboxes dir
        self.assertIsNone(m["screenshots"])                    # no file -> None
        self.assertIsNone(m["titlescreens"])

    def test_all_none_when_media_dir_absent(self):
        m = es_gamelist.media_for("genesis", "sonic")          # nothing created
        self.assertTrue(all(v is None for v in m.values()))
        self.assertEqual(set(m), set(es_gamelist.media_kinds()))

    def test_empty_stem_is_safe(self):
        self._touch("genesis", "covers", "sonic.png")
        m = es_gamelist.media_for("genesis", "")
        self.assertTrue(all(v is None for v in m.values()))

    def test_prefix_over_match_guarded(self):
        # A DIFFERENT game's file that merely shares the "sonic." prefix must NOT match:
        # media is named EXACTLY <stem> + one extension.
        self._touch("genesis", "covers", "sonic.The.Hedgehog.png")
        self.assertIsNone(es_gamelist.media_for("genesis", "sonic")["covers"])
        # ...but the exact file does match.
        cover = self._touch("genesis", "covers", "sonic.png")
        self.assertEqual(es_gamelist.media_for("genesis", "sonic")["covers"], str(cover))

    def test_stem_with_glob_chars(self):
        # A bracket in the stem is treated literally, not as a glob character class.
        cover = self._touch("genesis", "covers", "Game [!].png")
        self.assertEqual(es_gamelist.media_for("genesis", "Game [!]")["covers"],
                         str(cover))


if __name__ == "__main__":
    unittest.main()
