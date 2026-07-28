"""Debris exclusion (P8b): OS/VCS junk (.DS_Store, ._*, Thumbs.db, Desktop.ini, .gitattributes/.gitignore,
.git/, __pycache__/) is dropped on the BACKUP write path, but a RESTORE stays byte-faithful. The aggressive
*.bak*/*.tmp temp globs are deliberately NOT here (they can be real game/config filenames).

Run:  python3 -m unittest tests.test_backup_debris -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_debris as bd                    # noqa: E402
from lib import granular_backup as gb                  # noqa: E402
from lib import bios_map, emu_map                       # noqa: E402


class Predicate(unittest.TestCase):
    def test_junk_files(self):
        for n in [".DS_Store", ".ds_store", "Thumbs.db", "thumbs.db", "Desktop.ini", "desktop.ini",
                  ".gitattributes", ".gitignore", "._sonic.png", "._", "ehthumbs.db"]:
            self.assertTrue(bd.is_debris_file(n), n)

    def test_real_files_kept(self):
        # NOT debris: real ROM/save/config names, including *.bak (a legit filename, left to emu excludes).
        for n in ["sonic.zip", "game.srm", "PCSX2.ini", "save.bak", "notes.txt", "GX.p2s", ".mad-restore"]:
            self.assertFalse(bd.is_debris_file(n), n)

    def test_junk_dirs(self):
        for d in [".git", ".Git", "__pycache__", ".Spotlight-V100", ".Trashes", ".fseventsd"]:
            self.assertTrue(bd.is_debris_dir(d), d)
        for d in ["nes", "textures", "saves", "config"]:
            self.assertFalse(bd.is_debris_dir(d), d)


class CopyAndSize(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.src = self.base / "game"
        (self.src / ".git").mkdir(parents=True)
        (self.src / ".git" / "HEAD").write_bytes(b"ref")            # inside a junk dir
        (self.src / "sub").mkdir()
        (self.src / "sub" / "level.dat").write_bytes(b"REAL" * 10)  # real nested file
        (self.src / "rom.bin").write_bytes(b"ROM" * 100)            # real file
        (self.src / ".DS_Store").write_bytes(b"junk")
        (self.src / "._rom.bin").write_bytes(b"junk")
        (self.src / "Thumbs.db").write_bytes(b"junk")
        (self.src / ".gitattributes").write_bytes(b"junk")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _copied_names(self, dst):
        return {p.name for p in Path(dst).rglob("*") if p.is_file()}

    def test_backup_skips_debris(self):
        dst = self.base / "backup"
        gb._copy_path(str(self.src), str(dst), lambda e: None, lambda: False, skip_debris=True)
        names = self._copied_names(dst)
        self.assertEqual(names, {"rom.bin", "level.dat"})
        self.assertFalse((Path(dst) / ".git").exists(), ".git dir is pruned whole")

    def test_restore_is_byte_faithful(self):
        # skip_debris defaults False on restore: an OLD dirty backup reproduces exactly, nothing dropped.
        dst = self.base / "restore"
        gb._copy_path(str(self.src), str(dst), lambda e: None, lambda: False)
        names = self._copied_names(dst)
        self.assertIn(".DS_Store", names)
        self.assertIn("._rom.bin", names)
        self.assertIn("HEAD", names)  # .git/HEAD reproduced

    def test_path_size_honest(self):
        full = gb._path_size(str(self.src))
        clean = gb._path_size(str(self.src), skip_debris=True)
        self.assertLess(clean, full, "debris bytes excluded from the backup-honest size")
        # clean == exactly the two real files
        self.assertEqual(clean, len(b"ROM" * 100) + len(b"REAL" * 10))


class Enumerators(unittest.TestCase):
    def test_bios_map_skips_debris(self):
        base = Path(tempfile.mkdtemp())
        try:
            (base / "ps2").mkdir()
            (base / "ps2" / "scph.bin").write_bytes(b"BIOS")
            (base / "ps2" / ".DS_Store").write_bytes(b"junk")
            (base / "._scph.bin").write_bytes(b"junk")
            rels = {f["rel"] for b in bios_map.list_buckets(base) for f in b["files"]}
            self.assertIn("bios/ps2/scph.bin", rels)
            self.assertNotIn("bios/ps2/.DS_Store", rels)
            self.assertNotIn("bios/._scph.bin", rels)
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)

    def test_emu_map_excluded_covers_debris(self):
        self.assertTrue(emu_map._excluded(".DS_Store", None))
        self.assertTrue(emu_map._excluded("sub/._x.ini", None))
        self.assertTrue(emu_map._excluded(".git/config", None))
        self.assertFalse(emu_map._excluded("PCSX2.ini", None))


class ShellDriftGuard(unittest.TestCase):
    """The rclone paths build their excludes from lib.backup_debris (no drift by construction). The grep
    (--files-from precious) and the tar archive can't, so assert they still cover the debris basenames."""
    def test_shell_excludes_cover_debris(self):
        cloud = (ROOT / "deck-cloud.sh").read_text()
        backup = (ROOT / "deck-backup.sh").read_text()
        for token in ["._", ".DS_Store", "humbs.db", "esktop.ini", ".gitattributes", ".gitignore"]:
            self.assertIn(token, cloud, f"deck-cloud.sh _cloud_debris_filter must drop {token}")
        for token in ["._*", ".gitattributes", ".gitignore", "Desktop.ini"]:
            self.assertIn(token, backup, f"deck-backup.sh EXCLUDES must drop {token}")


if __name__ == "__main__":
    unittest.main()
