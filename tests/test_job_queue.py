"""The QUEUED job state: a backup asked for while another is running waits its turn.

A queued job is a registry record with NO process behind it, which is exactly what makes it
dangerous. The contracts pinned here are the ones that bite:

  * it is NOT in LIVE, so _reap_locked (which fails any LIVE job whose pid is gone) leaves it alone -
    otherwise a queued job would be marked failed within REAP_GRACE_S of being queued;
  * it is NOT TERMINAL, so pruning keeps it;
  * signalable() refuses it OUTRIGHT. pgid is None, `int(None or 0)` is 0, and killpg(0, sig) means
    "signal the CALLER's process group" - which is ES-DE's. A queued job is the one job kind with no
    group at all, so it must never reach a signal; cancel it with dequeue();
  * live_jobs() does not report it, so the dispatcher asking "is anything running?" is not blocked by
    the very job it is about to start;
  * order is queue_pos, reorder() swaps neighbours, and start_queued() is an atomic
    queued -> running transition that refuses a job cancelled while it was being spawned.

Run:  python3 -m unittest tests.test_job_queue -v
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

from lib import job_registry as jr   # noqa: E402


class JobQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobq-"))
        self._saved = os.environ.get("DECK_CLOUD_STATE_DIR")
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        else:
            os.environ["DECK_CLOUD_STATE_DIR"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the dangerous ones -------------------------------------------------

    def test_a_queued_job_is_never_signalable(self):
        """pgid is None -> int(None or 0) == 0 -> killpg(0) would signal OUR OWN group, i.e. ES-DE."""
        jid = jr.enqueue("push-games", argv=["push-games", "games", "/plan"])
        job = jr.get(jid)
        self.assertIsNone(job["pgid"])
        self.assertFalse(jr.signalable(job), "a job with no process must never be signalled")

    def test_pause_and_stop_send_no_signal_for_a_queued_job(self):
        """These return the record either way, so the property that matters is that no signal is
        DELIVERED - with pgid None the call would have been killpg(0), i.e. our own group."""
        jid = jr.enqueue("push-games")
        sent = []
        with mock.patch.object(jr, "_signal_job", lambda job, sig: sent.append(sig) or True):
            jr.pause_job(jid)
            jr.stop_job(jid)
        self.assertEqual(sent, [], "a queued job has no process group to signal")
        self.assertEqual(jr.get(jid)["state"], jr.QUEUED, "and it stays queued, not failed")

    def test_reaping_leaves_a_queued_job_alone(self):
        """The reaper fails LIVE jobs whose pid is gone. A queued job has no pid at all and must
        survive - it is waiting, not dead."""
        jid = jr.enqueue("push-games")
        os.utime(jr.jobs_dir() / f"{jid}.json", (0, 0))   # far older than REAP_GRACE_S
        jr.list_jobs()                                    # reap pass
        self.assertEqual(jr.get(jid)["state"], jr.QUEUED)

    def test_pruning_keeps_a_queued_job(self):
        jid = jr.enqueue("push-games")
        for i in range(jr.KEEP_TERMINAL + 5):             # flood with terminal jobs
            t = jr.begin("push-bios", os.getpid(), argv=["push-bios"])
            jr.end(t, 0)
        jr.list_jobs()
        self.assertIsNotNone(jr.get(jid), "a waiting job is not old news to be pruned")

    def test_live_jobs_excludes_queued(self):
        """The dispatcher asks live_jobs() before starting the head; counting the head as live would
        deadlock the queue against itself."""
        jr.enqueue("push-games")
        self.assertEqual(jr.live_jobs(), [])

    # ---- ordering -----------------------------------------------------------

    def test_queue_order_is_fifo(self):
        ids = [jr.enqueue("push-games", argv=["push-games", str(i)]) for i in range(3)]
        self.assertEqual([j["id"] for j in jr.queued_jobs()], ids)

    def test_reorder_swaps_with_the_neighbour(self):
        a, b, c = (jr.enqueue("push-games", argv=[str(i)]) for i in range(3))
        self.assertTrue(jr.reorder(c, -1))
        self.assertEqual([j["id"] for j in jr.queued_jobs()], [a, c, b])
        self.assertTrue(jr.reorder(c, +1))
        self.assertEqual([j["id"] for j in jr.queued_jobs()], [a, b, c])

    def test_reorder_at_the_ends_is_refused_not_silently_ignored(self):
        a, b = jr.enqueue("push-games"), jr.enqueue("push-bios")
        self.assertFalse(jr.reorder(a, -1), "already first")
        self.assertFalse(jr.reorder(b, +1), "already last")
        self.assertEqual([j["id"] for j in jr.queued_jobs()], [a, b], "and nothing moved")

    def test_reorder_refuses_a_job_that_is_not_queued(self):
        rid = jr.begin("push-games", os.getpid(), argv=["push-games"])
        self.assertFalse(jr.reorder(rid, -1), "a RUNNING job is not part of the queue")

    # ---- transitions --------------------------------------------------------

    def test_dequeue_removes_it_and_returns_the_record(self):
        jid = jr.enqueue("push-games", plan_dir="/tmp/plan-x")
        rec = jr.dequeue(jid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["plan_dir"], "/tmp/plan-x", "the caller needs this to delete the plan")
        self.assertIsNone(jr.get(jid))
        self.assertEqual(jr.queued_jobs(), [])

    def test_dequeue_refuses_a_running_job(self):
        rid = jr.begin("push-games", os.getpid(), argv=["push-games"])
        self.assertIsNone(jr.dequeue(rid), "a started job must be stopped, not forgotten")
        self.assertIsNotNone(jr.get(rid))

    def test_start_queued_makes_it_running_with_a_real_process(self):
        jid = jr.enqueue("push-games", argv=["push-games", "games", "/plan"])
        job = jr.start_queued(jid, os.getpid())
        self.assertIsNotNone(job)
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["pid"], os.getpid())
        self.assertEqual(job["pgid"], os.getpgid(os.getpid()))
        self.assertIsNotNone(job["starttime"], "the pid-reuse guard must be captured on start")
        self.assertEqual(jr.queued_jobs(), [], "it left the queue")
        self.assertEqual([j["id"] for j in jr.live_jobs()], [jid])

    def test_start_queued_refuses_a_job_cancelled_mid_spawn(self):
        """The dispatcher spawns, then transitions. If the user cancelled in between, the transition
        must fail so the caller knows to kill the process it just started."""
        jid = jr.enqueue("push-games")
        jr.dequeue(jid)
        self.assertIsNone(jr.start_queued(jid, os.getpid()))

    def test_start_queued_is_not_repeatable(self):
        jid = jr.enqueue("push-games")
        self.assertIsNotNone(jr.start_queued(jid, os.getpid()))
        self.assertIsNone(jr.start_queued(jid, os.getpid()), "a second dispatch would double-run it")

    def test_a_queued_job_survives_a_restart(self):
        """Persistence is the whole point: the queue lives in the registry, so closing the panel or
        restarting the daemon does not silently drop work the user asked for."""
        jid = jr.enqueue("push-games", argv=["push-games", "games", "/plan"], plan_dir="/plan")
        again = jr.get(jid)   # a fresh read from disk, as a new process would do
        self.assertEqual(again["state"], jr.QUEUED)
        self.assertEqual(again["argv"], ["push-games", "games", "/plan"])
        self.assertEqual(again["plan_dir"], "/plan")


if __name__ == "__main__":
    unittest.main()
