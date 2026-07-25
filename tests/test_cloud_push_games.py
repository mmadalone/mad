"""cloud.push_games RPC: resolve the selection via the SHARED planner, persist a plan-dir (a NUL src/rel
list + the manifest) under the daemon state dir, then stream deck-cloud.sh push-games.

Locks in the safety-critical contracts (no rclone/network - the shell engine is mocked out):
  * a valid selection persists <state>/games-plan/<ts>/{mad-manifest.json, plan} and streams push-games;
  * an EMPTY or ALL-SKIPPED selection is an EINVAL (so the C++ startCloudOp releases its synchronous
    mRunning guard - an empty {stream} would pin it forever);
  * a concurrent cloud op is EBUSY, and a rejected start does NOT orphan the plan dir;
  * a per-game upload is NOT a restore (so it auto-resumes as an upload, no confirm gate).

Run:  python3 -m unittest tests.test_cloud_push_games -v
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

from lib import backup_manifest as bm                  # noqa: E402
from lib.madsrv import cloud_cmds as cc                 # noqa: E402
from lib.madsrv.rpc import RpcError                     # noqa: E402


def _fake_plan(_items, _cat, _label, ts, emit=None):
    """A canned (manifest, plan) so the RPC test needs no on-disk library."""
    m = bm.new_manifest("granular", created=ts)
    bm.add_item(m, category="roms", category_label="ROMs & games", system="nes", system_label="NES",
                item=bm.make_item(id="nes:smb", name="SMB", src="/live/nes/smb.zip",
                                  rel="roms/nes/smb.zip", size=5))
    plan = [{"id": "nes:smb", "name": "SMB", "system": "nes", "stem": "smb",
             "src": "/live/nes/smb.zip", "rel": "roms/nes/smb.zip", "kind": "file"}]
    return m, plan


class PushGames(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_plan_dir_and_streams(self):
        seen = {}

        def fake_stream_op(argv):
            seen["argv"] = argv
            return {"stream": "s1"}

        with mock.patch.object(cc.granular_backup, "plan_selection", _fake_plan), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_games({"items": [{"system": "nes", "stem": "smb"}]})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-games"])
        ts, plandir = argv[2], Path(argv[3])
        self.assertEqual(plandir, cc._state_dir() / "games-plan" / ts)
        self.assertTrue((plandir / "mad-manifest.json").is_file(), "manifest persisted for the engine")
        self.assertEqual((plandir / "plan").read_bytes(), b"/live/nes/smb.zip\0roms/nes/smb.zip\0",
                         "the NUL src/rel plan is what the shell reads")

    def test_empty_items_einval(self):
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_games({"items": []})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_all_skipped_einval(self):
        with mock.patch.object(cc.granular_backup, "plan_selection",
                               lambda *a, **k: (bm.new_manifest("granular", created="x"), [])):
            with self.assertRaises(RpcError) as cm:
                cc._cloud_push_games({"items": [{"system": "nes", "stem": "ghost"}]})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_concurrent_op_ebusy_does_not_orphan_plan_dir(self):
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_selection", _fake_plan):
                with self.assertRaises(RpcError) as cm:
                    cc._cloud_push_games({"items": [{"system": "nes", "stem": "smb"}]})
            self.assertEqual(cm.exception.code, "EBUSY")
        finally:
            cc._RUN_ACTIVE.release()
        # a rejected start must clean up after itself (no orphaned plan dir under state/games-plan)
        gp = cc._state_dir() / "games-plan"
        leftover = list(gp.iterdir()) if gp.is_dir() else []
        self.assertEqual(leftover, [], "an EBUSY start must not orphan a plan dir")

    def test_push_games_is_not_a_restore_and_has_a_title(self):
        self.assertFalse(cc._is_restore(["push-games", "20260725T000000", "/x/plan"]),
                         "a games upload auto-resumes as an upload, not a restore-confirm")
        self.assertEqual(cc._op_title(["push-games"]), "Backing up games")


if __name__ == "__main__":
    unittest.main()
