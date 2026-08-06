#!/usr/bin/env python3
"""
Quark's Premium System Monitor(TM) - Steam Deck Edition
"Because mediocre monitoring is bad for business!"
No middlemen, no brokers, no Home Assistant - pure local latinum!

This is the Steam Deck port of the old Raspberry Pi monitor. Everything it
reports comes from this machine's own /sys and /proc. No network, no tokens.

WHAT CHANGED FROM THE PI VERSION
  * vcgencmd is gone (it does not exist on an AMD APU). Throttle status now
    comes from the amdgpu SMU via /sys/class/drm/card0/device/gpu_metrics,
    decoded into the eleven limiter bits Van Gogh actually reports.
  * thermal_zone0 was the *board* sensor, not the APU. The APU temperature now
    comes from gpu_metrics / amdgpu.
  * k10temp is not loaded on SteamOS, so gpu_metrics is the ONLY source of
    per-core CPU temperature on this machine.
  * hwmon devices are resolved by NAME. The hwmonN numbers are not stable
    across boots and must never be hardcoded.
  * Thresholds retuned for a handheld (warm 70 / hot 85) instead of 55/65.

Rule of Acquisition #3: "Never spend more for an acquisition than you have to."
"""

import argparse
import atexit
import csv
import datetime
import getpass
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unicodedata
import tty
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

VERSION = "2.0-deck"

# Colour-coding thresholds. Deck idles 38-46 C and games at 60-85 C; the
# firmware trips passive at 100 C and critical at 105 C (read from
# thermal_zone0 on this machine). Override with --warm/--hot or the env vars.
TEMP_HOT = float(os.environ.get("TEMP_HOT", 85.0))
TEMP_WARM = float(os.environ.get("TEMP_WARM", 70.0))

HWMON_ROOT = "/sys/class/hwmon"
DRM_ROOT = "/sys/class/drm"
CPU_ROOT = "/sys/devices/system/cpu"
PSU_ROOT = "/sys/class/power_supply"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0"

JOURNAL_TAG = "system_monitor"

# NOT /usr/local/bin: SteamOS protects the root subvolume with a BTRFS read-only
# PROPERTY, not a mount flag, so /proc/mounts and findmnt both report "rw" while
# every write still fails with EROFS. /var is a separate writable ext4 partition.
# The whole parent chain (/, /var, /var/lib) is root-owned 0755, which is what
# actually matters: a user-writable parent would let `deck` substitute the helper
# and gain root through the sudoers rule below.
FAN_HELPER_DIR = "/var/lib/deck-fan"
FAN_HELPER = FAN_HELPER_DIR + "/deck-fan-ctl"
# Privilege prefix for the helper, kept as a constant so tests can substitute
# a mock without needing real root.
FAN_SUDO = ["sudo", "-n"]
# "zz-", NOT the conventional "99-". sudo reads /etc/sudoers.d in LEXICAL order
# and the LAST matching rule wins. SteamOS ships /etc/sudoers.d/wheel containing
# "%wheel ALL=(ALL) ALL", and `deck` is in wheel - so anything sorting before
# "wheel" (every digit-prefixed name, since digits sort before letters) gets
# overridden and the NOPASSWD never takes effect.
FAN_SUDOERS = "/etc/sudoers.d/zz-deck-fan"
# Older builds of this script installed the 99- name; remove it on install/uninstall.
FAN_SUDOERS_LEGACY = "/etc/sudoers.d/99-deck-fan"
# Lives in $HOME so it SURVIVES a SteamOS update, unlike the two files above.
# deck-post-update.sh keys off it to reinstall them automatically.
FAN_MARKER = os.path.expanduser("~/.config/temp-deck/fan-helper-installed")
FAN_SERVICE = "jupiter-fan-control.service"
# The helper enforces this as an absolute backstop; --fan-off-timeout is
# clamped to it so the in-process timer can only ever be *earlier*.
FAN_DEADMAN_MAX = 600


def write_fan_marker() -> bool:
    """Record that this Deck opted into fan control. Best-effort; never raises.

    deck-post-update.sh reinstalls the helper + sudoers rule after a SteamOS
    update ONLY while this file exists, because the two system files it keys on
    are themselves wiped by the update and so cannot be the signal.

    Called from --install-fan-helper AND, as a self-heal, whenever the helper is
    found working without a marker. Without that second path a helper installed
    by a build predating this marker stays permanently unmarked, so the reapply
    silently skips and fan control breaks on the next OS update with no warning.
    """
    try:
        os.makedirs(os.path.dirname(FAN_MARKER), exist_ok=True)
        with open(FAN_MARKER, "w", encoding="utf-8") as f:
            f.write(
                "Installed by temp-deck.py --install-fan-helper.\n"
                "deck-post-update.sh reinstalls the helper while this exists.\n"
                "Run --uninstall-fan-helper to remove it.\n"
            )
        return True
    except OSError:
        return False


COLORS = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
    "orange": "\033[38;5;208m",
    "purple": "\033[38;5;141m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "underline": "\033[4m",
    "reverse": "\033[7m",
    "reset": "\033[0m",
}

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

SPARK = "▁▂▃▄▅▆▇█"

# --------------------------------------------------------------------------
# gpu_metrics
#
# Layout verified against the kernel header
#   drivers/gpu/drm/amd/include/kgd_pp_interface.h
# and cross-checked field-by-field against this machine (system_clock_counter
# vs /proc/uptime, average_gfx_voltage vs amdgpu vddgfx, current_coreclk vs
# scaling_cur_freq, gfx power vs V*I).
#
# Revisions 2.1 -> 2.4 are strictly ADDITIVE: identical offsets, each revision
# appending fields at the end.  So one offset table serves all of them; a field
# is only decoded when it actually fits inside the advertised structure_size.
#     v2_1 = 120 B   v2_2 = 128 B   v2_3 = 152 B   v2_4 = 168 B
# --------------------------------------------------------------------------

GM_U16 = {
    "temperature_gfx": 4,
    "temperature_soc": 6,
    "average_gfx_activity": 28,
    "average_mm_activity": 30,
    "average_socket_power": 40,
    "average_cpu_power": 42,
    "average_soc_power": 44,
    "average_gfx_power": 46,
    "average_gfxclk_frequency": 64,
    "average_socclk_frequency": 66,
    "average_uclk_frequency": 68,
    "average_fclk_frequency": 70,
    "average_vclk_frequency": 72,
    "average_dclk_frequency": 74,
    "current_gfxclk": 76,
    "current_socclk": 78,
    "current_uclk": 80,
    "current_fclk": 82,
    "current_vclk": 84,
    "current_dclk": 86,
    "fan_pwm": 112,
    "average_temperature_gfx": 128,
    "average_temperature_soc": 130,
    "average_cpu_voltage": 152,
    "average_soc_voltage": 154,
    "average_gfx_voltage": 156,
    "average_cpu_current": 158,
    "average_soc_current": 160,
    "average_gfx_current": 162,
}

GM_U16_ARRAY = {
    "temperature_core": (8, 8),
    "temperature_l3": (24, 2),
    "average_core_power": (48, 8),
    "current_coreclk": (88, 8),
    "current_l3clk": (104, 2),
    "average_temperature_core": (132, 8),
    "average_temperature_l3": (148, 2),
}

GM_U32 = {"throttle_status": 108}
GM_U64 = {"system_clock_counter": 32, "indep_throttle_status": 120}

U16_INVALID = 0xFFFF
U32_INVALID = 0xFFFFFFFF
U64_INVALID = 0xFFFFFFFFFFFFFFFF

# The ASIC-independent throttler bits Van Gogh actually populates, per
# vangogh_throttler_map in drivers/gpu/drm/amd/pm/swsmu/smu11/vangogh_ppt.c,
# named per SMU_THROTTLER_*_BIT in swsmu/inc/amdgpu_smu.h.
#
# Hitting a POWER limit on a handheld is normal and healthy - it is the APU
# running at its configured TDP, not a fault. Only THERMAL limits are alarming.
THROTTLE_BITS = {
    4: ("sustained power limit", "power"),
    5: ("fast power limit (burst)", "power"),
    6: ("slow power limit", "power"),
    7: ("APU power limit", "power"),
    16: ("GPU current limit", "current"),
    17: ("SoC current limit", "current"),
    19: ("VDD current limit", "current"),
    20: ("CVIP current limit", "current"),
    32: ("GPU temperature limit", "thermal"),
    33: ("CPU core temperature limit", "thermal"),
    37: ("SoC temperature limit", "thermal"),
}

FAN_AUTO, FAN_OFF, FAN_MAX = "auto", "off", "max"
FAN_CYCLE = [FAN_AUTO, FAN_OFF, FAN_MAX]


# --------------------------------------------------------------------------
# Safe low-level readers - a missing sensor yields None, never an exception
# and never a fake zero.
# --------------------------------------------------------------------------

def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def read_int(path: str, scale: float = 1.0) -> Optional[float]:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return int(raw) / scale
    except ValueError:
        return None


def read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def char_width(ch: str, following: str = "") -> int:
    """Columns one character occupies in a terminal.

    Emoji are the reason this exists: most render two columns wide, but many
    report an East Asian Width of "N" (❄ ⚠ ⚙ are all "N"), so counting
    codepoints under-measures every line that uses them and the padding drifts.
    A trailing U+FE0F is what forces emoji presentation, hence the lookahead.
    """
    cp = ord(ch)
    if cp in (0xFE0F, 0xFE0E, 0x200D) or unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    if cp >= 0x1F300:                      # pictographic planes
        return 2
    if following[:1] == "️":          # emoji-presentation variant
        return 2
    return 1


def icon(symbol: str) -> str:
    """An icon plus the gap that separates it from its label.

    Emoji that need U+FE0F to select emoji presentation (❄️ 🌡️ 🖥️) are drawn
    two cells wide by most terminals but only advance the cursor one, so the
    single space that follows gets overdrawn and the label looks glued to the
    icon. Emitting a second space restores the gap. Emoji from the
    pictographic planes (🔋 💽 🌀) advance correctly and need only one.
    """
    return symbol + ("  " if "️" in symbol else " ")


def visible_len(text: str) -> int:
    """Display columns, ignoring ANSI escapes and counting emoji as wide."""
    plain = ANSI_RE.sub("", text)
    return sum(char_width(c, plain[i + 1:i + 2]) for i, c in enumerate(plain))


def pack(parts: List[str], width: int, sep: str = "  ") -> List[str]:
    """Greedily pack pre-rendered chunks into lines that fit.

    Compact modes must not simply be truncated - dropping the right-hand end
    would silently hide the throttle status, which is the whole point of the
    line. Wrapping keeps every component visible on a narrow terminal.
    """
    lines: List[str] = []
    current = ""
    for part in parts:
        if not current:
            current = part
        elif visible_len(current) + visible_len(sep) + visible_len(part) <= width:
            current += sep + part
        else:
            lines.append(current)
            current = part
    if current:
        lines.append(current)
    return lines


