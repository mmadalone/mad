"""P9 - whole-system / all-systems "All" backup + restore.

An "All" backup expands the live library to EVERY game (per system, or every system), each ticked with the
FIXED asset allowlist {rom, media, saves, states}, and writes a DATED granular set (deck-granular-games-<ts>)
so a bulk snapshot is a discrete restore point that never merges into the cherry-pick fixed games set. An
"All" restore replays the reviewed game-first restore over the FULL game list read from a backup's manifest.

Locks in the contracts the P9 critique called out:
  * the allowlist is EXACTLY {rom, media, saves, states} - a future per-game key (textures/console-save/
    cheats, P13) must NOT balloon an "All";
  * versioned=True writes a DATED set and never touches / merges the fixed deck-granular-games set;
  * a ROM-missing / nothing-present game is LOGGED + skipped by the planner (never silently truncated);
  * _all_games_in_source expands every distinct game (optionally one system) with empty keys = all assets;
  * the restore-all RPCs guard an invalid source and reuse the reviewed restore path;
  * cloud.push_game_assets_all uploads to a DATED remote set (token=ts), NOT the merging fixed "games" set.

Run:  python3 -m unittest tests.test_all_scope -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm                        # noqa: E402
from lib import esde_settings, mad_paths, proc_guard          # noqa: E402
from lib import granular_backup as gb                        # noqa: E402
from lib.madsrv import cloud_cmds as cc                       # noqa: E402
from lib.madsrv import granular_cmds as g                     # noqa: E402
from lib.madsrv.rpc import RpcError                           # noqa: E402


# ── the fixed-allowlist expander (_games_for_scope) ─────────────────────────────
class ScopeExpander(unittest.TestCase):
    def setUp(self):
        self._p = [
            mock.patch.object(g.es_gamelist, "visible_records",
                              lambda s: {"a": {"stem": "A"}, "b": {"stem": "B"}}),
            mock.patch.object(g, "_game_systems", lambda: ["nes", "snes"]),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_allowlist_is_exactly_rom_media_saves_states_lutriscfg(self):
        # the P13 anti-balloon guarantee: an "All" always ticks exactly these keys, nothing
        # more. lutriscfg joined 2026-07-30: a tiny per-game Lutris YAML (steam games only;
        # other systems have no such group) - the one asset a fresh-Deck recovery needs first.
        self.assertEqual(tuple(g._ALL_ASSET_KEYS), ("rom", "media", "saves", "states", "lutriscfg"))
        games = g._games_for_scope("system", "nes")
        self.assertTrue(all(set(x["keys"]) == set(g._ALL_ASSET_KEYS) for x in games))

    def test_scope_system_is_one_system(self):
        games = g._games_for_scope("system", "nes")
        self.assertEqual([(x["system"], x["stem"]) for x in games], [("nes", "A"), ("nes", "B")])

    def test_scope_all_is_every_game_system(self):
        games = g._games_for_scope("all", None)
        self.assertEqual({x["system"] for x in games}, {"nes", "snes"})
        self.assertEqual(len(games), 4)  # 2 systems x 2 games, none dropped at expansion time

    def test_backup_all_rpc_guards(self):
        with self.assertRaises(RpcError) as cm:
            g._granular_backup_all({"scope": "bogus"})
        self.assertEqual(cm.exception.code, "EINVAL")
        with self.assertRaises(RpcError) as cm:
            g._granular_backup_all({"scope": "system"})  # missing system
        self.assertEqual(cm.exception.code, "EINVAL")


# ── the DATED set + no-silent-truncation (backup_game_assets versioned) ─────────
class BackupAllVersioned(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.live = self.base / "live"
        (self.live / "roms" / "nes").mkdir(parents=True)
        (self.live / "roms" / "nes" / "A.zip").write_bytes(b"ROM-A")
        self.dest = self.base / "dest"
        self.dest.mkdir()

        def _resolve(system, stem, systems=None, emucfg=True, steam_heavy=True):
            # game "A" has a ROM present; game "Ghost" has NOTHING present (all groups empty)
            rom = []
            if stem == "A":
                src = str(self.live / "roms" / "nes" / "A.zip")
                rom = [{"src": src, "rel": "roms/nes/A.zip", "kind": "file", "size": 5}]
            return [
                {"key": "rom", "label": "ROM", "category": "roms", "present": bool(rom), "size": 0,
                 "files": rom},
                {"key": "media", "label": "Media", "category": "media", "present": False, "size": 0,
                 "files": []},
                {"key": "saves", "label": "Save", "category": "saves", "present": False, "size": 0,
                 "files": []},
                {"key": "states", "label": "Save state", "category": "states", "present": False,
                 "size": 0, "files": []},
            ]

        self._p = [
            mock.patch.object(gb.game_files, "resolve_game_assets", _resolve),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st}),
            mock.patch.object(gb.es_systems, "fullname", lambda s: s.upper()),
            mock.patch.object(gb.es_systems, "load_systems", lambda: None),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _games(self):
        return [{"system": "nes", "stem": "A", "keys": list(g._ALL_ASSET_KEYS)},
                {"system": "nes", "stem": "Ghost", "keys": list(g._ALL_ASSET_KEYS)}]

    def test_versioned_writes_a_dated_set_not_the_fixed_set(self):
        lines = []
        out = gb.backup_game_assets(self._games(), str(self.dest), "20260728T090000",
                                    lambda e: lines.append(e), lambda: False, versioned=True)
        dated = self.dest / "deck-granular-games-20260728T090000"
        self.assertEqual(out["path"], str(dated))
        self.assertTrue(dated.is_dir(), "the dated snapshot set was written")
        self.assertFalse((self.dest / "deck-granular-games").exists(),
                         "an 'All' snapshot must NOT create / merge into the fixed games set")
        self.assertEqual((dated / "roms" / "nes" / "A.zip").read_bytes(), b"ROM-A")
        self.assertTrue(bm.validate(bm.read(dated)))
        # the ROM-missing game is LOGGED, never silently dropped
        self.assertTrue(any("Ghost" in (ln.get("line") or "") for ln in lines),
                        "a game with nothing present is reported (no silent truncation)")
        self.assertEqual(out["copied"], 1)  # only A's ROM landed

    def test_two_all_snapshots_coexist_without_clobber(self):
        gb.backup_game_assets(self._games(), str(self.dest), "20260728T090000",
                              lambda e: None, lambda: False, versioned=True)
        gb.backup_game_assets(self._games(), str(self.dest), "20260728T100000",
                              lambda e: None, lambda: False, versioned=True)
        self.assertTrue((self.dest / "deck-granular-games-20260728T090000").is_dir())
        self.assertTrue((self.dest / "deck-granular-games-20260728T100000").is_dir())

    def test_non_versioned_still_uses_the_fixed_merging_set(self):
        # regression: a NORMAL per-game backup (versioned=False) is unchanged - the fixed set.
        gb.backup_game_assets(self._games(), str(self.dest), "20260728T090000",
                              lambda e: None, lambda: False, versioned=False)
        self.assertTrue((self.dest / "deck-granular-games").is_dir())
        self.assertFalse((self.dest / "deck-granular-games-20260728T090000").exists())


# ── restore-all over a manifest (full game list, optional system filter) ────────
class RestoreAll(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"
        (self.roms / "nes").mkdir(parents=True)
        (self.roms / "snes").mkdir(parents=True)
        self.bdir = self.base / "backup" / "deck-granular-games-20260728T000000"
        self.bdir.mkdir(parents=True)
        # two games in two systems, each a single ROM item (whole-ROM-style + game tag)
        self.items = [
            ("nes", "nes:A", "roms/nes/A.zip", b"BK-A"),
            ("snes", "snes:B", "roms/snes/B.zip", b"BK-B"),
        ]
        m = bm.new_manifest("granular", created="20260728T000000")
        for sysk, gid, rel, data in self.items:
            (self.bdir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.bdir / rel).write_bytes(data)
            stem = gid.split(":", 1)[1]
            bm.add_item(m, category="roms", category_label="ROMs & games", system=sysk,
                        system_label=sysk.upper(),
                        item=bm.make_item(id=rel, name=stem, src="/x", rel=rel, kind="file",
                                          size=len(data), stem=stem,
                                          extra={"game": gid, "asset": "rom"}))
        bm.write(m, bm.manifest_path(self.bdir))
        self._p = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_all_games_in_source_full_and_filtered(self):
        allg = g._all_games_in_source(str(self.bdir), None)
        self.assertEqual({(x["system"], x["stem"]) for x in allg}, {("nes", "A"), ("snes", "B")})
        self.assertTrue(all(x["keys"] == [] for x in allg), "empty keys = restore ALL of each game's assets")
        just_nes = g._all_games_in_source(str(self.bdir), "nes")
        self.assertEqual([(x["system"], x["stem"]) for x in just_nes], [("nes", "A")])

    def test_restore_all_lands_every_game_with_rule5(self):
        # a live copy of A exists (corrupt) -> replace + rule-5 snapshot; B is fresh
        (self.roms / "nes" / "A.zip").write_bytes(b"LIVE-A")
        games = g._all_games_in_source(str(self.bdir), None)
        summary = gb.restore_game_assets(str(self.bdir), games, "20260728T010000",
                                         lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 2, summary)
        self.assertEqual((self.roms / "nes" / "A.zip").read_bytes(), b"BK-A")
        self.assertEqual((self.roms / "snes" / "B.zip").read_bytes(), b"BK-B")
        self.assertTrue(summary["snapshots"], "the overwritten live copy is snapshotted (rule #5)")

    def test_restore_all_rpc_runs_the_reviewed_path(self):
        (self.roms / "nes" / "A.zip").write_bytes(b"LIVE-A")
        captured = {}

        def _sync_start(fn):
            sink = []
            captured["summary"] = fn(sink.append, lambda: False)
            return {"stream": "s"}

        with mock.patch.object(g, "_start_granular", _sync_start):
            out = g._granular_restore_all({"source": str(self.bdir)})
        self.assertEqual(out, {"stream": "s"})
        self.assertEqual(captured["summary"]["restored"], 2)

    def test_restore_all_preview_classifies(self):
        (self.roms / "nes" / "A.zip").write_bytes(b"LIVE-A")  # A present -> replace; B fresh
        pv = g._granular_restore_all_preview({"source": str(self.bdir)})
        self.assertEqual([r["id"] for r in pv["replace"]], ["roms/nes/A.zip"])
        self.assertEqual([r["id"] for r in pv["fresh"]], ["roms/snes/B.zip"])

    def test_restore_all_guards(self):
        with self.assertRaises(RpcError) as cm:
            g._granular_restore_all({"source": ""})
        self.assertEqual(cm.exception.code, "EINVAL")
        with self.assertRaises(RpcError) as cm:
            g._granular_restore_all({"source": g.LIVE_SOURCE})
        self.assertEqual(cm.exception.code, "EINVAL")
        # a source with no valid manifest -> ENOENT (via _all_games_in_source)
        empty = self.base / "not-a-backup"
        empty.mkdir()
        with self.assertRaises(RpcError) as cm:
            g._granular_restore_all({"source": str(empty)})
        self.assertEqual(cm.exception.code, "ENOENT")


# ── cloud "All" uploads to a DATED remote set (not the merging fixed "games") ───
class CloudPushAll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _canned_plan(self, _games, ts, emit=None, is_stopped=None):
        m = bm.new_manifest("granular", created=ts)
        bm.add_item(m, category="roms", category_label="ROMs & games", system="nes",
                    system_label="NES",
                    item=bm.make_item(id="roms/nes/A.zip", name="A", src="/live/roms/nes/A.zip",
                                      rel="roms/nes/A.zip", kind="file", size=5,
                                      extra={"game": "nes:A", "asset": "rom"}))
        plan = [{"id": "roms/nes/A.zip", "name": "A", "system": "nes", "category": "roms",
                 "src": "/live/roms/nes/A.zip", "rel": "roms/nes/A.zip", "kind": "file"}]
        return m, plan

    def test_uploads_to_a_dated_set_no_merge(self):
        seen = {}

        def fake_stream_op(argv):
            seen["argv"] = argv
            return {"stream": "s1"}

        def fake_run(*a, **k):
            seen.setdefault("run", []).append(a)
            return (0, "", "")

        with mock.patch.object(g, "_games_for_scope",
                               lambda scope, system: [{"system": "nes", "stem": "A",
                                                       "keys": ["rom", "media", "saves", "states"]}]), \
             mock.patch.object(cc.granular_backup, "plan_game_assets", self._canned_plan), \
             mock.patch.object(cc, "_run", fake_run), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_game_assets_all({"scope": "all"})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-games"])
        # the remote SET token is the DATED ts (NOT the fixed "games" set) -> a discrete cloud snapshot
        self.assertNotEqual(argv[2], "games")
        self.assertRegex(argv[2], r"^\d{8}T\d{6}$")
        # and NO cat-manifest merge was run (a dated set is written fresh)
        self.assertNotIn("run", seen, "a dated 'All' cloud set must not merge a remote manifest")

    def test_cloud_all_guards(self):
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_game_assets_all({"scope": "bogus"})
        self.assertEqual(cm.exception.code, "EINVAL")
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_game_assets_all({"scope": "system"})  # missing system
        self.assertEqual(cm.exception.code, "EINVAL")


# ── BIOS / emulator-config "All" (every bucket / every emulator incl. giant dirs) ──
class AllCategoryBiosEmucfg(unittest.TestCase):
    def test_all_bios_items_flattens_every_bucket(self):
        with mock.patch.object(g.bios_map, "list_buckets", lambda: [
                {"key": "ps2", "files": [{"rel": "bios/ps2/scph.bin"}]},
                {"key": "other", "files": [{"rel": "bios/x.bin"}, {"rel": "bios/y.bin"}]}]):
            self.assertEqual(g._all_bios_items(), [
                {"bucket": "ps2", "rel": "bios/ps2/scph.bin"},
                {"bucket": "other", "rel": "bios/x.bin"},
                {"bucket": "other", "rel": "bios/y.bin"}])

    def test_all_emucfg_items_includes_the_giant_group(self):
        # "All" means ALL - the opt-in giant texture/mod group IS included (user decision; warning is in the UI).
        with mock.patch.object(g.emu_map, "list_emulators", lambda: [{"key": "pcsx2"}]), \
             mock.patch.object(g.emu_map, "list_files", lambda k: [
                 {"key": "config", "files": [{"rel": "emucfg/.config/PCSX2/PCSX2.ini"}]},
                 {"key": "textures", "files": [{"rel": "emucfg/.config/PCSX2/textures"}]}]):
            items = g._all_emucfg_items()
        self.assertIn({"emulator": "pcsx2", "group": "textures",
                       "rel": "emucfg/.config/PCSX2/textures"}, items)
        self.assertEqual(len(items), 2)

    def test_backup_all_size_sums_including_giant(self):
        with mock.patch.object(g.emu_map, "list_emulators", lambda: [{"key": "pcsx2"}]), \
             mock.patch.object(g.emu_map, "list_files", lambda k: [
                 {"key": "config", "files": [{"rel": "a", "size": 10}]},
                 {"key": "textures", "files": [{"rel": "b", "size": 40_000_000_000}]}]):
            out = g._granular_backup_all_size({"category": "emucfg"})
        self.assertEqual(out, {"size": 40_000_000_010, "count": 2})

    def test_backup_bios_all_empty_is_einval(self):
        with mock.patch.object(g.bios_map, "list_buckets", lambda: []):
            with self.assertRaises(RpcError) as cm:
                g._granular_backup_bios_all({})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_restore_all_bios_round_trip_rule5(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        bios = base / "bios"
        (bios / "ps2").mkdir(parents=True)
        (bios / "ps2" / "scph.bin").write_bytes(b"OLD")  # a live copy -> replace + rule-5 snapshot
        bdir = base / "backup" / "deck-granular-bios"
        (bdir / "bios" / "ps2").mkdir(parents=True)
        (bdir / "bios" / "ps2" / "scph.bin").write_bytes(b"NEWBIOS")
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="bios", category_label="BIOS", system="ps2", system_label="ps2",
                    item=bm.make_item(id="bios/ps2/scph.bin", name="scph", src="/x",
                                      rel="bios/ps2/scph.bin", kind="file", size=7))
        bm.write(m, bm.manifest_path(bdir))
        captured = {}

        def _sync(fn):
            captured["summary"] = fn(lambda e: None, lambda: False)
            return {"stream": "s"}

        with mock.patch.object(mad_paths, "bios_root", lambda: bios), \
             mock.patch.object(gb.proc_guard, "esde_running", lambda: False), \
             mock.patch.object(g, "_start_granular", _sync):
            out = g._granular_restore_all({"source": str(bdir), "category": "bios"})
        self.assertEqual(out, {"stream": "s"})
        self.assertEqual(captured["summary"]["restored"], 1)
        self.assertEqual((bios / "ps2" / "scph.bin").read_bytes(), b"NEWBIOS")
        self.assertTrue(captured["summary"]["snapshots"], "the replaced live bios is snapshotted (rule #5)")

    def test_restore_all_emucfg_refuses_while_that_emulator_runs(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        bdir = base / "backup" / "deck-granular-emucfg-x"
        bdir.mkdir(parents=True)
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="emucfg", category_label="Emulator config & data", system="pcsx2",
                    system_label="PCSX2",
                    item=bm.make_item(id="emucfg/.config/PCSX2/PCSX2.ini", name="ini", src="/x",
                                      rel="emucfg/.config/PCSX2/PCSX2.ini", kind="file", size=1,
                                      extra={"group": "config"}))
        bm.write(m, bm.manifest_path(bdir))
        # the per-emulator running guard (enforced in the reused _granular_restore path) must fire for "All" too
        with mock.patch.object(proc_guard, "emulator_running", lambda be: True):
            with self.assertRaises(RpcError) as cm:
                g._granular_restore_all({"source": str(bdir), "category": "emucfg"})
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_restore_all_unknown_category_einval(self):
        with self.assertRaises(RpcError) as cm:
            g._granular_restore_all({"source": "/x", "category": "bogus"})
        self.assertEqual(cm.exception.code, "EINVAL")


if __name__ == "__main__":
    unittest.main()
