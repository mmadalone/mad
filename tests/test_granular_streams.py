"""granular_backup engine + granular.backup/restore RPC wrappers - the WRITE path (highest risk).

These drive the real copy/restore engine against a SANDBOX rom_root (no real library touched) and lock
in the safety-critical contracts:

  * backup copies a game's ROM file OR folder into deck-granular-<ts>/roms/<system>/... and writes a
    valid manifest; a game whose ROM is absent is SKIPPED (never faked); an all-missing backup leaves
    no empty manifest-less folder;
  * RULE #5: restore moves any existing target ASIDE to a same-fs snapshot (content preserved) with a
    RECOVERY.txt + rollback line BEFORE writing the restored copy; a fresh target makes no snapshot;
  * restore REJECTS an invalid/foreign manifest and SKIPS an item whose backup file is missing;
  * the ES-DE-closed guard fires for a category that needs it (engine RuntimeError + RPC EBUSY);
  * cancellation raises Cancelled (backup + restore);
  * the RPC wrappers reject bad params (EINVAL) and refuse a concurrent op (EBUSY).

Run:  python3 -m unittest tests.test_granular_streams -v
"""
from __future__ import annotations

import os
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
        return gb.backup_selection(items, str(self.dest), "roms", "ROMs & games", ts, self.emit, NO_STOP)


class Backup(_Sandbox):
    def test_backup_file_and_folder_with_manifest(self):
        r = self._backup([{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}])
        self.assertEqual((r["copied"], r["skipped"]), (2, 0))
        bdir = Path(r["path"])
        self.assertTrue((bdir / "roms/nes/smb.zip").is_file())
        self.assertTrue((bdir / "roms/ps3/MyGame/EBOOT.BIN").is_file())
        m = bm.read(bdir)
        self.assertTrue(bm.validate(m))
        smb = bm.find_item(m, "roms", "nes", "nes:smb")
        self.assertEqual(smb["kind"], "file")
        self.assertEqual(smb["size"], 500)
        self.assertEqual(bm.find_item(m, "roms", "ps3", "ps3:MyGame")["kind"], "folder")

    def test_missing_rom_skipped(self):
        r = self._backup([{"system": "nes", "stem": "smb"}, {"system": "nes", "stem": "ghost"}])
        self.assertEqual((r["copied"], r["skipped"]), (1, 1))
        self.assertTrue(any("skip (ROM missing)" in e.get("line", "") for e in self.sink))

    def test_all_missing_leaves_no_folder(self):
        r = self._backup([{"system": "nes", "stem": "ghost"}])
        self.assertEqual(r["copied"], 0)
        self.assertFalse(Path(r["path"]).exists(), "an all-skip backup must not leave an empty folder")

    def test_cancel_raises(self):
        with self.assertRaises(gb.Cancelled):
            gb.backup_selection([{"system": "nes", "stem": "smb"}], str(self.dest), "roms",
                                "ROMs & games", "20260724T120000", self.emit, lambda: True)


