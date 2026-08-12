"""mad-switch-launch.py is a forwarding shim over mad-standalone-launch.py
(audit overlaps-4 collapse). The shim must exec the generic binder with argv
intact — proven here by the usage error COMING FROM the standalone binder —
while the wrap tooling (W constant, seeder, golden tests) keeps minting the
shim's name, structurally avoiding the double-wrap trap.

Run:  python3 -m unittest tests.test_switch_launch_shim -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "mad-switch-launch.py"


class SwitchLaunchShim(unittest.TestCase):
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SHIM)] + argv,
                              capture_output=True, text=True, timeout=60)

    def test_no_args_yields_the_standalone_usage(self):
        r = self._run([])
        self.assertEqual(r.returncode, 2)
        self.assertIn("mad-standalone-launch.py <emu> <rom> -- <emulator cmd...>",
                      r.stderr)

    def test_partial_args_are_forwarded(self):
        r = self._run(["eden"])           # forwarded argv still fails usage in the target
        self.assertEqual(r.returncode, 2)
        self.assertIn("mad-standalone-launch.py", r.stderr)

    def test_shim_stays_a_shim(self):
        src = SHIM.read_text()
        self.assertIn("os.execv", src)
        self.assertIn("mad-standalone-launch.py", src)
        self.assertNotIn("switch_bind", src, "logic belongs in the generic binder")


if __name__ == "__main__":
    unittest.main()
