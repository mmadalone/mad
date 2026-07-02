"""devices.* methods — serialization of lib.devices for the native panel.

devices.scan is fast (lib.devices' per-node identity cache — the evdev-close
~37ms/node lag fix — stays warm for the daemon's lifetime). devices.sdl and
devices.wiimotes are slow (SDL_Init ~seconds on first call; the DolphinBar
active probe writes to hidraw and can block) → worker pool + TTL cache.

devices.watch starts the hotplug push: a thread polls the /dev/input/event*
path-set every 2s (same cadence as the Tk GamepadNav._scan) and pushes a
"devices.changed" event with a fresh scan on any change.
"""
from __future__ import annotations

import threading
import time

from .. import devices as dv
from .. import staterev
# pad_label is re-exported: pads_cmds/preview_cmds/retroarch_cmds historically
# import it from here. Labeling itself lives in lib/pad_labels.py.
from ..pad_labels import device_label, pad_label  # noqa: F401
from ..policy import load_merged
from ..routing import xarcade_port
from .rpc import Stream, method, stop_stream

try:
    import evdev
except Exception:           # mad-backend.py guards this before importing us
    evdev = None


def evdev_by_sdl_index(devs, sdl_devs) -> dict:
    """Map SDL joystick index -> its evdev Device twin via dv.sdl_index_of (the
    k-th-of-vidpid correlation). Lets a routed SDL pad (which carries no port)
    recover its USB port from the evdev side — needed to tell the identified
    X-Arcade apart from a real Xbox 360 pad. Shared by _preview_all and
    _route_one; first evdev pad wins per index."""
    by_sdl = {}
    for d in dv.joypads(devs):
        try:
            idx = dv.sdl_index_of(d, devs, sdl_devs)
        except Exception:
            idx = None
        if idx is not None and idx not in by_sdl:
            by_sdl[idx] = d
    return by_sdl


def ser_device(d, xport: str = "") -> dict:
    port = dv.port_of(d.phys)
    vidpid = f"{d.vid:04x}:{d.pid:04x}"
    pin = dv.pin_id(d)
    label = device_label(d, xport)       # KNOWN_PADS / "X-Arcade P1"/"P2" (iface split)
    out = {
        "name": d.name, "path": d.path, "vid": d.vid, "pid": d.pid,
        "vidpid": vidpid, "uniq": d.uniq, "phys": d.phys, "port": port,
        "js_index": d.js_index, "mouse_index": d.mouse_index,
        "is_joypad": d.is_joypad, "is_mouse": d.is_mouse,
        "is_keyboard": d.is_keyboard, "is_sinden": d.is_sinden,
        "is_steam_virtual": d.is_steam_virtual, "has_face_btn": d.has_face_btn,
        "pin_id": pin, "pin_kind": dv.pin_kind(pin),
        "label": label,
    }
    if d.is_joypad and d.uniq and ":" in d.uniq:
        try:
            pct, status = dv.battery_pct(d.uniq)      # (pct|None, status-str)
            if pct is not None:
                out["battery"] = {"pct": pct, "status": status}
        except Exception:
            pass
    return out


def _scan(xport: str | None = None) -> list[dict]:
    if xport is None:
        xport = xarcade_port(load_merged())
    return [ser_device(d, xport) for d in dv.enumerate_devices()]


@method("devices.scan")
def _devices_scan(params):
    return {"devices": _scan()}


_WII = {"t": 0.0, "data": None}
_WII_LOCK = threading.Lock()
_WII_TTL = 20.0          # the Tk Preview's 2026-06-11 probe-reuse window


@method("devices.wiimotes", slow=True)
def _devices_wiimotes(params):
    """DolphinBar presence + slot nodes + ACTIVE Wiimote count (writes a 0x15
    status request per slot — GUI-preview semantics, same as the Tk page).
    TTL-cached 20s; {"force": true} busts the cache."""
    now = time.monotonic()
    with _WII_LOCK:
        if (not params.get("force") and _WII["data"] is not None
                and now - _WII["t"] < _WII_TTL):
            return _WII["data"]
    present = dv.dolphinbar_present()
    slots = dv._dolphinbar_slot_nodes() if present else []
    count = dv.dolphinbar_wiimotes(active=True) if slots else 0
    data = {"present": present, "slots": len(slots), "count": count}
    with _WII_LOCK:
        _WII.update(t=time.monotonic(), data=data)
    return data


class _WatchStream(Stream):
    """Hotplug push: emits {"changed": true, "devices": [...]} when the
    /dev/input/event* path-set changes (2s poll, like GamepadNav._scan)."""

    def run(self):
        last = None
        while not self.stopped.wait(2.0):
            try:
                paths = frozenset(evdev.list_devices()) if evdev else frozenset()
            except Exception:
                continue
            if last is not None and paths != last:
                # Warm SDL with the (dis)connected pad HERE, on the watch thread,
                # before telling the page to refresh. Opening a freshly-plugged
                # controller (e.g. a DS4) can cost SDL several seconds of HIDAPI
                # identity/calibration reads; doing it inline in a preview.all
                # would block that RPC past its 10s timeout (the page showed a
                # "preview timed out" error on a 2nd-DS4 connect). The watch
                # thread has no deadline, so the subsequent preview.all hits an
                # already-warm SDL and returns fast — the list just updates a
                # beat after plug-in, with no error.
                try:
                    dv.sdl_devices()
                except Exception:
                    pass
                staterev.bump("devices")   # invalidate device-dependent caches (Preview)
                try:
                    self.emit({"changed": True, "devices": _scan()})
                except Exception:
                    pass
            last = paths


_WATCH = {"token": None}


@method("devices.watch")
def _devices_watch(params):
    if _WATCH["token"]:
        return {"stream": _WATCH["token"], "already": True}
    s = _WatchStream()
    _WATCH["token"] = s.start()
    return {"stream": _WATCH["token"]}


@method("devices.unwatch")
def _devices_unwatch(params):
    tok = _WATCH.pop("token", None)
    _WATCH["token"] = None
    return {"stopped": bool(tok and stop_stream(tok))}
