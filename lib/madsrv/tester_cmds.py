"""tester.* / gamepads.* / xarcade.* — the live controller testers (phase 4).

Headless extraction of lib/mad_gamepad_tester.py + lib/mad_xarcade_tester.py:
the daemon owns ALL evdev/hidraw work (open, EVIOCGRAB, poll, event→spot
mapping, calibration capture, escape combos) and streams ≤30 Hz coalesced
sprite snapshots; the native panel only renders. Grab-safety per the plan:
the grab happens ~150 ms AFTER the stream token is returned (the triggering
button-up still reaches SDL), escapes are BACKEND-owned (hold Start 6 s on a
pad / + on a Wii Remote / P1+P2 Start 3 s on the X-Arcade; the Steam Deck pad
auto-stops after ~20 s idle), and Stream.cleanup ungrabs + closes on every
exit path including daemon teardown.
"""
from __future__ import annotations

import json
import os
import select
import time
from pathlib import Path

from .. import devices as dv
from ..policy import load_merged
from ..wii_slot_reader import WiiSlotReader
from .rpc import RpcError, Stream, event, method, stop_stream
from .systems_cmds import resolve_art

CONTROL_PANEL = Path.home() / "Emulation" / "storage" / "control-panel"
GP_DEFAULTS = Path(__file__).resolve().parent.parent.parent / "data" / "gp-defaults"
# The wii tester's slot claim — wii-nav-bridge.py releases this slot while
# the file exists (and the mad-backend flock is held).
_TESTER_SLOT_FILE = Path.home() / "Emulation/storage/controller-router/wii-tester-slot"

# (vid, pid, key, label, sprite_dir, picker_icon) — verbatim from the Tk mixin.
GP_PROFILES = [
    (0x2dc8, 0x2810, "fc30", "8BitDo FC30", "8bitdofc30-tester", "8bitdofc30.png"),
    (0x2dc8, 0x3820, "n30", "8BitDo N30 Pro", "8bitdon30-tester", "8bitdon30pro.png"),
    (0x054c, 0x0ce6, "dualsense", "DualSense", "dualsense-tester", "dualsense.png"),
    (0x054c, 0x09cc, "dualshock4", "DualShock 4", "dualshock4-tester", "dualshock.png"),
    (0x057e, 0x0330, "wiiupro", "Wii U Pro", "wiiupro-tester", "wiiupro.png"),
    (0x045e, 0x02a1, "xbox360", "Xbox 360", "xbox360-tester", "xbox360.png"),
    (0x28de, 0x1205, "steamdeck", "Steam Deck", "steamdeck-controller-tester",
     "steamdeck.png"),
    (0x057e, 0x0306, "wiimote", "Wii Remote", "wiimote-tester", "wiimote.png"),
]
XARCADE_VIDPIDS = {(0x045e, 0x02a1), (0x1241, 0x1111)}

# Accessory buttons the decoders actually emit (whitelist).
EXT_BTNS = {
    "nunchuk": frozenset({"c", "z"}),
    "classic": frozenset({"a", "b", "x", "y", "dpadup", "dpaddown", "dpadleft",
                          "dpadright", "l", "r", "zl", "zr", "plus", "minus", "home"}),
}


def _profile_for(vid: int, pid: int, name: str):
    for v, p, key, label, d, icon in GP_PROFILES:
        if (vid, pid) == (v, p):
            return {"key": key, "label": label, "dir": d, "icon": icon}
    n = (name or "").lower()
    for needle, key in (("8bitdo", "n30"), ("dualsense", "dualsense"),
                        ("wireless controller", "dualshock4"), ("wii u pro", "wiiupro"),
                        ("wii remote pro", "wiiupro"), ("xbox", "xbox360"),
                        ("wii remote", "wiimote"), ("wiimote", "wiimote")):
        if needle in n:
            for v, p, k, label, d, icon in GP_PROFILES:
                if k == key:
                    return {"key": k, "label": label, "dir": d, "icon": icon}
    return None


def _xport() -> str:
    try:
        return str((load_merged().get("hardware") or {}).get("xarcade_port", "") or "")
    except Exception:
        return ""


def _wii_probe_kind(fd, quiet: bool = True) -> str:
    """Extension attached to a live remote — verbatim Tk _gp_probe_kind.
    quiet=False leaves CONTINUOUS reporting set: when the wii-nav-bridge
    co-reads the slot, dropping to quiet mode would stall its navigation
    until the next keepalive."""
    try:
        os.write(fd, WiiSlotReader.EXT_F0)
        os.write(fd, WiiSlotReader.EXT_FB)
        os.write(fd, WiiSlotReader.EXT_ID)
        os.write(fd, WiiSlotReader.SET_MODE)
    except OSError:
        return ""
    kind = ""
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.7:
        try:
            r, _, _ = select.select([fd], [], [], 0.15)
        except OSError:
            break
        if not r:
            continue
        try:
            buf = os.read(fd, 64)
        except OSError:
            break
        if not buf:
            break
        if buf[0] == 0x21 and len(buf) >= 12:
            kind = {(0x00, 0x00): "nunchuk",
                    (0x01, 0x01): "classic"}.get((buf[10], buf[11]), "")
            break
        if buf[0] == 0x20 and len(buf) > 3 and not (buf[3] & 0x02):
            break  # No extension attached.
    if quiet:
        try:
            os.write(fd, bytes([0x12, 0x00, 0x30]))  # Quiet non-continuous reporting.
        except OSError:
            pass
    return kind


