"""esde-rollback-prune.sh: keep the newest N ES-DE rollback AppImages, MOVE the rest.

ES-DE's updater renames the running AppImage to "<name>_<version>.OLD" and never
deletes it. Upstream assumes consecutive releases have different version strings, so
one rollback accumulates per version. Every MAD build reports 3.4.1 and differs only
by the CI run number, so today they all collide on one filename and exactly one 120MB
.OLD exists. The moment that name carries the run number the bound disappears, so this
prune has to land no later than the naming change - hence the glob matches BOTH forms.

Rule #5 throughout: nothing is deleted, and a file that cannot be moved is left alone.

Run:  python3 -m unittest tests.test_rollback_prune -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "esde-rollback-prune.sh"

STOCK = "ES-DE-MAD.AppImage_3.4.1.OLD"                 # today's colliding name
PERBUILD = [f"ES-DE-MAD.AppImage_3.4.1-mad.{n}.OLD" for n in (158, 159, 160, 161)]


class RollbackPrune(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.apps = self.home / "Applications"
        self.apps.mkdir()
        self.log = self.home / "prune.log"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _mk(self, names):
        """Create the files with strictly increasing mtimes (oldest first)."""
        made = []
        for i, n in enumerate(names):
            p = self.apps / n
            p.write_text(n)
            os.utime(p, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
            made.append(p)
        return made

    def _run(self, keep=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MAD_APPS_DIR"] = str(self.apps)
        env["MAD_ROLLBACK_LOG"] = str(self.log)
        if keep is not None:
            env["MAD_ROLLBACK_KEEP"] = str(keep)
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True,
                              text=True, env=env, timeout=60)

    def _tmp_dirs(self):
        return sorted(self.apps.glob("_TMP-esde-mad-rollback-*"))

    def test_keeps_newest_two_moves_the_rest(self):
        self._mk(PERBUILD)                      # 158 oldest ... 161 newest
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(p.name for p in self.apps.glob("*.OLD"))
        self.assertEqual(left, sorted(PERBUILD[-2:]), "must keep the two NEWEST")
        moved = self._tmp_dirs()
        self.assertEqual(len(moved), 1)
        self.assertEqual(sorted(p.name for p in moved[0].glob("*.OLD")),
                         sorted(PERBUILD[:2]))

    def test_nothing_is_ever_deleted(self):
        made = self._mk(PERBUILD)
        self._run()
        surviving = {p.name for p in self.apps.rglob("*.OLD")}
        self.assertEqual(surviving, {p.name for p in made},
                         "every rollback must still exist somewhere")

    def test_recovery_note_written_with_a_usable_command(self):
        self._mk(PERBUILD)
        self._run()
        note = self._tmp_dirs()[0] / "RECOVERY.txt"
        self.assertTrue(note.is_file())
        text = note.read_text()
        self.assertIn("NOT deleted", text)
        self.assertIn("ES-DE-MAD.AppImage", text)       # the rollback command
        self.assertIn(str(self.apps), text)

    def test_no_tmp_dir_when_nothing_to_prune(self):
        self._mk(PERBUILD[:2])                  # exactly KEEP
        self._run()
        self.assertEqual(self._tmp_dirs(), [], "must not leave an empty _TMP dir")

    def test_empty_dir_is_a_no_op(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._tmp_dirs(), [])

    def test_matches_the_stock_name_too(self):
        # Ships BEFORE the C++ naming change, so it must handle today's single .OLD.
        self._mk([STOCK] + PERBUILD)
        self._run(keep=1)
        left = [p.name for p in self.apps.glob("*.OLD")]
        self.assertEqual(left, [PERBUILD[-1]])
        self.assertIn(STOCK, [p.name for p in self._tmp_dirs()[0].glob("*.OLD")])

    def test_live_appimage_and_wrapper_are_untouched(self):
        # The glob must not sweep the running build, the extracted AppDir or the
        # stock fallback the wrapper falls back to.
        keep_these = ["ES-DE-MAD.AppImage", "ES-DE.AppImage", "ES-DE.AppImage.real"]
        for n in keep_these:
            (self.apps / n).write_text(n)
        (self.apps / "ES-DE-MAD.AppDir").mkdir()
        self._mk(PERBUILD)
        self._run()
        for n in keep_these:
            self.assertTrue((self.apps / n).is_file(), f"{n} must not be pruned")
        self.assertTrue((self.apps / "ES-DE-MAD.AppDir").is_dir())

    def test_filenames_with_spaces_survive(self):
        odd = "ES-DE-MAD.AppImage_3.4.1-mad.99 (copy).OLD"
        self._mk([odd] + PERBUILD)
        self._run()
        self.assertIn(odd, [p.name for p in self._tmp_dirs()[0].glob("*.OLD")])

    def test_junk_keep_override_does_not_wipe_everything(self):
        self._mk(PERBUILD)
        r = self._run(keep="not-a-number")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(list(self.apps.glob("*.OLD"))), 2,
                         "a junk MAD_ROLLBACK_KEEP must fall back to the default, not 0")

    def test_keep_zero_still_moves_never_deletes(self):
        made = self._mk(PERBUILD)
        self._run(keep=0)
        self.assertEqual(list(self.apps.glob("*.OLD")), [])
        self.assertEqual(len(list(self._tmp_dirs()[0].glob("*.OLD"))), len(made))

    def test_unwritable_target_leaves_files_in_place(self):
        # Rule #5 hard invariant: if the backup cannot be written, do not touch the live file.
        self._mk(PERBUILD)
        os.chmod(self.apps, 0o555)
        try:
            r = self._run()
            self.assertEqual(r.returncode, 0, "must never fail the launch")
            self.assertEqual(len(list(self.apps.glob("*.OLD"))), len(PERBUILD),
                             "nothing may be unlinked when it cannot be preserved")
        finally:
            os.chmod(self.apps, 0o755)


if __name__ == "__main__":
    unittest.main()
