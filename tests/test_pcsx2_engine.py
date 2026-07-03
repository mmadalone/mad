"""Tests for pcsx2_engine.BufferedEngine — the reusable buffered settings engine
shared by standard PCSX2 and the two Namco 246/256 forks (arcade + retail). The
standard-PCSX2 behaviour is already covered exhaustively by test_pcsx2_settings
(which now drives this engine); here we lock the property the FORKS depend on:
two engine instances on different inis with their own buffers are fully isolated."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil
from lib.madsrv import pcsx2_engine as eng
from lib.madsrv.rpc import RpcError

CATS = {
    "tstgfx": ("Graphics", [
        {"title": "Renderer", "note": "", "items": [
            {"key": "Renderer", "label": "Renderer", "file": "PCSX2.ini",
             "section": "EmuCore/GS", "type": "enum", "write_mode": "option",
             "options_display": ["Automatic", "Vulkan"], "options_stored": ["-1", "14"]},
            {"key": "VsyncEnable", "label": "VSync", "file": "PCSX2.ini",
             "section": "EmuCore/GS", "type": "bool", "bool_true": "true", "bool_false": "false"},
        ]},
    ]),
}
FIX = "[EmuCore/GS]\nRenderer = 14\nVsyncEnable = false\n"


def _read(path, key):
    return cfgutil.ini_read(path.read_text(newline=""), "EmuCore/GS", key)


class Engine(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.a = self.dir / "a.ini"
        self.a.write_text(FIX, newline="")
        self.b = self.dir / "b.ini"
        self.b.write_text(FIX, newline="")
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.dir, ignore_errors=True)

    def _eng(self, path, running=lambda: False):
        return eng.BufferedEngine(path, running, CATS, eng.new_buf(), note_label="Test")

    def test_two_engines_do_not_cross_contaminate(self):
        ea, eb = self._eng(self.a), self._eng(self.b)
        ea.get("tstgfx")
        ea.set("tstgfx", {"key": "Renderer", "value": 0})  # index 0 -> stored '-1'
        ea.save("tstgfx")
        self.assertEqual(_read(self.a, "Renderer"), "-1")   # engine A wrote its own file
        self.assertEqual(_read(self.b, "Renderer"), "14")   # engine B's file untouched
        # engine B has its own clean buffer
        pb = eb.get("tstgfx")
        self.assertFalse(pb["dirty"])
        self.assertEqual({s["key"]: s["value"] for g in pb["groups"] for s in g["settings"]}["Renderer"], 1)

    def test_buffered_stage_then_save(self):
        e = self._eng(self.a)
        e.get("tstgfx")
        r = e.set("tstgfx", {"key": "VsyncEnable", "value": True})
        self.assertTrue(r["dirty"])
        self.assertEqual(_read(self.a, "VsyncEnable"), "false")   # not written until save
        self.assertTrue(e.save("tstgfx")["saved"])
        self.assertEqual(_read(self.a, "VsyncEnable"), "true")
        self.assertFalse(e.buf["dirty"])

    def test_cancel_discards(self):
        e = self._eng(self.a)
        e.get("tstgfx")
        e.set("tstgfx", {"key": "VsyncEnable", "value": True})
        self.assertTrue(e.buf["dirty"])
        e.cancel("tstgfx")
        self.assertFalse(e.buf["dirty"])
        e.save("tstgfx")
        self.assertEqual(_read(self.a, "VsyncEnable"), "false")

    def test_ebusy_guard(self):
        busy = self._eng(self.a, running=lambda: True)
        with self.assertRaises(RpcError):
            busy.set("tstgfx", {"key": "VsyncEnable", "value": True})
        with self.assertRaises(RpcError):
            busy.save("tstgfx")

    def test_save_preserves_external_change_to_other_keys(self):
        e = self._eng(self.a)
        e.get("tstgfx")
        e.set("tstgfx", {"key": "VsyncEnable", "value": True})   # staged
        # external writer changes a DIFFERENT key after the buffer loaded
        t = cfgutil.ini_replace(self.a.read_text(newline=""), "EmuCore/GS", "Renderer", "12")
        self.a.write_text(t, newline="")
        e.save("tstgfx")
        self.assertEqual(_read(self.a, "VsyncEnable"), "true")   # our edit applied
        self.assertEqual(_read(self.a, "Renderer"), "12")        # external change kept

    def test_register_wires_rpc_methods(self):
        from lib.madsrv import rpc
        e = eng.BufferedEngine(self.a, lambda: False, CATS, eng.new_buf())
        e.register()
        for verb in ("get", "set", "save", "cancel"):
            self.assertIn(f"tstgfx.{verb}", rpc._METHODS)


if __name__ == "__main__":
    unittest.main()
