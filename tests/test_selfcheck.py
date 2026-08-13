"""mad-backend --selfcheck runs clean in a fresh interpreter (hermetic subprocess).

Before this test the selfcheck import path had ZERO coverage: tests/test_recovery.py only
exercises the evdev-missing fatal, which returns before the big import. This is the fence
that catches a module falling out of the backend's import list - the failure the panel
sees as a whole page answering ENOMETHOD.

Run: python3 -m unittest tests.test_selfcheck -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "mad-backend.py"


class Selfcheck(unittest.TestCase):
    def test_selfcheck_ok_and_hermetic(self):
        try:
            import evdev  # noqa: F401
        except ImportError:
            self.skipTest("python-evdev not installed (CI installs it; the Deck has it)")
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, MAD_DATA_ROOT=td, PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([sys.executable, str(BACKEND), "--selfcheck"],
                               cwd=ROOT, env=env, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("selfcheck OK", r.stdout)
            # Hermeticity tripwire: importing the backend must never WRITE anywhere
            # under the data root (registration only; RUN_DIR mkdir happens after the
            # selfcheck early-return).
            self.assertEqual(os.listdir(td), [],
                             "a backend import wrote into the data root at import time")


if __name__ == "__main__":
    unittest.main()
