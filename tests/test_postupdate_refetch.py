"""Task D: post-update re-fetch of /home-resident assets that survive an OS update but had no recovery
path if ever deleted - the pixel-es-de theme and the rclone/restic cloud binaries.

Two pieces:
  * fetch-cloud-bins.sh is idempotent - a binary already present is skipped (no download).
  * deck-post-update.sh --check (check_missing) now flags a missing theme (gated on INSTALL_THEME=1,
    which the real install.conf sets) and the missing cloud binaries - but the binaries only when the
    user actually set up cloud backup (a credentials file is present), so non-cloud users aren't nagged.

The --check tests run against a SANDBOX $HOME (missing everything) but the REAL launchers dir, so
check_missing prints a long missing-list; we assert only on our own lines.

Run:  python3 -m unittest tests.test_postupdate_refetch -v
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FETCH = ROOT / "fetch-cloud-bins.sh"
POSTUPDATE = ROOT / "deck-post-update.sh"


class FetchIdempotent(unittest.TestCase):
    def test_present_binaries_are_skipped_no_download(self):
        with tempfile.TemporaryDirectory() as d:
            binp = Path(d) / "bin"
            binp.mkdir()
            for name in ("rclone", "restic"):
                f = binp / name
                f.write_text("#!/bin/sh\ntrue\n")
                f.chmod(f.stat().st_mode | stat.S_IEXEC)
            env = dict(os.environ, MAD_BIN_DIR=str(binp))
            r = subprocess.run(["bash", str(FETCH)], capture_output=True, text=True,
                               timeout=30, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("rclone present - skip", r.stdout)
            self.assertIn("restic present - skip", r.stdout)
            self.assertNotIn("fetching", r.stdout, "must not download when already present")


class CheckMissingRefetch(unittest.TestCase):
    def _check(self, home: Path):
        env = dict(os.environ, HOME=str(home))
        # --check runs ONLY check_missing then exits; $L stays the real launchers dir (script path).
        return subprocess.run(["bash", str(POSTUPDATE), "--check"], capture_output=True,
                              text=True, timeout=90, env=env)

    def test_missing_theme_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._check(Path(d))  # sandbox HOME has no ES-DE/themes/pixel-es-de
            self.assertIn("MAD theme (pixel-es-de)", r.stdout, r.stdout)

    def test_cloud_bins_flagged_only_when_cloud_configured(self):
        # (a) no credentials file -> binaries NOT nagged (user doesn't use cloud)
        with tempfile.TemporaryDirectory() as d:
            r = self._check(Path(d))
            self.assertNotIn("rclone (cloud backup binary)", r.stdout,
                             "must not nag a non-cloud user about rclone")
        # (b) credentials present but binaries absent -> both flagged
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            creds = home / ".ssh" / "credentials-steamdeck"
            creds.parent.mkdir(parents=True)
            creds.write_text("dummy\n")
            r = self._check(home)
            self.assertIn("rclone (cloud backup binary)", r.stdout, r.stdout)
            self.assertIn("restic (cloud backup binary)", r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