def _db_slots() -> list:
    """[(slot, node)] for the DolphinBar's hidraw slots (Tk _gp_db_slots)."""
    import re
    try:
        nodes = dv._dolphinbar_slot_nodes()
    except Exception:
        nodes = []
    ranked = []
    for node in nodes:
        base = os.path.basename(node)
        idx = 99
        try:
            for line in open(f"/sys/class/hidraw/{base}/device/uevent"):
                if line.startswith("HID_PHYS="):
                    m = re.search(r"input(\d+)", line)
                    if m:
                        idx = int(m.group(1))
                    break
        except OSError:
            pass
        ranked.append((idx, node))
    ranked.sort()
    return [(i + 1, node) for i, (_idx, node) in enumerate(ranked)]


@method("gamepads.list", slow=True)
def _gamepads_list(params):
    """Connected, supported pads from the cached device walk + a LIVE DolphinBar
    probe (slow: the probe writes to each slot, ≤0.7 s per live remote)."""
    xport = _xport()
    out, seen = [], set()
    for d in dv.enumerate_devices():
        vid, pid = d.vid, d.pid
        skip = (vid == 0x16c0 or vid == 0x28de and pid != 0x1205
                or d.is_mad_virtual)  # the wii-nav-bridge's own uinput pad
        # The Deck's own pad IS testable (28de:1205) — but lizard-mode nodes
        # without face buttons are not.
        if vid == 0x045e and pid == 0x02a1 and xport and dv.port_of(d.phys) == xport:
            skip = True  # The X-Arcade has its own page.
        prof = _profile_for(vid, pid, d.name) if (d.has_face_btn and not skip) else None
        if prof:
            key = d.uniq or dv.port_of(d.phys) or d.path
            if key in seen:
                continue
            seen.add(key)
            idtail = d.uniq[-8:] if d.uniq else (dv.port_of(d.phys) or
                                                 d.path.rsplit("/", 1)[-1])
            out.append({"kind": "pad", "path": d.path, "name": d.name,
                        "uniq": d.uniq or "", "idtail": idtail,
                        "profile": dict(prof,
                                        icon_path=resolve_art(
                                            [f"icons/{prof['icon']}"]))})
    wprof = _profile_for(0x057e, 0x0306, "Wii Remote")
    # Never write probe reports into a slot while a tester stream is live.
    # tester.stop is FAST (inline, in-order) so the picker's post-pop scan is
    # already ordered after cleanup; this covers a scan still in flight on the
    # worker pool when a test starts.
    if _active["stream"] is not None:
        return {"pads": out}
    for slot, node in _db_slots():
        try:
            fd = os.open(node, os.O_RDWR)
        except OSError:
            continue
        try:
            os.write(fd, WiiSlotReader.RPT_STATUS)  # ok=live; raises if empty/asleep
            kind = _wii_probe_kind(fd, quiet=False)  # The nav bridge co-reads.
        except OSError:
            continue
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        acc = {"nunchuk": " + Nunchuk", "classic": " + Classic"}.get(kind, "")
        # Short name (the picker tile must show it in full); the bar/slot
        # context lives in idtail (tile sublabel + tester header).
        out.append({"kind": "wii", "slot": slot, "node": node, "ext": kind,
                    "name": f"Wii Remote{acc}",
                    "idtail": f"DolphinBar slot {slot}", "uniq": "",
                    "profile": dict(wprof,
                                    icon_path=resolve_art(
                                        [f"icons/{wprof['icon']}"]))})
    return {"pads": out}


# ── sprite layouts / positions / calibration files ──

def _read_json(path: Path) -> dict:
    try:
        if path.is_file():
            d = json.loads(path.read_text())
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _baked_positions(key: str) -> dict:
    d = _read_json(GP_DEFAULTS / f"gp-{key}-positions.json")
    return {k: v for k, v in d.items() if isinstance(v, list) and len(v) == 2}


def _sprite_dir_paths(sprite_dir: str) -> dict:
    """{stem: abs path} for every sprite in the dir (art-chain resolution of
    the DIR itself, then a flat listing — mirrors _gp_load_into)."""
    from .systems_cmds import art_dirs
    found = None
    for base in art_dirs():
        cand = Path(base) / "icons" / sprite_dir
        if cand.is_dir():
            found = cand
            break
    if found is None:
        return {}
    return {p.stem: str(p) for p in sorted(found.iterdir()) if p.suffix == ".png"}


@method("gamepads.layout")
def _gamepads_layout(params):
    """Everything the test page needs to draw one pad: sprite paths (base/back
    + stems), merged positions (saved > baked), the P2 flag, and the same for
    an accessory kind when requested."""
    key = params["key"]
    sprite_dir = params["dir"]
    sprites = _sprite_dir_paths(sprite_dir)
    saved = _read_json(CONTROL_PANEL / f"gp-{key}-positions.json")
    positions = {**_baked_positions(key), **saved}
    result = {"key": key, "sprites": sprites, "positions": positions}
    ext = params.get("ext")
    if ext in ("nunchuk", "classic"):
        sub = f"{sprite_dir}/{ext}-tester"
        result["ext"] = {
            "kind": ext, "sprites": _sprite_dir_paths(sub),
            "allowed": sorted(EXT_BTNS.get(ext, frozenset())),
            "positions": {**_baked_positions(ext),
                          **_read_json(CONTROL_PANEL / f"gp-{ext}-positions.json")}}
    uniq = params.get("uniq", "")
    if uniq:
        overrides = _read_json(CONTROL_PANEL / "gp-p2-units.json")
        name = params.get("name", "")
        tokens = (name or "").lower().replace("#", " ").split()
        auto = any(t in ("p2", "ii", "2", "player2") for t in tokens) or \
            "player 2" in (name or "").lower()
        result["p2"] = bool(overrides.get(uniq, auto))
    return result


