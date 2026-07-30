"""The transfer QUEUE dispatcher: a second backup waits instead of being refused.

The hazards this pins are the ones the design exists to avoid, each of which is silent if wrong:

  * a queued job NEVER writes the interrupted-transfer marker. There is exactly ONE marker file, so a
    queued job writing it would either clobber the running job's or, after a restart, make auto-resume
    fire the queued op DIRECTLY and bypass the queue. The marker is written at dispatch;
  * the remote-manifest merge happens at DISPATCH, not at enqueue. Two pushes to the same fixed set
    queued back to back would otherwise both merge against the state they saw when queued, and the
    second would publish an index with no trace of the first's files - bytes on MEGA, no record;
  * dispatch_queue() holds _RUN_ACTIVE across check AND spawn, because deck-cloud.sh's push-precious
    and prune take the engine lock with `flock -n` and SILENTLY exit 0 when they cannot get it, which
    auto-resume reads as a clean finish;
  * a transport failure on the deferred merge FAILS the job visibly instead of uploading an index that
    would hide what is already there;
  * cancelling a waiting job drops its plan dir (nothing else will) and sends no signal.

Run:  python3 -m unittest tests.test_queue_dispatch -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm          # noqa: E402
from lib import job_registry as jr             # noqa: E402
from lib.madsrv import cloud_cmds as cc        # noqa: E402
from lib.madsrv.rpc import RpcError            # noqa: E402


def _manifest(dirpath: Path, game: str):
    m = bm.new_manifest("granular", created="20260731T010000")
    bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                item=bm.make_item(id=f"nes:{game}", name=game, src=f"/live/{game}",
                                  rel=f"roms/nes/{game}.zip", kind="file", size=1))
    bm.write(m, bm.manifest_path(dirpath))
    return m


class QueueDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qdisp-"))
        self._saved = os.environ.get("DECK_CLOUD_STATE_DIR")
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp)
        # a stub "engine" that just exits 0 - we assert on WHAT was spawned, not on rclone
        self.engine = self.tmp / "engine.sh"
        self.engine.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.engine.chmod(0o755)
        self._eng = mock.patch.object(cc, "ENGINE", self.engine)
        self._eng.start()
        self._free_lock()

    @staticmethod
    def _free_lock():
        """_stream_op hands _RUN_ACTIVE to the tail stream it starts, so a test that actually ran an
        op can leave it held. Release it so the next test's dispatch is not blocked by the previous."""
        while True:
            if cc._RUN_ACTIVE.acquire(blocking=False):
                cc._RUN_ACTIVE.release()
                return
            try:
                cc._RUN_ACTIVE.release()
            except RuntimeError:
                return

    def _queue(self, *args, merge_cmd="", plan_dir=""):
        """Enqueue directly - the dispatcher is the unit under test here, and _stream_op only queues
        when something is already running."""
        return cc._enqueue_op([str(self.engine), *args], merge_cmd=merge_cmd, plan_dir=plan_dir)

    def tearDown(self):
        self._free_lock()
        self._eng.stop()
        if self._saved is None:
            os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        else:
            os.environ["DECK_CLOUD_STATE_DIR"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _live(self, kind="push-precious"):
        """A detached cloud job that looks alive (our own pid, so _alive() holds)."""
        return jr.begin(kind, os.getpid(), argv=[kind], detached=True)

    # ---- enqueue vs run now -------------------------------------------------

    def test_busy_enqueues_instead_of_raising(self):
        self._live()
        out = cc._stream_op([str(self.engine), "push-games", "games", "/p"], queue_if_busy=True)
        self.assertIn("queued", out, f"expected a queued reply, got {out}")
        self.assertEqual(out["position"], 1)
        self.assertEqual(len(jr.queued_jobs()), 1)

    def test_without_the_flag_it_still_raises_ebusy(self):
        """Every pre-existing caller keeps the one-at-a-time contract."""
        self._live()
        with self.assertRaises(RpcError) as cm:
            cc._stream_op([str(self.engine), "push-games", "games", "/p"])
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_a_queued_job_writes_no_marker(self):
        """ONE marker file exists. A queued job writing it would clobber the running job's, and after
        a restart auto-resume would fire the queued op directly, bypassing the queue."""
        self._live()
        cc._clear_marker()
        cc._stream_op([str(self.engine), "push-games", "games", "/p"], queue_if_busy=True)
        self.assertIsNone(cc._read_marker(), "the marker belongs to the RUNNING op only")

    # ---- dispatch -----------------------------------------------------------

    def test_dispatch_is_a_noop_while_something_runs(self):
        self._live()
        cc._stream_op([str(self.engine), "push-games", "games", "/p"], queue_if_busy=True)
        self.assertEqual(cc.dispatch_queue(), "", "the engine is busy")
        self.assertEqual(len(jr.queued_jobs()), 1, "and the job is still waiting")

    def test_dispatch_starts_the_head_and_writes_the_marker(self):
        cc._clear_marker()
        self._queue("push-games", "games", "/p")
        started = cc.dispatch_queue()
        self.assertTrue(started)
        job = jr.get(started)
        self.assertEqual(job["state"], "running")
        self.assertIsNotNone(job["pid"])
        self.assertEqual(cc._read_marker(), ["push-games", "games", "/p"],
                         "the marker is written when the op actually starts")
        self.assertEqual(jr.queued_jobs(), [])

    def test_dispatch_runs_them_in_order(self):
        ids = [self._queue("push-games", f"set{i}", "/p")["queued"] for i in range(2)]
        first = cc.dispatch_queue()
        self.assertEqual(first, ids[0])
        jr.end(first, 0)                       # the head finishes
        self.assertEqual(cc.dispatch_queue(), ids[1])

    def test_reorder_changes_what_dispatches_first(self):
        a = self._queue("push-games", "a", "/p")["queued"]
        b = self._queue("push-games", "b", "/p")["queued"]
        cc._transfers_reorder({"id": b, "direction": "up"})
        self.assertEqual(cc.dispatch_queue(), b, "the promoted job runs first")
        self.assertEqual([j["id"] for j in jr.queued_jobs()], [a])

    # ---- the deferred merge -------------------------------------------------

    def test_the_manifest_merge_happens_at_dispatch_not_at_enqueue(self):
        """Two pushes to the same fixed set: the second must merge against what the FIRST published,
        which is only knowable when it runs."""
        pd = self.tmp / "games-plan" / "p1"
        pd.mkdir(parents=True)
        _manifest(pd, "second")
        (pd / "plan").write_bytes(b"")
        calls = []

        def fake_run(args, **kw):
            calls.append(list(args))
            return (3, "", "")             # rclone "not found" -> no existing set yet

        with mock.patch.object(cc, "_run", fake_run):
            self._live()                                     # busy -> must queue
            cc._stream_op([str(self.engine), "push-games", "games", str(pd)],
                          queue_if_busy=True, merge_cmd="cat-manifest", plan_dir=str(pd))
            self.assertEqual(calls, [], "NO merge while the job is only queued")
            jr.end(jr.live_jobs()[0]["id"], 0)               # the running op finishes
            cc.dispatch_queue()
        self.assertEqual(calls, [["cat-manifest", "games"]], "merged exactly once, at dispatch")

    def test_a_transport_failure_on_the_deferred_merge_fails_the_job(self):
        """Rather than upload an index that would hide everything already in the set."""
        pd = self.tmp / "games-plan" / "p2"
        pd.mkdir(parents=True)
        _manifest(pd, "x")
        (pd / "plan").write_bytes(b"")
        jid = self._queue("push-games", "games", str(pd),
                          merge_cmd="cat-manifest", plan_dir=str(pd))["queued"]
        with mock.patch.object(cc, "_run", lambda *a, **k: (5, "", "network down")):
            self.assertEqual(cc.dispatch_queue(), "", "nothing started")
        self.assertEqual(jr.get(jid)["state"], "failed")
        self.assertIn("hide", jr.out_path(jid).read_text(), "the reason is visible in the tile")

    # ---- cancel -------------------------------------------------------------

    def test_cancelling_a_waiting_job_drops_it_and_its_plan_dir(self):
        pd = self.tmp / "games-plan" / "p3"
        pd.mkdir(parents=True)
        (pd / "plan").write_bytes(b"")
        self._live()
        out = cc._stream_op([str(self.engine), "push-games", "games", str(pd)],
                            queue_if_busy=True, plan_dir=str(pd))
        res = cc._transfers_cancel({"id": out["queued"]})
        self.assertTrue(res["cancelled"])
        self.assertTrue(res["was_queued"])
        self.assertIsNone(jr.get(out["queued"]))
        self.assertFalse(pd.exists(), "nothing else would ever delete this plan dir")

    def test_cancelling_a_waiting_job_leaves_the_running_one_alone(self):
        running = self._live()
        out = cc._stream_op([str(self.engine), "push-games", "games", "/p"], queue_if_busy=True)
        cc._transfers_cancel({"id": out["queued"]})
        self.assertEqual(jr.get(running)["state"], "running")

    def test_reorder_refuses_a_running_job(self):
        running = self._live()
        with self.assertRaises(RpcError) as cm:
            cc._transfers_reorder({"id": running, "direction": "up"})
        self.assertEqual(cm.exception.code, "EINVAL")

    # ---- the rows the tile draws -------------------------------------------

    def test_dispatched_children_are_reaped(self):
        """An unreaped child is a ZOMBIE, and a zombie's /proc entry keeps its original starttime, so
        job_registry._alive() calls it RUNNING. An engine killed without running its EXIT trap would
        then look alive for ever and the queue would stall permanently."""
        self._queue("push-games", "games", "/p")
        started = cc.dispatch_queue()
        self.assertTrue(started)
        proc = cc._DISPATCHED[-1]
        proc.wait(timeout=10)                       # the stub engine exits immediately
        cc._reap_dispatched()
        self.assertEqual(cc._DISPATCHED, [], "finished children must not accumulate")

    def test_queued_rows_carry_a_position_and_no_progress(self):
        self._live()
        cc._stream_op([str(self.engine), "push-games", "games", "/p"], queue_if_busy=True)
        rows = {r["state"]: r for r in cc._transfers_list({})["jobs"]}
        self.assertIn(jr.QUEUED, rows)
        self.assertNotIn("pct", rows[jr.QUEUED], "a waiting job has no progress to report")
        self.assertGreaterEqual(rows[jr.QUEUED]["position"], 1)


if __name__ == "__main__":
    unittest.main()
