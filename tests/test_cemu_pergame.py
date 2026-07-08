"""cemu_pergame - per-game gameProfiles/<tid>.ini: create-on-demand (exists:true), inherit-aware
rows with index 0 = "Use default", option-mode enums (cpuMode/threadQuantum) matching CRLF values,
plain key = value writes (NO \\default twin), clear-to-default removing the key, and CRLF preserved
on write. Regression: cpuMode read from a CRLF file must map to a real option, not "(current: 4\\r)"."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cemu_games, cfgutil
from lib.madsrv import cemu_pergame as pg
from lib.madsrv import rpc

_TID = "0005000010111100"


class CemuPerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "gameProfiles").mkdir()
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

    def _get(self, ns):
        return rpc._METHODS[f"{ns}.get"][0]({"titleid": _TID})

    def _set(self, ns, key, value):
        return rpc._METHODS[f"{ns}.set"][0]({"titleid": _TID, "key": key, "value": value})

    def _rows(self, ns):
        return {s["key"]: s for g in self._get(ns)["groups"] for s in g["settings"]}

    def _ini_bytes(self):
        return cemu_games.pergame_path(_TID).read_bytes()

    def _disk(self, sec, key):
        lf = self._ini_bytes().decode().replace("\r\n", "\n")
        return cfgutil.ini_read(lf, sec, key)

    def test_fresh_game_all_default(self):
        p = self._get("cemu_pg_gfx")
        self.assertTrue(p["exists"])                              # create-on-demand
        row = self._rows("cemu_pg_gfx")["graphics_api"]
        self.assertEqual(row["options"][0], "Use default")
        self.assertEqual(row["value"], 0)

    def test_crlf_option_enum_maps_regression(self):
        # A CRLF file with cpuMode = 4 must map to "Auto", NOT "(current: 4\r)".
        cemu_games.pergame_path(_TID).write_bytes(b"[CPU]\r\ncpuMode = 4\r\n")
        row = self._rows("cemu_pg_general")["cpuMode"]
        self.assertNotIn("(current", "".join(row["options"]))
        self.assertEqual(row["options"][row["value"]], "Auto")

    def test_enum_override_no_twin_and_crlf_kept(self):
        cemu_games.pergame_path(_TID).write_bytes(b"[CPU]\r\ncpuMode = 4\r\n")
        # display index 3 = "Multi-core recompiler" -> stored code "3"
        self._set("cemu_pg_general", "cpuMode", 3)
        self.assertEqual(self._disk("CPU", "cpuMode"), "3")
        self.assertIsNone(self._disk("CPU", "cpuMode\\default"))  # NO yuzu-style twin
        self.assertIn(b"\r\n", self._ini_bytes())                # CRLF preserved
        self.assertEqual(self._rows("cemu_pg_general")["cpuMode"]["value"], 3)

    def test_graphics_api_index_enum(self):
        self._set("cemu_pg_gfx", "graphics_api", 2)              # idx2 -> Vulkan -> stored "1"
        self.assertEqual(self._disk("Graphics", "graphics_api"), "1")
        self.assertEqual(self._rows("cemu_pg_gfx")["graphics_api"]["value"], 2)

    def test_clear_to_default_removes_key(self):
        self._set("cemu_pg_general", "cpuMode", 4)               # Auto -> stored "4"
        self.assertEqual(self._disk("CPU", "cpuMode"), "4")
        self._set("cemu_pg_general", "cpuMode", 0)               # Use default -> remove
        self.assertIsNone(self._disk("CPU", "cpuMode"))
        self.assertEqual(self._rows("cemu_pg_general")["cpuMode"]["value"], 0)

    def test_bool_three_way(self):
        row = self._rows("cemu_pg_general")["loadSharedLibraries"]
        self.assertEqual(row["options"], ["Use default", "Off", "On"])
        self._set("cemu_pg_general", "loadSharedLibraries", 2)   # On
        self.assertEqual(self._disk("General", "loadSharedLibraries"), "true")
        self._set("cemu_pg_general", "loadSharedLibraries", 0)   # default -> remove
        self.assertIsNone(self._disk("General", "loadSharedLibraries"))

    def test_bad_titleid_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["cemu_pg_gfx.get"][0]({"titleid": "../etc/passwd"})


if __name__ == "__main__":
    unittest.main()
