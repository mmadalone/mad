"""Regression tests for mad_backup.restore_router_backups.

Covers the review finding (2026-07-15 #1): a cemu-style DIR target's pristine
snapshot is a `.router-backup` SUBDIR of files, but the old code globbed that
hidden dir and stripped `.router-backup` off its name -> with_name('') raised a
ValueError that escaped the try/except and aborted the ENTIRE restore (cemu is
typically processed first, so nothing got restored). These tests assert the dir
target restores its pristine files, that a following file target still restores,
and that no exception escapes.

Run:  python3 -m unittest tests.test_mad_backup_restore -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import mad_backup


class RestoreRouterBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cemu_dir(self):
        """A cemu config_dir: live (MAD-edited) controllerN.xml + a .router-backup
        SUBDIR holding the pristine originals (as backup_active_once creates)."""
        cdir = self.tmp / "controllerProfiles"
        cdir.mkdir()
        (cdir / "controller0.xml").write_text("LIVE-0")
        (cdir / "controller1.xml").write_text("LIVE-1")
        rb = cdir / ".router-backup"
        rb.mkdir()
        (rb / "controller0.xml").write_text("PRISTINE-0")
        (rb / "controller1.xml").write_text("PRISTINE-1")
        return cdir

    def test_dir_target_restores_pristine_files(self):
        cdir = self._cemu_dir()
        msg = mad_backup.restore_router_backups({"cemu": cdir})
        # The live files are reverted to the pristine snapshot contents.
        self.assertEqual((cdir / "controller0.xml").read_text(), "PRISTINE-0")
        self.assertEqual((cdir / "controller1.xml").read_text(), "PRISTINE-1")
        self.assertIn("controller0.xml", msg)
        self.assertIn("controller1.xml", msg)

    def test_dir_target_does_not_abort_following_file_target(self):
        """The original crash aborted the whole loop; ensure a file target that
        comes AFTER the cemu dir target still gets restored."""
        cdir = self._cemu_dir()
        # An eden-style file target with a sibling .router-backup pristine file.
        fdir = self.tmp / "eden"
        fdir.mkdir()
        live = fdir / "qt-config.ini"
        live.write_text("LIVE-edited")
        (fdir / "qt-config.ini.router-backup").write_text("PRISTINE-ini")
        # dict order: cemu (dir) first, then the file target — the crash case.
        msg = mad_backup.restore_router_backups({"cemu": cdir, "eden": live})
        self.assertEqual(live.read_text(), "PRISTINE-ini")
        self.assertEqual((cdir / "controller0.xml").read_text(), "PRISTINE-0")
        self.assertIn("qt-config.ini", msg)

    def test_file_target_bak_and_router_backup(self):
        fdir = self.tmp / "pcsx2"
        fdir.mkdir()
        live = fdir / "PCSX2.ini"
        live.write_text("LIVE")
        (fdir / "PCSX2.ini.bak").write_text("PRISTINE-bak")
        msg = mad_backup.restore_router_backups({"pcsx2": live})
        self.assertEqual(live.read_text(), "PRISTINE-bak")
        self.assertIn("PCSX2.ini", msg)

    def test_dir_target_no_backup_is_graceful(self):
        cdir = self.tmp / "controllerProfiles"
        cdir.mkdir()
        (cdir / "controller0.xml").write_text("LIVE-0")
        msg = mad_backup.restore_router_backups({"cemu": cdir})
        # No .router-backup present -> nothing restored, no crash, clear message.
        self.assertEqual((cdir / "controller0.xml").read_text(), "LIVE-0")
        self.assertIn("No input backups", msg)

    def test_no_exception_escapes_on_dir_target(self):
        """The exact bug: restoring a cemu dir target must not raise."""
        cdir = self._cemu_dir()
        try:
            mad_backup.restore_router_backups({"cemu": cdir})
        except Exception as e:  # noqa: BLE001
            self.fail(f"restore_router_backups raised {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
