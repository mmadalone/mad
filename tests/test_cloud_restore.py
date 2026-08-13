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


def _plan_roms_backup(items, ts):
    """TEST-ONLY replica of the retired granular_backup.plan_selection (audit 2026-08-12 phase 5: dead
    engine, no caller once granular.backup was retired): resolves `items` via game_files.resolve_rom
    (mocked in CloudRestore.setUp below) into a (manifest, plan) pair. CloudRestore's real subject is
    the CLOUD SIDE of restore - push a game to a LOCAL-override cloud set, then download + restore it
    through the LIVE granular RPCs (restore_selection, unchanged) - not this planning step, so a local
    stand-in for the retired planner is enough to build the pushed fixture."""
    manifest = bm.new_manifest("granular", created=ts)
    rom_root = gb.es_collections.rom_root()
    plan = []
    for it in items:
        system, stem = it["system"], it["stem"]
        paths = game_files.resolve_rom(system, stem)
        if not paths:
            continue
        src = os.path.realpath(paths[0])
        sysdir = os.path.realpath(str(rom_root / system))
        rel_rom = os.path.relpath(src, sysdir)
        kind = "folder" if os.path.isdir(src) else "file"
        rel = f"roms/{system}/{rel_rom}"
        name = gb.es_gamelist_record(system, stem).get("name") or stem
        bm.add_item(manifest, category="roms", category_label="ROMs & games",
                   system=system, system_label=es_systems.fullname(system),
                   item=bm.make_item(id=f"{system}:{stem}", name=name, src=src, rel=rel,
                                     kind=kind, size=gb._path_size(src), stem=stem))
        plan.append({"id": f"{system}:{stem}", "name": name, "system": system, "stem": stem,
                    "src": src, "rel": rel, "kind": kind})
    return manifest, plan


class CloudGateRefusals(unittest.TestCase):
    """The cloud download gate must SURFACE a refusal with its real reason and exclude
    the item from the whole operation (review 2026-08-04): before this, a
    library_unmounted prefix was silently dropped from the fetch plan and the
    post-download re-plan relabelled it missing_in_backup - preview and restore
    contradicted each other, and the insert-the-card hint never appeared."""

    APPID = 4108777888

    def test_game_first_refusal_is_surfaced_and_not_relabelled(self):
        from lib import steam_shortcuts as ss
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            roms = home / "ROMs"
            (roms / "steam").mkdir(parents=True)
            gone = home / "sdcard"                            # listed in vdf, absent
            vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
            vdf.parent.mkdir(parents=True)
            vdf.write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                           % gone)

            m = bm.new_manifest("granular", created="games")
            bm.add_item(m, category="roms", category_label="ROMs", system="steam",
                        system_label="Steam",
                        item=bm.make_item(id="roms/steam/Punisher.sh", name="The Punisher",
                                          src="/x/P.sh", rel="roms/steam/Punisher.sh",
                                          kind="file", size=1, stem="Punisher",
                                          extra={"game": "steam:Punisher", "asset": "rom"}))
            bm.add_item(m, category="steam", category_label="Steam", system="steam",
                        system_label="Steam",
                        item=bm.make_item(
                            id=f"steam/compatdata/{self.APPID}/pfx/drive_c",
                            name="The Punisher", src="/x/pfx",
                            rel=f"steam/compatdata/{self.APPID}/pfx/drive_c",
                            kind="folder", size=1, stem="Punisher",
                            extra={"game": "steam:Punisher", "asset": "saves",
                                   "croot": str(gone / "steamapps" / "compatdata")}))

            def fake_fetch(cts, staging, planpath, emit, is_stopped, **kw):
                bm.write(m, bm.manifest_path(staging))
                f = Path(staging) / "roms" / "steam" / "Punisher.sh"
                f.parent.mkdir(parents=True)
                f.write_text("exec steam\n")
                return 0

            ss._LIBRARY_CACHE["entry"] = (None, [])
            sink: list = []
            try:
                with mock.patch.object(g, "_cloud_manifest", return_value=m), \
                     mock.patch.object(g, "_stream_fetch", fake_fetch), \
                     mock.patch.object(ss, "home", return_value=home), \
                     mock.patch.object(ss, "nonsteam_shortcuts", return_value={
                         self.APPID: {"name": "The Punisher", "exe": "", "start_dir": ""}}), \
                     mock.patch.object(ss, "launcher_appid", return_value=self.APPID), \
                     mock.patch.object(gb.es_collections, "rom_root", lambda: roms), \
                     mock.patch.object(gb.proc_guard, "esde_running", return_value=False):
                    out = g._cloud_restore_assets(
                        "cloud:games",
                        [{"system": "steam", "stem": "Punisher", "keys": []}],
                        "20260804T000000", sink.append, lambda: False)
            finally:
                ss._LIBRARY_CACHE["entry"] = (None, [])

            lines = " | ".join(str(e.get("line", "")) for e in sink if isinstance(e, dict))
            self.assertIn("library_unmounted", lines, lines)
            self.assertIn("insert/mount", lines, "the hint must reach the user")
            self.assertNotIn("missing_in_backup", lines, "no wrong relabel after the download")
            self.assertEqual(out["restored"], 1, lines)      # the launcher still restores
            self.assertGreaterEqual(out["skipped"], 1)
            self.assertTrue((roms / "steam" / "Punisher.sh").exists())


