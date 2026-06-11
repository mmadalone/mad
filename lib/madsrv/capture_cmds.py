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


def _gamepad_nodes() -> list:
    """Open every evdev node with a face button (GamepadNav's admission test:
    any EV_KEY in 0x130-0x13f). Non-blocking; caller closes."""
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            keys = set(d.capabilities().get(e.EV_KEY, []))
            if any(0x130 <= k <= 0x13F for k in keys):
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
        self.mode = mode
        self.timeout_s = timeout_s

    def run(self):
        event("input.lock", {"locked": True, "stream": self.token})
        nodes = _gamepad_nodes()
        if not nodes:
            self.emit({"error": "no gamepads connected"})
            self._nodes = []
            return
        self._nodes = nodes
        # Opening 8 nodes costs ~0.5s on this kernel (evdev open+caps per node) —
        # "ready" tells the panel the capture is actually LISTENING (a press
        # before this would be missed; the modal should arm its prompt on it).
        self.emit({"ready": True})
        held: set[int] = set()
        deadline = time.monotonic() + self.timeout_s
        try:
            while not self.stopped.is_set():
                if time.monotonic() > deadline:
                    self.emit({"timeout": True})
                    return
                try:
                    r, _, _ = select.select([d.fd for d in nodes], [], [], 0.05)
                except (OSError, ValueError):     # a node vanished mid-select
                    nodes = [d for d in nodes if self._alive(d)]
                    if not nodes:
                        self.emit({"error": "all gamepads vanished"})
                        return
                    continue
                if not r:
                    continue
                fdmap = {d.fd: d for d in nodes}
                for fd in r:
                    d = fdmap.get(fd)
                    if d is None:
                        continue
                    # evdev's read() generator ALWAYS ends a drained burst by
                    # raising BlockingIOError — list(d.read()) would discard the
                    # events collected before the raise. Append incrementally so
                    # the burst survives the terminating exception.
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
                        if ev.type != e.EV_KEY or not (0x130 <= ev.code <= 0x13F):
                            continue
                        if ev.value:              # press (incl. autorepeat value 2)
                            held.add(ev.code)
                        elif held:                # first release with something held
                            codes = sorted(held)
                            self.emit({
                                "held": codes,
                                "names": [btn_name(c) for c in codes],
                                "device": self._identify(d),
                            })
                            return
        finally:
            pass    # cleanup() closes nodes + unlocks

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
    if mode not in ("identify", "combo"):
        raise RpcError("EINVAL", f"mode must be identify|combo, got {mode!r}")
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
