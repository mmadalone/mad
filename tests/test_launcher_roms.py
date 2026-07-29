"""Launcher / manifest / index ROM completeness (task #104 generalized).

For openbor / mugen / pcsx2x6 / segacd / cannonball the ES-DE gamelist <path> is a tiny POINTER; the real
game bytes live in a sibling folder / referenced .cue tracks / a romset in the same folder. Before this fix
a per-game backup captured ONLY the pointer -> a restore left the game missing. These tests prove:
  * resolve_rom_files() expands each launcher to launcher + real payload, kind file/folder, WITHIN ~/ROMs/<sys>;
  * an out-of-tree reference (a crafted .cue) is dropped (no traversal);
  * the ROM asset group carries the whole set + a real size; OpenBOR saves get their own "Save" asset;
  * cover_path falls back to a miximage/screenshot when no cover was scraped (mugen);
  * a real backup CAPTURES the payload and restores it with the rule-5 snapshot.

Runs against a FAKE ROMs tree (patch es_collections.rom_root + es_gamelist.rom_paths).
Run:  python3 -m unittest tests.test_launcher_roms -v
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

from lib import (es_collections, es_gamelist, esde_settings, game_files,  # noqa: E402
                 granular_backup as gb, mad_paths, retroarch_cfg)
from lib.madsrv import granular_cmds as g  # noqa: E402


def _build_rom_tree(root: Path) -> dict:
    """A fake ~/ROMs with one game per launcher system + a plain nes ROM + an out-of-tree bait file.
    Returns the per-system rom_paths dict (stem_lower -> gamelist raw path)."""
    roms = root / "ROMs"

    # openbor: <G>.openbor manifest (DIR=) + the real game FOLDER (with regenerable + saves subdirs)
    ob = roms / "openbor"
    ob.mkdir(parents=True)
    (ob / "CARNAGE.openbor").write_text("# ES-DE OpenBOR launcher manifest\nDIR=CARNAGE\nEXE=game.exe\n")
    (ob / "CARNAGE").mkdir()
    (ob / "CARNAGE" / "game.exe").write_bytes(b"OPENBOR-EXE")
    (ob / "CARNAGE" / "Paks").mkdir()
    (ob / "CARNAGE" / "Paks" / "data.pak").write_bytes(b"PAKDATA")
    (ob / "CARNAGE" / "Logs").mkdir()
    (ob / "CARNAGE" / "Logs" / "run.log").write_bytes(b"LOG")          # regenerable -> excluded
    (ob / "CARNAGE" / "ScreenShots").mkdir()
    (ob / "CARNAGE" / "ScreenShots" / "s.png").write_bytes(b"SHOT")    # regenerable -> excluded
    (ob / "CARNAGE" / "Saves").mkdir()
    (ob / "CARNAGE" / "Saves" / "slot1.save").write_bytes(b"OPENBOR-SAVE")  # its own Save asset

    # mugen: <stem>.mugen bash launcher whose game FOLDER differs from the stem (the arg after the mode)
    mg = roms / "mugen"
    mg.mkdir(parents=True)
    (mg / "AvX_game.mugen").write_text(
        '#!/usr/bin/env bash\nexec "$HOME/Emulation/tools/launchers/mugen.sh" ikemen "AvXdir"\n')
    (mg / "AvX_game.sh").write_text("# dead RetroPie/Wine alt-launcher - must NOT be in the payload\n")
    (mg / "AvXdir").mkdir()
    (mg / "AvXdir" / "select.def").write_bytes(b"MUGEN")
    # a launcher with an ABSOLUTE path arg (win/native mode) - must NOT make _rom_payload grab the whole dir
    (mg / "AbsGame.mugen").write_text(
        '#!/usr/bin/env bash\nexec "$HOME/Emulation/tools/launchers/mugen.sh" win "/opt/wine/AbsGame/g.exe"\n')

    # pcsx2x6 (namco): <ID>.acgame INI (subdir=) + the game FOLDER
    px = roms / "pcsx2x6"
    px.mkdir(parents=True)
    (px / "NM1.acgame").write_text("[game]\nname=Test\ngameid=NM1\n[data]\nsubdir=NM1game\nelf=a.elf\n")
    (px / "NM1game").mkdir()
    (px / "NM1game" / "game.chd").write_bytes(b"NAMCO-CHD")

    # segacd: a .cue index referencing sibling .bin tracks (+ a crafted out-of-tree .cue)
    sc = roms / "segacd"
    sc.mkdir(parents=True)
    (sc / "Disc.cue").write_text(
        'FILE "Disc (Track 01).bin" BINARY\n  TRACK 01 MODE1/2352\n'
        'FILE "Disc (Track 02).bin" BINARY\n  TRACK 02 AUDIO\n'
        'FILE "Disc (Track 99).bin" BINARY\n  TRACK 03 AUDIO\n')       # track 99 missing on disk
    (sc / "Disc (Track 01).bin").write_bytes(b"TRACK1" * 100)
    (sc / "Disc (Track 02).bin").write_bytes(b"TRACK2" * 100)
    (sc / "Evil.cue").write_text('FILE "../evil.bin" BINARY\n  TRACK 01 MODE1/2352\n')  # traversal bait
    (root / "ROMs" / "evil.bin").write_bytes(b"SECRET")               # outside segacd, inside ROMs

    # cannonball: a 0-byte .game marker + the sibling romset in the same folder
    cb = roms / "cannonball"
    cb.mkdir(parents=True)
    (cb / "CannonBall.game").write_bytes(b"")
    (cb / "epr-1.bin").write_bytes(b"ROMSET1")
    (cb / "epr-2.bin").write_bytes(b"ROMSET2")

    # a plain single-file ROM (unchanged behaviour)
    nes = roms / "nes"
    nes.mkdir(parents=True)
    (nes / "smb.zip").write_bytes(b"NESROM")

    # a folder-per-game ROM (daphne-style): the primary IS a directory -> the browse must DEFER its size to 0
    dp = roms / "daphnex"
    dp.mkdir(parents=True)
    (dp / "Game.daphne").mkdir()
    (dp / "Game.daphne" / "big.bin").write_bytes(b"X" * 5000)

    return {
        "openbor": {"carnage": "./CARNAGE.openbor"},
        "mugen": {"avx_game": "./AvX_game.mugen", "absgame": "./AbsGame.mugen"},
        "pcsx2x6": {"nm1": "./NM1.acgame"},
        "segacd": {"disc": "./Disc.cue", "evil": "./Evil.cue"},
        "cannonball": {"cannonball": "./CannonBall.game"},
        "nes": {"smb": "./smb.zip"},
        "daphnex": {"game": "./Game.daphne"},
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="launcher-"))
        self.rom_paths = _build_rom_tree(self.tmp)
        self.roms = self.tmp / "ROMs"
        self._p = [
            mock.patch.object(es_collections, "rom_root", lambda: self.roms),
            mock.patch.object(es_gamelist, "rom_paths", lambda s: self.rom_paths.get(s, {})),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _names(self, system, stem):
        return {os.path.basename(it["path"]): it["kind"]
                for it in game_files.resolve_rom_files(system, stem)}


class ResolveRomFiles(_Base):
    def test_openbor_expands_folder_excludes_regenerable_and_saves(self):
        got = self._names("openbor", "CARNAGE")
        self.assertEqual(got, {"CARNAGE.openbor": "file", "game.exe": "file", "Paks": "folder"})
        for junk in ("Logs", "ScreenShots", "Saves"):
            self.assertNotIn(junk, got, f"{junk} must not be in the ROM payload")

    def test_mugen_uses_launcher_arg_folder_not_stem(self):
        got = self._names("mugen", "AvX_game")
        # the game folder is "AvXdir" (the launcher arg), NOT the stem "AvX_game"; the dead .sh is EXCLUDED
        self.assertEqual(got, {"AvX_game.mugen": "file", "AvXdir": "folder"})
        self.assertNotIn("AvX_game.sh", got, "the dead Wine-era .sh must not be in the ROM payload")

    def test_mugen_absolute_arg_does_not_grab_system_dir(self):
        # a win/native launcher with an ABSOLUTE path arg -> _mugen_game_dir must fall back to the stem, so
        # _rom_payload does NOT append the whole ~/ROMs/mugen dir (which would sweep in every sibling game)
        got = self._names("mugen", "AbsGame")
        self.assertEqual(set(got), {"AbsGame.mugen"}, "only the launcher (no folder matches) - never the dir")
        self.assertNotIn("mugen", got, "the mugen system dir must never be grabbed as a payload folder")
        self.assertEqual(game_files._mugen_game_dir(
            str(self.roms / "mugen" / "AbsGame.mugen"), "AbsGame"), "AbsGame")

    def test_pcsx2x6_subdir_folder(self):
        self.assertEqual(self._names("pcsx2x6", "NM1"), {"NM1.acgame": "file", "NM1game": "folder"})

    def test_segacd_cue_tracks_present_missing_dropped(self):
        got = self._names("segacd", "Disc")
        self.assertEqual(set(got), {"Disc.cue", "Disc (Track 01).bin", "Disc (Track 02).bin"})
        self.assertNotIn("Disc (Track 99).bin", got, "a referenced-but-absent track is not returned")

    def test_cannonball_marker_plus_siblings(self):
        self.assertEqual(self._names("cannonball", "CannonBall"),
                         {"CannonBall.game": "file", "epr-1.bin": "file", "epr-2.bin": "file"})

    def test_out_of_tree_cue_reference_dropped(self):
        # the crafted Evil.cue points at ../evil.bin (inside ROMs but OUTSIDE segacd) -> only the cue itself
        got = self._names("segacd", "Evil")
        self.assertEqual(set(got), {"Evil.cue"}, "a traversal reference must be dropped")
        self.assertTrue((self.roms / "evil.bin").exists(), "sanity: the bait file exists")

    def test_plain_file_rom_unchanged(self):
        self.assertEqual(self._names("nes", "smb"), {"smb.zip": "file"})

    def test_missing_game_returns_empty(self):
        self.assertEqual(game_files.resolve_rom_files("nes", "NoSuchGame"), [])


class AssetGroups(_Base):
    def setUp(self):
        super().setUp()
        self.media = self.tmp / "media"
        self.media.mkdir()
        self._p2 = [
            mock.patch.object(esde_settings, "media_root", lambda: self.media),
            mock.patch.object(mad_paths, "saves_root", lambda: self.tmp / "saves"),
            mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: None),
            mock.patch.object(es_gamelist, "media_for", lambda s, st, kinds=None: {}),
        ]
        for p in self._p2:
            p.start()

    def tearDown(self):
        for p in self._p2:
            p.stop()
        super().tearDown()

    def test_openbor_rom_group_carries_whole_set(self):
        groups = {g["key"]: g for g in game_files.resolve_game_assets("openbor", "CARNAGE")}
        rels = {f["rel"] for f in groups["rom"]["files"]}
        self.assertEqual(rels, {"roms/openbor/CARNAGE.openbor", "roms/openbor/CARNAGE/game.exe",
                                "roms/openbor/CARNAGE/Paks"})
        # the ROM size counts the REAL payload, strictly more than the pointer manifest alone
        pointer_size = os.path.getsize(self.roms / "openbor" / "CARNAGE.openbor")
        self.assertGreater(groups["rom"]["size"], pointer_size)

    def test_openbor_save_is_its_own_asset_in_roms_category(self):
        groups = {g["key"]: g for g in game_files.resolve_game_assets("openbor", "CARNAGE")}
        self.assertIn("saves", groups)
        save = groups["saves"]
        self.assertTrue(save["present"])
        self.assertEqual(save["category"], "roms")
        self.assertEqual(save["files"][0]["rel"], "roms/openbor/CARNAGE/Saves")

    def test_cover_falls_back_to_miximage(self):
        mix = str(self.media / "mugen" / "miximages" / "AvX_game.png")

        def _media_for(system, stem, kinds=None):
            # a game scraped with a miximage but NO cover
            avail = {"covers": None, "miximages": mix, "screenshots": None, "titlescreens": None}
            return {k: (avail.get(k) if (kinds is None or k in kinds) else None) for k in avail}

        with mock.patch.object(es_gamelist, "media_for", _media_for):
            self.assertEqual(game_files.cover_path("mugen", "AvX_game"), mix)


class RoundTrip(_Base):
    def setUp(self):
        super().setUp()
        self._p2 = [
            mock.patch.object(esde_settings, "media_root", lambda: self.tmp / "media"),
            mock.patch.object(mad_paths, "saves_root", lambda: self.tmp / "saves"),
            mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: None),
            mock.patch.object(es_gamelist, "media_for", lambda s, st, kinds=None: {}),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
        ]
        for p in self._p2:
            p.start()

    def tearDown(self):
        for p in self._p2:
            p.stop()
        super().tearDown()

    def test_openbor_backup_captures_payload_and_restores_with_rule5(self):
        dest = self.tmp / "backup"
        dest.mkdir()
        games = [{"system": "openbor", "stem": "CARNAGE", "keys": ["rom", "saves"]}]
        gb.backup_game_assets(games, str(dest), "20260729T000000", lambda e: None, lambda: False)
        # locate the created backup dir + prove the REAL payload (not just the pointer) was copied
        bdir = next(d for d in dest.glob("deck-granular*") if d.is_dir())
        self.assertTrue((bdir / "roms/openbor/CARNAGE/game.exe").exists(), "the game exe was backed up")
        self.assertTrue((bdir / "roms/openbor/CARNAGE.openbor").exists(), "the launcher was backed up")
        self.assertTrue((bdir / "roms/openbor/CARNAGE/Saves/slot1.save").exists(), "the save was backed up")
        self.assertFalse((bdir / "roms/openbor/CARNAGE/Logs").exists(), "regenerable Logs excluded")

        # corrupt the live game, then restore -> original bytes back + a rule-5 snapshot of the corrupt copy
        live_exe = self.roms / "openbor" / "CARNAGE" / "game.exe"
        live_exe.write_bytes(b"CORRUPT")
        summary = gb.restore_game_assets(str(bdir), games, "20260729T010000", lambda e: None, lambda: False)
        self.assertEqual(live_exe.read_bytes(), b"OPENBOR-EXE", "the game exe was restored")
        self.assertTrue(summary["snapshots"], "rule #5: a snapshot dir was made")
        self.assertTrue(any(_find_bytes(Path(s), b"CORRUPT") for s in summary["snapshots"]),
                        "the corrupt live copy is recoverable from the snapshot")


class BrowseSize(_Base):
    """_live_roms_items size: launcher ROMs expand (real size), folder-per-game ROMs DEFER to 0 (no os.walk
    in the browse = no timeout regression), plain file ROMs report their own size."""

    def _rows(self, system):
        recs = {k: {"stem": Path(v).stem, "name": k} for k, v in self.rom_paths.get(system, {}).items()}
        with mock.patch.object(es_gamelist, "visible_records", lambda s: recs), \
             mock.patch.object(esde_settings, "media_root", lambda: self.tmp / "media"):
            return {r["stem"]: r for r in g._live_roms_items(system)}

    def test_folder_per_game_size_is_deferred_to_zero(self):
        row = self._rows("daphnex")["Game"]
        self.assertEqual(row["kind"], "folder")
        self.assertEqual(row["size"], 0, "a folder-per-game ROM must not be os.walk'd in the browse list")

    def test_launcher_rom_size_is_the_real_payload(self):
        pointer = os.path.getsize(self.roms / "openbor" / "CARNAGE.openbor")
        self.assertGreater(self._rows("openbor")["CARNAGE"]["size"], pointer,
                           "a launcher ROM expands past the pointer to its real payload size")

    def test_plain_file_rom_size_is_its_own_size(self):
        self.assertEqual(self._rows("nes")["smb"]["size"], len(b"NESROM"))


class MediaFallback(unittest.TestCase):
    """media_for's fake-extension fallback resolves lindbergh/openbor media (named after the FULL rom
    filename) WITHOUT stealing a real sibling game's media."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="media-"))
        self.media = self.tmp / "media"
        (self.media / "lindbergh" / "covers").mkdir(parents=True)
        (self.media / "lindbergh" / "covers" / "rambo.lindbergh.png").write_bytes(b"IMG")
        (self.media / "nes" / "covers").mkdir(parents=True)
        (self.media / "nes" / "covers" / "Doom.2.png").write_bytes(b"IMG")   # belongs to the game 'Doom.2'
        self._rp = {"lindbergh": {"rambo": "./rambo.lindbergh"},
                    "nes": {"doom": "./Doom.nes", "doom.2": "./Doom.2.nes"}}
        self._p = [
            mock.patch.object(esde_settings, "media_root", lambda: self.media),
            mock.patch.object(es_gamelist, "rom_paths", lambda s: self._rp.get(s, {})),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fake_extension_media_resolves(self):
        # media 'rambo.lindbergh.png' with rom ext '.lindbergh' -> resolves for game 'rambo'
        self.assertEqual(es_gamelist.media_for("lindbergh", "rambo", kinds=("covers",))["covers"],
                         str(self.media / "lindbergh" / "covers" / "rambo.lindbergh.png"))

    def test_sibling_media_not_misattributed(self):
        # game 'Doom' (rom .nes, no cover) must NOT be given the sibling 'Doom.2' game's 'Doom.2.png'
        self.assertIsNone(es_gamelist.media_for("nes", "Doom", kinds=("covers",))["covers"])
        # ...but the real owner 'Doom.2' still resolves it (exact match)
        self.assertEqual(es_gamelist.media_for("nes", "Doom.2", kinds=("covers",))["covers"],
                         str(self.media / "nes" / "covers" / "Doom.2.png"))


def _find_bytes(root: Path, needle: bytes) -> bool:
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            try:
                if needle in (Path(dp) / f).read_bytes():
                    return True
            except OSError:
                pass
    return False


if __name__ == "__main__":
    unittest.main()
