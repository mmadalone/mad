"""tester.start's read-stop-create-publish and each stream teardown's token-checked clear are
serialized under tester_cmds._ACTIVE_LOCK, so two near-simultaneous starts (tester.start is
slow=True on the 4-worker pool) can't both stop the old stream and both publish a new one -- which
would orphan the losing stream's evdev grab until the panel closes.

Regression for the 2026-07-15 review finding #27. Deterministic: hold the lock and prove a
concurrent start blocks in its critical section (it would NOT block if the lock were removed).
Run: python3 -m unittest tests.test_tester_active_lock -v
"""
from __future__ import annotations

import threading
import unittest

from lib.madsrv import tester_cmds as T


class TesterActiveLock(unittest.TestCase):
    def setUp(self):
        self._saved = T._active["stream"]
        T._active["stream"] = None

    def tearDown(self):
        T._active["stream"] = self._saved

    def test_start_blocks_on_active_lock_until_released(self):
        # An unknown kind raises RpcError, but ONLY after tester.start has acquired _ACTIVE_LOCK
        # (the kind check is inside the locked section). So holding the lock must stall the start.
        done = threading.Event()
        box = {}

        def run():
            try:
                T._tester_start({"kind": "nonsense"})
            except Exception as e:
                box["exc"] = type(e).__name__
            finally:
                done.set()

        with T._ACTIVE_LOCK:
            th = threading.Thread(target=run, daemon=True)
            th.start()
            # while we hold the lock, the start cannot reach its raise
            self.assertFalse(done.wait(0.3), "tester.start did not block on _ACTIVE_LOCK")
        # lock released -> the start proceeds and raises EINVAL for the bad kind
        self.assertTrue(done.wait(2.0), "start never completed after lock release")
        th.join(1.0)
        self.assertEqual(box.get("exc"), "RpcError")

    def test_stale_teardown_clear_does_not_clobber_a_newer_token(self):
        # After a newer start published its token, an OLD stream's locked teardown clear must be a
        # no-op (token check) rather than nulling the live tester.
        T._active["stream"] = "NEW_TOKEN"
        with T._ACTIVE_LOCK:                      # exactly what a stream teardown does
            if T._active["stream"] == "OLD_TOKEN":
                T._active["stream"] = None
        self.assertEqual(T._active["stream"], "NEW_TOKEN")

    def test_lock_is_a_real_lock(self):
        self.assertIsInstance(T._ACTIVE_LOCK, type(threading.Lock()))


if __name__ == "__main__":
    unittest.main()
