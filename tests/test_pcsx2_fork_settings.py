"""Tests for pcsx2_fork_settings — the Namco 246/256 Arcade + Retail GLOBAL settings
trees. Hermetic: a fixture ini is generated from the member's own descriptor set and
each Member is pointed at a temp copy. Locks: the per-tab Video split, the
OutputVolume->StandardVolume fork re-key, arcade/retail isolation, and the nested
Graphics{Video{tabs}, Emulation, OSD} sections tree."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil
from lib.madsrv import pcsx2_fork_settings as fs
from lib.madsrv import rpc

VIDEO_SUFFIXES = ["gfx_display", "gfx_hw", "gfx_sw", "gfx_hwfix", "gfx_upscale",
                  "gfx_texrepl", "gfx_post", "gfx_capture", "gfx_advgs"]
PAGE_SUFFIXES = VIDEO_SUFFIXES + ["emu", "osd", "aud", "adv"]


def _default(it):
    t = it["type"]
    if t == "bool":
        return "false"
    if t == "enum":
        return it["options_stored"][0] if it.get("write_mode") == "option" else "0"
    if t == "float":
        return str(it.get("min", 0))
    if t == "float_scaled":
        return "0"
    return str(it.get("min", 0))


def _fixture(member):
    sections = {}
    for _ns, (_title, groups) in member.categories.items():
        for g in groups:
            for it in g["items"]:
                if it["type"] == "clamp":
                    for k in it["clamp_keys"]:
                        sections.setdefault(it["section"], {}).setdefault(k, "false")
                else:
                    sec, key = it["section"], it.get("name", it["key"])
                    sections.setdefault(sec, {}).setdefault(key, _default(it))
    out = []
    for sec, kv in sections.items():
        out.append(f"[{sec}]")
        out.extend(f"{k} = {v}" for k, v in kv.items())
        out.append("")
    return "\n".join(out)


class ForkSettings(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.a = self.dir / "arcade.ini"
        self.a.write_text(_fixture(fs.ARCADE), newline="")
        self.r = self.dir / "retail.ini"
        self.r.write_text(_fixture(fs.RETAIL), newline="")
        self._a_ini, self._r_ini = fs.ARCADE.ini, fs.RETAIL.ini
        self._a_run, self._r_run = fs.ARCADE.running, fs.RETAIL.running
        fs.ARCADE.ini, fs.RETAIL.ini = self.a, self.r
        fs.ARCADE.running = fs.RETAIL.running = lambda: False
        fs.ARCADE.buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        fs.RETAIL.buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        fs.ARCADE.ini, fs.RETAIL.ini = self._a_ini, self._r_ini
        fs.ARCADE.running, fs.RETAIL.running = self._a_run, self._r_run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, member, suffix):
        pay = member.engine().get(f"{member.prefix}_{suffix}")
        return {s["key"]: s for g in pay["groups"] for s in g["settings"]}

    def test_all_namespaces_registered_both_members(self):
        for pfx in ("x6a", "x6r"):
            for suf in PAGE_SUFFIXES:
                for verb in ("get", "set", "save", "cancel"):
                    self.assertIn(f"{pfx}_{suf}.{verb}", rpc._METHODS)

    def test_pages_report_exists_true(self):
        # GuiMadPageEmuSettings renders EMPTY unless the payload's "exists" is True (the Eden bug).
        # Every Namco fork page (Graphics tabs / Emulation / OSD / Audio / Advanced) must report it.
        for member in (fs.ARCADE, fs.RETAIL):
            for suf in PAGE_SUFFIXES:
                pay = member.engine().get(f"{member.prefix}_{suf}")
                self.assertTrue(pay.get("exists"), f"{member.prefix}_{suf} must report exists:true")

    def test_audio_uses_standard_volume_not_output_volume(self):
        rows = self._rows(fs.ARCADE, "aud")
        self.assertIn("StandardVolume", rows)
        self.assertNotIn("OutputVolume", rows)

    def test_video_tabs_render_expected_pages(self):
        # Renderer & Display page has Renderer + AspectRatio; Texture Replacement page
        # has the load/dump toggles; Hardware Fixes has the UserHacks master enable.
        disp = self._rows(fs.ARCADE, "gfx_display")
        self.assertIn("Renderer", disp)
        self.assertIn("AspectRatio", disp)
        tex = self._rows(fs.ARCADE, "gfx_texrepl")
        self.assertIn("LoadTextureReplacements", tex)
        hwf = self._rows(fs.ARCADE, "gfx_hwfix")
        self.assertIn("UserHacks", hwf)

    def test_set_save_writes_only_that_member(self):
        # the hermetic fixture defaults the Renderer enum to options_stored[0] = '-1' (Automatic).
        e = fs.ARCADE.engine()
        e.get("x6a_gfx_display")
        e.set("x6a_gfx_display", {"key": "Renderer", "value": 2})   # OpenGL (12)
        self.assertEqual(cfgutil.ini_read(self.a.read_text(newline=""), "EmuCore/GS", "Renderer"), "-1")
        e.save("x6a_gfx_display")
        self.assertEqual(cfgutil.ini_read(self.a.read_text(newline=""), "EmuCore/GS", "Renderer"), "12")
        # retail ini untouched
        self.assertEqual(cfgutil.ini_read(self.r.read_text(newline=""), "EmuCore/GS", "Renderer"), "-1")

    def test_standard_volume_roundtrips(self):
        e = fs.RETAIL.engine()
        e.get("x6r_aud")
        e.set("x6r_aud", {"key": "StandardVolume", "value": 80})
        e.save("x6r_aud")
        self.assertEqual(cfgutil.ini_read(self.r.read_text(newline=""), "SPU2/Output", "StandardVolume"), "80")

    def test_sections_tree_shape(self):
        g = fs.graphics_group(fs.ARCADE, "Namco 246/256 (Arcade)")
        self.assertEqual(g["kind"], "group")
        labels = [s["label"] for s in g["sections"]]
        self.assertEqual(labels, ["Video", "Emulation", "On-Screen Display"])
        video = g["sections"][0]
        self.assertEqual(video["kind"], "group")
        vlabels = [s["label"] for s in video["sections"]]
        self.assertEqual(vlabels, ["Renderer & Display", "Rendering (Hardware)",
                                   "Rendering (Software)", "Hardware Fixes", "Upscaling Fixes",
                                   "Texture Replacement", "Post-Processing", "Media Capture",
                                   "Advanced (Graphics)"])
        # every video leaf is a settings row pointing at an x6a_gfx_* namespace
        for s in video["sections"]:
            self.assertEqual(s["kind"], "settings")
            self.assertTrue(s["arg"].startswith("x6a_gfx_"))

    def test_shared_descriptors_not_mutated(self):
        # the fork Audio re-key must not have changed the standard OutputVolume item
        from lib.madsrv import pcsx2_settings as ps
        vol = _by = None
        for g in ps.AUD_GROUPS:
            for it in g["items"]:
                if it["key"] in ("OutputVolume", "StandardVolume"):
                    vol = it["key"]
        self.assertEqual(vol, "OutputVolume")

    def test_fresh_engine_per_verb_threads_shared_buffer(self):
        # Mirror the REGISTERED-verb flow: a fresh BufferedEngine per call, sharing state ONLY via
        # Member.buf. If Member.engine() ever stopped passing self.buf, save would see no staged
        # edits and silently drop the write with no error (regression the other tests miss because
        # they reuse one engine object across get/set/save).
        fs.ARCADE.engine().get("x6a_gfx_display")
        fs.ARCADE.engine().set("x6a_gfx_display", {"key": "Renderer", "value": 2})   # OpenGL (12)
        self.assertEqual(cfgutil.ini_read(self.a.read_text(newline=""), "EmuCore/GS", "Renderer"), "-1")
        self.assertTrue(fs.ARCADE.engine().save("x6a_gfx_display")["saved"])
        self.assertEqual(cfgutil.ini_read(self.a.read_text(newline=""), "EmuCore/GS", "Renderer"), "12")


if __name__ == "__main__":
    unittest.main()
