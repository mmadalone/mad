"""
install.sh must FAIL LOUDLY when the launchers clone has local changes.

Why this exists: a dirty clone makes `git pull --ff-only` unreachable, so the run
installs nothing new while still printing "ALMOST DONE". That silent no-op is the
case you most need to be told about. Before 2026-08-06 it was only a `warn`, and
no test covered the branch at all (tests/test_install_standalone.py uses an empty
$HOME, so $MAD_DIR never exists and this code path is never reached there).

All three cases run under `--dry-run`, which mutates nothing.

Run:  python3 -m unittest tests.test_install_dirty_clone -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "install.sh"
HAVE_GIT = shutil.which("git") is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


def _run(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(["bash", str(INSTALL), "--dry-run", *args],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


@unittest.skipUnless(HAVE_GIT, "git not available")
class InstallDirtyClone(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        # ES-DE present so the EmuDeck gate (which runs BEFORE the clone step)
        # takes the deterministic "using it as-is" branch.
        (self.home / "ES-DE").mkdir()
        self.mad = self.home / "Emulation" / "tools" / "launchers"
        self.mad.mkdir(parents=True)
        _git(self.mad, "init", "-b", "main")
        # install.sh identifies "our repo" by the origin URL containing mmadalone/mad.
        _git(self.mad, "remote", "add", "origin",
             "https://github.com/mmadalone/mad.git")
        (self.mad / "tracked.txt").write_text("v1\n")
        _git(self.mad, "add", "tracked.txt")
        _git(self.mad, "commit", "-m", "seed")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _dirty(self):
        (self.mad / "tracked.txt").write_text("locally edited\n")

    def test_clean_clone_pulls(self):
        r = _run(self.home)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn("pull --ff-only", r.stdout)
        self.assertIn("updated existing clone", r.stdout)
        self.assertNotIn("has local changes", r.stdout)

    def test_dirty_clone_is_fatal(self):
        self._dirty()
        r = _run(self.home)
        self.assertEqual(r.returncode, 1, "a dirty clone must abort the install")
        # die() writes to stderr; the failure must name the offending path.
        self.assertIn(str(self.mad), r.stderr)
        self.assertIn("nothing new was pulled", r.stderr)
        # and it must show WHICH files, so the user can act without extra commands
        self.assertIn("tracked.txt", r.stdout)
        # it aborts BEFORE pulling and before finishing
        self.assertNotIn("pull --ff-only", r.stdout)
        self.assertNotIn("ALMOST DONE", r.stdout)

    def test_force_dirty_restores_old_behaviour(self):
        self._dirty()
        r = _run(self.home, "--force-dirty")
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn("--force-dirty given", r.stdout)
        self.assertIn("ALMOST DONE", r.stdout)          # ran to the end
        self.assertNotIn("pull --ff-only", r.stdout)    # still does not pull

    def test_new_flag_is_documented_in_help(self):
        # --help prints a fixed sed line range over the header block; adding a flag
        # without widening that range silently hides it. Guard against that.
        r = subprocess.run(["bash", str(INSTALL), "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        for flag in ("--dry-run", "--standalone", "--express",
                     "--reconfigure", "--force-dirty"):
            self.assertIn(flag, r.stdout, f"{flag} missing from --help")

    def test_runtime_dotfiles_are_not_local_changes(self):
        """The deadlock this guards against.

        The ES-DE binary writes .esde-scripts-expect into this clone at startup, and the
        skew dialog tells the user to run install.sh. On a checkout predating the
        .gitignore rule that file is untracked, so a naive dirty check aborts the pull --
        and the rule can only ARRIVE through that pull. The skew would then be permanent
        while the dialog kept prescribing a fix that could not run.
        """
        for name in (".esde-scripts-expect", ".scripts-skew-snooze", ".scripts-rev-test",
                     ".post-update-pending", ".healthcheck-test", ".last-os-build"):
            (self.mad / name).write_text("1\n")
        r = _run(self.home)
        self.assertEqual(r.returncode, 0,
                         "runtime state files must never block the pull")
        self.assertIn("pull --ff-only", r.stdout)

    def test_real_dirt_alongside_runtime_dotfiles_still_aborts(self):
        # The filter must not become a blanket excuse: genuine local work still stops it.
        (self.mad / ".esde-scripts-expect").write_text("1\n")
        self._dirty()
        r = _run(self.home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("tracked.txt", r.stdout)
        self.assertNotIn(".esde-scripts-expect", r.stdout,
                         "runtime files must not be listed as the user's local changes")

    def test_unknown_flag_still_exits_2(self):
        r = subprocess.run(["bash", str(INSTALL), "--no-such-flag"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
