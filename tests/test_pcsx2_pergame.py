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
        # The PS2 tile is a NESTED menu now: 4 top-level rows, group rows carry sub-sections.
        # Flatten (recurse into groups) to assert on the leaf (kind, arg) pairs.
        def flat(secs):
            out = []
            for s in secs:
                if s.get("kind") == "group":
                    out.extend(flat(s.get("sections", [])))
                else:
                    out.append((s["kind"], s.get("arg")))
            return out
        orig = standalones_cmds._pcsx2x6_has_guncon2_retail
        try:
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: False
            off = flat(standalones_cmds._sections_for(ENTRY))
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: True
            on = flat(standalones_cmds._sections_for(ENTRY))
        finally:
            standalones_cmds._pcsx2x6_has_guncon2_retail = orig
        self.assertIn(("settings_pergame", "pcsx2pg"), off)   # per-game Settings, in the Per-game group
        self.assertIn(("settings_pergame", "pcsx2pg"), on)
        # global settings are 5 category rows (pcsx2emu/gfx/osd/aud/adv) under the group menus,
        # all distinct from the per-game namespace (pcsx2pg); the old single ("settings","pcsx2") is gone.
        self.assertIn(("settings", "pcsx2gfx"), off)
        self.assertNotIn(("settings", "pcsx2"), off)


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
        pg._buf.update({"titleid": None, "text": None, "disk": None, "dirty": False, "edits": []})

    def tearDown(self):
        pg._GS_DIR = self._orig_gs

    def _get(self, tid, ws=False, patches=None):
        # mock patch_labels so tests are hermetic (no reaching into the real patches.zip).
        pl = patches or {}
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False), \
             mock.patch.object(pg.pcsx2_games, "has_widescreen", lambda s, c: ws), \
             mock.patch.object(pg.pcsx2_games, "patch_labels",
                               lambda s, c, kind="patches": pl.get(kind, [])):
            return pg._pergame_get(tid)

    def _set(self, **params):
        # per-game is buffered now: stage the edit then Save so the disk reflects it (the
        # tests assert on-disk state, which is what a real Save produces).
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False):
            r = pg._pergame_set(params)
            pg._pergame_save(params["titleid"])
            return r

    def _keys(self, r):
        return [s["key"] for g in r["groups"] for s in g["settings"]]

    def test_get_missing_file_all_inherit(self):
        r = self._get(TID, ws=False)
        # exists MUST be true even with no file: the C++ page renders nothing when
        # exists=false, and a missing override file is the normal first-use state.
        self.assertTrue(r["exists"])
        for g in r["groups"]:
            for s in g["settings"]:
                if s["type"] == "enum":
                    self.assertEqual(s["value"], 0, s["key"])          # Inherit global at index 0
                else:                                                  # int/float numeric
                    self.assertTrue(s.get("inherited"), s["key"])      # inherit slot
        self.assertIn("AspectRatio", self._keys(r))
        self.assertNotIn("WidescreenPatch", self._keys(r))    # no patch -> no toggle

    def test_patches_appear_as_toggles(self):
        keys = self._keys(self._get(TID, patches={"patches": ["Widescreen 16:9", "60 FPS"],
                                                  "cheats": ["Infinite Health"]}))
        self.assertIn("pt:Patches:Widescreen 16:9", keys)
        self.assertIn("pt:Patches:60 FPS", keys)
        self.assertIn("pt:Cheats:Infinite Health", keys)

    def test_no_patches_no_group(self):
        self.assertNotIn("pt:Patches:Widescreen 16:9", self._keys(self._get(TID)))

    def test_set_aspect_creates_file_no_bak(self):
        # AspectRatio now reuses pcsx2gfx's 5 options: 0=Inherit,1=Stretch,2=Auto,3=4:3,4=16:9,5=10:7
        r = self._set(titleid=TID, key="AspectRatio", value=4)   # 16:9
        p = pg._pergame_path(TID)
        txt = p.read_text()
        self.assertIn("[EmuCore/GS]", txt)
        self.assertIn("AspectRatio = 16:9", txt)
        self.assertFalse(p.with_name(p.name + ".bak").exists())  # brand-new file -> no backup
        self.assertEqual(r["value"], 4)

    def test_override_then_clear_inherits(self):
        self._set(titleid=TID, key="AspectRatio", value=3)       # 4:3 (index 3 in the 5-option list)
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

    def test_enum_unknown_token_appended_not_prepended(self):
        # A foreign out-of-curated on-disk token (e.g. a Windows Renderer code) must be
        # APPENDED at the end, so curated option indices don't shift. This structurally
        # removes the review's second-consecutive-edit off-by-one (prepend would shift them).
        p = pg._pergame_path(TID)
        p.write_text("[EmuCore/GS]\nRenderer = 15\n", encoding="utf-8")   # 15 not in ['-1','14','12','13']
        r = self._get(TID)
        ren = next(s for grp in r["groups"] for s in grp["settings"] if s["key"] == "Renderer")
        self.assertEqual(ren["options"][1:5], ["Automatic", "Vulkan", "OpenGL", "Software"])
        self.assertTrue(ren["options"][-1].startswith("(current: 15"))     # unknown appended LAST
        self.assertEqual(ren["value"], len(ren["options"]) - 1)
        # edit 1: OpenGL (index 3) -> "12"
        self._set(titleid=TID, key="Renderer", value=3)
        self.assertIn("Renderer = 12", p.read_text())
        # edit 2 with the index Vulkan held in the STALE 6-item list (2) -> still Vulkan now
        self._set(titleid=TID, key="Renderer", value=2)
        self.assertIn("Renderer = 14", p.read_text())

    def test_widescreen_patch_preserves_other_enables(self):
        p = pg._pergame_path(TID)
        p.write_text("[Patches]\nEnable = 50 FPS\n", encoding="utf-8")
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=True)
        txt = p.read_text()
        self.assertIn("Enable = Widescreen 16:9", txt)
        self.assertIn("Enable = 50 FPS", txt)                    # other patch untouched
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=False)
        txt = p.read_text()
        self.assertNotIn("Widescreen 16:9", txt)
        self.assertIn("Enable = 50 FPS", txt)

    def test_widescreen_patch_no_duplicate(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=True)
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=True)
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
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=False)
        self.assertFalse(pg._patches_has(p.read_text(), pg._WS_LABEL))

    def test_no_duplicate_section_on_bare_trailing_header(self):
        p = pg._pergame_path(TID)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[EmuCore/GS]\nAspectRatio = 4:3\n\n[Patches]", encoding="utf-8")  # no trailing \n
        self._set(titleid=TID, key="pt:Patches:Widescreen 16:9", value=True)
        txt = p.read_text()
        self.assertEqual(txt.count("[Patches]"), 1)            # reused the existing section
        self.assertIn("Enable = Widescreen 16:9", txt)

    # ── new per-game capabilities from the full-tree rework ──────────────────
    def test_numeric_inherit_row_shape(self):
        rows = {s["key"]: s for g in self._get(TID)["groups"] for s in g["settings"]}
        crop = rows["CropLeft"]                                 # an int setting (Graphics/Display)
        self.assertEqual(crop["type"], "int")
        self.assertTrue(crop["inherit"])
        self.assertTrue(crop["inherited"])                     # missing file -> inheriting

    def test_numeric_override_and_inherit_clear(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="CropLeft", value=10)
        self.assertIn("CropLeft = 10", p.read_text())
        crop = next(s for g in self._get(TID)["groups"] for s in g["settings"] if s["key"] == "CropLeft")
        self.assertFalse(crop["inherited"])
        self.assertEqual(crop["value"], 10)
        self._set(titleid=TID, key="CropLeft", value="inherit")   # the numeric inherit sentinel
        self.assertNotIn("CropLeft", p.read_text())

    def test_clamp_pergame_inherit_and_set(self):
        p = pg._pergame_path(TID)
        ee = next(s for g in self._get(TID)["groups"] for s in g["settings"] if s["key"] == "EEClampMode")
        self.assertEqual(ee["options"][0], "Inherit global")
        self.assertEqual(ee["value"], 0)                       # missing -> inherit
        self._set(titleid=TID, key="EEClampMode", value=2)     # Inherit=0, None=1, Normal=2 -> (T,F,F)
        txt = p.read_text()
        self.assertIn("fpuOverflow = true", txt)
        self.assertIn("fpuExtraOverflow = false", txt)
        self.assertIn("fpuFullMode = false", txt)
        self._set(titleid=TID, key="EEClampMode", value=0)     # inherit -> clear all 3 keys
        self.assertNotIn("fpuOverflow", p.read_text())

    def test_float_scaled_pergame(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="ExpandShift", value=-50)   # scaled-int -50 -> stored -0.5
        self.assertIn("ExpandShift = -0.5", p.read_text())
        self._set(titleid=TID, key="ExpandShift", value="inherit")
        self.assertNotIn("ExpandShift", p.read_text())

    def test_buffered_cancel_discards(self):
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False):
            pg._pergame_get(TID)
            pg._pergame_set({"titleid": TID, "key": "CropLeft", "value": 20})
            self.assertTrue(pg._buf["dirty"])
            pg._pergame_cancel(TID)
        self.assertFalse(pg._pergame_path(TID).exists())       # nothing written to disk

    def test_buffered_save_replays_multiple_edits(self):
        with mock.patch.object(pg.proc_guard, "emulator_running", lambda n: False):
            pg._pergame_get(TID)
            pg._pergame_set({"titleid": TID, "key": "CropLeft", "value": 5})
            pg._pergame_set({"titleid": TID, "key": "CropTop", "value": 7})
            pg._pergame_save(TID)
        txt = pg._pergame_path(TID).read_text()
        self.assertIn("CropLeft = 5", txt)
        self.assertIn("CropTop = 7", txt)

    def test_cheat_toggle_writes_cheats_section(self):
        p = pg._pergame_path(TID)
        self._set(titleid=TID, key="pt:Cheats:Infinite Health", value=True)
        txt = p.read_text()
        self.assertIn("[Cheats]", txt)
        self.assertIn("Enable = Infinite Health", txt)
        self.assertNotIn("[Patches]", txt)                 # cheats go to their OWN section
        self._set(titleid=TID, key="pt:Cheats:Infinite Health", value=False)
        self.assertNotIn("Infinite Health", p.read_text())

    def test_pergame_only_encodings(self):
        rows = {s["key"]: s for g in self._get(TID)["groups"] for s in g["settings"]}
        # HWDownloadMode: exactly the 4 real GSHardwareDownloadMode values (+ Inherit global at 0)
        self.assertEqual(rows["HWDownloadMode"]["options"],
                         ["Inherit global", "Accurate", "Disable Readbacks", "Unsynchronized", "Disabled"])
        # RtcYear is a 0-99 offset from 2000 (not an absolute year)
        self.assertEqual((rows["RtcYear"]["min"], rows["RtcYear"]["max"]), (0, 99))


