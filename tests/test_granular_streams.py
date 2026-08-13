"""granular_backup engine + granular.restore RPC wrapper - the WRITE path (highest risk).

These drive the real restore engine against a SANDBOX rom_root (no real library touched) and lock
in the safety-critical contracts:

  * RULE #5: restore moves any existing target ASIDE to a same-fs snapshot (content preserved) with a
    RECOVERY.txt + rollback line BEFORE writing the restored copy; a fresh target makes no snapshot;
  * restore REJECTS an invalid/foreign manifest and SKIPS an item whose backup file is missing;
  * the ES-DE-closed guard fires for a category that needs it (engine RuntimeError + RPC EBUSY);
  * cancellation raises Cancelled (restore);
  * the RPC wrapper rejects bad params (EINVAL) and refuses a concurrent op (EBUSY).

granular.backup (+ its engine, plan_selection/backup_selection) was retired audit 2026-08-12 phase 5:
dead RPC, no C++/script/hook caller (granular.backup_assets is the live per-game backup path now).
_seed_roms_backup below is a TEST-ONLY replica of the old plan_selection+backup_selection - it exists
solely to build a real on-disk "roms" backup fixture for the restore-side tests in this file (restore_
selection / restore_preview are both still LIVE production code and need real fixtures to restore
FROM). It is not imported by, or a stand-in for, any production path.

Run:  python3 -m unittest tests.test_granular_streams -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm                # noqa: E402
from lib import es_systems, game_files, granular_backup as gb  # noqa: E402
from lib.madsrv import granular_cmds as g            # noqa: E402
from lib.madsrv.rpc import RpcError                  # noqa: E402

NO_STOP = lambda: False


def _seed_roms_backup(items, dest_dir, ts="20260724T120000", emit=None, is_stopped=None):
    """TEST-ONLY stand-in for the retired granular_backup.plan_selection + backup_selection (see the
    module docstring): resolves `items` via the SAME game_files.resolve_rom the engine used, copies
    each ROM file/folder into a "roms" granular backup dir under `dest_dir`, and writes a matching
    mad-manifest.json - mirroring the old rel-path convention (roms/<system>/<relpath>) exactly, so
    every restore assertion below still holds. Returns {path, copied, skipped}."""
    is_stopped = is_stopped or NO_STOP
    emit = emit or (lambda d: None)
    manifest = bm.new_manifest("granular", created=ts)
    rom_root = gb.es_collections.rom_root()
    backupdir = gb._backup_dir(str(dest_dir), "games", ts, versioned=False)
    backupdir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for it in items:
        if is_stopped():
            raise gb.Cancelled()
        system, stem = it["system"], it["stem"]
        paths = game_files.resolve_rom(system, stem)
        name = gb.es_gamelist_record(system, stem).get("name") or stem
        if not paths:
            emit({"line": f"skip (ROM missing): {name}"})
            continue
        src = os.path.realpath(paths[0])
        sysdir = os.path.realpath(str(rom_root / system))
        rel_rom = os.path.relpath(src, sysdir)
        kind = "folder" if os.path.isdir(src) else "file"
        rel = f"roms/{system}/{rel_rom}"
        dst = backupdir / rel
        if kind == "folder":
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        bm.add_item(manifest, category="roms", category_label="ROMs & games",
                   system=system, system_label=es_systems.fullname(system),
                   item=bm.make_item(id=f"{system}:{stem}", name=name, src=src, rel=rel,
                                     kind=kind, size=gb._path_size(src), stem=stem))
        copied += 1
        emit({"item_done": f"{system}:{stem}", "copied": copied})
    if copied:
        # DRIFT TRIPWIRE, narrowly scoped (audit 2026-08-12 phase 5 review LOW-4 corrected two
        # overclaims in the previous wording of this comment): this is NOT the one place in the
        # suite that hand-builds a backup fixture instead of using a production writer -
        # test_backup_merge.py, test_cloud_restore.py, test_cloud_launchers_backup.py and
        # test_game_restore.py all do the same bm.new_manifest/add_item construction, this is
        # just one of several. And bm.validate does NOT catch item-SHAPE drift - it is an
        # envelope check only (schema int matches + at least one category/system has a non-empty
        # items list of dicts); it returns True even for a garbage item like [{"totally":
        # "wrong"}], since _item_list only checks isinstance(i, dict). What this assert DOES
        # catch: a bump to backup_manifest.SCHEMA that this fixture wasn't updated for - the
        # narrower, still-real failure mode of "the manifest ENVELOPE this helper writes no
        # longer matches what real backups carry."
        assert bm.validate(manifest), (
            "test fixture no longer matches backup_manifest's schema - the real backup format "
            "moved; update _seed_roms_backup to match before trusting the restore tests below")
        gb._write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "skipped": len(items) - copied}


class _Sandbox(unittest.TestCase):
    """A temp rom_root with a file ROM (nes/smb.zip) + a folder ROM (ps3/MyGame), and the resolvers
    monkeypatched to it. Box art absent, names synthesized."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.romroot = self.tmp / "ROMs"
        (self.romroot / "nes").mkdir(parents=True)
        (self.romroot / "ps3").mkdir(parents=True)
        (self.romroot / "nes" / "smb.zip").write_bytes(b"MARIO" * 100)
        gamedir = self.romroot / "ps3" / "MyGame"
        gamedir.mkdir()
        (gamedir / "EBOOT.BIN").write_bytes(b"X" * 50)
        self.dest = self.tmp / "backups"
        self.dest.mkdir()
        self.sink = []

        def fake_resolve(system, stem):
            p = {("nes", "smb"): self.romroot / "nes" / "smb.zip",
                 ("ps3", "MyGame"): self.romroot / "ps3" / "MyGame"}.get((system, stem))
            return [str(p)] if p and p.exists() else []

        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.romroot),
            mock.patch.object(game_files, "resolve_rom", fake_resolve),
            mock.patch.object(game_files, "resolve_boxart", lambda s, st: {}),
            mock.patch.object(es_systems, "fullname", lambda s: s.upper()),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": f"{st} (Game)"}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def emit(self, d):
        self.sink.append(d)

    def _backup(self, items, ts="20260724T120000"):
        return _seed_roms_backup(items, self.dest, ts, self.emit)


