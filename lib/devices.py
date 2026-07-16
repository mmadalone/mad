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

import ctypes
import ctypes.util
import glob
import os
import re
import select
import threading
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

# X-Arcade trackball (vid:pid) — its red button = BTN_MIDDLE ("Mouse3"). For RA
# mouse-button SYSTEM hotkeys the router pins this as player-1's mouse (RA polls
# hotkeys on player-1's mouse only). See ra_mouse_index().
XARCADE_TRACKBALL = (0x1241, 0x1111)


@dataclass(frozen=True)
class Device:
    name: str            # evdev kernel name. USUALLY == the SDL2 name, but NOT for pads SDL renames
                         # via its controller DB (e.g. a DS4 is evdev "Wireless Controller" but SDL
                         # "PS4 Controller"). To match a Dolphin SDL/ profile, go by vid:pid, not name.
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
    has_face_btn: bool = False   # BTN_SOUTH/BTN_GAMEPAD/BTN_A present (MAD Gamepads picker test —
                                 # looser than is_joypad: no abs-axis requirement)

    @property
    def is_sinden(self) -> bool:
        return self.pid in (SINDEN_PID_P1, SINDEN_PID_P2) or \
               "Sinden" in self.name

    @property
    def is_steam_virtual(self) -> bool:
        # Steam's virtual-gamepad pool (28de:11ff, "Microsoft X-Box 360 pad N").
        # Steam creates these at the system level — and injects them into SDL inside
        # the Game-Mode session — even though ES-DE runs with Steam Input OFF. MAD
        # routes raw evdev (the real Deck is 28de:1205), so these phantom pads must be
        # ignored by the router and the Preview. NOT filtered from enumerate_devices():
        # RA's udev driver counts them when assigning joypad_index, so the raw walk
        # order has to keep matching RA — we only drop them from the pad selectors.
        return self.vid == 0x28DE and self.pid == 0x11FF

    @property
    def is_mad_virtual(self) -> bool:
        # Any uinput pad MAD itself creates (vid 0x4D41). These exist only to
        # serve one consumer and must never be routed into a game, pinned as a
        # player, or listed as a real pad:
        #   4d41:0001 "MAD Wii Nav"     — wii-nav-bridge.py, so Wii Remotes
        #                                 navigate ES-DE/MAD.
        #   4d41:0002 "MAD OpenBOR Pn"  — mad-openbor-pads.py, the canonical
        #                                 twins OpenBOR sees instead of the real
        #                                 pads (which the merger has grabbed).
        # Matched by VID, not name: a name test silently stops excluding the
        # moment a new MAD pad is added, and the twins would then show up in the
        # pad pickers as if the user could route them.
        # Same enumerate_devices() caveat as is_steam_virtual: they stay in the
        # raw walk (RA counts them for joypad_index) and are dropped by selectors.
        return self.vid == 0x4D41


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

