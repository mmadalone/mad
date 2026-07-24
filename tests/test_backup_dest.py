"""Local-backup DESTINATION selection.

Covers the engine side of the "choose where backups go" feature:
  - backup.run_full appends --dest (and rejects a bad/in-tree dest without leaking the
    single-run lock). Captured via a FAKE Stream so deck-backup.sh is NEVER spawned (the
    real script tars multi-GB trees - see test_backup_items.py's warning).
  - backup.mad_code writes its tarball under the chosen dir (tarred over a miniature stub
    launchers tree so the real tar is tiny + fast).
  - backup.get_dest / set_dest persist + validate the choice, and get falls back to the
    default when the remembered folder is gone (drive unplugged).
  - _validate_dest: accepts an existing/creatable writable dir, rejects a missing parent
    and anything inside the MAD code tree.

Run:  python3 -m unittest tests.test_backup_dest -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import backup_cmds as bc   # noqa: E402
from lib.madsrv.rpc import RpcError         # noqa: E402
from lib import mad_backup                  # noqa: E402


class _FakeStream:
    """Records the argv backup.run_full would spawn, without running deck-backup.sh."""

    last_argv = None

    def __init__(self, argv):
        _FakeStream.last_argv = list(argv)

    def start(self):
        return "tok-test"


class RunFullDest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save_stream = bc.RunFullStream
        bc.RunFullStream = _FakeStream
        _FakeStream.last_argv = None

    def tearDown(self):
        bc.RunFullStream = self._save_stream
        # FakeStream.start() never runs the real run()'s finally, so the lock stays ours.
        if bc._RUN_ACTIVE.locked():
            bc._RUN_ACTIVE.release()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dest_is_appended(self):
        bc._backup_run_full({"include": {"saves": True}, "dest": str(self.tmp)})
        argv = _FakeStream.last_argv
        self.assertIn("--dest", argv)
        self.assertEqual(argv[argv.index("--dest") + 1], os.path.abspath(str(self.tmp)))
        # the include flags still ride along
        self.assertIn("--saves", argv)
        self.assertIn("--no-bios", argv)

    def test_no_dest_keeps_default(self):
        bc._backup_run_full({"include": {"saves": True}})
        self.assertNotIn("--dest", _FakeStream.last_argv)

    def test_invalid_dest_raises_and_frees_lock(self):
        with self.assertRaises(RpcError):
            bc._backup_run_full({"include": {}, "dest": "/no/such/parent/here"})
        self.assertFalse(bc._RUN_ACTIVE.locked(), "lock leaked on validation failure")

    def test_in_tree_dest_rejected(self):
        with self.assertRaises(RpcError):
            bc._backup_run_full({"include": {}, "dest": str(bc.LAUNCHERS / "sub")})
        self.assertFalse(bc._RUN_ACTIVE.locked())


class MadCodeDest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # A miniature launchers tree so backup_mad_code's real tar is tiny + fast.
        self.src = self.tmp / "src"
        (self.src / "launchers" / "lib").mkdir(parents=True)
        (self.src / "launchers" / "deck-backup.sh").write_text("#!/bin/sh\n")
        self._save_l = mad_backup.LAUNCHERS
        mad_backup.LAUNCHERS = self.src / "launchers"

    def tearDown(self):
        mad_backup.LAUNCHERS = self._save_l
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tar_lands_under_chosen_dest(self):
        dest = self.tmp / "out"
        dest.mkdir()
        msg = mad_backup.backup_mad_code(dest_dir=str(dest))
        self.assertEqual(len(list(dest.glob("mad-code-*.tar.gz"))), 1, msg)

    def test_default_dir_used_when_none(self):
        # dest_dir=None -> the built-in ~/deck-config-backups (redirect HOME so the test
        # can't touch the real one).
        home = self.tmp / "home"
        home.mkdir()
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            mad_backup.backup_mad_code()
        finally:
            if old is not None:
                os.environ["HOME"] = old
        self.assertEqual(len(list((home / "deck-config-backups").glob("mad-code-*.tar.gz"))), 1)

    def test_rpc_validates_and_passes_dest(self):
        dest = self.tmp / "out2"
        dest.mkdir()
        r = bc._backup_mad_code({"dest": str(dest)})
        self.assertIn("mad-code-", r["message"])
        self.assertEqual(len(list(dest.glob("mad-code-*.tar.gz"))), 1)


class DestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save_file = bc.DEST_FILE
        bc.DEST_FILE = self.tmp / ".backup-dest"

    def tearDown(self):
        bc.DEST_FILE = self._save_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_when_unset(self):
        self.assertEqual(bc._backup_get_dest({})["dest"], bc.DEFAULT_DEST)

    def test_set_then_get_roundtrip(self):
        d = self.tmp / "chosen"
        d.mkdir()
        want = os.path.abspath(str(d))
        self.assertEqual(bc._backup_set_dest({"dest": str(d)})["dest"], want)
        self.assertEqual(bc._backup_get_dest({})["dest"], want)

    def test_set_rejects_bad_path(self):
        with self.assertRaises(RpcError):
            bc._backup_set_dest({"dest": "/no/such/parent/x"})

    def test_get_falls_back_when_remembered_dir_gone(self):
        d = self.tmp / "usb"
        d.mkdir()
        bc._backup_set_dest({"dest": str(d)})
        shutil.rmtree(d)                                   # drive unplugged
        self.assertEqual(bc._backup_get_dest({})["dest"], bc.DEFAULT_DEST)


class ValidateDest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accepts_existing_writable_dir(self):
        self.assertEqual(bc._validate_dest(str(self.tmp)), os.path.abspath(str(self.tmp)))

    def test_creates_when_parent_exists(self):
        d = self.tmp / "new"
        self.assertEqual(bc._validate_dest(str(d)), os.path.abspath(str(d)))
        self.assertTrue(d.is_dir())

    def test_rejects_missing_parent(self):
        with self.assertRaises(RpcError):
            bc._validate_dest(str(self.tmp / "a" / "b"))

    def test_rejects_in_tree(self):
        with self.assertRaises(RpcError):
            bc._validate_dest(str(bc.LAUNCHERS / "x"))

    def test_rejects_dest_inside_a_backup_source_tree(self):
        # A dest at/under a tree deck-backup.sh archives must be refused (else successive full
        # backups would swallow the prior archives sitting there). _source_roots() is the seam.
        root = self.tmp / "ROMs"
        (root / "sub").mkdir(parents=True)
        save = bc._source_roots
        bc._source_roots = lambda: [os.path.realpath(str(root))]
        try:
            with self.assertRaises(RpcError):
                bc._validate_dest(str(root))            # the root itself
            with self.assertRaises(RpcError):
                bc._validate_dest(str(root / "sub"))    # a folder under it
            # a sibling OUTSIDE the root is still fine
            other = self.tmp / "elsewhere"
            other.mkdir()
            self.assertEqual(bc._validate_dest(str(other)), os.path.abspath(str(other)))
        finally:
            bc._source_roots = save

    def test_expands_user(self):
        self.assertEqual(bc._validate_dest("~"), os.path.abspath(os.path.expanduser("~")))


class CleanEnv(unittest.TestCase):
    """_clean_env strips Steam's Game Mode overlay from LD_PRELOAD so ld.so's harmless 'wrong ELF
    class' ERROR doesn't clutter the streamed backup output."""

    def setUp(self):
        self._save = os.environ.get("LD_PRELOAD")

    def tearDown(self):
        if self._save is None:
            os.environ.pop("LD_PRELOAD", None)
        else:
            os.environ["LD_PRELOAD"] = self._save

    def test_strips_steam_overlay_entirely(self):
        os.environ["LD_PRELOAD"] = ("/x/ubuntu12_64/gameoverlayrenderer.so:"
                                    "/x/ubuntu12_32/gameoverlayrenderer.so")
        self.assertNotIn("LD_PRELOAD", bc._clean_env())

    def test_keeps_other_preloads(self):
        os.environ["LD_PRELOAD"] = "/opt/legit.so:/x/gameoverlayrenderer.so"
        self.assertEqual(bc._clean_env().get("LD_PRELOAD"), "/opt/legit.so")

    def test_noop_when_unset(self):
        os.environ.pop("LD_PRELOAD", None)
        self.assertNotIn("LD_PRELOAD", bc._clean_env())


class RunFullCompress(unittest.TestCase):
    def setUp(self):
        self._save_stream = bc.RunFullStream
        bc.RunFullStream = _FakeStream
        _FakeStream.last_argv = None

    def tearDown(self):
        bc.RunFullStream = self._save_stream
        if bc._RUN_ACTIVE.locked():
            bc._RUN_ACTIVE.release()

    def test_compress_true_appends_flag(self):
        bc._backup_run_full({"include": {}, "compress": True})
        self.assertIn("--compress", _FakeStream.last_argv)
        self.assertNotIn("--no-compress", _FakeStream.last_argv)

    def test_compress_false_appends_no_compress(self):
        bc._backup_run_full({"include": {}, "compress": False})
        self.assertIn("--no-compress", _FakeStream.last_argv)

    def test_absent_compress_appends_nothing(self):
        bc._backup_run_full({"include": {}})
        self.assertNotIn("--compress", _FakeStream.last_argv)
        self.assertNotIn("--no-compress", _FakeStream.last_argv)


class FormatPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save_fmt, self._save_comp = bc.FORMAT_FILE, bc.COMPRESS_FILE
        bc.FORMAT_FILE = self.tmp / ".backup-format"
        bc.COMPRESS_FILE = self.tmp / ".backup-compress"

    def tearDown(self):
        bc.FORMAT_FILE, bc.COMPRESS_FILE = self._save_fmt, self._save_comp
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_gzip_when_unset(self):
        self.assertEqual(bc._backup_get_format({})["format"], "gzip")

    def test_set_then_get_roundtrip(self):
        for fmt in ("store", "mirror", "gzip"):
            self.assertEqual(bc._backup_set_format({"format": fmt})["format"], fmt)
            self.assertEqual(bc._backup_get_format({})["format"], fmt)

    def test_reject_unknown_format(self):
        with self.assertRaises(bc.RpcError):
            bc._backup_set_format({"format": "zip"})

    def test_migrates_legacy_compress_zero_to_store(self):
        bc.COMPRESS_FILE.write_text("0\n", encoding="utf-8")   # legacy "compress off"
        self.assertEqual(bc._backup_get_format({})["format"], "store")

    def test_migrates_legacy_compress_one_to_gzip(self):
        bc.COMPRESS_FILE.write_text("1\n", encoding="utf-8")
        self.assertEqual(bc._backup_get_format({})["format"], "gzip")

    def test_format_file_wins_over_legacy_compress(self):
        bc.COMPRESS_FILE.write_text("0\n", encoding="utf-8")
        bc.FORMAT_FILE.write_text("mirror\n", encoding="utf-8")
        self.assertEqual(bc._backup_get_format({})["format"], "mirror")


class RunFullFormat(unittest.TestCase):
    def setUp(self):
        self._save_stream = bc.RunFullStream
        bc.RunFullStream = _FakeStream
        _FakeStream.last_argv = None

    def tearDown(self):
        bc.RunFullStream = self._save_stream
        if bc._RUN_ACTIVE.locked():
            bc._RUN_ACTIVE.release()

    def test_format_appends_flag(self):
        for fmt in ("gzip", "store", "mirror"):
            bc._backup_run_full({"include": {}, "format": fmt})
            argv = _FakeStream.last_argv
            self.assertIn("--format", argv)
            self.assertEqual(argv[argv.index("--format") + 1], fmt)
            bc._RUN_ACTIVE.release()

    def test_format_takes_precedence_over_legacy_compress(self):
        bc._backup_run_full({"include": {}, "format": "mirror", "compress": True})
        argv = _FakeStream.last_argv
        self.assertIn("--format", argv)
        self.assertNotIn("--compress", argv)

    def test_unknown_format_ignored_falls_to_compress(self):
        bc._backup_run_full({"include": {}, "format": "bogus", "compress": False})
        argv = _FakeStream.last_argv
        self.assertNotIn("--format", argv)
        self.assertIn("--no-compress", argv)


if __name__ == "__main__":
    unittest.main()
