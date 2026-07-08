"""cemu_packs - per-game graphic packs as Cemu-parity category pages: each pack = an Enabled toggle +
a dropdown per option group (rules.txt [Preset] category), the pack's real default pre-selected (no
synthetic entry). Buffered get/set/save/cancel over a working <GraphicPack> model; byte-preserving
edits that keep stored presets on toggle-off; category/hide + enabled_titleids."""
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cemu_games
from lib.madsrv import cemu_packs_cmds as cp
from lib.madsrv import rpc

_A = "0005000010111100"
_B = "0005000010222200"

_RES = ("\n[Preset]\nname = 720p\ncategory = Resolution\ndefault = 1\n"
        "\n[Preset]\nname = 1080p\ncategory = Resolution\n"
        "\n[Preset]\nname = 4K\ncategory = Resolution\n")
_GFXOPTS = ("\n[Preset]\nname = Off\ncategory = Anti-Aliasing\n"
            "\n[Preset]\nname = On\ncategory = Anti-Aliasing\ndefault = 1\n"
            "\n[Preset]\nname = Low\ncategory = Shadows\n"
            "\n[Preset]\nname = High\ncategory = Shadows\ndefault = 1\n")

# GameA_Res is enabled with Resolution=1080p (a non-default stored choice), to test display + preserve.
_SETTINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<content>
    <fullscreen>true</fullscreen>
    <GraphicPack>
        <Entry filename="graphicPacks/GameA_Res/rules.txt">
            <Preset>
                <category>Resolution</category>
                <preset>1080p</preset>
            </Preset>
        </Entry>
    </GraphicPack>
