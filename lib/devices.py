"""
Input-device enumeration shared by the controller-router and the existing
Sinden mouse-index helper.

Walks /dev/input/event* in numeric order (which is the order RetroArch's udev
input driver uses) and classifies each device as joypad / mouse / keyboard /
lightgun based on its evdev capability bits. Maintains running per-kind
indices so the values can be plugged straight into:

    input_player[N]_joypad_index  (RA udev driver joypad order)
    input_player[N]_mouse_index   (RA udev driver mouse order)

For name-based device reservation (RetroArch 1.18+), the `name` field is
the SDL2-comparable controller name (same string `evdev.InputDevice.name`
returns).

Used by:
- controller-router.py        — picks devices to reserve per port
- sinden-update-retroarch-mouseindex.py — backwards-compat caller for the
  Sinden P1/P2 mouse_index pin
"""
from __future__ import annotations

import glob
import os
import re
import select
import time
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import evdev
    from evdev import ecodes as e
except ImportError:
    raise SystemExit("python-evdev not installed (system package)")


# Sinden gun firmware product IDs (also pinned by udev rules)
SINDEN_PID_P1 = 0x0f38
SINDEN_PID_P2 = 0x0f39


@dataclass(frozen=True)
class Device:
    name: str            # evdev kernel name; SDL2 reports the same string
    path: str            # /dev/input/eventN
    is_joypad: bool      # has gamepad-style keys + abs axes
    is_mouse: bool       # has BTN_LEFT + ABS_X/REL_X  (RA's udev test)
    is_keyboard: bool    # has letter keys, no mouse buttons
    js_index: Optional[int]    # for input_player*_joypad_index
    mouse_index: Optional[int] # for input_player*_mouse_index
    vid: int
    pid: int
    uniq: str = ""       # evdev .uniq — BT MAC / serial (per-unit); "" or shared junk if none
    phys: str = ""       # evdev .phys — USB port topology (wired) or BT-adapter MAC (wireless)

    @property
    def is_sinden(self) -> bool:
        return self.pid in (SINDEN_PID_P1, SINDEN_PID_P2) or \
               "Sinden" in self.name


# ----------------------------------------------------------------------------
# capability classifiers
# ----------------------------------------------------------------------------

# Joypad detection: has at least one gamepad-style button (face button or
# trigger or thumb-stick) AND has at least one absolute axis (ABS_X/ABS_Y or
# ABS_HAT). Same heuristic SDL2 + RetroArch's udev driver use to decide a
# device is a joystick.
_JOYPAD_KEYS = {
    e.BTN_GAMEPAD, e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
    e.BTN_JOYSTICK, e.BTN_TRIGGER, e.BTN_THUMB, e.BTN_THUMBL, e.BTN_THUMBR,
    e.BTN_TL, e.BTN_TR, e.BTN_TL2, e.BTN_TR2, e.BTN_START, e.BTN_SELECT,
    e.BTN_MODE,
}
_JOYPAD_ABS = {e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY, e.ABS_HAT0X, e.ABS_HAT0Y}


def _has_joypad_caps(keys: set, abs_codes: set) -> bool:
    return bool(keys & _JOYPAD_KEYS) and bool(abs_codes & _JOYPAD_ABS)


def _has_mouse_caps(keys: set, abs_codes: set, rel_codes: set) -> bool:
    """Same as RetroArch's udev mouse classifier: BTN_LEFT + a pointer axis."""
    return (e.BTN_LEFT in keys) and (e.ABS_X in abs_codes or e.REL_X in rel_codes)


def _has_keyboard_caps(keys: set) -> bool:
    """Heuristic: has letter keys and lacks BTN_LEFT (= not also a mouse)."""
    # KEY_A through KEY_Z = 30..44 -ish
    letters = set(range(e.KEY_A, e.KEY_Z + 1))
    return bool(keys & letters) and e.BTN_LEFT not in keys


# ----------------------------------------------------------------------------
# main enumeration
# ----------------------------------------------------------------------------

