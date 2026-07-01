"""Tests for the standard-PCSX2 per-game settings page (pcsx2pg.*) + its cache/WS helpers.

Covers: RPC registration + PS2-tile gating (independent of the retail-GunCon2 gate); the
GLCE gamelist.cache parser (good, truncated, bad); per-game get/set with the presence =
override / "Inherit global" = clear model; the repeatable [Patches] Enable helpers; the
widescreen-patch zip index + on-disk precedence + graceful degradation; and the running
guard. The global PCSX2.ini is never written (the module has no global write path).

Run:  python3 -m unittest tests.test_pcsx2_pergame -v
"""
from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from lib.madsrv import pcsx2_games, rpc, standalones_cmds
from lib.madsrv import pcsx2_pergame_cmds as pg

ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2")
TID = "SLUS-21665_BBE4D862"


# ── GLCE blob builder (mirrors PCSX2 GameList.cpp WriteEntryToCache) ──────────
def _s(x: str) -> bytes:
    b = x.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def _entry(path, serial, title, crc, *, title_sort="", title_en="",
           typ=0, region=0, size=0, mtime=0, compat=0) -> bytes:
    return (_s(path) + _s(serial) + _s(title) + _s(title_sort) + _s(title_en)
            + struct.pack("<BB", typ, region) + struct.pack("<Q", size)
            + struct.pack("<Q", mtime) + struct.pack("<I", crc) + struct.pack("<B", compat))


def _blob(entries, *, magic=b"GLCE", version=34) -> bytes:
    return magic + struct.pack("<I", version) + b"".join(entries)


def _write(tmp: Path, data: bytes) -> Path:
    p = tmp / "gamelist.cache"
    p.write_bytes(data)
    return p


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("pcsx2pg.get", "pcsx2pg.set", "pcsx2pg.games"):
            self.assertIn(m, rpc._METHODS, m)

    def test_pergame_section_on_ps2_tile_independent_of_retail_gate(self):
        orig = standalones_cmds._pcsx2x6_has_guncon2_retail
        try:
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: False
            off = [(s["kind"], s.get("arg")) for s in standalones_cmds._sections_for(ENTRY)]
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: True
            on = [(s["kind"], s.get("arg")) for s in standalones_cmds._sections_for(ENTRY)]
        finally:
            standalones_cmds._pcsx2x6_has_guncon2_retail = orig
        self.assertIn(("settings_pergame", "pcsx2pg"), off)   # present regardless of the retail gate
        self.assertIn(("settings_pergame", "pcsx2pg"), on)
        # distinct namespace from the global Settings page (arg "pcsx2"), so no collision
        self.assertIn(("settings", "pcsx2"), off)


class CacheParse(unittest.TestCase):
    def test_parse_basic(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), _blob([
                _entry("/roms/ps2/Simpsons Game The (USA).chd", "SLUS-21665",
                       "The Simpsons Game", 0xBBE4D862, title_sort="Simpsons Game, The"),
                _entry("/roms/ps2/God Hand (Europe).iso", "SLES-54970", "God Hand", 0x0EE5646B),
            ]))
            got = pcsx2_games.parse_cache(p)
        self.assertEqual([(e["serial"], e["key"]) for e in got],
                         [("SLUS-21665", "SLUS-21665_BBE4D862"),
                          ("SLES-54970", "SLES-54970_0EE5646B")])
        self.assertEqual(got[0]["crc"], 0xBBE4D862)

    def test_title_en_preferred_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), _blob([
                _entry("/x.chd", "SLPM-65001", "ベルセルク", 0x11112222, title_en="Berserk")]))
            with mock.patch.object(pcsx2_games, "cache_path", lambda: p):
                self.assertEqual(pcsx2_games.games()[0]["name"], "Berserk")

    def test_truncated_returns_leading_good(self):
        with tempfile.TemporaryDirectory() as d:
            full = _blob([_entry("/a.iso", "SLUS-20001", "A", 0xAAAA0001),
                          _entry("/b.iso", "SLUS-20002", "B", 0xAAAA0002)])
            p = _write(Path(d), full[:-6])                    # chop the 2nd entry's tail
            got = pcsx2_games.parse_cache(p)
        self.assertEqual([e["serial"] for e in got], ["SLUS-20001"])

    def test_bad_magic_and_version(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pcsx2_games.parse_cache(
                _write(Path(d), _blob([_entry("/a", "S", "A", 1)], magic=b"XXXX"))), [])
            self.assertEqual(pcsx2_games.parse_cache(
                _write(Path(d), _blob([_entry("/a", "S", "A", 1)], version=99))), [])

    def test_dedup_by_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), _blob([
                _entry("/a.iso", "SLUS-20001", "A", 0xAAAA0001),
                _entry("/a-copy.iso", "SLUS-20001", "A", 0xAAAA0001)]))
            with mock.patch.object(pcsx2_games, "cache_path", lambda: p):
                self.assertEqual(len(pcsx2_games.games()), 1)


class PerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._orig_gs = pg._GS_DIR
        pg._GS_DIR = self.d

    def tearDown(self):
        pg._GS_DIR = self._orig_gs

    def _get(self, tid, ws=False):
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False), \
             mock.patch.object(pg.pcsx2_games, "has_widescreen", lambda s, c: ws):
            return pg._pergame_get(tid)

    def _set(self, **params):
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False):
            return pg._pergame_set(params)

    def _keys(self, r):
        return [s["key"] for g in r["groups"] for s in g["settings"]]

    def test_get_missing_file_all_inherit(self):
        r = self._get(TID, ws=False)
        # exists MUST be true even with no file: the C++ page renders nothing when
        # exists=false, and a missing override file is the normal first-use state.
        self.assertTrue(r["exists"])
        vals = [s["value"] for g in r["groups"] for s in g["settings"]]
        self.assertTrue(all(v == 0 for v in vals))            # every knob inherits
        self.assertIn("AspectRatio", self._keys(r))
        self.assertNotIn("WidescreenPatch", self._keys(r))    # no patch -> no toggle

    def test_ws_knob_only_when_patch_available(self):
        self.assertIn("WidescreenPatch", self._keys(self._get(TID, ws=True)))

    def test_set_aspect_creates_file_no_bak(self):
        r = self._set(titleid=TID, key="AspectRatio", value=3)   # 0=Inherit,1=Auto,2=4:3,3=16:9
        p = pg._pergame_path(TID)
        txt = p.read_text()
        self.assertIn("[EmuCore/GS]", txt)
        self.assertIn("AspectRatio = 16:9", txt)
        self.assertFalse(p.with_name(p.name + ".bak").exists())  # brand-new file -> no backup
        self.assertEqual(r["value"], 3)

    def test_override_then_clear_inherits(self):
        self._set(titleid=TID, key="AspectRatio", value=2)       # 4:3
        p = pg._pergame_path(TID)
        self.assertIn("AspectRatio = 4:3", p.read_text())
        self._set(titleid=TID, key="AspectRatio", value=0)       # Inherit global -> remove
        self.assertNotIn("AspectRatio", p.read_text())
        self.assertTrue(p.with_name(p.name + ".bak").exists())   # modified existing file -> .bak

    def test_bool_tristate(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="VsyncEnable", value=2)
        self.assertIn("VsyncEnable = true", p.read_text())
        self._set(titleid=TID, key="VsyncEnable", value=1)
        self.assertIn("VsyncEnable = false", p.read_text())
        self._set(titleid=TID, key="VsyncEnable", value=0)
        self.assertNotIn("VsyncEnable", p.read_text())

    def test_widescreen_patch_preserves_other_enables(self):
        p = pg._pergame_path(TID)
        p.write_text("[Patches]\nEnable = 50 FPS\n", encoding="utf-8")
        self._set(titleid=TID, key="WidescreenPatch", value=True)
        txt = p.read_text()
        self.assertIn("Enable = Widescreen 16:9", txt)
        self.assertIn("Enable = 50 FPS", txt)                    # other patch untouched
        self._set(titleid=TID, key="WidescreenPatch", value=False)
        txt = p.read_text()
        self.assertNotIn("Widescreen 16:9", txt)
        self.assertIn("Enable = 50 FPS", txt)

    def test_widescreen_patch_no_duplicate(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="WidescreenPatch", value=True)
        self._set(titleid=TID, key="WidescreenPatch", value=True)
        self.assertEqual(p.read_text().count("Widescreen 16:9"), 1)

    def test_running_guard(self):
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: True):
            with self.assertRaises(rpc.RpcError):
                pg._pergame_set({"titleid": TID, "key": "AspectRatio", "value": 3})

    def test_bad_titleid_rejected(self):
        for bad in ("../evil", "SLUS-21665", "SLUS-21665_ZZZZ", "x_00000000"):
            with self.assertRaises(rpc.RpcError):
                self._set(titleid=bad, key="AspectRatio", value=3)

    def test_get_bad_titleid_rejected(self):
        for bad in ("../../etc/passwd", "SLUS-21665", ""):
            with self.assertRaises(rpc.RpcError):
                self._get(bad)

    def test_games_marks_override(self):
        pg._pergame_path(TID).write_text("[EmuCore/GS]\nAspectRatio = 16:9\n", encoding="utf-8")
        # an emptied stub (all overrides cleared) must read as NOT custom, not "• custom"
        stub = "SLES-54970_0EE5646B"
        pg._pergame_path(stub).write_text("[EmuCore/GS]\n", encoding="utf-8")
        fake = [{"key": TID, "serial": "SLUS-21665", "crc": 0xBBE4D862, "name": "Simpsons", "path": "/x"},
                {"key": stub, "serial": "SLES-54970", "crc": 1, "name": "God Hand", "path": "/y"},
                {"key": "SLUS-20001_00000001", "serial": "SLUS-20001", "crc": 1, "name": "None", "path": "/z"}]
        with mock.patch.object(pg.pcsx2_games, "games", lambda: fake):
            out = {g["titleid"]: g["override"] for g in pg._pergame_games()["games"]}
        self.assertTrue(out[TID])                              # real override
        self.assertFalse(out[stub])                            # empty stub -> not custom
        self.assertFalse(out["SLUS-20001_00000001"])           # no file at all

    def test_clear_last_override_drops_custom_badge(self):
        self._set(titleid=TID, key="AspectRatio", value=3)     # create an override
        self.assertTrue(pg._has_overrides(pg._pergame_path(TID).read_text()))
        self._set(titleid=TID, key="AspectRatio", value=0)     # clear it (Inherit global)
        self.assertFalse(pg._has_overrides(pg._pergame_path(TID).read_text()))  # stub -> not custom

    def test_widescreen_crlf_file(self):
        p = pg._pergame_path(TID)
        p.write_text("[Patches]\r\nEnable = Widescreen 16:9\r\n", encoding="utf-8", newline="")
        # get must see the toggle as ON despite CRLF; toggling OFF must actually remove it
        self.assertTrue(pg._patches_has(p.read_text(), pg._WS_LABEL))
        self._set(titleid=TID, key="WidescreenPatch", value=False)
        self.assertFalse(pg._patches_has(p.read_text(), pg._WS_LABEL))

    def test_no_duplicate_section_on_bare_trailing_header(self):
        p = pg._pergame_path(TID)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[EmuCore/GS]\nAspectRatio = 4:3\n\n[Patches]", encoding="utf-8")  # no trailing \n
        self._set(titleid=TID, key="WidescreenPatch", value=True)
        txt = p.read_text()
        self.assertEqual(txt.count("[Patches]"), 1)            # reused the existing section
        self.assertIn("Enable = Widescreen 16:9", txt)


