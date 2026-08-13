"""transfers.* RPCs + the detached-job stream plumbing (lib/madsrv/cloud_cmds).

Pins: transfers.list rows (live pct/summary from the .out tail), the tail stream's
event shapes ({progress}/{line}/{done,rc}) and its NEVER-signals-the-job contract,
pause refusal for in-process jobs, _stream_op's registry EBUSY precheck (with the
distinct gameplay-paused message), cloud.status's boolean fields, and the marker
consult that keeps auto-resume from double-running a surviving detached job.

Everything runs against a tmp DECK_CLOUD_STATE_DIR; no engine is spawned (the EBUSY
paths raise before any spawn) and jobs are throwaway `sleep` children.

Run: python3 -m unittest tests.test_transfers_rpc -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from lib import job_registry as jr
from lib.madsrv import cloud_cmds as cc
from lib.madsrv.rpc import RpcError


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
        p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self._procs.append(p)
        return p

    def live_job(self, kind="push-precious", paused_by=None):
        p = self.sleeper()
        jid = jr.begin(kind, p.pid, argv=[kind])
        if paused_by:
            jr.pause_job(jid, by=paused_by)
        return jid, p


STATS = ('{"level":"info","msg":"","stats":{"bytes":50,"totalBytes":100,'
         '"speed":10.0,"eta":5,"checks":0,"totalChecks":0,"transferring":'
         '[{"name":"a.bin","percentage":50,"bytes":50,"size":100,"speed":10.0}]}}')


class TransfersList(Base):
    def test_rows_carry_live_progress_from_the_out_tail(self):
        jid, _ = self.live_job("sync-library")
        jr.out_path(jid).write_text(f"[cloud 00:00:00] starting\n{STATS}\n")
        out = cc._transfers_list({})
        (row,) = [r for r in out["jobs"] if r["id"] == jid]
        self.assertEqual(row["state"], "running")
        self.assertEqual(row["title"], "Syncing library")
        self.assertEqual(row["pct"], 50)
        self.assertIn("50%", row["summary"])

    def test_list_reaps_dead_jobs(self):
        jid, p = self.live_job()
        os.killpg(p.pid, 9)
        p.wait(timeout=5)
        with mock.patch.object(jr, "REAP_GRACE_S", 0):   # skip the rc-in-flight grace
            out = cc._transfers_list({})
        (row,) = [r for r in out["jobs"] if r["id"] == jid]
        self.assertEqual(row["state"], "failed")


class TailStream(Base):
    def _run_tail(self, jid):
        events = []
        s = cc._JobTailStream(jid)
        s.emit = events.append
        t = threading.Thread(target=s.run)
        t.start()
        return s, t, events

    def test_tail_emits_progress_and_done_rc(self):
        jid, p = self.live_job("push-precious")
        jr.out_path(jid).write_text(f"[cloud 00:00:00] uploading\n{STATS}\n")
        s, t, events = self._run_tail(jid)
        for _ in range(100):
            if any("progress" in e for e in events):
                break
            import time
            time.sleep(0.05)
        jr.end(jid, 0)
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        kinds = [next(iter(e)) for e in events]
        self.assertIn("progress", kinds)
        self.assertEqual(events[-1]["done"], True)
        self.assertEqual(events[-1]["rc"], 0)
        self.assertIsNone(p.poll(), "the tail must NEVER signal the job")

    def test_attach_replay_is_ONE_progress_event(self):
        # An attach must not replay history: the RPC layer stashes only a handful of
        # events before the panel's callback exists, so a burst could push the terminal
        # {done} out of that buffer and freeze the page on "Reattaching…". Old lines are
        # scanned (to recover the failed-count) but not emitted.
        jid, _p = self.live_job("push-games")
        jr.out_path(jid).write_text("\n".join([
            "[cloud] old line 1", STATS, "[cloud] old line 2", STATS,
            "MAD_SET_SUMMARY uploaded=3 failed=2", ""]))
        s, t, events = self._run_tail(jid)
        jr.end(jid, 0)
        t.join(timeout=5)
        kinds = [next(iter(e)) for e in events]
        self.assertEqual(kinds.count("progress"), 1, events)
        self.assertNotIn("line", kinds)
        self.assertEqual(events[-1]["failed"], 2)   # the count still reaches the UI

    def test_stopping_the_tail_leaves_the_job_running(self):
        jid, p = self.live_job("push-precious")
        s, t, events = self._run_tail(jid)
        s.stopped.set()
        t.join(timeout=5)
        self.assertIsNone(p.poll())                    # job survives (detached model)
        self.assertEqual(jr.get(jid)["state"], "running")


class Controls(Base):
    def test_pause_refuses_in_process_jobs(self):
        jid = jr.begin("granular", os.getpid(), detached=False)
        with self.assertRaises(RpcError):
            cc._transfers_pause({"id": jid})

    def test_pause_resume_roundtrip(self):
        jid, _ = self.live_job("sync-library")
        self.assertTrue(cc._transfers_pause({"id": jid})["paused"])
        self.assertFalse(cc._transfers_resume({"id": jid})["paused"])

    def test_unknown_id_is_enoent(self):
        with self.assertRaises(RpcError):
            cc._transfers_pause({"id": "nope"})

    def test_cancel_clears_a_matching_marker_and_plan_dir(self):
        jid, _ = self.live_job("push-games")
        plandir = self.tmp / "games-plan" / "x"
        plandir.mkdir(parents=True)
        jr.update(jid, argv=["push-games", "20260101T000000", str(plandir)])
        cc._write_marker(["push-games", "20260101T000000", str(plandir)])
        out = cc._transfers_cancel({"id": jid})
        self.assertTrue(out["cancelled"])
        self.assertIsNone(cc._read_marker())
        self.assertFalse(plandir.exists())


class StreamOpBusy(Base):
    def test_live_detached_job_is_ebusy(self):
        self.live_job("push-precious")
        with self.assertRaisesRegex(RpcError, "already running"):
            cc._stream_op([str(cc.ENGINE), "sync-library"])
        # the lock must have been released: a second failure says the same thing
        with self.assertRaisesRegex(RpcError, "already running"):
            cc._stream_op([str(cc.ENGINE), "sync-library"])

    def test_gameplay_frozen_job_gets_the_distinct_message(self):
        self.live_job("push-precious", paused_by="gameplay")
        with self.assertRaisesRegex(RpcError, "paused during gameplay"):
            cc._stream_op([str(cc.ENGINE), "sync-library"])

    def test_non_cloud_jobs_do_not_block_cloud_ops(self):
        jid = jr.begin("granular", os.getpid(), detached=False)
        seen = {}
        with mock.patch.object(cc, "_spawn_registered",
                               side_effect=lambda argv, kind, source="panel":
                               seen.update(kind=kind) or ("j1", None)), \
             mock.patch.object(cc, "_JobTailStream") as tails:
            tails.return_value.start.return_value = "tok"
            out = cc._stream_op([str(cc.ENGINE), "push-precious", "--force"])
        self.assertEqual(out, {"stream": "tok"})
        self.assertEqual(seen["kind"], "push-precious")
        cc._RUN_ACTIVE.release()   # _stream_op holds it for the (mocked) tail's life


class CloudStatus(Base):
    # cloud.set_toggle (+ its validator test that used to live here) was retired audit 2026-08-12
    # phase 5: dead RPC, no caller - the three GLOBAL backup toggles moved to ES-DE's Other-settings
    # menu, which shells out to "deck-cloud.sh set-toggle" directly instead.
    def test_status_bools_include_gameplay(self):
        out_text = ("connected\t1\nonexit_enabled\t1\nautoresume_enabled\t0\n"
                    "gameplay_enabled\t1\nlast_backup\tx\n")
        with mock.patch.object(cc, "_run", return_value=(0, out_text, "")):
            st = cc._cloud_status({})
        self.assertTrue(st["gameplay_enabled"])
        self.assertFalse(st["autoresume_enabled"])
        self.assertNotIn("timer_active", st)


class ActiveAndMarker(Base):
    def test_active_reports_a_detached_survivor(self):
        jid, _ = self.live_job("push-precious")
        out = cc._cloud_active({})
        self.assertTrue(out["running"])
        self.assertEqual(out["job"], jid)
        self.assertIsNone(out["token"])   # not started by THIS daemon session

    def test_marker_with_no_live_job_is_pending(self):
        cc._write_marker(["push-precious", "--force"])
        out = cc._cloud_active({})
        self.assertFalse(out["running"])
        self.assertTrue(out["pending"])
        self.assertFalse(out["pending_restore"])


if __name__ == "__main__":
    unittest.main()