def enumerate_devices() -> list[Device]:
    """Mirror RetroArch's udev driver enumeration order so joypad_index /
    mouse_index values match what RA will see at its own startup.

    Returns a list of `Device` records in /dev/input/event* numeric order
    (which is the order both RA and SDL2's udev backends walk). Mouse and
    joypad indices are per-kind counters within that walk.
    """
    out: list[Device] = []
    js_counter = 0
    mouse_counter = 0

    event_files = sorted(
        (f for f in os.listdir("/dev/input")
         if f.startswith("event") and f[5:].isdigit()),
        key=lambda f: int(f[5:]),
    )

    for evt in event_files:
        path = f"/dev/input/{evt}"
        try:
            d = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        try:
            caps = d.capabilities()
            # evdev returns ABS as list of (code, AbsInfo) tuples — flatten
            keys = set(caps.get(e.EV_KEY, []))
            abs_codes = {c[0] if isinstance(c, tuple) else c
                         for c in caps.get(e.EV_ABS, [])}
            rel_codes = set(caps.get(e.EV_REL, []))

            is_joypad = _has_joypad_caps(keys, abs_codes)
            is_mouse = _has_mouse_caps(keys, abs_codes, rel_codes)
            is_keyboard = _has_keyboard_caps(keys)

            js_idx = js_counter if is_joypad else None
            mouse_idx = mouse_counter if is_mouse else None
            if is_joypad:
                js_counter += 1
            if is_mouse:
                mouse_counter += 1

            out.append(Device(
                name=d.name,
                path=path,
                is_joypad=is_joypad,
                is_mouse=is_mouse,
                is_keyboard=is_keyboard,
                js_index=js_idx,
                mouse_index=mouse_idx,
                vid=d.info.vendor,
                pid=d.info.product,
                uniq=d.uniq or "",
                phys=d.phys or "",
            ))
        finally:
            d.close()
    return out


# ----------------------------------------------------------------------------
# convenience helpers used by controller-router.py
# ----------------------------------------------------------------------------

def by_substring(devs: list[Device], substr: str,
                 kind: str = "joypad") -> list[Device]:
    """Case-insensitive substring match against `name`, filtered to a kind.

    `kind` is one of: 'joypad', 'mouse', 'keyboard', 'any'.

    Returns ALL hits (a single physical device can produce multiple event
    nodes — RetroArch reservation matches the first one of the right kind).
    """
    sl = substr.lower()
    if kind == "joypad":
        pred = lambda d: d.is_joypad
    elif kind == "mouse":
        pred = lambda d: d.is_mouse
    elif kind == "keyboard":
        pred = lambda d: d.is_keyboard
    elif kind == "any":
        pred = lambda d: True
    else:
        raise ValueError(f"unknown kind: {kind}")
    return [d for d in devs if sl in d.name.lower() and pred(d)]


def sinden_present() -> tuple[bool, bool]:
    """Quick stat of the PID-pinned udev symlinks. Doesn't depend on the
    smoother daemon running."""
    return (
        Path("/dev/input/sinden-gun-p1-event").exists(),
        Path("/dev/input/sinden-gun-p2-event").exists(),
    )


# ----------------------------------------------------------------------------
# vid:pid class helpers — used by the standalone (Cemu/Dolphin) backends to
# bucket connected gamepads by device CLASS (e.g. "057e:0330" = Wii U Pro
# Controller, "054c:0ce6" = DualSense) rather than by name substring.
# ----------------------------------------------------------------------------

def vidpid(d: Device) -> str:
    """Canonical lowercase 'vvvv:pppp' for a Device (matches policy keys)."""
    return f"{d.vid:04x}:{d.pid:04x}"


def joypads(devs: list[Device]) -> list[Device]:
    """Real gamepads (joypads that aren't Sinden guns), in enumeration order."""
    return [d for d in devs if d.is_joypad and not d.is_sinden]