class PatchLabels(unittest.TestCase):
    def _labels(self, serial, crc, kind, patches_dir=None, cheats_dir=None):
        op, oc = pcsx2_games._PATCHES_DIR, pcsx2_games._CHEATS_DIR
        try:
            if patches_dir is not None:
                pcsx2_games._PATCHES_DIR = patches_dir
            if cheats_dir is not None:
                pcsx2_games._CHEATS_DIR = cheats_dir
            with mock.patch.object(pcsx2_games, "_cached_patches_zip", lambda: None):  # disk-only, hermetic
                return pcsx2_games.patch_labels(serial, crc, kind)
        finally:
            pcsx2_games._PATCHES_DIR, pcsx2_games._CHEATS_DIR = op, oc

    def test_disk_labels_deduped_and_strip_comments(self):
        pd = Path(tempfile.mkdtemp()) / "patches"
        pd.mkdir()
        (pd / "SLUS-21665_BBE4D862.pnach").write_text(
            "gametitle=X\n[Widescreen 16:9] // note\npatch=1\n[60 FPS]\n// [ignored]\n[Widescreen 16:9]\n",
            encoding="utf-8")
        # inline `//` stripped from the header, whole-line `//` ignored, deduped, source order
        self.assertEqual(self._labels("SLUS-21665", "BBE4D862", "patches", patches_dir=pd),
                         ["Widescreen 16:9", "60 FPS"])

    def test_merges_both_disk_stems(self):
        pd = Path(tempfile.mkdtemp()) / "patches"
        pd.mkdir()
        (pd / "SLUS-21665_BBE4D862.pnach").write_text("[Widescreen 16:9]\n", encoding="utf-8")
        (pd / "BBE4D862.pnach").write_text("[60 FPS]\n[Widescreen 16:9]\n", encoding="utf-8")
        # PCSX2 merges both stems (serial then crc), de-duped first-wins — not shadowed
        self.assertEqual(self._labels("SLUS-21665", "BBE4D862", "patches", patches_dir=pd),
                         ["Widescreen 16:9", "60 FPS"])

    def test_cheats_not_bundled_returns_empty(self):
        self.assertEqual(self._labels("SLUS-99999", "00000000", "cheats",
                                      cheats_dir=Path(tempfile.mkdtemp()) / "cheats"), [])


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

    def test_has_widescreen_merges_disk_and_index(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "SLUS-20221_7FBCDA34.pnach").write_text("[Widescreen 16:9]\n")
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", dd):
                self.assertTrue(pcsx2_games.has_widescreen("SLUS-20221", "7FBCDA34"))   # disk WS
            # a disk pnach WITHOUT the block must NOT hide the bundled index (PCSX2 MERGES both)
            (dd / "SLUS-20221_7FBCDA34.pnach").write_text("[60 FPS]\n")
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", dd), \
                 mock.patch.object(pcsx2_games, "ws_index", lambda: {"SLUS-20221_7FBCDA34"}):
                self.assertTrue(pcsx2_games.has_widescreen("SLUS-20221", "7FBCDA34"))   # from index
            # neither disk nor index has it -> False
            with mock.patch.object(pcsx2_games, "_PATCHES_DIR", dd), \
                 mock.patch.object(pcsx2_games, "ws_index", lambda: set()):
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


