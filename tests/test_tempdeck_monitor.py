"""Tests for ~/bin/temp-deck.py, the Steam Deck sensor monitor.

These assert against the LIVE Deck: the amdgpu SMU metrics table, steamdeck_hwmon,
BAT1 and the real terminal behaviour, so they carry @skip_on_ci and run only on the
device. What they are actually protecting:

  * The gpu_metrics binary parser. k10temp is not loaded on SteamOS, so that table
    is the ONLY source of per-core CPU temperature on this machine; a silent offset
    regression would misreport every core reading with no visible symptom.
  * Graceful degradation. A monitor that raises when a sensor disappears is worse
    than one that prints "--".
  * Column widths. Emoji occupy two columns while several report an East Asian
    Width of "N", so a naive length count under-measures and the layout drifts.

Parser checks parse ONE captured blob two ways rather than comparing two separate
sysfs reads: core temperature and socket power move between reads, which produces
false mismatches.
"""
from __future__ import annotations

import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._ci import skip_on_ci

# The REPO copy, which is what install.sh deploys to ~/bin - not ~/bin itself. Testing
# the deployed copy would exercise whatever happens to be on this machine, including a
# hand-edited or stale one, instead of the file that actually ships.
MONITOR = str(Path(__file__).resolve().parent.parent / "temp-deck.py")
GPU_METRICS = "/sys/class/drm/card0/device/gpu_metrics"
ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")