# Per-node static-identity cache. Probing a node is cheap, but CLOSING an evdev
# fd costs ~37 ms on this kernel (USB-HID close path blocks) — a full walk of
# ~33 nodes burned ~1.2 s, and MAD's GamepadNav re-walks every 2 s on the Tk
# thread (= the 2026-06-11 "MAD lags everywhere" bug). A node's identity can't
# change while the same inode exists, so cache the static fields keyed by the
# node's stat signature and only open+close genuinely NEW nodes. The per-walk
# js/mouse counters are recomputed every call, so RA-order semantics are
# unchanged. Replug ⇒ udev recreates the node ⇒ new inode ⇒ fresh probe.
_ENUM_CACHE: dict[str, tuple[tuple, dict]] = {}
# enumerate_devices() is NOT single-threaded in the daemon: _WatchStream
# (madsrv/device_cmds._WatchStream) calls it from its own 2 s poll thread while
# slow-pool workers (devices.sdl / preview.route / gamepads.list / tester.start)
# call it concurrently. Serialize all _ENUM_CACHE access so the shared dict can't
# be read/written/pruned by two threads at once. (Mirrors _SDL_LOCK below.)
_ENUM_CACHE_LOCK = threading.Lock()


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

    alive = set()
    for evt in event_files:
        path = f"/dev/input/{evt}"
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = (st.st_ino, st.st_rdev, st.st_mtime_ns)
        # Hold the lock across the whole check-then-populate so two concurrent
        # walks can't both probe the same new node (or race the .pop / .get / set).
        with _ENUM_CACHE_LOCK:
            hit = _ENUM_CACHE.get(path)
            if hit is not None and hit[0] == sig:
                f = hit[1]
            else:
                try:
                    d = evdev.InputDevice(path)
                except (PermissionError, OSError):
                    # don't negative-cache: udev may still be applying permissions
                    _ENUM_CACHE.pop(path, None)
                    continue
                try:
                    caps = d.capabilities()
                    # evdev returns ABS as list of (code, AbsInfo) tuples — flatten
                    keys = set(caps.get(e.EV_KEY, []))
                    abs_codes = {c[0] if isinstance(c, tuple) else c
                                 for c in caps.get(e.EV_ABS, [])}
                    rel_codes = set(caps.get(e.EV_REL, []))
                    f = dict(
                        name=d.name,
                        is_joypad=_has_joypad_caps(keys, abs_codes),
                        is_mouse=_has_mouse_caps(keys, abs_codes, rel_codes),
                        is_keyboard=_has_keyboard_caps(keys),
                        vid=d.info.vendor,
                        pid=d.info.product,
                        uniq=d.uniq or "",
                        phys=d.phys or "",
                        has_face_btn=e.BTN_SOUTH in keys,   # == BTN_GAMEPAD == BTN_A (0x130)
                    )
                finally:
                    d.close()
                _ENUM_CACHE[path] = (sig, f)
        alive.add(path)

        js_idx = js_counter if f["is_joypad"] else None
        mouse_idx = mouse_counter if f["is_mouse"] else None
        if f["is_joypad"]:
            js_counter += 1
        if f["is_mouse"]:
            mouse_counter += 1
        out.append(Device(
            name=f["name"],
            path=path,
            is_joypad=f["is_joypad"],
            is_mouse=f["is_mouse"],
            is_keyboard=f["is_keyboard"],
            js_index=js_idx,
            mouse_index=mouse_idx,
            vid=f["vid"],
            pid=f["pid"],
            uniq=f["uniq"],
            phys=f["phys"],
            has_face_btn=f.get("has_face_btn", False),
        ))
    with _ENUM_CACHE_LOCK:
        for p in [p for p in _ENUM_CACHE if p not in alive]:
            del _ENUM_CACHE[p]
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
    """Real gamepads (joypads that aren't Sinden guns or Steam virtual pads), in
    enumeration order. See Device.is_steam_virtual for why 28de:11ff is dropped."""
    return [d for d in devs if d.is_joypad and not d.is_sinden
            and not d.is_steam_virtual and not d.is_mad_virtual]


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