def class_index(devs: list[Device], dev: Device) -> int:
    """Position of `dev` among all CONNECTED gamepads of its own vid:pid, in
    /dev/input enumeration order. 0 = first such device, 1 = second, … — this
    is the index the Cemu SDL `<uuid>` prefix encodes to disambiguate two
    physically-identical pads (e.g. two Pro Controllers => 0_… and 1_…)."""
    same = [d for d in joypads(devs) if vidpid(d) == vidpid(dev)]
    # Dedup by path while preserving order (a pad can yield >1 event node).
    seen, ordered = set(), []
    for d in same:
        if d.path not in seen:
            seen.add(d.path)
            ordered.append(d.path)
    return ordered.index(dev.path) if dev.path in ordered else 0


# --- per-unit pinning identity (Phase 8) ---------------------------------

# Shared / firmware-placeholder `uniq` values that are NOT per-unit (so unusable
# as a pin key): the two Sinden guns both report HIDDO; some report HIDLG; etc.
_JUNK_UNIQS = {"", "HIDDO", "HIDLG", "0", "0000000000000000"}


def _is_real_uniq(uniq: str) -> bool:
    """True if `uniq` is a usable PER-UNIT id (a Bluetooth MAC or a real serial),
    not empty and not a shared firmware placeholder."""
    return (uniq or "").strip().upper() not in _JUNK_UNIQS


def _iface_suffix(path: str) -> str:
    """USB interface suffix ('1.0', '1.1', …) for an event node, read from sysfs
    — splits the two same-`phys` interfaces of a multi-interface device (e.g. the
    X-Arcade's two joystick halves). '' if not a USB device / not resolvable."""
    try:
        node = os.path.basename(path)
        real = os.path.realpath(f"/sys/class/input/{node}/device")
        m = re.search(r"/\d+-[\d.]+:(\d+\.\d+)(?:/|$)", real + "/")
        return m.group(1) if m else ""
    except Exception:
        return ""


def pin_id(d: Device) -> str:
    """A stable, preferably PORT-AGNOSTIC identity key for pinning a physical pad
    to a player. Tagged so the resolver knows which matcher to apply:
      uniq:<vid>:<pid>:<mac>      — port-agnostic ✓  (BT MAC / real serial)
      port:<vid>:<pid>:<phys>:<i> — port-only ⚠      (wired, no per-unit id)
      vidpid:<vid>:<pid>          — model-only        (terminal; e.g. Steam virtual)
    All event nodes of one physical unit share `uniq`, so this is stable across a
    pad's multiple event nodes."""
    vp = vidpid(d)
    if _is_real_uniq(d.uniq):
        return f"uniq:{vp}:{d.uniq.strip().lower()}"
    if d.phys:
        return f"port:{vp}:{d.phys}:{_iface_suffix(d.path)}"
    return f"vidpid:{vp}"


def pin_kind(pid_str: str) -> str:
    """The tag of a pin_id ('uniq' | 'port' | 'vidpid') — for GUI badges."""
    return pid_str.split(":", 1)[0] if pid_str else ""


def port_of(phys: str) -> str:
    """The stable USB port chain from a device's evdev `phys`, e.g.
    'usb-xhci-hcd.2.auto-1.2.4.1/input0' -> '1.2.4.1'. '' for Bluetooth pads
    (phys is the adapter MAC, no /input) and virtual devices (empty phys). Lets us
    tell apart devices that SHARE a vid:pid by WHERE they're plugged — e.g. an
    X-Arcade in Xbox mode vs a real Xbox 360 pad, both 045e:02a1, different ports."""
    m = re.search(r"-([0-9]+(?:\.[0-9]+)*)/input", phys or "")
    return m.group(1) if m else ""


def count_by_vidpid(devs: list[Device]) -> dict[str, int]:
    """How many distinct connected gamepads of each vid:pid class are present.
    Counts unique physical devices (by path), not event nodes."""
    counts: dict[str, set[str]] = {}
    for d in joypads(devs):
        counts.setdefault(vidpid(d), set()).add(d.path)
    return {k: len(v) for k, v in counts.items()}


