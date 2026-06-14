"""capture.* methods — press-to-identify / press-a-combo, ported from
GamepadNav's capture mode (router-config-gui.py ~:605-617).

Semantics (identical to Tk): only face-button codes 0x130-0x13f participate;
presses accumulate into a held set; the FIRST release with a non-empty set
fires the result — {held codes + names, the emitting device} — and the
capture closes itself. Devices are opened WITHOUT grabbing (the press also
reaches SDL/ES-DE; the panel swallows its own input while the `input.lock`
event says locked=true — the native analog of `self.nav.capture is not None`).

One capture at a time: starting a new one cancels the previous.
"""
from __future__ import annotations

import os
import select
import threading
import time

from .. import devices as dv
from ..policy import load_merged
from ..routing import xarcade_port
from .device_cmds import ser_device
from .rpc import RpcError, Stream, event, method, stop_stream

try:
    import evdev
    from evdev import ecodes as e
except Exception:           # mad-backend.py guards this before importing us
    evdev = None
    e = None

_CURRENT = {"token": None}
_LOCK = threading.Lock()


def btn_name(code: int) -> str:
    """Port of App._btn_name: ecodes lookup, BTN_/KEY_ prefix stripped."""
    if e is not None:
        n = e.BTN.get(code) or e.KEY.get(code)
        if isinstance(n, (list, tuple)):
            n = n[0]
        if n:
            return n.replace("BTN_", "").replace("KEY_", "")
    return str(code)


# Mouse buttons → RetroArch mbtn numbers. Confirmed on-device (the gun config
# already on this box): trigger = "1" = left, offscreen/reload = "2" = right.
# 3 = middle; side/extra are best-effort 4/5.
_MBTN = {0x110: 1, 0x111: 2, 0x112: 3, 0x113: 4, 0x114: 5}  # BTN_LEFT/RIGHT/MIDDLE/SIDE/EXTRA


def _build_keymap() -> dict:
    """evdev KEY_* → RetroArch config keyname (input/input_keymaps.c)."""
    if e is None:
        return {}
    km: dict = {}
    for ch in "abcdefghijklmnopqrstuvwxyz":
        c = getattr(e, f"KEY_{ch.upper()}", None)
        if c is not None:
            km[c] = ch
    for d in range(10):                       # digit row → num0..num9 (not keypad)
        c = getattr(e, f"KEY_{d}", None)
        if c is not None:
            km[c] = f"num{d}"
    for code, name in (("KEY_UP", "up"), ("KEY_DOWN", "down"), ("KEY_LEFT", "left"),
                       ("KEY_RIGHT", "right"), ("KEY_ENTER", "enter"), ("KEY_KPENTER", "enter"),
                       ("KEY_SPACE", "space"), ("KEY_ESC", "escape"),
                       ("KEY_BACKSPACE", "backspace"), ("KEY_TAB", "tab"),
                       ("KEY_LEFTSHIFT", "shift"), ("KEY_RIGHTSHIFT", "rshift"),
                       ("KEY_LEFTCTRL", "ctrl"), ("KEY_LEFTALT", "alt")):
        c = getattr(e, code, None)
        if c is not None:
            km[c] = name
    return km


_RA_KEYMAP = _build_keymap()


def _axis_index_map(d) -> dict:
    """evdev ABS code → joypad axis index = its rank among the device's non-hat
    ABS axes. That is how the udev / SDL joypad drivers number axes, so it matches
    RetroArch's '+N'/'-N' tokens (e.g. ABS_X→0, ABS_Y→1 — the udev autoconfig truth)."""
    abs_caps = d.capabilities(absinfo=False).get(e.EV_ABS, [])
    codes = sorted(c for c in abs_caps if not (e.ABS_HAT0X <= c <= e.ABS_HAT3Y))
    return {c: i for i, c in enumerate(codes)}


def _gamepad_nodes() -> list:
    """Open every evdev node with a face button (GamepadNav's admission test:
    any EV_KEY in 0x130-0x13f). Non-blocking; caller closes."""
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            # The wii-nav-bridge's virtual pad mirrors Wii Remote presses —
            # capturing it would pin "MAD Wii Nav" instead of a real device.
            if d.name == "MAD Wii Nav":
                d.close()
                continue
            keys = set(d.capabilities().get(e.EV_KEY, []))
            if any(0x130 <= k <= 0x13F for k in keys):
                os.set_blocking(d.fd, False)
                out.append(d)
            else:
                d.close()
        except Exception:
            continue
    return out


def _mouse_kbd_nodes() -> list:
    """Open every mouse (EV_KEY has BTN_LEFT) or keyboard (has KEY_A) node — for
    pointer capture (Sinden guns enumerate as USB mice; some buttons are keys)."""
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            if d.name == "MAD Wii Nav":
                d.close()
                continue
            keys = set(d.capabilities().get(e.EV_KEY, []))
            if e.BTN_LEFT in keys or e.KEY_A in keys:
                os.set_blocking(d.fd, False)
                out.append(d)
            else:
                d.close()
        except Exception:
            continue
    return out


