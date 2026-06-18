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


def _hat_label(token: str) -> str:
    """'h0up' -> 'Joy Up' for the on-screen name of a captured stick direction."""
    d = token[2:] if len(token) > 2 else token
    return "Joy " + d.capitalize()


# A controller whose d-pad enumerates as discrete buttons (BTN_DPAD_UP..RIGHT,
# 0x220..0x223 — e.g. the Wii U Pro Controller, which exposes NO ABS hat) → the
# hat-direction token, so d-pad capture works the same as a hat-reporting pad.
_DPAD_BTN_TOKEN = {0x220: "h0up", 0x221: "h0down", 0x222: "h0left", 0x223: "h0right"}


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


def _btn_index_map(d) -> dict:
    """evdev face-button code (0x130-0x13f) → joypad BUTTON index = its rank among the
    device's PRESENT face buttons — which is how RetroArch's udev driver numbers buttons
    (NOT code-0x130). The X-Arcade skips 0x132/0x135, so its BTN_NORTH (0x133) is index 2,
    not 3; a code-0x130 device-mode rebind wrote the wrong index and 'didn't reflect
    in-game'. Button twin of _axis_index_map."""
    try:
        key_caps = d.capabilities(absinfo=False).get(e.EV_KEY, [])
    except Exception:
        return {}            # unreadable caps → _fire falls back to code-0x130 (old behaviour)
    codes = sorted(c for c in key_caps if 0x130 <= c <= 0x13F)
    return {c: i for i, c in enumerate(codes)}


# evdev ABS code → canonical stick/trigger axis name (rank-INDEPENDENT), for the
# "axisname" capture mode the standalone per-button input-map pages use. (The
# RetroArch page keeps the rank-based "axis" mode — it wants the udev axis index.)
_ABS_CANONICAL = {} if e is None else {
    e.ABS_X: "left_x", e.ABS_Y: "left_y", e.ABS_Z: "trigger_left",
    e.ABS_RX: "right_x", e.ABS_RY: "right_y", e.ABS_RZ: "trigger_right",
}


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


