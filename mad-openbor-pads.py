#!/usr/bin/env python3
"""OpenBOR pad merger: real pads in, canonical virtual pads out.

WHY THIS EXISTS
    OpenBOR-under-Proton binds one input per control and encodes it as
    601 + port*stride + offset, where `offset` depends on how the engine's SDL
    enumerates THAT pad and `port` on the order winebus happened to enumerate.
    Both are hostile to us:
      * the X-Arcade is TWO XInput halves whose port order Wine decides, so
        P1/P2 swap between launches (openbor.sh's old SDL_JOYSTICK_DEVICE pin
        was a no-op under Proton — verified 2026-07-16);
      * each pad family reports a different shape, so one map cannot fit all;
      * a stick and a d-pad cannot both drive "up" — one control, one binding.

    So we stop asking the game to cope. Each real pad gets a VIRTUAL twin with
    one canonical shape; the game is whitelisted to see ONLY the twins, in the
    order WE create them. Then:
      * ports are ours -> the X-Arcade half-swap is fixed by construction;
      * every player is identical -> ONE map per game serves all pads;
      * stick and d-pad both feed the twin's hat -> both drive movement.

PROVEN ON-DEVICE (2026-07-16, see deck-docs/openbor.md "winebus"):
    a uinput pad reaches OpenBOR under Proton, synthesized presses register
    (banked keycode 610 = ThumbR at offset 9), and winebus re-shapes ANY vpad
    into the canonical XInput view: 11 buttons / 6 axes / 1 hat, hat base 23,
    slots A,B,X,Y,LB,RB,Back,Start,ThumbL,ThumbR,Guide.

USAGE
    mad-openbor-pads.py --probe    # print the pad plan; exit 3 if no player pad
    mad-openbor-pads.py            # create the twins, pump, print READY, run
                                   # until SIGTERM (openbor.sh owns the lifetime)
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import re
import select
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evdev import AbsInfo, InputDevice, UInput
from evdev import ecodes as e

from lib import mad_paths
from lib.devices import enumerate_devices, joypads, usb_iface_num, vidpid
from lib.openbor_maps import (CLASS_OF_VIDPID, EVDEV_ABS_ROLE, EVDEV_BTN,
                              GEOM_XINPUT, HAPPY_HAT)
from lib.policy import load_merged
from lib.routing import is_xarcade, xarcade_port

VENDOR, PRODUCT, VERSION = 0x4D41, 0x0002, 0x0001
MAX_PADS = 4                       # OpenBOR's JOY_LIST_TOTAL

# Canonical token -> the evdev code the twin emits. winebus maps these to the
# XInput slots our offsets assume; it does NOT use raw code order (measured:
# BTN_THUMBR landed on slot 9, not its code-order index 10).
# NB the kernel's legacy aliases: BTN_X == BTN_NORTH(0x133), BTN_Y ==
# BTN_WEST(0x134) — that is why the per-family input tables differ, and why the
# twin emits NORTH for canonical "x".
BTN_CODE = {"a": e.BTN_SOUTH, "b": e.BTN_EAST, "x": e.BTN_NORTH,
            "y": e.BTN_WEST, "lb": e.BTN_TL, "rb": e.BTN_TR,
            "back": e.BTN_SELECT, "start": e.BTN_START,
            "thumbl": e.BTN_THUMBL, "thumbr": e.BTN_THUMBR,
            "guide": e.BTN_MODE}
AX_CODE = {"lx": e.ABS_X, "ly": e.ABS_Y, "lt": e.ABS_Z,
           "rx": e.ABS_RX, "ry": e.ABS_RY, "rt": e.ABS_RZ}

STICK_MIN, STICK_MAX = -32768, 32767
TRIG_MIN, TRIG_MAX = 0, 255
# Stick -> d-pad digitization. Engage high, release lower: without hysteresis a
# stick resting near the line chatters the hat every poll.
ENGAGE, RELEASE = 0.40, 0.30

LOG = mad_paths.storage("openbor", "logs") / "pads.log"
LOCK = mad_paths.storage("controller-router") / "openbor-pads.lock"


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(f"{msg}\n")
    except OSError:
        pass
    print(msg, file=sys.stderr, flush=True)


# ── the pad plan ─────────────────────────────────────────────────────────────

def class_of(dev) -> str | None:
    return CLASS_OF_VIDPID.get(vidpid(dev))


def _node_num(path: str) -> int:
    """The NUMERIC event-node index.

    Never sort these paths as strings: "event258" < "event30" lexically, so a
    string sort seats pads by collation instead of by node. That is not cosmetic
    — a pad's node number changes every time it reconnects (a DualSense that
    re-pairs can jump from event30 to event258), so string order reshuffled the
    player seats between launches. Observed on-device 2026-07-16: the same two
    DualSense pads took different seats on consecutive runs."""
    m = re.search(r"(\d+)$", path)
    return int(m.group(1)) if m else 1 << 30


def build_plan(devs, pad_classes, xport: str = "") -> list[tuple[object, str]]:
    """Real pads -> the ordered list whose index IS the OpenBOR player slot.

    Order is deterministic and ours (that is the whole point):
      1. the X-Arcade's halves by USB interface — :1.0 is P1, :1.1 is P2. Wine
         used to decide this and got it wrong at random; usb_iface_num is
         replug-stable, so the cabinet's own labelling now always wins.
      2. every other configured family, in `pad_classes` priority order, then
         by enumeration order within a family.
    Pads we have no translation table for are dropped (never guessed at).
    Capped at MAX_PADS."""
    pads = [d for d in joypads(devs) if class_of(d)]
    xa = [d for d in pads if xport and is_xarcade(d, xport)]
    xa.sort(key=lambda d: (usb_iface_num(d.path) if usb_iface_num(d.path) is not None else 9,
                           _node_num(d.path)))
    rest = [d for d in pads if d not in xa]

    def rank(d):
        vp = vidpid(d)
        try:
            i = [c for c in pad_classes if c != "x-arcade"].index(vp)
        except ValueError:
            i = len(pad_classes)
        return (i, _node_num(d.path))

    rest.sort(key=rank)
    return [(d, class_of(d)) for d in (xa + rest)][:MAX_PADS]


def describe_plan(plan) -> str:
    if not plan:
        return "  (no player pads)"
    out = []
    for i, (d, cls) in enumerate(plan):
        iface = usb_iface_num(d.path)
        tag = f" iface=:1.{iface}" if iface is not None else ""
        out.append(f"  P{i + 1}: {d.name} [{vidpid(d)} {cls}]{tag} {d.path}")
    return "\n".join(out)


# ── the twins ────────────────────────────────────────────────────────────────

def _caps() -> dict:
    stick = AbsInfo(0, STICK_MIN, STICK_MAX, 16, 128, 0)
    trig = AbsInfo(0, TRIG_MIN, TRIG_MAX, 0, 0, 0)
    hat = AbsInfo(0, -1, 1, 0, 0, 0)
    return {
        e.EV_KEY: sorted(BTN_CODE.values()),
        e.EV_ABS: [(e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_Z, trig),
                   (e.ABS_RX, stick), (e.ABS_RY, stick), (e.ABS_RZ, trig),
                   (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)],
    }


class Twin:
    """One canonical virtual pad, fed by exactly one real pad."""

    def __init__(self, slot: int, src: InputDevice, cls: str):
        self.slot, self.src, self.cls = slot, src, cls
        self.ui = UInput(_caps(), name=f"MAD OpenBOR P{slot + 1}",
                         vendor=VENDOR, product=PRODUCT, version=VERSION,
                         bustype=e.BUS_USB)
        self.dpad = [0, 0]     # from the real d-pad (hat or HAPPY buttons)
        self.stick = [0, 0]    # from the digitized left stick
        self.hat = [0, 0]      # what the twin currently reports
        self._rng = {}
        try:
            for code, info in src.capabilities().get(e.EV_ABS, []):
                self._rng[code] = (info.min, info.max)
        except OSError:
            pass

    # -- axis scaling: sources disagree (DS sticks 0..255 on some drivers,
    #    -32768..32767 on others); the twin must always speak one range.
    def _scale(self, code: int, val: int, lo: int, hi: int) -> int:
        smin, smax = self._rng.get(code, (lo, hi))
        if smax <= smin:
            return lo
        frac = (val - smin) / (smax - smin)
        return int(lo + frac * (hi - lo))

    def _frac(self, code: int, val: int) -> float:
        """Signed -1..1 position of a stick axis, whatever the source range."""
        smin, smax = self._rng.get(code, (STICK_MIN, STICK_MAX))
        if smax <= smin:
            return 0.0
        return ((val - smin) / (smax - smin)) * 2.0 - 1.0

    def _digitize(self, idx: int, frac: float) -> None:
        """Stick position -> -1/0/1, with a hysteresis band.

        Past ENGAGE always wins immediately (so a fast flick right->left maps
        straight to left, never through a dropped centre frame); inside the
        band we hold, which is what stops a stick resting on the line from
        chattering the hat every poll."""
        if frac >= ENGAGE:
            self.stick[idx] = 1
        elif frac <= -ENGAGE:
            self.stick[idx] = -1
        elif abs(frac) <= RELEASE:
            self.stick[idx] = 0
        # else: in the band — keep whatever we had

    def _push_hat(self) -> bool:
        """The twin's hat = real d-pad OR digitized stick, so BOTH drive
        movement through the game's single 'up' binding."""
        want = [self.dpad[i] or self.stick[i] for i in (0, 1)]
        if want == self.hat:
            return False
        for i, code in enumerate((e.ABS_HAT0X, e.ABS_HAT0Y)):
            if want[i] != self.hat[i]:
                self.ui.write(e.EV_ABS, code, want[i])
        self.hat = want
        return True

    def feed(self, ev) -> None:
        dirty = False
        if ev.type == e.EV_KEY:
            d = HAPPY_HAT.get(ev.code)
            if d is not None:                      # X-Arcade stick = buttons
                on = 1 if ev.value else 0
                if d in ("left", "right"):
                    self.dpad[0] = (-on if d == "left" else on) if ev.value else 0
                else:
                    self.dpad[1] = (-on if d == "up" else on) if ev.value else 0
                dirty = self._push_hat()
            else:
                tok = EVDEV_BTN.get(self.cls, {}).get(ev.code)
                if tok:
                    self.ui.write(e.EV_KEY, BTN_CODE[tok.split(":")[1]],
                                  1 if ev.value else 0)
                    dirty = True
        elif ev.type == e.EV_ABS:
            role = EVDEV_ABS_ROLE.get(ev.code)
            if role in ("hatx", "haty"):
                self.dpad[0 if role == "hatx" else 1] = (
                    0 if ev.value == 0 else (1 if ev.value > 0 else -1))
                dirty = self._push_hat()
            elif role in ("lx", "ly"):
                self.ui.write(e.EV_ABS, AX_CODE[role],
                              self._scale(ev.code, ev.value, STICK_MIN, STICK_MAX))
                self._digitize(0 if role == "lx" else 1, self._frac(ev.code, ev.value))
                self._push_hat()
                dirty = True
            elif role in ("rx", "ry"):
                self.ui.write(e.EV_ABS, AX_CODE[role],
                              self._scale(ev.code, ev.value, STICK_MIN, STICK_MAX))
                dirty = True
            elif role in ("lt", "rt"):
                self.ui.write(e.EV_ABS, AX_CODE[role],
                              self._scale(ev.code, ev.value, TRIG_MIN, TRIG_MAX))
                dirty = True
        if dirty:
            self.ui.syn()

    def neutralize(self) -> None:
        """Release EVERYTHING the twin is reporting.

        The analog axes matter as much as the buttons: when a pad vanishes
        mid-game its twin stays alive (removing it would renumber the other
        players), so a latched ABS_RZ would leave SPECIAL held down forever —
        the character stuck mid-move with no pad left to release it. 0 is the
        correct rest value for both declared ranges (sticks centre at 0 in
        -32768..32767, triggers rest at 0 in 0..255)."""
        try:
            for code in BTN_CODE.values():
                self.ui.write(e.EV_KEY, code, 0)
            for code in (*AX_CODE.values(), e.ABS_HAT0X, e.ABS_HAT0Y):
                self.ui.write(e.EV_ABS, code, 0)
            self.ui.syn()
            self.dpad, self.stick, self.hat = [0, 0], [0, 0], [0, 0]
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.ui.close()
        except OSError:
            pass


