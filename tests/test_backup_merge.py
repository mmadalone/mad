"""No-version fixed-set MERGE for games/BIOS + dated snapshots for ES-DE settings (the standard-chooser
restructure). Games/BIOS write ONE fixed set per location that accumulates on re-backup; ES-DE settings
write a fresh dated snapshot each time. Plus the category-aware local source scan (BIOS/ES-DE-only backups
are now listable).

Run:  python3 -m unittest tests.test_backup_merge -v
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

from lib import backup_manifest as bm                              # noqa: E402
from lib import esde_settings, granular_backup as gb, mad_paths    # noqa: E402
from lib.madsrv import granular_cmds as g                          # noqa: E402


class GamesFixedMerge(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"; (self.roms / "nes").mkdir(parents=True)
        for stem in ("A", "B"):
            (self.roms / "nes" / f"{stem}.zip").write_bytes(stem.encode())
        self.dest = self.base / "dest"; self.dest.mkdir()
        self._p = [mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms),
                   mock.patch.object(gb.game_files, "resolve_rom",
                                     lambda s, st: [str(self.roms / "nes" / f"{st}.zip")]
                                     if (s == "nes" and st in ("A", "B")) else []),
                   mock.patch.object(gb.game_files, "resolve_boxart", lambda s, st: {}),
                   mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st})]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _backup(self, stem, ts):
        return gb.backup_selection([{"system": "nes", "stem": stem}], str(self.dest), "roms", "ROMs",
                                   ts, lambda e: None, lambda: False)

    def test_fixed_dir_and_merge(self):
        out_a = self._backup("A", "20260101T000000")
        # a FIXED name, not deck-granular-<ts>
        self.assertTrue(out_a["path"].endswith("deck-granular-games"))
        out_b = self._backup("B", "20260202T000000")
        self.assertEqual(out_a["path"], out_b["path"], "same fixed set both times")
        # one folder, both games, both ROM files on disk
        setdir = Path(out_a["path"])
        self.assertEqual(sorted(p.name for p in (setdir / "roms" / "nes").iterdir()), ["A.zip", "B.zip"])
        m = bm.read(setdir)
        self.assertEqual(sorted(it["id"] for it in bm.items(m, "roms", "nes")), ["nes:A", "nes:B"])
        # created is the set's BIRTH; updated bumped on the merge
        self.assertEqual(m["created"], "20260101T000000")
        self.assertEqual(m.get("updated"), "20260202T000000")

    def test_only_one_fixed_dir_ever(self):
        self._backup("A", "20260101T000000")
        self._backup("B", "20260202T000000")
        dirs = [p.name for p in self.dest.iterdir() if p.is_dir()]
        self.assertEqual(dirs, ["deck-granular-games"], "no dated game dirs accumulate")


class EsdeDatedSnapshots(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.esde = self.base / "ES-DE"
        (self.esde / "settings").mkdir(parents=True)
        (self.esde / "settings" / "es_settings.xml").write_text("S")
        self.dest = self.base / "dest"; self.dest.mkdir()
        self._p = mock.patch.object(esde_settings, "APPDATA", self.esde)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_dated_and_no_merge(self):
        items = [{"group": "settings", "rel": "esde/settings/es_settings.xml"}]
        a = gb.backup_esde(items, str(self.dest), "20260101T000000", lambda e: None, lambda: False)
        b = gb.backup_esde(items, str(self.dest), "20260202T000000", lambda e: None, lambda: False)
        self.assertTrue(a["path"].endswith("deck-granular-esde-20260101T000000"))
        self.assertNotEqual(a["path"], b["path"], "each ES-DE backup is a fresh dated snapshot")
        dirs = sorted(p.name for p in self.dest.iterdir() if p.is_dir())
        self.assertEqual(dirs, ["deck-granular-esde-20260101T000000", "deck-granular-esde-20260202T000000"])


class CategoryAwareSources(unittest.TestCase):
    def test_bios_only_backup_is_listable(self):
        base = Path(tempfile.mkdtemp())
        try:
            setdir = base / "deck-granular-bios"; (setdir / "bios").mkdir(parents=True)
            m = bm.new_manifest("granular", created="20260101T000000")
            bm.add_item(m, category="bios", category_label="BIOS", system="ps2", system_label="ps2",
                        item=bm.make_item(id="bios/ps2/x.bin", name="x.bin", src="/x",
                                          rel="bios/ps2/x.bin", kind="file", size=1))
            bm.write(m, bm.manifest_path(setdir))
            # roms scan (default) SKIPS it (0 games); bios scan LISTS it (1 file)
            self.assertEqual(g._scan_backup_sources([base], "roms"), [])
            bios = g._scan_backup_sources([base], "bios")
            self.assertEqual([s["count"] for s in bios], [1])
            self.assertEqual([s["id"] for s in bios], [str(setdir)])
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
