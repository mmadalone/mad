"""Value round-trips for the granular Ryujinx global settings pages (ryujinx_settings.*), with the
enum OFF-BY-ONE regression as the centrepiece:

An on-disk value OUTSIDE MAD's curated options_stored is PREPENDED to the display by
cfgutil._enum_get (so nothing is lost), which shifts every real option +1. The write MUST mirror
that prepend (ryujinx_cmds._apply_key routes through cfgutil._enum_write) or picking an option
silently stores the NEIGHBOUR's value. Pre-fix, on-disk max_anisotropy=1 + picking 'Auto' stored 2.

Also covers: exact NAME-enum tokens, stored_int enums (integer in Config.json), the float
(audio_volume), bool-from-string, version-safe present-only offering, and the running guard.

Run:  python3 -m unittest tests.test_ryujinx_settings -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard, staterev
from lib.madsrv import rpc, ryujinx_json, ryujinx_settings  # noqa: F401  (import registers the pages)
from lib.madsrv.rpc import RpcError


class RyujinxSettings(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "Config.json"
        self._c = ryujinx_json.CONFIG
        ryujinx_json.CONFIG = self.cfg
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda n: False
        self._bump = staterev.bump
        staterev.bump = lambda n: None

    def tearDown(self):
        ryujinx_json.CONFIG = self._c
        proc_guard.emulator_running = self._run
        staterev.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, data):
        self.cfg.write_text(json.dumps(data))

    def _read(self):
        return json.loads(self.cfg.read_text())

    def _get(self, ns):
        return rpc._METHODS[f"{ns}.get"][0]({})

    def _set(self, ns, key, value):
        return rpc._METHODS[f"{ns}.set"][0]({"key": key, "value": value})

    def _row(self, ns, key):
        return [s for g in self._get(ns)["groups"] for s in g["settings"] if s["key"] == key][0]

    def test_name_enum_stores_exact_token(self):
        self._write({"aspect_ratio": "Fixed16x9"})
        self._set("ryujinx_gfx", "aspect_ratio", "0")      # display idx 0 -> Fixed4x3
        self.assertEqual(self._read()["aspect_ratio"], "Fixed4x3")

    def test_stored_int_enum_writes_integer(self):
        self._write({"vsync_mode": 0, "dram_size": 0})
        self._set("ryujinx_gfx", "vsync_mode", "1")        # Off (unlimited) -> 1
        self._set("ryujinx_cpu", "dram_size", "2")         # 8 GiB -> 2
        r = self._read()
        self.assertEqual(r["vsync_mode"], 1)
        self.assertIsInstance(r["vsync_mode"], int)
        self.assertEqual(r["dram_size"], 2)
        self.assertIsInstance(r["dram_size"], int)

    def test_float_round_trips(self):
        self._write({"audio_volume": 1.0})
        self._set("ryujinx_audio", "audio_volume", "0.5")
        v = self._read()["audio_volume"]
        self.assertEqual(v, 0.5)
        self.assertIsInstance(v, float)

    def test_bool_from_string_zero(self):
        self._write({"docked_mode": True})
        self._set("ryujinx_system", "docked_mode", "0")    # the C++ sends bool as "0"
        self.assertFalse(self._read()["docked_mode"])

    # ── THE regression: an out-of-curated on-disk value must not off-by-one ──
    def test_out_of_list_enum_maps_to_picked_option(self):
        # max_anisotropy=1 (valid Ryujinx 1x, NOT in MAD's curated [-1,2,4,8,16]) -> the display
        # prepends "1" at index 0, shifting the real options +1. Picking one must store THAT option.
        self._write({"max_anisotropy": 1})
        row = self._row("ryujinx_gfx", "max_anisotropy")
        self.assertEqual(row["options"][0], "1")           # current prepended so nothing is lost
        self.assertEqual(row["value"], 0)
        self._set("ryujinx_gfx", "max_anisotropy", "1")    # display idx 1 = 'Auto' (-1)
        self.assertEqual(self._read()["max_anisotropy"], -1)   # NOT the neighbour (was 2 pre-fix)

    def test_out_of_list_enum_keep_current(self):
        self._write({"max_anisotropy": 1})
        self._set("ryujinx_gfx", "max_anisotropy", "0")    # idx 0 = keep the prepended current
        self.assertEqual(self._read()["max_anisotropy"], 1)

    def test_version_safe_absent_key_not_offered(self):
        self._write({"aspect_ratio": "Fixed16x9"})         # this build has no vsync_mode key
        keys = [s["key"] for g in self._get("ryujinx_gfx")["groups"] for s in g["settings"]]
        self.assertIn("aspect_ratio", keys)
        self.assertNotIn("vsync_mode", keys)

    def test_running_refuses_write(self):
        self._write({"docked_mode": True})
        proc_guard.emulator_running = lambda n: True
        with self.assertRaises(RpcError):
            self._set("ryujinx_system", "docked_mode", "0")


if __name__ == "__main__":
    unittest.main()