@method("gamepads.positions_save")
def _gamepads_positions_save(params):
    """{key, positions:{stem:[nx,ny]}} → control-panel/gp-<key>-positions.json
    (the SAME format the Tk tester and the baked defaults use)."""
    key = params["key"]
    positions = params.get("positions")
    if not isinstance(positions, dict):
        raise RpcError("EINVAL", "positions must be an object")
    clean = {k: [round(float(v[0]), 4), round(float(v[1]), 4)]
             for k, v in positions.items()
             if isinstance(v, list) and len(v) == 2}
    _write_json(CONTROL_PANEL / f"gp-{key}-positions.json", clean)
    return {"message": f"Saved {len(clean)} positions."}


@method("gamepads.set_p2")
def _gamepads_set_p2(params):
    overrides = _read_json(CONTROL_PANEL / "gp-p2-units.json")
    overrides[params["uniq"]] = bool(params["on"])
    _write_json(CONTROL_PANEL / "gp-p2-units.json", overrides)
    return {"p2": bool(params["on"])}


# X-Arcade fixed spot table (key, label, nx, ny) — verbatim Tk defaults.
def _xa_default_spots() -> list:
    spots = [("p1_stick", "P1 stick", 0.115, 0.34),
             ("p2_stick", "P2 stick", 0.885, 0.34)]
    p1cols = (0.205, 0.252, 0.299, 0.346)
    rows = (0.36, 0.52)
    for r, ry in enumerate(rows):
        for c, cx in enumerate(p1cols):
            bn = r * 4 + c + 1
            spots.append((f"p1_b{bn}", f"P1 b{bn}", cx, ry))
            spots.append((f"p2_b{bn}", f"P2 b{bn}", 1.0 - cx, ry))
    spots += [("mouse1", "Mouse1 (top-left)", 0.475, 0.135),
              ("mouse2", "Mouse2 (top-right)", 0.525, 0.135),
              ("p1_coin", "P1 coin", 0.475, 0.26),
              ("p2_coin", "P2 coin", 0.525, 0.26),
              ("mouse3", "Mouse3 (red)", 0.905, 0.135),
              ("trackball", "Trackball", 0.50, 0.42),
              ("side_l1", "L side 1", 0.045, 0.30), ("side_l2", "L side 2", 0.045, 0.52),
              ("side_r1", "R side 1", 0.955, 0.30), ("side_r2", "R side 2", 0.955, 0.52)]
    return spots


@method("xarcade.layout")
def _xarcade_layout(params):
    saved = _read_json(CONTROL_PANEL / "xarcade-positions.json")
    spots = []
    for key, label, nx, ny in _xa_default_spots():
        s = saved.get(key)
        spots.append({"key": key, "label": label,
                      "x": s[0] if s else nx, "y": s[1] if s else ny})
    return {"overlay": resolve_art(["icons/x-arcade-tester/base.png",
                                    "icons/x-arcade-tester-overlay.png"]),
            "sprites": _sprite_dir_paths("x-arcade-tester"),
            "spots": spots, "xbox_mode": _xa_mode()}


def _xa_mode() -> bool:
    """Metadata-only: the cab's 045e:02a1 gamepad present at the identified port."""
    xport = _xport()
    try:
        for rec in dv.enumerate_devices():
            if (rec.vid, rec.pid) == (0x045e, 0x02a1) and (
                    not xport or dv.port_of(rec.phys) == xport):
                return True
    except Exception:
        pass
    return False


@method("xarcade.status")
def _xarcade_status(params):
    return {"xbox_mode": _xa_mode()}


@method("xarcade.positions_save")
def _xarcade_positions_save(params):
    positions = params.get("positions")
    if not isinstance(positions, dict):
        raise RpcError("EINVAL", "positions must be an object")
    clean = {k: [round(float(v[0]), 4), round(float(v[1]), 4)]
             for k, v in positions.items()
             if isinstance(v, list) and len(v) == 2}
    _write_json(CONTROL_PANEL / "xarcade-positions.json", clean)
    return {"message": f"Saved {len(clean)} positions."}


# ── the live tester streams ──

_active = {"stream": None}  # One tester at a time.


class _TesterBase(Stream):
    """30 Hz coalesced sprite snapshots. Subclasses fill self.spots/self.sticks
    and may end the run by returning an 'ended' reason from pump()."""

    HZ = 1 / 30

    def __init__(self):
        super().__init__()
        self.spots = {}      # stem -> bool
        self.sticks = {}     # stick key -> token ("rest","up","dl",...)
        self.dirty = True
        self._last_push = {}
        self._cal_armed = None  # spot awaiting an input bind (calibrate)

    def snapshot(self) -> dict:
        return {"spots": {k: v for k, v in self.spots.items()},
                "sticks": dict(self.sticks)}

    def push_if_dirty(self, extra: dict | None = None):
        snap = self.snapshot()
        if extra or snap != self._last_push:
            self._last_push = snap
            data = dict(snap)
            if extra:
                data.update(extra)
            self.emit(data)

    def set_spot(self, spot: str, on: bool):
        if spot and self.spots.get(spot) != bool(on):
            self.spots[spot] = bool(on)
            self.dirty = True

    def set_stick(self, key: str, token: str):
        if self.sticks.get(key) != token:
            self.sticks[key] = token
            self.dirty = True