class ShortNames(unittest.TestCase):
    """The system tiles use short, familiar names (the console art identifies the system) and sort
    alphabetically by the visible label. Applies to backup-select AND restore-browse."""
    def test_short_name_map_and_fallback(self):
        self.assertEqual(es_systems.short_name("gc"), "Nintendo GameCube")
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
            rows = g._manifest_games_browse(m, "src", "roms", None)["systems"]  # local + CLOUD share this
        # sorted by the VISIBLE label, so a manufacturer prefix moves a system: "Nintendo
        # GameCube" sorts under N, after "NES".
        self.assertEqual([r["label"] for r in rows], ["NES", "Nintendo GameCube", "PlayStation 2"])

    def test_cloud_browse_reaches_a_save_only_game(self):
        # REVIEW (cloud): the cloud restore browse is now the same UNIFIED game view as local, so a game
        # whose only backed-up asset is a save (ROM unticked) is reachable, not just ROM-bearing games.
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="saves", category_label="Saves", system="nes", system_label="NES",
                    item=bm.make_item(id="saves/retroarch/saves/smb.srm", name="SMB", src="/x",
                                      rel="saves/retroarch/saves/smb.srm", kind="file", size=1,
                                      extra={"game": "nes:smb", "asset": "saves"}))
        with mock.patch.object(g, "console_art", lambda s: ""):
            sysr = g._manifest_games_browse(m, "cloud:x", "roms", None)
            itemr = g._manifest_games_browse(m, "cloud:x", "roms", "nes")
        self.assertEqual([s["key"] for s in sysr["systems"]], ["nes"])
        self.assertEqual([it["id"] for it in itemr["items"]], ["nes:smb"])


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
        # the games set is the FIXED undated "games" (only esde/emucfg stay dated on MEGA)
        ts = "games"
        manifest, plan = _plan_roms_backup([{"system": "nes", "stem": "smb"}], ts)
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
        # same FIXED "games" set: this push REPLACES its manifest with the per-asset one, which is
        # what we want to count here (the shell writes the plan's manifest verbatim; the accumulate
        # -across-pushes merge lives in the Python push RPC, covered by its own tests).
        ts2 = "games"
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

    def test_cloud_game_first_restore_assets(self):
        # GAME-FIRST cloud restore: download the game's assets from the cloud set + restore via
        # restore_game_assets (item_game unification means a whole-ROM cloud backup restores this way too).
        self.rom.write_bytes(b"CORRUPT")
        sink = []
        summary = g._cloud_restore_assets(f"cloud:{self.ts}", [{"system": "nes", "stem": "smb", "keys": []}],
                                          "20260726T240000", sink.append, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual(self.rom.read_bytes(), b"MARIO" * 100, "the cloud copy was downloaded + restored")
        self.assertIsNotNone(summary["snapshot"], "rule-5 snapshot of the corrupt live copy")
        # the cloud preview classifies replace/fresh from the manifest, WITHOUT downloading
        self.rom.write_bytes(b"MARIO" * 100)   # now exists live -> a replace
        pv = gb.restore_preview_game_assets_manifest(g._cloud_manifest(self.ts),
                                                     [{"system": "nes", "stem": "smb", "keys": []}])
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