def pad(text: str, width: int) -> str:
    """Left-justify to a VISIBLE width.

    An f-string's own `:<12` counts the ANSI escape bytes too, so colourised
    columns drift out of alignment the moment colour is enabled. Always pad
    through here instead.
    """
    return text + " " * max(0, width - visible_len(text))


def fit(text: str, width: int) -> str:
    """Truncate to a visible width without cutting an escape sequence in half."""
    if visible_len(text) <= width:
        return text
    out, shown = [], 0
    i = 0
    while i < len(text) and shown < width:
        m = ANSI_RE.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        w = char_width(text[i], text[i + 1:i + 2])
        if shown + w > width:
            break
        out.append(text[i])
        shown += w
        i += 1
    out.append(COLORS["reset"])
    return "".join(out)


# --------------------------------------------------------------------------
# Hardware discovery
# --------------------------------------------------------------------------

class HwmonMap:
    """Resolves hwmon devices by NAME.

    The hwmonN indexes are assigned in probe order and are NOT stable across
    boots, so hardcoding hwmon3/hwmon5 is a bug waiting for a reboot.
    """

    def __init__(self) -> None:
        self.by_name: Dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        found: Dict[str, str] = {}
        try:
            entries = sorted(os.listdir(HWMON_ROOT))
        except OSError:
            self.by_name = {}
            return
        for entry in entries:
            path = os.path.join(HWMON_ROOT, entry)
            name = read_text(os.path.join(path, "name"))
            if name:
                found.setdefault(name, path)
        self.by_name = found

    def path(self, *names: str) -> Optional[str]:
        for name in names:
            if name in self.by_name:
                return self.by_name[name]
        return None

    def attr(self, device: str, attr: str) -> Optional[str]:
        base = self.path(device)
        if not base:
            return None
        return os.path.join(base, attr)

    def value(self, device: str, attr: str, scale: float = 1.0) -> Optional[float]:
        p = self.attr(device, attr)
        if not p:
            return None
        return read_int(p, scale)

    def revalidate(self) -> None:
        """Re-scan if any cached path has disappeared (module reload, hotplug)."""
        for path in self.by_name.values():
            if not os.path.exists(path):
                self.refresh()
                return


