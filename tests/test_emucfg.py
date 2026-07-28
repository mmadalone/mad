"""Emulator config+data granular category (P7): emu_map enumeration + backup + rule-5 restore, the three
multi-root safety points (writable snapshot anchor, allowlist, front-door symlink), the per-emulator running
guard, folder groups, and versioned dated naming.

emucfg spans MANY roots under $HOME, so the whole suite runs against a FAKE $HOME (patch Path.home) with a
few emulator trees built under it - never the real config.

Run:  python3 -m unittest tests.test_emucfg -v
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

from lib import emu_map, granular_backup as gb   # noqa: E402
from lib import backup_manifest as bm            # noqa: E402
from lib.madsrv import granular_cmds as gc        # noqa: E402
from lib.madsrv.rpc import RpcError               # noqa: E402


def _items_from_groups(emulator, groups, only_default=True):
    """The C++ leaf flow: collect ticked groups' files into [{emulator, group, rel}]."""
    items = []
    for g in groups:
        if only_default and not g["default_ticked"]:
            continue
        for f in g["files"]:
            items.append({"emulator": emulator, "group": g["key"], "rel": f["rel"]})
    return items


class _FakeHome(unittest.TestCase):
    """Base: a temp dir as $HOME with a PCSX2 tree (config + a memcard) and helpers."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        # PCSX2 config + a memory card (front-door-real files under the fake home).
        (self.home / ".config/PCSX2/inis").mkdir(parents=True)
        (self.home / ".config/PCSX2/inis/PCSX2.ini").write_bytes(b"PCSX2ORIG")
        (self.home / ".config/PCSX2/inis/PCSX2.ini.bak").write_bytes(b"BAKDEBRIS")  # excluded
        (self.home / ".config/PCSX2/inputprofiles").mkdir(parents=True)
        (self.home / ".config/PCSX2/inputprofiles/Default.ini").write_bytes(b"PADORIG")
        (self.home / "Emulation/saves/pcsx2/saves").mkdir(parents=True)
        (self.home / "Emulation/saves/pcsx2/saves/Mcd001.ps2").write_bytes(b"MCDORIG")
        self.dest = self.home / "dest"; self.dest.mkdir()
        self._p = [mock.patch.object(Path, "home", staticmethod(lambda: self.home)),
                   mock.patch.object(gb.es_collections, "rom_root", lambda: self.home / "ROMs"),
                   mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
                   mock.patch.object(gb.proc_guard, "emulator_running", lambda name: False)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _backup(self, items, ts="20260728T120000"):
        return gb.backup_emucfg(items, str(self.dest), ts, lambda e: None, lambda: False)


class Enumeration(_FakeHome):
    def test_list_emulators_present_only(self):
        keys = {t["key"] for t in emu_map.list_emulators()}
        self.assertIn("pcsx2", keys)
        self.assertNotIn("dolphin", keys, "an absent emulator is not a tile")

    def test_groups_defaults_and_rels(self):
        groups = {g["key"]: g for g in emu_map.list_files("pcsx2")}
        self.assertTrue(groups["config"]["default_ticked"])
        self.assertTrue(groups["saves"]["default_ticked"])
        self.assertFalse(groups["textures"]["default_ticked"], "textures opt-in")
        rels = {f["rel"] for g in groups.values() for f in g["files"]}
        self.assertIn("emucfg/.config/PCSX2/inis/PCSX2.ini", rels)
        self.assertIn("emucfg/Emulation/saves/pcsx2/saves/Mcd001.ps2", rels)
        self.assertNotIn("emucfg/.config/PCSX2/inis/PCSX2.ini.bak", rels, "*.bak* excluded")

    def test_allowlist(self):
        self.assertTrue(emu_map.rel_allowed("emucfg/.config/PCSX2/inis/PCSX2.ini"))
        self.assertFalse(emu_map.rel_allowed("emucfg/.ssh/id_rsa"))
        self.assertFalse(emu_map.rel_allowed("emucfg/../.bashrc"))


class BackupRestore(_FakeHome):
    def test_backup_then_restore_with_rule5(self):
        items = _items_from_groups("pcsx2", emu_map.list_files("pcsx2"))
        out = self._backup(items)
        self.assertTrue(out["copied"] >= 3)                                   # ini + pad + memcard
        self.assertTrue(Path(out["path"]).name.startswith("deck-granular-emucfg-"), "versioned dated set")
        self.assertTrue((Path(out["path"]) / "emucfg/.config/PCSX2/inis/PCSX2.ini").is_file())
        # corrupt the live files, restore over them (rule #5 keeps the corrupt copies aside).
        (self.home / ".config/PCSX2/inis/PCSX2.ini").write_bytes(b"CORRUPT")
        (self.home / "Emulation/saves/pcsx2/saves/Mcd001.ps2").write_bytes(b"CORRUPT")
        restore_items = [{"system": "pcsx2", "id": "emucfg/.config/PCSX2/inis/PCSX2.ini"},
                         {"system": "pcsx2", "id": "emucfg/Emulation/saves/pcsx2/saves/Mcd001.ps2"}]
        summary = gb.restore_selection(out["path"], restore_items, "emucfg",
                                       "20260728T130000", lambda e: None, lambda: False)
        self.assertEqual((summary["restored"], summary["replaced"]), (2, 2))
        self.assertEqual((self.home / ".config/PCSX2/inis/PCSX2.ini").read_bytes(), b"PCSX2ORIG")
        self.assertEqual((self.home / "Emulation/saves/pcsx2/saves/Mcd001.ps2").read_bytes(), b"MCDORIG")
        # THE /home-unwritable regression: the snapshot must be a WRITABLE dir UNDER the (fake) home, never
        # $HOME/.. -> /home. root_used = dirname(target) puts it inside the emulator's own tree.
        self.assertTrue(summary["snapshots"], "rule-5 snapshot taken")
        for snap in summary["snapshots"]:
            self.assertTrue(snap.startswith(str(self.home)), f"snapshot inside home: {snap}")
            self.assertTrue(os.path.isdir(snap) and (Path(snap) / "RECOVERY.txt").is_file())

    def test_folder_group_roundtrip(self):
        # a folder-mode opt-in group (textures) backs up + restores as a whole folder.
        tex = self.home / "Emulation/storage/pcsx2/textures/SLUS-123"
        tex.mkdir(parents=True)
        (tex / "tex0.png").write_bytes(b"TEX")
        groups = {g["key"]: g for g in emu_map.list_files("pcsx2")}
        tx = groups["textures"]
        self.assertEqual([f["kind"] for f in tx["files"]], ["folder"], "one folder row, not per-file")
        items = _items_from_groups("pcsx2", [tx], only_default=False)
        out = self._backup(items, ts="20260728T121500")
        self.assertEqual(out["copied"], 1)
        self.assertTrue((Path(out["path"]) / "emucfg/Emulation/storage/pcsx2/textures/SLUS-123/tex0.png")
                        .is_file(), "folder copied recursively")
        (tex / "tex0.png").write_bytes(b"CORRUPT")
        gb.restore_selection(out["path"],
                             [{"system": "pcsx2", "id": "emucfg/Emulation/storage/pcsx2/textures"}],
                             "emucfg", "20260728T122000", lambda e: None, lambda: False)
        self.assertEqual((tex / "tex0.png").read_bytes(), b"TEX", "folder restored")

    def test_cemu_frontdoor_symlink(self):
        # Cemu saves reach mlc01 via the front-door symlink Emulation/saves/Cemu/saves -> mlc01/usr/save.
        # The backup must enumerate + copy THROUGH the symlink (LEXICAL, not realpath) and restore through it.
        (self.home / ".config/Cemu").mkdir(parents=True)
        (self.home / ".config/Cemu/settings.xml").write_bytes(b"CEMUCFG")
        real = self.home / "Emulation/roms/wiiu/mlc01/usr/save/00050000/game"
        real.mkdir(parents=True)
        (real / "save.dat").write_bytes(b"CEMUSAVE")
        (self.home / "Emulation/saves/Cemu").mkdir(parents=True)
        os.symlink(self.home / "Emulation/roms/wiiu/mlc01/usr/save",
                   self.home / "Emulation/saves/Cemu/saves")
        groups = emu_map.list_files("cemu")
        rels = {f["rel"] for g in groups for f in g["files"]}
        self.assertIn("emucfg/Emulation/saves/Cemu/saves/00050000/game/save.dat", rels,
                      "save behind the front-door symlink is enumerated")
        items = _items_from_groups("cemu", groups)
        out = self._backup(items, ts="20260728T123000")
        (real / "save.dat").write_bytes(b"CORRUPT")   # corrupt through the real path
        gb.restore_selection(out["path"],
                             [{"system": "cemu",
                               "id": "emucfg/Emulation/saves/Cemu/saves/00050000/game/save.dat"}],
                             "emucfg", "20260728T124000", lambda e: None, lambda: False)
        self.assertEqual((real / "save.dat").read_bytes(), b"CEMUSAVE", "restored through the symlink")


class Safety(_FakeHome):
    def _forged_backup(self):
        """A backup dir whose manifest carries a LEGIT item + a FORGED item pointing outside emu dirs."""
        bdir = self.dest / "deck-granular-emucfg-forged"; bdir.mkdir()
        (bdir / "emucfg/.config/PCSX2/inis").mkdir(parents=True)
        (bdir / "emucfg/.config/PCSX2/inis/PCSX2.ini").write_bytes(b"FROMBACKUP")
        (bdir / "emucfg/.ssh").mkdir(parents=True)
        (bdir / "emucfg/.ssh/id_rsa").write_bytes(b"EVIL")
        m = bm.new_manifest("granular", created="20260728T100000")
        bm.add_item(m, category="emucfg", category_label="Emulator config & data", system="pcsx2",
                    system_label="PCSX2",
                    item=bm.make_item(id="emucfg/.config/PCSX2/inis/PCSX2.ini", name="PCSX2.ini",
                                      src="x", rel="emucfg/.config/PCSX2/inis/PCSX2.ini", kind="file",
                                      size=10, extra={"group": "config"}))
        bm.add_item(m, category="emucfg", category_label="Emulator config & data", system="pcsx2",
                    system_label="PCSX2",
                    item=bm.make_item(id="emucfg/.ssh/id_rsa", name="id_rsa", src="x",
                                      rel="emucfg/.ssh/id_rsa", kind="file", size=4,
                                      extra={"group": "config"}))
        bm.write(m, bm.manifest_path(bdir))
        return bdir

    def test_allowlist_blocks_forged_restore(self):
        bdir = self._forged_backup()
        prev = gb.restore_preview(str(bdir), [{"system": "pcsx2", "id": "emucfg/.ssh/id_rsa"}], "emucfg")
        self.assertEqual([s["reason"] for s in prev["skip"]], ["outside_emu_roots"])
        gb.restore_selection(str(bdir),
                             [{"system": "pcsx2", "id": "emucfg/.config/PCSX2/inis/PCSX2.ini"},
                              {"system": "pcsx2", "id": "emucfg/.ssh/id_rsa"}],
                             "emucfg", "20260728T131500", lambda e: None, lambda: False)
        self.assertFalse((self.home / ".ssh/id_rsa").exists(), "the forged item was NEVER written")
        self.assertEqual((self.home / ".config/PCSX2/inis/PCSX2.ini").read_bytes(), b"FROMBACKUP",
                         "the legit item restored normally")

    def test_per_emulator_running_guard(self):
        items = _items_from_groups("pcsx2", emu_map.list_files("pcsx2"))
        out = self._backup(items, ts="20260728T140000")
        restore_items = [{"system": "pcsx2", "id": "emucfg/.config/PCSX2/inis/PCSX2.ini"}]
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda name: name == "pcsx2"):
            with self.assertRaises(RuntimeError):
                gb.restore_selection(out["path"], restore_items, "emucfg",
                                     "20260728T141000", lambda e: None, lambda: False)
        # with the emulator NOT running it proceeds (already the default patch).
        summary = gb.restore_selection(out["path"], restore_items, "emucfg",
                                       "20260728T142000", lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 1)


class Rpc(_FakeHome):
    def test_systems_and_files_live(self):
        sysr = gc._emucfg_systems({"source": "live"})
        self.assertIn("pcsx2", {s["key"] for s in sysr["systems"]})
        filesr = gc._emucfg_files({"source": "live", "emulator": "pcsx2"})
        groups = {g["key"]: g for g in filesr["groups"]}
        self.assertTrue(groups["config"]["default_ticked"])
        self.assertFalse(groups["textures"]["default_ticked"])
        with self.assertRaises(RpcError):
            gc._emucfg_files({"source": "live"})          # emulator required

    def test_backup_rpc_requires_items(self):
        with self.assertRaises(RpcError):
            gc._granular_backup_emucfg({"items": []})

    def test_restore_rpc_ebusy_when_emulator_running(self):
        out = self._backup(_items_from_groups("pcsx2", emu_map.list_files("pcsx2")), ts="20260728T160000")
        params = {"source": out["path"], "category": "emucfg",
                  "items": [{"system": "pcsx2", "id": "emucfg/.config/PCSX2/inis/PCSX2.ini"}]}
        with mock.patch.object(gc, "emu_map", emu_map), \
             mock.patch("lib.proc_guard.emulator_running", lambda name: name == "pcsx2"):
            with self.assertRaises(RpcError) as ctx:
                gc._granular_restore(params)
            self.assertEqual(ctx.exception.code, "EBUSY")


class OutOfTreeFirmware(_FakeHome):
    """P10: firmware/BIOS an emulator keeps in its OWN dir (RPCS3 dev_flash, RetroArch system/) is NOT under
    ~/Emulation/bios, so ONLY the emucfg category can reach it - and it must restore to that real location."""

    def test_rpcs3_firmware_enumerated_allowed_and_restored(self):
        fw = self.home / ".config/rpcs3/dev_flash/sys/external"
        fw.mkdir(parents=True)
        (fw / "libaudio.sprx").write_bytes(b"FIRMWARE")
        (self.home / ".config/rpcs3/config.yml").write_bytes(b"CFG")
        groups = {g["key"]: g for g in emu_map.list_files("rpcs3")}
        self.assertIn("firmware", groups)
        self.assertTrue(groups["firmware"]["default_ticked"], "firmware is essential -> default ON")
        self.assertEqual([f["kind"] for f in groups["firmware"]["files"]], ["folder"],
                         "dev_flash is one folder row, not thousands of file rows")
        self.assertIn("emucfg/.config/rpcs3/dev_flash",
                      {f["rel"] for f in groups["firmware"]["files"]})
        self.assertTrue(emu_map.rel_allowed("emucfg/.config/rpcs3/dev_flash/sys/external/libaudio.sprx"),
                        "the restore allowlist accepts dev_flash (derived from the new spec)")
        # round-trip: back up dev_flash, corrupt it live, restore -> lands back at the out-of-tree location
        items = _items_from_groups("rpcs3", [groups["firmware"]])
        out = self._backup(items, ts="20260728T140000")
        self.assertTrue((Path(out["path"]) /
                         "emucfg/.config/rpcs3/dev_flash/sys/external/libaudio.sprx").is_file())
        (fw / "libaudio.sprx").write_bytes(b"CORRUPT")
        summary = gb.restore_selection(out["path"],
                                       [{"system": "rpcs3", "id": "emucfg/.config/rpcs3/dev_flash"}],
                                       "emucfg", "20260728T141000", lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual((fw / "libaudio.sprx").read_bytes(), b"FIRMWARE",
                         "PS3 firmware restored to ~/.config/rpcs3/dev_flash (out of the BIOS store)")

    def test_retroarch_system_enumerated_and_allowed(self):
        sysd = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/system"
        sysd.mkdir(parents=True)
        (sysd / "scph5501.bin").write_bytes(b"BIOS")
        (self.home / ".var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg").write_bytes(b"CFG")
        groups = {g["key"]: g for g in emu_map.list_files("retroarch")}
        self.assertIn("system", groups)
        self.assertTrue(groups["system"]["default_ticked"])
        self.assertIn("emucfg/.var/app/org.libretro.RetroArch/config/retroarch/system",
                      {f["rel"] for f in groups["system"]["files"]})
        self.assertTrue(emu_map.rel_allowed(
            "emucfg/.var/app/org.libretro.RetroArch/config/retroarch/system/scph5501.bin"),
            "RA system/ (out-of-tree BIOS) is restorable via emucfg")

    def test_retroarch_autoconfig_is_the_gamepad_routing(self):
        # the MAD router writes RA device-bind autoconfigs (per-pad input profiles) = RetroArch gamepad
        # routing; a folder-row "controller" group backs it up (default on).
        auto = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/autoconfig/udev"
        auto.mkdir(parents=True)
        (auto / "DualSense.cfg").write_bytes(b"input_device = \"DualSense\"")
        groups = {g["key"]: g for g in emu_map.list_files("retroarch")}
        self.assertIn("controller", groups)
        self.assertTrue(groups["controller"]["default_ticked"])
        self.assertEqual([f["kind"] for f in groups["controller"]["files"]], ["folder"],
                         "autoconfig is one folder row, not 1000+ file rows")
        self.assertTrue(emu_map.rel_allowed(
            "emucfg/.var/app/org.libretro.RetroArch/config/retroarch/autoconfig/udev/DualSense.cfg"))


class Versioning(unittest.TestCase):
    def test_dated_naming_not_fixed(self):
        d = gb._backup_dir("/tmp/x", "emucfg", "20260728T150000", versioned=True)
        self.assertEqual(d.name, "deck-granular-emucfg-20260728T150000")
        self.assertNotIn("emucfg", gb._FIXED_BUCKETS, "emucfg is versioned, never merged")


if __name__ == "__main__":
    unittest.main()
