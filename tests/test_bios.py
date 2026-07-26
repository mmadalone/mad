"""BIOS backup & restore (P5): bucketing (display-only, file-first) + backup + rule-5 restore.

bios_map buckets every file under the BIOS root: a SUBDIR is the system (authoritative); a loose .zip is
Arcade/MAME; a loose named file matches the curated pattern map; anything else is Other. `rel` is the TRUE
'bios/<path>' key, so a file restores to its exact origin regardless of the bucket that showed it.

Run:  python3 -m unittest tests.test_bios -v
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

from lib import bios_map, granular_backup as gb, mad_paths      # noqa: E402
from lib import backup_manifest as bm                            # noqa: E402
from lib.madsrv import granular_cmds as g                        # noqa: E402
from lib.madsrv.rpc import RpcError                              # noqa: E402


class Bucketing(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.bios = self.base / "bios"
        (self.bios / "ps2").mkdir(parents=True)
        (self.bios / "ps2" / "scph39001.bin").write_bytes(b"PS2BIOS")   # subdir -> PlayStation 2
        (self.bios / "scph1001.bin").write_bytes(b"PS1BIOS")            # loose named -> PlayStation
        (self.bios / "neogeo.zip").write_bytes(b"ZIP")                  # loose .zip -> Arcade / MAME
        (self.bios / "totally_unknown.xyz").write_bytes(b"?")           # loose -> Other

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_buckets(self):
        buckets = {b["key"]: b for b in bios_map.list_buckets(self.bios)}
        self.assertIn("ps2", buckets)
        self.assertEqual(buckets["ps2"]["label"], "PlayStation 2")
        self.assertEqual(buckets["ps1"]["label"], "PlayStation")       # scph loose -> PS1
        self.assertEqual(buckets["arcade"]["label"], "Arcade / MAME")  # the .zip
        self.assertEqual(buckets["other"]["label"], "Other")           # the unknown file
        # rel is the TRUE bios-relative path (the restore key)
        rels = {f["rel"] for b in buckets.values() for f in b["files"]}
        self.assertIn("bios/ps2/scph39001.bin", rels)
        self.assertIn("bios/scph1001.bin", rels)

    def test_no_duplicate_labels(self):
        labels = [b["label"] for b in bios_map.list_buckets(self.bios)]
        self.assertEqual(len(labels), len(set(labels)), "each system is one tile")

    def test_symlinked_subdir_is_walked(self):
        # REVIEW #3: a front-door symlink subdir (like bios/ryujinx/keys -> the emu store) must be WALKED
        ext = self.base / "ext"; ext.mkdir()
        (ext / "prod.keys").write_bytes(b"K")
        os.symlink(ext, self.bios / "ryujinx")
        rels = {f["rel"] for b in bios_map.list_buckets(self.bios) for f in b["files"]}
        self.assertIn("bios/ryujinx/prod.keys", rels, "files behind a symlinked subdir are backable")


class BackupRestore(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.bios = self.base / "bios"
        (self.bios / "ps2").mkdir(parents=True)
        (self.bios / "ps2" / "scph39001.bin").write_bytes(b"PS2ORIG")
        (self.bios / "scph1001.bin").write_bytes(b"PS1ORIG")
        self.dest = self.base / "dest"; self.dest.mkdir()
        self._p = [mock.patch.object(mad_paths, "bios_root", lambda: self.bios),
                   mock.patch.object(gb.es_collections, "rom_root", lambda: self.base / "ROMs"),
                   mock.patch.object(gb.proc_guard, "esde_running", lambda: False)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _backup(self, items):
        ts = "20260726T120000"
        return gb.backup_bios(items, str(self.dest), ts, lambda e: None, lambda: False)

    def test_backup_then_restore_with_rule5(self):
        items = [{"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"},
                 {"bucket": "ps1", "rel": "bios/scph1001.bin"}]
        out = self._backup(items)
        self.assertEqual(out["copied"], 2)
        bdir = out["path"]
        self.assertTrue((Path(bdir) / "bios" / "ps2" / "scph39001.bin").is_file())
        # corrupt the live files, then restore over them (rule #5 keeps the corrupt copies aside)
        (self.bios / "ps2" / "scph39001.bin").write_bytes(b"CORRUPT")
        (self.bios / "scph1001.bin").write_bytes(b"CORRUPT")
        summary = gb.restore_selection(bdir, [{"system": "ps2", "id": "bios/ps2/scph39001.bin"},
                                              {"system": "ps1", "id": "bios/scph1001.bin"}],
                                       "bios", "20260726T130000", lambda e: None, lambda: False)
        self.assertEqual((summary["restored"], summary["replaced"]), (2, 2))
        self.assertEqual((self.bios / "ps2" / "scph39001.bin").read_bytes(), b"PS2ORIG")
        self.assertEqual((self.bios / "scph1001.bin").read_bytes(), b"PS1ORIG")
        self.assertTrue(summary["snapshots"], "rule-5 snapshot of the corrupt copies")

    def test_plan_bios_skips_traversal_and_missing(self):
        m, plan = gb.plan_bios([{"bucket": "x", "rel": "bios/../evil.bin"},       # traversal
                                {"bucket": "x", "rel": "bios/ghost.bin"},         # absent
                                {"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"}],  # real
                               "t")
        self.assertEqual([e["rel"] for e in plan], ["bios/ps2/scph39001.bin"])
        self.assertFalse((self.base / "evil.bin").exists())

    def test_symlinked_subdir_file_backs_up_and_restores(self):
        # REVIEW #3 backup/restore: a file behind a front-door symlink dir must back up (LEXICAL
        # containment, not realpath) and restore THROUGH the symlink.
        ext = self.base / "switch-store"; ext.mkdir()
        (ext / "prod.keys").write_bytes(b"KEYSORIG")
        os.symlink(ext, self.bios / "switch")
        _m, plan = gb.plan_bios([{"bucket": "switch", "rel": "bios/switch/prod.keys"}], "t")
        self.assertEqual([e["rel"] for e in plan], ["bios/switch/prod.keys"], "symlinked file is backable")
        out = gb.backup_bios([{"bucket": "switch", "rel": "bios/switch/prod.keys"}],
                             str(self.dest), "20260726T140000", lambda e: None, lambda: False)
        self.assertEqual(out["copied"], 1)
        (ext / "prod.keys").write_bytes(b"CORRUPT")   # corrupt the live file (through the symlink)
        gb.restore_selection(out["path"], [{"system": "switch", "id": "bios/switch/prod.keys"}],
                             "bios", "20260726T150000", lambda e: None, lambda: False)
        self.assertEqual((ext / "prod.keys").read_bytes(), b"KEYSORIG", "restored through the symlink")

    def test_bios_only_backup_not_a_game_restore_source(self):
        # REVIEW #1/#2: a bios-only backup has 0 GAMES, so the game-first restore source scan must skip it
        self._backup([{"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"}])
        self.assertEqual(g._scan_backup_sources([self.dest]), [],
                         "a bios-only backup is not listed for the game restore")

    def test_rpc_systems_files_and_guard(self):
        sysr = g._bios_systems({"source": "live"})
        keys = {s["key"] for s in sysr["systems"]}
        self.assertIn("ps2", keys)
        filesr = g._bios_files({"source": "live", "bucket": "ps2"})
        self.assertEqual([f["rel"] for f in filesr["files"]], ["bios/ps2/scph39001.bin"])
        with self.assertRaises(RpcError):
            g._granular_backup_bios({"items": []})


if __name__ == "__main__":
    unittest.main()
