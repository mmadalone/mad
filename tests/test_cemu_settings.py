"""cemu_settings - global settings.xml pages: byte-preserving single-element edits, enum ENUM-CODE
vs index, bool true/false, int clamp, section isolation (<api> under Graphic vs Audio), pinned Audio
Cubeb, and version-safety (a key absent from the file is not offered / not created)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cemu_settings as cs
from lib.madsrv import rpc

_SETTINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<content>
    <fullscreen>true</fullscreen>
    <check_update>false</check_update>
    <console_language>1</console_language>
    <Graphic>
        <api>1</api>
        <VSync>2</VSync>
        <AsyncCompile>true</AsyncCompile>
        <UpscaleFilter>2</UpscaleFilter>
        <Overlay>
            <Position>0</Position>
            <TextScale>100</TextScale>
            <FPS>true</FPS>
        </Overlay>
        <Notification>
            <Position>1</Position>
            <ControllerProfiles>true</ControllerProfiles>
        </Notification>
    </Graphic>
    <Audio>
        <api>3</api>
        <TVVolume>100</TVVolume>
        <TVChannels>1</TVChannels>
    </Audio>
</content>
"""


class CemuSettings(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.f = self.d / "settings.xml"
        self.f.write_text(_SETTINGS, encoding="utf-8")
        self._file = cs._FILE
        cs._FILE = self.f
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        cs._FILE = self._file
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _rows(self, ns):
        return {s["key"]: s for g in rpc._METHODS[f"{ns}.get"][0]({})["groups"] for s in g["settings"]}

    def _set(self, ns, key, value):
        return rpc._METHODS[f"{ns}.set"][0]({"key": key, "value": value})

    def test_all_pages_registered(self):
        for ns in cs.PAGES:
            self.assertIn(f"{ns}.get", rpc._METHODS)
            self.assertIn(f"{ns}.set", rpc._METHODS)

    def test_enum_reads_code_and_bool(self):
        r = self._rows("cemu_gfx")
        self.assertEqual(r["graphic_api"]["options"], ["OpenGL", "Vulkan"])
        self.assertEqual(r["graphic_api"]["value"], 1)             # code 1 = Vulkan
        self.assertEqual(r["UpscaleFilter"]["value"], 2)           # BicubicHermite
        self.assertTrue(r["AsyncCompile"]["value"])

    def test_section_isolation_api(self):
        # <api> exists under BOTH Graphic and Audio; each page reads its own.
        self.assertEqual(self._rows("cemu_gfx")["graphic_api"]["value"], 1)
        a = self._rows("cemu_audio")["audio_api"]
        self.assertEqual(a["options"], ["Cubeb"])
        self.assertEqual(a["value"], 0)                            # stored "3" -> the only option

    def test_overlay_vs_notification_position(self):
        self.assertEqual(self._rows("cemu_overlay")["Position"]["value"], 0)   # Disabled
        self.assertEqual(self._rows("cemu_notif")["Position"]["value"], 1)     # Top left

    def test_byte_preserving_single_edit(self):
        before = self.f.read_text()
        self._set("cemu_gfx", "VSync", 0)
        after = self.f.read_text()
        self.assertEqual(after, before.replace("<VSync>2</VSync>", "<VSync>0</VSync>"))
        self.assertEqual(self._rows("cemu_gfx")["VSync"]["value"], 0)

    def test_int_clamped(self):
        self._set("cemu_audio", "TVVolume", 250)                   # max 100
        self.assertIn("<TVVolume>100</TVVolume>", self.f.read_text())
        self.assertEqual(self._rows("cemu_audio")["TVVolume"]["value"], 100)

    def test_version_safe_absent_key_not_offered(self):
        # feral_gamemode / fullscreen_menubar are not in the fixture -> not offered, never created.
        self.assertNotIn("feral_gamemode", self._rows("cemu_general"))
        with self.assertRaises(rpc.RpcError):
            self._set("cemu_general", "feral_gamemode", 1)
        self.assertNotIn("feral_gamemode", self.f.read_text())

    def test_pinned_audio_api_stays_cubeb(self):
        self._set("cemu_audio", "audio_api", 0)
        self.assertIn("<api>3</api>", self.f.read_text())          # Cubeb code, not 0


if __name__ == "__main__":
    unittest.main()
