"""cemu_games - installed Wii U resolver: base-title filtering (drop update/DLC), lowercase title
ids, ES-DE/cache name fallback, ghost-drop (hide roms whose path is gone, keep-all when unmounted),
and the per-game override badge (gameProfiles/<tid>.ini exists)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cemu_games as cg
from lib.madsrv import rpc


def _cache(tmp: Path, entries: list) -> None:
    parts = ['<title_list_cache>']
    for tid, app, name, path in entries:
        parts.append(f'<title titleId="{tid}" app_type="{app}"><name>{name}</name>'
                     f'<path>{path}</path></title>')
    parts.append('</title_list_cache>')
    (tmp / "title_list_cache.xml").write_text("\n".join(parts), encoding="utf-8")


class CemuGames(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.data = self.d / "data"
        self.cfg = self.d / "config"
        (self.data).mkdir()
        (self.cfg / "gameProfiles").mkdir(parents=True)
        self._data, self._cfg = cg._DATA_DIR, cg._CONFIG_DIR
        cg._DATA_DIR, cg._CONFIG_DIR = self.data, self.cfg

    def tearDown(self):
        cg._DATA_DIR, cg._CONFIG_DIR = self._data, self._cfg
        shutil.rmtree(self.d, ignore_errors=True)

    def test_base_only_lowercased_and_named(self):
        one = self.d / "GameOne.wua"; one.write_bytes(b"x")
        two = self.d / "GameTwo.wux"; two.write_bytes(b"x")
        _cache(self.data, [
            ("0005000010111100", "80000000", "Game One", str(one)),          # base
            ("0005000E10111100", "0800000e", "Game One Update", str(one)),    # update -> dropped
            ("0005000C10111100", "0000000e", "Game One DLC", str(one)),       # dlc   -> dropped
            ("0005000010222200", "80000000", "Game Two", str(two)),           # base
        ])
        games = {g["titleid"]: g for g in cg.listing()}
        self.assertEqual(set(games), {"0005000010111100", "0005000010222200"})
        self.assertEqual(games["0005000010111100"]["name"], "Game One")     # cache name (no gamelist)

    def test_ghost_dropped_unless_all_missing(self):
        real = self.d / "Here.wua"; real.write_bytes(b"x")
        _cache(self.data, [
            ("0005000010111100", "80000000", "Here", str(real)),
            ("0005000010222200", "80000000", "Gone", str(self.d / "missing.wux")),
        ])
        ids = {g["titleid"] for g in cg.listing()}
        self.assertEqual(ids, {"0005000010111100"})                          # ghost hidden
        # all missing -> keep the whole list (library likely just unmounted)
        _cache(self.data, [
            ("0005000010111100", "80000000", "A", str(self.d / "a.wua")),
            ("0005000010222200", "80000000", "B", str(self.d / "b.wux")),
        ])
        self.assertEqual(len({g["titleid"] for g in cg.listing()}), 2)

    def test_override_badge_tracks_gameprofile(self):
        rom = self.d / "G.wua"; rom.write_bytes(b"x")
        _cache(self.data, [("0005000010111100", "80000000", "G", str(rom))])
        self.assertFalse(cg.listing()[0]["override"])
        cg.pergame_path("0005000010111100").write_text("[CPU]\r\ncpuMode = 4\r\n")
        self.assertTrue(cg.listing()[0]["override"])
        self.assertEqual(cg.pergame_path("0005000010111100").name, "0005000010111100.ini")

    def test_crlf_ini_roundtrip_helpers(self):
        p = self.cfg / "gameProfiles" / "x.ini"
        p.write_bytes(b"[CPU]\r\ncpuMode = 4\r\n")
        lf, crlf = cg.read_ini(p)
        self.assertTrue(crlf)
        self.assertNotIn("\r", lf)
        cg.write_ini(p, lf.replace("cpuMode = 4", "cpuMode = 3"), crlf)
        data = p.read_bytes()
        self.assertIn(b"cpuMode = 3\r\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))   # every LF is part of a CRLF

    def test_missing_cache_is_empty(self):
        self.assertEqual(cg.listing(), [])

    def test_games_badge_counts_enabled_packs(self):
        # A game customised by graphic packs ALONE (no gameProfiles ini) must still badge custom.
        from lib.madsrv import cemu_packs_cmds as cp
        r1 = self.d / "G.wua"; r1.write_bytes(b"x")
        r2 = self.d / "H.wua"; r2.write_bytes(b"x")
        _cache(self.data, [
            ("0005000010111100", "80000000", "Pack Only", str(r1)),
            ("0005000010222200", "80000000", "Nothing", str(r2)),
        ])
        orig = cp.enabled_titleids
        cp.enabled_titleids = lambda: {"0005000010111100"}
        try:
            games = {g["titleid"]: g for g in rpc._METHODS["cemu.games"][0]({})["games"]}
        finally:
            cp.enabled_titleids = orig
        self.assertTrue(games["0005000010111100"]["override"])          # pack-only -> custom
        self.assertIn("graphic packs", games["0005000010111100"]["summary"])
        self.assertFalse(games["0005000010222200"]["override"])         # neither -> not custom


if __name__ == "__main__":
    unittest.main()
