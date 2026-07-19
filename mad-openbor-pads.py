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
    --backend NAME                 # read [backends.NAME] and name the twins/log/
                                   # lock after it (default openbor; mugen.sh
                                   # passes 'mugen' to reuse this for Ikemen GO)
"""
from __future__ import annotations

import ctypes
import fcntl
import math
import os
import re
import select
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evdev import AbsInfo, InputDevice, UInput
from evdev import ecodes as e

from lib import mad_paths, sdl_filter
from lib.devices import enumerate_devices, joypads, usb_iface_num, vidpid
from lib.openbor_maps import (CLASS_OF_VIDPID, EVDEV_ABS_ROLE, EVDEV_BTN,
                              GEOM_XINPUT, HAPPY_HAT)
from lib.policy import load_merged
from lib.routing import is_xarcade, xarcade_port

VENDOR, PRODUCT, VERSION = 0x4D41, 0x0002, 0x0001
# Player N's twin gets its OWN product id (P1=0x0002 .. P4=0x0005). This is what
# fixes the seat order, and it is not cosmetic — see product_for().
PRODUCT_BASE = PRODUCT
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

# Stick -> d-pad, RADIAL gate (opt-in per backend via [backends.NAME].stick_gate =
# "radial"; default stays the per-axis box gate above). The box gate makes a round
# stick's DIAGONALS need ~41% more push than cardinals (dead until magnitude 0.566 vs
# 0.40), so quarter-circle / DP motions silently drop the diagonal. The radial gate
# treats the stick as a VECTOR: one engage radius (with hysteresis) then snap the ANGLE
# to 8-way, so cardinals and diagonals engage at the SAME push. (Ref: Hypersect
# "Interpreting Analog Sticks".) MUGEN (fighters) opts in; OpenBOR keeps the box gate
# (its 42 tests assert it and it is the daily driver).
RADIAL_ON, RADIAL_OFF = 0.35, 0.25    # engage / release radius (magnitude hysteresis)
SECTOR_MARGIN = 8.0                    # degrees of angular hysteresis at sector edges
# sector 0=Right 1=DownRight 2=Down 3=DownLeft 4=Left 5=UpLeft 6=Up 7=UpRight ->
# (hatx, haty), with +x = right and +y = down (matching _frac and the twin hat).
_SECTOR_XY = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def _radial_sector(fx: float, fy: float, prev: int,
                   on: float = RADIAL_ON, off: float = RADIAL_OFF) -> int:
    """Stick vector (fx, fy) + previous sector -> new 8-way sector (-1 = neutral).

    `on`/`off` are the engage/release radius (tunable per backend via stick_deadzone).
    Radial engage/release (so cardinals and diagonals engage at the same push, unlike
    the per-axis box gate) plus angular hysteresis at the sector edges (so a stick held
    near a boundary does not chatter between two directions). Pure -- no I/O -- so it is
    unit-tested directly; Twin._radial_digitize just wires the result onto self.stick."""
    mag = math.hypot(fx, fy)
    if prev < 0:
        if mag < on:
            return -1
    elif mag < off:
        return -1
    ang = math.degrees(math.atan2(fy, fx)) % 360.0
    sector = int((ang + 22.5) // 45) % 8
    if prev >= 0 and sector != prev:                  # hold prev unless clearly moved
        off = abs((ang - prev * 45.0 + 180.0) % 360.0 - 180.0)
        if off <= 22.5 + SECTOR_MARGIN:
            sector = prev
    return sector

# ── backend identity ─────────────────────────────────────────────────────────
# This merger is reused by more than OpenBOR: mugen.sh passes --backend mugen to
# drive the SAME pipeline for Ikemen GO (native SDL2). The virtual pad's NAME is
# now shared and backend-INDEPENDENT ("MAD Pad P{n}") -- the seats are pinned by
# the per-player product id, not the name's crc (see product_for), so the name
# carries no ordering meaning and does not need to say which backend it is. Only
# three things vary per backend: the log path, the lock path, and which
# [backends.NAME] policy table is read. Everything else -- build_plan, the twin
# shaping, the re-attach/pump, the product ids and the whitelist -- is identical.
# The default is "openbor", so an invocation with no --backend keeps OpenBOR's
# behaviour (its tests rely on that).
BACKEND = "openbor"
TAG = "openbor-pads"
LOG = mad_paths.storage(BACKEND, "logs") / "pads.log"
LOCK = mad_paths.storage("controller-router") / f"{BACKEND}-pads.lock"


def _configure(backend: str) -> None:
    """Point the per-backend log/lock/policy at `backend` (default 'openbor'). The
    twin NAME is backend-independent ("MAD Pad P{n}"), so nothing name-related varies."""
    global BACKEND, TAG, LOG, LOCK
    BACKEND = backend
    TAG = f"{backend}-pads"
    LOG = mad_paths.storage(backend, "logs") / "pads.log"
    LOCK = mad_paths.storage("controller-router") / f"{backend}-pads.lock"


def log(msg: str) -> None:
    # Timestamped: the log spans every launch, so without one there is no way to
    # tell a warning from THIS run apart from the same warning an hour ago. That
    # cost real time during the 2026-07-16 gate. Multi-line messages get the
    # stamp on the first line only, so the census stays readable.
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass
    print(msg, file=sys.stderr, flush=True)


# ── the pad plan ─────────────────────────────────────────────────────────────

def class_of(dev) -> str | None:
    return CLASS_OF_VIDPID.get(vidpid(dev))


def vidpid_str(d) -> str:
    """vid:pid of a raw evdev InputDevice (which exposes .info, not .vid)."""
    return f"{d.info.vendor:04x}:{d.info.product:04x}"


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


def product_for(slot: int) -> int:
    """The uinput product id for player `slot` (0-based): P1=0x0002 .. P4=0x0005.

    Giving each twin its own product id is what pins the OpenBOR player seats.

    Wine registers each pad under a key built from its SDL GUID:

        ##?#HID#VID_4D41&PID_000X&IG_00#1&<GUID>.<n>&0&<m>&1

    and enumerates those keys ALPHABETICALLY -- the string order IS the port
    order. The GUID is bus + crc16(NAME) + vid + pid + version, so with one
    shared pid the first bytes that differed between our twins were the name's
    CRC, i.e. an arbitrary hash of "MAD Pad P1".."P4" -- which could sort P2 ahead
    of P1 and swap the X-Arcade halves. (Verified against the live registry
    2026-07-16 with the then-name; the failure mode is a pure function of the name
    hash, so it recurs for ANY shared-pid naming.)

    A pid per player fixes it because the pid sits EARLIER in that key than the
    GUID, so it decides the comparison before the name's hash can: 0002 < 0003
    < 0004 < 0005 == P1 < P2 < P3 < P4. It is also why nothing this process does
    at runtime ever mattered — the sort key is a pure function of vid, pid and
    name, so reversing the creation order moved every node and left the seats
    exactly where they were.

    Same rule explains the Steam Deck phantom: VID_28DE sorts before VID_4D41,
    so Steam's virtual pad took port 0 and shifted everyone up a seat until
    openbor.sh blocklisted it."""
    return PRODUCT_BASE + slot


def sdl_whitelist() -> str:
    """Every twin pid, for SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT."""
    return ",".join(f"0x{VENDOR:04x}/0x{product_for(i):04x}"
                    for i in range(MAX_PADS))


def _listed(d, pad_classes, xport: str = "") -> bool:
    """Did the user list this pad's FAMILY on the Controllers page?

    The X-Arcade answers to either spelling: the base policy lists it by vid:pid
    (045e:02a1), MAD's own picker writes the "x-arcade" token, and sdl_filter
    already rules that the token simply IS that vid:pid (_to_vidpid). We resolve
    the same way rather than inventing a second, stricter meaning.

    Deliberately NOT gated on is_xarcade(d, xport): this asks which FAMILIES may
    play, not which device is the cabinet. Gating it cost the whole cabinet the
    moment the port identify went stale -- and re-cabling the stick is exactly
    what routing.is_xarcade's own docstring warns makes it stale. Both halves
    then dropped out of the plan, no merger ran, WL came back empty, and
    openbor.sh read that as HANDHELD and wrote the canonical map on a docked
    launch. Found by the 2026-07-17 review (test_a_stale_xarcade_identify_...).

    Consequence, on purpose: listing "x-arcade" also admits a genuine Xbox 360
    pad, because they are the same vid:pid and nothing but the port tells them
    apart. That is the pre-batch behaviour and what the picker's own labels
    promise; seat ORDER still uses the identify (build_plan), which is where it
    actually belongs."""
    vp = vidpid(d)
    return any(sdl_filter._to_vidpid(c) == vp for c in pad_classes)


def build_plan(devs, pad_classes, xport: str = "") -> list[tuple[object, str]]:
    """Real pads -> the ordered list whose index IS the OpenBOR player slot.

    Order is deterministic and ours (that is the whole point):
      1. the X-Arcade's halves by USB interface — :1.0 is P1, :1.1 is P2. Wine
         used to decide this and got it wrong at random; usb_iface_num is
         replug-stable, so the cabinet's own labelling now always wins.
      2. every other configured family, in `pad_classes` priority order, then
         by enumeration order within a family.
    A pad must be BOTH listed in `pad_classes` and translatable to play. Listing
    is the user's choice, made on MAD's "Player pad families" row, whose help
    says "Pads not listed are hidden from this emulator" — so unchecking one has
    to actually keep it out, or that row is lying. (It was: this used to filter
    on translatability alone, and an unchecked pad still took a seat.)
    Capped at MAX_PADS."""
    pads = [d for d in joypads(devs)
            if class_of(d) and _listed(d, pad_classes, xport)]
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

    def __init__(self, slot: int, src: InputDevice, cls: str, gate: str = "box",
                 on: float = RADIAL_ON):
        self.slot = slot
        self.gate = gate       # "box" (per-axis, default) or "radial" (vector 8-way)
        self._on = on          # radial engage radius; release trails it (hysteresis)
        self._off = max(0.10, on - 0.10)
        self.ui = UInput(_caps(), name=f"MAD Pad P{slot + 1}",
                         vendor=VENDOR, product=product_for(slot),
                         version=VERSION, bustype=e.BUS_USB)
        self.dpad = [0, 0]     # from the real d-pad (hat or HAPPY buttons)
        self.stick = [0, 0]    # from the digitized left stick
        self.hat = [0, 0]      # what the twin currently reports
        self._sector = -1      # radial gate: current 8-way sector (-1 = neutral)
        self._fx = self._fy = 0.0   # radial gate: latest stick fracs
        self._rng = {}
        self.attach(src, cls)

    def attach(self, src: InputDevice, cls: str) -> None:
        """Point this twin at a (new) real pad.

        EVERYTHING SOURCE-DERIVED MUST MOVE TOGETHER — that is the whole reason
        this is one method and not three assignments. A pad brings three things:
        its fd (src), its button table (cls) and its AXIS RANGES (_rng). Swapping
        only the first two is what broke Miquel's DualSense on 2026-07-17: the
        game launched on the X-Arcade, he unplugged it and plugged the DS in, the
        DS took P1 -- and P1's twin was still scaling with the X-Arcade's ranges.
        pads.log:
            05:37:31 plan  P1: Xbox 360 Wireless Receiver ... iface=:1.0
            05:38:16 P1 re-attached to DualSense Wireless Controller (different pad)
        The families genuinely disagree: this DualSense reports ABS_X 0..255
        (measured on the rig), an X-Arcade stick is -32768..32767. Read a 0..255
        stick through a +-32768 scale and the pad's ENTIRE travel collapses into a
        sliver at the MIDDLE of the range -- full-left reads 0.004 instead of -1.0
        -- so _frac() says "centred" however far you push, _digitize() never
        reaches ENGAGE (0.40), and the stick does NOTHING. Dead, not pegged: I
        first wrote "pegged hard-over" here and the test proved otherwise, which
        is the only reason this comment is right. Both _scale (analog out) and
        _frac (the stick -> hat digitization) read _rng, so a stale one silently
        kills movement AND the triggers.

        The derived state goes too: dpad/stick/hat describe where the OLD pad was
        holding, which is meaningless now (neutralize() already zeroes them when a
        source is lost; this is the belt to that braces, and covers a swap with no
        loss in between)."""
        self.src, self.cls = src, cls
        self.dpad, self.stick, self.hat = [0, 0], [0, 0], [0, 0]
        self._sector, self._fx, self._fy = -1, 0.0, 0.0
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

    def _radial_digitize(self) -> None:
        """Radial gate: the stick as a VECTOR (both axes at once), not two independent
        thresholds. Snaps to an 8-way sector past the engage radius, so a diagonal
        engages at the same push as a cardinal and the hat carries it as two dpad
        presses -- what quarter-circle / charge motions need. See _radial_sector."""
        self._sector = _radial_sector(self._fx, self._fy, self._sector,
                                      self._on, self._off)
        self.stick = list(_SECTOR_XY[self._sector]) if self._sector >= 0 else [0, 0]

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
                frac = self._frac(ev.code, ev.value)
                if self.gate == "radial":       # vector gate needs BOTH axes current
                    if role == "lx":
                        self._fx = frac
                    else:
                        self._fy = frac
                    self._radial_digitize()
                else:
                    self._digitize(0 if role == "lx" else 1, frac)
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
            self._sector, self._fx, self._fy = -1, 0.0, 0.0
        except Exception:
            pass

    def close(self) -> None:
        # Deliberately broad, and it is not defensive noise: evdev raises
        # ValueError, NOT OSError, once the fd is gone ("file descriptor cannot
        # be a negative integer (-1)"). An escape from here aborts shutdown()
        # before it ungrabs the real pads, leaving every pad in the rig muted
        # with no working controller left to kill us. Seen for real in
        # DD_FINAL.log on 2026-07-16.
        try:
            self.ui.close()
        except Exception:
            pass


# ── main ─────────────────────────────────────────────────────────────────────

def _policy_now():
    """(pad_classes, xarcade_port) from policy — the two inputs the plan and the
    re-attach scan BOTH need, read once so they cannot disagree."""
    pol = load_merged()
    be = pol.get("backends", {}).get(BACKEND, {})
    return be.get("pad_classes", []), xarcade_port(pol)


def _gate_now() -> str:
    """The stick->hat gate mode for this backend: "radial" (vector 8-way, no dead
    diagonals) or "box" (per-axis, the default). Set per-rig via
    [backends.NAME].stick_gate in policy; only MUGEN opts into "radial" today."""
    be = load_merged().get("backends", {}).get(BACKEND, {})
    return "radial" if be.get("stick_gate") == "radial" else "box"


def _deadzone_now() -> float:
    """Radial engage radius (0..1) for this backend, from [backends.NAME].stick_deadzone
    -- a PERCENT int (35 -> 0.35). Defaults to RADIAL_ON; clamped to a sane band. Only
    the radial gate uses it (the box gate keeps its own ENGAGE/RELEASE)."""
    pct = load_merged().get("backends", {}).get(BACKEND, {}).get("stick_deadzone")
    if not isinstance(pct, (int, float)):
        return RADIAL_ON
    return max(0.15, min(0.55, pct / 100.0))


def _plan_now():
    pad_classes, xport = _policy_now()
    return build_plan(enumerate_devices(), pad_classes, xport)


RETRY_S = 2.0        # how often to look for a pad to fill a vacant slot


def slot_identity(dev, xport: str = "") -> tuple:
    """What makes a pad "the pad that was in this slot".

    vid:pid plus, for the X-Arcade, its USB interface — because that is what
    orders its two halves in build_plan, and usb_iface_num is REPLUG-STABLE, so a
    cabinet that goes and comes back lands on P1/P2 the way its own labelling says.

    BLUETOOTH IS WHY THIS IS SO SHORT. A pad that dies on USB may come back on BT,
    and BT changes almost everything about it EXCEPT vid:pid:
      * the NAME differs ("Wireless Controller" on BT vs "Sony Interactive
        Entertainment Wireless Controller" on USB) — so identity must not use it;
      * the NODE jumps (a re-paired DualSense goes event11 -> event262; both are in
        pads.log) — so identity must not use that either, and `_node_num` exists
        precisely because that reshuffled seats once;
      * usb_iface_num is None off USB — harmless, it is only consulted for the
        X-Arcade, which is a wired cabinet.
    vid:pid is the one stable thing: a DS4 over BT logs 054c:09cc, same as USB, and
    nothing in the merger, devices.py or the policy keys on bus type at all."""
    return (vidpid(dev), usb_iface_num(dev.path) if is_xarcade(dev, xport) else None)


def make_reattach(pad_classes, xport, want, _open=InputDevice,
                  _scan=None):
    """The callable pump uses to refill vacant slots: reattach(vacant, busy).

    `want[slot]` is the identity the slot STARTED with, so the same cabinet or the
    same pad goes home. A slot is NOT reserved for it, though — the ask was
    explicitly "what if i connect ANOTHER charged pad?" — so any listed,
    translatable, unused pad can take a vacant slot. The original identity is a
    PREFERENCE, not a gate.

    TWO PASSES, and the order is the point: every vacant slot gets its EXACT pad
    first, and only then do leftovers fill what remains. One pass would let a
    replugged X-Arcade drop :1.1 into P1 just because P1 was scanned first —
    silently swapping the halves the whole merger exists to pin.

    Only ever fills VACANT slots. Re-running build_plan wholesale would reorder
    LIVE players (lose the cabinet and the surviving DualSense slides from P3 to
    P1), renumbering the engine's ports mid-game."""
    scan = _scan or (lambda: joypads(enumerate_devices()))

    def _grab(t, d):
        s = _open(d.path)
        s.grab()                        # the game must never see the real pad
        # attach(), never bare assignment: the fd, the button table AND the axis
        # ranges all belong to the pad and must move together. See Twin.attach --
        # swapping only src+cls left a DualSense being scaled with the X-Arcade's
        # stick range, which killed the stick outright.
        t.attach(s, class_of(d))
        return s.fd

    def reattach(vacant, busy):
        free = [d for d in scan()
                if class_of(d) and _listed(d, pad_classes, xport)
                and d.path not in busy]
        if not free:
            return []
        out, left = [], list(vacant)
        for exact in (True, False):
            for t in list(left):
                for d in list(free):
                    if exact and slot_identity(d, xport) != want.get(t.slot):
                        continue
                    try:
                        fd = _grab(t, d)
                    except Exception as exc:
                        log(f"{TAG}: cannot take {d.path} for "
                            f"P{t.slot + 1} ({exc!r})")
                        free.remove(d)
                        continue
                    log(f"{TAG}: P{t.slot + 1} re-attached to "
                        f"{d.name} [{vidpid(d)}]{'' if exact else ' (different pad)'}")
                    out.append((fd, t))
                    free.remove(d)
                    left.remove(t)
                    break
                if not free:
                    return out
        return out
    return reattach


def pump(by_fd, twins, reattach, _select=select.select) -> None:
    """Forward every source's events to its twin, and WAIT for pads to come back.

    LOSING A PAD IS NEVER FATAL — not to the merger, and not to the game. The other
    players keep playing, and the dead player's twin must OUTLIVE its source:
    destroying a twin renumbers the engine's ports under the running game. Its slot
    simply goes VACANT and `reattach` refills it when a pad turns up.

    NOT KILLING THE GAME IS THE POINT (Miquel, 2026-07-17: "if a ds loses connection
    cause the battery runs out... the game should not get killed. what if i reconnect
    the pad or if i connect another charged pad?"). A dead battery must cost you a
    pause, not your progress. So there is no timeout and no give-up: plug the pad
    back in, or plug a DIFFERENT charged one in, and play resumes.

    This replaces two wrong answers in a row. The original
    `while True: time.sleep(1.0)` ("idling to hold the twins") idled forever, and
    nothing re-grabbed — `by_fd` only ever shrank — so a replug could not recover
    and the game was permanently input-dead, including OpenBOR's own
    Options -> Quit, its ONLY exit. I then made it EXIT so openbor.sh would kill
    the game: that cured the hang by throwing the session away, which is worse. The
    idle was never the bug; having nothing to wake up FOR was.

    ★ BlockingIOError IS NOT A DISCONNECT and must never pop a source. select() says
    readable, but `read()` still raises EAGAIN (verified on-device: errno 11) on a
    spurious wakeup, and BlockingIOError is an OSError subclass, so the broad handler
    below would swallow it and drop a LIVE pad — costing that player their controls
    until the poll happened to re-grab them. The broad catch stays for everything
    else ON PURPOSE: `except OSError` was wrong — once the fd is gone evdev raises
    ValueError ("file descriptor cannot be a negative integer (-1)"), so the handler
    crashed on its own trigger event and killed the merger mid-session (on-device
    2026-07-16, DD_FINAL log).

    `reattach(vacant)` -> [(fd, twin)] does the device work (see _reattacher); pump
    only decides WHEN to ask. With every slot vacant `by_fd` is empty and select()
    is just the poll timer, which is exactly the idle we want."""
    while True:
        vacant = [t for t in twins if t not in by_fd.values()]
        try:
            r, _, _ = _select(list(by_fd), [], [], RETRY_S if vacant else 1.0)
        except (OSError, InterruptedError):
            continue
        if vacant:
            busy = {t.src.path for t in by_fd.values()}
            try:
                for fd, t in reattach(vacant, busy):
                    by_fd[fd] = t
            except Exception as exc:        # a bad rescan must never stop the pump
                log(f"{TAG}: re-attach scan failed ({exc!r})")
        for fd in list(r):
            t = by_fd.get(fd)
            if t is None:
                continue
            try:
                for ev in t.src.read():
                    t.feed(ev)
            except BlockingIOError:
                continue                     # readable but nothing pending: alive
            except Exception as exc:
                log(f"{TAG}: P{t.slot + 1} source lost ({exc!r}) — twin "
                    f"held neutral, waiting for a pad to take the slot")
                by_fd.pop(fd, None)          # stop selecting the dead fd FIRST
                try:
                    t.neutralize()           # release anything it was holding
                except Exception:
                    pass


def _parse_backend(argv: list[str]) -> str:
    """--backend NAME or --backend=NAME; default 'openbor'."""
    for i, a in enumerate(argv):
        if a == "--backend":
            return argv[i + 1] if i + 1 < len(argv) else "openbor"
        if a.startswith("--backend="):
            return a.split("=", 1)[1]
    return "openbor"


def main(argv: list[str]) -> int:
    _configure(_parse_backend(argv))
    plan = _plan_now()
    if "--probe" in argv:
        print(describe_plan(plan))
        return 0 if plan else 3
    if not plan:
        log(f"{TAG}: no player pads — nothing to merge")
        return 3

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"{TAG}: another instance holds the lock — exiting")
        return 4
    # Never outlive the launcher. This is what covers the deaths openbor.sh's
    # trap cannot (SIGKILL, SIGHUP): without it an orphaned merger keeps the
    # EVIOCGRAB on every pad, muting them rig-wide — ES-DE included — with no
    # working controller left to kill it.
    # REQUIRES openbor.sh to `exec` us, so our parent really is that script and
    # not a bash subshell wrapping it (verified 2026-07-16: without exec the
    # parent is a subshell that cannot predecease us, so this never fires).
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG

    log(f"{TAG}: plan\n{describe_plan(plan)}")

    srcs, twins = [], []

    def shutdown(*_):
        # Giving the pads BACK is the one thing that must always happen — a
        # missed ungrab mutes the whole rig, ES-DE included. So nothing above it
        # is allowed to raise past it, however broken a twin turns out to be.
        try:
            for t in twins:
                t.neutralize()
                t.close()
        except Exception as exc:
            log(f"{TAG}: twin teardown failed ({exc!r}) — ungrabbing anyway")
        for s in srcs:
            try:
                s.ungrab()
            except Exception:
                pass
        log(f"{TAG}: stopped")
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
            log(f"{TAG}: cannot grab {dev.path}: {exc}")
            for s in srcs:
                try:
                    s.ungrab()
                except OSError:
                    pass
            return 1
    # Player order. The seats are pinned by the per-player product id (see
    # product_for), NOT by the order we create them in — measured: reversing
    # this loop moved every node and left the seats exactly where they were.
    # Creating in player order simply keeps node order agreeing with pid order
    # instead of contradicting it.
    gate, on = _gate_now(), _deadzone_now()
    try:
        for i, ((dev, cls), s) in enumerate(zip(plan, srcs)):
            twins.append(Twin(i, s, cls, gate, on))
    except Exception as exc:
        # Deliberately broad: evdev raises UInputError (a bare Exception, NOT an
        # OSError) when /dev/uinput is unavailable, so `except OSError` here
        # would sail straight past the exact failure this unwind exists for —
        # and leave the user's pads grabbed with no twins to replace them.
        log(f"{TAG}: cannot create twin: {exc!r}")
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
    log(f"{TAG}: READY ({len(twins)} twin(s))")
    # Census: EVERY 4d41 device the game could see, in PID order — which is the
    # order the engine seats them (see product_for), so this line predicts the
    # seats. Node order is listed too but does not decide anything: proven by
    # reversing it and watching the seats not move.
    # Anything here that is not one of our twins is stealing a player seat.
    # (Miquel's 2026-07-16 gate: 2 twins, but the engine reported 3 joysticks
    # and every pad was shifted one seat up. This is the line that will name the
    # third device instead of us guessing at it.)
    try:
        from evdev import list_devices
        found = []
        for p in list_devices():
            try:
                d = InputDevice(p)
                if d.info.vendor == VENDOR:
                    mine = any(t.ui.device.path == p for t in twins)
                    found.append((d.info.product, p, vidpid_str(d), d.name, mine))
                d.close()
            except OSError:
                pass
        found.sort(key=lambda r: (r[0], _node_num(r[1])))     # pid, then node
        seats, census = 0, []
        for pid, p, vp, nm, mine in found:
            # Only a whitelisted pid reaches the game; anything else (the Wii Nav
            # bridge at 4d41:0001, say) is ours but invisible to it, and must not
            # be reported as holding a seat.
            if PRODUCT_BASE <= pid < PRODUCT_BASE + MAX_PADS:
                census.append(f"port {seats}: {vp} {nm!r} {p}"
                              f"{'' if mine else '  <-- NOT OURS, STEALING A SEAT'}")
                seats += 1
            else:
                census.append(f"  (not whitelisted, game cannot see it): "
                              f"{vp} {nm!r} {p}")
        log(f"{TAG}: 4d41 census (pid order == engine port order):\n  "
            + "\n  ".join(census or ["(none?!)"]))
    except Exception as exc:
        log(f"{TAG}: census failed: {exc!r}")

    # The identity each slot STARTED with, so a replugged cabinet/pad goes home.
    pad_classes, xport = _policy_now()
    want = {i: slot_identity(dev, xport) for i, (dev, _cls) in enumerate(plan)}
    pump(by_fd, twins, make_reattach(pad_classes, xport, want))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