# Mayflash DolphinBar presents each connected real Wii Remote as a Nintendo HID
# device 057e:0306 (same id the wiimote-quit-watcher co-reads). No DolphinBar /
# no remote => zero such hidraw nodes.
DOLPHINBAR_VID, DOLPHINBAR_PID = 0x057e, 0x0306


# One SDL2 joystick as PCSX2/Cemu's SDL backend sees it: the device `index`
# (= the N in PCSX2's `SDL-N` bindings and Cemu's `<index>_` uuid prefix), its
# vid:pid class, the GUID string, and the SDL name.
SdlDevice = namedtuple("SdlDevice", "index vidpid guid name")


def sdl_devices() -> list[SdlDevice]:
    """Every currently-connected SDL2 joystick, in SDL joystick-index order,
    from a SINGLE SDL init (SDL_Init is ~seconds, so callers enumerate once).

    The order mirrors what PCSX2 walks when it assigns `SDL-0`, `SDL-1`, … and
    the index is what its `[PadN]` bindings reference. The GUID embeds bus +
    name-CRC + vid + pid + version (Cemu's `<uuid>` after the `index_` prefix);
    it can't be hand-built, so SDL is authoritative. Best-effort: returns [] if
    SDL2 is unavailable. Read-only."""
    import ctypes
    import ctypes.util
    libname = ctypes.util.find_library("SDL2") or "libSDL2-2.0.so.0"
    try:
        sdl = ctypes.CDLL(libname)
    except OSError:
        return []

    class _GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_uint8 * 16)]

    sdl.SDL_JoystickGetDeviceGUID.restype = _GUID
    sdl.SDL_JoystickGetGUIDString.argtypes = [_GUID, ctypes.c_char_p, ctypes.c_int]
    sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
    SDL_INIT_JOYSTICK = 0x00000200
    if sdl.SDL_Init(SDL_INIT_JOYSTICK) != 0:
        return []
    out: list[SdlDevice] = []
    try:
        buf = ctypes.create_string_buffer(33)
        for i in range(sdl.SDL_NumJoysticks()):
            g = sdl.SDL_JoystickGetDeviceGUID(i)
            sdl.SDL_JoystickGetGUIDString(g, buf, 33)
            s = buf.value.decode()
            # GUID layout (little-endian 16-bit fields): bus, crc, vid, 0, pid…
            try:
                gvid = int(s[10:12] + s[8:10], 16)
                gpid = int(s[18:20] + s[16:18], 16)
            except ValueError:
                continue
            nm = sdl.SDL_JoystickNameForIndex(i)
            out.append(SdlDevice(i, f"{gvid:04x}:{gpid:04x}", s,
                                 nm.decode() if nm else ""))
        return out
    finally:
        sdl.SDL_Quit()


def sdl_guid_map() -> dict[str, str]:
    """Map of 'vid:pid' -> SDL2 GUID for every connected joystick (first wins).
    Used by the Cemu backend, where only the GUID (device class) matters."""
    out: dict[str, str] = {}
    for d in sdl_devices():
        out.setdefault(d.vidpid, d.guid)
    return out


def sdl_guid_for_vidpid(vid: int, pid: int) -> Optional[str]:
    """Convenience single-device lookup (one SDL init). Prefer `sdl_guid_map()`
    / `sdl_devices()` when resolving several devices."""
    return sdl_guid_map().get(f"{vid:04x}:{pid:04x}")


def sdl_index_of(dev: Device, devs: list[Device],
                 sdl_devs: Optional[list] = None) -> Optional[int]:
    """The SDL joystick index for `dev` (for PCSX2 `SDL-N` / Cemu uuid binds):
    maps the k-th connected pad of dev's vid:pid in evdev order to the k-th SDL
    joystick of the same vid:pid in SDL order. None if unmatched. Pass `sdl_devs`
    from a single `sdl_devices()` call when resolving several pins at once."""
    if sdl_devs is None:
        sdl_devs = sdl_devices()
    vp = vidpid(dev)
    same = [s for s in sdl_devs if s.vidpid == vp]
    ci = class_index(devs, dev)
    return same[ci].index if ci < len(same) else None