class GpuMetrics:
    """Parser for the amdgpu SMU metrics table."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or self._discover()
        self.revision: Optional[Tuple[int, int]] = None
        self.size: Optional[int] = None
        self.unsupported_reason: Optional[str] = None

    @staticmethod
    def _discover() -> Optional[str]:
        try:
            cards = sorted(os.listdir(DRM_ROOT))
        except OSError:
            return None
        for card in cards:
            if not re.fullmatch(r"card\d+", card):
                continue
            candidate = os.path.join(DRM_ROOT, card, "device", "gpu_metrics")
            if os.path.exists(candidate):
                return candidate
        return None

    def read(self) -> Dict[str, Any]:
        """Return a decoded dict, or {} when unavailable/unsupported."""
        if not self.path:
            self.unsupported_reason = "no gpu_metrics node found"
            return {}
        blob = read_bytes(self.path)
        if not blob or len(blob) < 4:
            self.unsupported_reason = "gpu_metrics unreadable"
            return {}

        size, fmt_rev, content_rev = struct.unpack_from("<HBB", blob, 0)
        self.revision = (fmt_rev, content_rev)
        self.size = size

        if fmt_rev != 2:
            # Format 1 is the discrete-GPU layout with entirely different
            # offsets. Refuse rather than misreport.
            self.unsupported_reason = (
                f"gpu_metrics v{fmt_rev}.{content_rev} is not the APU layout"
            )
            return {}
        if size > len(blob) or size < 4:
            self.unsupported_reason = "gpu_metrics truncated"
            return {}

        self.unsupported_reason = None
        out: Dict[str, Any] = {"_revision": f"{fmt_rev}.{content_rev}"}

        def u16(off: int) -> Optional[int]:
            if off + 2 > size:
                return None
            (v,) = struct.unpack_from("<H", blob, off)
            return None if v == U16_INVALID else v

        for name, off in GM_U16.items():
            out[name] = u16(off)

        for name, (off, count) in GM_U16_ARRAY.items():
            values = [u16(off + 2 * i) for i in range(count)]
            # Trailing 0xFFFF entries mean "this core does not exist";
            # the Deck has 4 physical cores so cores 4-7 read as invalid.
            out[name] = [v for v in values if v is not None]

        for name, off in GM_U32.items():
            if off + 4 <= size:
                (v,) = struct.unpack_from("<I", blob, off)
                out[name] = None if v == U32_INVALID else v
            else:
                out[name] = None

        for name, off in GM_U64.items():
            if off + 8 <= size:
                (v,) = struct.unpack_from("<Q", blob, off)
                out[name] = None if v == U64_INVALID else v
            else:
                out[name] = None

        return out


# --------------------------------------------------------------------------
# Sensor aggregation
# --------------------------------------------------------------------------

class SensorHub:
    """Builds one immutable-ish snapshot per poll."""

    def __init__(self) -> None:
        self.hwmon = HwmonMap()
        self.gpu_metrics = GpuMetrics()
        self.drm_device = self._find_drm_device()
        self.model = read_text("/sys/devices/virtual/dmi/id/product_name") or "unknown"
        self.os_name = self._os_name()
        self.ncpu = self._count_cpus()
        self._prev_cpu: Optional[List[Tuple[int, int]]] = None
        self.trip_passive, self.trip_critical = self._trip_points()

    @staticmethod
    def _find_drm_device() -> Optional[str]:
        try:
            cards = sorted(os.listdir(DRM_ROOT))
        except OSError:
            return None
        for card in cards:
            if not re.fullmatch(r"card\d+", card):
                continue
            dev = os.path.join(DRM_ROOT, card, "device")
            if os.path.exists(os.path.join(dev, "gpu_busy_percent")):
                return dev
        return None

    @staticmethod
    def _os_name() -> str:
        raw = read_text("/etc/os-release") or ""
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', raw, re.M)
        if m:
            return m.group(1)
        m = re.search(r'^NAME="?([^"\n]+)"?', raw, re.M)
        return m.group(1) if m else "Linux"

    @staticmethod
    def _count_cpus() -> int:
        try:
            return os.cpu_count() or 1
        except Exception:
            return 1

    @staticmethod
    def _trip_points() -> Tuple[Optional[float], Optional[float]]:
        passive = critical = None
        try:
            for entry in os.listdir(THERMAL_ZONE):
                if not entry.endswith("_type"):
                    continue
                kind = read_text(os.path.join(THERMAL_ZONE, entry))
                temp = read_int(
                    os.path.join(THERMAL_ZONE, entry.replace("_type", "_temp")), 1000.0
                )
                if kind == "passive":
                    passive = temp
                elif kind == "critical":
                    critical = temp
        except OSError:
            pass
        return passive, critical

    # -- individual sections -------------------------------------------------

    def _cpu_busy(self) -> Optional[List[float]]:
        """Per-CPU busy percent, derived from deltas between polls."""
        raw = read_text("/proc/stat")
        if not raw:
            return None
        cur: List[Tuple[int, int]] = []
        for line in raw.splitlines():
            if not re.match(r"^cpu\d+ ", line):
                continue
            parts = [int(x) for x in line.split()[1:]]
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            cur.append((sum(parts), idle))
        if not cur:
            return None
        prev, self._prev_cpu = self._prev_cpu, cur
        if not prev or len(prev) != len(cur):
            return None
        out = []
        for (t1, i1), (t0, i0) in zip(cur, prev):
            dt, di = t1 - t0, i1 - i0
            out.append(0.0 if dt <= 0 else max(0.0, min(100.0, (dt - di) / dt * 100.0)))
        return out

    def _memory(self) -> Dict[str, Any]:
        raw = read_text("/proc/meminfo")
        out: Dict[str, Any] = {}
        if not raw:
            return out
        vals = {}
        for line in raw.splitlines():
            m = re.match(r"^(\w+):\s+(\d+)", line)
            if m:
                vals[m.group(1)] = int(m.group(2))
        total = vals.get("MemTotal")
        avail = vals.get("MemAvailable")
        if total:
            out["mem_total_mb"] = total // 1024
            if avail is not None:
                out["mem_used_mb"] = (total - avail) // 1024
                out["mem_percent"] = round((total - avail) / total * 100, 1)
        swap_total = vals.get("SwapTotal")
        swap_free = vals.get("SwapFree")
        if swap_total:
            out["swap_total_mb"] = swap_total // 1024
            if swap_free is not None:
                out["swap_used_mb"] = (swap_total - swap_free) // 1024
                out["swap_percent"] = round(
                    (swap_total - swap_free) / swap_total * 100, 1
                )
        return out

    @staticmethod
    def _pressure() -> Dict[str, float]:
        out = {}
        for kind in ("cpu", "io", "memory"):
            raw = read_text(f"/proc/pressure/{kind}")
            if not raw:
                continue
            m = re.search(r"some avg10=([\d.]+)", raw)
            if m:
                out[kind] = float(m.group(1))
        return out

    def _battery(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        bat = os.path.join(PSU_ROOT, "BAT1")
        if not os.path.isdir(bat):
            for name in sorted(os.listdir(PSU_ROOT)) if os.path.isdir(PSU_ROOT) else []:
                cand = os.path.join(PSU_ROOT, name)
                if read_text(os.path.join(cand, "type")) == "Battery":
                    bat = cand
                    break
        if not os.path.isdir(bat):
            return out

        out["capacity"] = read_int(os.path.join(bat, "capacity"))
        out["status"] = read_text(os.path.join(bat, "status"))
        out["cycles"] = read_int(os.path.join(bat, "cycle_count"))
        volts = read_int(os.path.join(bat, "voltage_now"), 1e6)
        amps = read_int(os.path.join(bat, "current_now"), 1e6)
        charge_now = read_int(os.path.join(bat, "charge_now"), 1e6)
        charge_full = read_int(os.path.join(bat, "charge_full"), 1e6)
        design = read_int(os.path.join(bat, "charge_full_design"), 1e6)

        out["volts"] = volts
        out["amps"] = amps
        if volts is not None and amps is not None:
            out["watts"] = round(volts * amps, 2)
        if charge_full and design:
            out["health_percent"] = round(charge_full / design * 100, 1)
        out["charge_now_ah"] = charge_now
        out["charge_full_ah"] = charge_full

        # Time remaining, from amp-hours and the present current draw.
        if amps and amps > 0.01 and charge_now is not None:
            status = (out.get("status") or "").lower()
            if status == "charging" and charge_full is not None:
                remaining = max(0.0, charge_full - charge_now)
                out["time_to_full_h"] = remaining / amps
            elif status == "discharging":
                out["time_to_empty_h"] = charge_now / amps

        ac = os.path.join(PSU_ROOT, "ACAD")
        out["ac_online"] = read_int(os.path.join(ac, "online"))
        return out

    def _fan(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out["rpm"] = self.hwmon.value("steamdeck_hwmon", "fan1_input")
        out["target"] = self.hwmon.value("steamdeck_hwmon", "fan1_target")
        out["fault"] = self.hwmon.value("steamdeck_hwmon", "fan1_fault")
        if out["rpm"] is None:
            out["rpm"] = self.hwmon.value("jupiter", "fan1_input")
            out["target"] = self.hwmon.value("jupiter", "fan1_target")
        return out

    def _charger(self) -> Dict[str, Any]:
        """USB-PD contract negotiated with the charger."""
        out: Dict[str, Any] = {}
        volts = self.hwmon.value("steamdeck_hwmon", "in0_input", 1000.0)
        amps = self.hwmon.value("steamdeck_hwmon", "curr1_input", 1000.0)
        out["pd_volts"] = volts
        out["pd_amps"] = amps
        if volts is not None and amps is not None:
            out["pd_watts"] = round(volts * amps, 1)
        return out

    def _gpu(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if not self.drm_device:
            return out
        out["busy_percent"] = read_int(
            os.path.join(self.drm_device, "gpu_busy_percent")
        )
        vram_used = read_int(os.path.join(self.drm_device, "mem_info_vram_used"))
        vram_total = read_int(os.path.join(self.drm_device, "mem_info_vram_total"))
        gtt_used = read_int(os.path.join(self.drm_device, "mem_info_gtt_used"))
        if vram_used is not None:
            out["vram_used_mb"] = round(vram_used / 1048576)
        if vram_total is not None:
            out["vram_total_mb"] = round(vram_total / 1048576)
        if gtt_used is not None:
            out["gtt_used_mb"] = round(gtt_used / 1048576)
        out["perf_level"] = read_text(
            os.path.join(self.drm_device, "power_dpm_force_performance_level")
        )
        return out

    @staticmethod
    def _storage() -> List[Dict[str, Any]]:
        out = []
        mounts = [("/", "root"), (os.path.expanduser("~"), "home")]
        media = "/run/media/deck"
        if os.path.isdir(media):
            try:
                for entry in sorted(os.listdir(media)):
                    mounts.append((os.path.join(media, entry), entry))
            except OSError:
                pass
        seen = set()
        for path, label in mounts:
            try:
                st = os.statvfs(path)
            except OSError:
                continue
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            if not total or (st.f_blocks, st.f_frsize) in seen:
                continue
            seen.add((st.f_blocks, st.f_frsize))
            out.append(
                {
                    "label": label,
                    "path": path,
                    "total_gb": round(total / 1e9, 1),
                    "free_gb": round(free / 1e9, 1),
                    "used_percent": round((total - free) / total * 100, 1),
                }
            )
        return out

    @staticmethod
    def _wifi() -> Optional[float]:
        raw = read_text("/proc/net/wireless")
        if not raw:
            return None
        for line in raw.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    return float(parts[3].rstrip("."))
                except ValueError:
                    return None
        return None

    # -- the snapshot --------------------------------------------------------

    def sample(self) -> Dict[str, Any]:
        self.hwmon.revalidate()
        gm = self.gpu_metrics.read()
        snap: Dict[str, Any] = {
            "timestamp": time.time(),
            "model": self.model,
            "os": self.os_name,
        }

        # --- temperatures ---------------------------------------------------
        # Preference order for "the APU temperature": the SMU's own gfx sensor,
        # then the amdgpu edge sensor, then the ACPI board sensor as a distant
        # fallback (it reads several degrees off and is NOT the APU).
        apu = None
        if gm.get("temperature_gfx") is not None:
            apu = gm["temperature_gfx"] / 100.0
        if apu is None:
            apu = self.hwmon.value("amdgpu", "temp1_input", 1000.0)
        if apu is None:
            apu = read_int(os.path.join(THERMAL_ZONE, "temp"), 1000.0)
        snap["apu_temp"] = round(apu, 2) if apu is not None else None

        snap["soc_temp"] = (
            round(gm["temperature_soc"] / 100.0, 2)
            if gm.get("temperature_soc") is not None
            else None
        )
        cores = gm.get("temperature_core") or []
        snap["core_temps"] = [round(c / 100.0, 2) for c in cores]
        snap["cpu_temp"] = max(snap["core_temps"]) if snap["core_temps"] else None
        l3 = gm.get("temperature_l3") or []
        snap["l3_temps"] = [round(c / 100.0, 2) for c in l3]
        snap["board_temp"] = read_int(os.path.join(THERMAL_ZONE, "temp"), 1000.0)
        snap["nvme_temp"] = self.hwmon.value("nvme", "temp1_input", 1000.0)
        snap["nvme_crit"] = self.hwmon.value("nvme", "temp1_crit", 1000.0)
        snap["wifi_temp"] = self.hwmon.value("ath11k_hwmon", "temp1_input", 1000.0)
        snap["battery_temp"] = self.hwmon.value(
            "steamdeck_hwmon", "temp1_input", 1000.0
        )
        snap["trip_passive"] = self.trip_passive
        snap["trip_critical"] = self.trip_critical

        # --- cpu ------------------------------------------------------------
        snap["cpu_freq_mhz"] = read_int(
            os.path.join(CPU_ROOT, "cpu0/cpufreq/scaling_cur_freq"), 1000.0
        )
        snap["governor"] = read_text(
            os.path.join(CPU_ROOT, "cpu0/cpufreq/scaling_governor")
        )
        snap["scaling_driver"] = read_text(
            os.path.join(CPU_ROOT, "cpu0/cpufreq/scaling_driver")
        )
        snap["epp"] = read_text(
            os.path.join(CPU_ROOT, "cpu0/cpufreq/energy_performance_preference")
        )
        snap["core_clocks"] = gm.get("current_coreclk") or []
        snap["core_power_w"] = [
            round(p / 1000.0, 2) for p in (gm.get("average_core_power") or [])
        ]
        snap["cpu_busy"] = self._cpu_busy()

        loads = read_text("/proc/loadavg")
        snap["load"] = (
            [float(x) for x in loads.split()[:3]] if loads else None
        )
        uptime_raw = read_text("/proc/uptime")
        snap["uptime_s"] = float(uptime_raw.split()[0]) if uptime_raw else None

        # --- power ----------------------------------------------------------
        socket = gm.get("average_socket_power")
        snap["apu_watts"] = round(socket / 1000.0, 2) if socket is not None else None
        if snap["apu_watts"] is None:
            snap["apu_watts"] = self.hwmon.value("amdgpu", "power1_average", 1e6)
        for key, field in (
            ("cpu_watts", "average_cpu_power"),
            ("soc_watts", "average_soc_power"),
            ("gfx_watts", "average_gfx_power"),
        ):
            v = gm.get(field)
            snap[key] = round(v / 1000.0, 2) if v is not None else None
        # slowPPT / fastPPT running averages, the pair deck-temps.sh reports as
        # "pwr slow/fast W (cap N)". These are the amdgpu driver's own averaging
        # windows and read differently from the SMU's average_socket_power above,
        # so all three are kept rather than collapsed into one number.
        snap["ppt_slow_w"] = self.hwmon.value("amdgpu", "power1_average", 1e6)
        snap["ppt_fast_w"] = self.hwmon.value("amdgpu", "power2_average", 1e6)
        snap["ppt_slow_cap_w"] = self.hwmon.value("amdgpu", "power1_cap", 1e6)
        snap["ppt_fast_cap_w"] = self.hwmon.value("amdgpu", "power2_cap", 1e6)
        snap["ppt_cap_max_w"] = self.hwmon.value("amdgpu", "power1_cap_max", 1e6)

        # --- gpu ------------------------------------------------------------
        gpu = self._gpu()
        snap.update({f"gpu_{k}": v for k, v in gpu.items()})
        gfxclk = gm.get("current_gfxclk")
        if gfxclk is None:
            f = self.hwmon.value("amdgpu", "freq1_input", 1e6)
            gfxclk = round(f) if f is not None else None
        snap["gpu_clock_mhz"] = gfxclk
        snap["uclk_mhz"] = gm.get("current_uclk")
        snap["fclk_mhz"] = gm.get("current_fclk")

        # --- throttle -------------------------------------------------------
        snap["throttle_raw"] = gm.get("indep_throttle_status")
        snap["throttle_asic_raw"] = gm.get("throttle_status")
        snap["gpu_metrics_rev"] = gm.get("_revision")
        snap["gpu_metrics_error"] = self.gpu_metrics.unsupported_reason
        snap["throttle_reasons"] = decode_throttle(snap["throttle_raw"])

        # --- the rest -------------------------------------------------------
        snap.update(self._memory())
        snap["pressure"] = self._pressure()
        snap["battery"] = self._battery()
        snap["fan"] = self._fan()
        snap["charger"] = self._charger()
        snap["storage"] = self._storage()
        snap["wifi_dbm"] = self._wifi()
        return snap


def decode_throttle(raw: Optional[int]) -> List[Tuple[str, str]]:
    """[(human reason, kind)] for each asserted limiter bit."""
    if not raw:
        return []
    out = []
    for bit, (desc, kind) in sorted(THROTTLE_BITS.items()):
        if raw & (1 << bit):
            out.append((desc, kind))
    return out


# --------------------------------------------------------------------------
# Fan control
# --------------------------------------------------------------------------

FAN_HELPER_SRC = r'''#!/bin/bash
# deck-fan-ctl - privileged Steam Deck fan helper.
#
# Installed root-owned and invoked through a tightly-scoped sudoers rule, so
# that the unprivileged monitor never needs root itself. Accepts exactly one
# of: auto | off | max | status.
#
# On this Deck's BIOS the SteamOS fan daemon uses its "standard BIOS" path,
# where fan1_target = 0 means "hand control back to the EC" and NOT "off".
# The minimum commanded speed is 10, which measures as 0 rpm.
#
# Installed by temp-deck.py --install-fan-helper. Lives on /var (writable);
# the SteamOS read-only root would reject /usr/local/bin. Wiped by OS updates.
set -uo pipefail

SERVICE=jupiter-fan-control.service
DEADMAN=deck-fan-deadman
DEADMAN_SECS=600          # absolute backstop; the caller cannot extend this
FAN_MIN=10
FAN_MAX=7300
SAFE_MAX_TEMP=75          # refuse to stop the fan at or above this APU temp

die() { echo "deck-fan-ctl: $*" >&2; exit 1; }

# Validate the verb FIRST, before touching any hardware, so an unknown or
# hostile argument is rejected on its own terms rather than falling through
# to some unrelated error.
ACTION="${1:-}"
case "$ACTION" in
    auto|off|max|status) ;;
    *) die "usage: deck-fan-ctl {auto|off|max|status}" ;;
esac
[ "$#" -eq 1 ] || die "exactly one argument expected"

find_hwmon() {   # $1 = hwmon name
    local d n
    for d in /sys/class/hwmon/hwmon*; do
        [ -r "$d/name" ] || continue
        n=$(cat "$d/name" 2>/dev/null)
        if [ "$n" = "$1" ]; then echo "$d"; return 0; fi
    done
    return 1
}

FANDIR=$(find_hwmon steamdeck_hwmon) || FANDIR=$(find_hwmon jupiter) \
    || die "no steamdeck_hwmon/jupiter hwmon device found"
[ -w "$FANDIR/fan1_target" ] || die "$FANDIR/fan1_target not writable (need root)"

apu_temp() {     # centidegrees -> whole degrees C, or empty
    local d t
    d=$(find_hwmon amdgpu) || return 1
    t=$(cat "$d/temp1_input" 2>/dev/null) || return 1
    echo $(( t / 1000 ))
}

arm_deadman() {
    systemctl stop "${DEADMAN}.timer" >/dev/null 2>&1
    systemd-run --unit="$DEADMAN" --on-active="$DEADMAN_SECS" \
        --description="Return Steam Deck fan to SteamOS control" \
        /usr/bin/systemctl start "$SERVICE" >/dev/null 2>&1 \
        || echo "deck-fan-ctl: warning: could not arm deadman timer" >&2
}

cancel_deadman() {
    systemctl stop "${DEADMAN}.timer" >/dev/null 2>&1
    true
}

set_target() { echo "$1" > "$FANDIR/fan1_target"; }

case "$ACTION" in
  auto)
    cancel_deadman
    set_target 0
    systemctl start "$SERVICE" >/dev/null 2>&1
    echo "auto"
    ;;
  off)
    t=$(apu_temp) || t=""
    if [ -n "$t" ] && [ "$t" -ge "$SAFE_MAX_TEMP" ]; then
        die "refusing: APU is ${t}C (limit ${SAFE_MAX_TEMP}C)"
    fi
    systemctl stop "$SERVICE" >/dev/null 2>&1
    set_target "$FAN_MIN"
    arm_deadman
    echo "off"
    ;;
  max)
    systemctl stop "$SERVICE" >/dev/null 2>&1
    set_target "$FAN_MAX"
    arm_deadman
    echo "max"
    ;;
  status)
    printf 'service=%s target=%s actual=%s deadman=%s\n' \
        "$(systemctl is-active "$SERVICE" 2>/dev/null)" \
        "$(cat "$FANDIR/fan1_target" 2>/dev/null)" \
        "$(cat "$FANDIR/fan1_input" 2>/dev/null)" \
        "$(systemctl is-active "${DEADMAN}.timer" 2>/dev/null)"
    ;;
esac
'''


class FanController:
    """Drives the fan through the privileged helper, with hard interlocks.

    Forcing the fan OFF is the only dangerous direction: the SteamOS daemon
    exists to hold the APU under 90 C and the firmware trips passive at 100 C.
    Every path out of OFF is covered - temperature cutoff, deadman timer,
    clean exit, signals, and an out-of-process systemd timer for the case
    where this program is killed outright.
    """

    def __init__(self, enabled: bool, max_temp: float, timeout: int) -> None:
        self.enabled = enabled
        self.max_temp = max_temp
        self.timeout = max(10, min(timeout, FAN_DEADMAN_MAX))
        self.mode = FAN_AUTO
        self.since: Optional[float] = None
        self.message: Optional[str] = None
        self.message_until = 0.0
        self._reverted_by_guard = False
        if enabled:
            # Only arm the exit hook when this instance can actually touch the
            # fan; main() replaces the monitor's placeholder controller.
            atexit.register(self.restore)

    @property
    def installed(self) -> bool:
        return os.path.exists(FAN_HELPER)

    def available(self) -> Tuple[bool, str, str]:
        """(usable, message, state).

        state is one of: ready | disabled | not_installed | needs_auth.

        The three failure modes are genuinely different and must not be
        collapsed. Telling someone to run --install-fan-helper when the files
        are already on disk sends them round a loop that cannot fix anything;
        a passwordless probe can fail purely because sudo's cached credential
        expired, which reinstalling does not address.
        """
        if not self.enabled:
            return False, "fan control disabled (pass --allow-fan-control)", "disabled"
        if not self.installed:
            return False, "fan helper not installed (run --install-fan-helper)", \
                   "not_installed"
        rc = subprocess.run(
            FAN_SUDO + [FAN_HELPER, "status"], capture_output=True, text=True
        )
        if rc.returncode == 0:
            # Self-heal: the helper works, so this Deck IS opted in. If the marker
            # is absent (installed by a build predating it, or a failed write) the
            # post-update reapply would silently skip and fan control would break
            # on the next SteamOS update. Cheap existence check keeps this off the
            # per-tick write path.
            if not os.path.exists(FAN_MARKER):
                write_fan_marker()
            return True, "ready", "ready"
        if os.path.exists(FAN_SUDOERS) or os.path.exists(FAN_SUDOERS_LEGACY):
            # Installed correctly; sudo simply will not act without a password
            # right now. Re-authenticating fixes it, reinstalling does not.
            return False, "sudo password needed - press o", "needs_auth"
        return False, "sudoers rule missing (run --install-fan-helper)", \
               "not_installed"

    def authenticate(self) -> Tuple[bool, str]:
        """Refresh sudo's credential cache interactively.

        The caller is responsible for handing the terminal back to a normal
        cooked state first, since this prompts.
        """
        try:
            rc = subprocess.run(["sudo", "-v"], timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"sudo failed: {exc}"
        if rc.returncode != 0:
            return False, "sudo authentication cancelled"
        return True, "unlocked"

    def _invoke(self, mode: str) -> Tuple[bool, str]:
        try:
            rc = subprocess.run(
                FAN_SUDO + [FAN_HELPER, mode],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"helper failed: {exc}"
        if rc.returncode != 0:
            return False, (rc.stderr or rc.stdout or "helper refused").strip()
        return True, (rc.stdout or "").strip()

    def notify(self, text: str, seconds: float = 6.0) -> None:
        self.message = text
        self.message_until = time.time() + seconds

    def active_message(self) -> Optional[str]:
        if self.message and time.time() < self.message_until:
            return self.message
        return None

    def cycle(self, apu_temp: Optional[float]) -> None:
        ok, why, _state = self.available()
        if not ok:
            self.notify(why)
            return
        nxt = FAN_CYCLE[(FAN_CYCLE.index(self.mode) + 1) % len(FAN_CYCLE)]
        refusal = None
        if nxt == FAN_OFF and apu_temp is not None and apu_temp >= self.max_temp:
            refusal = (
                f"OFF refused at {apu_temp:.0f}°C - MAX instead"
            )
            # Skip past OFF rather than stall the cycle on a key that then
            # appears to do nothing every time it is pressed.
            nxt = FAN_MAX
        self.set(nxt)
        if refusal:
            # set() posts its own message; the refusal is the more important one.
            self.notify(refusal, 10.0)

    def set(self, mode: str) -> None:
        ok, out = self._invoke(mode)
        if not ok:
            self.notify(out)
            return
        self.mode = mode
        self.since = time.time() if mode != FAN_AUTO else None
        self._reverted_by_guard = False
        if mode == FAN_OFF:
            self.notify(f"fan OFF, auto-revert {self.timeout}s")
        elif mode == FAN_MAX:
            self.notify(f"fan MAX, auto-revert {self.timeout}s")
        else:
            self.notify("fan back to SteamOS control")

    def guard(self, apu_temp: Optional[float]) -> None:
        """Called every poll. Reverts to AUTO when any limit is reached."""
        if self.mode == FAN_AUTO or self.since is None:
            return
        elapsed = time.time() - self.since
        if self.mode == FAN_OFF and apu_temp is not None and apu_temp >= self.max_temp:
            self.set(FAN_AUTO)
            self.notify(f"AUTO restored - APU hit {apu_temp:.0f}°C", 10.0)
            self._reverted_by_guard = True
            return
        if elapsed >= self.timeout:
            self.set(FAN_AUTO)
            self.notify(f"AUTO restored - {self.mode} timed out", 10.0)
            self._reverted_by_guard = True

    def remaining(self) -> Optional[int]:
        if self.mode == FAN_AUTO or self.since is None:
            return None
        return max(0, int(self.timeout - (time.time() - self.since)))

    def restore(self) -> None:
        """Idempotent; registered with atexit and called from the finally block."""
        if self.mode == FAN_AUTO:
            return
        try:
            self._invoke(FAN_AUTO)
        finally:
            self.mode = FAN_AUTO
            self.since = None

    def label(self) -> str:
        if self.mode == FAN_AUTO:
            return "AUTO"
        left = self.remaining()
        mins, secs = divmod(left or 0, 60)
        return f"{self.mode.upper()} {mins}:{secs:02d} left"


def install_fan_helper(assume_yes: bool = False) -> int:
    """Install the root-owned helper and its sudoers rule. Explicitly opt-in."""
    user = getpass.getuser()
    verbs = ("auto", "off", "max", "status")
    alias = ", \\\n".ljust(24).join(f"{FAN_HELPER} {v}" for v in verbs)
    sudoers = (
        "# Installed by temp-deck.py --install-fan-helper\n"
        "# Grants exactly four fixed commands, nothing else.\n"
        "#\n"
        "# The bare NOPASSWD user spec is not enough on SteamOS: sudo applies\n"
        "# last-match-wins across user specs, and SteamOS ships\n"
        "# /etc/sudoers.d/wheel with '%wheel ALL=(ALL) ALL', which matches every\n"
        "# command and can be evaluated after this file. 'Defaults!<Cmnd_Alias>\n"
        "# !authenticate' is applied SEPARATELY from user-spec ordering, so it\n"
        "# still takes effect. Both lines are kept: the spec scopes WHAT may run,\n"
        "# the Defaults line drops the password prompt for exactly those commands.\n"
        f"Cmnd_Alias DECK_FAN = {alias}\n"
        "Defaults!DECK_FAN !authenticate\n"
        f"{user} ALL=(root) NOPASSWD: DECK_FAN\n"
    )

    print("This installs two SYSTEM files (needs sudo, will prompt for a password):")
    print(f"  {FAN_HELPER}   root:root 0755  privileged fan helper")
    print(f"  {FAN_SUDOERS}  root:root 0440  four NOPASSWD commands for {user}")
    print()
    print("Both are wiped by SteamOS updates and must be reapplied afterwards.")
    print(f"To undo:  {sys.argv[0]} --uninstall-fan-helper")
    print()
    if not assume_yes:
        try:
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted. Nothing was changed.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nAborted. Nothing was changed.")
            return 1

    # Stage inside a private 0700 directory. These files are handed straight to
    # `sudo install` into root-owned locations, so a predictable path in
    # world-writable /tmp would let a local attacker pre-create or symlink them
    # and choose what lands in /var/lib/deck-fan and /etc/sudoers.d.
    tmp_dir = tempfile.mkdtemp(prefix="deck-fan-install.")
    os.chmod(tmp_dir, 0o700)
    tmp_helper = os.path.join(tmp_dir, "deck-fan-ctl")
    tmp_sudoers = os.path.join(tmp_dir, "99-deck-fan")
    try:
        with open(tmp_helper, "w", encoding="utf-8") as f:
            f.write(FAN_HELPER_SRC)
        with open(tmp_sudoers, "w", encoding="utf-8") as f:
            f.write(sudoers)

        # Validate the sudoers syntax BEFORE it is anywhere near /etc, so a
        # typo can never lock sudo out.
        check = subprocess.run(
            ["sudo", "visudo", "-c", "-f", tmp_sudoers],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            print("sudoers validation FAILED - nothing installed:")
            print(check.stdout or check.stderr)
            return 1

        cmds = [
            # Drop the old digit-prefixed name if a previous version left one:
            # it sorts BEFORE /etc/sudoers.d/wheel and is silently overridden.
            ["sudo", "rm", "-f", FAN_SUDOERS_LEGACY],
            ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0755",
             FAN_HELPER_DIR],
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "0755",
             tmp_helper, FAN_HELPER],
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "0440",
             tmp_sudoers, FAN_SUDOERS],
        ]
        for cmd in cmds:
            rc = subprocess.run(cmd, capture_output=True, text=True)
            if rc.returncode != 0:
                err = (rc.stderr or rc.stdout or "").strip()
                print(f"FAILED: {' '.join(cmd)}")
                if err:
                    print(f"        {err}")
                if "Read-only file system" in err:
                    # SteamOS marks the root subvolume read-only via a BTRFS
                    # PROPERTY, so mount flags still claim "rw". Say so plainly
                    # rather than leaving a bare EROFS.
                    print("\nThat path is on the SteamOS read-only root. It reports"
                          " itself as writable\nbut is not. Nothing was installed;"
                          " no partial state was left behind.")
                return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    verify = subprocess.run(
        FAN_SUDO + [FAN_HELPER, "status"], capture_output=True, text=True
    )
    if verify.returncode == 0:
        write_fan_marker()
        print("Installed. Passwordless check:", verify.stdout.strip())
        print("Run the monitor with --allow-fan-control to enable the 'o' key.")
        return 0
    print("Installed, but the passwordless check failed:",
          (verify.stderr or verify.stdout).strip())
    return 1


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

class Renderer:
    def __init__(self, monitor: "QuarkSystemMonitor") -> None:
        self.m = monitor

    # -- primitives ----------------------------------------------------------

    def c(self, text: str, *colors: str) -> str:
        return self.m.colorize(text, *colors)

    def temp_color(self, temp: Optional[float]) -> List[str]:
        if temp is None:
            return ["dim"]
        if temp >= self.m.temp_hot:
            return ["red", "bold"]
        if temp >= self.m.temp_warm:
            return ["yellow"]
        return ["green"]

    def temp_str(self, temp: Optional[float], suffix: str = "°C") -> str:
        if temp is None:
            return self.c("--", "dim")
        return self.c(f"{temp:.1f}{suffix}", *self.temp_color(temp))

    def bar(self, percent: Optional[float], width: int = 20,
            good_low: bool = True) -> str:
        if percent is None:
            return "[" + self.c("?" * width, "dim") + "]"
        percent = max(0.0, min(100.0, percent))
        filled = int(round(width * percent / 100.0))
        body = "█" * filled + "░" * (width - filled)
        if good_low:
            color = "red" if percent > 85 else "yellow" if percent > 65 else "green"
        else:
            color = "green" if percent > 50 else "yellow" if percent > 20 else "red"
        return "[" + self.c(body, color) + "]"

    def sparkline(self, values: List[float], width: int) -> str:
        if len(values) < 2 or width < 2:
            return ""
        vals = values[-width:]
        lo, hi = min(vals), max(vals)
        span = hi - lo if hi > lo else 1.0
        return "".join(
            SPARK[min(len(SPARK) - 1, int((v - lo) / span * (len(SPARK) - 1)))]
            for v in vals
        )

    def graph(self, width: int, height: int = 5) -> List[str]:
        """Temperature history. Scale is computed over the WINDOW ACTUALLY DRAWN."""
        hist = list(self.m.temp_history)
        if len(hist) < 2:
            return [self.c("Collecting temperature history...", "dim")]
        label_w = 9
        plot_w = max(4, width - label_w)
        window = hist[-plot_w:]
        lo, hi = min(window), max(window)
        span = hi - lo if hi > lo else 1.0

        rows = [[" "] * len(window) for _ in range(height)]
        for x, val in enumerate(window):
            level = int(round((val - lo) / span * (height - 1)))
            rows[height - 1 - level][x] = "█"

        out = []
        for i, row in enumerate(rows):
            value_here = hi - (i * span / (height - 1)) if height > 1 else hi
            label = self.c(f"{value_here:5.1f}°C │", "dim")
            body = "".join(row)
            color = self.temp_color(value_here)[0]
            out.append(label + self.c(body, color))
        return out

    # -- reusable component strings -----------------------------------------

    def throttle_text(self, snap: Dict[str, Any]) -> str:
        if snap.get("gpu_metrics_error"):
            return self.c(f"unavailable ({snap['gpu_metrics_error']})", "dim")
        raw = snap.get("throttle_raw")
        if raw is None:
            return self.c("unavailable", "dim")
        reasons = snap.get("throttle_reasons") or []
        if not reasons:
            return self.c("✓ not limited", "green")
        thermal = [d for d, k in reasons if k == "thermal"]
        others = [d for d, k in reasons if k != "thermal"]
        if thermal:
            return self.c("⚠ THERMAL: " + ", ".join(thermal[:2]), "red", "bold")
        # Power/current limits are the APU doing its job on a handheld.
        return self.c("limited by " + ", ".join(others[:2]), "yellow")

    def trend_text(self) -> str:
        hist = list(self.m.temp_history)
        if len(hist) < 3:
            return self.c("Trend: warming up...", "dim")
        recent = hist[-10:]
        delta = recent[-1] - recent[0]
        span = (len(recent) - 1) * self.m.update_interval
        if delta > 0.5:
            arrow, color = "↑", "red"
        elif delta < -0.5:
            arrow, color = "↓", "green"
        else:
            arrow, color = "→", "cyan"
        window = format_duration(datetime.timedelta(seconds=span))
        return f"Trend: {self.c(f'{arrow} {delta:+.1f}C', color, 'bold')} over {window}"

    def fan_text(self, snap: Dict[str, Any]) -> str:
        notice = self.fan_message()
        if notice:
            return notice
        fan = snap.get("fan") or {}
        rpm, target = fan.get("rpm"), fan.get("target")
        rpm_s = "--" if rpm is None else f"{rpm:.0f} rpm"
        tgt_s = "" if target is None else f" (target {target:.0f})"
        color = "cyan" if (rpm or 0) > 0 else "dim"
        text = self.c(rpm_s, color) + self.c(tgt_s, "dim")
        if self.m.fan.enabled:
            mode = self.m.fan.label()
            mcolor = "green" if mode == "AUTO" else "yellow"
            text += "  " + self.c(f"[{mode}]", mcolor, "bold")
        if fan.get("fault"):
            text += " " + self.c("FAULT", "red", "bold")
        return text

    def fan_message(self) -> Optional[str]:
        """Transient fan notice, drawn OVER the fan line while it is showing.

        It replaces the fan readout rather than being appended to it: the row
        count never changes, so the layout does not jump, and the line does not
        grow into something that has to be truncated. The readout comes back
        when the notice expires a few seconds later.
        """
        msg = self.m.fan.active_message()
        return self.c("⚠ " + msg, "yellow", "bold") if msg else None

    def battery_text(self, snap: Dict[str, Any]) -> str:
        bat = snap.get("battery") or {}
        cap = bat.get("capacity")
        status = (bat.get("status") or "").lower()
        watts = bat.get("watts")
        parts = []
        if cap is not None:
            color = "red" if cap < 15 else "yellow" if cap < 30 else "green"
            parts.append(self.c(f"{cap:.0f}%", color, "bold"))
            parts.append(self.bar(cap, 16, good_low=False))
        if status == "charging":
            label = f"charging {watts:.1f}W" if watts else "charging"
            parts.append(self.c(label, "green"))
        elif status == "discharging":
            label = f"draining {watts:.1f}W" if watts else "draining"
            parts.append(self.c(label, "yellow"))
            hours = bat.get("time_to_empty_h")
            if hours:
                parts.append(self.c(f"~{format_hours(hours)} left", "dim"))
        elif bat.get("ac_online"):
            # Deck reports "Not charging" while sitting at Steam's charge limit.
            parts.append(self.c("on AC (at charge limit)", "cyan"))
        else:
            parts.append(self.c(status or "idle", "dim"))
        hours = bat.get("time_to_full_h")
        if hours and status == "charging":
            parts.append(self.c(f"~{format_hours(hours)} to full", "dim"))
        return "  ".join(parts)

    # -- component dictionary used by the legacy line modes ------------------

    def components(self, snap: Dict[str, Any], width: int) -> Dict[str, str]:
        comp: Dict[str, str] = {}
        temp = snap.get("apu_temp")
        temp_icon = "\U0001f525" if temp is not None and temp >= self.m.temp_hot else (
            "\U0001f321️" if temp is not None and temp >= self.m.temp_warm
            else "❄️"
        )
        thresholds = self.c(
            f"(warm@{self.m.temp_warm:g}° hot@{self.m.temp_hot:g}°)", "dim"
        )
        comp["temp"] = f"{icon(temp_icon)}APU: {self.temp_str(temp)} {thresholds}"
        comp["trend"] = "\U0001f4c9 " + self.trend_text()

        freq = snap.get("cpu_freq_mhz")
        gov = snap.get("governor") or "?"
        if freq is not None:
            comp["perf"] = (
                f"⚡ {self.c(f'{freq/1000:.2f}GHz', 'blue')} "
                f"[{self.c(gov, 'cyan')}]"
            )

        if snap.get("mem_percent") is not None:
            comp["mem"] = (
                f"\U0001f4be Mem: {self.bar(snap['mem_percent'], 10)} "
                f"{snap['mem_percent']:.0f}%"
            )

        if snap.get("load"):
            parts = []
            for value in snap["load"]:
                color = "red" if value > 8 else "yellow" if value > 4 else "green"
                parts.append(self.c(f"{value:.2f}", color))
            comp["load"] = "\U0001f4ca Load: " + "/".join(parts)

        comp["throttle"] = "\U0001f6a6 " + self.throttle_text(snap)
        comp["cpu"] = (
            "\U0001f9e0 CPU: " + self.temp_str(snap.get("cpu_temp"))
            + self.c(
                f"  cores {' '.join(f'{t:.0f}°' for t in snap.get('core_temps') or []) or '--'}",
                "dim",
            )
        )
        gpu_busy = snap.get("gpu_busy_percent")
        gpu_line = "\U0001f3ae GPU: " + self.c(
            f"{snap.get('gpu_clock_mhz') or '--'}MHz", "blue"
        )
        if gpu_busy is not None:
            gpu_line += f"  busy {gpu_busy:.0f}%"
        comp["gpu"] = gpu_line
        watts = snap.get("apu_watts")
        cap = snap.get("ppt_slow_cap_w")
        if watts is not None:
            pct = (watts / cap * 100) if cap else None
            comp["power"] = (
                f"\U0001f50c Power: {self.c(f'{watts:.1f}W', 'magenta')} "
                + (f"{self.bar(pct, 14)} cap {cap:.0f}W" if cap else "")
            )
        comp["battery"] = "\U0001f50b " + self.battery_text(snap)
        comp["fan"] = "\U0001f300 Fan: " + self.fan_text(snap)
        # No row icon: each reading now carries its own.
        comp["temps"] = self.temps_text(snap)
        cap = (snap.get("battery") or {}).get("capacity")
        if cap is not None:
            color = "red" if cap < 15 else "yellow" if cap < 30 else "green"
            comp["battery_short"] = "\U0001f50b " + self.c(f"{cap:.0f}%", color, "bold")
        comp["sensors"] = "\U0001f300 " + self.sensors_text(snap)
        return comp

    # Icons for the secondary temperatures. The row is already all-temperatures
    # so the values need no word label; the icon just says which sensor.
    TEMP_ICONS = (
        ("\U0001f50b", "battery_temp"),   # battery
        ("\U0001f4bd", "nvme_temp"),      # SSD
        ("\U0001f5a5️", "board_temp"),    # board / chassis
    )

    def temps_text(self, snap: Dict[str, Any]) -> str:
        """The secondary temperatures, shown immediately after the APU reading."""
        return "  ".join(
            f"{icon(sym)}{self.temp_str(snap.get(key))}"
            for sym, key in self.TEMP_ICONS
        )

    def sensors_text(self, snap: Dict[str, Any]) -> str:
        """Fan and power, the rest of what deck-temps.sh reports."""
        notice = self.fan_message()
        if notice:
            return notice
        bits = []
        fan = (snap.get("fan") or {}).get("rpm")
        bits.append(
            "fan " + self.c(f"{fan:.0f}rpm" if fan is not None else "--", "cyan")
        )
        slow, fast = snap.get("ppt_slow_w"), snap.get("ppt_fast_w")
        cap = snap.get("ppt_slow_cap_w")
        if slow is not None or fast is not None:
            slow_s = f"{slow:.1f}" if slow is not None else "--"
            fast_s = f"{fast:.1f}" if fast is not None else "--"
            power = self.c(f"{slow_s}/{fast_s}W", "magenta")
            bits.append(f"pwr {power}" + (self.c(f" (cap {cap:.0f}W)", "dim") if cap else ""))
        return "  ".join(bits)

    # -- modes ---------------------------------------------------------------

    def render(self, snap: Dict[str, Any], width: int) -> List[str]:
        mode = self.m.display_mode
        if mode == "dash":
            return self.render_dash(snap, width)
        if mode == "line":
            return [self.render_line(snap, width)]
        return self.render_legacy(snap, width, mode)

    def render_legacy(self, snap: Dict[str, Any], width: int,
                      mode: str) -> List[str]:
        comp = self.components(snap, width)
        lines: List[str] = []

        if mode in ("default", "defnotemp"):
            lines.extend(
                pack([comp[k] for k in ("temp", "temps", "trend", "perf")
                      if k in comp],
                     width, " ")
            )
            lines.extend(
                pack([comp[k] for k in
                      ("sensors", "battery_short", "mem", "load", "throttle")
                      if k in comp],
                     width)
            )
            if mode == "default":
                lines.append("")
                lines.extend(self.graph(width))
        elif mode in ("multi", "full"):
            order = ["temp", "temps", "trend", "cpu", "perf", "gpu", "sensors"]
            # "sensors" already carries fan rpm and the PPT pair, so full adds
            # only the richer power bar - never the standalone "fan" row again.
            if mode == "full":
                order += ["power", "battery"]
            else:
                order += ["battery_short"]
            order += ["mem", "load", "throttle"]
            lines.extend(comp[k] for k in order if k in comp)
            if mode == "full":
                lines.append("")
                lines.extend(self.graph(width))
                lines.extend(self.storage_lines(snap))
        elif mode == "temphis":
            lines.extend(self.graph(width, height=10))

        if self.m.verbose:
            lines.extend(self.verbose_lines(snap))
        # Fan notices render inline on the fan line, not as an extra row.
        # Clamp here, as render_dash does, so the renderer is self-consistent and
        # no caller can emit an over-long line by forgetting to fit() it. pack()
        # wraps what it can; a single component wider than the terminal (the APU
        # line with its threshold hint, at 30 columns) can only be truncated.
        return [fit(line, width) for line in lines]

    def storage_lines(self, snap: Dict[str, Any]) -> List[str]:
        out = []
        for disk in snap.get("storage") or []:
            out.append(
                self.c(
                    f"  {disk['label']:<10} {disk['free_gb']:.0f}G free "
                    f"of {disk['total_gb']:.0f}G ({disk['used_percent']:.0f}% used)",
                    "dim",
                )
            )
        return out

    def verbose_lines(self, snap: Dict[str, Any]) -> List[str]:
        out = [""]
        ratio = self.m.cache_hits / max(self.m.total_reads, 1) * 100
        out.append(
            self.c(
                f"gpu_metrics v{snap.get('gpu_metrics_rev') or '-'}  "
                f"throttle_raw=0x{snap.get('throttle_raw') or 0:x}  "
                f"asic=0x{snap.get('throttle_asic_raw') or 0:x}  "
                f"samples={self.m.samples}  cache={ratio:.0f}%",
                "dim",
            )
        )
        if self.m.throttle_event_times:
            out.append(self.c("Recent limiter events:", "yellow"))
            for event, stamp in sorted(
                self.m.throttle_event_times.items(), key=lambda x: x[1], reverse=True
            )[:5]:
                ago = format_time_ago(datetime.datetime.now() - stamp)
                out.append(
                    self.c(f"  - {event}: {stamp:%H:%M:%S} ({ago} ago)", "dim")
                )
        return out

    def render_line(self, snap: Dict[str, Any], width: int) -> str:
        bits = [f"APU {self.temp_str(snap.get('apu_temp'))}"]
        if snap.get("cpu_temp") is not None:
            bits.append(f"CPU {self.temp_str(snap['cpu_temp'])}")
        if snap.get("cpu_freq_mhz"):
            bits.append(self.c(f"{snap['cpu_freq_mhz']/1000:.2f}GHz", "blue"))
        if snap.get("gpu_clock_mhz"):
            bits.append(self.c(f"GPU {snap['gpu_clock_mhz']}MHz", "blue"))
        if snap.get("apu_watts") is not None:
            bits.append(self.c(f"{snap['apu_watts']:.1f}W", "magenta"))
        bat = snap.get("battery") or {}
        if bat.get("capacity") is not None:
            bits.append(f"BAT {bat['capacity']:.0f}%")
        fan = snap.get("fan") or {}
        if fan.get("rpm") is not None:
            bits.append(f"fan {fan['rpm']:.0f}")
        reasons = snap.get("throttle_reasons") or []
        bits.append(
            self.c("✓", "green") if not reasons
            else self.c(f"⚠{len(reasons)}", "yellow")
        )
        return fit(" | ".join(bits), width)

    def render_dash(self, snap: Dict[str, Any], width: int) -> List[str]:
        w = max(48, min(width, 120))
        rule = self.c("─" * w, "dim")
        out: List[str] = []

        up = format_uptime(snap.get("uptime_s"))
        head_left = f" {friendly_model(snap.get('model'))}  ·  {snap.get('os')}  ·  up {up}"
        clock = datetime.datetime.now().strftime("%H:%M:%S")
        gap = max(1, w - visible_len(head_left) - len(clock) - 1)
        out.append(self.c(head_left, "bold") + " " * gap + self.c(clock, "dim"))
        out.append(rule)

        # --- thermal block ---
        # APU first, then the secondary temperatures in the same order as the
        # compact view: bat, ssd, board.
        apu_line = (
            "  APU   "
            + pad(self.temp_str(snap.get("apu_temp")), 9)
            + pad("soc " + self.temp_str(snap.get("soc_temp")), 13)
            + pad(self.temps_text(snap), 40)
        )
        spark = self.sparkline(
            list(self.m.temp_history), max(6, w - visible_len(apu_line) - 3)
        )
        out.append(apu_line + " " + self.c(spark, "cyan"))

        cores = snap.get("core_temps") or []
        core_s = "  ".join(self.temp_str(t) for t in cores) or self.c("--", "dim")
        freq = snap.get("cpu_freq_mhz")
        driver = snap.get("scaling_driver") or "?"
        out.append(
            f"  CPU   {core_s}   "
            + self.c(f"{freq/1000:.2f}GHz" if freq else "--", "blue")
            + self.c(f"  {driver}/{snap.get('governor')}", "dim")
        )
        busy = snap.get("cpu_busy")
        if busy:
            out.append(
                self.c("        busy  ", "dim")
                + " ".join(f"{b:3.0f}%" for b in busy)
            )
        gpu_bits = [
            self.c(f"{snap.get('gpu_clock_mhz') or '--'} MHz", "blue"),
            f"busy {snap.get('gpu_busy_percent'):.0f}%"
            if snap.get("gpu_busy_percent") is not None else "busy --",
        ]
        if snap.get("gpu_vram_used_mb") is not None:
            gpu_bits.append(
                f"VRAM {snap['gpu_vram_used_mb']}/{snap.get('gpu_vram_total_mb', '?')} MB"
            )
        if snap.get("gfx_watts") is not None:
            gpu_bits.append(f"gfx {snap['gfx_watts']:.2f} W")
        out.append("  GPU   " + "   ".join(gpu_bits))
        out.append(rule)

        # --- power block ---
        watts, cap = snap.get("apu_watts"), snap.get("ppt_slow_cap_w")
        pct = (watts / cap * 100) if (watts is not None and cap) else None
        watt_s = self.c(
            f"{watts:.1f} W" if watts is not None else "-- W", "magenta", "bold"
        )
        power_line = "  POWER " + pad(watt_s, 9)
        if cap:
            power_line += f"{self.bar(pct, 20)} cap {cap:.0f} W"
        throttle = self.throttle_text(snap)
        gap = max(1, w - visible_len(power_line) - visible_len(throttle) - 2)
        out.append(power_line + " " * gap + throttle)
        detail = []
        if snap.get("cpu_watts") is not None:
            detail.append(
                f"cpu {snap['cpu_watts']:.2f} W   "
                f"soc {snap.get('soc_watts') or 0:.2f} W   "
                f"gfx {snap.get('gfx_watts') or 0:.2f} W"
            )
        slow, fast = snap.get("ppt_slow_w"), snap.get("ppt_fast_w")
        if slow is not None or fast is not None:
            detail.append(
                f"slowPPT {slow:.2f} W   fastPPT {fast:.2f} W"
                if slow is not None and fast is not None
                else f"PPT {slow if slow is not None else fast:.2f} W"
            )
        for line in pack(detail, w - 8, "      "):
            out.append(self.c("        " + line, "dim"))
        out.append("  BATT  " + self.battery_text(snap))
        bat = snap.get("battery") or {}
        extras = []
        if bat.get("health_percent") is not None:
            extras.append(f"health {bat['health_percent']:.0f}%")
        if snap.get("battery_temp") is not None:
            extras.append(f"{snap['battery_temp']:.1f}°C")
        if bat.get("cycles"):
            extras.append(f"{bat['cycles']:.0f} cycles")
        chg = snap.get("charger") or {}
        if chg.get("pd_watts"):
            extras.append(
                f"PD {chg['pd_volts']:.1f}V/{chg['pd_amps']:.1f}A = {chg['pd_watts']:.0f}W"
            )
        if extras:
            out.append(self.c("        " + "   ".join(extras), "dim"))

        fan_extras = []
        # NVMe moved up to the APU row with the other temperatures.
        if snap.get("wifi_dbm") is not None:
            fan_extras.append(f"Wi-Fi {snap['wifi_dbm']:.0f} dBm")
        fan_row = "  FAN   " + self.fan_text(snap)
        # A notice takes priority over the dim extras: drop them rather than let
        # fit() truncate the notice off the end of the row.
        if fan_extras and not self.m.fan.active_message():
            fan_row += "   " + self.c("   ".join(fan_extras), "dim")
        out.append(fan_row)
        out.append(rule)

        # --- system block ---
        mem_line = "  MEM   "
        if snap.get("mem_percent") is not None:
            mem_line += (
                f"{snap.get('mem_used_mb', 0)/1024:.1f}/"
                f"{snap.get('mem_total_mb', 0)/1024:.1f} GB "
                f"{self.bar(snap['mem_percent'], 10)}"
            )
        if snap.get("swap_percent") is not None:
            mem_line += (
                f"  swap {snap.get('swap_used_mb', 0)/1024:.1f}/"
                f"{snap.get('swap_total_mb', 0)/1024:.1f} GB"
            )
        if snap.get("load"):
            mem_line += "  load " + "/".join(f"{v:.2f}" for v in snap["load"])
        out.append(mem_line)

        psi = snap.get("pressure") or {}
        if psi:
            psi_s = "  ".join(f"{k} {v:.1f}%" for k, v in psi.items())
            disks = [
                f"{d['label']} {d['used_percent']:.0f}% full"
                for d in (snap.get("storage") or [])
            ]
            chunks = [f"PSI   {psi_s}"] + disks
            for i, line in enumerate(pack(chunks, w - 4, "   ")):
                prefix = "  " if i == 0 else "        "
                out.append(self.c(prefix + line, "dim"))

        if self.m.verbose:
            out.extend(self.verbose_lines(snap))
        # Fan notices render inline on the FAN row, not as an extra row.
        return [fit(line, width) for line in out]


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def format_duration(duration: datetime.timedelta) -> str:
    total = int(duration.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_time_ago(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        hours, minutes = divmod(total, 3600)
        minutes //= 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, rem = divmod(total, 86400)
    hours = rem // 3600
    return f"{days}d {hours}h" if hours else f"{days}d"


def format_hours(hours: float) -> str:
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    if minutes == 60:
        whole, minutes = whole + 1, 0
    return f"{whole}h{minutes:02d}m" if whole else f"{minutes}m"


def friendly_model(model: Optional[str]) -> str:
    """DMI board names for the two Deck generations."""
    return {
        "Jupiter": "Steam Deck LCD",
        "Galileo": "Steam Deck OLED",
    }.get(model or "", model or "unknown")


def format_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--"
    return format_time_ago(datetime.timedelta(seconds=seconds))


# --------------------------------------------------------------------------
# The monitor
# --------------------------------------------------------------------------

class QuarkSystemMonitor:
    """The finest self-sufficient system monitor this side of the wormhole!"""

    def __init__(self) -> None:
        self.running = True
        self.temp_history: deque = deque(maxlen=240)
        self.power_history: deque = deque(maxlen=240)

        self.display_mode = "default"
        self.verbose = False
        self.update_interval = 2.0
        self.temp_hot = TEMP_HOT
        self.temp_warm = TEMP_WARM
        self.use_color = True

        self.hub = SensorHub()
        self.renderer = Renderer(self)
        self.fan = FanController(False, 70.0, 600)

        self.samples = 0
        self.cache_hits = 0
        self.total_reads = 0

        self.last_throttle: Optional[int] = None
        self.throttle_event_times: Dict[str, datetime.datetime] = {}

        self.csv_path: Optional[str] = None
        self._csv_file = None
        self._csv_writer = None

        # session statistics
        self.stat_temp_min: Optional[float] = None
        self.stat_temp_max: Optional[float] = None
        self.stat_temp_sum = 0.0
        self.stat_temp_n = 0
        self.stat_power_max: Optional[float] = None
        self.stat_throttled_samples = 0
        self.started = time.time()
        self._resized = False
        self._termios_settings: Optional[List[Any]] = None

    # -- presentation --------------------------------------------------------

    def colorize(self, text: str, *colors: str) -> str:
        if not self.use_color:
            return text
        prefix = "".join(COLORS.get(c, "") for c in colors)
        return f"{prefix}{text}{COLORS['reset']}" if prefix else text

    def width(self) -> int:
        try:
            return max(30, shutil.get_terminal_size(fallback=(80, 24)).columns)
        except Exception:
            return 80

    # -- data ----------------------------------------------------------------

    def poll(self, record: bool = True) -> Dict[str, Any]:
        """Take one sample. record=False for throwaway priming reads."""
        snap = self.hub.sample()
        if not record:
            return snap
        self.samples += 1

        temp = snap.get("apu_temp")
        if temp is not None:
            self.temp_history.append(temp)
            self.stat_temp_min = temp if self.stat_temp_min is None else min(
                self.stat_temp_min, temp
            )
            self.stat_temp_max = temp if self.stat_temp_max is None else max(
                self.stat_temp_max, temp
            )
            self.stat_temp_sum += temp
            self.stat_temp_n += 1
        watts = snap.get("apu_watts")
        if watts is not None:
            self.power_history.append(watts)
            self.stat_power_max = watts if self.stat_power_max is None else max(
                self.stat_power_max, watts
            )
        if snap.get("throttle_reasons"):
            self.stat_throttled_samples += 1

        self.track_throttle(snap)
        self.fan.guard(temp)
        self.log_csv(snap)
        return snap

    def track_throttle(self, snap: Dict[str, Any]) -> None:
        raw = snap.get("throttle_raw")
        if raw is None:
            return
        if self.last_throttle is not None and raw != self.last_throttle:
            now = datetime.datetime.now()
            for bit, (desc, kind) in THROTTLE_BITS.items():
                was = bool(self.last_throttle & (1 << bit))
                now_on = bool(raw & (1 << bit))
                if was == now_on:
                    continue
                if now_on:
                    self.throttle_event_times[desc] = now
                    self.journal(f"LIMITER: {desc} - STARTED", "warning")
                else:
                    started = self.throttle_event_times.get(desc)
                    if started:
                        lasted = format_duration(now - started)
                        self.journal(
                            f"LIMITER: {desc} - CLEARED (lasted {lasted})", "info"
                        )
                    else:
                        self.journal(f"LIMITER: {desc} - CLEARED", "info")
        self.last_throttle = raw

    @staticmethod
    def journal(message: str, priority: str = "info") -> None:
        try:
            subprocess.run(
                ["logger", "-t", JOURNAL_TAG, "-p", priority, message],
                check=False,
                capture_output=True,
            )
        except OSError:
            pass

    # -- csv -----------------------------------------------------------------

    CSV_FIELDS = [
        "timestamp", "apu_temp", "cpu_temp", "soc_temp", "board_temp",
        "nvme_temp", "battery_temp", "cpu_freq_mhz", "gpu_clock_mhz",
        "gpu_busy_percent", "apu_watts", "cpu_watts", "gfx_watts",
        "ppt_slow_w", "ppt_fast_w", "ppt_slow_cap_w",
        "mem_percent", "swap_percent", "fan_rpm",
        "battery_percent", "battery_watts", "throttle_raw", "throttle_reasons",
    ]

    def open_csv(self, path: str) -> None:
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        self._csv_file = open(path, "a", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        if not exists:
            self._csv_writer.writerow(self.CSV_FIELDS)
            self._csv_file.flush()
        self.csv_path = path

    def log_csv(self, snap: Dict[str, Any]) -> None:
        if not self._csv_writer:
            return
        flat = self.flatten(snap)
        self._csv_writer.writerow([flat.get(k) for k in self.CSV_FIELDS])
        self._csv_file.flush()

    @staticmethod
    def flatten(snap: Dict[str, Any]) -> Dict[str, Any]:
        bat = snap.get("battery") or {}
        fan = snap.get("fan") or {}
        flat = {k: v for k, v in snap.items() if not isinstance(v, (dict, list))}
        flat["timestamp"] = datetime.datetime.fromtimestamp(
            snap["timestamp"]
        ).isoformat(timespec="seconds")
        flat["fan_rpm"] = fan.get("rpm")
        flat["battery_percent"] = bat.get("capacity")
        flat["battery_watts"] = bat.get("watts")
        flat["throttle_reasons"] = ";".join(
            d for d, _ in (snap.get("throttle_reasons") or [])
        )
        return flat

    # -- terminal ------------------------------------------------------------

    def handle_signal(self, signum, frame) -> None:
        self.running = False

    def handle_resize(self, signum, frame) -> None:
        self._resized = True

    def draw(self, lines: List[str]) -> None:
        """Home the cursor and erase per line - no full-screen clear, no flicker."""
        width = self.width()
        buf = ["\033[H"]
        for line in lines:
            buf.append(fit(line, width) + "\033[K\n")
        buf.append("\033[J")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def session_summary(self) -> str:
        elapsed = time.time() - self.started
        parts = [f"Session: {format_duration(datetime.timedelta(seconds=elapsed))}"]
        if self.stat_temp_n:
            avg = self.stat_temp_sum / self.stat_temp_n
            parts.append(
                f"APU min {self.stat_temp_min:.1f}°C  avg {avg:.1f}°C  "
                f"max {self.stat_temp_max:.1f}°C"
            )
        if self.stat_power_max is not None:
            parts.append(f"peak {self.stat_power_max:.1f}W")
        if self.samples:
            pct = self.stat_throttled_samples / self.samples * 100
            parts.append(f"limited {pct:.0f}% of samples")
        if self.csv_path:
            parts.append(f"log: {self.csv_path}")
        return "  |  ".join(parts)

    # -- main loop -----------------------------------------------------------

    def run_once(self, as_json: bool = False) -> None:
        # Per-CPU busy is a DELTA between two /proc/stat reads, so a single
        # poll can only ever report null. Prime with a throwaway sample that
        # is deliberately not counted, logged, or fed to the throttle tracker.
        self.poll(record=False)
        time.sleep(min(0.4, self.update_interval))
        snap = self.poll()
        if as_json:
            print(json.dumps(snap, indent=2, default=str))
            return
        width = self.width()
        for line in self.renderer.render(snap, width):
            print(fit(line, width))

    def unlock_sudo(self) -> None:
        """Hand the terminal back so sudo can prompt, then resume the display.

        Needed because the live view runs in cbreak mode with the cursor
        hidden, which a password prompt cannot usefully share.
        """
        settings = self._termios_settings
        if settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
            except termios.error:
                pass
        self.show_cursor()
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        print("Fan control needs sudo. Your password unlocks it for this session.\n")
        ok, msg = self.fan.authenticate()
        if settings is not None:
            try:
                tty.setcbreak(sys.stdin.fileno())
            except termios.error:
                pass
        self.hide_cursor()
        sys.stdout.write("\033[2J")
        sys.stdout.flush()
        self.fan.notify(msg if ok else f"fan control locked: {msg}", 8.0)

    def run(self) -> None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        old_settings = None
        try:
            if interactive:
                self.hide_cursor()
                old_settings = termios.tcgetattr(sys.stdin)
                self._termios_settings = old_settings
                tty.setcbreak(sys.stdin.fileno())
                sys.stdout.write("\033[2J")
            while self.running:
                snap = self.poll()
                self.draw(self.renderer.render(snap, self.width()))
                if not self.wait(interactive, snap):
                    break
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self.fan.restore()
            if old_settings:
                try:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                except termios.error:
                    pass
            if interactive:
                sys.stdout.write("\033[2J\033[H")
                self.show_cursor()
            if self._csv_file:
                self._csv_file.close()
            print(self.session_summary())

    def wait(self, interactive: bool, snap: Dict[str, Any]) -> bool:
        """Sleep for the interval, reacting to keys and resizes. False = quit."""
        if not interactive:
            time.sleep(self.update_interval)
            return self.running
        deadline = time.time() + self.update_interval
        while self.running and time.time() < deadline:
            if self._resized:
                self._resized = False
                sys.stdout.write("\033[2J")
                return True
            timeout = max(0.0, min(0.15, deadline - time.time()))
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                continue
            try:
                key = sys.stdin.read(1)
            except (OSError, ValueError):
                return False
            if key.lower() == "q" or key == "\x03":
                self.running = False
                return False
            if key.lower() == "o":
                _ok, _why, state = self.fan.available()
                if state == "needs_auth":
                    # Installed and correct, sudo just wants a password. Ask for
                    # it instead of sending the user off to reinstall something
                    # that is already there.
                    self.unlock_sudo()
                else:
                    self.fan.cycle(snap.get("apu_temp"))
                return True
            if key.lower() == "h":
                self.fan.set(FAN_AUTO)
                return True
            return True
        return self.running

    @staticmethod
    def hide_cursor() -> None:
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

    @staticmethod
    def show_cursor() -> None:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


# --------------------------------------------------------------------------
# Throttle history
# --------------------------------------------------------------------------

def show_throttle_history(monitor: QuarkSystemMonitor, days: int = 7) -> int:
    print(f"\nLimiter event history (last {days} days):\n")
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    try:
        out = subprocess.check_output(
            ["journalctl", "-t", JOURNAL_TAG, "--since", since,
             "--grep", "LIMITER|THROTTLE EVENT", "--no-pager", "--reverse"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace").strip()
    except (subprocess.CalledProcessError, OSError):
        print("  No matching journal entries (or journal unreadable).")
        return 0
    if not out:
        print("  No limiter events recorded. Lucky you!")
        return 0
    for line in out.splitlines()[:50]:
        if "STARTED" in line:
            print("  " + monitor.colorize(line, "yellow"))
        elif "CLEARED" in line:
            print("  " + monitor.colorize(line, "green"))
        else:
            print("  " + line)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

EPILOG = """
DISPLAY MODES
  (default)          Compact two rows + temperature history graph
  --defnotemp        Compact two rows, no graph
  --multi            One metric per line, no graph
  --full             Multi-line + graph + power/battery/fan/storage
  --temphis          Temperature history graph only
  --dash             Full dashboard panel, the whole sensor set
  --line             Single status line (tmux/Konsole bar, pipes cleanly)
  --json             One-shot machine-readable snapshot
  --once             Print the chosen view once and exit
  --throttle-history Recent limiter events from the journal