class GamesRomPresence(unittest.TestCase):
    """games() hides a stale PCSX2-cache ghost (ROM deleted) but keeps the whole list when EVERY
    rom is missing (the library is probably just unmounted, not emptied)."""

    def _entries(self, tmp, present, missing):
        ents = []
        for nm in present:
            p = tmp / f"{nm}.chd"
            p.write_bytes(b"x")
            ents.append({"serial": f"SLES-000{len(ents)}", "crc": 1, "title": nm, "title_en": nm,
                         "region": 0, "path": str(p), "key": f"SLES-000{len(ents)}_00000001"})
        for nm in missing:
            ents.append({"serial": f"SLES-000{len(ents)}", "crc": 1, "title": nm, "title_en": nm,
                         "region": 0, "path": str(tmp / f"{nm}.chd"),  # never written -> missing
                         "key": f"SLES-000{len(ents)}_00000001"})
        return ents

    def test_missing_rom_hidden_present_kept(self):
        tmp = Path(tempfile.mkdtemp())
        ents = self._entries(tmp, ["Alive"], ["Police 24-7"])
        with mock.patch.object(pcsx2_games, "parse_cache", lambda p: ents), \
             mock.patch.object(pcsx2_games, "cache_path", lambda: tmp / "gamelist.cache"):
            names = [g["name"] for g in pcsx2_games.games()]
        self.assertIn("Alive", names)
        self.assertNotIn("Police 24-7", names)     # deleted ROM = stale ghost, hidden

    def test_all_missing_keeps_full_list(self):
        tmp = Path(tempfile.mkdtemp())
        ents = self._entries(tmp, [], ["G1", "G2"])
        with mock.patch.object(pcsx2_games, "parse_cache", lambda p: ents), \
             mock.patch.object(pcsx2_games, "cache_path", lambda: tmp / "gamelist.cache"):
            names = sorted(g["name"] for g in pcsx2_games.games())
        self.assertEqual(names, ["G1", "G2"])       # all missing => unmounted, don't blank


if __name__ == "__main__":
    unittest.main()