def _norm(absinfo, value) -> float:
    if absinfo is None or value is None or absinfo.max == absinfo.min:
        return 0.0
    mid = (absinfo.max + absinfo.min) / 2
    return max(-1.0, min(1.0, (value - mid) / ((absinfo.max - absinfo.min) / 2)))


_STICK_TOKENS = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right",
                 (-1, -1): "ul", (1, -1): "ur", (-1, 1): "dl", (1, 1): "dr"}


class PadTesterStream(_TesterBase):
    """Generic evdev pad: grab ONE node (150 ms delayed), map events → sprite
    stems via the calibration file + the stem-adaptive defaults; hold Start 6 s
    ends the test; the Steam Deck pad auto-stops after ~20 s idle."""

    def __init__(self, path: str, key: str, stems: list):
        super().__init__()
        self.path = path
        self.key = key
        self.stems = set(stems)
        self.locked = False
        self.dev = None
        self.absinfo = {}
        self.absval = {}
        self.cal = _read_json(CONTROL_PANEL / f"gp-{key}-calib.json")

    # Stem-adaptive default mapping (Tk _gp_default_spot).
    def _default_spot(self, code):
        from evdev import ecodes as e
        stems = self.stems

        def pick(*candidates):
            return next((c for c in candidates if c in stems), None)

        sony = "circle" in stems or "triangle" in stems
        if sony:
            m = {e.BTN_SOUTH: pick("x"), e.BTN_EAST: pick("circle"),
                 e.BTN_NORTH: pick("triangle"), e.BTN_WEST: pick("square")}
        else:
            m = {e.BTN_A: pick("a"), e.BTN_B: pick("b"), e.BTN_X: pick("x"),
                 e.BTN_Y: pick("y")}
        m.update({e.BTN_TL: pick("l1", "l"), e.BTN_TR: pick("r1", "r"),
                  e.BTN_TL2: pick("l2", "zl"), e.BTN_TR2: pick("r2", "zr"),
                  e.BTN_SELECT: pick("select", "minus", "back"),
                  e.BTN_START: pick("start", "plus"),
                  e.BTN_MODE: pick("guide", "home", "steam"),
                  e.BTN_THUMBL: pick("l3"), e.BTN_THUMBR: pick("r3"),
                  e.BTN_DPAD_UP: pick("dpadup"), e.BTN_DPAD_DOWN: pick("dpaddown"),
                  e.BTN_DPAD_LEFT: pick("dpadleft"),
                  e.BTN_DPAD_RIGHT: pick("dpadright")})
        return m.get(code)

    def _update_sticks(self):
        from evdev import ecodes as e
        for stick, ax, ay in (("lstick", e.ABS_X, e.ABS_Y),
                              ("rstick", e.ABS_RX, e.ABS_RY)):
            nx = _norm(self.absinfo.get(ax), self.absval.get(ax))
            ny = _norm(self.absinfo.get(ay), self.absval.get(ay))
            T = 0.5
            dx = -1 if nx < -T else (1 if nx > T else 0)
            dy = -1 if ny < -T else (1 if ny > T else 0)
            self.set_stick(stick, _STICK_TOKENS.get((dx, dy), "rest"))
        # D-pad from the hat; stickless pads (FC30) ride ABS_X/Y instead.
        stickless = not any(s.startswith("lstick_") for s in self.stems)

        def sign(hat, ax):
            h = self.absval.get(hat, 0)
            if h:
                return -1 if h < 0 else 1
            if stickless:
                v = _norm(self.absinfo.get(ax), self.absval.get(ax))
                return -1 if v < -0.5 else (1 if v > 0.5 else 0)
            return 0

        hx = sign(e.ABS_HAT0X, e.ABS_X)
        hy = sign(e.ABS_HAT0Y, e.ABS_Y)
        for spot, on in (("dpadleft", hx < 0), ("dpadright", hx > 0),
                         ("dpadup", hy < 0), ("dpaddown", hy > 0)):
            if spot in self.stems:
                self.set_spot(spot, on)

    def _cal_capture(self, ev) -> bool:
        from evdev import ecodes as e
        if self._cal_armed is None:
            return False
        input_key = None
        if ev.type == e.EV_KEY and ev.value == 1:
            input_key = f"k{ev.code}"
        elif ev.type == e.EV_ABS and ev.code not in (
                e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY, e.ABS_HAT0X, e.ABS_HAT0Y):
            ai = self.absinfo.get(ev.code)
            if ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.5:
                input_key = f"a{ev.code}"
        if input_key is None:
            return False
        spot = self._cal_armed
        self._cal_armed = None
        self.cal = {k: v for k, v in self.cal.items() if k != input_key}
        self.cal[input_key] = spot
        self.emit({"bound": {"input": input_key, "spot": spot}})
        return True

    def _event(self, ev):
        from evdev import ecodes as e
        if self._cal_capture(ev):
            return
        if ev.type == e.EV_KEY:
            spot = self.cal.get(f"k{ev.code}") or self._default_spot(ev.code)
            if spot:
                self.set_spot(spot, bool(ev.value))
        elif ev.type == e.EV_ABS:
            self.absval[ev.code] = ev.value
            if ev.code in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY,
                           e.ABS_HAT0X, e.ABS_HAT0Y):
                self._update_sticks()
            else:
                spot = self.cal.get(f"a{ev.code}")
                if spot is None and ev.code in (e.ABS_Z, e.ABS_RZ):
                    cands = ("l2", "zl") if ev.code == e.ABS_Z else ("r2", "zr")
                    spot = next((c for c in cands if c in self.stems), None)
                if spot:
                    ai = self.absinfo.get(ev.code)
                    self.set_spot(spot, ai is not None and
                                  ev.value > ai.min + (ai.max - ai.min) * 0.4)

    def run(self):
        import evdev
        from evdev import ecodes as e
        try:
            self.dev = evdev.InputDevice(self.path)
            os.set_blocking(self.dev.fd, False)
        except Exception as ex:
            self.emit({"ended": "open_failed", "message":
                       f"Couldn't open that pad — reconnect and reopen. ({ex})"})
            return
        self.absinfo = dict(self.dev.capabilities().get(e.EV_ABS, []))
        # 150 ms delayed grab: the press that started the test still reaches
        # SDL as a clean down+up pair (no stale-DOWN runaway scroll).
        if self.stopped.wait(0.15):
            return
        try:
            self.dev.grab()
        except Exception:
            self.emit({"ended": "grab_failed", "message":
                       "Couldn't grab the pad (in use elsewhere?). Close other "
                       "apps + retry."})
            return
        # Steam Input reads pads via hidraw ABOVE the evdev grab — the virtual
        # pad would still navigate the panel. Lock ALL panel input for the
        # test; the backend escapes (Start 6 s / idle) are the way out.
        # cleanup() ALWAYS unlocks.
        self.locked = True
        event("input.lock", {"locked": True, "nav": True})
        self.emit({"ready": True})
        start_held_t0 = None
        l1_down = False
        l2_down = False
        combo_t0 = None           # both L1+L2 held — the FC30-safe escape
        COMBO_SECS = 6.0          # avoids Start entirely (FC30 Start = power-off)
        idle_ticks = 0
        while not self.stopped.wait(self.HZ):
            changed = False
            try:
                for ev in self.dev.read():
                    changed = True
                    if ev.type == e.EV_KEY and ev.value in (0, 1):
                        if ev.code == e.BTN_START:
                            start_held_t0 = time.monotonic() if ev.value == 1 else None
                        elif ev.code == e.BTN_TL:
                            l1_down = (ev.value == 1)
                        elif ev.code == e.BTN_TL2:
                            l2_down = (ev.value == 1)
                    self._event(ev)
            except BlockingIOError:
                pass
            except OSError:
                self.emit({"ended": "device_lost", "message":
                           "Pad disconnected — test ended."})
                return
            if changed:
                idle_ticks = 0
            else:
                idle_ticks += 1
                if self.key == "steamdeck" and idle_ticks > 666:
                    self.emit({"ended": "idle", "message":
                               "Auto-stopped after ~20 s idle — Deck pad released."})
                    return
            # Two escape gestures: L1+L2 (the FC30-safe combo — never touches
            # Start, whose hold powers an 8BitDo FC30 off) OR Start alone (the
            # original, for pads whose Start isn't a power button).
            both = l1_down and l2_down
            if both and combo_t0 is None:
                combo_t0 = time.monotonic()
            elif not both:
                combo_t0 = None
            extra = None
            now = time.monotonic()
            if combo_t0 is not None:
                remaining = COMBO_SECS - (now - combo_t0)
                if remaining <= 0:
                    self.emit(dict(self.snapshot(), ended="escape", message=
                              "Test ended (held L1+L2) — controller released."))
                    return
                extra = {"countdown": int(remaining) + 1}
            elif start_held_t0 is not None:
                remaining = 6.0 - (now - start_held_t0)
                if remaining <= 0:
                    self.emit(dict(self.snapshot(), ended="escape", message=
                              "Test ended (held Start) — controller released."))
                    return
                extra = {"countdown": int(remaining) + 1}
            if self.dirty or extra:
                self.dirty = False
                self.push_if_dirty(extra)

    def cleanup(self):
        if self.locked:
            self.locked = False
            event("input.lock", {"locked": False})
        if self.dev is not None:
            try:
                self.dev.ungrab()
            except Exception:
                pass
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        if _active["stream"] == self.token:
            _active["stream"] = None


