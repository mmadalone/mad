#!/usr/bin/env python3
"""MAD Wii navigation bridge — Wii Remotes (+ accessories) navigate ES-DE/MAD.

Reads every DolphinBar mode-4 slot via lib.wii_slot_reader.WiiSlotReader
(Dolphin's WiimoteReal init, verbatim) and feeds ONE virtual uinput gamepad
("MAD Wii Nav", vid 0x4d41 pid 0x0001) that ES-DE picks up through SDL like
any controller. Mapping (user spec 2026-06-12):

  wiimote   d-pad → d-pad (hat) · A/B → A/B · 1/2 → LT/RT · Home → start
            +/− → start/back WITH an accessory; on a BARE remote (no C/Z to
            switch sections) +/− → R1/L1 bumpers instead (5dfa3dc)
  nunchuk   C/Z → L1/R1 · stick → d-pad
  classic   buttons 1:1 (a/b/x/y/L1/R1/ZL→LT/ZR→RT/start/back/d-pad) ·
            both sticks → d-pad

Any live slot navigates; directional sources OR together. The bridge is
spawned BY the ES-DE fork (dies with it via PR_SET_PDEATHSIG) and obeys a
line protocol on stdin:

  pause\n   stop reading the slots, close them, release every virtual control
            (written before EVERY game launch — NEVER write to the slots while
            a game owns the remotes, deck-docs/wiimote.md)
  resume\n  re-open the slots and resume

While the MAD wii tester streams a slot, tester_cmds writes its NODE PATH to
~/Emulation/storage/controller-router/wii-tester-slot — the bridge releases
that node (the tester owns it; a second remote keeps navigating) and resumes
when the file disappears. The claim is checked EVERY poll tick (a slot-number
scheme was rejected: the tester ranks slots by HID_PHYS ordinal while a naive
node sort is lexicographic — hidraw10 < hidraw8). A stale file (daemon crash)
is ignored when the mad-backend lock-holder pid is dead.

The virtual pad is INVISIBLE to MAD/router code: lib/devices.py excludes the
device by name at the enumeration source, so it can never be routed into a
game, listed as a real pad, or captured as a player pin.

Single instance via flock; exits 0 on stdin EOF.
"""
from __future__ import annotations

import fcntl
import os
import select
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import devices as dv                      # noqa: E402
from lib import mad_paths                           # noqa: E402
from lib.wii_slot_reader import WiiSlotReader      # noqa: E402

try:
    from evdev import AbsInfo, UInput
    from evdev import ecodes as e
except Exception:
    print("FATAL: python-evdev missing (run deck-post-update.sh)", file=sys.stderr)
    sys.exit(3)

RUN_DIR = mad_paths.storage("controller-router")
LOCK_FILE = RUN_DIR / "wii-nav-bridge.lock"
BACKEND_LOCK = RUN_DIR / "mad-backend.lock"
TESTER_SLOT_FILE = RUN_DIR / "wii-tester-slot"

DEVICE_NAME = "MAD Wii Nav"
VENDOR, PRODUCT, VERSION = 0x4D41, 0x0001, 1      # "MA"D — unique, unrouted.

POLL_HZ = 30.0
RESCAN_SEC = 4.0          # Slot-node + tester-file re-check cadence.
DOCK_CHECK_SEC = 4.0      # Physical dock-state re-check cadence (handheld -> no nav pad).
# Min suppression after a slot is (re)acquired, on TOP of wait-for-neutral: the
# wii tester exits on a 6 s +-hold, and a single neutral frame (~68 ms) used to
# re-arm before the finger settled, leaking + (R1) into nav. Stay disarmed at
# least this long AND until neutral.
REARM_COOLDOWN_SEC = 1.0

# ── output state model ──────────────────────────────────────────────────────
BUTTONS = ("a", "b", "x", "y", "l1", "r1", "start", "back")
BTN_CODE = {"a": e.BTN_SOUTH, "b": e.BTN_EAST, "x": e.BTN_NORTH,
            "y": e.BTN_WEST, "l1": e.BTN_TL, "r1": e.BTN_TR,
            "start": e.BTN_START, "back": e.BTN_SELECT}
DIR8 = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
        "ul": (-1, -1), "ur": (1, -1), "dl": (-1, 1), "dr": (1, 1)}

