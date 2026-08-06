"""P12a - the SYSTEM config category: irreplaceable system config (control-panel calibration, lightgun cal,
Samba/backup prefs, EmuDeck settings), versioned dated snapshots, LIVE restore + rule-5.

Every item is $HOME-relative (front-door), so backup/restore anchor at $HOME - but bounded by system_map's
TIGHT EXACT allowlist, NOT a broad emulator-dir set. These tests lock in:
  * enumeration picks the curated files and EXCLUDES debris (.log / .bak next to control-panel .json);
  * the allowlist accepts the exact config files + the control-panel dir, and REJECTS all of ~/Emulation/
    tools (only smb.conf), all of ~/.config/EmuDeck (only settings.*), and any other $HOME path (~/.ssh);
  * a backup -> corrupt-live -> restore round-trip lands the file back with a rule-5 snapshot in a WRITABLE
    dir under home (the /home-unwritable-parent regression);
  * a forged manifest rel outside the allowlist writes nothing.

Run:  python3 -m unittest tests.test_system -v
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

from lib import backup_manifest as bm                        # noqa: E402
from lib import granular_backup as gb                        # noqa: E402
from lib import system_map                                   # noqa: E402
from lib.madsrv import granular_cmds as gc                    # noqa: E402
from lib.madsrv.rpc import RpcError                           # noqa: E402


class _FakeHome(unittest.TestCase):
    """A temp $HOME populated with the real System config layout + debris siblings."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        cp = self.home / "Emulation/storage/control-panel"
        cp.mkdir(parents=True)
        (cp / "gp-dualsense-calib.json").write_bytes(b"CALIB")
        (cp / "xarcade-positions.json").write_bytes(b"POS")
        (cp / "sinden-camera.log").write_bytes(b"LOGDEBRIS")               # excluded (not *.json)
        (cp / "xarcade-calib.json.bak-20260610").write_bytes(b"BAKDEBRIS")  # excluded (not *.json)
        (self.home / "Lightgun").mkdir()
        (self.home / "Lightgun/LightgunMono.exe.config").write_bytes(b"GUNCAL")
        (self.home / "Lightgun/LightgunMono.exe.config.bak").write_bytes(b"GUNBAK")  # excluded (exact list)
        (self.home / "Emulation/tools").mkdir(parents=True)
        (self.home / "Emulation/tools/smb.conf").write_bytes(b"SMBCFG")
        (self.home / "Emulation/tools/emu-launch.sh").write_bytes(b"SCRIPT")  # NOT in the map
        (self.home / ".config/deck-cloud").mkdir(parents=True)
        (self.home / ".config/deck-cloud/categories.conf").write_bytes(b"roms=off")
        (self.home / ".config/EmuDeck").mkdir(parents=True)
        (self.home / ".config/EmuDeck/settings.sh").write_bytes(b"EDCFG")
        (self.home / ".config/EmuDeck/settings.json").write_bytes(b"{}")
        (self.home / ".config/EmuDeck/backend").mkdir()
        (self.home / ".config/EmuDeck/backend/big").write_bytes(b"REGENERABLE")  # NOT in the map
        self.dest = self.home / "dest"; self.dest.mkdir()
        self._p = [mock.patch.object(Path, "home", staticmethod(lambda: self.home)),
                   mock.patch("os.path.expanduser", lambda p: p.replace("~", str(self.home), 1)
                              if p.startswith("~") else p),
                   mock.patch.object(gb.proc_guard, "esde_running", lambda: False)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _all_items(self):
        items = []
        for g in system_map.list_groups():
            for f in g["files"]:
                items.append({"group": g["key"], "rel": f["rel"]})
        return items


class Enumeration(_FakeHome):
    def test_groups_pick_config_exclude_debris(self):
        groups = {g["key"]: g for g in system_map.list_groups()}
        rels = {f["rel"] for g in groups.values() for f in g["files"]}
        self.assertIn("system/Emulation/storage/control-panel/gp-dualsense-calib.json", rels)
        self.assertIn("system/Lightgun/LightgunMono.exe.config", rels)
        self.assertIn("system/Emulation/tools/smb.conf", rels)
        self.assertIn("system/.config/EmuDeck/settings.sh", rels)
        # debris + out-of-map files are NOT enumerated
        self.assertNotIn("system/Emulation/storage/control-panel/sinden-camera.log", rels)
        self.assertNotIn("system/Emulation/storage/control-panel/xarcade-calib.json.bak-20260610", rels)
        self.assertNotIn("system/Lightgun/LightgunMono.exe.config.bak", rels)
        self.assertNotIn("system/Emulation/tools/emu-launch.sh", rels)
        self.assertNotIn("system/.config/EmuDeck/backend/big", rels)

    def test_allowlist_tight(self):
        ok = ["system/Emulation/storage/control-panel/x.json", "system/Emulation/tools/smb.conf",
              "system/Lightgun/LightgunMono.exe.config", "system/.config/EmuDeck/settings.json",
              "system/.config/deck-cloud/categories.conf",
              "system/bin/temp-deck.py", "system/.config/temp-deck/fan-helper-installed"]
        bad = ["system/Emulation/tools/emu-launch.sh", "system/Emulation/tools/launchers/deck-backup.sh",
               "system/.config/EmuDeck/backend/big", "system/.ssh/id_rsa", "system/../.bashrc",
               # the fan helper's ROOT-owned half is generated, never archived: restoring a
               # NOPASSWD sudoers rule from a backup would silently re-grant privilege
               "system/etc/sudoers.d/zz-deck-fan", "system/var/lib/deck-fan/deck-fan-ctl",
               "system/bin/other-tool.sh"]
        for r in ok:
            self.assertTrue(system_map.rel_allowed(r), r)
        for r in bad:
            self.assertFalse(system_map.rel_allowed(r), r)


class BackupRestore(_FakeHome):
    def test_round_trip_with_rule5(self):
        # back up via the engine directly (deterministic), then restore over a corrupted live copy
        summary_b = gb.backup_system(self._all_items(), str(self.dest), "20260728T120000",
                                     lambda e: None, lambda: False)
        self.assertTrue(Path(summary_b["path"]).name.startswith("deck-granular-system-"), "versioned dated set")
        self.assertTrue((Path(summary_b["path"]) /
                         "system/Lightgun/LightgunMono.exe.config").is_file())
        # corrupt two live files, restore them
        (self.home / "Lightgun/LightgunMono.exe.config").write_bytes(b"CORRUPT")
        (self.home / "Emulation/tools/smb.conf").write_bytes(b"CORRUPT")
        items = [{"system": "lightgun", "id": "system/Lightgun/LightgunMono.exe.config"},
                 {"system": "samba", "id": "system/Emulation/tools/smb.conf"}]
        summary = gb.restore_selection(summary_b["path"], items, "system", "20260728T130000",
                                       lambda e: None, lambda: False)
        self.assertEqual((summary["restored"], summary["replaced"]), (2, 2))
        self.assertEqual((self.home / "Lightgun/LightgunMono.exe.config").read_bytes(), b"GUNCAL")
        self.assertEqual((self.home / "Emulation/tools/smb.conf").read_bytes(), b"SMBCFG")
        # rule-5: the snapshot is under home (never $HOME/.. = /home) + has RECOVERY.txt
        self.assertTrue(summary["snapshots"])
        for snap in summary["snapshots"]:
            self.assertTrue(snap.startswith(str(self.home)), snap)
            self.assertTrue(os.path.isdir(snap) and (Path(snap) / "RECOVERY.txt").is_file())

    def test_forged_rel_outside_allowlist_writes_nothing(self):
        bdir = self.dest / "deck-granular-system-forged"; bdir.mkdir()
        (bdir / "system/.ssh").mkdir(parents=True)
        (bdir / "system/.ssh/id_rsa").write_bytes(b"EVIL")
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="system", category_label="System config", system="samba", system_label="samba",
                    item=bm.make_item(id="system/.ssh/id_rsa", name="id_rsa", src="x",
                                      rel="system/.ssh/id_rsa", kind="file", size=4, extra={"group": "samba"}))
        bm.write(m, bm.manifest_path(bdir))
        summary = gb.restore_selection(str(bdir), [{"system": "samba", "id": "system/.ssh/id_rsa"}],
                                       "system", "20260728T140000", lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 0)
        self.assertFalse((self.home / ".ssh/id_rsa").exists(), "forged rel outside the allowlist wrote nothing")