class RestoreRule5(_Sandbox):
    def _backup_then(self, corrupt=True):
        r = self._backup([{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}])
        if corrupt:
            (self.romroot / "nes" / "smb.zip").write_bytes(b"CORRUPT")
        return Path(r["path"])

    def test_restore_snapshots_existing_then_overwrites(self):
        bdir = self._backup_then(corrupt=True)
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:smb"},
                                              {"system": "ps3", "id": "ps3:MyGame"}],
                                  "roms", "20260724T130000", self.emit, NO_STOP)
        self.assertEqual((rr["restored"], rr["skipped"]), (2, 0))
        self.assertEqual(rr["replaced"], 2, "both live games existed -> both counted as replaced")
        self.assertEqual(rr["restart_scope"], "none")
        # restored content is the backed-up original, not the corrupt live copy
        self.assertEqual((self.romroot / "nes" / "smb.zip").read_bytes(), b"MARIO" * 100)
        # rule 5: the corrupt pre-restore copy was preserved in the snapshot + a rollback line written
        snap = Path(rr["snapshot"])
        self.assertEqual((snap / "nes" / "smb.zip").read_bytes(), b"CORRUPT")
        rec = (snap / "RECOVERY.txt").read_text()
        self.assertIn("mv", rec)
        self.assertIn("nes/smb.zip", rec)

    def test_restore_fresh_target_no_snapshot(self):
        bdir = self._backup_then(corrupt=False)
        (self.romroot / "nes" / "smb.zip").unlink()          # target absent -> nothing to snapshot
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:smb"}],
                                  "roms", "20260724T140000", self.emit, NO_STOP)
        self.assertEqual(rr["restored"], 1)
        self.assertIsNone(rr["snapshot"], "a fresh target must not create a snapshot")
        self.assertEqual((self.romroot / "nes" / "smb.zip").read_bytes(), b"MARIO" * 100)

    def test_restore_skips_item_missing_in_backup(self):
        bdir = self._backup_then(corrupt=False)
        (bdir / "roms/nes/smb.zip").unlink()                 # corrupt the backup: file gone
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:smb"}],
                                  "roms", "20260724T150000", self.emit, NO_STOP)
        self.assertEqual((rr["restored"], rr["skipped"]), (0, 1))

    def test_cancel_raises(self):
        bdir = self._backup_then(corrupt=False)
        with self.assertRaises(gb.Cancelled):
            gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:smb"}],
                                 "roms", "20260724T160000", self.emit, lambda: True)


