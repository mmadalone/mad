"""Citron global settings (citron_general/system/cpu/gfx/gfxadv/audio):
the mandatory `\\default=false` twin flip on every write (else Citron discards the value),
create-the-twin-when-absent, the Citron-specific enum indices, use_docked_mode 1/0, and the
staterev bump. These pages reuse cfgutil.do_get/do_set with citron_settings._yuzu_write."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import citron_settings as cs
from lib.madsrv import rpc

# Representative slice of a pristine Citron ini: every value carries a `\default=true` twin,
# EXCEPT nvdec_emulation (twin deliberately absent -> exercises create-on-write of the twin).
FIX = (
    "[Core]\n"
    "use_multi_core\\default=true\nuse_multi_core=true\n"
    "memory_layout_mode\\default=true\nmemory_layout_mode=0\n"
    "speed_limit\\default=true\nspeed_limit=100\n\n"
    "[Cpu]\n"
    "cpu_accuracy\\default=true\ncpu_accuracy=0\n\n"
    "[Renderer]\n"
    "backend\\default=true\nbackend=0\n"
    "resolution_setup\\default=true\nresolution_setup=3\n"
    "scaling_filter\\default=true\nscaling_filter=1\n"
    "gpu_accuracy\\default=true\ngpu_accuracy=1\n"
    "crt_gamma\\default=true\ncrt_gamma=1\n"
    "nvdec_emulation=2\n\n"                       # <- no \default twin on purpose
    "[Audio]\n"
    "output_engine\\default=true\noutput_engine=auto\n"
    "volume\\default=true\nvolume=100\n\n"
    "[System]\n"
    "use_docked_mode\\default=true\nuse_docked_mode=1\n"
    "region_index\\default=true\nregion_index=1\n"
)


class CitronSettings(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(FIX, newline="")
        self._orig_file = cs._FILE
        cs._FILE = self.ini
        self._orig_run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._orig_bump = sr.bump
        self.bumps = []
        sr.bump = lambda n: self.bumps.append(n)

    def tearDown(self):
        cs._FILE = self._orig_file
        proc_guard.emulator_running = self._orig_run
        import lib.staterev as sr
        sr.bump = self._orig_bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _get(self, ns):
        return rpc._METHODS[f"{ns}.get"][0]({})

    def _set(self, ns, key, value):
        return rpc._METHODS[f"{ns}.set"][0]({"key": key, "value": value})

    def _rows(self, ns):
        return {s["key"]: s for g in self._get(ns)["groups"] for s in g["settings"]}

    def _disk(self, sec, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), sec, key)

    # ── registration + payload shape ─────────────────────────────────────────
    def test_all_pages_registered(self):
        for ns in ("citron_general", "citron_system", "citron_cpu",
                   "citron_gfx", "citron_gfxadv", "citron_audio"):
            for verb in ("get", "set"):
                self.assertIn(f"{ns}.{verb}", rpc._METHODS)

    def test_get_reports_exists_true(self):
        # GuiMadPageEmuSettings renders an empty page unless the payload's exists is True.
        self.assertTrue(self._get("citron_gfx").get("exists"))

    # ── the \default gotcha (the whole reason this module exists) ─────────────
    def test_write_flips_default_twin(self):
        # A pristine key is \default=true; writing must set BOTH the value AND \default=false,
        # or Citron ignores the value on next read.
        self._set("citron_gfx", "resolution_setup", 4)
        self.assertEqual(self._disk("Renderer", "resolution_setup"), "4")
        self.assertEqual(self._disk("Renderer", "resolution_setup\\default"), "false")

    def test_write_creates_absent_default_twin(self):
        # nvdec_emulation has no \default twin in the fixture; the write must CREATE it.
        self.assertIsNone(self._disk("Renderer", "nvdec_emulation\\default"))
        self._set("citron_gfx", "nvdec_emulation", 1)
        self.assertEqual(self._disk("Renderer", "nvdec_emulation"), "1")
        self.assertEqual(self._disk("Renderer", "nvdec_emulation\\default"), "false")

    def test_unchanged_value_still_flips_default(self):
        # Even when the value equals what's on disk, a pristine \default=true must flip to false
        # (else the value stays "compiled default" and is discarded).
        self._set("citron_gfxadv", "gpu_accuracy", 1)       # already =1 (Adv. Graphics page)
        self.assertEqual(self._disk("Renderer", "gpu_accuracy\\default"), "false")

    def test_set_bumps_config_rev(self):
        self._set("citron_gfx", "resolution_setup", 5)
        self.assertIn("config", self.bumps)

    # ── Citron-specific enum correctness (must differ from Eden) ──────────────
    def test_resolution_enum_native_is_index_3(self):
        row = self._rows("citron_gfx")["resolution_setup"]
        self.assertEqual(row["value"], 3)                    # live=3
        self.assertEqual(row["options"][3], "1x (720p, native)")
        self.assertEqual(row["options"][0], "0.25x (180p)")  # Citron inserted 0.25x at 0

    def test_backend_has_no_opengl(self):
        row = self._rows("citron_gfx")["backend"]
        self.assertEqual(row["options"], ["Vulkan", "Null (no graphics)"])
        self.assertEqual(row["value"], 0)                    # 0 = Vulkan

    def test_scaling_filter_fsr_is_index_7(self):
        row = self._rows("citron_gfx")["scaling_filter"]
        self.assertEqual(row["options"][7], "AMD FSR")
        self.assertEqual(row["options"][5], "ScaleFx")

    def test_gpu_accuracy_low_prepended(self):
        row = self._rows("citron_gfxadv")["gpu_accuracy"]
        self.assertEqual(row["options"][0], "Low")
        self.assertEqual(row["value"], 1)                    # live=1 == Normal

    def test_shader_backend_not_offered(self):
        # OpenGL is gone in Citron; shader_backend is a dead/orphaned key -> never surfaced.
        self.assertNotIn("shader_backend", self._rows("citron_gfx"))

    # ── use_docked_mode is a 1/0 bool, not true/false ─────────────────────────
    def test_docked_mode_writes_one_zero(self):
        rows = self._rows("citron_system")
        self.assertTrue(rows["use_docked_mode"]["value"])    # live=1 -> True
        self._set("citron_system", "use_docked_mode", False)
        self.assertEqual(self._disk("System", "use_docked_mode"), "0")
        self.assertEqual(self._disk("System", "use_docked_mode\\default"), "false")
        self._set("citron_system", "use_docked_mode", True)
        self.assertEqual(self._disk("System", "use_docked_mode"), "1")

    # ── audio output_engine is a stored token, not an index ───────────────────
    def test_output_engine_option_tokens(self):
        self._set("citron_audio", "output_engine", 2)        # index 2 -> "sdl2"
        self.assertEqual(self._disk("Audio", "output_engine"), "sdl2")
        self.assertEqual(self._disk("Audio", "output_engine\\default"), "false")

    # ── byte-preservation: only touched lines change ──────────────────────────
    def test_untouched_lines_preserved(self):
        before = self.ini.read_text(newline="")
        self._set("citron_audio", "volume", 80)
        after = self.ini.read_text(newline="")
        # every line except the two volume lines is identical + order preserved
        self.assertEqual(self._disk("Audio", "volume"), "80")
        self.assertIn("use_multi_core=true", after)
        self.assertEqual(before.count("\n"), after.count("\n"))   # no lines added/removed


if __name__ == "__main__":
    unittest.main()