class PlanSelection(_Sandbox):
    """The shared non-copying planner behind BOTH the local backup and the cloud upload: it resolves the
    selection to a src/rel plan + a manifest, applies the SAME skips as the copy, and writes NOTHING."""
    def test_plan_file_and_folder_writes_nothing(self):
        before = {p.name for p in self.dest.iterdir()}
        manifest, plan = gb.plan_selection(
            [{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}],
            "roms", "ROMs & games", "20260725T000000", self.emit)
        self.assertEqual({p.name for p in self.dest.iterdir()}, before,
                         "plan_selection must not create a backup folder")
        self.assertEqual([e.get("item_done") for e in self.sink if "item_done" in e], [],
                         "planning must not emit item_done (no copy happened)")
        byid = {e["id"]: e for e in plan}
        self.assertEqual((byid["nes:smb"]["rel"], byid["nes:smb"]["kind"]), ("roms/nes/smb.zip", "file"))
        self.assertEqual((byid["ps3:MyGame"]["rel"], byid["ps3:MyGame"]["kind"]),
                         ("roms/ps3/MyGame", "folder"))
        self.assertTrue(bm.validate(manifest))
        self.assertEqual(bm.find_item(manifest, "roms", "nes", "nes:smb")["size"], 500)

    def test_plan_skips_missing_rom(self):
        _m, plan = gb.plan_selection(
            [{"system": "nes", "stem": "smb"}, {"system": "nes", "stem": "ghost"}],
            "roms", "ROMs & games", "20260725T000000", self.emit)
        self.assertEqual([e["id"] for e in plan], ["nes:smb"])
        self.assertTrue(any("skip (ROM missing)" in e.get("line", "") for e in self.sink))

    def test_plan_silent_when_no_emit(self):
        # the cloud path calls plan_selection BEFORE its stream exists (emit=None) -> skips are silent
        _m, plan = gb.plan_selection([{"system": "nes", "stem": "ghost"}], "roms", "ROMs & games", "x")
        self.assertEqual(plan, [])

    def test_plan_equals_backup_selection(self):
        # what the cloud path uploads (plan rel set) == what a local backup copies (same games)
        _m, plan = gb.plan_selection(
            [{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}],
            "roms", "ROMs & games", "20260725T000000")
        r = self._backup([{"system": "nes", "stem": "smb"}, {"system": "ps3", "stem": "MyGame"}])
        self.assertEqual({e["rel"] for e in plan}, {"roms/nes/smb.zip", "roms/ps3/MyGame"})
        self.assertEqual(r["copied"], len(plan))


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
        r = gb.backup_selection([{"system": "ps2", "stem": "gt4"}], str(self.dest), "roms",
                                "ROMs & games", "20260724T120000", self.sink.append, NO_STOP)
        self.assertEqual(r["copied"], 1, "a symlinked-system ROM must back up")
        self.internal.joinpath("gt4.iso").write_bytes(b"CORRUPT")   # mutate the live file
        rr = gb.restore_selection(r["path"], [{"system": "ps2", "id": "ps2:gt4"}], "roms",
                                  "20260724T130000", self.sink.append, NO_STOP)
        self.assertEqual(rr["restored"], 1, "a symlinked-system ROM must RESTORE (not target_escapes_root)")
        self.assertEqual((self.internal / "gt4.iso").read_bytes(), b"PS2-ROM",
                         "restore must land back on the internal drive via the per-system symlink")

    def test_out_of_tree_src_skipped_in_backup(self):
        # a resolver result OUTSIDE the system dir (e.g. an rpcs3 dev_hdd0 install) is not a plain ROM
        outside = self.tmp / "elsewhere" / "psn.bin"
        outside.parent.mkdir()
        outside.write_bytes(b"PSN")
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: [str(outside)]):
            r = gb.backup_selection([{"system": "ps2", "stem": "psn"}], str(self.dest), "roms",
                                    "ROMs & games", "20260724T140000", self.sink.append, NO_STOP)
        self.assertEqual((r["copied"], r["skipped"]), (0, 1))
        self.assertFalse(Path(r["path"]).exists())


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
        r = gb.backup_selection([{"system": "nes", "stem": "smb"}], str(self.dest), "roms",
                                "ROMs & games", "20260724T120000", self.sink.append, NO_STOP)
        bdir = Path(r["path"])
        self.assertTrue((bdir / "roms" / "nes" / "hacks" / "smb.zip").is_file(),
                        "backup must preserve the sub-path, not flatten to roms/nes/smb.zip")
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
    def test_backup_rejects_bad_params(self):
        with self.assertRaises(RpcError):
            g._granular_backup({"category": "nope", "items": [{"system": "nes", "stem": "smb"}]})
        with self.assertRaises(RpcError):
            g._granular_backup({"category": "roms", "items": []})

    def test_restore_rejects_bad_params(self):
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
        self.assertTrue(g._GRAN_ACTIVE.acquire(blocking=False))
        try:
            with self.assertRaises(RpcError) as cm:
                g._granular_backup({"category": "roms", "items": [{"system": "nes", "stem": "smb"}]})
            self.assertEqual(cm.exception.code, "EBUSY")
        finally:
            g._GRAN_ACTIVE.release()


if __name__ == "__main__":
    unittest.main()
