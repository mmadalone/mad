"""Manage backups (P11): list every deletable backup SET (local + cloud, across the 5 granular categories)
and PERMANENTLY delete one - a deliberate user override of rule #5 for a BACKUP copy (primary data is never
touched).

ManageList / ManageDeleteLocal (CI-safe, hermetic): manage.list classifies each local backup by category
with a manifest-summed size + counts, and the cloud branch parses list-<cat>; manage.delete_local removes a
real backup but its GUARDS reject anything not a direct child of a backup root, the root itself, a protected
primary dir, or a path with no valid manifest (so primary data can never be rmtree'd), and it is EBUSY while a
granular op runs.

CloudDeleteSet (CI-safe, the shell is mocked): cloud.delete_set validates category+token, purges the right
subcommand, and - critically - clears the interrupted-transfer marker + drops its plan-dir when it targets the
set being deleted (so mad-backend auto-resume can't re-upload what we just purged) while leaving a marker for
a DIFFERENT set intact; a live op uploading the set is stopped first.

PurgeShell (Deck-only, needs rclone): a real round-trip via DECK_CLOUD_GAMES_BASE_OVERRIDE -> push-games ->
purge-games removes the set folder; a traversal token is rejected and deletes nothing.

Run:  python3 -m unittest tests.test_manage -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CLOUD = ROOT / "deck-cloud.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_RCLONE = (BIN / "rclone").exists()

from lib import backup_manifest as bm                     # noqa: E402
from lib.madsrv import cloud_cmds as cc                    # noqa: E402
from lib.madsrv import granular_cmds as g                  # noqa: E402
from lib.madsrv.rpc import RpcError                        # noqa: E402


def _make_backup(folder: Path, category: str, system: str, rels, ts: str) -> None:
    """Write a valid granular backup FOLDER (mad-manifest.json + no real payload needed for the metadata
    tests). Each rel becomes one item of `category`/`system`."""
    folder.mkdir(parents=True, exist_ok=True)
    m = bm.new_manifest("granular", created=ts)
    for rel in rels:
        bm.add_item(m, category=category, category_label=category, system=system, system_label=system,
                    item=bm.make_item(id=rel, name=os.path.basename(rel), src="/live/" + rel,
                                      rel=rel, kind="file", size=100))
    bm.write(m, bm.manifest_path(folder))


class ManageList(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_backup(self.tmp / "deck-granular-games", "roms", "nes", ["nes:Super Mario Bros"], "20260728T100000")
        _make_backup(self.tmp / "deck-granular-bios", "bios", "ps2", ["bios/ps2/scph39001.bin"], "20260728T110000")
        _make_backup(self.tmp / "deck-granular-esde-20260728T120000", "esde", "Main settings",
                     ["esde/settings/es_settings.xml"], "20260728T120000")
        (self.tmp / "not-a-backup").mkdir()   # a bare dir with no manifest -> must be ignored
        self._p = [mock.patch.object(g, "_local_backup_roots", lambda: [self.tmp]),
                   mock.patch.object(g, "_cloud_run", lambda *a, **k: (1, ""))]  # no cloud by default
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lists_and_classifies_local_backups(self):
        out = g._manage_list({})
        by_cat = {r["category"]: r for r in out["local"]}
        self.assertEqual(set(by_cat), {"games", "bios", "esde"})       # the bare dir was skipped
        self.assertEqual(by_cat["games"]["label"], "Games")
        self.assertEqual(by_cat["bios"]["label"], "BIOS")
        self.assertTrue(by_cat["games"]["count"] >= 1)
        self.assertEqual(by_cat["bios"]["count"], 1)
        self.assertTrue(all(r["size"] > 0 for r in out["local"]))
        self.assertTrue(all(r["scope"] == "local" for r in out["local"]))
        self.assertEqual(out["cloud"], [])
        self.assertFalse(out["connected"])                             # _cloud_run returned rc!=0

    def test_newest_local_first(self):
        rows = g._manage_list({})["local"]
        self.assertEqual([r["category"] for r in rows], ["esde", "bios", "games"])  # by created desc

    def test_cloud_rows_parsed_per_category(self):
        def fake_cloud(args, timeout=120):
            cmd = args[0]
            if cmd == "list-games":
                return 0, "games\t7\t20260728T090000\n"
            if cmd == "list-esde":
                return 0, "20260728T130000\t4\t20260728T130000\n"
            return 0, ""   # other categories: connected but empty
        with mock.patch.object(g, "_cloud_run", fake_cloud):
            out = g._manage_list({})
        self.assertTrue(out["connected"])
        cats = {(r["category"], r["token"]) for r in out["cloud"]}
        self.assertIn(("games", "games"), cats)
        self.assertIn(("esde", "20260728T130000"), cats)
        gm = next(r for r in out["cloud"] if r["category"] == "games")
        self.assertEqual(gm["id"], "cloud:games")
        self.assertEqual(gm["count"], 7)


class ManageDeleteLocal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._p = mock.patch.object(g, "_local_backup_roots", lambda: [self.tmp])
        self._p.start()

    def tearDown(self):
        self._p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _a_backup(self, name="deck-granular-bios"):
        d = self.tmp / name
        _make_backup(d, "bios", "ps2", ["bios/ps2/scph39001.bin"], "20260728T110000")
        return d

    def test_deletes_a_folder_backup(self):
        d = self._a_backup()
        r = g._manage_delete_local({"path": str(d)})
        self.assertTrue(r["deleted"])
        self.assertFalse(d.exists())

    def test_deletes_an_archive_backup_with_sidecar(self):
        arc = self.tmp / "deck-roms.tar"
        arc.write_bytes(b"ARCHIVE")
        # a valid sidecar manifest so read()/validate() accept the archive
        m = bm.new_manifest("granular", created="20260728T110000")
        bm.add_item(m, category="bios", category_label="BIOS", system="ps2", system_label="ps2",
                    item=bm.make_item(id="bios/x.bin", name="x.bin", src="/live/x", rel="bios/x.bin",
                                      kind="file", size=1))
        side = self.tmp / ("deck-roms.tar." + bm.MANIFEST_NAME)
        bm.write(m, side)
        r = g._manage_delete_local({"path": str(arc)})
        self.assertTrue(r["deleted"])
        self.assertFalse(arc.exists())
        self.assertFalse(side.exists())

    def test_rejects_path_outside_the_backup_roots(self):
        other = Path(tempfile.mkdtemp())
        try:
            _make_backup(other / "deck-granular-bios", "bios", "ps2", ["bios/x.bin"], "20260728T110000")
            with self.assertRaises(RpcError) as e:
                g._manage_delete_local({"path": str(other / "deck-granular-bios")})
            self.assertEqual(e.exception.code, "EINVAL")
            self.assertTrue((other / "deck-granular-bios").exists())   # untouched
        finally:
            import shutil
            shutil.rmtree(other, ignore_errors=True)

    def test_rejects_a_root_itself(self):
        with self.assertRaises(RpcError) as e:
            g._manage_delete_local({"path": str(self.tmp)})
        self.assertEqual(e.exception.code, "EINVAL")
        self.assertTrue(self.tmp.exists())

    def test_rejects_a_dir_with_no_manifest_primary_data_safety(self):
        # a real directory under the backup root that is NOT a backup (no mad-manifest.json) - e.g. a games
        # library folder a user pointed the dest near. It must be REFUSED and left intact.
        d = self.tmp / "ROMs"
        (d / "nes").mkdir(parents=True)
        (d / "nes" / "mario.nes").write_bytes(b"ROMDATA")
        with self.assertRaises(RpcError) as e:
            g._manage_delete_local({"path": str(d)})
        self.assertEqual(e.exception.code, "EINVAL")
        self.assertTrue((d / "nes" / "mario.nes").exists())            # NEVER deleted

    def test_rejects_a_protected_dir_even_with_a_manifest(self):
        d = self._a_backup("protected-set")
        with mock.patch.object(g, "_MANAGE_PROTECTED_DIRS", (str(d),)):
            with self.assertRaises(RpcError) as e:
                g._manage_delete_local({"path": str(d)})
        self.assertEqual(e.exception.code, "EINVAL")
        self.assertTrue(d.exists())

    def test_ebusy_while_a_granular_op_runs(self):
        d = self._a_backup()
        self.assertTrue(g._GRAN_ACTIVE.acquire(blocking=False))
        try:
            with self.assertRaises(RpcError) as e:
                g._manage_delete_local({"path": str(d)})
            self.assertEqual(e.exception.code, "EBUSY")
            self.assertTrue(d.exists())
        finally:
            g._GRAN_ACTIVE.release()


class _FakeStream:
    def __init__(self, token="tok"):
        self.token = token
        self.resumed = False

    def resume(self):
        self.resumed = True


class CloudDeleteSet(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")
        self.calls = []

        def fake_run(args, timeout=90):
            self.calls.append(list(args))
            return 0, "", ""
        self._p = mock.patch.object(cc, "_run", fake_run)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_purges_the_right_subcommand(self):
        r = cc._cloud_delete_set({"category": "games", "token": "games"})
        self.assertTrue(r["deleted"])
        self.assertIn(["purge-games", "games"], self.calls)

    def test_dated_set_token(self):
        cc._cloud_delete_set({"category": "esde", "token": "20260728T120000"})
        self.assertIn(["purge-esde", "20260728T120000"], self.calls)

    def test_bad_category(self):
        with self.assertRaises(RpcError) as e:
            cc._cloud_delete_set({"category": "nope", "token": "games"})
        self.assertEqual(e.exception.code, "EINVAL")
        self.assertEqual(self.calls, [])

    def test_bad_token_rejected_before_shelling(self):
        for bad in ("../evil", "games/../..", "", "20260728", "gAmes"):
            with self.assertRaises(RpcError):
                cc._cloud_delete_set({"category": "games", "token": bad})
        self.assertEqual(self.calls, [])

    def test_clears_marker_and_plandir_targeting_this_set(self):
        pd = self.tmp / "state" / "games-plan" / "20260728T120000"
        pd.mkdir(parents=True)
        cc._write_marker(["push-games", "games", str(pd)])
        cc._cloud_delete_set({"category": "games", "token": "games"})
        self.assertIsNone(cc._read_marker())          # cleared -> auto-resume can't re-upload
        self.assertFalse(pd.exists())                 # plan-dir dropped

    def test_keeps_marker_for_a_different_set(self):
        pd = self.tmp / "state" / "esde-plan" / "20260728T130000"
        pd.mkdir(parents=True)
        cc._write_marker(["push-esde", "20260728T130000", str(pd)])
        cc._cloud_delete_set({"category": "games", "token": "games"})   # a DIFFERENT set
        self.assertEqual(cc._read_marker(), ["push-esde", "20260728T130000", str(pd)])
        self.assertTrue(pd.exists())

    def test_stops_a_live_op_uploading_this_set(self):
        # A LIVE registered job uploading THIS set (registry model: the job may belong to
        # a previous panel session, so delete_set consults the registry, not _ACTIVE).
        import subprocess
        from lib import job_registry as jr
        proc = subprocess.Popen(["sleep", "300"], start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            jid = jr.begin("push-games", proc.pid, argv=["push-games", "games", "/pd"])
            cc._cloud_delete_set({"category": "games", "token": "games"})
            job = jr.get(jid)
            self.assertIn(job["state"], ("failed", "done"), "the live upload was stopped")
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll())
        finally:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass

    def test_purge_failure_raises(self):
        with mock.patch.object(cc, "_run", lambda *a, **k: (1, "", "purge: could not delete")):
            with self.assertRaises(RpcError) as e:
                cc._cloud_delete_set({"category": "games", "token": "games"})
        self.assertEqual(e.exception.code, "EIO")


class ManageDeleteAll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")
        _make_backup(self.tmp / "deck-granular-games", "roms", "nes", ["nes:A"], "20260728T100000")
        _make_backup(self.tmp / "deck-granular-games-20260728T101010", "roms", "snes", ["snes:B"], "20260728T101010")
        _make_backup(self.tmp / "deck-granular-bios", "bios", "ps2", ["bios/ps2/x.bin"], "20260728T110000")
        self.purged = []

        def fake_cloud(args, timeout=120):   # the manage.list CLOUD enumeration (one esde set)
            return (0, "20260728T120000\t3\t20260728T120000\n") if args[0] == "list-esde" else (0, "")

        def fake_run(args, timeout=90):      # the cloud PURGE shell (cc._run)
            self.purged.append(list(args))
            return 0, "", ""
        self._p = [mock.patch.object(g, "_local_backup_roots", lambda: [self.tmp]),
                   mock.patch.object(g, "_cloud_run", fake_cloud),
                   mock.patch.object(cc, "_run", fake_run)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_all_of_one_category_only(self):
        r = g._manage_delete_all({"category": "games"})
        self.assertEqual(r["deleted"], 2)
        self.assertEqual(r["failed"], 0)
        self.assertFalse((self.tmp / "deck-granular-games").exists())
        self.assertFalse((self.tmp / "deck-granular-games-20260728T101010").exists())
        self.assertTrue((self.tmp / "deck-granular-bios").exists())     # a different category is untouched
        self.assertEqual(self.purged, [])                               # no cloud games sets to purge

    def test_delete_everything(self):
        r = g._manage_delete_all({})
        self.assertEqual(r["deleted"], 4)                               # 2 local games + 1 local bios + 1 cloud esde
        self.assertFalse((self.tmp / "deck-granular-bios").exists())
        self.assertFalse((self.tmp / "deck-granular-games").exists())
        self.assertIn(["purge-esde", "20260728T120000"], self.purged)

    def test_bad_category_rejected(self):
        with self.assertRaises(RpcError) as e:
            g._manage_delete_all({"category": "nope"})
        self.assertEqual(e.exception.code, "EINVAL")

    def test_per_set_oserror_is_counted_not_fatal(self):
        # a per-set OSError (e.g. an in-use file on a Samba/exFAT dest) must be COUNTED, not abort the whole
        # bulk delete after some sets are already gone (review MED-1).
        real = g._manage_delete_local

        def flaky(params):
            if params["path"].endswith("deck-granular-bios"):
                raise OSError("Device or resource busy")
            return real(params)
        with mock.patch.object(g, "_manage_delete_local", flaky):
            r = g._manage_delete_all({})
        self.assertEqual(r["failed"], 1)                                # the bios set
        self.assertEqual(r["deleted"], 3)                              # 2 games + 1 cloud esde still removed
        self.assertTrue((self.tmp / "deck-granular-bios").exists())    # left intact, not half-deleted
        self.assertIn(["purge-esde", "20260728T120000"], self.purged)  # cloud loop still ran

    def test_reports_connected(self):
        self.assertTrue(g._manage_delete_all({"category": "games"})["connected"])
        with mock.patch.object(g, "_cloud_run", lambda *a, **k: (1, "")):  # cloud unreachable
            self.assertFalse(g._manage_delete_all({"category": "games"})["connected"])


@unittest.skipUnless(HAVE_RCLONE, "needs the vendored rclone (Deck only)")
class PurgeShell(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.remote = self.base / "games-remote"
        self._env = {"DECK_CLOUD_RCLONE": str(BIN / "rclone"), "DECK_CLOUD_SKIP_CONNCHECK": "1",
                     "DECK_CLOUD_NO_NICE": "1", "DECK_CLOUD_STATE_DIR": str(self.base / "state"),
                     "DECK_CLOUD_GAMES_BASE_OVERRIDE": str(self.remote)}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _push_a_set(self, ts="games"):
        src = self.base / "live" / "roms" / "nes"
        src.mkdir(parents=True)
        (src / "mario.nes").write_bytes(b"ROM")
        pd = self.base / ("plan-" + ts)
        pd.mkdir()
        m = bm.new_manifest("granular", created="20260728T100000")
        bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="nes",
                    item=bm.make_item(id="nes:mario", name="mario.nes", src=str(src / "mario.nes"),
                                      rel="roms/nes/mario.nes", kind="file", size=3))
        bm.write(m, bm.manifest_path(pd))
        (pd / "plan").write_bytes(str(src / "mario.nes").encode() + b"\0" + b"roms/nes/mario.nes\0")
        r = subprocess.run([str(CLOUD), "push-games", ts, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_purge_removes_the_set(self):
        self._push_a_set("games")
        self.assertTrue((self.remote / "games").is_dir())
        r = subprocess.run([str(CLOUD), "purge-games", "games"], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.remote / "games").exists())

    def test_purge_of_a_missing_set_is_success(self):
        r = subprocess.run([str(CLOUD), "purge-games", "20260101T000000"], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)   # idempotent: already gone

    def test_traversal_token_is_rejected_and_deletes_nothing(self):
        self._push_a_set("games")
        sentinel = self.remote.parent / "sibling"     # would be hit by ../sibling from the base
        sentinel.mkdir()
        (sentinel / "keep").write_bytes(b"KEEP")
        r = subprocess.run([str(CLOUD), "purge-games", "../sibling"], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)          # _safe_settoken rejects it -> die
        self.assertTrue((sentinel / "keep").exists())
        self.assertTrue((self.remote / "games").is_dir())


if __name__ == "__main__":
    unittest.main()
