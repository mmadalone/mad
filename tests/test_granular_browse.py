"""granular.* read side (granular_cmds): categories, sources, and the browse drill.

These monkeypatch the enumeration primitives (game systems, gamelist records, ROM/box-art resolvers)
so they are DETERMINISTIC and CI-safe - they do NOT depend on ~/ES-DE existing (it does not on CI).
They lock in:

  * browse source="live" builds per-system TILES (label=fullname, count) and per-game ROWS with a
    has_rom flag - a game whose ROM is absent comes back has_rom=false (greyed, NOT dropped);
  * browse of a BACKUP source reads its mad-manifest.json (systems + items) and rejects a source with
    no valid manifest;
  * an unknown category is rejected;
  * granular.sources lists the live source + local backups that carry a valid manifest.

Run:  python3 -m unittest tests.test_granular_browse -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm         # noqa: E402
from lib import es_gamelist, es_systems, game_files  # noqa: E402
from lib.madsrv import granular_cmds as g     # noqa: E402
from lib.madsrv.rpc import RpcError           # noqa: E402

# A fake one-system library: "smb" has a ROM, "gone" is a stale entry (ROM absent).
_FAKE_RECORDS = {
    "nes": {
        "smb": {"stem": "smb", "name": "Super Mario Bros.", "hidden": False},
        "gone": {"stem": "gone", "name": "Ghost Game", "hidden": False},
    }
}


def _fake_resolve_rom(system, stem):
    return ["/does/not/exist/nes/smb.zip"] if (system, stem) == ("nes", "smb") else []


def _fake_boxart(system, stem):
    return {"covers": "/media/nes/smb.png"} if stem == "smb" else {}


class _LiveBase(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(g, "_game_systems", lambda: ["nes"]),
            mock.patch.object(es_gamelist, "visible_records",
                              lambda s: _FAKE_RECORDS.get(s, {})),
            mock.patch.object(es_systems, "fullname",
                              lambda s: {"nes": "Nintendo Entertainment System"}.get(s, s)),
            mock.patch.object(g, "console_art", lambda s: f"/art/{s}.png"),
            mock.patch.object(game_files, "resolve_rom", _fake_resolve_rom),
            mock.patch.object(game_files, "resolve_boxart", _fake_boxart),
            mock.patch.object(game_files, "cover_path", lambda s, st: _fake_boxart(s, st).get("covers")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()


class LiveBrowse(_LiveBase):
    def test_systems_level_tiles(self):
        r = g._granular_browse({"source": "live", "category": "roms"})
        self.assertEqual(r["level"], "systems")
        # the tile shows the SHORT name (es_systems.short_name), not the long <fullname>
        self.assertEqual(r["systems"], [{"key": "nes", "label": "NES",
                                         "art": "/art/nes.png", "count": 2}])

    def test_items_level_has_rom_flag(self):
        r = g._granular_browse({"source": "live", "category": "roms", "system": "nes"})
        self.assertEqual(r["level"], "items")
        by_id = {i["id"]: i for i in r["items"]}
        self.assertIn("nes:smb", by_id)
        self.assertIn("nes:gone", by_id)
        smb = by_id["nes:smb"]
        self.assertTrue(smb["has_rom"])
        self.assertEqual(smb["kind"], "file")
        self.assertEqual(smb["art"], "/media/nes/smb.png")
        self.assertEqual(smb["name"], "Super Mario Bros.")
        gone = by_id["nes:gone"]
        self.assertFalse(gone["has_rom"], "a stale entry (absent ROM) must come back has_rom=false")
        self.assertIsNone(gone["art"])

    def test_items_sorted_by_name(self):
        r = g._granular_browse({"source": "live", "category": "roms", "system": "nes"})
        names = [i["name"] for i in r["items"]]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_unknown_category_rejected(self):
        with self.assertRaises(RpcError):
            g._granular_browse({"source": "live", "category": "nope"})


class ManifestBrowse(unittest.TestCase):
    def _backup_with_manifest(self, d: Path) -> Path:
        folder = d / "deck-roms-20260724"
        folder.mkdir()
        m = bm.new_manifest("roms", created="20260724T101530")
        bm.add_item(m, category="roms", category_label="ROMs & games", system="nes",
                    system_label="Nintendo Entertainment System",
                    item=bm.make_item(id="nes:smb", name="Super Mario Bros.", src="/x/smb.zip",
                                      rel="roms/nes/smb.zip", size=40960, boxart=True))
        bm.write(m, bm.manifest_path(folder))
        return folder

    def test_browse_manifest_systems_and_items(self):
        with tempfile.TemporaryDirectory() as d:
            folder = self._backup_with_manifest(Path(d))
            with mock.patch.object(g, "console_art", lambda s: f"/art/{s}.png"):
                sysr = g._granular_browse({"source": str(folder), "category": "roms"})
            self.assertEqual(sysr["level"], "systems")
            self.assertEqual(sysr["systems"][0]["key"], "nes")
            self.assertEqual(sysr["systems"][0]["count"], 1)
            # a LOCAL backup browses GAME-FIRST: one row per game ({id,stem,name,art}), unioning categories
            itemr = g._granular_browse({"source": str(folder), "category": "roms", "system": "nes"})
            self.assertEqual(itemr["items"][0]["id"], "nes:smb")
            self.assertEqual(itemr["items"][0]["stem"], "smb")
            self.assertEqual(itemr["items"][0]["name"], "Super Mario Bros.")

    def test_browse_source_without_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RpcError):
                g._granular_browse({"source": d, "category": "roms"})


class Sources(unittest.TestCase):
    def test_sources_live_plus_local_backup(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "deck-config-20260724"
            folder.mkdir()
            # the GAME restore source list shows backups that HAVE games; use a game-keyed item
            m = bm.new_manifest("granular", created="20260724T090000")
            bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                        item=bm.make_item(id="nes:smb", name="SMB", src="/x", rel="roms/nes/smb.zip"))
            bm.write(m, bm.manifest_path(folder))
            from lib.madsrv import backup_cmds
            with mock.patch.object(backup_cmds, "_remembered_dest", lambda: d), \
                 mock.patch.object(backup_cmds, "DEFAULT_DEST", d):
                srcs = g._granular_sources({})["sources"]
        kinds = {s["kind"] for s in srcs}
        self.assertIn("live", kinds)
        self.assertTrue(any(s["kind"] == "local" and s["label"] == "deck-config-20260724"
                            for s in srcs), srcs)

    def _make_backup(self, parent: Path, name: str, created: str) -> Path:
        folder = parent / name
        folder.mkdir()
        # a game-keyed backup so it lists in the GAME restore source scan (0-game backups are skipped)
        m = bm.new_manifest("granular", created=created)
        bm.add_item(m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                    item=bm.make_item(id="nes:smb", name="SMB", src="/x", rel="roms/nes/smb.zip"))
        bm.write(m, bm.manifest_path(folder))
        return folder

    def test_sources_under_scans_an_arbitrary_folder(self):
        # the SOURCE BROWSER: any folder (SD card / USB / ~/Downloads), NOT the remembered dest.
        with tempfile.TemporaryDirectory() as d:
            self._make_backup(Path(d), "deck-config-20260101", "20260101T010000")
            self._make_backup(Path(d), "deck-config-20260725", "20260725T120000")
            out = g._granular_sources_under({"path": d})
        self.assertEqual(out["path"], str(Path(d).resolve()))
        labels = [s["label"] for s in out["sources"]]
        self.assertEqual(labels, ["deck-config-20260725", "deck-config-20260101"])  # newest first
        self.assertTrue(all(s["kind"] == "local" for s in out["sources"]))

    def test_sources_under_tolerates_a_corrupt_created(self):
        # an arbitrary/foreign folder may hold a valid-schema manifest with a non-string `created`
        # (int/null); mixing it with a string one must NOT crash the newest-first sort.
        with tempfile.TemporaryDirectory() as d:
            good = self._make_backup(Path(d), "good", "20260101T000000")
            bad = self._make_backup(Path(d), "bad", "20260101T000000")
            # rewrite the bad one's created to an int (a foreign/hand-edited manifest)
            import json
            mp = bm.manifest_path(bad)
            data = json.loads(Path(mp).read_text())
            data["created"] = 123
            Path(mp).write_text(json.dumps(data))
            out = g._granular_sources_under({"path": d})  # must not raise
            labels = sorted(s["label"] for s in out["sources"])
        self.assertEqual(labels, ["bad", "good"])
        self.assertTrue(all(isinstance(s["created"], str) for s in out["sources"]))

    def test_sources_under_ignores_non_backup_entries(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "some-random-file.txt").write_text("not a backup")
            (Path(d) / "empty-dir").mkdir()
            out = g._granular_sources_under({"path": d})
        self.assertEqual(out["sources"], [])

    def test_sources_under_rejects_missing_or_non_folder(self):
        with self.assertRaises(RpcError):
            g._granular_sources_under({"path": ""})
        with self.assertRaises(RpcError):
            g._granular_sources_under({})
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "nope")
            with self.assertRaises(RpcError):
                g._granular_sources_under({"path": missing})
            afile = Path(d) / "afile"
            afile.write_text("x")
            with self.assertRaises(RpcError):
                g._granular_sources_under({"path": str(afile)})


if __name__ == "__main__":
    unittest.main()