def usb_iface_num(event_path) -> Optional[int]:
    """bInterfaceNumber of the USB interface behind a /dev/input/eventN node, or
    None (Bluetooth / virtual / platform devices, or sysfs surprises).

    The only stable P1/P2 discriminator for the X-Arcade's two gamepad nodes:
    in Xbox mode the cab (USB product string 'X-Arcade 2', 045e:0719 with two
    xpad interfaces) exposes two byte-identical evdev devices — same name +
    vid:pid, IDENTICAL phys (both literally '…-1.1/input0'), empty uniq — but
    each cabinet side is hard-wired to its own interface (00 / 01), which
    survives replug/re-enumeration while event-node numbering need not
    (verified live on 3-1.1:1.0/:1.1 → event6/event10, 2026-06-10; see
    deck-docs/xarcade-usb-identity.md)."""
    try:
        p = (Path("/sys/class/input") / os.path.basename(str(event_path))
             / "device" / "device" / "bInterfaceNumber")
        return int(p.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


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
# (Cemu's `<index>_` uuid prefix + the raw SDL2 enumeration order), its vid:pid
# class, the GUID string, the SDL name, and the SDL `player_index`. NOTE: PCSX2's
# `SDL-N` bindings use the PLAYER index, NOT the enumeration index (a non-gamepad
# joystick shifts it), so player_index is the authoritative N for pcsx2. -1 = SDL
# has not assigned one. player_index defaults to -1 so older 4-field constructions
# and test fakes keep working.
SdlDevice = namedtuple("SdlDevice", "index vidpid guid name player_index", defaults=(-1,))

# SDL's joystick subsystem is NOT thread-safe. Historically each sdl_devices()
# call ran a full SDL_Init → enumerate → SDL_Quit cycle, and the MAD GUI calls
# this from short-lived worker threads (the Preview rescan): a controller
# (dis)connect could fire a burst of rescans whose SDL_Init outlived the next
# one, running two SDL_Init/SDL_Quit cycles at once and SEGFAULTing libSDL (its
# internal udev hotplug monitor races the init/quit — MAD crashed on BT-gamepad
# disconnect). We now SDL_Init ONCE per process and keep it (SDL_Quit only on
# daemon teardown, via sdl_quit()), which removes that init/quit race entirely
# AND drops the per-call ~seconds init cost; _SDL_LOCK still serializes ALL SDL
# access so only one thread is ever inside libSDL at a time.
_SDL_LOCK = threading.Lock()
_SDL = None              # libSDL2 CDLL handle, signatures configured once
_SDL_INITED = False      # SDL_Init(_SDL_INIT_JOYSTICK) done, not yet SDL_Quit'd
_SDL_INIT_JOYSTICK = 0x00000200
_SDL_LIB_PATH = None     # override: a SPECIFIC libSDL2 to load (see set_sdl_lib)
# Last successful enumeration, published (rebound to a fresh list — never mutated
# in place) at the end of every successful sdl_devices() pass under _SDL_LOCK. A
# READER that loses the try-lock returns list(_SDL_CACHE) WITHOUT taking any lock:
# under the GIL the publisher's `_SDL_CACHE = out` rebind and the reader's read +
# list() copy are each atomic, so the reader sees a fully-built old or new list,
# never a half-built one — no second lock needed for cache coherency.
_SDL_CACHE: list[SdlDevice] = []


def set_sdl_lib(path: str) -> None:
    """Load a SPECIFIC libSDL2 (e.g. an emulator's BUNDLED one) for enumeration so
    the joystick INDEX matches that emulator's. Different SDL versions order
    joysticks differently (e.g. SDL 2.30 surfaces the Steam Virtual Gamepad as a
    separate device, shifting indices) — and the Ryujinx id is `{index}-{guid}`,
    so the index must match. Call BEFORE the first sdl_devices() in the process
    (the lib is loaded once). No-op-safe: ignored once SDL is already loaded."""
    global _SDL_LIB_PATH
    _SDL_LIB_PATH = path


class _SdlGUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_uint8 * 16)]


def _sdl_lib():
    """Load libSDL2 once and configure the signatures we use; returns the CDLL
    handle or None if SDL2 is unavailable. Caller must hold _SDL_LOCK."""
    global _SDL
    if _SDL is not None:
        return _SDL
    libname = _SDL_LIB_PATH or ctypes.util.find_library("SDL2") or "libSDL2-2.0.so.0"
    try:
        sdl = ctypes.CDLL(libname)
    except OSError:
        return None
    sdl.SDL_JoystickGetDeviceGUID.restype = _SdlGUID
    sdl.SDL_JoystickGetGUIDString.argtypes = [_SdlGUID, ctypes.c_char_p, ctypes.c_int]
    sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
    try:    # SDL 2.0.9+: per-device SDL player index = the N PCSX2 writes in SDL-N
        sdl.SDL_JoystickGetDevicePlayerIndex.restype = ctypes.c_int
        sdl.SDL_JoystickGetDevicePlayerIndex.argtypes = [ctypes.c_int]
    except AttributeError:
        pass
    _SDL = sdl
    return _SDL