class Rpc(_FakeHome):
    def test_groups_live_and_backup_guard(self):
        r = gc._system_groups({"source": "live"})
        keys = {g["key"] for g in r["groups"]}
        self.assertEqual(keys, {"control-panel", "lightgun", "samba", "backup-settings", "emudeck",
                                "mega-keys",    # the S4 credentials files (2026-07-30)
                                "temp-deck"})   # monitor + fan opt-in marker (2026-08-06)
        with self.assertRaises(RpcError):
            gc._granular_backup_system({"items": []})

    def test_restore_all_category_system(self):
        # granular.restore_all{category:"system"} enumerates every item + restores via the reviewed path.
        gb.backup_system(self._all_items(), str(self.dest), "20260728T150000", lambda e: None, lambda: False)
        src = str(self.dest / "deck-granular-system-20260728T150000")
        (self.home / "Emulation/storage/control-panel/gp-dualsense-calib.json").write_bytes(b"X")
        captured = {}

        def _sync(fn):
            captured["s"] = fn(lambda e: None, lambda: False)
            return {"stream": "s"}

        with mock.patch.object(gc, "_start_granular", _sync):
            gc._granular_restore_all({"source": src, "category": "system"})
        self.assertGreaterEqual(captured["s"]["restored"], 5)
        self.assertEqual((self.home / "Emulation/storage/control-panel/gp-dualsense-calib.json").read_bytes(),
                         b"CALIB")


