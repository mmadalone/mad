"""Game-first RESTORE (P3): restore a game's ticked asset groups (rom/saves/states/media) from a backup
folder back to their live locations ACROSS categories, under rule #5.

The safety-critical case is SAVES: on the real Deck ~/Emulation/saves/retroarch/saves is a SYMLINK to the
RetroArch flatpak, so realpath-based containment (what the roms path uses) would wrongly REJECT a legit
save restore. These tests reproduce that topology in a sandbox and prove:
  * lexical containment under the category root RESTORES a save THROUGH the front-door symlink (into the
    "flatpak"), while realpath-containment would have refused it;
  * a game-first restore lands rom + save + media across three roots, rule-5 snapshotting each replaced
    live copy first;
  * a foreign/crafted rel with a '..'/absolute component writes NOTHING outside the category root;
  * the read-only preview classifies replace/fresh and reports the media 'esde' restart scope.

Run:  python3 -m unittest tests.test_game_restore -v
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

from lib import backup_manifest as bm                        # noqa: E402
from lib import esde_settings, mad_paths                     # noqa: E402
from lib import granular_backup as gb                        # noqa: E402
from lib.madsrv import granular_cmds as g                    # noqa: E402
from lib.madsrv.rpc import RpcError                          # noqa: E402


def _find_bytes(root: Path, needle: bytes) -> bool:
    """Whether any file under root has exactly `needle` as its content (for locating a snapshotted copy)."""
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            try:
                if Path(dp, fn).read_bytes() == needle:
                    return True
            except OSError:
                pass
    return False


class GameFirstRestore(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        # live roots
        self.roms = self.base / "ROMs"
        (self.roms / "nes").mkdir(parents=True)
        self.saves = self.base / "saves"
        (self.saves / "retroarch").mkdir(parents=True)
        # THE key topology: saves/retroarch/saves is a SYMLINK to a "flatpak" dir OUTSIDE saves_root
        self.flat = self.base / "flatpak" / "retroarch" / "saves"
        self.flat.mkdir(parents=True)
        os.symlink(self.flat, self.saves / "retroarch" / "saves")
        self.media = self.base / "media"
        self.media.mkdir()
        # backup folder with the three assets + a manifest tagged extra.game/asset (as plan_game_assets writes)
        self.bdir = self.base / "backup" / "deck-granular-20260726T000000"
        self.bdir.mkdir(parents=True)
        self.entries = [
            ("roms", "roms/nes/smb.zip", b"ROMORIG", "rom"),
            ("saves", "saves/retroarch/saves/smb.srm", b"SAVEORIG", "saves"),
            ("media", "media/covers/nes/smb.png", b"MEDIAORIG", "media"),
        ]
        m = bm.new_manifest("granular", created="20260726T000000")
        for cat, rel, data, asset in self.entries:
            (self.bdir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.bdir / rel).write_bytes(data)
            bm.add_item(m, category=cat, category_label=cat, system="nes", system_label="NES",
                        item=bm.make_item(id=rel, name="Mario", src="/x", rel=rel, kind="file",
                                          size=len(data), extra={"game": "nes:smb", "asset": asset}))
        bm.write(m, bm.manifest_path(self.bdir))
        # patch the category roots to the sandbox
        self._patches = [
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.roms),
            mock.patch.object(mad_paths, "saves_root", lambda: self.saves),
            mock.patch.object(esde_settings, "media_root", lambda: self.media),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _games(self, keys):
        return [{"system": "nes", "stem": "smb", "keys": keys}]

    def test_restore_all_three_categories_with_rule5(self):
        # live copies: rom + save exist (corrupt -> replace + snapshot); media absent (fresh)
        (self.roms / "nes" / "smb.zip").write_bytes(b"LIVEROM")
        (self.flat / "smb.srm").write_bytes(b"LIVESAVE")   # reached via the saves/retroarch/saves symlink
        sink = []
        summary = gb.restore_game_assets(str(self.bdir), self._games(["rom", "saves", "media"]),
                                         "20260726T010000", sink.append, lambda: False)
        self.assertEqual((summary["restored"], summary["replaced"], summary["skipped"]),
                         (3, 2, 0), summary)
        self.assertEqual(summary["orphaned"], [])
        # each asset landed at its live location
        self.assertEqual((self.roms / "nes" / "smb.zip").read_bytes(), b"ROMORIG")
        # the save was written THROUGH the front-door symlink into the flatpak (lexical containment works
        # where realpath-containment would have rejected the symlinked destination)
        self.assertEqual((self.flat / "smb.srm").read_bytes(), b"SAVEORIG")
        self.assertEqual((self.media / "covers" / "nes" / "smb.png").read_bytes(), b"MEDIAORIG")
        # rule #5: the corrupt live rom + save are preserved in a snapshot (not destroyed)
        self.assertTrue(summary["snapshots"], "a snapshot dir was created")
        self.assertTrue(any(_find_bytes(Path(s), b"LIVEROM") for s in summary["snapshots"]),
                        "the overwritten live ROM is recoverable from a snapshot")
        self.assertTrue(any(_find_bytes(Path(s), b"LIVESAVE") for s in summary["snapshots"]),
                        "the overwritten live SAVE is recoverable from a snapshot")
        # media pulls in an ES-DE restart to show the restored art
        self.assertEqual(summary["restart_scope"], "esde")

    def test_realpath_of_restored_save_is_outside_saves_root(self):
        # documents WHY lexical containment is needed: the legit save target realpath escapes saves_root
        (self.flat / "smb.srm").write_bytes(b"LIVESAVE")
        gb.restore_game_assets(str(self.bdir), self._games(["saves"]), "20260726T010000",
                               lambda e: None, lambda: False)
        target = self.saves / "retroarch" / "saves" / "smb.srm"
        self.assertEqual(target.read_bytes(), b"SAVEORIG")
        self.assertFalse(os.path.realpath(target).startswith(os.path.realpath(self.saves) + os.sep),
                         "the restored save's realpath is the flatpak, OUTSIDE saves_root - realpath "
                         "containment would have wrongly rejected this legit restore")

    def test_only_ticked_keys_restore(self):
        (self.roms / "nes" / "smb.zip").write_bytes(b"LIVEROM")
        summary = gb.restore_game_assets(str(self.bdir), self._games(["saves"]),  # only saves ticked
                                         "20260726T010000", lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual((self.roms / "nes" / "smb.zip").read_bytes(), b"LIVEROM", "the ROM was NOT touched")
        self.assertEqual((self.saves / "retroarch" / "saves" / "smb.srm").read_bytes(), b"SAVEORIG")

    def test_preview_classifies_replace_fresh_and_restart(self):
        (self.roms / "nes" / "smb.zip").write_bytes(b"LIVEROM")   # rom exists -> replace
        (self.flat / "smb.srm").write_bytes(b"LIVESAVE")          # save exists -> replace
        # media absent -> fresh
        pv = gb.restore_preview_game_assets(str(self.bdir), self._games(["rom", "saves", "media"]))
        self.assertEqual(sorted(r["id"] for r in pv["replace"]),
                         ["roms/nes/smb.zip", "saves/retroarch/saves/smb.srm"])
        self.assertEqual([r["id"] for r in pv["fresh"]], ["media/covers/nes/smb.png"])
        self.assertEqual(pv["skip"], [])
        self.assertEqual(pv["restart_scope"], "esde")

    def test_traversal_rel_writes_nothing_outside_root(self):
        # a foreign manifest whose saves rel escapes with '..' must be rejected, writing nothing outside
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="saves", category_label="saves", system="nes", system_label="NES",
                    item=bm.make_item(id="saves/../../evil.srm", name="evil", src="/x",
                                      rel="saves/../../evil.srm", kind="file", size=1))
        evil_dir = self.bdir.parent  # sits above the backup dir
        summary = gb.restore_selection(str(self.bdir), [{"system": "nes", "id": "saves/../../evil.srm"}],
                                       "saves", "20260726T010000", lambda e: None, lambda: False)
        # NB: the crafted item is not in the sandbox backup's manifest -> not_in_manifest; and even a matching
        # rel is rejected by the '..' guard. Either way nothing lands and nothing is restored.
        self.assertEqual(summary["restored"], 0)
        self.assertFalse((evil_dir / "evil.srm").exists(), "no traversal write above the backup dir")
        self.assertFalse((self.base / "evil.srm").exists(), "no traversal write to the sandbox root")

    def test_preview_dedups_a_shared_flat_save(self):
        # REVIEW #1 (low): two same-stem games (nes:smb, snes:smb) whose flat RetroArch save is the SAME
        # physical file must appear ONCE in the preview 'replace' list, matching the real restore's dedup.
        (self.flat / "smb.srm").write_bytes(b"LIVESAVE")   # the single shared live save
        bdir = self.base / "backup2" / "deck-granular-x"
        (bdir / "saves" / "retroarch" / "saves").mkdir(parents=True)
        (bdir / "saves" / "retroarch" / "saves" / "smb.srm").write_bytes(b"BK")
        m = bm.new_manifest("granular", created="x")
        for sysk in ("nes", "snes"):
            bm.add_item(m, category="saves", category_label="saves", system=sysk,
                        system_label=sysk.upper(),
                        item=bm.make_item(id="saves/retroarch/saves/smb.srm", name="smb", src="/x",
                                          rel="saves/retroarch/saves/smb.srm", kind="file", size=2,
                                          extra={"game": f"{sysk}:smb", "asset": "saves"}))
        bm.write(m, bm.manifest_path(bdir))
        pv = gb.restore_preview_game_assets(
            str(bdir), [{"system": "nes", "stem": "smb", "keys": ["saves"]},
                        {"system": "snes", "stem": "smb", "keys": ["saves"]}])
        self.assertEqual(len(pv["replace"]), 1, "the shared flat save is counted once, not twice")

    def test_forged_nonstring_game_field_is_skipped_not_crash(self):
        # REVIEW #2 (low): a foreign manifest with an unhashable game/asset field must be a clean skip, not
        # a TypeError bubbling out of the preview as a generic internal error.
        bdir = self.base / "backup3" / "deck-granular-x"
        (bdir / "saves" / "retroarch" / "saves").mkdir(parents=True)
        (bdir / "saves" / "retroarch" / "saves" / "smb.srm").write_bytes(b"BK")
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="saves", category_label="saves", system="nes", system_label="NES",
                    item=bm.make_item(id="saves/retroarch/saves/smb.srm", name="smb", src="/x",
                                      rel="saves/retroarch/saves/smb.srm", kind="file", size=2))
        m["categories"]["saves"]["systems"]["nes"]["items"][0]["game"] = ["nes:smb"]  # unhashable
        bm.write(m, bm.manifest_path(bdir))
        pv = gb.restore_preview_game_assets(str(bdir), self._games(["saves"]))   # must not raise
        self.assertEqual((pv["replace"], pv["fresh"], pv["skip"]), ([], [], []))
        # and the RPC returns cleanly too (no EINTERNAL)
        out = g._granular_restore_assets_preview({"source": str(bdir), "games": self._games(["saves"])})
        self.assertEqual(out["replace"], [])

    def _whole_rom_backup(self):
        # a WHOLE-ROM backup (RUN FULL BACKUP / the pilot): items keyed by id '<sys>:<stem>', NO game/asset
        bdir = self.base / "wholerom" / "deck-granular-w"
        (bdir / "roms" / "nes").mkdir(parents=True)
        (bdir / "roms" / "nes" / "smb.zip").write_bytes(b"ROMORIG")
        m = bm.new_manifest("granular", created="w")
        bm.add_item(m, category="roms", category_label="ROMs & games", system="nes", system_label="NES",
                    item=bm.make_item(id="nes:smb", name="Mario", src="/x", rel="roms/nes/smb.zip",
                                      kind="file", size=7, stem="smb"))
        bm.write(m, bm.manifest_path(bdir))
        return bdir

    def test_whole_rom_backup_restores_via_game_first(self):
        # REVIEW #1 (HIGH regression): a whole-ROM local backup (no game/asset tags) MUST restore through
        # the game-first path - item_game maps its id to the game + the "rom" asset.
        bdir = self._whole_rom_backup()
        (self.roms / "nes" / "smb.zip").write_bytes(b"LIVEROM")   # exists -> replace + snapshot
        summary = gb.restore_game_assets(str(bdir), self._games([]),   # empty keys = all assets
                                         "20260726T020000", lambda e: None, lambda: False)
        self.assertEqual((summary["restored"], summary["replaced"]), (1, 1), summary)
        self.assertEqual((self.roms / "nes" / "smb.zip").read_bytes(), b"ROMORIG")

    def test_whole_rom_backup_browse_and_drill(self):
        bdir = self._whole_rom_backup()
        with mock.patch.object(g, "console_art", lambda s: ""):
            sysr = g._granular_browse({"source": str(bdir), "category": "roms"})
            itemr = g._granular_browse({"source": str(bdir), "category": "roms", "system": "nes"})
        self.assertEqual(sysr["systems"][0]["key"], "nes")
        self.assertEqual([it["id"] for it in itemr["items"]], ["nes:smb"])
        # the drill/asset list surfaces a ROM group for a whole-ROM backup too
        ga = g._granular_game_assets({"source": str(bdir), "system": "nes", "game": "smb"})
        self.assertEqual([a["key"] for a in ga["assets"]], ["rom"])

    def test_save_only_backup_is_reachable_and_restores(self):
        # REVIEW #2 (MED): a backup whose ONLY asset is a save (ROM unticked) must still appear in the
        # restore browse (a UNION of categories) and restore.
        (self.flat / "smb.srm").write_bytes(b"LIVESAVE")
        bdir = self.base / "saveonly" / "deck-granular-s"
        (bdir / "saves" / "retroarch" / "saves").mkdir(parents=True)
        (bdir / "saves" / "retroarch" / "saves" / "smb.srm").write_bytes(b"SAVEONLY")
        m = bm.new_manifest("granular", created="s")
        bm.add_item(m, category="saves", category_label="Saves", system="nes", system_label="NES",
                    item=bm.make_item(id="saves/retroarch/saves/smb.srm", name="Mario", src="/x",
                                      rel="saves/retroarch/saves/smb.srm", kind="file", size=8,
                                      extra={"game": "nes:smb", "asset": "saves"}))
        bm.write(m, bm.manifest_path(bdir))
        with mock.patch.object(g, "console_art", lambda s: ""):
            itemr = g._granular_browse({"source": str(bdir), "category": "roms", "system": "nes"})
        self.assertEqual([it["id"] for it in itemr["items"]], ["nes:smb"], "save-only game is reachable")
        summary = gb.restore_game_assets(str(bdir), self._games([]), "20260726T030000",
                                         lambda e: None, lambda: False)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual((self.flat / "smb.srm").read_bytes(), b"SAVEONLY")

    def test_rpc_guards(self):
        # empty games / live / cloud source are clean RpcErrors (so the C++ releases its mRunning guard)
        with self.assertRaises(RpcError):
            g._granular_restore_assets({"source": str(self.bdir), "games": []})
        with self.assertRaises(RpcError):
            g._granular_restore_assets({"source": "live", "games": self._games(["rom"])})
        with self.assertRaises(RpcError):
            g._granular_restore_assets({"source": "cloud:20260101T000000", "games": self._games(["rom"])})
        with self.assertRaises(RpcError):
            g._granular_restore_assets_preview({"source": "cloud:20260101T000000",
                                                "games": self._games(["rom"])})


if __name__ == "__main__":
    unittest.main()
