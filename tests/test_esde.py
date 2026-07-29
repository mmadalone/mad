"""ES-DE settings granular backup + STAGED restore (P6).

esde_map groups the fixed ~/ES-DE settings set into 5 tickable GROUPS (+ a per-system gamelist drill).
Restore is STAGED (rule #3): it writes NOTHING live - it copies into a $HOME-mirrored _staged-apply tree and
arms the single-shot marker, and the EXISTING apply-staged-restore.sh lays it onto live $HOME (with its own
rule-5) at the next ES-DE start. This test drives that whole path in a sandboxed $HOME.

CloudEsde (Deck-only, needs rclone): a real round-trip via DECK_CLOUD_ESDE_BASE_OVERRIDE + the SEPARATION
invariant (an ES-DE settings set is listed by esde.cloud_sources but NOT by the game / BIOS restore).

Run:  python3 -m unittest tests.test_esde -v
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
APPLIER = ROOT / "apply-staged-restore.sh"
BIN = Path.home() / "Emulation" / "tools" / "bin"
HAVE_RCLONE = (BIN / "rclone").exists()

from lib import backup_manifest as bm                      # noqa: E402
from lib import esde_map, esde_settings, granular_backup as gb  # noqa: E402
from lib.madsrv import granular_cmds as g                  # noqa: E402
from lib.madsrv.rpc import RpcError                        # noqa: E402


def _seed_esde(root: Path):
    """Create a minimal live ~/ES-DE settings tree; return {group: [rels]}."""
    (root / "settings").mkdir(parents=True)
    (root / "settings" / "es_settings.xml").write_text("SETTINGS-ORIG")
    (root / "settings" / "es_input.xml").write_text("INPUT-ORIG")
    (root / "settings" / "es_settings.xml.bak-old").write_text("BAK-DEBRIS")   # must be EXCLUDED
    (root / "custom_systems").mkdir()
    (root / "custom_systems" / "es_systems.xml").write_text("SYSTEMS-ORIG")
    (root / "collections").mkdir()
    (root / "collections" / "custom-fav.cfg").write_text("COLL-ORIG")
    (root / "splashscreens").mkdir()                     # user-curated launch artwork (default-on group)
    (root / "splashscreens" / "001.jpg").write_text("SPLASH-ORIG")
    (root / "splashscreens" / "002.png").write_text("SPLASH2-ORIG")
    for sysk in ("nes", "ps2"):
        (root / "gamelists" / sysk).mkdir(parents=True)
        (root / "gamelists" / sysk / "gamelist.xml").write_text(f"{sysk.upper()}-META-ORIG")
    # a regenerable dir that must NEVER be backed up
    (root / "themes").mkdir()
    (root / "themes" / "big.pak").write_text("X" * 100)


class EsdeMap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.esde = self.tmp / "ES-DE"
        _seed_esde(self.esde)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_groups_curated_and_exclude_debris(self):
        groups = {gp["key"]: gp for gp in esde_map.list_groups(self.esde)}
        self.assertEqual(set(groups),
                         {"settings", "input", "custom_systems", "collections", "splashscreens", "gamelists"})
        self.assertEqual([f["rel"] for f in groups["settings"]["files"]], ["esde/settings/es_settings.xml"])
        self.assertEqual(groups["gamelists"]["count"], 2)   # nes + ps2
        self.assertEqual(groups["splashscreens"]["count"], 2, "flat glob of the splash images")
        self.assertTrue(all(f["rel"].startswith("esde/splashscreens/")
                            for f in groups["splashscreens"]["files"]))
        # regenerable + debris never appear
        allrels = [f["rel"] for gp in groups.values() for f in gp["files"]]
        self.assertFalse(any("themes" in r or ".bak" in r for r in allrels))

    def test_gamelist_drill(self):
        rows = esde_map.list_gamelist_systems(self.esde)
        self.assertEqual({r["system"] for r in rows}, {"nes", "ps2"})
        self.assertTrue(all(r["rel"].startswith("esde/gamelists/") for r in rows))


class LocalStagedRestore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.esde = self.home / "ES-DE"
        _seed_esde(self.esde)
        self.dest = self.home / "dest"; self.dest.mkdir()
        self._env = {"HOME": str(self.home),
                     "DECK_CLOUD_STATE_DIR": str(self.home / ".config" / "deck-cloud")}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._p = [mock.patch.object(esde_settings, "APPDATA", self.esde),
                   mock.patch.object(gb.es_collections, "rom_root", lambda: self.home / "ROMs"),
                   mock.patch.object(gb.proc_guard, "esde_running", lambda: True)]  # ES-DE IS running
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def _items(self):
        # tick Main settings + Game favorites(nes) via the live groups
        groups = {gp["key"]: gp for gp in esde_map.list_groups(self.esde)}
        items = [{"group": "settings", "rel": groups["settings"]["files"][0]["rel"]}]
        nes = next(f for f in groups["gamelists"]["files"] if "/nes/" in f["rel"])
        items.append({"group": "gamelists", "rel": nes["rel"]})
        return items

    def test_backup_then_staged_restore_applies_at_boot(self):
        items = self._items()
        out = gb.backup_esde(items, str(self.dest), "20260727T120000", lambda e: None, lambda: False)
        self.assertEqual(out["copied"], 2)
        bdir = out["path"]
        self.assertTrue((Path(bdir) / "esde" / "settings" / "es_settings.xml").is_file())

        # change the LIVE files, then RESTORE - restore must NOT touch them (staged, ES-DE is running).
        (self.esde / "settings" / "es_settings.xml").write_text("SETTINGS-CHANGED")
        (self.esde / "gamelists" / "nes" / "gamelist.xml").write_text("NES-CHANGED")
        restore_items = [{"system": "settings", "id": "esde/settings/es_settings.xml"},
                         {"system": "gamelists", "id": "esde/gamelists/nes/gamelist.xml"}]
        summary = gb.restore_selection(bdir, restore_items, "esde", "20260727T130000",
                                       lambda e: None, lambda: False)
        # rule #3: the LIVE files are UNCHANGED by the restore itself
        self.assertEqual(summary["deferred"], True)
        self.assertEqual(summary["restored"], 2)
        self.assertEqual((self.esde / "settings" / "es_settings.xml").read_text(), "SETTINGS-CHANGED")
        self.assertEqual((self.esde / "gamelists" / "nes" / "gamelist.xml").read_text(), "NES-CHANGED")
        # the staged tree + marker are armed
        staged = Path(summary["staged"])
        self.assertTrue((staged / "ES-DE" / "settings" / "es_settings.xml").is_file())
        marker = self.home / ".config" / "deck-cloud" / "pending-restore-apply"
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text().strip(), str(staged))

        # now the launch wrapper (apply-staged-restore.sh) runs BEFORE ES-DE starts -> applies + rule-5.
        r = subprocess.run([str(APPLIER)], env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.esde / "settings" / "es_settings.xml").read_text(), "SETTINGS-ORIG")
        self.assertEqual((self.esde / "gamelists" / "nes" / "gamelist.xml").read_text(), "NES-META-ORIG")
        self.assertFalse(marker.exists(), "marker is single-shot (cleared after applying)")
        # rule-5: the overwritten live copies were moved aside first
        wrap = list((self.home / "Downloads" / "_TMP").glob("wrapper-apply-*"))
        self.assertTrue(wrap, "the applier kept a rule-5 backup of the overwritten live files")

    def test_splashscreens_staged_roundtrip(self):
        # user content (gap-audit): splash artwork backs up + STAGES its restore (rule #3, ES-DE running).
        groups = {gp["key"]: gp for gp in esde_map.list_groups(self.esde)}
        sp = sorted(groups["splashscreens"]["files"], key=lambda f: f["rel"])[0]   # esde/splashscreens/001.jpg
        out = gb.backup_esde([{"group": "splashscreens", "rel": sp["rel"]}], str(self.dest),
                             "20260729T120000", lambda e: None, lambda: False)
        self.assertTrue((Path(out["path"]) / sp["rel"]).is_file(), "splash image backed up")
        (self.esde / "splashscreens" / "001.jpg").write_text("SPLASH-CHANGED")
        summary = gb.restore_selection(out["path"], [{"system": "splashscreens", "id": sp["rel"]}],
                                       "esde", "20260729T130000", lambda e: None, lambda: False)
        self.assertTrue(summary["deferred"], "esde restore is staged")
        self.assertEqual((self.esde / "splashscreens" / "001.jpg").read_text(), "SPLASH-CHANGED",
                         "rule #3: nothing written live")
        self.assertTrue((Path(summary["staged"]) / "ES-DE" / "splashscreens" / "001.jpg").is_file())

    def test_preview_deferred_flag(self):
        items = self._items()
        gb.backup_esde(items, str(self.dest), "20260727T140000", lambda e: None, lambda: False)
        bdir = next(self.dest.glob("deck-granular-*"))
        pv = gb.restore_preview(str(bdir), [{"system": "settings", "id": "esde/settings/es_settings.xml"}],
                                "esde")
        self.assertTrue(pv["deferred"])
        self.assertEqual([r["id"] for r in pv["replace"]], ["esde/settings/es_settings.xml"])

    def test_plan_esde_skips_traversal_and_missing(self):
        _m, plan = gb.plan_esde([{"group": "x", "rel": "esde/../evil"},              # traversal
                                 {"group": "x", "rel": "esde/settings/ghost.xml"},   # absent
                                 {"group": "settings", "rel": "esde/settings/es_settings.xml"}],  # real
                                "t")
        self.assertEqual([e["rel"] for e in plan], ["esde/settings/es_settings.xml"])
        self.assertFalse((self.home / "evil").exists())

    def test_rpc_groups_and_backup_guard(self):
        gr = g._esde_groups({"source": "live"})
        self.assertEqual({x["key"] for x in gr["groups"]},
                         {"settings", "input", "custom_systems", "collections", "splashscreens", "gamelists"})
        # every group carries a plain-English explanation for the UI
        self.assertTrue(all(x["explain"] for x in gr["groups"]))
        with self.assertRaises(RpcError):
            g._granular_backup_esde({"items": []})


@unittest.skipUnless(HAVE_RCLONE, "needs rclone (Deck-only)")
class CloudEsde(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.esde = self.home / "ES-DE"
        _seed_esde(self.esde)
        self.esde_remote = self.home / "esde-remote"
        self.games_remote = self.home / "games-remote"
        self.bios_remote = self.home / "bios-remote"
        self._env = {"HOME": str(self.home), "DECK_CLOUD_RCLONE": str(BIN / "rclone"),
                     "DECK_CLOUD_SKIP_CONNCHECK": "1", "DECK_CLOUD_NO_NICE": "1",
                     "DECK_CLOUD_STATE_DIR": str(self.home / ".config" / "deck-cloud"),
                     "DECK_CLOUD_ESDE_BASE_OVERRIDE": str(self.esde_remote),
                     "DECK_CLOUD_GAMES_BASE_OVERRIDE": str(self.games_remote),
                     "DECK_CLOUD_BIOS_BASE_OVERRIDE": str(self.bios_remote)}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._p = [mock.patch.object(esde_settings, "APPDATA", self.esde),
                   mock.patch.object(gb.es_collections, "rom_root", lambda: self.home / "ROMs")]
        for p in self._p:
            p.start()
        self.ts = self._push()

    def tearDown(self):
        for p in self._p:
            p.stop()
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def _push(self):
        ts = "20260727T210000"
        groups = {gp["key"]: gp for gp in esde_map.list_groups(self.esde)}
        items = [{"group": "settings", "rel": groups["settings"]["files"][0]["rel"]}]
        manifest, plan = gb.plan_esde(items, ts)
        pd = self.home / "plan"; pd.mkdir()
        bm.write(manifest, bm.manifest_path(pd))
        (pd / "plan").write_bytes(
            b"".join(e["src"].encode() + b"\0" + e["rel"].encode() + b"\0" for e in plan))
        r = subprocess.run([str(CLOUD), "push-esde", ts, str(pd)], env=dict(os.environ),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return ts

    def test_esde_cloud_source_listed_and_separated(self):
        ids = {s["id"] for s in g._esde_cloud_sources({})["sources"]}
        self.assertIn(f"cloud:{self.ts}", ids)
        # SEPARATION: an ES-DE set must NOT appear in the game or BIOS restore lists
        self.assertNotIn(f"cloud:{self.ts}", {s["id"] for s in g._granular_cloud_sources({})["sources"]})
        self.assertNotIn(f"cloud:{self.ts}", {s["id"] for s in g._bios_cloud_sources({})["sources"]})

    def test_esde_cloud_restore_is_staged(self):
        (self.esde / "settings" / "es_settings.xml").write_text("CHANGED")
        sink = []
        summary = g._cloud_restore(f"cloud:{self.ts}",
                                   [{"system": "settings", "id": "esde/settings/es_settings.xml"}],
                                   "esde", "20260727T220000", sink.append, lambda: False)
        self.assertTrue(summary.get("deferred"))
        # cloud restore ALSO stages - the live file is untouched until the applier runs
        self.assertEqual((self.esde / "settings" / "es_settings.xml").read_text(), "CHANGED")
        marker = self.home / ".config" / "deck-cloud" / "pending-restore-apply"
        self.assertTrue(marker.is_file())
        r = subprocess.run([str(APPLIER)], env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.esde / "settings" / "es_settings.xml").read_text(), "SETTINGS-ORIG")


if __name__ == "__main__":
    unittest.main()