# ── main ─────────────────────────────────────────────────────────────────────

def _plan_now():
    pol = load_merged()
    be = pol.get("backends", {}).get("openbor", {})
    devs = enumerate_devices()
    return build_plan(devs, be.get("pad_classes", []), xarcade_port(pol))


def main(argv: list[str]) -> int:
    plan = _plan_now()
    if "--probe" in argv:
        print(describe_plan(plan))
        return 0 if plan else 3
    if not plan:
        log("openbor-pads: no player pads — nothing to merge")
        return 3

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("openbor-pads: another instance holds the lock — exiting")
        return 4
    # Never outlive the launcher. This is what covers the deaths openbor.sh's
    # trap cannot (SIGKILL, SIGHUP): without it an orphaned merger keeps the
    # EVIOCGRAB on every pad, muting them rig-wide — ES-DE included — with no
    # working controller left to kill it.
    # REQUIRES openbor.sh to `exec` us, so our parent really is that script and
    # not a bash subshell wrapping it (verified 2026-07-16: without exec the
    # parent is a subshell that cannot predecease us, so this never fires).
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG

    log(f"openbor-pads: plan\n{describe_plan(plan)}")

    srcs, twins = [], []

    def shutdown(*_):
        for t in twins:
            t.neutralize()
            t.close()
        for s in srcs:
            try:
                s.ungrab()
            except OSError:
                pass
        log("openbor-pads: stopped")
        sys.exit(0)

    # Grab first, create second, and unwind the grabs if creation fails —
    # otherwise a half-built merger leaves the real pads muted with no twin to
    # replace them (the sinden-smoother ordering).
    for dev, cls in plan:
        try:
            s = InputDevice(dev.path)
            s.grab()
            srcs.append(s)
        except OSError as exc:
            log(f"openbor-pads: cannot grab {dev.path}: {exc}")
            for s in srcs:
                try:
                    s.ungrab()
                except OSError:
                    pass
            return 1
    try:
        for i, ((dev, cls), s) in enumerate(zip(plan, srcs)):
            twins.append(Twin(i, s, cls))
    except Exception as exc:
        # Deliberately broad: evdev raises UInputError (a bare Exception, NOT an
        # OSError) when /dev/uinput is unavailable, so `except OSError` here
        # would sail straight past the exact failure this unwind exists for —
        # and leave the user's pads grabbed with no twins to replace them.
        log(f"openbor-pads: cannot create twin: {exc!r}")
        for t in twins:
            t.close()
        for s in srcs:
            try:
                s.ungrab()
            except OSError:
                pass
        return 1

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    by_fd = {t.src.fd: t for t in twins}
    print("READY", flush=True)
    log(f"openbor-pads: READY ({len(twins)} twin(s))")

    while True:
        try:
            r, _, _ = select.select(list(by_fd), [], [], 1.0)
        except (OSError, InterruptedError):
            continue
        for fd in r:
            t = by_fd.get(fd)
            if t is None:
                continue
            try:
                for ev in t.src.read():
                    t.feed(ev)
            except OSError:
                # The pad vanished mid-game. Keep its twin alive and neutral:
                # destroying it would renumber every other player's port.
                log(f"openbor-pads: P{t.slot + 1} disconnected — twin held neutral")
                t.neutralize()
                by_fd.pop(fd, None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
