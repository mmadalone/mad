"""Per-kind MEDIA drill (P4): back up / restore individual media kinds (box art, marquee, ...) under the
coarse 'media' asset. The kind is derived from each file's rel ('media/<sys>/<subdir>/<stem>.<ext>'), so
NO manifest schema change is needed and pre-P4 backups (media items tagged only asset='media') stay
per-kind restorable. Locks in:
  * es_gamelist.media_kind_from_rel / media_kind_label + the 3dboxes->box3d reverse-map;
  * plan_game_assets: a 'media.<kind>' key backs up ONLY that kind; 'media' backs up all; no media key skips;
  * _manifest_items_for_games (restore + preview filter): the same per-kind selection off the item rel;
  * game_files.resolve_media_kinds (live) + granular.game_media RPC (live + a backup-manifest source).

Run:  python3 -m unittest tests.test_media_kinds -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm                                  # noqa: E402
from lib import es_gamelist, es_systems, esde_settings, game_files, granular_backup  # noqa: E402
from lib.madsrv import granular_cmds as g                             # noqa: E402
from lib.madsrv.rpc import RpcError                                    # noqa: E402


class KindHelpers(unittest.TestCase):
    def test_kind_from_rel(self):
        self.assertEqual(es_gamelist.media_kind_from_rel("media/nes/covers/Game.png"), "covers")
        self.assertEqual(es_gamelist.media_kind_from_rel("media/nes/3dboxes/Game.png"), "box3d")
        self.assertEqual(es_gamelist.media_kind_from_rel("media/nes/marquees/Game.png"), "marquees")
        self.assertIsNone(es_gamelist.media_kind_from_rel("roms/nes/Game.zip"))
        self.assertIsNone(es_gamelist.media_kind_from_rel("media/nes"))       # too short
        self.assertIsNone(es_gamelist.media_kind_from_rel(None))              # non-str
        self.assertEqual(es_gamelist.media_kind_label("covers"), "Box art")


def _canned_two_media(live: Path):
    (live / "cov.png").write_bytes(b"C")
    (live / "mar.png").write_bytes(b"M")
    return [
        {"key": "rom", "label": "ROM", "category": "roms", "present": True, "size": 3,
         "files": [{"src": str(live / "cov.png"), "rel": "roms/nes/Game.nes", "kind": "file", "size": 3}]},
        {"key": "media", "label": "Media", "category": "media", "present": True, "size": 2,
         "files": [{"src": str(live / "cov.png"), "rel": "media/nes/covers/Game.png",
                    "kind": "file", "size": 1},
                   {"src": str(live / "mar.png"), "rel": "media/nes/marquees/Game.png",
                    "kind": "file", "size": 1}]},
    ]


class PlanPerKindMedia(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mk-"))
        (self.tmp / "live").mkdir()
        self.canned = _canned_two_media(self.tmp / "live")
        self._p = [
            mock.patch.object(game_files, "resolve_game_assets", lambda s, st, systems=None, emucfg=True, steam_heavy=True: self.canned),
            mock.patch.object(es_systems, "load_systems", lambda: {}),
            mock.patch.object(es_systems, "fullname", lambda s: "NES"),
            mock.patch.object(granular_backup, "es_gamelist_record", lambda s, st: {"name": "Game"}),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, keys):
        _m, plan = granular_backup.plan_game_assets(
            [{"system": "nes", "stem": "Game", "keys": keys}], "20260101T000000")
        return {e["rel"] for e in plan}

    def test_single_kind_backs_up_only_that_kind(self):
        self.assertEqual(self._plan(["media.covers"]), {"media/nes/covers/Game.png"})

    def test_coarse_media_backs_up_all_kinds(self):
        self.assertEqual(self._plan(["media"]),
                         {"media/nes/covers/Game.png", "media/nes/marquees/Game.png"})

    def test_two_kinds(self):
        self.assertEqual(self._plan(["media.covers", "media.marquees"]),
                         {"media/nes/covers/Game.png", "media/nes/marquees/Game.png"})

    def test_no_media_key_skips_media(self):
        self.assertEqual(self._plan(["rom"]), {"roms/nes/Game.nes"})

    def test_manifest_media_items_untagged_by_kind(self):
        # the manifest still tags media items with the COARSE asset='media' (schema unchanged) - the kind
        # lives only in the rel, so pre-P4 backups restore per-kind too.
        m, _plan = granular_backup.plan_game_assets(
            [{"system": "nes", "stem": "Game", "keys": ["media.covers"]}], "20260101T000000")
        it = bm.find_item(m, "media", "nes", "media/nes/covers/Game.png")
        self.assertEqual(it["asset"], "media")


def _manifest_two_media():
    m = bm.new_manifest("granular", created="20260101T000000")
    for rel in ("media/nes/covers/Game.png", "media/nes/marquees/Game.png"):
        bm.add_item(m, category="media", category_label="Media", system="nes", system_label="NES",
                    item=bm.make_item(id=rel, name="Game", src="/x", rel=rel, size=1,
                                      extra={"game": "nes:Game", "asset": "media"}))
    return m


class RestorePerKindMedia(unittest.TestCase):
    """The restore/preview filter selects media items per kind (derived from the item rel)."""

    def _ids(self, keys):
        m = _manifest_two_media()
        triples = granular_backup._manifest_items_for_games(
            m, [{"system": "nes", "stem": "Game", "keys": keys}])
        return {item_id for _c, _s, item_id in triples}

    def test_single_kind(self):
        self.assertEqual(self._ids(["media.covers"]), {"media/nes/covers/Game.png"})

    def test_coarse_media_matches_all(self):
        self.assertEqual(self._ids(["media"]),
                         {"media/nes/covers/Game.png", "media/nes/marquees/Game.png"})

    def test_empty_keys_restores_everything(self):
        self.assertEqual(self._ids([]),
                         {"media/nes/covers/Game.png", "media/nes/marquees/Game.png"})

    def test_other_category_key_excludes_media(self):
        self.assertEqual(self._ids(["saves"]), set())


class MediaKindsResolveAndRPC(unittest.TestCase):
    def test_resolve_media_kinds_live_present_only(self):
        tmp = Path(tempfile.mkdtemp(prefix="mkl-"))
        try:
            (tmp / "cov.png").write_bytes(b"0123456789")   # 10 bytes
            (tmp / "mar.png").write_bytes(b"01234")         # 5 bytes
            with mock.patch.object(es_gamelist, "media_for",
                                   lambda s, st: {"covers": str(tmp / "cov.png"),
                                                  "marquees": str(tmp / "mar.png"), "videos": None}):
                kinds = game_files.resolve_media_kinds("nes", "Game")
            self.assertEqual([k["key"] for k in kinds], ["covers", "marquees"])  # stable order, present only
            self.assertTrue(all(k["present"] for k in kinds))
            self.assertEqual(kinds[0]["size"], 10)
            self.assertEqual(kinds[0]["label"], "Box art")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_game_media_rpc_live(self):
        with mock.patch.object(game_files, "resolve_media_kinds",
                               lambda s, st: [{"key": "covers", "label": "Box art", "present": True,
                                               "size": 5, "count": 1}]):
            out = g._granular_game_media({"source": "live", "system": "nes", "game": "Game"})
        self.assertEqual((out["system"], out["game"]), ("nes", "Game"))
        self.assertEqual([k["key"] for k in out["kinds"]], ["covers"])

    def test_game_media_rpc_from_manifest_source(self):
        m = _manifest_two_media()
        with mock.patch.object(bm, "read", lambda p: m):
            out = g._granular_game_media({"source": "/some/backup", "system": "nes", "game": "nes:Game"})
        self.assertEqual({k["key"] for k in out["kinds"]}, {"covers", "marquees"})
        self.assertTrue(all(k["present"] and k["count"] == 1 for k in out["kinds"]))

    def test_game_media_rpc_missing_args(self):
        with self.assertRaises(RpcError):
            g._granular_game_media({"source": "live", "system": "nes"})


if __name__ == "__main__":
    unittest.main()
