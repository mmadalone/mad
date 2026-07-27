"""Cloud BIOS backup + restore (Slice 2): the CLOUD parity of the local BIOS backup/restore, using a
SEPARATE remote base (bios-backups/<ts>) so a BIOS set never cross-lists in the per-game cloud restore.

PushBios (CI-safe, the shell engine is mocked): cloud.push_bios resolves the selection via the SHARED
planner granular_backup.plan_bios, persists a plan-dir under <state>/bios-plan/<ts>, and streams
deck-cloud.sh push-bios (its OWN subcommand + plan_root, distinct from push-games). Locks the safety
contracts: valid selection persists+streams; empty/all-skipped is EINVAL (so the C++ mRunning guard
releases); a concurrent op is EBUSY and does not orphan the plan dir; a BIOS upload is not a restore.

CloudBios (Deck-only, needs rclone): a real round-trip via DECK_CLOUD_BIOS_BASE_OVERRIDE ->
push-bios -> list-bios (own source list) -> cat-bios-manifest (browse) -> fetch-bios -> the reviewed
restore_selection(category="bios") lands the file with a rule-5 snapshot. Proves the SEPARATION invariant:
a pushed BIOS set is listed by bios.cloud_sources but NOT by granular.cloud_sources (the game restore).

Run:  python3 -m unittest tests.test_cloud_bios -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CLOUD = ROOT / "deck-cloud.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_RCLONE = (BIN / "rclone").exists()

from lib import backup_manifest as bm                    # noqa: E402
from lib import granular_backup as gb, mad_paths          # noqa: E402
from lib.madsrv import cloud_cmds as cc                    # noqa: E402
from lib.madsrv import granular_cmds as g                  # noqa: E402
from lib.madsrv.rpc import RpcError                        # noqa: E402


def _fake_bios_plan(_items, ts, emit=None, is_stopped=None):
    """A canned (manifest, plan) for two BIOS files, so the RPC test needs no on-disk bios tree."""
    m = bm.new_manifest("granular", created=ts)
    for bucket, rel in (("ps2", "bios/ps2/scph39001.bin"), ("ps1", "bios/scph1001.bin")):
        bm.add_item(m, category="bios", category_label="BIOS", system=bucket, system_label=bucket,
                    item=bm.make_item(id=rel, name=os.path.basename(rel), src="/live/" + rel, rel=rel,
                                      kind="file", size=8))
    plan = [{"id": "bios/ps2/scph39001.bin", "name": "scph39001.bin", "system": "ps2",
             "src": "/live/bios/ps2/scph39001.bin", "rel": "bios/ps2/scph39001.bin", "kind": "file"},
            {"id": "bios/scph1001.bin", "name": "scph1001.bin", "system": "ps1",
             "src": "/live/bios/scph1001.bin", "rel": "bios/scph1001.bin", "kind": "file"}]
    return m, plan


class PushBios(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_plan_dir_and_streams_push_bios(self):
        seen = {}

        def fake_stream_op(argv):
            seen["argv"] = argv
            return {"stream": "s1"}

        items = [{"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"},
                 {"bucket": "ps1", "rel": "bios/scph1001.bin"}]
        with mock.patch.object(cc.granular_backup, "plan_bios", _fake_bios_plan), \
             mock.patch.object(cc, "_run", lambda *a, **k: (1, "", "")), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_bios({"items": items})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        # push-bios (NOT push-games): a SEPARATE subcommand -> a SEPARATE remote base (bios-backups).
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-bios"])
        # argv[2] = the FIXED "bios" token (single non-versioned BIOS set); the plan-dir keeps a unique ts.
        self.assertEqual(argv[2], "bios")
        plandir = Path(argv[3])
        self.assertEqual(plandir.parent, cc._state_dir() / "bios-plan")
        self.assertTrue((plandir / "mad-manifest.json").is_file(), "manifest persisted for the engine")
        self.assertEqual(
            (plandir / "plan").read_bytes(),
            b"/live/bios/ps2/scph39001.bin\0bios/ps2/scph39001.bin\0"
            b"/live/bios/scph1001.bin\0bios/scph1001.bin\0",
            "the NUL src/rel plan carries every BIOS path verbatim for the shell")

    def test_empty_items_einval(self):
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_bios({"items": []})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_nothing_present_einval(self):
        # all files absent -> plan_bios returns an empty plan -> EINVAL (the C++ mRunning guard releases)
        with mock.patch.object(cc.granular_backup, "plan_bios",
                               lambda *a, **k: (bm.new_manifest("granular", created="x"), [])):
            with self.assertRaises(RpcError) as cm:
                cc._cloud_push_bios({"items": [{"bucket": "ps2", "rel": "bios/ghost.bin"}]})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_concurrent_op_ebusy_does_not_orphan_plan_dir(self):
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_bios", _fake_bios_plan):
                with self.assertRaises(RpcError) as cm:
                    cc._cloud_push_bios({"items": [{"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"}]})
            self.assertEqual(cm.exception.code, "EBUSY")
        finally:
            cc._RUN_ACTIVE.release()
        bp = cc._state_dir() / "bios-plan"
        leftover = list(bp.iterdir()) if bp.is_dir() else []
        self.assertEqual(leftover, [], "an EBUSY start must not orphan a plan dir")

    def test_bios_upload_is_not_a_restore(self):
        self.assertFalse(cc._is_restore(["push-bios", "20260726T000000", "/x/plan"]))
        self.assertEqual(cc._op_title(["push-bios"]), "Backing up BIOS")


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class CloudBios(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.bios = self.base / "bios"
        (self.bios / "ps2").mkdir(parents=True)
        self.f = self.bios / "ps2" / "scph39001.bin"
        self.f.write_bytes(b"PS2BIOS" * 50)
        self.bios_remote = self.base / "bios-remote"
        self.games_remote = self.base / "games-remote"   # a SEPARATE base to prove no cross-listing
        self._env = {"DECK_CLOUD_RCLONE": str(BIN / "rclone"), "DECK_CLOUD_SKIP_CONNCHECK": "1",
                     "DECK_CLOUD_NO_NICE": "1", "DECK_CLOUD_STATE_DIR": str(self.base / "state"),
                     "DECK_CLOUD_BIOS_BASE_OVERRIDE": str(self.bios_remote),
                     "DECK_CLOUD_GAMES_BASE_OVERRIDE": str(self.games_remote)}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._patches = [
            mock.patch.object(mad_paths, "bios_root", lambda: self.bios),
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.base / "ROMs"),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
            mock.patch.object(g, "console_art", lambda s: ""),
        ]
        for p in self._patches:
            p.start()
        self.ts = self._push_one()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _push_one(self):
        ts = "20260726T210000"
        manifest, plan = gb.plan_bios([{"bucket": "ps2", "rel": "bios/ps2/scph39001.bin"}], ts)
        pd = self.base / "plan"; pd.mkdir()
        bm.write(manifest, bm.manifest_path(pd))
        (pd / "plan").write_bytes(
            b"".join(e["src"].encode() + b"\0" + e["rel"].encode() + b"\0" for e in plan))
        r = subprocess.run([str(CLOUD), "push-bios", ts, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return ts

    def test_bios_cloud_sources_lists_the_set(self):
        out = g._bios_cloud_sources({})
        self.assertTrue(out["connected"])
        src = {s["id"]: s for s in out["sources"]}.get(f"cloud:{self.ts}")
        self.assertIsNotNone(src, "the pushed BIOS set is listed by bios.cloud_sources")
        self.assertEqual(src["count"], 1, "1 BIOS file")

    def test_bios_set_is_not_in_the_game_restore(self):
        # SEPARATION: a BIOS set lives in bios-backups, so the GAME cloud restore (list-games) never sees it.
        ids = {s["id"] for s in g._granular_cloud_sources({})["sources"]}
        self.assertNotIn(f"cloud:{self.ts}", ids, "a BIOS set must NOT cross-list in the game restore")

    def test_bios_cloud_browse(self):
        sysr = g._bios_systems({"source": f"cloud:{self.ts}"})
        self.assertIn("ps2", {s["key"] for s in sysr["systems"]})
        filesr = g._bios_files({"source": f"cloud:{self.ts}", "bucket": "ps2"})
        self.assertEqual([f["rel"] for f in filesr["files"]], ["bios/ps2/scph39001.bin"])

    def test_bios_cloud_preview_replace(self):
        pv = g._granular_restore_preview({"source": f"cloud:{self.ts}", "category": "bios",
                                          "items": [{"system": "ps2", "id": "bios/ps2/scph39001.bin"}]})
        self.assertEqual([r["id"] for r in pv["replace"]], ["bios/ps2/scph39001.bin"])

    def test_bios_cloud_restore_downloads_and_restores_with_rule5(self):
        self.f.write_bytes(b"CORRUPT")   # corrupt the live BIOS
        sink = []
        summary = g._cloud_restore(f"cloud:{self.ts}", [{"system": "ps2", "id": "bios/ps2/scph39001.bin"}],
                                   "bios", "20260726T220000", sink.append, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual(self.f.read_bytes(), b"PS2BIOS" * 50, "the cloud BIOS was downloaded + restored")
        self.assertIsNotNone(summary["snapshot"], "rule-5 snapshot of the corrupt live copy")


if __name__ == "__main__":
    unittest.main()