def _combo_nodes() -> list:
    """Gamepad nodes PLUS mouse nodes (deduped by path) — for `combo` capture that
    may include a MOUSE button (e.g. the X-Arcade red button = BTN_MIDDLE) in a
    quit combo. Keyboards are excluded: _on_button only accumulates face buttons
    (0x130-0x13f) and mouse buttons (_MBTN), so keyboard keys wouldn't participate
    anyway. A device that is BOTH a gamepad and a mouse is opened once (gamepad)."""
    out = _gamepad_nodes()
    have = {d.path for d in out}
    for path in evdev.list_devices():
        if path in have:
            continue
        try:
            d = evdev.InputDevice(path)
            if d.name == "MAD Wii Nav":
                d.close()
                continue
            keys = set(d.capabilities().get(e.EV_KEY, []))
            if e.BTN_LEFT in keys:          # a mouse (pointer); KEY_A-only kbds skipped
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
        self._held: set = set()     # face buttons currently down (combo accumulation)
        self._hats: dict = {}       # path -> set of active RA hat tokens (e.g. "h0up")
        self._lock_path = None      # combo: the device the combo is locked to (single-device)

    def run(self):
        event("input.lock", {"locked": True, "stream": self.token})
        pointer = self.mode == "pointer"
        if pointer:
            nodes = _mouse_kbd_nodes()
        elif self.mode == "combo":
            nodes = _combo_nodes()      # gamepad + mouse: a mouse button (e.g. the
                                        # X-Arcade red button) can join a quit combo
        else:
            nodes = _gamepad_nodes()
        if not nodes:
            self.emit({"error": "no mouse/keyboard connected" if pointer
                       else "no gamepads connected"})
            self._nodes = []
            return
        self._nodes = nodes
        if self.mode in ("axis", "axisname"):
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
        self._held = set()
        self._hats = {}
        self._lock_path = None
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
                    out = self._handle(ev, d)
                    if out is not None:
                        self.emit(out)
                        return

    def _handle(self, ev, d):
        if self.mode in ("axis", "axisname"):
            return self._on_axis(ev, d)
        if self.mode == "pointer":
            return self._on_pointer(ev)
        return self._on_button(ev, d)

    def _on_button(self, ev, d):
        # Hat (d-pad / arcade-stick) directions. The X-Arcade in Xbox mode reports
        # its joystick as ABS_HAT0X/Y, NOT face buttons — capture it so a stick
        # direction can be identified, bound (RetroArch "hNdir" token), or held in
        # a combo just like a button. Previously dropped: identify/combo read only
        # EV_KEY 0x130-0x13f, which is why "pulling the X-Arcade joystick did nothing".
        if ev.type == e.EV_ABS and e.ABS_HAT0X <= ev.code <= e.ABS_HAT3Y:
            return self._on_hat(ev, d)
        # D-pad as discrete buttons (BTN_DPAD_UP..RIGHT, 0x220..0x223) — e.g. the Wii
        # U Pro Controller, which has no ABS hat. Route through the hat machinery so
        # it identifies / binds / combos exactly like a hat direction.
        if ev.type == e.EV_KEY and 0x220 <= ev.code <= 0x223:
            return self._on_dpad_button(ev, d)
        # Accept face buttons (0x130-0x13f) ALWAYS; mouse buttons (_MBTN: BTN_LEFT..EXTRA,
        # 0x110-0x114) ONLY in combo mode, where _combo_nodes() opens mouse nodes — so the
        # X-Arcade red button (BTN_MIDDLE) can join a quit combo while identify/axis can't
        # capture a stray mouse click on a hotplug race (the contract is enforced here, not
        # just by which nodes get opened).
        is_btn = 0x130 <= ev.code <= 0x13F
        is_mouse = self.mode == "combo" and ev.code in _MBTN
        if ev.type != e.EV_KEY or not (is_btn or is_mouse):
            return None
        if self._combo_locked(d, bool(ev.value)):   # reject other-device events in combo
            return None
        if ev.value:                  # press (incl. autorepeat value 2)
            self._held.add(ev.code)
            return None
        if self._held or self._any_hat():    # first release with something held
            return self._fire(d)
        return None

    def _on_hat(self, ev, d):
        if self._combo_locked(d, ev.value != 0):    # reject other-device events in combo
            return None
        hat = (ev.code - e.ABS_HAT0X) // 2          # which hat (0..3)
        is_y = (ev.code - e.ABS_HAT0X) % 2          # 0 = X axis, 1 = Y axis
        neg, pos = ((f"h{hat}up", f"h{hat}down") if is_y
                    else (f"h{hat}left", f"h{hat}right"))
        cur = self._hats.setdefault(d.path, set())
        if ev.value == 0:                           # re-centred = a release
            # Fire the accumulated combo, KEEPING the direction in the set (mirrors
            # the button path, which doesn't drop the released code before firing).
            return self._fire(d) if (self._held or self._any_hat()) else None
        cur.discard(neg); cur.discard(pos)          # engage (or switch direction)
        cur.add(neg if ev.value < 0 else pos)
        # A direction just engaged: identify fires now (you don't HOLD a stick to
        # pick it); combo keeps accumulating until the first release.
        return self._fire(d) if self.mode == "identify" else None

    def _on_dpad_button(self, ev, d):
        """A discrete d-pad button (BTN_DPAD_*) treated as a hat direction: engage on
        press (identify fires now), accumulate for a combo, fire on release."""
        token = _DPAD_BTN_TOKEN.get(ev.code)
        if token is None:
            return None
        if self._combo_locked(d, bool(ev.value)):   # reject other-device events in combo
            return None
        cur = self._hats.setdefault(d.path, set())
        if ev.value:                                # press (incl. autorepeat)
            cur.add(token)
            return self._fire(d) if self.mode == "identify" else None
        return self._fire(d) if (self._held or self._any_hat()) else None

    def _combo_locked(self, d, is_press) -> bool:
        """Combo mode: lock the capture to the FIRST device that registers an input, so a
        CROSS-DEVICE combo (e.g. a gamepad button + the trackball red button, or two
        different pads) — which the quit watcher's PER-DEVICE held-set could never satisfy
        — can't be captured. No-op outside combo mode. True ⇒ ignore this event."""
        if self.mode != "combo":
            return False
        if self._lock_path is None:
            if is_press:
                self._lock_path = d.path
            return False
        return d.path != self._lock_path

    def _any_hat(self) -> bool:
        return any(self._hats.values())

    def _hat_tokens(self) -> list:
        out: set = set()
        for s in self._hats.values():
            out |= s
        return sorted(out)

    def _fire(self, d):
        codes = sorted(self._held)
        hats = self._hat_tokens()
        bmap = _btn_index_map(d)
        res = {"held": codes,
               "names": [btn_name(c) for c in codes] + [_hat_label(t) for t in hats],
               # RA udev button index = rank among present face buttons (not code-0x130);
               # aligned 1:1 with `held` so the page can bind non-contiguous pads correctly.
               "btn_indices": [bmap.get(c, c - 0x130) for c in codes],
               "device": self._identify(d)}
        if hats:
            res["hats"] = hats                      # for combos (held directions)
            if not codes and len(hats) == 1:        # a single stick direction → bindable
                res["bind_token"] = hats[0]         # e.g. "h0up" (RetroArch hat token)
        return res

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
        sign = "+" if ev.value > rest else "-"
        idx = self._axis_idx.get(d.path, {}).get(ev.code)
        if self.mode == "axisname":
            # Canonical, rank-independent axis name + the raw axis RANK appended
            # (some emulators — Eden — store the raw SDL joystick axis index).
            canonical = _ABS_CANONICAL.get(ev.code)
            if canonical is None:
                return None
            tok = f"{sign}{canonical}" + (f"@{idx}" if idx is not None else "")
            return {"axis_token": tok, "name": f"{canonical} {sign}",
                    "device": self._identify(d)}
        if idx is None:
            return None
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
    if mode not in ("identify", "combo", "axis", "axisname", "pointer"):
        raise RpcError("EINVAL",
                       f"mode must be identify|combo|axis|axisname|pointer, got {mode!r}")
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
