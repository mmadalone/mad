"""openbor.sh's stop_game(): the only thing that actually stops an OpenBOR game.

`kill $game_pid` does NOT. $game_pid is proton's launcher script; the game is a
Wine process inside pressure-vessel, not our child. proton forks, so SIGTERM to
the launcher leaves the game running and Proton says so:

    pid 2429550 != 2429549, skipping destruction (fork without exec?)

Observed on-device 2026-07-17 during gating: the merger correctly exited on losing
the last pad, openbor.sh correctly logged "merger died first -- stopping the game",
the kill went to the wrapper, and OpenBOR kept running until it was killed by hand
in htop. `wineserver -k` ends the Wine session in that prefix, which is what is
actually holding the game up.

The function is EXECUTED here (extracted with sed, run against a stub wineserver),
not text-scanned -- a source-scan is what the review retired StoreIsolation for.
The rest of openbor.sh cannot run headlessly (it needs Proton, a display and pads),
so this is the seam.

Run:  python3 -m unittest tests.test_openbor_stop_game -v
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "openbor.sh"

# Extract just the function and run it with everything else stubbed. Built by
# substitution, NOT str.format: the sed range /^}/p contains a bare brace and
# format() rejects it ("Single '}' encountered").
_HARNESS = r'''
eval "$(sed -n '/^stop_game()/,/^}/p' "@SCRIPT@")"
PROTON_DIR="@PROTON@"
PREFIX="@PREFIX@"
LOG=/dev/null
game_pid=@PID@
stop_game
echo "EXIT=$?"
'''


def _harness(script, proton, prefix, pid) -> str:
    out = _HARNESS
    for token, val in (("@SCRIPT@", script), ("@PROTON@", proton),
                       ("@PREFIX@", prefix), ("@PID@", pid)):
        out = out.replace(token, str(val))
    return out


class StopGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.calls = self.d / "calls.txt"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _proton(self, layout: str | None):
        """A fake Proton dir; `layout` is where its wineserver lives (None = absent)."""
        p = self.d / "proton"
        if layout:
            ws = p / layout
            ws.parent.mkdir(parents=True, exist_ok=True)
            ws.write_text('#!/usr/bin/env bash\n'
                          f'echo "$WINEPREFIX $*" >> "{self.calls}"\n')
            ws.chmod(0o755)
        else:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def _run(self, proton: Path, pid: int = 999999):
        r = subprocess.run(
            ["bash", "-c", _harness(SCRIPT, proton, self.d / "myprefix", pid)],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        return r.stdout

    def test_it_kills_the_wine_session_in_THIS_prefix(self):
        out = self._run(self._proton("files/bin/wineserver"))
        self.assertIn("EXIT=0", out)
        self.assertTrue(self.calls.exists(),
                        "wineserver was never called: `kill $game_pid` alone does "
                        "not stop a Proton game")
        called = self.calls.read_text().strip()
        self.assertIn("-k", called, "wineserver was not asked to kill the session")
        self.assertIn(str(self.d / "myprefix" / "pfx"), called,
                      "wineserver was pointed at the wrong WINEPREFIX, so it would "
                      "kill nothing (or someone else's session)")

    def test_it_finds_wineserver_in_either_proton_layout(self):
        # GE-Proton uses files/; older/other builds use dist/. Support both rather
        # than hardcode the one this rig happens to ship.
        out = self._run(self._proton("dist/bin/wineserver"))
        self.assertIn("EXIT=0", out)
        self.assertTrue(self.calls.exists(), "the dist/ layout was not found")

    def test_no_wineserver_degrades_quietly(self):
        # An unknown Proton layout must never abort the teardown: the merger kill
        # and the launcher's own exit still have to happen.
        out = self._run(self._proton(None))
        self.assertIn("EXIT=0", out,
                      "a missing wineserver aborted stop_game, stranding teardown")
        self.assertFalse(self.calls.exists())

    def test_a_dead_game_pid_is_not_an_error(self):
        # stop_game runs on the teardown path, where the launcher may already be
        # gone. `kill` on a stale pid must not fail the function.
        out = self._run(self._proton("files/bin/wineserver"), pid=999999)
        self.assertIn("EXIT=0", out)


if __name__ == "__main__":
    unittest.main()
