"""
Per-device RetroArch button-bind profiles for the controller router.

WHY THIS EXISTS
---------------
RetroArch applies a controller's *autoconfig* profile only to the port the
device auto-lands in at hotplug time. When the router *reserves* a device into
a specific port (`input_playerN_reserved_device`), RetroArch moves the device
to that port but does NOT carry the autoconfig binds with it — the reserved
port keeps whatever `input_playerN_*` binds live in the loaded config. Verified
live 2026-05-29/30 from the udev-driver verbose log: the 8BitDo FC30 was
"configured in port 6", then "reserved for player 1, updating" — yet player 1
kept the stale global binds (which were saved for a different, contiguous-layout
pad), so Start was dead and L/R acted as Select/Start.

For most pads this is invisible (a standard contiguous 0–7 layout matches the
default binds). It bites devices with a NON-contiguous button layout, where no
device-agnostic global binds can ever be correct. The 8BitDo FC30 is the
canonical case: its firmware exposes phantom BTN_C (idx 2) and BTN_Z (idx 5),
which shove Select/Start out to udev indices 10/11.

So when the router reserves such a device into a port, it ALSO writes that
device's correct physical→RetroPad binds into the same per-game override
sentinel block. Devices not listed here get no bind lines — RetroArch's own
autoconfig/global binds handle them exactly as before (no regression).

LAYERING NOTE
-------------
These are the *canonical* physical→RetroPad binds (button index → RetroPad
button), NOT user preferences. Per-system preferences that should apply
regardless of which pad is in P1 (e.g. an A/B swap, or which RetroPad button is
turbo) belong in the core/per-game REMAP (.rmp), which is applied on top of
these binds. The one exception encoded below is the FC30 a/b swap, which is
kept here to preserve the exact A/B feel the user already had on NES (global
was a_btn=1/b_btn=0); flipping it is a one-line change.

D-PAD
-----
The FC30 d-pad enumerates as ABS_X/ABS_Y (axis 0/1 — what RetroArch calls the
"left analog"), not buttons or a hat. It is already handled by the NES remap's
`input_player1_analog_dpad_mode = "1"` (left-analog → d-pad) and the user
reported no d-pad problem, so we deliberately emit NO d-pad bind lines here and
leave that mechanism untouched.

BIND KEYS
---------
Each value is the suffix after `input_player{N}_`. Button binds use the udev
button index (RetroArch udev index = evdev code − 0x130 for the contiguous
BTN_* block). Axis binds use RetroArch's "±<axis>" form.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from lib.devices import Device

# RetroArch ships udev autoconfig profiles for hundreds of pads. When the router
# reserves ANY device, we read that device's own autoconfig and emit its binds
# for the reserved port — so every controller works seamlessly without a
# hardcoded profile here. The _PROFILES dict below is now only for special-cases
# (e.g. the FC30 A/B-swap) that must override the stock autoconfig.
_AUTOCONF_DIR = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/autoconfig/udev"

# autoconfig keys that are NOT per-player gamepad binds (skip when copying).
def _skip_key(suffix: str) -> bool:
    return (suffix.endswith("_label")
            or suffix in {"driver", "device", "device_display_name", "vendor_id", "product_id"}
            or suffix.startswith("menu_toggle")
            or "hotkey" in suffix)


def _autoconfig_binds(d: Device) -> Optional[dict[str, str]]:
    """Parse the RetroArch udev autoconfig matching device `d` (by exact name,
    else by vid:pid) and return its bind suffix→value map (e.g. {'a_btn':'1',
    'up_btn':'h0up', 'l2_axis':'+2', 'select_btn':'8', ...}). None if no match."""
    if not _AUTOCONF_DIR.is_dir():
        return None
    by_name: Optional[Path] = None
    by_id: Optional[Path] = None
    for f in _AUTOCONF_DIR.glob("*.cfg"):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        nm = re.search(r'input_device\s*=\s*"([^"]*)"', txt)
        if nm and nm.group(1) == d.name:
            by_name = f; break
        vm = re.search(r'input_vendor_id\s*=\s*"?(\d+)"?', txt)
        pm = re.search(r'input_product_id\s*=\s*"?(\d+)"?', txt)
        if vm and pm and int(vm.group(1)) == d.vid and int(pm.group(1)) == d.pid:
            by_id = by_id or f
    chosen = by_name or by_id
    if not chosen:
        return None
    txt = chosen.read_text(encoding="utf-8", errors="replace")
    binds: dict[str, str] = {}
    for m in re.finditer(r'^[ \t]*input_([a-z0-9_]+)\s*=\s*"([^"]*)"', txt, re.M):
        suf, val = m.group(1), m.group(2)
        if _skip_key(suf) or val in ("nul", ""):
            continue
        binds[suf] = val
    return binds or None


# 8BitDo FC30 / FC30 II (both report vid:pid 2dc8:2810; layout identical).
# Ground-truth udev indices captured live from evdev:
#   A=BTN_SOUTH 0x130→0   B=BTN_EAST 0x131→1   (phantom BTN_C 0x132→2)
#   X=BTN_NORTH 0x133→3   Y=BTN_WEST 0x134→4   (phantom BTN_Z 0x135→5)
#   L=BTN_TL    0x136→6   R=BTN_TR   0x137→7
#   Select=BTN_SELECT 0x13a→10        Start=BTN_START 0x13b→11
#
# a_btn=1 / b_btn=0 keeps the bind-layer A/B swap the user already ran on NES
# (combined with the remap's A↔B swap this nets to physical-A → NES-A). The
# remaining entries FIX the breakage: Y was bound to phantom idx2 (dead), L/R
# to 4/5, Select/Start to 6/7 (so physical L/R hijacked Select/Start and
# physical Start did nothing).
_FC30_BINDS = {
    "a_btn": "1",
    "b_btn": "0",
    "x_btn": "3",
    "y_btn": "4",
    "l_btn": "6",
    "r_btn": "7",
    "select_btn": "10",
    "start_btn": "11",
}


# Each profile: (vid, pid, name_substr_or_None, binds). First match wins.
# name_substr lets us scope a profile to a subset of a shared vid:pid if ever
# needed; None means "any device with this vid:pid".
_PROFILES: list[tuple[int, int, Optional[str], dict[str, str]]] = [
    (0x2dc8, 0x2810, None, _FC30_BINDS),   # 8BitDo FC30 GamePad + FC30 II
]


def binds_for(d: Device) -> Optional[dict[str, str]]:
    """Canonical bind suffix→value map for device `d`. Special-case profiles
    (e.g. FC30 A/B swap) win; otherwise fall back to the device's own RetroArch
    udev autoconfig so ANY reserved controller is bound correctly. None only if
    the device has neither (caller then writes no bind lines)."""
    nl = d.name.lower()
    for vid, pid, name_sub, binds in _PROFILES:
        if d.vid == vid and d.pid == pid:
            if name_sub is None or name_sub.lower() in nl:
                return binds
    return _autoconfig_binds(d)
