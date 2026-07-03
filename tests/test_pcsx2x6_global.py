"""pcsx2x6 Global settings page: per-member (x6a_/x6r_), default-when-absent + create-on-write
for [UI] EnableMouseMapping, immediate writes, EBUSY guard."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil
from lib.madsrv import pcsx2x6_global_cmds as gc
from lib.madsrv import rpc

# Present: SDL keys + multitap. ABSENT: [UI] EnableMouseMapping (the [UI] section exists but not the key).
FIX = ("[InputSources]\nSDL = true\nSDLControllerEnhancedMode = true\nSDLPS5PlayerLED = true\n\n"
       "[Pad]\nMultitapPort1 = false\nMultitapPort2 = false\n\n[UI]\nMainWindowGeometry = x\n")


class Global(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.a = self.d / "arcade.ini"
        self.a.write_text(FIX, newline="")
        self._orig = gc._INIS["x6a"]
        gc._INIS["x6a"] = self.a
        self._run = gc._running
        gc._running = lambda: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        gc._INIS["x6a"] = self._orig
        gc._running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _rows(self):
        return {s["key"]: s for g in gc._get(self.a)["groups"] for s in g["settings"]}

    def _disk(self, sec, key):
        return cfgutil.ini_read(self.a.read_text(newline=""), sec, key)

    def test_registered_both_members(self):
        for pfx in ("x6a", "x6r"):
            for v in ("get", "set"):
                self.assertIn(f"{pfx}_global.{v}", rpc._METHODS)

    def test_page_reports_exists_true(self):
        # GuiMadPageEmuSettings renders an EMPTY page unless the payload's "exists" is True (the
        # documented Eden bug); the Global settings page must always report it.
        self.assertTrue(gc._get(self.a).get("exists"))

    def test_set_bumps_config_rev(self):
        # a write MUST bump staterev "config" or the page keeps serving the pre-write value.
        import lib.staterev as sr
        bumps = []
        sr.bump = lambda n: bumps.append(n)          # tearDown restores the original
        gc._set(self.a, {"key": "InputSources/SDL", "value": "0"})
        self.assertIn("config", bumps)

    def test_absent_mousemap_renders_default_off(self):
        rows = self._rows()
        self.assertIn("UI/EnableMouseMapping", rows)
        self.assertFalse(rows["UI/EnableMouseMapping"]["value"])   # absent -> default off
        self.assertTrue(rows["InputSources/SDL"]["value"])         # present true
        # the page has exactly the 6 expected bools
        self.assertEqual(set(rows), {"InputSources/SDL", "InputSources/SDLControllerEnhancedMode",
                                     "InputSources/SDLPS5PlayerLED", "UI/EnableMouseMapping",
                                     "Pad/MultitapPort1", "Pad/MultitapPort2"})

    def test_set_creates_absent_key(self):
        res = gc._set(self.a, {"key": "UI/EnableMouseMapping", "value": "1"})
        self.assertTrue(res["value"])
        self.assertEqual(self._disk("UI", "EnableMouseMapping"), "true")

    def test_set_creates_absent_section(self):
        # a fixture with no [UI] section at all -> _set must create it
        self.a.write_text("[Pad]\nMultitapPort1 = false\nMultitapPort2 = false\n", newline="")
        gc._set(self.a, {"key": "UI/EnableMouseMapping", "value": "1"})
        self.assertEqual(self._disk("UI", "EnableMouseMapping"), "true")

    def test_multitap_writes_pad_section(self):
        gc._set(self.a, {"key": "Pad/MultitapPort1", "value": "1"})
        self.assertEqual(self._disk("Pad", "MultitapPort1"), "true")
        gc._set(self.a, {"key": "Pad/MultitapPort1", "value": "0"})
        self.assertEqual(self._disk("Pad", "MultitapPort1"), "false")

    def test_ebusy_guard(self):
        gc._running = lambda: True
        with self.assertRaises(rpc.RpcError):
            gc._set(self.a, {"key": "InputSources/SDL", "value": "0"})

    def test_reject_unknown_key(self):
        with self.assertRaises(rpc.RpcError):
            gc._set(self.a, {"key": "Foo/Bar", "value": "1"})


if __name__ == "__main__":
    unittest.main()