EXAMPLES
  %(prog)s                          # compact live view
  %(prog)s --dash -i 1              # full dashboard, 1 s refresh
  %(prog)s --json | jq .apu_temp    # scriptable
  %(prog)s --log session.csv --dash # capture a gaming session to CSV
  %(prog)s --warm 60 --hot 80       # custom colour thresholds

INTERACTIVE KEYS
  q          Quit          o   Cycle fan AUTO -> OFF -> MAX (needs setup)
  h          Fan to AUTO   any Refresh immediately

FAN CONTROL
  Off by default. It needs a one-time, explicitly-confirmed install:
      %(prog)s --install-fan-helper
  then run with --allow-fan-control. Forcing the fan OFF is guarded by a
  temperature cutoff, a deadman timer, restore-on-exit, and an out-of-process
  systemd timer that restores the fan even if this program is killed.
  Undo:  %(prog)s --uninstall-fan-helper

WHERE THE NUMBERS COME FROM
  APU/core temps, per-core power and the limiter bits come from the amdgpu SMU
  metrics table (gpu_metrics). k10temp is not loaded on SteamOS, so that table
  is the only source of per-core CPU temperature on this machine. Fan, battery
  temp and the USB-PD contract come from steamdeck_hwmon; hwmon devices are
  resolved by name because the hwmonN numbers move between boots.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quark's Premium System Monitor - Steam Deck Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--defnotemp", action="store_true",
                       help="compact display without the graph")
    modes.add_argument("--multi", action="store_true",
                       help="multi-line display without the graph")
    modes.add_argument("--full", action="store_true",
                       help="multi-line display with graph and Deck extras")
    modes.add_argument("--temphis", action="store_true",
                       help="temperature history graph only")
    modes.add_argument("--dash", action="store_true",
                       help="full dashboard panel")
    modes.add_argument("--line", action="store_true",
                       help="single status line")
    modes.add_argument("--json", action="store_true",
                       help="print one JSON snapshot and exit")
    modes.add_argument("--throttle-history", action="store_true",
                       help="show recent limiter events from the journal")

    parser.add_argument("-i", "--interval", type=float, default=2.0,
                        help="update interval in seconds (default: 2.0)")
    parser.add_argument("--once", action="store_true",
                        help="render once and exit")
    parser.add_argument("--log", metavar="FILE",
                        help="append a CSV row per sample")
    parser.add_argument("--warm", type=float, default=TEMP_WARM,
                        help=f"warm threshold C (default: {TEMP_WARM:g})")
    parser.add_argument("--hot", type=float, default=TEMP_HOT,
                        help=f"hot threshold C (default: {TEMP_HOT:g})")
    parser.add_argument("--no-color", action="store_true",
                        help="disable colour (also honours NO_COLOR)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show raw limiter values and cache statistics")

    fan = parser.add_argument_group("fan control")
    fan.add_argument("--allow-fan-control", action="store_true",
                     help="enable the 'o' key (needs --install-fan-helper first)")
    fan.add_argument("--install-fan-helper", action="store_true",
                     help="install the privileged helper + sudoers rule (asks first)")
    fan.add_argument("--uninstall-fan-helper", action="store_true",
                     help="remove the helper and sudoers rule, restore SteamOS control")
    fan.add_argument("--yes", action="store_true",
                     help="skip the install confirmation (for deck-post-update.sh)")
    fan.add_argument("--fan-off-max-temp", type=float, default=70.0,
                     help="revert fan to AUTO at this APU temp (default: 70)")
    fan.add_argument("--fan-off-timeout", type=int, default=600,
                     help=f"revert to AUTO after N s (default: 600, max {FAN_DEADMAN_MAX})")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def uninstall_fan_helper() -> int:
    print("Removing:")
    print(" ", FAN_SUDOERS, f"(and legacy {FAN_SUDOERS_LEGACY} if present)")
    print(" ", FAN_HELPER)
    print("and handing the fan back to SteamOS.")
    try:
        os.unlink(FAN_MARKER)
    except OSError:
        pass
    subprocess.run(["sudo", "rm", "-f", FAN_SUDOERS, FAN_SUDOERS_LEGACY, FAN_HELPER])
    subprocess.run(["sudo", "rmdir", FAN_HELPER_DIR], capture_output=True)
    subprocess.run(["sudo", "systemctl", "start", FAN_SERVICE])
    subprocess.run(["sudo", "systemctl", "stop", "deck-fan-deadman.timer"],
                   capture_output=True)
    state = subprocess.run(
        ["systemctl", "is-active", FAN_SERVICE], capture_output=True, text=True
    ).stdout.strip()
    print(f"{FAN_SERVICE} is now: {state}")
    return 0 if state == "active" else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.install_fan_helper:
        return install_fan_helper(assume_yes=args.yes)
    if args.uninstall_fan_helper:
        return uninstall_fan_helper()

    monitor = QuarkSystemMonitor()
    monitor.use_color = (
        not args.no_color
        and not os.environ.get("NO_COLOR")
        and sys.stdout.isatty()
    )

    if args.throttle_history:
        return show_throttle_history(monitor)

    if args.warm > args.hot:
        parser.error("--warm must not exceed --hot (a bad deal for everyone)")

    if args.defnotemp:
        monitor.display_mode = "defnotemp"
    elif args.multi:
        monitor.display_mode = "multi"
    elif args.full:
        monitor.display_mode = "full"
    elif args.temphis:
        monitor.display_mode = "temphis"
    elif args.dash:
        monitor.display_mode = "dash"
    elif args.line:
        monitor.display_mode = "line"
    else:
        monitor.display_mode = "default"

    monitor.verbose = args.verbose
    monitor.update_interval = max(0.2, args.interval)
    monitor.temp_warm = args.warm
    monitor.temp_hot = args.hot
    monitor.fan = FanController(
        args.allow_fan_control, args.fan_off_max_temp, args.fan_off_timeout
    )

    if args.log:
        try:
            monitor.open_csv(args.log)
        except OSError as exc:
            parser.error(f"cannot open --log file: {exc}")

    if args.json:
        monitor.run_once(as_json=True)
        return 0
    if args.once:
        monitor.run_once()
        return 0

    signal.signal(signal.SIGTERM, monitor.handle_signal)
    signal.signal(signal.SIGINT, monitor.handle_signal)
    signal.signal(signal.SIGHUP, monitor.handle_signal)
    try:
        signal.signal(signal.SIGWINCH, monitor.handle_resize)
    except (AttributeError, ValueError):
        pass

    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
