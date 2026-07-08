"""cemu_pg_input - per-game controller profiles ([Controller] controller1..8 in gameProfiles/<tid>.ini
naming a controllerProfiles/<name>.xml). Options exclude the router-managed active slot files
(controller0..7); index 0 = "Use router / global" (no key); set writes the bare profile NAME; CRLF
preserved."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cemu_games, cfgutil
from lib.madsrv import cemu_pg_input_cmds as pgi
from lib.madsrv import rpc

_TID = "0005000010111100"


class CemuPgInput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "gameProfiles").mkdir()
        prof = self.d / "controllerProfiles"
        prof.mkdir()
        for nm in ("DualSense 1", "WiiU Pro 1 + Steamdeck", "Steamdeck"):
            (prof / f"{nm}.xml").write_text("<emulated_controller/>")
        for slot in range(8):                                     # active slot files -> excluded
            (prof / f"controller{slot}.xml").write_text("<emulated_controller/>")
        self._cfg = cemu_games._CONFIG_DIR
        cemu_games._CONFIG_DIR = self.d
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        cemu_games._CONFIG_DIR = self._cfg
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self):
        return rpc._METHODS["cemu_pg_input.get"][0]({"titleid": _TID})

    def _set(self, key, value):
        return rpc._METHODS["cemu_pg_input.set"][0]({"titleid": _TID, "key": key, "value": value})

    def _disk(self, key):
        lf = cemu_games.pergame_path(_TID).read_bytes().decode().replace("\r\n", "\n")
        return cfgutil.ini_read(lf, "Controller", key)

    def test_options_exclude_slot_files(self):
        rows = self._get()["groups"][0]["settings"]
        self.assertEqual(len(rows), 8)                            # 8 ports
        opts = rows[0]["options"]
        self.assertEqual(opts[0], "Use router / global")
        self.assertEqual(opts[1:], ["DualSense 1", "Steamdeck", "WiiU Pro 1 + Steamdeck"])
        self.assertNotIn("controller0", opts)

    def test_set_writes_bare_name_and_clear(self):
        self._set("controller1", 3)                               # "WiiU Pro 1 + Steamdeck"
        self.assertEqual(self._disk("controller1"), "WiiU Pro 1 + Steamdeck")
        self.assertEqual(self._get()["groups"][0]["settings"][0]["value"], 3)
        self._set("controller1", 0)                               # Use router / global
        self.assertIsNone(self._disk("controller1"))

    def test_crlf_preserved(self):
        cemu_games.pergame_path(_TID).write_bytes(b"[General]\r\nstartWithPadView = false\r\n")
        self._set("controller2", 1)                               # "DualSense 1"
        self.assertIn(b"\r\n", cemu_games.pergame_path(_TID).read_bytes())
        self.assertEqual(self._disk("controller2"), "DualSense 1")

    def test_bad_key_rejected(self):
        with self.assertRaises(rpc.RpcError):
            self._set("controller9", 1)


if __name__ == "__main__":
    unittest.main()
