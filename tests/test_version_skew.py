"""Script-skew detection: deployed scripts older than the running ES-DE build expects.

Ships DORMANT. Until a build writes `.esde-scripts-expect`, there is no expectation to
compare against and this must claim nothing at all -- that is what lets the scripts half
go out ahead of the C++, which is the push order the whole mechanism exists to enforce.

Two guards here matter more than the comparator itself:

  * the skew line must NOT flip check_missing's exit code. That code decides whether a
    successful reapply writes .last-os-build and clears .post-update-pending
    (tests/test_postupdate_flag.py exists because that broke live on 2026-07-24).
  * esde-health-check.sh must NOT arm .post-update-pending for skew alone. That flag
    offers a deck-post-update.sh reapply, which needs sudo and does not git-pull, so it
    could never clear a skew: an unclearable nag pointing at the wrong fix.

Run:  python3 -m unittest tests.test_version_skew -v
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEALTH = ROOT / "esde-health-check.sh"


class Comparator(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.rev = self.t / "SCRIPTS_REV"
        self.expect = self.t / ".esde-scripts-expect"
        os.environ["MAD_SCRIPTS_REV_FILE"] = str(self.rev)
        os.environ["MAD_SCRIPTS_EXPECT_FILE"] = str(self.expect)
        import lib.version_skew as vs
        self.vs = importlib.reload(vs)

    def tearDown(self):
        os.environ.pop("MAD_SCRIPTS_REV_FILE", None)
        os.environ.pop("MAD_SCRIPTS_EXPECT_FILE", None)
        shutil.rmtree(self.t, ignore_errors=True)

    def test_no_expect_file_claims_nothing(self):
        # THE DORMANCY CASE: an older binary has made no demand, so invent none.
        self.rev.write_text("3\n")
        self.assertIsNone(self.vs.skew())
        self.assertIsNone(self.vs.skew_line())

    def test_behind_is_reported_with_both_numbers_and_the_fix(self):
        self.rev.write_text("3\n")
        self.expect.write_text("7\n")
        self.assertEqual(self.vs.skew(), (3, 7))
        line = self.vs.skew_line()
        self.assertTrue(line.startswith(self.vs.SKEW_PREFIX))
        self.assertIn("3", line)
        self.assertIn("7", line)
        self.assertIn("install.sh", line)      # names the remedy, not just the problem

    def test_equal_is_silent(self):
        self.rev.write_text("7\n")
        self.expect.write_text("7\n")
        self.assertIsNone(self.vs.skew_line())

    def test_scripts_ahead_is_silent(self):
        # Scripts before binary is the REQUIRED order; nagging would punish it.
        self.rev.write_text("9\n")
        self.expect.write_text("7\n")
        self.assertIsNone(self.vs.skew_line())

    def test_missing_rev_file_counts_as_zero(self):
        self.expect.write_text("2\n")
        self.assertEqual(self.vs.scripts_rev(), 0)
        self.assertEqual(self.vs.skew(), (0, 2))

    def test_unparseable_expect_claims_nothing(self):
        # A corrupt file is not evidence of skew.
        self.rev.write_text("1\n")
        for junk in ("", "   \n", "abc\n", "-4\n", "3.5\n"):
            self.expect.write_text(junk)
            self.assertIsNone(self.vs.skew_line(), f"junk {junk!r} must not claim skew")

    def test_unparseable_rev_is_treated_as_zero(self):
        self.expect.write_text("5\n")
        self.rev.write_text("garbage\n")
        self.assertEqual(self.vs.scripts_rev(), 0)

    def test_trailing_content_is_tolerated(self):
        self.rev.write_text("4  # bumped for the backup RPC\n")
        self.expect.write_text("4\n")
        self.assertEqual(self.vs.scripts_rev(), 4)
        self.assertIsNone(self.vs.skew_line())


class ShippedRevFile(unittest.TestCase):
    def test_scripts_rev_is_a_positive_int(self):
        raw = (ROOT / "SCRIPTS_REV").read_text().split()
        self.assertTrue(raw, "SCRIPTS_REV must not be empty")
        self.assertGreater(int(raw[0]), 0)

    def test_health_check_filters_the_exact_prefix(self):
        # Drift guard: the prefix is a contract between the python and the shell.
        import lib.version_skew as vs
        self.assertIn(vs.SKEW_PREFIX, HEALTH.read_text(),
                      "esde-health-check.sh must filter on lib/version_skew.SKEW_PREFIX")


class HealthCheckArming(unittest.TestCase):
    """The real esde-health-check.sh, against a stubbed --check."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.mad = self.home / "Emulation" / "tools" / "launchers"
        self.mad.mkdir(parents=True)
        self.flag = self.mad / ".post-update-pending"
        self.marker = self.mad / ".last-os-build"
        self.marker.write_text("some-older-build\n")     # != the live BUILD_ID -> gate opens

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _stub_check(self, output: str):
        # Emit via a file, not printf: bash's %s does not interpret \n, so an escaped
        # literal would collapse multi-line output onto one line and the ^-anchored
        # filter could never match it.
        payload = self.mad / "_check_output.txt"
        payload.write_text(output)
        stub = self.mad / "deck-post-update.sh"
        stub.write_text("#!/usr/bin/env bash\n"
                        f'[ "${{1:-}}" = --check ] && cat "{payload}"\nexit 0\n')
        stub.chmod(0o755)

    def _run(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MAD_POSTUPDATE_FLAG"] = str(self.flag)
        return subprocess.run(["bash", str(HEALTH)], capture_output=True,
                              text=True, env=env, timeout=60)

    def test_skew_alone_does_not_arm_the_flag(self):
        self._stub_check("Scripts older than this ES-DE build: rev 1 < 7 needed"
                         " - run install.sh from Desktop Mode to update them\n")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.flag.exists(),
                         "skew must not arm a flag whose remedy cannot clear it")

    def test_a_real_missing_component_still_arms(self):
        # Regression guard: the filter must not break the normal path.
        self._stub_check("Samba file sharing\n")
        self._run()
        self.assertTrue(self.flag.exists())
        self.assertIn("Samba", self.flag.read_text())

    def test_mixed_arms_on_the_real_component_only(self):
        self._stub_check("Samba file sharing\n"
                         "Scripts older than this ES-DE build: rev 1 < 7 needed\n")
        self._run()
        self.assertTrue(self.flag.exists())
        text = self.flag.read_text()
        self.assertIn("Samba", text)
        self.assertNotIn("Scripts older", text)

    def test_all_clear_clears_an_existing_flag(self):
        self.flag.write_text("stale\n")
        self._stub_check("")
        self._run()
        self.assertFalse(self.flag.exists())


if __name__ == "__main__":
    unittest.main()