# ── debug logging ────────────────────────────────────────────────────────────
# On by default (set MAD_WII_DEBUG=0 to silence). The bridge's stderr is
# captured to ~/Emulation/storage/controller-router/wii-nav-bridge.log by the
# ES-DE spawn, so dbg() lines land there. Everything below is CHANGES-ONLY, so
# at 30 Hz idle it prints nothing — only on rescan/slot-state/emit transitions.
DEBUG = os.environ.get("MAD_WII_DEBUG", "1") != "0"


def dbg(msg: str) -> None:
    if not DEBUG:
        return
    t = time.time()
    ts = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
    print(f"[wii-nav {ts}] {msg}", file=sys.stderr, flush=True)


def _state_str(state: dict) -> str:
    b = ",".join(sorted(state["buttons"])) or "-"
    return f"buttons=[{b}] hat={state['hat']} lt={int(state['lt'])} rt={int(state['rt'])}"


def _is_neutral(state: dict) -> bool:
    return (not state["buttons"] and state["hat"] == (0, 0)
            and not state["lt"] and not state["rt"])


def blank_state() -> dict:
    return {"buttons": frozenset(), "hat": (0, 0), "lt": False, "rt": False}


def decode_slot(snap: dict) -> dict:
    """One slot's WiiSlotReader snapshot → desired output controls."""
    buttons: set = set()
    dx = dy = 0
    lt = rt = False
    core = snap.get("core", frozenset())
    kind = snap.get("kind", "none")
    ext = snap.get("ext", frozenset())

    if "a" in core:
        buttons.add("a")
    if "b" in core:
        buttons.add("b")
    # Bare remote (no accessory): +/− become the bumpers (R1/L1) — without
    # C/Z there is no way to switch MAD sections. With an accessory attached
    # they stay start/back (the accessory provides the bumpers).
    bare = kind in ("none", "")
    if "plus" in core:
        buttons.add("r1" if bare else "start")
    if "minus" in core:
        buttons.add("l1" if bare else "back")
    # Home → start (open the ES-DE menu), but ONLY on a bare remote — there + is
    # the R1 bumper so Home restores menu access. With an accessory attached + is
    # already start, so Home stays unmapped (no double-start). START, not the SDL
    # guide button, to avoid Steam's Game-Mode guide grab.
    if "home" in core and bare:
        buttons.add("start")
    lt = "one" in core
    rt = "two" in core
    if "dpadup" in core:
        dy -= 1
    if "dpaddown" in core:
        dy += 1
    if "dpadleft" in core:
        dx -= 1
    if "dpadright" in core:
        dx += 1

    if kind == "nunchuk":
        if "c" in ext:
            buttons.add("l1")
        if "z" in ext:
            buttons.add("r1")
    elif kind == "classic":
        for stem, out in (("a", "a"), ("b", "b"), ("x", "x"), ("y", "y"),
                          ("l", "l1"), ("r", "r1"),
                          ("plus", "start"), ("minus", "back")):
            if stem in ext:
                buttons.add(out)
        lt = lt or "zl" in ext
        rt = rt or "zr" in ext
        if "dpadup" in ext:
            dy -= 1
        if "dpaddown" in ext:
            dy += 1
        if "dpadleft" in ext:
            dx -= 1
        if "dpadright" in ext:
            dx += 1
    # Accessory sticks behave like d-pads (user spec).
    for stick in ("lstick", "rstick"):
        token = snap.get(stick, "rest")
        if token in DIR8:
            dx += DIR8[token][0]
            dy += DIR8[token][1]

    return {"buttons": frozenset(buttons),
            "hat": (max(-1, min(1, dx)), max(-1, min(1, dy))),
            "lt": lt, "rt": rt}


def merge(states: list) -> dict:
    buttons: set = set()
    dx = dy = 0
    lt = rt = False
    for s in states:
        buttons |= s["buttons"]
        dx += s["hat"][0]
        dy += s["hat"][1]
        lt = lt or s["lt"]
        rt = rt or s["rt"]
    return {"buttons": frozenset(buttons),
            "hat": (max(-1, min(1, dx)), max(-1, min(1, dy))),
            "lt": lt, "rt": rt}


