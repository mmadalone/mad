"""Cloud emulator-config backup + restore (P7-2): the CLOUD parity of the local emucfg backup/restore, using
a SEPARATE remote base (emucfg-backups/<ts>) so an emulator-config set never cross-lists in the game / BIOS /
ES-DE cloud restore.

PushEmucfg (CI-safe, the shell engine is mocked): cloud.push_emucfg resolves the selection via the SHARED
planner granular_backup.plan_emucfg, persists a plan-dir under <state>/emucfg-plan/<ts>, and streams
deck-cloud.sh push-emucfg (its OWN subcommand + plan_root). emucfg is VERSIONED, so the remote token is the
timestamp (not a fixed name). Locks: valid selection persists+streams; empty/all-skipped is EINVAL; a
concurrent op is EBUSY without orphaning the plan dir; an emucfg upload is not a restore.

CloudEmucfg (Deck-only, needs rclone): a real round-trip against a FAKE $HOME via DECK_CLOUD_EMUCFG_BASE_
OVERRIDE -> push-emucfg -> list-emucfg -> cat-emucfg-manifest -> fetch-emucfg -> restore_selection(category=
"emucfg") lands the file with a rule-5 snapshot. Proves SEPARATION: the set is listed by emucfg.cloud_sources
but NOT by granular/bios/esde cloud sources.

Run:  python3 -m unittest tests.test_cloud_emucfg -v
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
from lib import granular_backup as gb, emu_map           # noqa: E402
from lib.madsrv import cloud_cmds as cc                    # noqa: E402
from lib.madsrv import granular_cmds as g                  # noqa: E402
from lib.madsrv.rpc import RpcError                        # noqa: E402


def _fake_emucfg_plan(_items, ts, emit=None, is_stopped=None):
    """A canned (manifest, plan) for one PCSX2 config file (no on-disk tree needed for the RPC contract)."""
    rel = "emucfg/.config/PCSX2/inis/PCSX2.ini"
    m = bm.new_manifest("granular", created=ts)
    bm.add_item(m, category="emucfg", category_label="Emulator config & data", system="pcsx2",
                system_label="PCSX2",
                item=bm.make_item(id=rel, name="PCSX2.ini", src="/home/deck/" + rel[len("emucfg/"):],
                                  rel=rel, kind="file", size=9, extra={"group": "config"}))
    plan = [{"id": rel, "name": "PCSX2.ini", "system": "pcsx2",
             "src": "/home/deck/" + rel[len("emucfg/"):], "rel": rel, "kind": "file"}]
    return m, plan


class PushEmucfg(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_plan_dir_and_streams_push_emucfg(self):
        seen = {}

        def fake_stream_op(argv, **_kw):   # **_kw: _persist_games_plan_and_stream now
            # passes queue_if_busy/merge_cmd/plan_dir, and a fake with a rigid
            # signature would break on any future one too.
            seen["argv"] = argv
            return {"stream": "s1"}

        items = [{"emulator": "pcsx2", "group": "config", "rel": "emucfg/.config/PCSX2/inis/PCSX2.ini"}]
        with mock.patch.object(cc.granular_backup, "plan_emucfg", _fake_emucfg_plan), \
             mock.patch.object(cc, "_run", lambda *a, **k: (1, "", "")), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_emucfg({"items": items})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-emucfg"])
        # VERSIONED: the remote token is the timestamp (not a fixed "bios"/"games" name).
        self.assertTrue(g._safe_ts(argv[2]), f"remote token is a dated ts, got {argv[2]!r}")
        plandir = Path(argv[3])
        self.assertEqual(plandir.parent, cc._state_dir() / "emucfg-plan")
        self.assertTrue((plandir / "mad-manifest.json").is_file())

    def test_empty_items_einval(self):
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_emucfg({"items": []})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_nothing_present_einval(self):
        with mock.patch.object(cc.granular_backup, "plan_emucfg",
                               lambda *a, **k: (bm.new_manifest("granular", created="x"), [])):
            with self.assertRaises(RpcError) as cm:
                cc._cloud_push_emucfg({"items": [{"emulator": "pcsx2", "group": "config",
                                                  "rel": "emucfg/.config/PCSX2/x"}]})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_a_busy_engine_queues_the_push_and_keeps_its_plan(self):
        """Queueing replaced rejection (user 2026-07-31): a second backup WAITS its turn instead of
        being refused. Its plan dir must SURVIVE - the dispatcher hands that exact directory to the
        engine when its turn comes, so deleting it here would queue a job that cannot run."""
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_emucfg", _fake_emucfg_plan):
                out = cc._cloud_push_emucfg({"items": [{"emulator": "pcsx2", "group": "config",
                                                        "rel": "emucfg/.config/PCSX2/inis/PCSX2.ini"}]})
        finally:
            cc._RUN_ACTIVE.release()
        self.assertIn("queued", out, f"expected a queued reply, got {out}")
        self.assertGreaterEqual(out["position"], 1)
        pd = cc._state_dir() / "emucfg-plan"
        self.assertTrue(pd.is_dir() and list(pd.iterdir()),
                        "the queued job's plan dir must be kept for the dispatcher")

    def test_emucfg_upload_is_not_a_restore(self):
        self.assertFalse(cc._is_restore(["push-emucfg", "20260728T000000", "/x/plan"]))
        self.assertEqual(cc._op_title(["push-emucfg"]), "Backing up emulator config")


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class CloudEmucfg(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.home = self.base / "home"
        (self.home / ".config/PCSX2/inis").mkdir(parents=True)
        self.f = self.home / ".config/PCSX2/inis/PCSX2.ini"
        self.f.write_bytes(b"PCSX2CFG" * 20)
        self.rel = "emucfg/.config/PCSX2/inis/PCSX2.ini"
        self._env = {"DECK_CLOUD_RCLONE": str(BIN / "rclone"), "DECK_CLOUD_SKIP_CONNCHECK": "1",
                     "DECK_CLOUD_NO_NICE": "1", "DECK_CLOUD_STATE_DIR": str(self.base / "state"),
                     "DECK_CLOUD_EMUCFG_BASE_OVERRIDE": str(self.base / "emucfg-remote"),
                     "DECK_CLOUD_GAMES_BASE_OVERRIDE": str(self.base / "games-remote"),
                     "DECK_CLOUD_BIOS_BASE_OVERRIDE": str(self.base / "bios-remote"),
                     "DECK_CLOUD_ESDE_BASE_OVERRIDE": str(self.base / "esde-remote")}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._patches = [
            mock.patch.object(Path, "home", staticmethod(lambda: self.home)),
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.base / "ROMs"),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
            mock.patch.object(gb.proc_guard, "emulator_running", lambda name: False),
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
        ts = "20260728T210000"
        manifest, plan = gb.plan_emucfg(
            [{"emulator": "pcsx2", "group": "config", "rel": self.rel}], ts)
        self.assertTrue(plan, "the fake-home PCSX2.ini resolved")
        pd = self.base / "plan"; pd.mkdir()
        bm.write(manifest, bm.manifest_path(pd))
        (pd / "plan").write_bytes(
            b"".join(e["src"].encode() + b"\0" + e["rel"].encode() + b"\0" for e in plan))
        r = subprocess.run([str(CLOUD), "push-emucfg", ts, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return ts

    def test_emucfg_cloud_sources_lists_the_set(self):
        out = g._emucfg_cloud_sources({})
        self.assertTrue(out["connected"])
        self.assertIsNotNone({s["id"]: s for s in out["sources"]}.get(f"cloud:{self.ts}"))

    def test_emucfg_set_is_separate_from_games_bios_esde(self):
        for fn in (g._granular_cloud_sources, g._bios_cloud_sources, g._esde_cloud_sources):
            ids = {s["id"] for s in fn({})["sources"]}
            self.assertNotIn(f"cloud:{self.ts}", ids,
                             f"an emucfg set must NOT cross-list in {fn.__name__}")

    def test_emucfg_cloud_browse(self):
        sysr = g._emucfg_systems({"source": f"cloud:{self.ts}"})
        self.assertIn("pcsx2", {s["key"] for s in sysr["systems"]})
        filesr = g._emucfg_files({"source": f"cloud:{self.ts}", "emulator": "pcsx2"})
        rels = {f["rel"] for grp in filesr["groups"] for f in grp["files"]}
        self.assertIn(self.rel, rels)

    def test_emucfg_cloud_preview_reads_the_right_base(self):
        # REVIEW #1: restore_preview must read the emucfg base (not games) for a cloud: source, so the
        # replace-warning is correct. Before the emucfg flag fix this ENOENT'd on GAMES_BASE.
        pv = g._granular_restore_preview({"source": f"cloud:{self.ts}", "category": "emucfg",
                                          "items": [{"system": "pcsx2", "id": self.rel}]})
        self.assertEqual([r["id"] for r in pv["replace"]], [self.rel],
                         "the live config exists -> flagged as a REPLACE from the emucfg cloud manifest")

    def test_emucfg_cloud_restore_downloads_and_restores_with_rule5(self):
        self.f.write_bytes(b"CORRUPT")
        summary = g._cloud_restore(f"cloud:{self.ts}", [{"system": "pcsx2", "id": self.rel}],
                                   "emucfg", "20260728T220000", lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual(self.f.read_bytes(), b"PCSX2CFG" * 20, "the cloud emucfg was downloaded + restored")
        self.assertIsNotNone(summary["snapshot"], "rule-5 snapshot of the corrupt live copy")


if __name__ == "__main__":
    unittest.main()
