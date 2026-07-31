"""cloud.push_games RPC: resolve the selection via the SHARED planner, persist a plan-dir (a NUL src/rel
list + the manifest) under the daemon state dir, then stream deck-cloud.sh push-games.

Locks in the safety-critical contracts (no rclone/network - the shell engine is mocked out):
  * a valid selection persists <state>/games-plan/<ts>/{mad-manifest.json, plan} and streams push-games;
  * an EMPTY or ALL-SKIPPED selection is an EINVAL (so the C++ startCloudOp releases its synchronous
    mRunning guard - an empty {stream} would pin it forever);
  * a concurrent cloud op QUEUES (and keeps its plan dir for the dispatcher), rather than being
    refused - and a queued push does no network work, because the merge waits for dispatch;
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

        # _run rc 3 = rclone "not found" -> no remote set yet, so the push writes a FRESH manifest.
        # (A transport failure rc - 1/5/... - deliberately ABORTS rather than clobber the set index.)
        with mock.patch.object(cc.granular_backup, "plan_selection", _fake_plan), \
             mock.patch.object(cc, "_run", lambda *a, **k: (3, "", "")), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_games({"items": [{"system": "nes", "stem": "smb"}]})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-games"])
        # argv[2] = the FIXED remote token (the single non-versioned games set), NOT the plan-dir id.
        self.assertEqual(argv[2], "games")
        plandir = Path(argv[3])
        self.assertEqual(plandir.parent, cc._state_dir() / "games-plan")  # a UNIQUE ts plan-dir id
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

    def test_a_busy_engine_queues_the_push_and_keeps_its_plan(self):
        """Queueing replaced rejection (user 2026-07-31): a second backup WAITS its turn instead of
        being refused. Its plan dir must SURVIVE - the dispatcher hands that exact directory to the
        engine when its turn comes, so deleting it here would queue a job that cannot run."""
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_selection", _fake_plan):
                out = cc._cloud_push_games({"items": [{"system": "nes", "stem": "smb"}]})
        finally:
            cc._RUN_ACTIVE.release()
        self.assertIn("queued", out, f"expected a queued reply, got {out}")
        self.assertGreaterEqual(out["position"], 1)
        pd = cc._state_dir() / "games-plan"
        self.assertTrue(pd.is_dir() and list(pd.iterdir()),
                        "the queued job's plan dir must be kept for the dispatcher")

    def test_a_queued_push_does_not_touch_the_network(self):
        """The remote-manifest merge is DEFERRED to dispatch, and this is why: the index to merge
        against is the one that will be on MEGA when the job runs, not the one there now. Two pushes
        to the same fixed set queued together would otherwise both merge against today's index, and
        the second would publish one with no trace of the first's files."""
        ran = []
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_selection", _fake_plan), \
                 mock.patch.object(cc, "_run", lambda *a, **k: (ran.append(a), (1, "", "boom"))[1]):
                out = cc._cloud_push_games({"items": [{"system": "nes", "stem": "smb"}]})
        finally:
            cc._RUN_ACTIVE.release()
        self.assertIn("queued", out)
        self.assertEqual(ran, [], "no fetch while queued - it happens when the job is dispatched")

    def test_push_games_is_not_a_restore_and_has_a_title(self):
        self.assertFalse(cc._is_restore(["push-games", "20260725T000000", "/x/plan"]),
                         "a games upload auto-resumes as an upload, not a restore-confirm")
        self.assertEqual(cc._op_title(["push-games"]), "Backing up games")


if __name__ == "__main__":
    unittest.main()
