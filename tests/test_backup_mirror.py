"""--format mirror writes the config/saves archive as a browsable folder deck-config-<ts>/ carrying a
.mad-manifest.txt of the archived item roots. These lock in the restore-side data-safety behavior that
an adversarial review surfaced:

  * a folder with an EMPTY or ABSENT .mad-manifest.txt must be REJECTED as corrupt - otherwise the
    rule-5 pre-restore snapshot is silently skipped (live=0 -> guards bypassed) while $HOME is still
    overwritten;
  * a folder with a non-empty manifest is accepted;
  * the deck-roms-internal-* backup (a separate, newer archive with no restore branch) must NOT be
    mis-selected as the main ROMs archive by the deck-roms-* glob.

They drive deck-restore.sh with crafted fixtures - no real backup run needed.

Run:  python3 -m unittest tests.test_backup_mirror -v
"""
from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESTORE = ROOT / "deck-restore.sh"


def _mirror_folder(src: Path, ts: str, manifest):
    """deck-config-<ts>/ with one file. manifest=None -> no manifest file; "" -> empty; else that text."""
    d = src / f"deck-config-{ts}"
    (d / "home" / "deck" / ".config").mkdir(parents=True)
    (d / "home" / "deck" / ".config" / "settings.sh").write_text("livedata\n")
    if manifest is not None:
        (d / ".mad-manifest.txt").write_text(manifest)
    return d


def _tiny_tar(path: Path, member: str, mtime: int):
    with tarfile.open(path, "w") as t:
        info = tarfile.TarInfo(member)
        info.size = 0
        t.addfile(info)
    os.utime(path, (mtime, mtime))


class RestoreFolderSafety(unittest.TestCase):
    def _run(self, src: Path, stdin):
        return subprocess.run(["bash", str(RESTORE), str(src)], input=stdin,
                              capture_output=True, text=True, timeout=60)

    def test_empty_manifest_folder_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _mirror_folder(Path(d), "20260101-000000", manifest="")   # empty manifest
            r = self._run(Path(d), stdin="y\ny\n")                    # user says yes; must die first
            self.assertNotEqual(r.returncode, 0, "empty-manifest folder must abort")
            self.assertIn("corrupt", (r.stdout + r.stderr).lower())
            self.assertNotIn("config restored", r.stdout)

    def test_absent_manifest_folder_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _mirror_folder(Path(d), "20260101-000000", manifest=None)  # no manifest at all
            r = self._run(Path(d), stdin="y\ny\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("corrupt", (r.stdout + r.stderr).lower())

    def test_garbage_manifest_folder_rejected(self):
        # non-empty manifest whose entries do NOT describe this folder (corruption that replaces, not
        # truncates) must still be rejected - otherwise the snapshot is skipped while $HOME is written
        with tempfile.TemporaryDirectory() as d:
            _mirror_folder(Path(d), "20260101-000000", manifest="home/deck/totally/bogus/path\n")
            r = self._run(Path(d), stdin="y\ny\n")
            self.assertNotEqual(r.returncode, 0, "garbage-manifest folder must abort")
            self.assertIn("corrupt", (r.stdout + r.stderr).lower())

    def test_valid_manifest_folder_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            _mirror_folder(Path(d), "20260101-000000", manifest="home/deck/.config/settings.sh\n")
            r = self._run(Path(d), stdin="N\nN\nN\n")                  # detected+verified, user skips
            self.assertIn("integrity ok", r.stdout)
            self.assertIn("deck-config-20260101-000000", r.stdout)

    def test_roms_internal_not_selected_as_roms(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)
            _tiny_tar(src / "deck-roms-20260101-000000.tar", "ROMs/dummy", mtime=1)
            _tiny_tar(src / "deck-roms-internal-20260101-010000.tar", "Emulation/roms/dummy",
                      mtime=100)                                       # internal is NEWER
            r = self._run(src, stdin="N\nN\nN\n")
            self.assertIn("deck-roms-20260101-000000.tar", r.stdout)
            self.assertNotIn("deck-roms-internal", r.stdout,
                             "the internal-ROMs backup must not be picked as the main ROMs archive")


if __name__ == "__main__":
    unittest.main()
