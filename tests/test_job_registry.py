"""lib/job_registry - the persistent transfer-job registry.

Pins: the begin/end lifecycle (atomic JSON + .out), stale reaping via the /proc
starttime pid-reuse guard, terminal pruning (with .out files), SIGSTOP/SIGCONT
pause/resume against REAL processes, the gameplay freeze/thaw semantics (user-paused
jobs untouched in both directions), reconcile's crash safety, and stop_job's
CONT+TERM ladder. Everything runs against a tmp DECK_CLOUD_STATE_DIR and throwaway
`sleep` children in their own sessions - no real state is touched.

Run: python3 -m unittest tests.test_job_registry -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lib import job_registry as jr


def _proc_state(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw.rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return "?"


def _await_proc_state(pid: int, wanted, timeout: float = 3.0) -> str:
    """Wait (briefly) for a process to REACH one of `wanted`.

    A signal is asynchronous: SIGSTOP is delivered, then the kernel schedules the target and only
    then does /proc report 'T'. Reading immediately is a race that only loses under load - which is
    exactly when the full suite runs it. Returns the last state seen, so a failure still reports what
    it actually was."""
    if isinstance(wanted, str):
        wanted = (wanted,)
    deadline = time.monotonic() + timeout
    state = _proc_state(pid)
    while state not in wanted and time.monotonic() < deadline:
        time.sleep(0.02)
        state = _proc_state(pid)
    return state


def _sleeper():
    """A detached `sleep` in its OWN session (like a real detached transfer)."""
    return subprocess.Popen(["sleep", "300"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp)
        self._procs = []

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        for p in self._procs:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sleeper(self):
        p = _sleeper()
        self._procs.append(p)
        return p


class Lifecycle(Base):
    def test_begin_list_end(self):
        p = self.sleeper()
        jid = jr.begin("push-precious", p.pid, argv=["push-precious", "--force"],
                       source="hook")
        job = jr.get(jid)
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["kind"], "push-precious")
        self.assertEqual(job["title"], "Backing up saves")      # TITLES map
        self.assertEqual(job["pgid"], p.pid)                    # own session => pgid == pid
        self.assertEqual(job["starttime"], jr.starttime_of(p.pid))
        self.assertTrue(jr.out_path(jid).exists())
        jr.end(jid, 0)
        self.assertEqual(jr.get(jid)["state"], "done")
        jid2 = jr.begin("sync-library", self.sleeper().pid)
        jr.end(jid2, 143)
        self.assertEqual(jr.get(jid2)["state"], "failed")
        self.assertEqual(jr.get(jid2)["rc"], 143)

    def test_backend_id_is_honoured(self):
        p = self.sleeper()
        jid = jr.begin("push-games", p.pid, job_id="20260101T000000-77")
        self.assertEqual(jid, "20260101T000000-77")

    def test_new_id_unique_across_threads(self):
        # new_id() is reached from the RPC pool, stream threads and the auto-resume
        # thread at once; same second + same pid means uniqueness rests on _SEQ alone.
        n_threads, n_ids = 8, 250
        barrier = threading.Barrier(n_threads)
        buckets = [[] for _ in range(n_threads)]

        def worker(bucket):
            barrier.wait()
            for _ in range(n_ids):
                bucket.append(jr.new_id())

        threads = [threading.Thread(target=worker, args=(b,)) for b in buckets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = [i for b in buckets for i in b]
        self.assertEqual(len(set(ids)), n_threads * n_ids)


class Reaping(Base):
    def test_dead_process_is_reaped_to_failed(self):
        p = self.sleeper()
        jid = jr.begin("push-precious", p.pid)
        os.killpg(p.pid, 9)
        p.wait(timeout=5)
        with mock.patch.object(jr, "REAP_GRACE_S", 0):   # skip the rc-in-flight grace
            jobs = {j["id"]: j for j in jr.list_jobs()}
        self.assertEqual(jobs[jid]["state"], "failed")

    def test_fresh_records_get_a_reap_grace(self):
        # The rc may still be in flight (engine trap / spawner) right after death -
        # a just-touched record must NOT be insta-reaped to a -1 failure.
        p = self.sleeper()
        jid = jr.begin("push-precious", p.pid)
        os.killpg(p.pid, 9)
        p.wait(timeout=5)
        jobs = {j["id"]: j for j in jr.list_jobs()}      # default grace
        self.assertEqual(jobs[jid]["state"], "running")

    def test_pid_reuse_is_caught_by_starttime(self):
        # A job whose recorded starttime does not match the live pid's is DEAD, even
        # though a process with that pid exists (our own).
        jid = jr.begin("push-precious", os.getpid())
        jr.update(jid, starttime=(jr.get(jid)["starttime"] or 0) + 1)
        with mock.patch.object(jr, "REAP_GRACE_S", 0):
            jobs = {j["id"]: j for j in jr.list_jobs()}
        self.assertEqual(jobs[jid]["state"], "failed")

    def test_prune_keeps_the_newest_terminal_jobs(self):
        for i in range(jr.KEEP_TERMINAL + 3):
            jid = jr.begin("push-precious", os.getpid(), job_id=f"20260101T{i:06d}-1")
            jr.end(jid, 0)
        jobs = jr.list_jobs()
        self.assertEqual(len(jobs), jr.KEEP_TERMINAL)
        # the OLDEST were pruned, json + out
        self.assertFalse(jr.out_path("20260101T000000-1").exists())
        self.assertIsNone(jr.get("20260101T000000-1"))


class PauseResume(Base):
    def test_pause_and_resume_signal_the_group(self):
        p = self.sleeper()
        jid = jr.begin("sync-library", p.pid)
        job = jr.pause_job(jid)
        self.assertEqual(job["state"], "paused")
        self.assertEqual(job["paused_by"], "user")
        time.sleep(0.1)
        self.assertEqual(_await_proc_state(p.pid, "T"), "T")
        job = jr.resume_job(jid)
        self.assertEqual(job["state"], "running")
        time.sleep(0.1)
        self.assertIn(_await_proc_state(p.pid, ("S", "R")), ("S", "R"))

    def test_in_process_jobs_are_not_pausable(self):
        jid = jr.begin("granular", os.getpid(), detached=False)
        job = jr.pause_job(jid)
        self.assertEqual(job["state"], "running")   # refused: would SIGSTOP the daemon


class OwnGroupIsNeverSignalled(Base):
    """THE safety invariant. An in-daemon job's pgid is the mad-backend daemon's group,
    which IS ES-DE's own group (MadBackend forks python3 with no setsid), so signalling
    it by group would freeze or KILL the frontend. Every path must refuse it - a stop is
    the dangerous one (SIGTERM), and it used to lack the guard pause had."""

    def test_signalable_refuses_our_own_group(self):
        jid = jr.begin("granular", os.getpid(), detached=False)
        self.assertFalse(jr.signalable(jr.get(jid)))
        # ...and also a job that CLAIMS detached but shares our group (a child spawned
        # without start_new_session, e.g. the granular cloud-restore fetch before the fix).
        jid2 = jr.begin("fetch-games", os.getpid(), detached=True)
        self.assertFalse(jr.signalable(jr.get(jid2)))

    def test_stop_refuses_an_in_process_job(self):
        jid = jr.begin("granular", os.getpid(), detached=False)
        job = jr.stop_job(jid, grace=0.2)
        self.assertEqual(job["state"], "running")   # NOT SIGTERMed - we are still alive
        self.assertIsNone(job["rc"])

    def test_gameplay_freeze_skips_our_own_group(self):
        jr.begin("granular", os.getpid(), detached=False)
        self.assertEqual(jr.pause_all_gameplay(), 0)   # never freeze ES-DE at game start

    def test_deprioritize_skips_our_own_group(self):
        # renice is one-way for non-root: a hit would permanently idle the frontend.
        jr.begin("granular", os.getpid(), detached=False)
        self.assertEqual(jr.deprioritize_running(), 0)

    def test_a_real_detached_job_is_still_controllable(self):
        p = self.sleeper()                            # its own session
        jid = jr.begin("sync-library", p.pid)
        self.assertTrue(jr.signalable(jr.get(jid)))
        self.assertEqual(jr.pause_job(jid)["state"], "paused")


class Gameplay(Base):
    def test_freeze_thaw_cycle_leaves_user_pauses_alone(self):
        run = self.sleeper()
        usr = self.sleeper()
        j_run = jr.begin("push-precious", run.pid)
        j_usr = jr.begin("sync-library", usr.pid)
        jr.pause_job(j_usr)                          # paused_by=user
        self.assertEqual(jr.pause_all_gameplay(), 1)  # only the running one
        self.assertEqual(jr.get(j_run)["paused_by"], "gameplay")
        self.assertEqual(jr.get(j_usr)["paused_by"], "user")
        self.assertEqual(_await_proc_state(run.pid, "T"), "T")
        self.assertEqual(jr.resume_gameplay(), 1)     # thaws EXACTLY the gameplay one
        self.assertEqual(jr.get(j_run)["state"], "running")
        self.assertEqual(jr.get(j_usr)["state"], "paused")

    def test_deprioritize_running_freezes_restore_but_only_deprioritizes_push(self):
        """Toggle ON (deprioritize_running is the toggle-ON path): a restore/fetch job
        is NEVER merely reniced - it overwrites live saves/config the running game may
        hold open, and the toggle's name only promised to keep BACKUPS (uploads)
        running. It must take the exact freeze path pause_all_gameplay() uses, while a
        push job in the same batch is left running (just deprioritized)."""
        push = self.sleeper()
        restore = self.sleeper()
        j_push = jr.begin("push-precious", push.pid)
        j_restore = jr.begin("restore-precious", restore.pid)
        touched = jr.deprioritize_running()
        self.assertEqual(touched, 2, "counts both the deprioritized push and the frozen restore")
        self.assertEqual(jr.get(j_restore)["state"], "paused")
        self.assertEqual(jr.get(j_restore)["paused_by"], "gameplay")
        self.assertEqual(_await_proc_state(restore.pid, "T"), "T",
                         "the restore's REAL process must actually be SIGSTOPped")
        self.assertEqual(jr.get(j_push)["state"], "running",
                         "a push job stays running under the toggle - only deprioritized")
        self.assertEqual(_proc_state(push.pid), "S", "not stopped - still asleep/running")

    def test_resume_gameplay_thaws_a_deprioritize_frozen_restore(self):
        """deprioritize_running()'s freeze writes paused_by='gameplay' via the SAME
        helper pause_all_gameplay() uses, so resume_gameplay() (the game-end hook)
        thaws it with no special-casing - the clean seam the task calls for."""
        restore = self.sleeper()
        j_restore = jr.begin("fetch-games", restore.pid)
        jr.deprioritize_running()
        self.assertEqual(jr.get(j_restore)["state"], "paused")
        self.assertEqual(jr.resume_gameplay(), 1)
        self.assertEqual(jr.get(j_restore)["state"], "running")
        self.assertIsNone(jr.get(j_restore)["paused_by"])
        self.assertIn(_await_proc_state(restore.pid, ("S", "R")), ("S", "R"),
                     "SIGCONTed - no longer stopped")

    def test_reconcile_thaws_only_without_a_fresh_marker(self):
        p = self.sleeper()
        jid = jr.begin("push-precious", p.pid)
        jr.pause_all_gameplay()
        jr.gameplay_marker().parent.mkdir(parents=True, exist_ok=True)
        jr.gameplay_marker().touch()                  # a game IS live: no thaw
        self.assertEqual(jr.reconcile(), 0)
        self.assertEqual(jr.get(jid)["state"], "paused")
        jr.gameplay_marker().unlink()                 # game gone (hook ran/crashed)
        self.assertEqual(jr.reconcile(), 1)
        self.assertEqual(jr.get(jid)["state"], "running")


class Stop(Base):
    def test_stop_kills_even_a_frozen_group(self):
        p = self.sleeper()
        jid = jr.begin("push-precious", p.pid)
        jr.pause_job(jid)                             # frozen groups must still die
        job = jr.stop_job(jid, grace=1.0)
        self.assertEqual(job["state"], "failed")
        p.wait(timeout=5)
        self.assertIsNotNone(p.poll())


if __name__ == "__main__":
    unittest.main()
