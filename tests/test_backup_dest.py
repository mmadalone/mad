"""Local-backup DESTINATION selection.

Covers the engine side of the "choose where backups go" feature:
  - backup.run_full appends --dest (and rejects a bad/in-tree dest without leaking the
    single-run lock). Captured via a FAKE Stream so deck-backup.sh is NEVER spawned (the
    real script tars multi-GB trees - see test_backup_items.py's warning).
  - backup.get_dest / set_dest persist + validate the choice, and get falls back to the
    default when the remembered folder is gone (drive unplugged).
  - _validate_dest: accepts an existing/creatable writable dir, rejects a missing parent
    and anything inside the MAD code tree.

Run:  python3 -m unittest tests.test_backup_dest -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import backup_cmds as bc   # noqa: E402
from lib.madsrv.rpc import RpcError         # noqa: E402
from lib import mad_paths                   # noqa: E402


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


class DestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save_file = bc.DEST_FILE
        bc.DEST_FILE = self.tmp / ".backup-dest"
        # backup-prefs.json (the new home) lives under mad_paths.storage('control-panel'),
        # which is $MAD_DATA_ROOT-relative and data_root() is lru_cached - without this
        # redirect+clear every test in this file would share (and pollute) the ONE
        # data root tests/__init__.py sets up for the whole suite.
        self._env = mock.patch.dict(os.environ, {"MAD_DATA_ROOT": str(self.tmp / "data")})
        self._env.start()
        mad_paths.data_root.cache_clear()

    def tearDown(self):
        bc.DEST_FILE = self._save_file
        self._env.stop()
        mad_paths.data_root.cache_clear()
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
    """env_hygiene.clean_env (the shared helper the backup spawns use) strips Steam's Game Mode
    overlay from LD_PRELOAD so ld.so's harmless 'wrong ELF class' ERROR doesn't clutter the
    streamed backup output."""

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
        self.assertNotIn("LD_PRELOAD", bc.clean_env())

    def test_keeps_other_preloads(self):
        os.environ["LD_PRELOAD"] = "/opt/legit.so:/x/gameoverlayrenderer.so"
        self.assertEqual(bc.clean_env().get("LD_PRELOAD"), "/opt/legit.so")

    def test_noop_when_unset(self):
        os.environ.pop("LD_PRELOAD", None)
        self.assertNotIn("LD_PRELOAD", bc.clean_env())


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
        # Same reasoning as DestPersistence.setUp above: backup-prefs.json is
        # $MAD_DATA_ROOT-relative and data_root() is lru_cached, so isolate it per test.
        self._env = mock.patch.dict(os.environ, {"MAD_DATA_ROOT": str(self.tmp / "data")})
        self._env.start()
        mad_paths.data_root.cache_clear()

    def tearDown(self):
        bc.FORMAT_FILE, bc.COMPRESS_FILE = self._save_fmt, self._save_comp
        self._env.stop()
        mad_paths.data_root.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_gzip_when_unset(self):
        self.assertEqual(bc._backup_get_format({})["format"], "gzip")

    def test_set_then_get_roundtrip(self):
        for fmt in ("store", "mirror", "gzip", "zstd"):
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


class PrefsMigration(unittest.TestCase):
    """backup-prefs.json (storage/control-panel) is the NEW home for dest+format; the OLD
    .backup-dest / .backup-format / .backup-compress files lived at the LAUNCHERS (git
    clone) root - runtime state inside the clone the installer inspects, the exact pattern
    the update-flow "Train A" lesson banned (see backup_cmds._prefs_file's docstring).

    self.clone simulates that old clone-root location (bc.DEST_FILE/FORMAT_FILE/
    COMPRESS_FILE point there); self.data simulates a fresh $MAD_DATA_ROOT so
    mad_paths.storage('control-panel') resolves under it, isolated per test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clone = self.tmp / "clone"
        self.clone.mkdir()
        self.data = self.tmp / "data"
        self._save_dest, self._save_fmt, self._save_comp = (
            bc.DEST_FILE, bc.FORMAT_FILE, bc.COMPRESS_FILE)
        bc.DEST_FILE = self.clone / ".backup-dest"
        bc.FORMAT_FILE = self.clone / ".backup-format"
        bc.COMPRESS_FILE = self.clone / ".backup-compress"
        self._env = mock.patch.dict(os.environ, {"MAD_DATA_ROOT": str(self.data)})
        self._env.start()
        mad_paths.data_root.cache_clear()

    def tearDown(self):
        bc.DEST_FILE, bc.FORMAT_FILE, bc.COMPRESS_FILE = (
            self._save_dest, self._save_fmt, self._save_comp)
        self._env.stop()
        mad_paths.data_root.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prefs(self) -> dict:
        return json.loads((mad_paths.storage("control-panel") / "backup-prefs.json")
                          .read_text(encoding="utf-8"))

    def test_migrates_legacy_files_into_json(self):
        # Seed ONLY the old files - no backup-prefs.json exists yet.
        d = self.tmp / "chosen"
        d.mkdir()
        bc.DEST_FILE.write_text(str(d) + "\n", encoding="utf-8")
        bc.FORMAT_FILE.write_text("mirror\n", encoding="utf-8")

        self.assertEqual(bc._backup_get_dest({})["dest"], os.path.abspath(str(d)))
        self.assertEqual(bc._backup_get_format({})["format"], "mirror")

        # The migration write-through must have happened: the JSON now exists WITH
        # both migrated values, so the next read never touches the old files again.
        prefs = self._prefs()
        self.assertEqual(prefs["dest"], os.path.abspath(str(d)))
        self.assertEqual(prefs["format"], "mirror")

    def test_legacy_compress_zero_migrates_to_store_and_persists(self):
        bc.COMPRESS_FILE.write_text("0\n", encoding="utf-8")   # legacy "compress off"
        self.assertEqual(bc._backup_get_format({})["format"], "store")
        self.assertEqual(self._prefs()["format"], "store")

    def test_set_dest_and_format_write_json_not_clone_root(self):
        d = self.tmp / "picked"
        d.mkdir()
        self.assertEqual(bc._backup_set_dest({"dest": str(d)})["dest"], os.path.abspath(str(d)))
        self.assertEqual(bc._backup_set_format({"format": "store"})["format"], "store")

        # A fresh set() must land ONLY in backup-prefs.json - the clone-root files are
        # never created by the setters any more.
        self.assertFalse(bc.DEST_FILE.exists(),
                         "backup.set_dest wrote into the launchers clone root")
        self.assertFalse(bc.FORMAT_FILE.exists(),
                         "backup.set_format wrote into the launchers clone root")

        prefs = self._prefs()
        self.assertEqual(prefs["dest"], os.path.abspath(str(d)))
        self.assertEqual(prefs["format"], "store")

        # And a subsequent get reads the JSON straight back, with no legacy files present.
        self.assertEqual(bc._backup_get_dest({})["dest"], os.path.abspath(str(d)))
        self.assertEqual(bc._backup_get_format({})["format"], "store")

    def test_non_utf8_prefs_file_falls_back_not_raises(self):
        # audit 2026-08-12 phase 5 review MED-1: _read_prefs caught only OSError, but
        # Path.read_text(encoding="utf-8") raises UnicodeDecodeError (a ValueError, not an
        # OSError) on stray non-UTF-8 bytes - so a damaged prefs file crashed every getter
        # AND set_dest instead of degrading to "nothing remembered yet" as the docstring
        # promised. Reproduce with the exact bytes from the finding and hit all four call
        # sites the reviewer flagged as blast radius.
        prefs_file = mad_paths.storage("control-panel") / "backup-prefs.json"
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_bytes(b'\xff\xfe\x00{"dest": "/tmp"}')

        self.assertEqual(bc._read_prefs(), {})
        self.assertEqual(bc._backup_get_dest({})["dest"], bc.DEFAULT_DEST)
        self.assertEqual(bc._backup_get_format({})["format"], "gzip")
        d = self.tmp / "picked-after-corrupt"
        d.mkdir()
        # set_dest must still work - a damaged file must not lock the user out of ever
        # re-picking a folder to overwrite it (the finding's "recovery needs Desktop Mode").
        self.assertEqual(bc._backup_set_dest({"dest": str(d)})["dest"], os.path.abspath(str(d)))

    def test_non_str_stored_dest_ignored_and_self_heals(self):
        # audit 2026-08-12 phase 5 review MED-1 part 2: a wrong-typed 'dest' (e.g. from a
        # hand-edited or otherwise corrupted JSON) survived _read_prefs (it's still valid
        # JSON, still a dict) and blew up later: os.path.isdir(["/tmp"]) raises TypeError.
        # _remembered_dest must discard a non-str dest the same way _remembered_format
        # already discards a format outside _FORMATS, falling back to the default.
        prefs_file = mad_paths.storage("control-panel") / "backup-prefs.json"
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text('{"dest": ["/tmp"]}', encoding="utf-8")

        self.assertEqual(bc._backup_get_dest({})["dest"], bc.DEFAULT_DEST)
        # Self-heal: the bad value must not survive in the JSON for the next read to trip
        # over again (mirrors legacy-migration write-through elsewhere in this module).
        self.assertNotIsInstance(self._prefs().get("dest"), list)

    def test_migration_persist_failure_does_not_raise(self):
        # A getter must never raise even if the write-through to backup-prefs.json fails
        # (e.g. a read-only/full storage dir) - it still returns the migrated value.
        d = self.tmp / "chosen2"
        d.mkdir()
        bc.DEST_FILE.write_text(str(d) + "\n", encoding="utf-8")
        with mock.patch.object(bc.fsutil, "atomic_write_json",
                               side_effect=OSError("disk full")):
            self.assertEqual(bc._backup_get_dest({})["dest"], os.path.abspath(str(d)))
        # No JSON was persisted (the write failed), so a later successful read migrates again.
        self.assertEqual(bc._backup_get_dest({})["dest"], os.path.abspath(str(d)))
        self.assertEqual(self._prefs()["dest"], os.path.abspath(str(d)))


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
        for fmt in ("gzip", "store", "mirror", "zstd"):
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
