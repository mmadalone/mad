"""_CloudStream surfaces a per-set push's failed-file count.

A per-set cloud push (games / BIOS / ES-DE / emulator config, all via deck-cloud.sh _push_set) publishes
its manifest and exits 0 even when SOME per-file copies failed - the manifest still lists them, so a later
restore's fetch surfaces the gap. _push_set therefore prints `MAD_SET_SUMMARY uploaded=N failed=M` on
stdout; _CloudStream must fold `failed` (>0) into the terminal {done} so the panel warns instead of
reporting a clean success - and must NOT surface the summary token itself as a display line.

No rclone / network: a tiny bash argv stands in for the engine so we exercise the pure stream parse.

Run:  python3 -m unittest tests.test_cloud_summary -v
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import cloud_cmds as cc                   # noqa: E402


class CloudStreamSummary(unittest.TestCase):
    def _drive(self, argv):
        """Run a _CloudStream synchronously, collecting its emits. run()'s finally releases _RUN_ACTIVE
        and clears the marker; patch both so a bare run() needs no daemon/state-dir setup."""
        s = cc._CloudStream(argv)
        events: list = []
        s.emit = events.append
        lock = threading.Lock()
        lock.acquire()
        with mock.patch.object(cc, "_RUN_ACTIVE", lock), \
                mock.patch.object(cc, "_clear_marker", lambda: None):
            s.run()
        return events

    @staticmethod
    def _done(events):
        return next(e for e in events if e.get("done"))

    def test_failed_count_folds_into_done(self):
        events = self._drive(
            ["bash", "-c", "printf 'copying smb.zip\\nMAD_SET_SUMMARY uploaded=3 failed=2\\n'"])
        done = self._done(events)
        self.assertEqual(done["rc"], 0)
        self.assertEqual(done["failed"], 2, "the partial-upload count reaches the UI via {done}")

    def test_summary_token_is_not_shown_as_a_line(self):
        events = self._drive(
            ["bash", "-c", "printf 'MAD_SET_SUMMARY uploaded=1 failed=1\\n'"])
        self.assertFalse(
            any(e.get("line", "").startswith("MAD_SET_SUMMARY") for e in events),
            "the machine-readable summary must never render as a footer line")

    def test_zero_failed_omits_the_key(self):
        # failed=0 is a clean success -> no `failed` key, so the C++ default (0) shows the success flash.
        events = self._drive(
            ["bash", "-c", "printf 'MAD_SET_SUMMARY uploaded=5 failed=0\\n'"])
        self.assertNotIn("failed", self._done(events))

    def test_no_summary_omits_the_key(self):
        # A non-push op (a restore/fetch) never emits the token -> {done} carries no `failed` key.
        events = self._drive(["bash", "-c", "printf 'restoring...\\n'"])
        self.assertNotIn("failed", self._done(events))

    def test_malformed_summary_is_ignored(self):
        events = self._drive(["bash", "-c", "printf 'MAD_SET_SUMMARY garbage\\n'"])
        self.assertNotIn("failed", self._done(events), "a malformed token must not crash or set failed")


if __name__ == "__main__":
    unittest.main()