class XArcadeTesterStream(_TesterBase):
    """The X-Arcade in Xbox mode: grab the cab's GAMEPAD nodes (the trackball
    mouse stays ungrabbed so the Deck cursor lives — but is still read so its
    sprites light); P1/P2 tags by USB interface; P1+P2 Start 3 s ends."""

    def __init__(self):
        super().__init__()
        self.devs = []  # [{dev, tag, mouse, absinfo, absval}]
        self.cal = _read_json(CONTROL_PANEL / "xarcade-calib.json")
        self.locked = False
        self.trackball_until = 0.0

    def _spot_for(self, tag, code):
        from evdev import ecodes as e
        if tag not in ("P1", "P2"):
            return None
        order = {e.BTN_EAST: 1, e.BTN_NORTH: 2, e.BTN_TL2: 3, e.BTN_TR2: 4,
                 e.BTN_SOUTH: 5, e.BTN_WEST: 6, e.BTN_TL: 7, e.BTN_TR: 8}
        n = order.get(code)
        if n:
            return f"{tag.lower()}_b{n}"
        if code == e.BTN_SELECT:
            return "side_l1" if tag == "P1" else "side_r1"
        if code == e.BTN_START:
            return "mouse1" if tag == "P1" else "mouse2"
        return None

    def _cal_capture(self, od, ev) -> bool:
        from evdev import ecodes as e
        if self._cal_armed is None:
            return False
        input_key = None
        if ev.type == e.EV_KEY and ev.value == 1:
            input_key = f"{od['tag']}:k{ev.code}"
        elif ev.type == e.EV_ABS and ev.code in (e.ABS_Z, e.ABS_RZ):
            ai = od["absinfo"].get(ev.code)
            if ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.5:
                input_key = f"{od['tag']}:a{ev.code}"
        if input_key is None:
            return False
        spot = self._cal_armed
        self._cal_armed = None
        self.cal = {k: v for k, v in self.cal.items() if k != input_key}
        self.cal[input_key] = spot
        self.emit({"bound": {"input": input_key, "spot": spot}})
        return True

    def _update_stick(self, od):
        from evdev import ecodes as e
        ax = od["absval"]

        def norm(code):
            return _norm(od["absinfo"].get(code), ax.get(code))

        T = 0.5
        nx = norm(e.ABS_X)
        if abs(nx) < T:
            nx = norm(e.ABS_HAT0X)
        ny = norm(e.ABS_Y)
        if abs(ny) < T:
            ny = norm(e.ABS_HAT0Y)
        dx = -1 if nx < -T else (1 if nx > T else 0)
        dy = -1 if ny < -T else (1 if ny > T else 0)
        token = _STICK_TOKENS.get((dx, dy), "rest")
        self.set_stick(f"{od['tag'].lower()}_stick", token)

    def _event(self, od, ev):
        from evdev import ecodes as e
        if self._cal_capture(od, ev):
            return
        tag = od["tag"]
        if ev.type == e.EV_KEY:
            spot = self.cal.get(f"{tag}:k{ev.code}") or (
                {e.BTN_LEFT: "side_l2", e.BTN_RIGHT: "side_r2",
                 e.BTN_MIDDLE: "mouse3"}.get(ev.code)
                if tag == "M" else self._spot_for(tag, ev.code))
            if spot:
                self.set_spot(spot, bool(ev.value))
        elif ev.type == e.EV_ABS and tag in ("P1", "P2"):
            od["absval"][ev.code] = ev.value
            if ev.code in (e.ABS_Z, e.ABS_RZ):
                ai = od["absinfo"].get(ev.code)
                on = ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.4
                spot = self.cal.get(f"{tag}:a{ev.code}") or \
                    f"{tag.lower()}_b{3 if ev.code == e.ABS_Z else 4}"
                self.set_spot(spot, on)
            else:
                self._update_stick(od)
        elif ev.type == e.EV_REL and tag == "M":
            self.trackball_until = time.monotonic() + 0.16
            self.set_spot("trackball", True)

    def run(self):
        import evdev
        from evdev import ecodes as e
        xport = _xport()
        try:
            candidates = sorted(
                rec.path for rec in dv.enumerate_devices()
                if (rec.vid, rec.pid) in XARCADE_VIDPIDS and (
                    (rec.vid, rec.pid) != (0x045e, 0x02a1) or not xport or
                    dv.port_of(rec.phys) == xport))
        except Exception:
            candidates = []
        if self.stopped.wait(0.15):  # Delayed grab, same rationale as pads.
            return
        failed = 0
        opened = []
        for path in candidates:
            try:
                d = evdev.InputDevice(path)
            except Exception:
                continue
            try:
                os.set_blocking(d.fd, False)
                if (d.info.vendor, d.info.product) != (0x1241, 0x1111):
                    d.grab()
            except Exception:
                failed += 1
                try:
                    d.close()
                except Exception:
                    pass
                continue
            caps = d.capabilities()
            opened.append({"dev": d, "path": path,
                           "mouse": e.BTN_LEFT in caps.get(e.EV_KEY, []),
                           "absinfo": dict(caps.get(e.EV_ABS, [])), "absval": {}})
        if not opened:
            self.emit({"ended": "no_device", "message":
                       "No X-Arcade nodes found — is it connected and in Xbox mode?"})
            return
        # P1/P2 by parent USB interface (00 < 01) — survives replug; event
        # number is the tiebreak.
        from ..devices import usb_iface_num

        def evnum(p):
            s = "".join(ch for ch in p.rsplit("/", 1)[-1] if ch.isdigit())
            return int(s) if s else 0

        def iface(p):
            n = usb_iface_num(p)
            return 99 if n is None else n

        n = 0
        for od in sorted(opened, key=lambda o: (o["mouse"], iface(o["path"]),
                                                evnum(o["path"]))):
            if od["mouse"]:
                od["tag"] = "M"
            else:
                n += 1
                od["tag"] = f"P{n}"
        self.devs = opened
        # The cab's Xbox-mode pads run through Steam Input too (hidraw, above
        # the grab) — lock the panel; P1+P2 Start 3 s is the way out.
        self.locked = True
        event("input.lock", {"locked": True, "nav": True})
        self.emit({"ready": True, "grab_failed": failed})
        quit_t0 = None
        lost = 0
        while not self.stopped.wait(self.HZ):
            lost = 0
            for od in self.devs:
                try:
                    for ev in od["dev"].read():
                        self._event(od, ev)
                except BlockingIOError:
                    pass
                except OSError:
                    lost += 1
            if lost and lost == len(self.devs):
                self.emit({"ended": "device_lost", "message":
                           "X-Arcade disconnected — test ended."})
                return
            if self.spots.get("trackball") and time.monotonic() > self.trackball_until:
                self.set_spot("trackball", False)
            # P1+P2 Start (the centre icon buttons) held together 3 s ends.
            both = self.spots.get("mouse1") and self.spots.get("mouse2")
            extra = None
            if both:
                if quit_t0 is None:
                    quit_t0 = time.monotonic()
                remaining = 3.0 - (time.monotonic() - quit_t0)
                if remaining <= 0:
                    self.emit(dict(self.snapshot(), ended="escape", message=
                              "Test ended (P1+P2 Start) — X-Arcade released."))
                    return
                extra = {"countdown": int(remaining) + 1}
            else:
                quit_t0 = None
            if self.dirty or extra:
                self.dirty = False
                self.push_if_dirty(extra)

    def cleanup(self):
        if self.locked:
            self.locked = False
            event("input.lock", {"locked": False})
        for od in self.devs:
            try:
                od["dev"].ungrab()
            except Exception:
                pass
            try:
                od["dev"].close()
            except Exception:
                pass
        self.devs = []
        if _active["stream"] == self.token:
            _active["stream"] = None