class BackupCancel(unittest.TestCase):
    """audit 2026-08-12 phase 5 review MED-3: the retired granular_backup.backup_selection
    used to be the ONLY test anywhere driving a backup_* engine function's is_stopped()
    guard (see git show HEAD:tests/test_granular_streams.py, class Backup.test_cancel_raises).
    Once backup_selection was retired that test went with it, dropping backup-side
    cancellation coverage to ZERO: the (copy-pasted, identical-shaped) `if is_stopped():
    raise Cancelled()` guard in backup_game_assets/backup_bios/backup_esde/backup_emucfg/
    backup_system/backup_controllers could be deleted from any of them and the suite would
    stay green while the Stop button silently became a no-op. Verified this test actually
    catches that: with the guard stubbed out (scratch in-memory module, lib/ untouched),
    the same call raises nothing.

    One representative test against the LIVE backup_game_assets closes the gap - the other
    five backup_* functions share the exact same plan_X-then-copy-loop guard shape, so this
    is not exhaustive per-function coverage, just proof the pattern is watched again.
    Modeled on the deleted Backup.test_cancel_raises and on RestoreRule5.test_cancel_raises
    below in this same file."""

    def test_backup_game_assets_cancel_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gb.Cancelled):
                gb.backup_game_assets(
                    [{"system": "nes", "stem": "smb", "keys": ["rom"]}],
                    d, "20260724T120000", lambda ev: None, lambda: True)


class RestoreGuards(_Sandbox):
    def test_bad_manifest_rejected(self):
        with self.assertRaises(ValueError):
            gb.restore_selection(str(self.tmp), [{"system": "nes", "id": "nes:smb"}],
                                 "roms", "20260724T170000", self.emit, NO_STOP)

    def test_needs_esde_closed_category_raises_when_running(self):
        with mock.patch.object(gb, "category_meta",
                               lambda c: {"needs_esde_stopped": True, "restart_scope": "esde"}), \
             mock.patch.object(gb.proc_guard, "esde_running", lambda: True):
            r = self._backup([{"system": "nes", "stem": "smb"}])
            with self.assertRaises(RuntimeError):
                gb.restore_selection(r["path"], [{"system": "nes", "id": "nes:smb"}],
                                     "roms", "20260724T180000", self.emit, NO_STOP)


