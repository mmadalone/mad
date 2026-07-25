"""Game-first cross-category assets (P2): resolve_game_assets + plan/backup_game_assets + the
granular.game_assets RPC.

CI-safe: builds a sandbox library (rom/media/saves/states dirs) and monkeypatches the enumeration
primitives so nothing depends on ~/ES-DE or ~/.var existing. Locks in:
  * resolve_game_assets groups ROM/Media/Save/Save state with the right rel scheme, incl. the RetroArch
    core-folder + flat layout and the mGBA family probe;
  * plan_game_assets writes a MULTI-category manifest, only for ticked+present groups, with the
    extra.game/asset back-link, and backup_game_assets copies each file to its rel;
  * granular.game_assets (live) returns the tickable groups; _manifest_game_assets regroups a backup.

Run:  python3 -m unittest tests.test_game_assets -v
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

from lib import backup_manifest as bm           # noqa: E402
from lib import (es_collections, es_gamelist, es_systems, esde_settings,  # noqa: E402
                 game_files, granular_backup, mad_paths, retroarch_cfg)
from lib.madsrv import granular_cmds as g        # noqa: E402
from lib.madsrv.rpc import RpcError              # noqa: E402


class ResolveGameAssets(unittest.TestCase):
    """resolve_game_assets against a real sandbox library."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-")
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
        (self.saveroot / "retroarch" / "states" / "Nestopia").mkdir(parents=True)
        (self.saveroot / "retroarch" / "states" / "Nestopia" / "Game.state").write_bytes(b"ST")
        (self.saveroot / "retroarch" / "states" / "Nestopia" / "Game.state.png").write_bytes(b"P")
        (self.saveroot / "mgba" / "saves").mkdir(parents=True)
        (self.saveroot / "mgba" / "saves" / "GBA.sav").write_bytes(b"GBASAV")
        self._p = [
            mock.patch.object(es_collections, "rom_root", lambda: self.romroot),
            mock.patch.object(esde_settings, "media_root", lambda: self.mediaroot),
            mock.patch.object(mad_paths, "saves_root", lambda: self.saveroot),
            mock.patch.object(es_gamelist, "rom_paths",
                              lambda s: {"game": "Game.nes", "gba": "GBA.gba"}),
            mock.patch.object(es_gamelist, "media_for",
                              lambda s, st: ({"covers": str(self.mediaroot / "nes" / "covers" / "Game.png"),
                                              "videos": None} if st == "Game" else {})),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retroarch_game_groups_and_rels(self):
        with mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: "Nestopia"):
            groups = {grp["key"]: grp for grp in game_files.resolve_game_assets("nes", "Game")}
        self.assertEqual(groups["rom"]["files"][0]["rel"], "roms/nes/Game.nes")
        self.assertEqual(groups["media"]["files"][0]["rel"], "media/nes/covers/Game.png")
        self.assertEqual({f["rel"] for f in groups["saves"]["files"]},
                         {"saves/retroarch/saves/Nestopia/Game.srm"})
        self.assertEqual({f["rel"] for f in groups["states"]["files"]},
                         {"states/retroarch/states/Nestopia/Game.state",
                          "states/retroarch/states/Nestopia/Game.state.png"})
        self.assertTrue(all(groups[k]["present"] for k in ("rom", "media", "saves", "states")))

    def test_mgba_family_flat_saves(self):
        # a standalone (corename None) gba game -> the mGBA flat dir is probed
        with mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: None), \
             mock.patch.object(es_systems, "resolved_command", lambda s, st, systems=None: "mgba %ROM%"):
            groups = {grp["key"]: grp for grp in game_files.resolve_game_assets("gba", "GBA")}
        self.assertEqual({f["rel"] for f in groups["saves"]["files"]}, {"saves/mgba/saves/GBA.sav"})
        self.assertFalse(groups["states"]["present"])

    def test_absent_asset_group_not_present(self):
        with mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: "Nestopia"):
            groups = {grp["key"]: grp for grp in game_files.resolve_game_assets("nes", "NoSuchGame")}
        self.assertFalse(groups["rom"]["present"])
        self.assertFalse(groups["saves"]["present"])
        self.assertEqual(groups["rom"]["files"], [])