class CloudPush(unittest.TestCase):
    """cloud.push_system persists a plan-dir + streams deck-cloud.sh push-system (SEPARATE system-backups
    base). No rclone/network - the shell engine is mocked."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _canned_plan(self, _items, ts, emit=None, is_stopped=None):
        m = bm.new_manifest("granular", created=ts)
        bm.add_item(m, category="system", category_label="System config", system="samba",
                    system_label="samba",
                    item=bm.make_item(id="system/Emulation/tools/smb.conf", name="smb.conf",
                                      src="/x/Emulation/tools/smb.conf", rel="system/Emulation/tools/smb.conf",
                                      kind="file", size=8, extra={"group": "samba"}))
        plan = [{"id": "system/Emulation/tools/smb.conf", "name": "smb.conf", "system": "samba",
                 "src": "/x/Emulation/tools/smb.conf", "rel": "system/Emulation/tools/smb.conf",
                 "kind": "file"}]
        return m, plan

    def test_push_system_persists_and_streams_own_subcmd(self):
        from lib.madsrv import cloud_cmds as cc
        seen = {}

        def fake_stream_op(argv):
            seen["argv"] = argv
            return {"stream": "s"}

        def fake_run(*a, **k):
            seen.setdefault("run", []).append(a)
            return (0, "", "")

        with mock.patch.object(cc.granular_backup, "plan_system", self._canned_plan), \
             mock.patch.object(cc, "_run", fake_run), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_system({"items": [{"group": "samba", "rel": "system/Emulation/tools/smb.conf"}]})
        self.assertEqual(out, {"stream": "s"})
        self.assertEqual(seen["argv"][1], "push-system")
        # fixed merged set: token is "system" (not a dated ts) + the remote manifest merge ran
        self.assertEqual(seen["argv"][2], "system")
        self.assertEqual(seen["run"][0][0], ["cat-system-manifest", "system"])
        plandir = Path(seen["argv"][3])
        self.assertEqual(plandir.parent, cc._state_dir() / "system-plan")
        self.assertTrue((plandir / "mad-manifest.json").is_file())

    def test_push_system_empty_einval(self):
        from lib.madsrv import cloud_cmds as cc
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_system({"items": []})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_push_system_is_not_a_restore(self):
        from lib.madsrv import cloud_cmds as cc
        self.assertFalse(cc._is_restore(["push-system", "20260728T000000", "/x/plan"]))
        self.assertEqual(cc._op_title(["push-system"]), "Backing up system config")


if __name__ == "__main__":
    unittest.main()