class RestorePreview(_Sandbox):
    """The read-only preview that drives the "these will be replaced" warning must agree with what the
    restore actually overwrites."""
    def test_preview_classifies_replace_fresh_skip(self):
        r = self._backup([{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}])
        bdir = r["path"]
        # nes/smb still exists live (replace); remove ps3 target so it's fresh; add a bogus id (skip)
        import shutil as _sh
        _sh.rmtree(self.romroot / "ps3" / "MyGame")
        pv = gb.restore_preview(bdir, [{"system": "nes", "id": "nes:smb"},
                                       {"system": "ps3", "id": "ps3:MyGame"},
                                       {"system": "nes", "id": "nes:ghost"}], "roms")
        self.assertEqual([x["id"] for x in pv["replace"]], ["nes:smb"])
        self.assertEqual([x["id"] for x in pv["fresh"]], ["ps3:MyGame"])
        self.assertEqual([x["id"] for x in pv["skip"]], ["nes:ghost"])
        self.assertEqual(pv["restart_scope"], "none")

    def test_preview_matches_restore_replaced_count(self):
        r = self._backup([{"system": "nes", "stem": "smb"}])
        bdir = r["path"]
        pv = gb.restore_preview(bdir, [{"system": "nes", "id": "nes:smb"}], "roms")
        rr = gb.restore_selection(bdir, [{"system": "nes", "id": "nes:smb"}], "roms",
                                  "20260724T210000", self.emit, NO_STOP)
        self.assertEqual(len(pv["replace"]), rr["replaced"],
                         "preview's replace count must equal the restore's replaced count")

    def test_preview_rejects_bad_manifest(self):
        with self.assertRaises(ValueError):
            gb.restore_preview(str(self.tmp), [{"system": "nes", "id": "nes:smb"}], "roms")


class TraversalSafety(_Sandbox):
    """A foreign/corrupt manifest must never make restore write outside the ROM root or read outside the
    backup folder. Every unsafe item is skipped; nothing lands on disk beyond the sandbox."""
    def _manifest_backup(self, system, rel):
        bdir = self.dest / "deck-granular-evil"
        (bdir / "roms").mkdir(parents=True)
        (bdir / "payload").write_bytes(b"PWNED")
        m = bm.new_manifest("granular", created="20260724T000000")
        bm.add_item(m, category="roms", category_label="ROMs & games", system=system,
                    system_label=system, item=bm.make_item(
                        id=f"{system}:x", name="Evil", src=f"/x/{system}/evil.bin",
                        rel=rel, kind="file", size=5))
        bm.write(m, bm.manifest_path(bdir))
        return bdir

    def test_system_traversal_skipped(self):
        bdir = self._manifest_backup("../../escape", "payload")
        rr = gb.restore_selection(str(bdir), [{"system": "../../escape", "id": "../../escape:x"}],
                                  "roms", "20260724T190000", self.emit, NO_STOP)
        self.assertEqual((rr["restored"], rr["skipped"]), (0, 1))
        self.assertFalse((self.tmp.parent / "escape").exists(), "must not write outside the ROM root")

    def test_rel_traversal_skipped(self):
        # a well-named system but a rel that climbs out of the backup folder to a real file
        outside = self.tmp / "secret.bin"
        outside.write_bytes(b"SECRET")
        bdir = self._manifest_backup("nes", "../secret.bin")
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:x"}],
                                  "roms", "20260724T200000", self.emit, NO_STOP)
        self.assertEqual(rr["skipped"], 1)
        self.assertFalse((self.romroot / "nes" / "secret.bin").exists())