class WiiTesterStream(_TesterBase):
    """DolphinBar Wii Remote: WiiSlotReader thread drives the remote; this
    stream forwards its snapshots (core/ext stems, accessory kind, sticks) and
    status changes; holding + for 6 s ends."""

    def __init__(self, slot: int, node: str):
        super().__init__()
        self.slot = slot
        self.node = node
        self.reader = None

    def run(self):
        node = dict(_db_slots()).get(self.slot, self.node)
        # Claim the slot's NODE PATH so the wii-nav-bridge releases it for
        # the test's duration (a second remote keeps navigating). The path —
        # not the slot number — is the claim: the tester's slot numbering is
        # HID_PHYS-ranked while a node sort is lexicographic; they disagree
        # whenever hidraw numbering crosses 10. Atomic write (the bridge
        # polls every tick); removed in cleanup() if still ours.
        try:
            tmp = _TESTER_SLOT_FILE.with_suffix(".tmp")
            tmp.write_text(f"{node}\n")
            os.replace(tmp, _TESTER_SLOT_FILE)
        except OSError:
            pass
        self._claimed_node = node
        # Give the bridge a beat to drop its reader before we start writing
        # (it honors the claim within one 33 ms tick).
        time.sleep(0.1)
        self.reader = WiiSlotReader(node)
        self.reader.start()
        # Settle the extension detection before trusting the kind. The page's
        # picker pre-built the accessory panel from its own probe, but a FRESH
        # reader reports kind="none" for the first tens of ms (before the
        # extension-ID reply lands) and THEN the real kind — which the view
        # would render as hide-then-show (the "respawn" flicker). Poll until
        # the slot is live + a short post-live beat, then SEED the debounced
        # kind so the first emitted kind is already the settled one.
        stable_kind = "none"
        live_since = None
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if self.stopped.wait(0.03):
                return
            s = self.reader.snapshot()
            if s["status"] == "live":
                stable_kind = s["kind"]
                if live_since is None:
                    live_since = time.monotonic()
                if time.monotonic() - live_since >= 0.12:
                    break
        self.emit({"ready": True})
        last_seq = -1
        last_status = None
        quit_t0 = None
        # Debounce further kind changes (unplug/replug mid-test): commit a new
        # kind only after it persists, so a momentary mis-read can't flicker
        # the accessory panel. The 30 Hz loop drives the timer even when idle.
        KIND_DEBOUNCE = 0.15
        pending_kind = None
        pending_t0 = 0.0
        while not self.stopped.wait(self.HZ):
            snap = self.reader.snapshot()
            raw_kind = snap["kind"]
            kind_changed = False
            if raw_kind != stable_kind:
                if raw_kind != pending_kind:
                    pending_kind = raw_kind
                    pending_t0 = time.monotonic()
                elif time.monotonic() - pending_t0 >= KIND_DEBOUNCE:
                    stable_kind = raw_kind
                    pending_kind = None
                    kind_changed = True
            else:
                pending_kind = None
            extra = {}
            if snap["status"] != last_status:
                last_status = snap["status"]
                extra["status"] = snap["status"]
            if snap["seq"] != last_seq or kind_changed:
                last_seq = snap["seq"]
                extra["wii"] = {"core": sorted(snap["core"]),
                                "ext": sorted(snap["ext"]),
                                "kind": stable_kind,  # debounced, not raw
                                "lstick": snap["lstick"],
                                "rstick": snap["rstick"]}
            plus_held = "plus" in (snap.get("core") or ())
            if plus_held:
                if quit_t0 is None:
                    quit_t0 = time.monotonic()
                remaining = 6.0 - (time.monotonic() - quit_t0)
                if remaining <= 0:
                    self.emit({"ended": "escape", "message":
                               "Test ended (held +) — Wii Remote released."})
                    return
                extra["countdown"] = int(remaining) + 1
            else:
                quit_t0 = None
            if extra:
                self.emit(extra)

    def cleanup(self):
        if self.reader is not None:
            self.reader.stop(timeout=0.6)
            self.reader = None
        # Hand the slot back ONLY if the claim is still ours: a quick
        # STOP→START restart's new stream may have re-written it already
        # (this cleanup runs AFTER the new run() — stop_stream doesn't join).
        try:
            if (_TESTER_SLOT_FILE.read_text().strip() ==
                    getattr(self, "_claimed_node", None)):
                _TESTER_SLOT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        if _active["stream"] == self.token:
            _active["stream"] = None


