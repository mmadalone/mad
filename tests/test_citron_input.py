"""citron.input_* — the per-button map clone: get payload (groups + selectors + players),
button remap with the \\default flip, input_clear -> [empty] (Eden lacks this method), the
docked/type selectors flipping \\default, and the "no controller configured" guard."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import citron_input_cmds as ci
from lib.madsrv import rpc

_G = "050000007e0500003003000001000000"
FIX = (
    "[Controls]\n"
    "player_0_type\\default=false\nplayer_0_type=0\n"
    f'player_0_button_a\\default=false\nplayer_0_button_a="engine:sdl,port:0,guid:{_G},button:1"\n'
    f'player_0_button_b\\default=false\nplayer_0_button_b="engine:sdl,port:0,guid:{_G},button:0"\n'
    f'player_0_lstick\\default=false\nplayer_0_lstick="engine:sdl,port:0,guid:{_G},'
    'axis_x:0,axis_y:1,invert_x:+,invert_y:+"\n\n'
    "[System]\n"
    "use_docked_mode\\default=true\nuse_docked_mode=1\n"
)


class CitronInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(FIX, newline="")
        self._orig = ci._FILE
        ci._FILE = self.ini
        ci._buf.reset()          # fresh buffer per case (the buffer is a module-level singleton)
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        ci._FILE = self._orig
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, verb, **params):
        return rpc._METHODS[f"citron.{verb}"][0](params)

    def _set(self, **params):
        """Stage a capture then Save. The buffered editor only writes disk on save, so a
        test that asserts on file content must commit first."""
        r = self._call("input_set", **params)
        self._call("input_save")
        return r

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", key)

    def _sys(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "System", key)

    def test_registered(self):
        for v in ("input_get", "input_set", "selector_set", "input_clear"):
            self.assertIn(f"citron.{v}", rpc._METHODS)

    def test_get_shape(self):
        p = self._call("input_get")
        self.assertEqual(len(p["groups"]), 3)               # Buttons / D-pad / Sticks
        keys = {s["key"] for s in p["selectors"]}
        self.assertEqual(keys, {"controller_type", "console_mode"})
        self.assertEqual(len(p["players"]), 8)
        self.assertEqual(p["player"], "player_0")

    def test_button_remap_flips_default(self):
        r = self._set(id="button_a", kind="btn", value=307)   # -> SDL button 2
        self.assertIn("button:2", self._disk("player_0_button_a"))
        self.assertEqual(self._disk("player_0_button_a\\default"), "false")
        self.assertIn("→", r["message"])

    def test_unconfigured_button_errors(self):
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="button_x", kind="btn", value=307)   # button_x absent

    def test_input_clear_sets_empty(self):
        self._call("input_clear", id="button_a", kind="btn")
        self._call("input_save")
        self.assertEqual(self._disk("player_0_button_a"), "[empty]")
        self.assertEqual(self._disk("player_0_button_a\\default"), "false")

    def test_console_selector_flips_default(self):
        self._call("selector_set", key="console_mode", value="0")
        self._call("input_save")
        self.assertEqual(self._sys("use_docked_mode"), "0")
        self.assertEqual(self._sys("use_docked_mode\\default"), "false")

    def test_type_selector(self):
        self._call("selector_set", key="controller_type", player="player_0", value="2")
        self._call("input_save")
        self.assertEqual(self._disk("player_0_type"), "2")
        self.assertEqual(self._disk("player_0_type\\default"), "false")


if __name__ == "__main__":
    unittest.main()
