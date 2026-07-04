r"""eden_* GLOBAL settings: the Eden-specific enum indices + the mandatory `\default` twin flip.

Two things this locks in:
  1. The silent-discard fix: Eden's config reader ignores a `key=value` whose `key\default` twin is
     not `false`, so a global write MUST flip the twin. The old flat eden.get/set (plain ini_replace)
     did NOT, silently reverting every non-default write. `_yuzu_write` fixes it.
  2. Eden's own enum indices (verified vs Eden settings_enums.h, github.com/eden-emulator/mirror),
     which DIVERGE from Citron / the old eden_cmds.py: resolution_setup 1x native = index 3;
     scaling_filter Lanczos@4 / ScaleForce@5 / Fsr@6; gpu_accuracy Low/Medium/High; output_engine an
     integer index (write_mode "index"), not the stale "sdl2" string list.

Run:  python3 -m unittest tests.test_eden_settings -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import eden_settings as es
from lib.madsrv import rpc


class Descriptors(unittest.TestCase):
    """Pure descriptor checks - no file needed. Guards the stale-index bugs from eden_cmds.py."""

    def test_resolution_1x_native_is_index_3(self):
        self.assertEqual(es._RESOLUTION[3], "1x (720p, native)")
        self.assertEqual(len(es._RESOLUTION), 13)          # 0.25x .. 8x

    def test_scaling_filter_lanczos_scaleforce_fsr_order(self):
        self.assertEqual(es._SCALING[4], "Lanczos")
        self.assertEqual(es._SCALING[5], "ScaleForce")
        self.assertEqual(es._SCALING[6], "AMD FSR")

    def test_gpu_accuracy_low_medium_high(self):
        self.assertEqual(es._GPU_ACCURACY, ["Low", "Medium", "High"])

    def test_cpu_accuracy_has_debugging(self):
        self.assertEqual(es._CPU_ACCURACY[4], "Debugging")

    def test_output_engine_is_integer_index_not_option(self):
        item = next(it for g in es.AUDIO_GROUPS for it in g["items"] if it["key"] == "output_engine")
        self.assertEqual(item["write_mode"], "index")      # live file stores output_engine=0 (int)
        self.assertNotIn("options_stored", item)           # NOT the stale "auto/cubeb/sdl2/.." strings
        self.assertEqual(item["options_display"][2], "SDL3")   # Sdl3, not Sdl2

    def test_docked_mode_is_int_bool(self):
        item = next(it for g in es.SYSTEM_GROUPS for it in g["items"] if it["key"] == "use_docked_mode")
        self.assertEqual((item["bool_true"], item["bool_false"]), ("1", "0"))


class TwinFlip(unittest.TestCase):
    """A global write must set BOTH `key=value` AND `key\\default=false`, else Eden discards it."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        # Pristine-style: value present but marked default (\default=true) -> Eden would ignore a
        # plain value write. The writer must flip the twin to false.
        self.ini.write_text(
            "[Renderer]\n"
            "resolution_setup\\default=true\n"
            "resolution_setup=3\n"
            "scaling_filter\\default=true\n"
            "scaling_filter=1\n",
            newline="")
        self._file = es._FILE
        es._FILE = self.ini
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        es._FILE = self._file
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, ns, verb, **params):
        return rpc._METHODS[f"{ns}.{verb}"][0](params)

    def test_set_writes_value_and_flips_twin(self):
        self._call("eden_gfx", "set", key="resolution_setup", value=6)   # -> 2x (1440p)
        t = self.ini.read_text()
        self.assertIn("resolution_setup=6", t)                           # value written
        self.assertIn("resolution_setup\\default=false", t)              # twin flipped (the fix)
        self.assertNotIn("resolution_setup\\default=true", t)

    def test_value_round_trips_through_get(self):
        self._call("eden_gfx", "set", key="scaling_filter", value=6)     # AMD FSR
        g = self._call("eden_gfx", "get")
        row = [r for grp in g["groups"] for r in grp["settings"] if r["key"] == "scaling_filter"][0]
        self.assertEqual(row["value"], 6)
        self.assertEqual(row["options"][6], "AMD FSR")


if __name__ == "__main__":
    unittest.main()