def load_monitor():
    """Import temp-deck.py by path (its name is not a valid module identifier)."""
    spec = importlib.util.spec_from_file_location("temp_deck", MONITOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob_file(data: bytes) -> str:
    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.write(data)
    handle.close()
    return handle.name


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class GpuMetricsParser(unittest.TestCase):
    """The parser must agree exactly with a hand-decode of the same bytes."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(GPU_METRICS):
            raise unittest.SkipTest("no gpu_metrics node (not a Van Gogh APU)")
        cls.tn = load_monitor()
        cls.blob = open(GPU_METRICS, "rb").read()

    def parsed(self):
        path = blob_file(self.blob)
        try:
            return self.tn.GpuMetrics(path).read()
        finally:
            os.unlink(path)

    def test_scalar_fields_match_manual_unpack(self):
        got = self.parsed()
        expect = {
            "temperature_gfx": ("<H", 4), "temperature_soc": ("<H", 6),
            "average_socket_power": ("<H", 40), "average_cpu_power": ("<H", 42),
            "average_gfx_power": ("<H", 46), "current_gfxclk": ("<H", 76),
            "current_uclk": ("<H", 80), "current_fclk": ("<H", 82),
            "average_gfx_voltage": ("<H", 156), "average_gfx_current": ("<H", 162),
            "throttle_status": ("<I", 108), "system_clock_counter": ("<Q", 32),
            "indep_throttle_status": ("<Q", 120),
        }
        for name, (fmt, off) in expect.items():
            want = struct.unpack_from(fmt, self.blob, off)[0]
            if want in (0xFFFF, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                self.assertIsNone(got.get(name), name)
            else:
                self.assertEqual(got.get(name), want, name)

    def test_per_core_arrays_drop_absent_cores(self):
        got = self.parsed()
        for name, off in (("temperature_core", 8), ("average_core_power", 48),
                          ("current_coreclk", 88)):
            want = [struct.unpack_from("<H", self.blob, off + 2 * i)[0]
                    for i in range(8)]
            want = [v for v in want if v != 0xFFFF]
            self.assertEqual(got.get(name), want, name)
            # The Deck is a 4-core part; cores 4-7 read as the invalid sentinel.
            self.assertEqual(len(want), 4, f"{name}: expected 4 physical cores")

    def test_clock_counter_tracks_uptime(self):
        """Independent cross-check that the u64 offset is right."""
        counter = self.parsed()["system_clock_counter"] / 1e9
        uptime = float(open("/proc/uptime").read().split()[0])
        self.assertLess(abs(counter - uptime), 60, "clock counter vs /proc/uptime")


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class MalformedMetrics(unittest.TestCase):
    """Bad input must degrade, never raise and never invent a reading."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(GPU_METRICS):
            raise unittest.SkipTest("no gpu_metrics node")
        cls.tn = load_monitor()
        cls.blob = open(GPU_METRICS, "rb").read()

    def read_of(self, data: bytes):
        path = blob_file(data)
        try:
            gm = self.tn.GpuMetrics(path)
            return gm, gm.read()
        finally:
            os.unlink(path)

    def test_missing_node(self):
        gm = self.tn.GpuMetrics("/nonexistent/gpu_metrics")
        self.assertEqual(gm.read(), {})
        self.assertTrue(gm.unsupported_reason)

    def test_truncated_is_refused(self):
        _gm, out = self.read_of(self.blob[:100])
        self.assertEqual(out, {})

    def test_empty_is_refused(self):
        _gm, out = self.read_of(b"")
        self.assertEqual(out, {})

    def test_discrete_gpu_layout_is_refused_not_misparsed(self):
        bad = bytearray(self.blob)
        bad[2] = 1                      # format_revision 1 = different struct
        _gm, out = self.read_of(bytes(bad))
        self.assertEqual(out, {})

    def test_older_revision_parses_common_prefix_only(self):
        """v2_1..v2_4 are strictly additive: same offsets, fields appended."""
        old = bytearray(self.blob[:120])
        struct.pack_into("<HBB", old, 0, 120, 2, 1)
        _gm, out = self.read_of(bytes(old))
        self.assertEqual(out.get("temperature_gfx"),
                         struct.unpack_from("<H", self.blob, 4)[0])
        self.assertIsNone(out.get("indep_throttle_status"))
        self.assertIsNone(out.get("average_gfx_voltage"))

    def test_invalid_sentinels_become_none(self):
        inv = bytearray(b"\xff" * 168)
        struct.pack_into("<HBB", inv, 0, 168, 2, 4)
        _gm, out = self.read_of(bytes(inv))
        self.assertIsNone(out.get("temperature_gfx"))
        self.assertEqual(out.get("temperature_core"), [])
        self.assertIsNone(out.get("indep_throttle_status"))


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class ThrottleDecode(unittest.TestCase):
    """The eleven limiter bits Van Gogh actually populates."""

    @classmethod
    def setUpClass(cls):
        cls.tn = load_monitor()

    def test_no_bits(self):
        self.assertEqual(self.tn.decode_throttle(None), [])
        self.assertEqual(self.tn.decode_throttle(0), [])

    def test_power_limit_is_not_flagged_as_thermal(self):
        # Hitting a power limit is normal on a handheld; only thermal is alarming.
        self.assertEqual(self.tn.decode_throttle(1 << 4),
                         [("sustained power limit", "power")])
        self.assertEqual(self.tn.decode_throttle(1 << 33),
                         [("CPU core temperature limit", "thermal")])

    def test_observed_under_load_value(self):
        """0x60 = FPPT|SPPT, what this Deck reports pinned at its 15 W cap."""
        kinds = dict(self.tn.decode_throttle(0x60))
        self.assertEqual(sorted(kinds), ["fast power limit (burst)",
                                         "slow power limit"])
        self.assertEqual(set(kinds.values()), {"power"})

    def test_undefined_bits_ignored(self):
        self.assertEqual(self.tn.decode_throttle(1 << 60), [])


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class ColumnWidths(unittest.TestCase):
    """Emoji are two columns wide; several report East Asian Width "N"."""

    @classmethod
    def setUpClass(cls):
        cls.tn = load_monitor()

    def test_emoji_measure_two_columns(self):
        for sym in ("❄️", "🌡️", "🖥️", "🔋", "💽", "⚡", "🌀"):
            self.assertEqual(self.tn.visible_len(sym), 2, repr(sym))

    def test_ansi_and_degree_sign_do_not_count(self):
        self.assertEqual(self.tn.visible_len("\033[91mabc\033[0m"), 3)
        self.assertEqual(self.tn.visible_len("42.0°C"), 6)

    def test_variation_selector_icons_get_a_wider_gap(self):
        """FE0F emoji advance one cell but draw two, eating the trailing space."""
        self.assertEqual(self.tn.icon("❄️"), "❄️  ")
        self.assertEqual(self.tn.icon("🔋"), "🔋 ")

    def test_fit_never_exceeds_the_budget(self):
        for text in ("🔋 32.0°C  💽 35.9°C  🖥️ 46.0°C", "plain ascii text here"):
            for width in (4, 7, 12, 30):
                self.assertLessEqual(self.tn.visible_len(self.tn.fit(text, width)),
                                     width, f"{text!r} @ {width}")


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class Renderers(unittest.TestCase):
    """Every mode must fit its terminal and survive a sensorless snapshot."""

    MODES = ("default", "defnotemp", "multi", "full", "temphis", "dash", "line")

    @classmethod
    def setUpClass(cls):
        cls.tn = load_monitor()

    def monitor(self, mode):
        mon = self.tn.QuarkSystemMonitor()
        mon.use_color = False
        mon.display_mode = mode
        mon.fan = self.tn.FanController(False, 70.0, 600)
        return mon

    def test_empty_snapshot_never_raises(self):
        for mode in self.MODES:
            mon = self.monitor(mode)
            self.assertIsInstance(mon.renderer.render({"timestamp": 0}, 80), list,
                                  mode)

    def test_output_fits_the_terminal(self):
        for mode in self.MODES:
            mon = self.monitor(mode)
            snap = mon.poll()
            for width in (30, 60, 80, 120):
                for line in mon.renderer.render(snap, width):
                    self.assertLessEqual(self.tn.visible_len(line), width,
                                         f"{mode} @ {width}: {line!r}")

    def test_fan_notice_draws_over_the_row_without_adding_one(self):
        for mode in ("default", "multi", "full", "dash"):
            mon = self.monitor(mode)
            mon.fan = self.tn.FanController(True, 70.0, 600)
            snap = mon.poll()
            mon.fan.message = None
            before = len(mon.renderer.render(snap, 100))
            mon.fan.notify("fan back to SteamOS control")
            after = mon.renderer.render(snap, 100)
            self.assertEqual(len(after), before, f"{mode}: row count changed")
            row = [l for l in after if "fan back to SteamOS control" in l]
            self.assertTrue(row, f"{mode}: notice missing")
            self.assertNotIn("rpm", row[0].lower(),
                             f"{mode}: notice should REPLACE the readout")


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class CommandLine(unittest.TestCase):
    """Every advertised entry point exits cleanly and honours NO_COLOR."""

    ARGS = (["--once"], ["--defnotemp", "--once"], ["--multi", "--once"],
            ["--full", "--once"], ["--temphis", "--once"], ["--dash", "--once"],
            ["--line", "--once"], ["--json"], ["--throttle-history"], ["--version"])

    def run_monitor(self, args, env=None):
        environ = dict(os.environ)
        environ.update(env or {})
        return subprocess.run([sys.executable, MONITOR] + args, capture_output=True,
                              text=True, timeout=60, env=environ)

    def test_all_modes_exit_zero(self):
        for args in self.ARGS:
            self.assertEqual(self.run_monitor(args).returncode, 0, " ".join(args))

    def test_json_is_parseable_and_has_the_key_fields(self):
        import json
        data = json.loads(self.run_monitor(["--json"]).stdout)
        for key in ("apu_temp", "cpu_temp", "core_temps", "apu_watts",
                    "throttle_reasons", "battery", "fan", "gpu_metrics_rev"):
            self.assertIn(key, data)
        self.assertEqual(len(data["core_temps"]), 4, "expected 4 physical cores")

    def test_piped_output_carries_no_escape_codes(self):
        out = self.run_monitor(["--dash", "--once"]).stdout
        self.assertNotIn("\033[", out)

    def test_rejects_bad_arguments(self):
        self.assertNotEqual(self.run_monitor(["--dash", "--multi"]).returncode, 0)
        self.assertNotEqual(self.run_monitor(["--warm", "90", "--hot", "50"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