def _dolphinbar_slot_nodes() -> list[str]:
    """The /dev/hidrawN nodes the Mayflash DolphinBar exposes (Nintendo
    057e:0306). The bar ALWAYS presents a FIXED set of slots (4 in mode 4) — one
    per potential player — whether or not a Wii Remote is actually synced to it,
    so the node COUNT is the bar's slot count, NOT the connected-remote count."""
    nodes = []
    for d in glob.glob("/sys/class/hidraw/hidraw*"):
        ue = os.path.join(d, "device", "uevent")
        try:
            txt = open(ue).read()
        except OSError:
            continue
        for ln in txt.splitlines():
            if ln.startswith("HID_ID="):
                parts = ln.split("=", 1)[1].split(":")
                if len(parts) == 3:
                    try:
                        vid, pid = int(parts[1], 16), int(parts[2], 16)
                    except ValueError:
                        break
                    if vid == DOLPHINBAR_VID and pid == DOLPHINBAR_PID:
                        nodes.append("/dev/" + os.path.basename(d))
                break
    return sorted(nodes)


def _dolphinbar_usb_present() -> bool:
    """True if a Mayflash DolphinBar (it enumerates its Wii Remotes as Nintendo
    057e:0306 on USB) is on the USB bus — regardless of how the kernel bound it.
    In mode 4 it exposes hidraw slots; in other modes / when `hid-wiimote` claims
    the interface as evdev there is NO matching hidraw node, so the hidraw scan
    misses it. A USB-tree scan catches it either way (a directly-BT-paired remote
    is NOT on the USB bus, so this only matches the bar). Read-only."""
    want = (f"{DOLPHINBAR_VID:04x}", f"{DOLPHINBAR_PID:04x}")
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = open(os.path.join(d, "idVendor")).read().strip().lower()
            pid = open(os.path.join(d, "idProduct")).read().strip().lower()
        except OSError:
            continue
        if (vid, pid) == want:
            return True
    return False


def dolphinbar_present() -> bool:
    """True if a Mayflash DolphinBar is connected. Checks its hidraw slots (mode 4)
    AND raw USB presence (other modes, or when hid-wiimote claims it as evdev so no
    hidraw node exists — the case that made the Preview page wrongly say 'no
    DolphinBar' while one was plugged in). Read-only."""
    return bool(_dolphinbar_slot_nodes()) or _dolphinbar_usb_present()


def battery_pct(mac: str):
    """(percent:int|None, status:str) for a BT controller's battery, matched by its MAC
    (the device's evdev `uniq`) against /sys/class/power_supply/*<mac>*. Sony pads expose
    `ps-controller-battery-<mac>`, Wii Remotes `wiimote_battery_<mac>`; 8BitDo / wired
    X-Arcade expose nothing → (None, ''). Read-only."""
    if not mac:
        return (None, "")
    import glob
    m = mac.lower()
    for ps in glob.glob("/sys/class/power_supply/*"):
        if m in os.path.basename(ps).lower():
            try:
                pct = int(open(os.path.join(ps, "capacity")).read().strip())
            except (OSError, ValueError):
                return (None, "")
            try:
                st = open(os.path.join(ps, "status")).read().strip()
            except OSError:
                st = ""
            return (pct, st)
    return (None, "")


