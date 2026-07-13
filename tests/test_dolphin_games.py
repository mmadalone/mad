"""Per-game browser listing (dolphin_games) + the ROM->GameID resolver (dolphin_gameids).

Verifies: the .games payload (titleid/name/stem/override/hide); dynamic AR/Gecko hide; name from the
ES-DE gamelist with a stem fallback; dedup by GameID; and the resolver's path+mtime cache (dolphin-tool
runs at most once per ROM) with a graceful skip when the tool can't resolve.

Run:  python3 -m unittest tests.test_dolphin_games -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import dolphin_gameids as gids
from lib.madsrv import dolphin_codes_cmds as codes
from lib.madsrv import dolphin_games as dg


class RomExtensions(unittest.TestCase):
    """The per-game browser lists WiiWare .wad titles (e.g. Retro City Rampage DX), not just discs."""
    def test_wad_wiiware_is_listed(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "wii").mkdir()
        (tmp / "wii" / "disc.rvz").write_text("x")
        (tmp / "wii" / "Retro City Rampage DX (Europe) (WiiWare).wad").write_text("x")
        (tmp / "wii" / "cover.png").write_text("x")          # a non-game file must stay filtered out
        _o = dg._rom_root
        dg._rom_root = lambda: tmp
        try:
            names = {p.name for p in dg._roms("wii")}
        finally:
            dg._rom_root = _o
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("Retro City Rampage DX (Europe) (WiiWare).wad", names)   # WiiWare now included
        self.assertIn("disc.rvz", names)
        self.assertNotIn("cover.png", names)

    def test_wad_in_accepted_extensions(self):
        self.assertIn(".wad", gids.EXTS)


class Listing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save = (dg._roms, gids.gameids, dg.es_gamelist.titles, codes.has_codes, gids.user_ini)
        dg._roms = lambda system: [Path("/roms/gc/Melee.rvz"), Path("/roms/gc/F-Zero GX.rvz")]
        gids.gameids = lambda roms: {"/roms/gc/Melee.rvz": "GALE01", "/roms/gc/F-Zero GX.rvz": "GFZE01"}
        dg.es_gamelist.titles = lambda system: {"melee": "Super Smash Bros. Melee"}
        codes.has_codes = lambda gid, sec: gid == "GALE01"            # only Melee has codes
        gids.user_ini = lambda gid: self.tmp / f"{gid}.ini"
        (self.tmp / "GALE01.ini").write_text("[Video_Settings]\nInternalResolution = 2\n")

    def tearDown(self):
        (dg._roms, gids.gameids, dg.es_gamelist.titles, codes.has_codes, gids.user_ini) = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_payload(self):
        r = dg._listing("gc")
        self.assertEqual(r["system"], "gc")
        by = {g["titleid"]: g for g in r["games"]}
        self.assertEqual(by["GALE01"]["name"], "Super Smash Bros. Melee")   # from the gamelist
        self.assertEqual(by["GFZE01"]["name"], "F-Zero GX")                 # stem fallback
        self.assertEqual(by["GALE01"]["stem"], "Melee")
        self.assertTrue(by["GALE01"]["override"])                          # has a user ini
        self.assertFalse(by["GFZE01"]["override"])
        self.assertNotIn("hide", by["GALE01"])                            # has codes -> both leaves shown
        self.assertEqual(set(by["GFZE01"]["hide"]), {"dolphin_ar", "dolphin_gecko"})   # no codes -> hidden

    def test_dedup_by_gameid(self):
        gids.gameids = lambda roms: {"/roms/gc/Melee.rvz": "GALE01",
                                     "/roms/gc/F-Zero GX.rvz": "GALE01"}   # same id twice
        r = dg._listing("gc")
        self.assertEqual([g["titleid"] for g in r["games"]], ["GALE01"])   # one entry

    def test_emptied_file_no_stale_badge(self):
        # a per-game file reduced to a bare section header (all overrides set back to Inherit) must
        # NOT keep the 'Custom settings' badge.
        (self.tmp / "GALE01.ini").write_text("[Video_Settings]\n")
        by = {g["titleid"]: g for g in dg._listing("gc")["games"]}
        self.assertFalse(by["GALE01"]["override"])

    def test_unresolvable_skipped(self):
        gids.gameids = lambda roms: {"/roms/gc/Melee.rvz": "GALE01", "/roms/gc/F-Zero GX.rvz": None}
        r = dg._listing("gc")
        self.assertEqual([g["titleid"] for g in r["games"]], ["GALE01"])


class Resolver(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save = (gids._CACHE, gids._cache, gids._tool_gameid)
        gids._CACHE = self.tmp / "cache.json"
        gids._cache = None
        self.calls = []
        gids._tool_gameid = lambda p: (self.calls.append(str(p)) or "GXXE01")

    def tearDown(self):
        gids._CACHE, gids._cache, gids._tool_gameid = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cache_resolves_once(self):
        rom = self.tmp / "x.rvz"
        rom.write_text("x")
        self.assertEqual(gids.gameid(rom), "GXXE01")
        self.assertEqual(gids.gameid(rom), "GXXE01")                       # 2nd call cached
        self.assertEqual(len(self.calls), 1)                              # tool ran once
        # a fresh process (cold _cache) still reads the persisted JSON, no tool call
        gids._cache = None
        self.calls.clear()
        self.assertEqual(gids.gameid(rom), "GXXE01")
        self.assertEqual(self.calls, [])

    def test_user_gs_is_dolphin_data_dir(self):
        # regression: per-game GameSettings is Dolphin's DATA user dir, NOT config/ (which Dolphin
        # does not read). Getting this wrong made every per-game page read a stale/foreign copy.
        s = str(gids._USER_GS)
        self.assertIn("data/dolphin-emu/GameSettings", s)
        self.assertNotIn("config/dolphin-emu", s)

    def test_unresolvable_returns_none(self):
        gids._tool_gameid = lambda p: None
        rom = self.tmp / "bad.rvz"
        rom.write_text("x")
        self.assertIsNone(gids.gameid(rom))

    def test_batch_mixed(self):
        good = self.tmp / "g.rvz"
        good.write_text("x")
        missing = self.tmp / "nope.rvz"                                   # does not exist
        out = gids.gameids([good, missing])
        self.assertEqual(out[str(good)], "GXXE01")
        self.assertIsNone(out[str(missing)])


if __name__ == "__main__":
    unittest.main()