def sdl_devices(pump: bool = True) -> list[SdlDevice]:
    """Every currently-connected SDL2 joystick, in SDL joystick-index order.

    The order mirrors what PCSX2 walks when it assigns `SDL-0`, `SDL-1`, … and
    the index is what its `[PadN]` bindings reference. The GUID embeds bus +
    name-CRC + vid + pid + version (Cemu's `<uuid>` after the `index_` prefix);
    it can't be hand-built, so SDL is authoritative. Best-effort: returns [] if
    SDL2 is unavailable. Read-only.

    SDL is initialized once (the slow ~seconds step) and kept alive; later calls
    just pump the hotplug machinery (SDL_PumpEvents + SDL_JoystickUpdate) so
    freshly (dis)connected pads appear/disappear without a costly re-init.

    pump=True (OWNER, default): blocking-acquire _SDL_LOCK, init-or-pump,
        enumerate, publish _SDL_CACHE, return. Used by the watch-thread warm,
        _warm_sdl, the launch-wrapper config writers, AND the pads.get RPC (which
        is slow=True, so it can afford to wait out the warm and return real pads on
        first open) — the callers that MUST drive hotplug / see fresh pads.
    pump=False (READER): for deadline-bound RPC handlers (e.g. preview). NEVER pumps. Tries the
        lock non-blocking; if it can't get it (an owner is mid-pump, holding the
        lock for seconds on a BT connect), it returns list(_SDL_CACHE) at once —
        taking NO lock on that path, so it can't freeze behind the pumper. If it
        DOES get the lock it enumerates cheaply (NumJoysticks+GUID+name only,
        no PumpEvents/JoystickUpdate), publishes the cache, and returns. (Cold
        edge: if a reader is the very first SDL caller before the daemon warm,
        it still pays the one-time SDL_Init under the lock — harmless, rare.)"""
    if not pump:
        # READER: whole-function try-lock — never block on the pumper.
        if not _SDL_LOCK.acquire(blocking=False):
            return list(_SDL_CACHE)          # last-good, lock-free, no pump
        try:
            return _enumerate_sdl(do_pump=False)
        finally:
            _SDL_LOCK.release()
    with _SDL_LOCK:
        return _enumerate_sdl(do_pump=True)


def _enumerate_sdl(do_pump: bool) -> list[SdlDevice]:
    """Enumerate SDL joysticks. CALLER MUST HOLD _SDL_LOCK. Inits SDL exactly once
    (the only slow step, guarded by _SDL_INITED so two threads can never both run
    SDL_Init — the historical segfault); pumps the hotplug machinery only when
    do_pump (owner mode). Publishes the result to _SDL_CACHE on success."""
    global _SDL_INITED, _SDL_CACHE
    out: list[SdlDevice] = []
    sdl = _sdl_lib()
    if sdl is None:
        return out
    if not _SDL_INITED:
        if sdl.SDL_Init(_SDL_INIT_JOYSTICK) != 0:
            return out
        _SDL_INITED = True
    elif do_pump:
        # Refresh the device list for hotplug WITHOUT re-initing: PumpEvents
        # drives the udev add/remove detection, JoystickUpdate the state.
        sdl.SDL_PumpEvents()
        sdl.SDL_JoystickUpdate()
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
        try:    # SDL player index = PCSX2's SDL-N; -1 when SDL has not assigned one
            pidx = int(sdl.SDL_JoystickGetDevicePlayerIndex(i))
        except (AttributeError, OSError):
            pidx = -1
        out.append(SdlDevice(i, f"{gvid:04x}:{gpid:04x}", s,
                             nm.decode(errors="replace") if nm else "", pidx))
    _SDL_CACHE = out                         # publish last-good (fresh list, never mutated in place)
    return list(out)                         # hand callers their OWN copy so a future mutator can't corrupt a concurrent reader


def sdl_quit() -> None:
    """SDL_Quit the persistent joystick subsystem — call once on daemon teardown
    so MAD closing leaves no SDL state behind. Idempotent and safe if SDL was
    never initialized; a later sdl_devices() will transparently re-init."""
    global _SDL_INITED, _SDL_CACHE
    with _SDL_LOCK:
        if _SDL_INITED and _SDL is not None:
            try:
                _SDL.SDL_Quit()
            except Exception:
                pass
            _SDL_INITED = False
            _SDL_CACHE = []     # drop the last-good list; a later sdl_devices() re-inits + republishes


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
                ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
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


