"""Per-game CLOUD RESTORE end-to-end + the short-system-name relabel.

ShortNames (CI-safe): es_systems.short_name mapping/fallback + the sorted short-labelled system tiles.

CloudRestore (Deck-only, needs rclone): push a game to a LOCAL-override cloud set, then list / browse /
preview / restore it back through the granular RPCs - proving the cloud restore = download the selected
games to a staging folder + run the EXISTING restore_selection engine (rule-5 snapshot + replace) unchanged.

Run:  python3 -m unittest tests.test_cloud_restore -v
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

from lib import backup_manifest as bm                        # noqa: E402
from lib import es_systems, game_files, granular_backup as gb  # noqa: E402
from lib.madsrv import granular_cmds as g                    # noqa: E402


class ShortNames(unittest.TestCase):
    """The system tiles use short, familiar names (the console art identifies the system) and sort
    alphabetically by the visible label. Applies to backup-select AND restore-browse."""
    def test_short_name_map_and_fallback(self):
        self.assertEqual(es_systems.short_name("gc"), "GameCube")
        self.assertEqual(es_systems.short_name("ps2"), "PlayStation 2")
        self.assertEqual(es_systems.short_name("nes"), "NES")
        with mock.patch.object(es_systems, "fullnames", lambda: {"zzz": "Zzz Full"}):
            self.assertEqual(es_systems.short_name("zzz"), "Zzz Full")  # unmapped -> <fullname>
            self.assertEqual(es_systems.short_name("nope"), "nope")      # no map, no fullname -> key

    def test_manifest_systems_rows_short_and_sorted(self):
        m = bm.new_manifest("granular", created="x")
        for sysk in ("ps2", "gc", "nes"):  # inserted out of alphabetical order
            bm.add_item(m, category="roms", category_label="ROMs", system=sysk, system_label=sysk.upper(),
                        item=bm.make_item(id=f"{sysk}:g", name="G", src="/x", rel=f"roms/{sysk}/g.zip",
                                          size=1))
        with mock.patch.object(g, "console_art", lambda s: ""):
            rows = g._manifest_systems_rows(m, "roms")
        self.assertEqual([r["label"] for r in rows], ["GameCube", "NES", "PlayStation 2"])


class PreviewFromManifest(unittest.TestCase):
    """The cloud restore preview classifies replace/fresh from the MANIFEST alone (files still on MEGA):
    check_backup_file=False must skip the on-disk backup-file test while the live-target test stands."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.roms = self.tmp / "ROMs"; (self.roms / "nes").mkdir(parents=True)
        (self.roms / "nes" / "here.zip").write_bytes(b"LIVE")   # exists live -> replace
        self._p = mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms)
        self._p.start()
        self.m = bm.new_manifest("granular", created="x")
        for stem in ("here", "gone"):
            bm.add_item(self.m, category="roms", category_label="ROMs", system="nes", system_label="NES",
                        item=bm.make_item(id=f"nes:{stem}", name=stem, src="/x",
                                          rel=f"roms/nes/{stem}.zip", kind="file", size=1))

    def tearDown(self):
        self._p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replace_vs_fresh_without_backup_files(self):
        pv = gb.restore_preview_manifest(self.m, [{"system": "nes", "id": "nes:here"},
                                                  {"system": "nes", "id": "nes:gone"}], "roms")
        self.assertEqual([r["id"] for r in pv["replace"]], ["nes:here"])   # live copy exists
        self.assertEqual([r["id"] for r in pv["fresh"]], ["nes:gone"])     # no live copy
        self.assertEqual(pv["skip"], [])


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class CloudRestore(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.roms = self.base / "ROMs"; (self.roms / "nes").mkdir(parents=True)
        self.rom = self.roms / "nes" / "smb.zip"; self.rom.write_bytes(b"MARIO" * 100)
        self.gb_remote = self.base / "gb-remote"
        # env the deck-cloud.sh subprocesses (spawned by granular_cmds) inherit via os.environ
        self._env = {"DECK_CLOUD_RCLONE": str(BIN / "rclone"), "DECK_CLOUD_SKIP_CONNCHECK": "1",
                     "DECK_CLOUD_NO_NICE": "1", "DECK_CLOUD_STATE_DIR": str(self.base / "state"),
                     "DECK_CLOUD_GAMES_BASE_OVERRIDE": str(self.gb_remote)}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms),
            mock.patch.object(game_files, "resolve_rom",
                              lambda s, st: [str(self.rom)] if (s, st) == ("nes", "smb") else []),
            mock.patch.object(game_files, "resolve_boxart", lambda s, st: {}),
            mock.patch.object(es_systems, "fullname", lambda s: s.upper()),
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {"name": st}),
        ]
        for p in self._patches:
            p.start()
        self.ts = self._push_one()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _push_one(self):
        ts = "20260725T210000"
        manifest, plan = gb.plan_selection([{"system": "nes", "stem": "smb"}], "roms", "ROMs & games", ts)
        pd = self.base / "plan"; pd.mkdir()
        bm.write(manifest, bm.manifest_path(pd))
        (pd / "plan").write_bytes(
            b"".join(e["src"].encode() + b"\0" + e["rel"].encode() + b"\0" for e in plan))
        r = subprocess.run([str(CLOUD), "push-games", ts, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return ts

    def test_cloud_sources_lists_the_set(self):
        out = g._granular_cloud_sources({})
        self.assertTrue(out["connected"])
        src = {s["id"]: s for s in out["sources"]}.get(f"cloud:{self.ts}")
        self.assertIsNotNone(src, "the pushed cloud set is listed")
        self.assertEqual(src["count"], 1)

    def test_cloud_sources_counts_distinct_games_for_a_per_asset_set(self):
        # A per-asset backup (push_game_assets) writes MANY manifest items for ONE game (rom + save +
        # media...), each tagged extra={"game": "<sys>:<stem>"}. list-games must report 1 GAME (distinct
        # game tags), not the raw item count - else the cloud restore browser shows an inflated number.
        ts2 = "20260725T230000"
        a_save = self.base / "smb.srm"; a_save.write_bytes(b"SAVE")
        a_med = self.base / "smb.png"; a_med.write_bytes(b"PNG")
        entries = [(str(self.rom), "roms/nes/smb.zip"),
                   (str(a_save), "saves/retroarch/saves/smb.srm"),
                   (str(a_med), "media/covers/nes/smb.png")]
        m = bm.new_manifest("granular", created=ts2)
        for src, rel in entries:
            cat = rel.split("/", 1)[0]
            bm.add_item(m, category=cat, category_label=cat, system="nes", system_label="NES",
                        item=bm.make_item(id=rel, name="smb", src=src, rel=rel, kind="file", size=1,
                                          extra={"game": "nes:smb", "asset": cat}))
        pd = self.base / "plan2"; pd.mkdir()
        bm.write(m, bm.manifest_path(pd))
        (pd / "plan").write_bytes(
            b"".join(src.encode() + b"\0" + rel.encode() + b"\0" for src, rel in entries))
        r = subprocess.run([str(CLOUD), "push-games", ts2, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        src = {s["id"]: s for s in g._granular_cloud_sources({})["sources"]}.get(f"cloud:{ts2}")
        self.assertIsNotNone(src, "the per-asset cloud set is listed")
        self.assertEqual(src["count"], 1, "3 asset items for 1 game -> 1 GAME, not 3")

    def test_cloud_browse_systems_and_items(self):
        systems = g._granular_browse({"source": f"cloud:{self.ts}", "category": "roms"})
        self.assertEqual([s["key"] for s in systems["systems"]], ["nes"])
        self.assertEqual(systems["systems"][0]["label"], "NES")  # short name, not the fullname
        items = g._granular_browse({"source": f"cloud:{self.ts}", "category": "roms", "system": "nes"})
        self.assertEqual([i["id"] for i in items["items"]], ["nes:smb"])

    def test_cloud_preview_replace(self):
        pv = g._granular_restore_preview({"source": f"cloud:{self.ts}", "category": "roms",
                                          "items": [{"system": "nes", "id": "nes:smb"}]})
        self.assertEqual([r["id"] for r in pv["replace"]], ["nes:smb"])

    def test_cloud_restore_downloads_and_restores_with_rule5(self):
        self.rom.write_bytes(b"CORRUPT")   # mutate the live copy
        sink = []
        summary = g._cloud_restore(f"cloud:{self.ts}", [{"system": "nes", "id": "nes:smb"}],
                                   "roms", "20260725T220000", sink.append, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual(self.rom.read_bytes(), b"MARIO" * 100, "the cloud copy was downloaded + restored")
        self.assertIsNotNone(summary["snapshot"], "rule-5 snapshot of the corrupt live copy taken")
        self.assertTrue(any("MEGA" in e.get("line", "") or "download" in e.get("line", "") for e in sink),
                        "the download streamed a progress line")


if __name__ == "__main__":
    unittest.main()
