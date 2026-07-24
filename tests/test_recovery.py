"""mad-backend.py RECOVERY mode.

When python-evdev is missing (a hypothetical future SteamOS image that drops it from base), a SERVING
backend must NOT fatally exit — it degrades to a limited "recovery" mode that serves only the
post-update reapply (which reinstalls evdev), so the in-panel reapply isn't dead precisely when evdev
is the thing that's gone. `--selfcheck` still hard-fails (it's the deps canary deck-post-update.sh runs
AFTER reinstalling).

evdev is simulated-wiped with a PYTHONPATH shim module that raises ImportError.

Run:  python3 -m unittest tests.test_recovery -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "mad-backend.py"


class RecoveryMode(unittest.TestCase):
    def setUp(self):
        self.shim = Path(tempfile.mkdtemp())
        (self.shim / "evdev.py").write_text('raise ImportError("test: evdev wiped")\n')

    def tearDown(self):
        shutil.rmtree(self.shim, ignore_errors=True)

    def _run(self, args, stdin=""):
        env = dict(os.environ, DECK_CLOUD_SKIP_CONNCHECK="1",
                   PYTHONPATH=str(self.shim) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        return subprocess.run([sys.executable, str(BACKEND), *args], input=stdin,
                              capture_output=True, text=True, timeout=40, env=env)

    def _events(self, out):
        evs = []
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    evs.append(json.loads(ln))
                except ValueError:
                    pass
        return evs

    def test_selfcheck_still_fatals_without_evdev(self):
        r = self._run(["--selfcheck"])
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("ENODEPS", r.stdout)

    def test_serving_recovers_and_serves_postupdate(self):
        stdin = "\n".join([
            '{"method":"hello.ack","params":{"proto":1},"id":1}',
            '{"method":"postupdate.status","id":2}',
            '{"method":"shutdown","id":3}']) + "\n"
        r = self._run([], stdin=stdin)
        if "EBUSY" in r.stdout:
            self.skipTest("a real mad-backend is holding the lock")
        evs = self._events(r.stdout)
        hello = [e for e in evs if e.get("event") == "hello"]
        self.assertTrue(hello, f"no hello event; stderr={r.stderr[-300:]}")
        self.assertTrue(hello[0]["data"].get("recovery"), "hello not flagged recovery")
        self.assertNotIn("evdev", hello[0]["data"].get("caps", []))
        status = [e for e in evs if e.get("id") == 2]
        self.assertTrue(status and status[0].get("ok"),
                        f"postupdate.status not served in recovery: {status}")
        self.assertIn("RECOVERY mode", r.stderr)


if __name__ == "__main__":
    unittest.main()
