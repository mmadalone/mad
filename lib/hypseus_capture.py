"""
Capture ONE physical control press from the X-Arcade via the SDL Joystick API and
report the ready-to-paste hypinput.ini value — a pure-Python ctypes port of the
Hypseus author's hypjsch_cli (github DirtBagXon/hypjsch).

WHY SDL-direct (not evdev+translate): Hypseus reads buttons with the raw SDL_Joystick
API, so the value it stores is literally `jbutton.button + 1 (+ which*100)`. Capturing
through the SAME API makes our number EXACT by construction (no evdev->SDL re-derivation
that a future device could break), and it records `which` (the X-Arcade has TWO joystick
interfaces — P1 side, P2 side) for free.

WHICH-INDEX ROBUSTNESS: the SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT whitelist that
hypseus-pin.sh relies on does NOT reliably filter enumeration in every SDL context, so
we do NOT trust SDL's raw joystick order. Instead we find the X-Arcade joysticks by
vid:pid (045e:02a1) and RANK them in device-index order (rank 0 = P1 side, 1 = P2 side)
— which equals the `which` Hypseus assigns at runtime, where only the X-Arcade survives
the whitelist. Presses from any other pad (Steam Deck, Sinden) are ignored.

Run as a SHORT-LIVED SUBPROCESS from the MAD GUI (SDL + Tk in one process risks a
C-level segfault — see deck-docs/tkinter-evdev-crashes.md). On a captured press it prints
one JSON line to stdout and exits 0; timeout exits 2; SDL unavailable exits 3; no X-Arcade
present exits 4.

    python3 lib/hypseus_capture.py [--timeout 15] [--no-axis] [--no-hat] [--list]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hypinput  # noqa: E402  (value codec lives there — single source of truth)

# The X-Arcade in Xbox mode enumerates as 045e:02a1 (identical to a real Xbox 360 pad —
# at Hypseus runtime the daphne whitelist exposes exactly this vid:pid, so matching it
# here mirrors what Hypseus sees).
XARCADE_VID, XARCADE_PID = 0x045E, 0x02A1

# SDL constants
SDL_INIT_JOYSTICK = 0x00000200
SDL_ENABLE = 1
SDL_QUIT = 0x100
SDL_JOYAXISMOTION = 0x600
SDL_JOYHATMOTION = 0x602
SDL_JOYBUTTONDOWN = 0x603
_AXIS_THRESHOLD = 20000          # |value| past this = an intentional deflection (range ±32767)
_HAT_CENTERED = 0


class _JoyButtonEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32), ("button", ctypes.c_uint8),
                ("state", ctypes.c_uint8), ("padding1", ctypes.c_uint8),
                ("padding2", ctypes.c_uint8)]


class _JoyAxisEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32), ("axis", ctypes.c_uint8),
                ("padding1", ctypes.c_uint8), ("padding2", ctypes.c_uint8),
                ("padding3", ctypes.c_uint8), ("value", ctypes.c_int16),
                ("padding4", ctypes.c_uint16)]


class _JoyHatEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32), ("hat", ctypes.c_uint8),
                ("value", ctypes.c_uint8), ("padding1", ctypes.c_uint8),
                ("padding2", ctypes.c_uint8)]


class _Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_uint32),
                ("jbutton", _JoyButtonEvent),
                ("jaxis", _JoyAxisEvent),
                ("jhat", _JoyHatEvent),
                ("padding", ctypes.c_uint8 * 64)]


def _load_sdl():
    libname = ctypes.util.find_library("SDL2") or "libSDL2-2.0.so.0"
    sdl = ctypes.CDLL(libname)
    sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
    sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickInstanceID.restype = ctypes.c_int32
    sdl.SDL_JoystickInstanceID.argtypes = [ctypes.c_void_p]
    sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
    sdl.SDL_JoystickNameForIndex.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickGetDeviceVendor.restype = ctypes.c_uint16
    sdl.SDL_JoystickGetDeviceVendor.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickGetDeviceProduct.restype = ctypes.c_uint16
    sdl.SDL_JoystickGetDeviceProduct.argtypes = [ctypes.c_int]
    sdl.SDL_PollEvent.argtypes = [ctypes.POINTER(_Event)]
    sdl.SDL_GetError.restype = ctypes.c_char_p
    return sdl


def _enumerate(sdl):
    """Open every joystick; return (xarcade_rank, names). xarcade_rank maps an
    X-Arcade joystick INSTANCE id -> its 0-based rank among X-Arcade interfaces in
    device-index order (= the `which` Hypseus uses at runtime). names maps instance
    id -> SDL name (for display)."""
    names: dict[int, str] = {}
    xa_iids: list[int] = []
    for i in range(sdl.SDL_NumJoysticks()):
        vid = int(sdl.SDL_JoystickGetDeviceVendor(i))
        pid = int(sdl.SDL_JoystickGetDeviceProduct(i))
        h = sdl.SDL_JoystickOpen(i)              # must open so events fire + iid is assigned
        if not h:
            continue
        iid = int(sdl.SDL_JoystickInstanceID(h))
        nm = sdl.SDL_JoystickNameForIndex(i)
        names[iid] = nm.decode("utf-8", "replace") if nm else ""
        if (vid, pid) == (XARCADE_VID, XARCADE_PID):
            xa_iids.append(iid)
    rank = {iid: r for r, iid in enumerate(xa_iids)}
    return rank, names


def capture(timeout: float = 15.0, allow_axis: bool = True, allow_hat: bool = True) -> dict:
    """Block until the first intentional X-Arcade press (or timeout). Returns a dict:
      press : {kind: button|axis|hat, which, index, value, name, js}
      none  : {error: 'no_xarcade'}  — no X-Arcade joystick detected
      none  : {error: 'timeout'}     — nothing pressed in time
    Raises OSError if SDL can't initialise.

      - button: value = which*100 + index + 1   (int, for the col-3 button cell)
      - axis  : value = '±NNN'                   (str, for the col-4 axis cell; directions only)
      - hat   : value = which*100                (int; goes in KEY_UP's button cell)
    """
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")          # no window needed
    os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"   # deliver events without focus

    sdl = _load_sdl()
    if sdl.SDL_Init(SDL_INIT_JOYSTICK) != 0:
        raise OSError("SDL_Init failed: " + (sdl.SDL_GetError() or b"").decode("utf-8", "replace"))
    try:
        rank, names = _enumerate(sdl)
        if not rank:
            return {"error": "no_xarcade"}
        sdl.SDL_JoystickEventState(SDL_ENABLE)

        ev = _Event()
        while sdl.SDL_PollEvent(ctypes.byref(ev)):     # flush startup/arming-press queue
            pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not sdl.SDL_PollEvent(ctypes.byref(ev)):
                sdl.SDL_Delay(10)
                continue
            t = ev.type
            if t == SDL_QUIT:
                return {"error": "timeout"}
            if t == SDL_JOYBUTTONDOWN:
                iid = ev.jbutton.which
                if iid not in rank:                    # Steam Deck pad / Sinden — ignore
                    continue
                which, btn = rank[iid], ev.jbutton.button
                val = hypinput.encode_button(btn, which)
                return {"kind": "button", "which": which, "index": btn,
                        "value": val, "name": hypinput.button_label(val), "js": names.get(iid, "")}
            if allow_axis and t == SDL_JOYAXISMOTION and abs(ev.jaxis.value) >= _AXIS_THRESHOLD:
                iid = ev.jaxis.which
                if iid not in rank:
                    continue
                which, ax = rank[iid], ev.jaxis.axis
                positive = ev.jaxis.value > 0
                val = hypinput.encode_axis(ax, positive, which)
                return {"kind": "axis", "which": which, "index": ax,
                        "value": val, "name": f"axis {ax}{'+' if positive else '-'}",
                        "js": names.get(iid, "")}
            if allow_hat and t == SDL_JOYHATMOTION and ev.jhat.value != _HAT_CENTERED:
                iid = ev.jhat.which
                if iid not in rank:
                    continue
                which = rank[iid]
                return {"kind": "hat", "which": which, "index": ev.jhat.hat,
                        "value": which * 100, "name": f"hat (js{which})", "js": names.get(iid, "")}
        return {"error": "timeout"}
    finally:
        sdl.SDL_Quit()


def list_joysticks() -> dict:
    """Enumerate joysticks SDL sees, flagging the X-Arcade interfaces. Read-only."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    sdl = _load_sdl()
    if sdl.SDL_Init(SDL_INIT_JOYSTICK) != 0:
        raise OSError("SDL_Init failed: " + (sdl.SDL_GetError() or b"").decode("utf-8", "replace"))
    try:
        js = []
        xa = 0
        for i in range(sdl.SDL_NumJoysticks()):
            vid = int(sdl.SDL_JoystickGetDeviceVendor(i))
            pid = int(sdl.SDL_JoystickGetDeviceProduct(i))
            nm = sdl.SDL_JoystickNameForIndex(i)
            is_xa = (vid, pid) == (XARCADE_VID, XARCADE_PID)
            if is_xa:
                xa += 1
            js.append({"index": i, "vidpid": f"{vid:04x}:{pid:04x}",
                       "name": nm.decode("utf-8", "replace") if nm else "", "xarcade": is_xa})
        return {"count": len(js), "xarcade_interfaces": xa, "joysticks": js}
    finally:
        sdl.SDL_Quit()


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Capture one X-Arcade press as a hypinput.ini value.")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--no-axis", action="store_true", help="ignore analog-axis motion")
    ap.add_argument("--no-hat", action="store_true", help="ignore hat (d-pad) motion")
    ap.add_argument("--list", action="store_true", help="enumerate joysticks SDL sees, then exit")
    a = ap.parse_args(argv)

    try:
        if a.list:
            print(json.dumps(list_joysticks()))
            return 0
        res = capture(timeout=a.timeout, allow_axis=not a.no_axis, allow_hat=not a.no_hat)
    except OSError as e:
        print(f"SDL unavailable: {e}", file=sys.stderr)
        return 3

    if res.get("error") == "no_xarcade":
        print("no X-Arcade joystick detected", file=sys.stderr)
        return 4
    if res.get("error") == "timeout":
        print("timeout: no press captured", file=sys.stderr)
        return 2
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