class SymlinkedSystem(unittest.TestCase):
    """REGRESSION (review, high): a per-system ROM dir that symlinks OUTSIDE ~/ROMs (ps2/ps3/switch/gba
    on this device point to the internal drive) must still back up AND restore. The original sandbox used
    a flat real tree and never reproduced this, so the containment bug shipped green."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.romroot = self.tmp / "ROMs"
        self.romroot.mkdir()
        (self.romroot / "nes").mkdir()
        (self.romroot / "nes" / "smb.zip").write_bytes(b"NES-ROM")
        # ps2 is a symlink to an internal dir OUTSIDE the ROM root - the real device topology
        self.internal = self.tmp / "internal" / "ps2"
        self.internal.mkdir(parents=True)
        (self.internal / "gt4.iso").write_bytes(b"PS2-ROM")
        (self.romroot / "ps2").symlink_to(self.internal)
        self.dest = self.tmp / "b"
        self.dest.mkdir()
        self.sink = []

        def fake_resolve(system, stem):
            p = {("nes", "smb"): self.romroot / "nes" / "smb.zip",
                 ("ps2", "gt4"): self.romroot / "ps2" / "gt4.iso"}.get((system, stem))
            return [str(p)] if p and os.path.exists(str(p)) else []

        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.romroot),
            mock.patch.object(game_files, "resolve_rom", fake_resolve),
            mock.patch.object(game_files, "resolve_boxart", lambda s, st: {}),
            mock.patch.object(es_systems, "fullname", lambda s: s),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_symlinked_system_round_trips(self):
        r = _seed_roms_backup([{"system": "ps2", "stem": "gt4"}], self.dest,
                              "20260724T120000", self.sink.append)
        self.assertEqual(r["copied"], 1, "a symlinked-system ROM must be seeded into the fixture")
        self.internal.joinpath("gt4.iso").write_bytes(b"CORRUPT")   # mutate the live file
        rr = gb.restore_selection(r["path"], [{"system": "ps2", "id": "ps2:gt4"}], "roms",
                                  "20260724T130000", self.sink.append, NO_STOP)
        self.assertEqual(rr["restored"], 1, "a symlinked-system ROM must RESTORE (not target_escapes_root)")
        self.assertEqual((self.internal / "gt4.iso").read_bytes(), b"PS2-ROM",
                         "restore must land back on the internal drive via the per-system symlink")


class DataLossCollision(_Sandbox):
    """REGRESSION (review, high, RULE #5): two selection entries aliasing one target must not clobber the
    snapshot and destroy the genuine live original."""
    def test_duplicate_selection_preserves_original(self):
        r = self._backup([{"system": "nes", "stem": "smb"}])
        (self.romroot / "nes" / "smb.zip").write_bytes(b"ORIGINAL-LIVE-IRREPLACEABLE")
        rr = gb.restore_selection(r["path"], [{"system": "nes", "id": "nes:smb"},
                                              {"system": "nes", "id": "nes:smb"}],
                                  "roms", "20260724T130000", self.emit, NO_STOP)
        self.assertEqual(rr["restored"], 1, "the same target restores once")
        self.assertEqual(rr["skipped"], 1, "the duplicate is skipped, not re-snapshotted")
        snap = Path(rr["snapshot"])
        saved = list(snap.rglob("smb.zip"))
        self.assertTrue(any(p.read_bytes() == b"ORIGINAL-LIVE-IRREPLACEABLE" for p in saved),
                        "the genuine original must be recoverable in the snapshot")

    def test_post_snapshot_copy_failure_is_orphaned_not_skipped(self):
        # REGRESSION (review #2, medium): if the copy fails AFTER the snapshot moved the original aside,
        # the item must be reported as orphaned/needs-rollback, NOT folded into a benign 'skipped'.
        r = self._backup([{"system": "nes", "stem": "smb"}])
        (self.romroot / "nes" / "smb.zip").write_bytes(b"LIVE-ORIGINAL")   # target exists -> snapshotted
        with mock.patch.object(gb, "_copy_path", side_effect=OSError("disk full")):
            rr = gb.restore_selection(r["path"], [{"system": "nes", "id": "nes:smb"}], "roms",
                                      "20260724T180000", self.emit, NO_STOP)
        self.assertEqual((rr["restored"], rr["skipped"]), (0, 0))
        self.assertEqual([o["id"] for o in rr["orphaned"]], ["nes:smb"])
        # the original is still recoverable in the snapshot (rule #5 preservation upheld)
        snap = Path(rr["orphaned"][0]["snapshot"])
        self.assertTrue(any(p.read_bytes() == b"LIVE-ORIGINAL" for p in snap.rglob("smb.zip")))

    def test_recovery_line_is_shell_safe(self):
        # a ROM whose name needs shell-quoting must still produce a valid rollback command
        weird = self.romroot / "nes" / 'Game "GOTY".zip'
        weird.write_bytes(b"LIVE")
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                    item=bm.make_item(id="nes:goty", name="GOTY", src=str(weird),
                                      rel='roms/nes/Game "GOTY".zip', size=4))
        bdir = self.dest / "deck-granular-x"
        (bdir / "roms" / "nes").mkdir(parents=True)
        (bdir / "roms" / "nes" / 'Game "GOTY".zip').write_bytes(b"BACKUP")
        bm.write(m, bm.manifest_path(bdir))
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:goty"}], "roms",
                                  "20260724T160000", self.emit, NO_STOP)
        self.assertEqual(rr["replaced"], 1)
        rec = (Path(rr["snapshot"]) / "RECOVERY.txt").read_text()
        # shlex.quote wraps a name with a double-quote in single quotes -> a valid, parseable command
        import shlex as _shlex
        self.assertIn(_shlex.quote(str(weird)), rec)


class CorruptManifestRobustness(_Sandbox):
    """REGRESSION (review): a validating-but-corrupt manifest must be SKIPPED per item, never crash."""
    def _backup_dir_with_item(self, item):
        bdir = self.dest / "deck-granular-corrupt"
        (bdir / "roms" / "nes").mkdir(parents=True)
        (bdir / "roms" / "nes" / "real.zip").write_bytes(b"X")
        m = bm.new_manifest("granular", created="x")
        # add one real item so validate() passes, plus the corrupt one under test
        bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                    item=bm.make_item(id="nes:real", name="Real", src="/x/real.zip",
                                      rel="roms/nes/real.zip", size=1))
        m["categories"]["roms"]["systems"]["nes"]["items"].append(item)
        bm.write(m, bm.manifest_path(bdir))
        return bdir

    def test_item_missing_rel_is_skipped_not_crash(self):
        bdir = self._backup_dir_with_item({"id": "nes:norel", "name": "NoRel", "src": "/x/z.zip"})
        # neither restore nor preview may raise
        pv = gb.restore_preview(str(bdir), [{"system": "nes", "id": "nes:norel"}], "roms")
        self.assertEqual([s["id"] for s in pv["skip"]], ["nes:norel"])
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:norel"}], "roms",
                                  "20260724T170000", self.emit, NO_STOP)
        self.assertEqual((rr["restored"], rr["skipped"]), (0, 1))

    def test_rel_with_control_char_rejected(self):
        # REGRESSION (review #2, low): a newline in rel must be rejected (can't corrupt RECOVERY.txt)
        bdir = self._backup_dir_with_item({"id": "nes:nl", "name": "NL", "src": "/x/a.zip",
                                           "rel": "roms/nes/a\nb.zip"})
        pv = gb.restore_preview(str(bdir), [{"system": "nes", "id": "nes:nl"}], "roms")
        self.assertEqual([s["reason"] for s in pv["skip"]], ["unsafe_path"])


class SubdirRomRoundTrip(unittest.TestCase):
    """REGRESSION (review #2): a ROM in a SUB-folder under its system (e.g. nes/hacks/smb.zip) must back
    up AND restore to the SAME sub-path, not flatten to the top level (which could clobber another game)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.romroot = self.tmp / "ROMs"
        (self.romroot / "nes" / "hacks").mkdir(parents=True)
        self.rom = self.romroot / "nes" / "hacks" / "smb.zip"
        self.rom.write_bytes(b"HACK-ROM")
        self.dest = self.tmp / "b"
        self.dest.mkdir()
        self.sink = []
        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.romroot),
            mock.patch.object(game_files, "resolve_rom",
                              lambda s, st: [str(self.rom)] if (s, st) == ("nes", "smb") else []),
            mock.patch.object(game_files, "resolve_boxart", lambda s, st: {}),
            mock.patch.object(es_systems, "fullname", lambda s: s),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_subdir_rom_round_trips_to_same_subpath(self):
        r = _seed_roms_backup([{"system": "nes", "stem": "smb"}], self.dest,
                              "20260724T120000", self.sink.append)
        bdir = Path(r["path"])
        self.assertTrue((bdir / "roms" / "nes" / "hacks" / "smb.zip").is_file(),
                        "the seeded fixture must preserve the sub-path, not flatten to roms/nes/smb.zip")
        self.rom.write_bytes(b"CORRUPT")
        rr = gb.restore_selection(str(bdir), [{"system": "nes", "id": "nes:smb"}], "roms",
                                  "20260724T130000", self.sink.append, NO_STOP)
        self.assertEqual(rr["restored"], 1)
        self.assertEqual(self.rom.read_bytes(), b"HACK-ROM", "must restore to nes/hacks/smb.zip")
        self.assertFalse((self.romroot / "nes" / "smb.zip").exists(),
                         "must NOT create a flattened top-level nes/smb.zip")


class StreamTeardown(unittest.TestCase):
    """REGRESSION (review #2): stop_all_streams must not raise (or leak the token) for a stream whose
    thread never started - otherwise a thread-start failure aborts daemon teardown mid-way."""
    def test_unstarted_stream_torn_down_cleanly(self):
        from lib.madsrv import rpc

        class _Never(rpc.Stream):
            def run(self):    # never invoked - we deliberately do NOT call start()
                pass

        s = _Never()                       # registers in _STREAMS but the thread is unstarted
        self.assertIn(s.token, rpc._STREAMS)
        rpc.stop_all_streams(join_timeout=0.1)     # must neither raise nor hang
        self.assertNotIn(s.token, rpc._STREAMS, "a never-started stream must be dropped from _STREAMS")


class CategoryMeta(unittest.TestCase):
    def test_roms_no_stop_no_restart(self):
        self.assertEqual(gb.category_meta("roms"),
                         {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"})

    def test_unknown_defaults_safe(self):
        self.assertTrue(gb.category_meta("totally-unwired-xyz")["needs_esde_stopped"],
                        "an unwired category must default to requiring ES-DE closed")


class Wrappers(unittest.TestCase):
    # granular.backup (_granular_backup) was retired audit 2026-08-12 phase 5: dead RPC, no caller.
    # granular.restore (_granular_restore) is the live wrapper below and shares the SAME category
    # validation (_CATEGORY_KEYS), so its "bad category" + "no items" cases are covered here instead.
    def test_restore_rejects_bad_params(self):
        with self.assertRaises(RpcError):
            g._granular_restore({"source": "/x", "category": "nope",
                                 "items": [{"system": "nes", "id": "nes:smb"}]})
        with self.assertRaises(RpcError):
            g._granular_restore({"source": "/x", "category": "roms", "items": []})
        with self.assertRaises(RpcError):
            g._granular_restore({"source": "live", "category": "roms",
                                 "items": [{"system": "nes", "id": "nes:smb"}]})

    def test_restore_ebusy_when_esde_up_for_guarded_category(self):
        from lib import proc_guard
        with mock.patch.object(gb, "category_meta",
                               lambda c: {"needs_esde_stopped": True, "restart_scope": "esde"}), \
             mock.patch.object(proc_guard, "esde_running", lambda: True):
            with self.assertRaises(RpcError) as cm:
                g._granular_restore({"source": "/some/backup", "category": "roms",
                                     "items": [{"system": "nes", "id": "nes:smb"}]})
            self.assertEqual(cm.exception.code, "EBUSY")

    def test_concurrent_op_rejected(self):
        # Retargeted from the retired _granular_backup (audit 2026-08-12 phase 5): _granular_restore
        # with a valid "roms" category (no ES-DE-closed guard) reaches _start_granular's _GRAN_ACTIVE
        # acquire the same way, so it still proves ONE granular op runs at a time. NOT
        # _granular_backup_assets - that passes queue_if_busy=True and would return {"queued": ...}
        # instead of raising, which would make this assertion meaningless.
        self.assertTrue(g._GRAN_ACTIVE.acquire(blocking=False))
        try:
            with self.assertRaises(RpcError) as cm:
                g._granular_restore({"source": "/some/backup", "category": "roms",
                                     "items": [{"system": "nes", "id": "nes:smb"}]})
            self.assertEqual(cm.exception.code, "EBUSY")
        finally:
            g._GRAN_ACTIVE.release()


if __name__ == "__main__":
    unittest.main()