class _CaptureStream(Stream):
    def __init__(self, mode: str, timeout_s: float):
        super().__init__()
        self.mode = mode            # identify | combo | axis | pointer
        self.timeout_s = timeout_s
        self._nodes: list = []
        self._axis_idx: dict = {}   # path -> {abs code -> joypad axis index}
        self._base: dict = {}       # (path, abs code) -> (rest, min, max)

    def run(self):
        event("input.lock", {"locked": True, "stream": self.token})
        pointer = self.mode == "pointer"
        nodes = _mouse_kbd_nodes() if pointer else _gamepad_nodes()
        if not nodes:
            self.emit({"error": "no mouse/keyboard connected" if pointer
                       else "no gamepads connected"})
            self._nodes = []
            return
        self._nodes = nodes
        if self.mode == "axis":
            # Precompute axis indices + resting baselines so a centered stick
            # AND a zero-rest trigger each fire only on a real deflection.
            self._axis_idx = {d.path: _axis_index_map(d) for d in nodes}
            for d in nodes:
                for code, info in d.capabilities(absinfo=True).get(e.EV_ABS, []):
                    if not (e.ABS_HAT0X <= code <= e.ABS_HAT3Y):
                        self._base[(d.path, code)] = (info.value, info.min, info.max)
        # Opening nodes costs ~0.5s on this kernel (evdev open+caps per node) —
        # "ready" tells the panel the capture is actually LISTENING (a press
        # before this would be missed; the modal should arm its prompt on it).
        self.emit({"ready": True})
        held: set[int] = set()
        deadline = time.monotonic() + self.timeout_s
        while not self.stopped.is_set():
            if time.monotonic() > deadline:
                self.emit({"timeout": True})
                return
            try:
                r, _, _ = select.select([d.fd for d in nodes], [], [], 0.05)
            except (OSError, ValueError):     # a node vanished mid-select
                nodes = [d for d in nodes if self._alive(d)]
                if not nodes:
                    self.emit({"error": "all input devices vanished"})
                    return
                continue
            if not r:
                continue
            fdmap = {d.fd: d for d in nodes}
            for fd in r:
                d = fdmap.get(fd)
                if d is None:
                    continue
                # evdev's read() generator ALWAYS ends a drained burst by raising
                # BlockingIOError — list(d.read()) would discard the events
                # collected before the raise. Append incrementally so the burst
                # survives the terminating exception.
                evs = []
                try:
                    for ev0 in d.read():
                        evs.append(ev0)
                except (BlockingIOError, InterruptedError):
                    pass
                except OSError:               # real error: unplugged → drop
                    nodes.remove(d)
                    try:
                        d.close()
                    except Exception:
                        pass
                    continue
                for ev in evs:
                    out = self._handle(ev, d, held)
                    if out is not None:
                        self.emit(out)
                        return

    def _handle(self, ev, d, held):
        if self.mode == "axis":
            return self._on_axis(ev, d)
        if self.mode == "pointer":
            return self._on_pointer(ev)
        return self._on_button(ev, d, held)

    def _on_button(self, ev, d, held):
        if ev.type != e.EV_KEY or not (0x130 <= ev.code <= 0x13F):
            return None
        if ev.value:                  # press (incl. autorepeat value 2)
            held.add(ev.code)
            return None
        if held:                      # first release with something held
            codes = sorted(held)
            return {"held": codes, "names": [btn_name(c) for c in codes],
                    "device": self._identify(d)}
        return None

    def _on_axis(self, ev, d):
        if ev.type != e.EV_ABS:
            return None
        base = self._base.get((d.path, ev.code))
        if base is None:              # a hat or an axis we didn't baseline
            return None
        rest, lo, hi = base
        span = (hi - lo) or 1
        if abs(ev.value - rest) < 0.45 * span:   # ignore noise / partial travel
            return None
        idx = self._axis_idx.get(d.path, {}).get(ev.code)
        if idx is None:
            return None
        sign = "+" if ev.value > rest else "-"
        return {"axis_token": f"{sign}{idx}", "name": f"axis {idx}{sign}",
                "device": self._identify(d)}

    def _on_pointer(self, ev):
        if ev.type != e.EV_KEY or ev.value == 0:   # press / autorepeat only
            return None
        if ev.code in _MBTN:
            return {"kind": "mouse", "mbtn": _MBTN[ev.code], "name": btn_name(ev.code)}
        kn = _RA_KEYMAP.get(ev.code)
        if kn:
            return {"kind": "key", "key": kn, "name": btn_name(ev.code)}
        return None                   # unmappable key — keep listening

    @staticmethod
    def _alive(d) -> bool:
        try:
            os.fstat(d.fd)
            return True
        except OSError:
            return False

    def _identify(self, raw) -> dict | None:
        """Resolve the emitting evdev node through the device cache so the
        payload carries pin_id / port / label (the identify flows' currency)."""
        try:
            xport = xarcade_port(load_merged())
            m = next((x for x in dv.enumerate_devices() if x.path == raw.path), None)
            return ser_device(m, xport) if m is not None else None
        except Exception:
            return None

    def cleanup(self):
        for d in getattr(self, "_nodes", []):
            try:
                d.close()
            except Exception:
                pass
        with _LOCK:
            if _CURRENT["token"] == self.token:
                _CURRENT["token"] = None
        event("input.lock", {"locked": False, "stream": self.token})


@method("capture.button")
def _capture_button(params):
    mode = params.get("mode", "identify")
    if mode not in ("identify", "combo", "axis", "pointer"):
        raise RpcError("EINVAL", f"mode must be identify|combo|axis|pointer, got {mode!r}")
    timeout_s = float(params.get("timeout_s", 15.0))
    with _LOCK:
        prev = _CURRENT["token"]
    if prev:
        stop_stream(prev)
    s = _CaptureStream(mode, timeout_s)
    with _LOCK:
        _CURRENT["token"] = s.token
    return {"stream": s.start()}


@method("capture.cancel")
def _capture_cancel(params):
    with _LOCK:
        tok = _CURRENT["token"]
    return {"cancelled": bool(tok and stop_stream(tok))}