def dolphinbar_wiimotes(window: float = 0.8, active: bool = False) -> int:
    """Count Wii Remotes connected & AWAKE via the DolphinBar.

    The bar exposes a fixed set of 057e:0306 hidraw slots whether or not remotes are
    paired, AND its slot 1 streams an idle `30 00 00` report even when EMPTY — so a
    PASSIVE read cannot tell "0 remotes" from "1 idle remote" (that was the Preview
    "stuck at 1" bug).

    active=False (DEFAULT — used by the router at game-start; stays strictly READ-ONLY
        so it never disturbs Dolphin): count slots streaming a 0x30 core-button report.
        Good enough for the real/real2 mode pick, but can over-count by 1 (slot 1's
        always-on idle stream).
    active=True (MAD GUI / Preview ONLY — runs only while MAD is open): EXACT count via
        a status probe — write a 0x15 status request to each slot; only a real connected
        remote replies with a 0x20 status report (an empty slot rejects the write). It
        WRITES to the hidraw nodes, so it must NEVER run on the game-start path.

    Caveat: a SLEEPING remote sends/answers nothing — wake remotes (press a button)
    before relying on the count."""
    nodes = _dolphinbar_slot_nodes()
    if not nodes:
        return 0
    if active:
        return _dolphinbar_wiimotes_active(nodes)
    fds = {}
    for path in nodes:
        try:
            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
        except OSError:
            pass
    if not fds:
        return len(nodes)        # couldn't open any node (perms) — don't claim zero
    streaming = set()
    deadline = time.monotonic() + window
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(fds), [], [], remaining)
            if not ready:
                break
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except OSError:
                    data = b""
                if len(data) >= 3 and data[0] == 0x30:   # core-button report = live slot
                    streaming.add(fd)
            if len(streaming) == len(fds):
                break
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
    return len(streaming)


def _dolphinbar_wiimotes_active(nodes, window: float = 0.35) -> int:
    """EXACT awake-remote count via an ACTIVE status probe (writes to the hidraw —
    MAD-GUI-only, never the game-start path). Per slot: write a 0x15 status-info
    request (rumble off); a real connected remote answers with a 0x20 status report,
    an empty slot rejects the write (EPIPE / broken pipe). On-demand only (the GUI
    caches the result), so it doesn't load the system."""
    n = 0
    for path in nodes:
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            try:                                   # drain buffered reports first
                while os.read(fd, 64):
                    pass
            except OSError:
                pass
            try:
                os.write(fd, bytes([0x15, 0x00]))  # status-info request, rumble off
            except OSError:
                continue                           # empty slot → write rejected (EPIPE)
            deadline = time.monotonic() + window
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], deadline - time.monotonic())
                if not ready:
                    break
                try:
                    data = os.read(fd, 64)
                except OSError:
                    data = b""
                if data and data[0] == 0x20:       # status reply → a real remote
                    n += 1
                    break
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
    return n


def detect_sinden_mouse_indices(devs: Optional[list[Device]] = None
                                ) -> tuple[Optional[int], Optional[int], bool]:
    """Return (p1_mouse_index, p2_mouse_index, using_smoothed).

    Prefers the Smoothed P1/P2 uinput devices (created by sinden-smoother.py);
    falls back to the raw Sinden mouse interfaces matched by USB PID.

    Backwards-compat with the old `sinden-update-retroarch-mouseindex.py`
    `detect_indices()` function — same return shape, same semantics.
    """
    if devs is None:
        devs = enumerate_devices()

    smoothed_p1 = next(
        (d.mouse_index for d in devs
         if d.is_mouse and "Smoothed P1" in d.name), None)
    smoothed_p2 = next(
        (d.mouse_index for d in devs
         if d.is_mouse and "Smoothed P2" in d.name), None)

    raw_p1 = next(
        (d.mouse_index for d in devs
         if d.is_mouse and d.pid == SINDEN_PID_P1 and "Smoothed" not in d.name),
        None)
    raw_p2 = next(
        (d.mouse_index for d in devs
         if d.is_mouse and d.pid == SINDEN_PID_P2 and "Smoothed" not in d.name),
        None)

    p1 = smoothed_p1 if smoothed_p1 is not None else raw_p1
    p2 = smoothed_p2 if smoothed_p2 is not None else raw_p2
    return p1, p2, smoothed_p1 is not None