class NavSlotReader(WiiSlotReader):
    """Snappier keepalive than the tester's: the MAD pages' periodic probes
    (gamepads.list / wii.probe_ext) re-write the slot's report mode and would
    otherwise stall navigation for up to the keepalive interval."""
    KEEPALIVE = 0.5


def _handheld() -> bool:
    """True when the on-the-go feature is enabled AND the Deck is physically handheld -- the SAME
    gate the rest of the on-the-go rail uses (switch_bind._launch_handheld,
    controller-router._handheld_active), honouring [handheld].force / MAD_FORCE_CONTEXT.
    Best-effort: any error -> False, so the bridge keeps its legacy always-on behaviour rather
    than wrongly killing Wii navigation."""
    try:
        from lib import deck_state, policy
        hh = policy.load_merged().get("handheld")
        if not (isinstance(hh, dict) and hh.get("enabled", False)):
            return False
        return deck_state.is_handheld(deck_state.resolve_force(hh))
    except Exception:
        return False


class Bridge:
    def __init__(self):
        caps = {
            e.EV_KEY: [BTN_CODE[b] for b in BUTTONS],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_Z, AbsInfo(0, 0, 255, 0, 0, 0)),    # LT
                (e.ABS_RZ, AbsInfo(0, 0, 255, 0, 0, 0)),   # RT
                (e.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)),
                (e.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0)),
            ],
        }
        self._caps = caps              # kept so _open_ui can (re)create the pad on a dock change
        self.ui = None
        self.readers: dict = {}        # node -> WiiSlotReader
        self.paused = False
        self.last = blank_state()
        self.last_rescan = 0.0
        self.last_claim = None
        self._slot_log: dict = {}      # node -> last-logged (kind, core, ext, sticks)
        # node -> earliest monotonic time it may re-arm. A freshly (re)acquired
        # slot is gated until now >= deadline AND it reports live+neutral, so a
        # still-held button can't leak into nav. Chiefly the wii tester's "hold
        # + for 6 s to exit" gesture: the slot returns to the bridge with + down
        # (and the finger bounces through a brief neutral), which would else
        # fire R1. Clears once settled, so a fresh press works normally.
        self._disarmed: dict = {}
        # HANDHELD (on-the-go): no DolphinBar / Wii Remotes, and the "MAD Wii Nav" pad would
        # occupy a controller slot (it grabbed RetroArch's Port-1 menu control). Start padless +
        # disabled when handheld; run()'s dock check reopens the pad if the Deck is later docked.
        self.last_dock_check = 0.0
        # Start disabled; enable + open the pad only when docked AND the pad actually opens. A
        # failed open leaves us disabled to retry on the next dock check, never crashing the bridge.
        self.disabled = True
        if _handheld():
            dbg("startup handheld -> Wii nav disabled (no MAD Wii Nav pad)")
        elif self._open_ui():
            self.disabled = False
        else:
            dbg("startup: UInput unavailable -> Wii nav disabled (retries on dock check)")

    def _open_ui(self) -> bool:
        """Create the MAD Wii Nav uinput pad if it isn't already up. Returns True if the pad is up
        afterward, False if /dev/uinput was unavailable -- so a transient open failure on a redock
        degrades to 'stay disabled, retry next dock check' instead of crashing the bridge."""
        if self.ui is not None:
            return True
        try:
            self.ui = UInput(self._caps, name=DEVICE_NAME, vendor=VENDOR, product=PRODUCT,
                             version=VERSION, bustype=e.BUS_VIRTUAL)
        except Exception as ex:
            dbg(f"UInput open failed ({ex!r}); Wii nav stays disabled")
            self.ui = None
            return False
        self.last = blank_state()
        dbg(f"device up: {DEVICE_NAME} {VENDOR:04x}:{PRODUCT:04x} "
            f"buttons={list(BUTTONS)}")
        return True

    def _close_ui(self) -> None:
        """Destroy the pad so SDL stops seeing 4d41:0001 (handheld). Idempotent + best-effort."""
        if self.ui is None:
            return
        try:
            self.ui.close()
        except Exception:
            pass
        self.ui = None
        dbg("device down: MAD Wii Nav pad closed")

    def _apply_dock_state(self) -> None:
        """Open/close the nav pad to match the live physical dock state (handheld = padless).
        Called only from run() while NOT paused, so the uinput swap never races a running game."""
        handheld = _handheld()
        if handheld and not self.disabled:
            dbg("undocked -> disabling Wii nav (releasing slots + closing MAD Wii Nav pad)")
            self.drop_all()
            self.apply(blank_state())      # neutralise the pad before it goes (no stuck nav keys)
            self._close_ui()
            self.disabled = True
        elif not handheld and self.disabled:
            if self._open_ui():        # only enable if the pad actually came up (else retry next tick)
                dbg("docked -> Wii nav enabled (MAD Wii Nav pad reopened)")
                self.disabled = False
                self.rescan()

    def _log_slot(self, node: str, snap: dict) -> None:
        """Log a slot's RAW reader snapshot whenever its inputs change — the
        ground truth feeding decode_slot (kind, core/ext button sets, sticks)."""
        if not DEBUG:
            return
        kind = snap.get("kind", "none")
        core = frozenset(snap.get("core", ()))
        ext = frozenset(snap.get("ext", ()))
        sticks = tuple(snap.get(s, "rest") for s in ("lstick", "rstick"))
        key = (snap.get("present"), snap.get("status"), kind, core, ext, sticks)
        if self._slot_log.get(node) == key:
            return
        self._slot_log[node] = key
        tail = node.rsplit("/", 1)[-1]
        dbg(f"slot {tail}: present={snap.get('present')} status={snap.get('status')} "
            f"kind={kind} core=[{','.join(sorted(core)) or '-'}] "
            f"ext=[{','.join(sorted(ext)) or '-'}] sticks={sticks} "
            f"-> decode {_state_str(decode_slot(snap))}")

    # ── slot management ──
    def tester_claim(self):
        """The NODE the MAD wii tester currently owns, or None. A leftover
        file is honored only while the mad-backend lock-holder pid is alive
        (no flock probe — momentarily ACQUIRING the lock could EBUSY a
        backend starting at that instant)."""
        try:
            node = TESTER_SLOT_FILE.read_text().strip()
        except OSError:
            return None
        if not node.startswith("/dev/"):
            return None
        try:
            pid = int(BACKEND_LOCK.read_text().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return None                # No/garbled lock file -> stale claim.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None                # Daemon gone -> stale claim.
        except PermissionError:
            pass                       # Exists but not ours -> alive.
        return node

    def rescan(self):
        self.last_rescan = time.monotonic()
        claimed = self.last_claim
        discovered = list(dv._dolphinbar_slot_nodes())
        want = {node for node in discovered if node != claimed}
        removed = [n for n in self.readers if n not in want]
        added = [n for n in want if n not in self.readers]
        for node in removed:
            self.readers.pop(node).stop(timeout=0.7)
            self._slot_log.pop(node, None)
            self._disarmed.pop(node, None)
        for node in want:
            if node not in self.readers:
                reader = NavSlotReader(node)
                reader.start()
                self.readers[node] = reader
                # Gate until live+neutral AND >= cooldown (held-button leak).
                self._disarmed[node] = time.monotonic() + REARM_COOLDOWN_SEC
        if DEBUG and (added or removed or not self.readers):
            short = lambda ns: [n.rsplit("/", 1)[-1] for n in ns]
            dbg(f"rescan: dolphinbar slots={short(discovered)} "
                f"claimed={claimed.rsplit('/', 1)[-1] if claimed else None} "
                f"added={short(added)} removed={short(removed)} "
                f"active={short(self.readers)}")

    def drop_all(self):
        # 0.7 s > the reader's 0.3 s select tick: no straggler keepalive
        # write may land after we report quiescence (pause = game launch).
        for reader in self.readers.values():
            reader.stop(timeout=0.7)
        self.readers.clear()

    # ── output ──
    def apply(self, state: dict):
        if self.ui is None:            # handheld: no pad to write to
            return
        if state == self.last:
            return
        dbg("EMIT " + _state_str(state))
        for name in BUTTONS:
            now = name in state["buttons"]
            if now != (name in self.last["buttons"]):
                self.ui.write(e.EV_KEY, BTN_CODE[name], 1 if now else 0)
        if state["hat"] != self.last["hat"]:
            self.ui.write(e.EV_ABS, e.ABS_HAT0X, state["hat"][0])
            self.ui.write(e.EV_ABS, e.ABS_HAT0Y, state["hat"][1])
        if state["lt"] != self.last["lt"]:
            self.ui.write(e.EV_ABS, e.ABS_Z, 255 if state["lt"] else 0)
        if state["rt"] != self.last["rt"]:
            self.ui.write(e.EV_ABS, e.ABS_RZ, 255 if state["rt"] else 0)
        self.ui.syn()
        self.last = state

    # ── control protocol ──
    def handle_command(self, line: str):
        line = line.strip().lower()
        if line == "pause" and not self.paused:
            dbg("CMD pause -> releasing slots (game launch)")
            self.paused = True
            self.drop_all()
            self.apply(blank_state())  # No stuck nav keys into the game.
        elif line == "resume" and self.paused:
            dbg("CMD resume -> reopening slots")
            self.paused = False
            if not self.disabled:      # handheld: stay padless, don't reopen slots
                self.rescan()

    def run(self):
        dbg(f"bridge start: pid={os.getpid()} POLL_HZ={POLL_HZ} debug=ON "
            "(set MAD_WII_DEBUG=0 to silence)")
        self.last_claim = self.tester_claim()
        if not self.disabled:          # handheld start: no pad, no slot reading (mirrors resume)
            self.rescan()
        tick = 1.0 / POLL_HZ
        # RAW fd reads with manual line assembly — NEVER sys.stdin.readline()
        # after select(): its userspace buffer swallows coalesced lines
        # ("pause\npause\n…resume\n" from back-to-back launches) that select()
        # on the fd can then never see, wedging the bridge paused.
        carry = b""
        while True:
            ready, _, _ = select.select([0], [], [], tick)
            if ready:
                chunk = os.read(0, 4096)
                if not chunk:
                    break              # ES-DE closed the pipe: exit.
                carry += chunk
                while b"\n" in carry:
                    line, _, carry = carry.partition(b"\n")
                    self.handle_command(line.decode(errors="replace"))
                continue
            if self.paused:
                continue
            # Live dock tracking: the on-the-go rail is per-launch dynamic, so re-check the
            # physical display every DOCK_CHECK_SEC and open/close the nav pad on a dock change.
            # Only here (never while paused = a game owns input) so the uinput swap can't race a game.
            if time.monotonic() - self.last_dock_check >= DOCK_CHECK_SEC:
                self.last_dock_check = time.monotonic()
                self._apply_dock_state()
            if self.disabled:
                continue               # handheld: no nav pad, no slot reading
            # The tester claim must take effect within ONE tick — a 4 s lag
            # would double-write the slot under test and mirror the tested
            # remote's presses into live navigation.
            claim = self.tester_claim()
            if claim != self.last_claim:
                dbg(f"tester claim changed: {self.last_claim} -> {claim}")
                self.last_claim = claim
                self.rescan()
            elif time.monotonic() - self.last_rescan > RESCAN_SEC:
                self.rescan()
            snaps = [(node, r.snapshot()) for node, r in self.readers.items()]
            for node, snap in snaps:
                self._log_slot(node, snap)
            states = []
            for node, snap in snaps:
                st = decode_slot(snap)
                if node in self._disarmed:
                    # Arm only once PAST the cooldown AND the slot is LIVE+neutral.
                    # The cooldown floor rides over the post-exit finger bounce;
                    # the live check skips the transient opening/empty-neutral
                    # window that would otherwise re-arm before the held button
                    # (the 6 s +-hold exit gesture) is actually released.
                    live = snap.get("present") and snap.get("status") == "live"
                    if (time.monotonic() >= self._disarmed[node]
                            and live and _is_neutral(st)):
                        self._disarmed.pop(node, None)   # settled -> arm it
                        dbg(f"slot {node.rsplit('/', 1)[-1]}: armed (settled)")
                    else:
                        continue                       # suppress held-button leak
                states.append(st)
            self.apply(merge(states) if states else blank_state())
        self.drop_all()
        self.apply(blank_state())      # guarded no-op if already padless
        self._close_ui()


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("FATAL: another wii-nav-bridge is running", file=sys.stderr)
        return 4
    os.write(lock_fd, f"{os.getpid()}\n".encode())

    try:                               # Die with ES-DE even if the pipe lingers.
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    except Exception:
        pass
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    Bridge().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