class WidescreenIndex(unittest.TestCase):
    def test_scan_zip(self):
        with tempfile.TemporaryDirectory() as d:
            zp = Path(d) / "patches.zip"
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("SLUS-20221_7FBCDA34.pnach", "gametitle=x\n[Widescreen 16:9]\ngsaspectratio=16:9\n")
                z.writestr("SLUS-11111_AAAAAAAA.pnach", "gametitle=y\n[60 FPS]\n")
                z.writestr("6D980D22.pnach", "[Widescreen 16:9]\n")            # CRC-only stem
                z.writestr("readme.txt", "ignored")
            self.assertEqual(pcsx2_games._scan_zip(zp),
                             {"SLUS-20221_7FBCDA34", "6D980D22"})

    def test_has_widescreen_ondisk_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "SLUS-20221_7FBCDA34.pnach").write_text("[Widescreen 16:9]\n")
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", dd):
                self.assertTrue(pcsx2_games.has_widescreen("SLUS-20221", "7FBCDA34"))
            # a present on-disk pnach WITHOUT the block -> False, never consults the zip
            (dd / "SLUS-20221_7FBCDA34.pnach").write_text("[60 FPS]\n")
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", dd), \
                 mock.patch.object(pcsx2_games, "ws_index", lambda: {"SLUS-20221_7FBCDA34"}):
                self.assertFalse(pcsx2_games.has_widescreen("SLUS-20221", "7FBCDA34"))

    def test_has_widescreen_from_index(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", Path(d)), \
                 mock.patch.object(pcsx2_games, "ws_index", lambda: {"6D980D22"}):
                self.assertTrue(pcsx2_games.has_widescreen("SLUS-99999", "6D980D22"))  # CRC-only
                self.assertFalse(pcsx2_games.has_widescreen("SLUS-99999", "DEADBEEF"))

    def test_has_widescreen_graceful_none(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", Path(d)), \
                 mock.patch.object(pcsx2_games, "ws_index", lambda: None):
                self.assertIsNone(pcsx2_games.has_widescreen("SLUS-99999", "DEADBEEF"))


if __name__ == "__main__":
    unittest.main()