class PlanBackupGameAssets(unittest.TestCase):
    """plan_game_assets + backup_game_assets with canned groups over real temp files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gaplan-")
        self.live = Path(self.tmp) / "live"
        self.live.mkdir()
        (self.live / "rom.nes").write_bytes(b"ROM")
        (self.live / "save.srm").write_bytes(b"SAVE")
        self.canned = [
            {"key": "rom", "label": "ROM", "category": "roms", "present": True, "size": 3,
             "files": [{"src": str(self.live / "rom.nes"), "rel": "roms/nes/Game.nes",
                        "kind": "file", "size": 3}]},
            {"key": "media", "label": "Media", "category": "media", "present": False,
             "size": 0, "files": []},
            {"key": "saves", "label": "Save", "category": "saves", "present": True, "size": 4,
             "files": [{"src": str(self.live / "save.srm"),
                        "rel": "saves/retroarch/saves/Nestopia/Game.srm", "kind": "file", "size": 4}]},
        ]
        self._p = [
            mock.patch.object(game_files, "resolve_game_assets",
                              lambda s, st, systems=None: self.canned),
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

    def test_plan_only_ticked_and_present(self):
        games = [{"system": "nes", "stem": "Game", "keys": ["rom", "saves", "media"]}]
        m, plan = granular_backup.plan_game_assets(games, "20260101T000000")
        self.assertEqual({e["rel"] for e in plan},
                         {"roms/nes/Game.nes", "saves/retroarch/saves/Nestopia/Game.srm"})  # media absent
        self.assertEqual({c["key"] for c in bm.categories(m)}, {"roms", "saves"})
        item = bm.find_item(m, "saves", "nes", "saves/retroarch/saves/Nestopia/Game.srm")
        self.assertEqual(item["game"], "nes:Game")   # extra{} folds at the item top level
        self.assertEqual(item["asset"], "saves")

    def test_unticked_group_excluded(self):
        games = [{"system": "nes", "stem": "Game", "keys": ["saves"]}]
        _m, plan = granular_backup.plan_game_assets(games, "20260101T000000")
        self.assertEqual({e["rel"] for e in plan}, {"saves/retroarch/saves/Nestopia/Game.srm"})

    def test_backup_copies_files_and_writes_manifest(self):
        games = [{"system": "nes", "stem": "Game", "keys": ["rom", "saves"]}]
        dest = Path(self.tmp) / "dest"
        dest.mkdir()
        res = granular_backup.backup_game_assets(games, str(dest), "20260101T000000",
                                                 lambda msg: None, lambda: False)
        self.assertEqual(res["copied"], 2)
        bdir = Path(res["path"])
        self.assertTrue((bdir / "roms/nes/Game.nes").exists())
        self.assertTrue((bdir / "saves/retroarch/saves/Nestopia/Game.srm").exists())
        self.assertTrue(bm.validate(bm.read(bdir)))

    def test_empty_selection_leaves_no_folder(self):
        games = [{"system": "nes", "stem": "Game", "keys": ["media"]}]  # media absent -> nothing planned
        dest = Path(self.tmp) / "dest2"
        dest.mkdir()
        res = granular_backup.backup_game_assets(games, str(dest), "20260101T000001",
                                                 lambda msg: None, lambda: False)
        self.assertEqual(res["copied"], 0)
        self.assertFalse(Path(res["path"]).exists())


class GameAssetsRPC(unittest.TestCase):
    def test_live_returns_tickable_groups(self):
        canned = [{"key": "rom", "label": "ROM", "category": "roms", "present": True,
                   "size": 3, "files": [{"x": 1}]},
                  {"key": "saves", "label": "Save", "category": "saves", "present": False,
                   "size": 0, "files": []}]
        with mock.patch.object(game_files, "resolve_game_assets", lambda s, st: canned):
            out = g._granular_game_assets({"source": "live", "system": "nes", "game": "Game"})
        self.assertEqual((out["system"], out["game"]), ("nes", "Game"))
        a = {x["key"]: x for x in out["assets"]}
        self.assertTrue(a["rom"]["present"])
        self.assertEqual(a["rom"]["count"], 1)
        self.assertFalse(a["saves"]["present"])
        self.assertNotIn("files", a["rom"])  # the RPC returns groups, not the file list

    def test_game_id_form_accepted(self):
        with mock.patch.object(game_files, "resolve_game_assets", lambda s, st: []):
            out = g._granular_game_assets({"source": "live", "system": "nes", "game": "nes:Game"})
        self.assertEqual(out["game"], "Game")

    def test_missing_args_rejected(self):
        with self.assertRaises(RpcError):
            g._granular_game_assets({"source": "live"})
        with self.assertRaises(RpcError):
            g._granular_game_assets({"source": "live", "system": "nes"})

    def test_manifest_grouping_by_game(self):
        m = bm.new_manifest("granular", created="20260101T000000")
        bm.add_item(m, category="saves", category_label="Saves", system="nes", system_label="NES",
                    item=bm.make_item(id="saves/r/Game.srm", name="Game", src="/x",
                                      rel="saves/r/Game.srm", extra={"game": "nes:Game", "asset": "saves"}))
        bm.add_item(m, category="media", category_label="Media", system="nes", system_label="NES",
                    item=bm.make_item(id="media/nes/covers/Game.png", name="Game", src="/y",
                                      rel="media/nes/covers/Game.png",
                                      extra={"game": "nes:Game", "asset": "media"}))
        bm.add_item(m, category="saves", category_label="Saves", system="snes", system_label="SNES",
                    item=bm.make_item(id="saves/r/Other.srm", name="Other", src="/z",
                                      rel="saves/r/Other.srm", extra={"game": "snes:Other", "asset": "saves"}))
        assets = g._manifest_game_assets(m, "nes:Game")
        self.assertEqual({a["key"] for a in assets}, {"saves", "media"})  # not the snes:Other game
        self.assertTrue(all(a["present"] and a["count"] == 1 for a in assets))


if __name__ == "__main__":
    unittest.main()