</content>
"""


def _pack(root: Path, folder: str, titleids: str, name: str, path: str, presets: str = "") -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / "rules.txt").write_text(
        f'[Definition]\ntitleIds = {titleids}\nname = {name}\npath = "{path}"\nversion = 6\n' + presets,
        encoding="utf-8")


class CemuPacks(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.data = self.d / "data"
        self.cfg = self.d / "config"
        (self.cfg / "gameProfiles").mkdir(parents=True)
        gp = self.data / "graphicPacks"
        gp.mkdir(parents=True)
        _pack(gp, "GameA_Res", _A, "Resolution", "Game A/Graphics/Resolution", _RES)
        _pack(gp, "GameA_GfxOpts", _A, "Graphic Options", "Game A/Graphics/Options", _GFXOPTS)
        _pack(gp, "GameA_FPS", _A, "FPS++", "Game A/Mods/FPS")                 # no options
        _pack(gp, "GameA_Cheat", _A, "Unlock All", "Game A/Cheats/Unlock")
        _pack(gp, "GameB", _B, "Widescreen", "Game B/Graphics")
        _pack(gp, "Universal", "*", "Downscaling", "Filters/Downscaling")      # universal -> excluded
        roms = self.d / "roms"; roms.mkdir()
        (roms / "A.wua").write_bytes(b"x"); (roms / "B.wua").write_bytes(b"x")
        (self.data / "title_list_cache.xml").write_text(
            f'<title_list_cache>'
            f'<title titleId="{_A}" app_type="80000000"><name>Game A</name><path>{roms/"A.wua"}</path></title>'
            f'<title titleId="{_B}" app_type="80000000"><name>Game B</name><path>{roms/"B.wua"}</path></title>'
            f'</title_list_cache>', encoding="utf-8")
        self.settings = self.cfg / "settings.xml"
        self.settings.write_text(_SETTINGS, encoding="utf-8")
        self._odata, self._ocfg, self._oset = cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS
        cemu_games._DATA_DIR = self.data
        cemu_games._CONFIG_DIR = self.cfg
        cp._SETTINGS = self.settings
        cp._BUF.update({"ctx": None, "disk": None, "entries": None})
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS = self._odata, self._ocfg, self._oset
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self, cat, tid=_A):
        return rpc._METHODS[f"cemu_packs_{cat}.get"][0]({"titleid": tid})

    def _set(self, cat, key, value, tid=_A):
        return rpc._METHODS[f"cemu_packs_{cat}.set"][0]({"titleid": tid, "key": key, "value": value})

    def _save(self, cat, tid=_A):
        return rpc._METHODS[f"cemu_packs_{cat}.save"][0]({"titleid": tid})

    def _cancel(self, cat, tid=_A):
        return rpc._METHODS[f"cemu_packs_{cat}.cancel"][0]({"titleid": tid})

    def _pack_group(self, cat, name, tid=_A):
        return next(g for g in self._get(cat, tid)["groups"] if g["title"] == name)

    def _rows(self, cat, name, tid=_A):
        return {s["label"]: s for s in self._pack_group(cat, name, tid)["settings"]}

    # ── structure ────────────────────────────────────────────────────────────────
    def test_all_namespaces_registered(self):
        for c in cp.CATEGORIES:
            for v in ("get", "set", "save", "cancel"):
                self.assertIn(f"cemu_packs_{cp.catkey(c)}.{v}", rpc._METHODS)

    def test_pack_group_has_enabled_plus_option_dropdowns(self):
        g = self._get("graphics")
        self.assertTrue(g["buffered"])
        titles = {grp["title"] for grp in g["groups"]}
        self.assertEqual(titles, {"Resolution", "Graphic Options"})   # GameA Graphics packs; universal excluded
        res = self._rows("graphics", "Resolution")
        self.assertEqual(res["Enabled"]["type"], "bool")
        self.assertTrue(res["Enabled"]["value"])                      # enabled in the fixture
        self.assertEqual(res["Resolution"]["type"], "enum")
        self.assertEqual(res["Resolution"]["options"], ["720p", "1080p", "4K"])   # no synthetic entry
        self.assertEqual(res["Resolution"]["value"], 1)               # stored 1080p

    def test_default_preselected_no_synthetic(self):
        opts = self._rows("graphics", "Graphic Options")              # not enabled, no stored presets
        self.assertEqual(opts["Anti-Aliasing"]["options"], ["Off", "On"])
        self.assertEqual(opts["Anti-Aliasing"]["value"], 1)           # default "On" pre-selected
        self.assertEqual(opts["Shadows"]["value"], 1)                 # default "High"
        self.assertFalse(opts["Enabled"]["value"])

    def test_simple_pack_is_toggle_only(self):
        rows = self._rows("mods", "FPS++")
        self.assertEqual(set(rows), {"Enabled"})                      # no option dropdowns

    # ── buffered edits ───────────────────────────────────────────────────────────
    def test_enable_stage_and_save(self):
        r = self._set("mods", "graphicPacks/GameA_FPS/rules.txt", True)
        self.assertTrue(r["dirty"])
        self.assertNotIn("GameA_FPS", self.settings.read_text())      # staged only
        self.assertTrue(self._save("mods")["saved"])
        self.assertIn('<Entry filename="graphicPacks/GameA_FPS/rules.txt"/>', self.settings.read_text())
        ET.fromstring(self.settings.read_text())

    def test_option_pick_writes_preset_and_default_clears(self):
        key = "graphicPacks/GameA_Res/rules.txt\x1fResolution"
        self._set("graphics", key, 2)                                 # 4K
        self._save("graphics")
        self.assertIn("<preset>4K</preset>", self.settings.read_text())
        self.assertNotIn("<preset>1080p</preset>", self.settings.read_text())
        self._set("graphics", key, 0)                                 # 720p = the default -> clears override
        self._save("graphics")
        text = self.settings.read_text()
        self.assertNotIn("<preset>4K</preset>", text)
        self.assertNotIn("<Preset>", text)                            # default => no <Preset> written
        self.assertIn('<Entry filename="graphicPacks/GameA_Res/rules.txt"/>', text)  # still enabled

    def test_disable_keeps_stored_options(self):
        self._set("graphics", "graphicPacks/GameA_Res/rules.txt", False)
        self._save("graphics")
        text = self.settings.read_text()
        self.assertIn('disabled="true"', text)
        self.assertIn("<preset>1080p</preset>", text)                 # stored option preserved

    def test_cancel_discards(self):
        self._set("mods", "graphicPacks/GameA_FPS/rules.txt", True)
        self.assertTrue(self._get("mods")["dirty"])
        self._cancel("mods")
        self.assertFalse(self._get("mods")["dirty"])
        self.assertFalse(self._rows("mods", "FPS++")["Enabled"]["value"])

    # ── byte-preservation ────────────────────────────────────────────────────────
    def test_repeated_saves_keep_indent_and_other_bytes(self):
        for _ in range(3):
            self._set("mods", "graphicPacks/GameA_FPS/rules.txt", True); self._save("mods")
            self._set("mods", "graphicPacks/GameA_FPS/rules.txt", False); self._save("mods")
        text = self.settings.read_text()
        self.assertEqual([ln for ln in text.splitlines() if "<GraphicPack>" in ln][0], "    <GraphicPack>")
        self.assertIn("<fullscreen>true</fullscreen>", text)
        self.assertEqual(text.count("</content>"), 1)

    def test_ampersand_pack_not_double_escaped(self):
        _pack(self.data / "graphicPacks", "Sonic & Knuckles", _A, "SK", "Game A/Mods/SK")
        self._set("mods", "graphicPacks/Sonic & Knuckles/rules.txt", True); self._save("mods")
        self.assertIn('filename="graphicPacks/Sonic &amp; Knuckles/rules.txt"', self.settings.read_text())
        self._set("mods", "graphicPacks/GameA_FPS/rules.txt", True); self._save("mods")
        self.assertNotIn("&amp;amp;", self.settings.read_text())

    # ── hide / badge / guards ────────────────────────────────────────────────────
    def test_applicable_categories_excludes_universal(self):
        cats = cp.applicable_categories()
        self.assertEqual(cats[_A], {"Graphics", "Mods", "Cheats"})
        self.assertEqual(cats[_B], {"Graphics"})

    def test_enabled_titleids_game_specific_only(self):
        self.assertEqual(cp.enabled_titleids(), {_A})
        self._set("graphics", "graphicPacks/GameA_Res/rules.txt", False); self._save("graphics")
        self.assertEqual(cp.enabled_titleids(), set())

    def test_bad_titleid_rejected(self):
        with self.assertRaises(rpc.RpcError):
            self._get("mods", tid="../etc/passwd")


if __name__ == "__main__":
    unittest.main()