@method("tester.start", slow=True)
def _tester_start(params):
    """Start ONE live tester stream (any previous one is stopped first).
    kinds: pad {path, key, stems} | xarcade | wii {slot, node}."""
    kind = params.get("kind")
    if _active["stream"] is not None:
        stop_stream(_active["stream"])
        time.sleep(0.2)  # Let the old grab release before the new one.
    if kind == "pad":
        stream = PadTesterStream(params["path"], params.get("key", ""),
                                 params.get("stems", []))
    elif kind == "xarcade":
        stream = XArcadeTesterStream()
    elif kind == "wii":
        stream = WiiTesterStream(int(params["slot"]), params.get("node", ""))
    else:
        raise RpcError("EINVAL", f"unknown tester kind {kind!r}")
    _active["stream"] = stream.token
    return {"stream": stream.start()}


@method("wii.barmode")
def _wii_barmode(params):
    """Best-effort DolphinBar mode for the tester/picker indicator. Mode 4 is
    definitive (the 4 hidraw slots exist); other modes are detected as a
    Mayflash USB presence without slots — disambiguating 1/2 vs 3 needs the
    bar's descriptors in those modes (refine when observed; deck-docs/wiimote.md)."""
    if dv._dolphinbar_slot_nodes():
        return {"mode": "4", "label": "DolphinBar mode 4",
                "explanation": "Dolphin passthrough — MAD reads the remotes "
                               "directly: tester + menu navigation active."}
    if dv._dolphinbar_usb_present():
        return {"mode": "1-3", "label": "DolphinBar mode 1, 2 or 3",
                "explanation": "The bar presents remotes as a mouse/standard "
                               "gamepad. Switch it to MODE 4 for the tester, "
                               "menu navigation and Dolphin real-Wiimote."}
    return {"mode": "none", "label": "No DolphinBar detected",
            "explanation": "Plug the Mayflash DolphinBar in (USB) and set it "
                           "to MODE 4."}


