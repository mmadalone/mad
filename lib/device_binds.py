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
import shutil
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


def _autoconfig_file(d: Device) -> Optional[Path]:
    """The writable udev autoconfig .cfg matching device `d` — by exact
    `input_device` name, else by vid:pid. None if the dir or a match is absent.
    Shared by the read path (_autoconfig_binds) and the write path
    (autoconfig_path_for) so both resolve the SAME file."""
    if not _AUTOCONF_DIR.is_dir():
        return None
    by_id: Optional[Path] = None
    for f in _AUTOCONF_DIR.glob("*.cfg"):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        nm = re.search(r'input_device\s*=\s*"([^"]*)"', txt)
        if nm and nm.group(1) == d.name:
            return f
        vm = re.search(r'input_vendor_id\s*=\s*"?(\d+)"?', txt)
        pm = re.search(r'input_product_id\s*=\s*"?(\d+)"?', txt)
        if vm and pm and int(vm.group(1)) == d.vid and int(pm.group(1)) == d.pid:
            by_id = by_id or f
    return by_id


def _autoconfig_binds(d: Device) -> Optional[dict[str, str]]:
    """Parse the RetroArch udev autoconfig matching device `d` and return its
    bind suffix→value map (e.g. {'a_btn':'1', 'up_btn':'h0up', 'l2_axis':'+2',
    'select_btn':'8', ...}). Last occurrence wins, so a MAD sentinel block appended
    by set_device_bind() overrides the stock binds. None if no match."""
    chosen = _autoconfig_file(d)
    if not chosen:
        return None
    txt = chosen.read_text(encoding="utf-8", errors="replace")
    binds: dict[str, str] = {}
    for m in re.finditer(r'^[ \t]*input_([a-z0-9_]+)\s*=\s*"([^"]*)"', txt, re.M):
        suf, val = m.group(1), m.group(2)
        if _skip_key(suf):
            continue
        if val in ("nul", ""):
            # Explicit unbind sentinel — a tombstone. Honour last-occurrence-wins (per the
            # docstring): a trailing nul must REMOVE an earlier stock bind, not be skipped
            # (else a cleared row redisplays the stale stock value and binds_for would carry
            # it onto the reserved port at launch).
            binds.pop(suf, None)
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
    """Canonical bind suffix→value map for device `d`, carried onto its reserved
    port by the router. A special-case profile (e.g. the FC30 A/B swap) is the
    base; the user's per-device edits from the MAD RA Input page (the sentinel
    block in the device's autoconfig) OVERLAY it so a user remap always wins.
    Devices without a profile fall back to their own RetroArch udev autoconfig
    (which already includes the sentinel, last-occurrence-wins). None only if the
    device has neither (caller then writes no bind lines)."""
    nl = d.name.lower()
    for vid, pid, name_sub, binds in _PROFILES:
        if d.vid == vid and d.pid == pid and (name_sub is None or name_sub.lower() in nl):
            base = dict(binds)
            base.update(get_device_binds(d, sentinel_only=True))   # user edits win
            return base or None
    return _autoconfig_binds(d)


# ── User-editable per-device binds (MAD RA Input page, device mode) ──────────
# The device-scoped RA Input page writes the user's per-button remaps into a MAD
# sentinel block appended to the device's WRITABLE autoconfig. _autoconfig_binds
# reads the file last-occurrence-wins, so the sentinel overrides the stock binds;
# the router then carries the result onto the device's reserved port via the
# per-game override — where it SURVIVES launch (RetroArch does not carry a
# device's autoconfig onto a reserved port). Mirrors retroarch_cfg's sentinel idiom.
DEV_BEGIN = "# >>> MAD device binds (auto-managed) >>>"
DEV_END = "# <<< MAD device binds end <<<"
_DEV_SENTINEL_RE = re.compile(
    re.escape(DEV_BEGIN) + r".*?" + re.escape(DEV_END) + r"\n?", re.DOTALL)


def autoconfig_path_for(d: Device) -> Path:
    """Writable autoconfig profile for `d`: the matched existing file, else a
    deterministic new path so a first-time device-mode edit creates a minimal
    profile."""
    existing = _autoconfig_file(d)
    if existing:
        return existing
    safe = re.sub(r"[^\w ()+-]", "_", d.name).strip() or f"{d.vid:04x}_{d.pid:04x}"
    return _AUTOCONF_DIR / f"{safe}.cfg"


def _minimal_profile(d: Device) -> str:
    return (f'input_driver = "udev"\n'
            f'input_device = "{d.name}"\n'
            f'input_vendor_id = "{d.vid}"\n'
            f'input_product_id = "{d.pid}"\n')


def _dev_managed(text: str) -> dict[str, str]:
    """The suffix→value pairs currently inside the MAD device-binds sentinel."""
    m = _DEV_SENTINEL_RE.search(text)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(0).splitlines():
        if line.strip().startswith("#"):
            continue
        mm = re.match(r'\s*input_(\w+)\s*=\s*"?([^"\n]*)"?\s*$', line)
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".mad-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def get_device_binds(d: Device, sentinel_only: bool = False) -> dict[str, str]:
    """For the RA Input page (device mode): the device's effective bind map (stock
    autoconfig + MAD sentinel overrides). `sentinel_only=True` returns ONLY the
    user's sentinel edits (used to overlay a _PROFILES special-case)."""
    if sentinel_only:
        p = _autoconfig_file(d)
        return _dev_managed(p.read_text(encoding="utf-8", errors="replace")) if p else {}
    return _autoconfig_binds(d) or {}


def set_device_bind(d: Device, suffix: str, value: str) -> Path:
    """Write ONE user bind (`input_<suffix> = "<value>"`) into the MAD sentinel
    block of device `d`'s writable autoconfig, so it overrides the stock bind and
    is carried onto the reserved port by the router. Creates a minimal profile if
    the device has none yet. Atomic (tmp+rename); one-time `.mad-bak`. Returns the
    path written."""
    path = autoconfig_path_for(d)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if not existing:
        existing = _minimal_profile(d)
    managed = _dev_managed(existing)
    body = _DEV_SENTINEL_RE.sub("", existing).rstrip("\n")
    managed[suffix] = value
    block = "\n".join(f'input_{k} = "{v}"' for k, v in sorted(managed.items()))
    new_text = (f"{body}\n\n{DEV_BEGIN}\n{block}\n{DEV_END}\n" if body
                else f"{DEV_BEGIN}\n{block}\n{DEV_END}\n")
    if path.exists():
        bak = path.parent / (path.name + ".mad-bak")
        if not bak.exists():
            shutil.copy2(path, bak)
    _atomic_write(path, new_text)
    return path
