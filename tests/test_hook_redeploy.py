"""deck-post-update.sh step 8 REDEPLOYS the ES-DE controller-router hooks (was report-only), via
lib/hook-deploy.sh. The always-on core set is now DERIVED from the hooks/ tree (hooks/game-{start,end}/
*.sh) MINUS the feature-gated hooks in MAD_GATED_HOOKS - so a new always-on hook is picked up just by
dropping its master into hooks/, with no list to maintain, while Sinden/theme/Wii hooks stay gated.

A missing/stale core hook is restored; a current one is skipped; a stale copy is backed up OUT to _TMP
(never in-place, or ES-DE would run the .bak as a second hook); a gated hook is NOT auto-deployed.

Run:  python3 -m unittest tests.test_hook_redeploy -v
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "hook-deploy.sh"

# real names, one bucket each; GATED entries are kept in sync with lib/hook-deploy.sh MAD_GATED_HOOKS
CORE = ["game-start/04-controller-router-setup.sh", "game-end/00-controller-router.sh",
        "game-end/06-mad-switch-restore.sh"]
GATED = ["game-start/sinden.sh", "game-end/launchscreen.sh"]


def _seed(src: Path, rels):
    for h in rels:
        p = src / h
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"#!/bin/sh\n# master {h}\n")


class DerivedCoreSet(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.src = self.t / "hooks"
        _seed(self.src, CORE + GATED)

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def _core(self):
        r = subprocess.run(["bash", "-c", f'. "{LIB}"\nmad_core_hooks "{self.src}"'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return set(r.stdout.split())

    def test_core_included_gated_excluded(self):
        got = self._core()
        for h in CORE:
            self.assertIn(h, got, f"core hook {h} should be derived")
        for h in GATED:
            self.assertNotIn(h, got, f"gated hook {h} must NOT be in the core set")

    def test_root_and_other_dirs_ignored(self):
        # a .sh at the hooks/ root or in another subdir is not a game-start/end hook -> excluded
        (self.src / "launchscreen-pack.sh").write_text("#!/bin/sh\n")
        (self.src / "system-select").mkdir()
        (self.src / "system-select" / "05-record-view.sh").write_text("#!/bin/sh\n")
        got = self._core()
        self.assertNotIn("launchscreen-pack.sh", got)
        self.assertNotIn("system-select/05-record-view.sh", got)


class HookRedeploy(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.src, self.dst, self.bak = self.t / "hooks", self.t / "dst", self.t / "bak"
        _seed(self.src, CORE + GATED)
        (self.dst / "game-end").mkdir(parents=True)
        shutil.copy(self.src / "game-end/00-controller-router.sh",       # 00 = current
                    self.dst / "game-end/00-controller-router.sh")
        (self.dst / "game-end/06-mad-switch-restore.sh").write_text("#!/bin/sh\n# OLD\n")  # 06 = stale
        # 04 = missing from dst; sinden/launchscreen = gated (must be left alone)

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def _redeploy(self):
        script = f'. "{LIB}"\nmad_redeploy_core_hooks "{self.src}" "{self.dst}" "{self.bak}"\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)

    def test_missing_and_stale_redeployed_current_skipped_gated_left_alone(self):
        r = self._redeploy()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("redeployed: game-start/04-controller-router-setup.sh", r.stdout)
        self.assertIn("redeployed: game-end/06-mad-switch-restore.sh", r.stdout)
        self.assertNotIn("00-controller-router", r.stdout)   # current -> silent
        self.assertTrue((self.dst / "game-start/04-controller-router-setup.sh").exists())
        self.assertEqual((self.dst / "game-end/06-mad-switch-restore.sh").read_text(),
                         (self.src / "game-end/06-mad-switch-restore.sh").read_text())
        self.assertTrue((self.bak / "game-end/06-mad-switch-restore.sh").exists(),
                        "the stale hook must be backed up OUT to _TMP")
        # gated hooks must NOT ride the always-on redeploy
        self.assertNotIn("sinden", r.stdout)
        self.assertFalse((self.dst / "game-start/sinden.sh").exists(),
                         "a gated hook must not be auto-deployed by the core redeploy")

    def test_second_run_reports_all_current(self):
        self._redeploy()               # first run deploys the missing/stale ones
        r = self._redeploy()           # second run: everything is current now
        self.assertIn("all core hooks already current", r.stdout)


if __name__ == "__main__":
    unittest.main()