@method("wii.probe_ext", slow=True)
def _wii_probe_ext(params):
    """One-shot accessory probe of ONE DolphinBar slot — the wii test page
    polls this while idle so accessory hotplug updates its layout (the slot
    emits no udev event). probed:false when the slot can't be read or any
    tester stream is live (the probe writes report-mode bytes)."""
    if _active["stream"] is not None:
        return {"probed": False}
    # Re-resolve slot→node like tester.start: remotes sleeping/reconnecting
    # re-enumerate hidraw, so the page's construction-time node can go stale.
    node = params.get("node", "")
    if "slot" in params:
        node = dict(_db_slots()).get(int(params["slot"]), node)
    try:
        fd = os.open(node, os.O_RDWR)
    except OSError:
        return {"probed": False}
    try:
        os.write(fd, WiiSlotReader.RPT_STATUS)  # raises if empty/asleep
        kind = _wii_probe_kind(fd, quiet=False)  # The nav bridge co-reads.
    except OSError:
        return {"probed": False}
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return {"probed": True, "ext": kind}


@method("tester.stop")
def _tester_stop(params):
    token = _active["stream"]
    if token is not None:
        stop_stream(token)
        return {"stopped": True}
    return {"stopped": False}


def _active_stream():
    from .rpc import _STREAMS, _STREAMS_LOCK
    with _STREAMS_LOCK:
        return _STREAMS.get(_active["stream"]) if _active["stream"] else None


@method("tester.calibrate")
def _tester_calibrate(params):
    """arm {spot}: the live stream binds the NEXT input to that spot (pushes
    {bound}); save: write the calib file; cancel: disarm."""
    action = params.get("action")
    stream = _active_stream()
    if stream is None or not isinstance(stream, (PadTesterStream,
                                                 XArcadeTesterStream)):
        raise RpcError("EINVAL", "no live pad/x-arcade tester")
    if action == "arm":
        stream._cal_armed = params["spot"]
        return {"armed": params["spot"]}
    if action == "cancel":
        stream._cal_armed = None
        return {"armed": None}
    if action == "save":
        if isinstance(stream, XArcadeTesterStream):
            _write_json(CONTROL_PANEL / "xarcade-calib.json", stream.cal)
        else:
            _write_json(CONTROL_PANEL / f"gp-{stream.key}-calib.json", stream.cal)
        return {"message": f"Calibration saved ({len(stream.cal)} bound)."}
    raise RpcError("EINVAL", f"unknown calibrate action {action!r}")
