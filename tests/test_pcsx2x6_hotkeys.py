"""pcsx2x6 per-member Hotkeys: reuse the standard pcsx2hk logic pointed at each fork ini + the
pcsx2x6 process guard. Namespaces x6a_hk / x6r_hk."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil
from lib.madsrv import pcsx2x6_hotkeys_cmds as hk
from lib.madsrv import rpc

FIX = "[Hotkeys]\nToggleFullscreen = Keyboard/F11\nZoomIn = Keyboard/Plus\n"


class ForkHotkeys(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.a = self.d / "a.ini"
        self.a.write_text(FIX, newline="")
        self._run = hk._running
        hk._running = lambda: False
        import lib.staterev as sr
        self._b = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        hk._running = self._run
        import lib.staterev as sr
        sr.bump = self._b
        shutil.rmtree(self.d, ignore_errors=True)

    def test_registered_both_members(self):
        for pfx in ("x6a", "x6r"):
            for v in ("input_get", "input_set", "input_clear"):
                self.assertIn(f"{pfx}_hk.{v}", rpc._METHODS)

    def test_targets_fork_inis(self):
        self.assertTrue(str(hk._INIS["x6a"]).endswith("pcsx2x6/PCSX2x6/inis/PCSX2.ini"))
        self.assertTrue(str(hk._INIS["x6r"]).endswith("pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini"))

    def test_get_renders_actions_and_unknown(self):
        pay = hk._get(self.a)
        self.assertIn("Navigation", [g["title"] for g in pay["groups"]])
        self.assertTrue(pay["clearable"])
        rows = {b["id"]: b for g in pay["groups"] for b in g["binds"]}
        self.assertEqual(rows["ToggleFullscreen"]["value"], "F11")   # known action, its binding
        self.assertIn("ZoomIn", rows)                                # unknown live key preserved

    def test_write_and_clear(self):
        hk._write(self.a, "TogglePause", "Keyboard/Space")
        self.assertEqual(cfgutil.ini_read(self.a.read_text(newline=""), "Hotkeys", "TogglePause"),
                         "Keyboard/Space")
        hk._input_clear(self.a, {"id": "TogglePause"})
        self.assertIsNone(cfgutil.ini_read(self.a.read_text(newline=""), "Hotkeys", "TogglePause"))

    def test_write_bumps_config_rev(self):
        # a hotkey write MUST bump staterev "config" or MAD keeps serving the stale page.
        import lib.staterev as sr
        bumps = []
        sr.bump = lambda n: bumps.append(n)          # tearDown restores the original
        hk._write(self.a, "TogglePause", "Keyboard/Space")
        self.assertIn("config", bumps)

    def test_ebusy_guard(self):
        hk._running = lambda: True
        with self.assertRaises(rpc.RpcError):
            hk._write(self.a, "TogglePause", "Keyboard/Space")

    def test_reject_unknown_action(self):
        with self.assertRaises(rpc.RpcError):
            hk._write(self.a, "NotAnAction", "Keyboard/Space")


if __name__ == "__main__":
    unittest.main()