def _ra_mouse_order() -> list[tuple[str, int]]:
    """The connected mice in RetroArch's exact index order, as (name, product_id).

    RetroArch's udev input driver enumerates subsystem=input via libudev
    (udev_enumerate returns devices sorted by sysfs path) and numbers every mouse
    — the ones passing its BTN_LEFT + ABS_X/REL_X test — in THAT order. We
    replicate it by classifying with _has_mouse_caps and sorting on each device's
    sysfs path. Verified to match RetroArch's own "[udev] Mouse/Touch #N" log
    lines; enumerate_devices' own mouse_index uses a different order, which
    mis-pinned input_player2_mouse_index (off-by-one around the smoothed guns).
    """
    import glob
    import os
    mice = []
    for path in glob.glob("/dev/input/event*"):
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            caps = d.capabilities()
            keys = set(caps.get(e.EV_KEY, []))
            abs_codes = {c[0] if isinstance(c, tuple) else c
                         for c in caps.get(e.EV_ABS, [])}
            rel_codes = set(caps.get(e.EV_REL, []))
            name = d.name
            pid = d.info.product
        finally:
            d.close()
        if _has_mouse_caps(keys, abs_codes, rel_codes):
            syspath = os.path.realpath("/sys/class/input/" + os.path.basename(path))
            mice.append((syspath, name, pid))
    mice.sort()   # lexicographic by sysfs path == udev_enumerate order
    return [(name, pid) for _sp, name, pid in mice]


def ra_mouse_index(vid: int, pid: int) -> Optional[int]:
    """The RetroArch udev mouse index (the value for input_player*_mouse_index) of
    the FIRST connected mouse with this vid:pid, or None if none is present. Same
    enumeration order as _ra_mouse_order (sysfs-path sorted = RA's udev order), so
    the number is correct for RA at its next startup.

    Used by the controller-router to pin player-1's mouse to the X-Arcade trackball
    (1241:1111) for non-lightgun RA games, so RA's mouse-button SYSTEM hotkeys —
    which RA polls on port 0 (= player 1) only — can read the red button. The index
    is re-derived every launch (it shifts on replug), so the stable vid:pid is the
    real pin; the number is just its translation for the current device topology."""
    mice = []
    for path in glob.glob("/dev/input/event*"):
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            caps = d.capabilities()
            keys = set(caps.get(e.EV_KEY, []))
            abs_codes = {c[0] if isinstance(c, tuple) else c
                         for c in caps.get(e.EV_ABS, [])}
            rel_codes = set(caps.get(e.EV_REL, []))
            v, p = d.info.vendor, d.info.product
        finally:
            d.close()
        if _has_mouse_caps(keys, abs_codes, rel_codes):
            syspath = os.path.realpath("/sys/class/input/" + os.path.basename(path))
            mice.append((syspath, v, p))
    mice.sort()   # lexicographic by sysfs path == udev_enumerate order (== _ra_mouse_order)
    for idx, (_sp, v, p) in enumerate(mice):
        if v == vid and p == pid:
            return idx
    return None


def detect_sinden_mouse_indices(devs: Optional[list[Device]] = None
                                ) -> tuple[Optional[int], Optional[int], bool]:
    """Return (p1_mouse_index, p2_mouse_index, using_smoothed) — the indices
    RetroArch will use for input_playerN_mouse_index.

    Prefers the Smoothed P1/P2 uinput devices (created by sinden-smoother.py);
    falls back to the raw Sinden mouse interfaces matched by USB PID. Indices are
    RetroArch's udev mouse order (_ra_mouse_order) — NOT enumerate_devices' order,
    which counts the same mice differently and mis-pinned P2.

    `devs` is accepted for backwards-compat but ignored: the order is always
    re-derived in RetroArch's udev order.
    """
    order = _ra_mouse_order()   # [(name, product_id), ...] in RetroArch index order
    smoothed_p1 = next((i for i, (nm, _p) in enumerate(order)
                        if "Smoothed P1" in nm), None)
    smoothed_p2 = next((i for i, (nm, _p) in enumerate(order)
                        if "Smoothed P2" in nm), None)
    raw_p1 = next((i for i, (nm, pid) in enumerate(order)
                   if pid == SINDEN_PID_P1 and "Smoothed" not in nm), None)
    raw_p2 = next((i for i, (nm, pid) in enumerate(order)
                   if pid == SINDEN_PID_P2 and "Smoothed" not in nm), None)
    p1 = smoothed_p1 if smoothed_p1 is not None else raw_p1
    p2 = smoothed_p2 if smoothed_p2 is not None else raw_p2
    return p1, p2, smoothed_p1 is not None
