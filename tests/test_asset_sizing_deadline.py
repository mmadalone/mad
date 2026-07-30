"""Deadline-capped asset sizing (granular_backup._path_size_capped + the deadline
kwarg of game_files.resolve_game_assets + the granular.game_assets budget).

Why: the asset page's single RPC has a 12 s panel-side timeout; sizing a huge wine
prefix (tens of GB of small files) used to blow past it and the page showed
"Couldn't read this game". The fix walks with a monotonic deadline and reports a
PARTIAL size honestly (size_partial=True + a "size still counting" detail note)
instead of never answering. Locks in:
  * _path_size_capped == _path_size when uncapped / not exceeded (complete=True);
  * an expired deadline stops the walk early (deterministic - fake clock, no sleeps);
  * resolve_game_assets(deadline=...) marks only the affected groups;
  * granular.game_assets never raises on a blown budget and carries size_partial.

Run:  python3 -m unittest tests.test_asset_sizing_deadline -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import (es_collections, es_gamelist, esde_settings, game_files,  # noqa: E402
                 granular_backup as gb, mad_paths, retroarch_cfg)
from lib.madsrv import granular_cmds as g        # noqa: E402


class PathSizeCapped(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cap-"))
        for d in ("a", "b", "c"):
            (self.tmp / d).mkdir()
            for i in range(3):
                (self.tmp / d / f"f{i}.bin").write_bytes(b"x" * 10)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_uncapped_matches_path_size(self):
        want = gb._path_size(str(self.tmp))
        self.assertEqual(want, 90)
        self.assertEqual(gb._path_size_capped(str(self.tmp), None), (90, True))
        far = time.monotonic() + 3600
        self.assertEqual(gb._path_size_capped(str(self.tmp), far), (90, True))

    def test_single_file_ignores_deadline(self):
        f = self.tmp / "a" / "f0.bin"
        self.assertEqual(gb._path_size_capped(str(f), time.monotonic() - 1), (10, True))

    def test_expired_deadline_stops_the_walk(self):
        size, complete = gb._path_size_capped(str(self.tmp), time.monotonic() - 1)
        self.assertFalse(complete)
        self.assertLess(size, 90)

    def test_deadline_cuts_between_walk_steps(self):
        # A fake clock that expires partway through the walk: it must stop there
        # instead of exhausting the tree - deterministic, no sleeping.
        ticks = iter([0.0, 0.0, 5.0])
        with mock.patch.object(gb.time, "monotonic", lambda: next(ticks, 5.0)):
            size, complete = gb._path_size_capped(str(self.tmp), 1.0)
        self.assertFalse(complete)
        self.assertLess(size, 90)


class ResolveWithDeadline(unittest.TestCase):
    """resolve_game_assets(deadline=...) against a sandbox library (pattern copied
    from tests.test_game_assets)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gad-")
        root = Path(self.tmp)
        self.romroot = root / "ROMs"
        (self.romroot / "nes").mkdir(parents=True)
        (self.romroot / "nes" / "Game.nes").write_bytes(b"ROM")
        self.mediaroot = root / "media"
        (self.mediaroot / "nes" / "covers").mkdir(parents=True)
        (self.mediaroot / "nes" / "covers" / "Game.png").write_bytes(b"IMG")
        self.saveroot = root / "saves"
        (self.saveroot / "retroarch" / "saves" / "Nestopia").mkdir(parents=True)
        (self.saveroot / "retroarch" / "saves" / "Nestopia" / "Game.srm").write_bytes(b"SAV")
        self._p = [
            mock.patch.object(es_collections, "rom_root", lambda: self.romroot),
            mock.patch.object(esde_settings, "media_root", lambda: self.mediaroot),
            mock.patch.object(mad_paths, "saves_root", lambda: self.saveroot),
            mock.patch.object(es_gamelist, "rom_paths", lambda s: {"game": "Game.nes"}),
            mock.patch.object(es_gamelist, "media_for",
                              lambda s, st: {"covers": str(self.mediaroot / "nes" / "covers" / "Game.png")}),
            mock.patch.object(retroarch_cfg, "save_corename",
                              lambda s, st, systems=None: "Nestopia"),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_deadline_no_partial_flags(self):
        groups = game_files.resolve_game_assets("nes", "Game")
        self.assertTrue(groups)
        for grp in groups:
            self.assertNotIn("size_partial", grp)
            self.assertNotIn("still counting", grp.get("detail", ""))

    def test_generous_deadline_no_partial_flags(self):
        groups = game_files.resolve_game_assets("nes", "Game",
                                                deadline=time.monotonic() + 3600)
        for grp in groups:
            self.assertNotIn("size_partial", grp)

    def test_expired_deadline_marks_folder_groups(self):
        # Plain files size via getsize (never capped) - so point one media "path" at a
        # DIRECTORY: its size walk is what the deadline cuts, making the media group
        # genuinely partial. Guards against this test going vacuous (flagged == []).
        mediadir = Path(self.tmp) / "media" / "nes" / "videos" / "Game"
        mediadir.mkdir(parents=True)
        (mediadir / "clip.mp4").write_bytes(b"v" * 64)
        with mock.patch.object(
                es_gamelist, "media_for",
                lambda s, st: {"covers": str(self.mediaroot / "nes" / "covers" / "Game.png"),
                               "videos": str(mediadir)}):
            groups = game_files.resolve_game_assets("nes", "Game",
                                                    deadline=time.monotonic() - 1)
        flagged = [grp for grp in groups if grp.get("size_partial")]
        self.assertTrue(flagged, "expired deadline must flag the folder-backed group")
        for grp in flagged:
            self.assertIn("size still counting", grp.get("detail", ""))
        by_key = {grp["key"]: grp for grp in groups}
        self.assertIn("media", [grp["key"] for grp in flagged])
        # A file-only group must never be flagged.
        self.assertNotIn("size_partial", by_key["rom"])

    def test_capped_zero_dir_stays_present_in_emucfg_group(self):
        # THE review-major fix: a dir the capped walk returned (0, incomplete) for is
        # PRESENT (size still counting), never dropped as an empty stub.
        d = Path(self.tmp) / "shader" / "cache"
        d.mkdir(parents=True)
        (d / "pipeline.bin").write_bytes(b"x" * 32)
        partial: set = set()

        def size_of(path):
            size, complete = gb._path_size_capped(path, time.monotonic() - 1)
            if not complete:
                partial.add(path)
            return size
        size_of.partial_srcs = partial

        with mock.patch.object(game_files, "_emucfg_rel", lambda sp: "emu/shader/cache"):
            grp = game_files._emucfg_group("shader", "Shader cache", [str(d)], size_of)
        self.assertTrue(grp["present"])
        self.assertEqual(grp["files"][0]["src"], str(d))
        # ...and WITHOUT the partial marker an all-zero dir is still dropped as empty.
        empty = Path(self.tmp) / "shader" / "stub"
        empty.mkdir()
        grp2 = game_files._emucfg_group("shader", "Shader cache", [str(empty)],
                                        gb._path_size)
        self.assertFalse(grp2["present"])

    def test_rpc_carries_size_partial_and_never_raises(self):
        with mock.patch.object(g, "_ASSET_SIZING_BUDGET_S", -1.0):
            reply = g._granular_game_assets(
                {"source": "live", "system": "nes", "game": "Game"})
        self.assertEqual(reply["system"], "nes")
        for asset in reply["assets"]:
            self.assertIn("size_partial", asset)
            self.assertIsInstance(asset["size_partial"], bool)


if __name__ == "__main__":
    unittest.main()
