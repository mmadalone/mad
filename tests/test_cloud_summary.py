"""The transfer tail stream surfaces a per-set push's failed-file count.

A per-set cloud push (games / BIOS / ES-DE / emulator config, all via deck-cloud.sh
_push_set) publishes its manifest and exits 0 even when SOME per-file copies failed -
the manifest still lists them, so a later restore's fetch surfaces the gap. _push_set
therefore prints `MAD_SET_SUMMARY uploaded=N failed=M`; the job tail stream
(_JobTailStream - the detached-job replacement for the old pipe _CloudStream, SAME
event shapes) must fold `failed` (>0) into the terminal {done} so the panel warns
instead of reporting a clean success - and must NOT surface the summary token itself
as a display line.

No rclone / engine: the job's .out file is written directly and the job marked done,
so this exercises the pure tail parse against a tmp DECK_CLOUD_STATE_DIR.

Run:  python3 -m unittest tests.test_cloud_summary -v
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

from lib import job_registry as jr                        # noqa: E402
from lib.madsrv import cloud_cmds as cc                   # noqa: E402


class CloudStreamSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp)

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _drive(self, lines: str, rc: int = 0):
        """A finished registered job whose .out holds the engine's output lines; run
        the tail synchronously and collect its emits."""
        jid = jr.begin("push-games", os.getpid(), argv=["push-games"])
        jr.out_path(jid).write_text(lines)
        jr.end(jid, rc)
        s = cc._JobTailStream(jid)
        events: list = []
        s.emit = events.append
        with mock.patch.object(cc, "_clear_marker", lambda: None):
            s.run()
        return events

    @staticmethod
    def _done(events):
        return next(e for e in events if e.get("done"))

    def test_failed_count_folds_into_done(self):
        events = self._drive("copying smb.zip\nMAD_SET_SUMMARY uploaded=3 failed=2\n")
        done = self._done(events)
        self.assertEqual(done["rc"], 0)
        self.assertEqual(done["failed"], 2, "the partial-upload count reaches the UI via {done}")

    def test_summary_token_is_not_shown_as_a_line(self):
        events = self._drive("MAD_SET_SUMMARY uploaded=1 failed=1\n")
        self.assertFalse(
            any(e.get("line", "").startswith("MAD_SET_SUMMARY") for e in events),
            "the machine-readable summary must never render as a footer line")

    def test_zero_failed_omits_the_key(self):
        # failed=0 is a clean success -> no `failed` key, so the C++ default (0) shows the success flash.
        events = self._drive("MAD_SET_SUMMARY uploaded=5 failed=0\n")
        self.assertNotIn("failed", self._done(events))

    def test_no_summary_omits_the_key(self):
        # A non-push op (a restore/fetch) never emits the token -> {done} carries no `failed` key.
        events = self._drive("restoring...\n")
        self.assertNotIn("failed", self._done(events))

    def test_malformed_summary_is_ignored(self):
        events = self._drive("MAD_SET_SUMMARY garbage\n")
        self.assertNotIn("failed", self._done(events), "a malformed token must not crash or set failed")

    def test_terminal_rc_comes_from_the_registry(self):
        events = self._drive("upload died\n", rc=143)
        self.assertEqual(self._done(events)["rc"], 143)


if __name__ == "__main__":
    unittest.main()
