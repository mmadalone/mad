#!/usr/bin/env python3
"""
Configurable hold-to-quit combo watcher for standalone emulators (evdev pads).

Generalises cemu-quit-watcher.py: instead of a hardcoded +&- (BTN_START+
BTN_SELECT), the combo is a CONFIGURABLE SET of evdev button codes read from
`[quit_combo]` in controller-policy.toml (+ the GUI's controller-policy.local.toml
override, and an optional per-system `[quit_combo.<system>]`). When every button
in the set is held together on the SAME pad for `hold_sec`, the per-launch
`--quit-cmd` runs (quitting the emulator → ES-DE returns).

Co-reads the pads' evdev nodes read-only (never EVIOCGRAB — doesn't steal input
from the emulator/SDL), re-enumerating as pads sleep/reconnect. Real Wii Remotes
are HID, not evdev → they keep wiimote-quit-watcher.py; this covers every SDL
pad (Pro Controller, DualSense, DS4, …). MOUSE devices are co-read too, so a
mouse-button combo (e.g. the X-Arcade red button = BTN_MIDDLE) can be a quit
combo — codes accumulate per device, so such a combo lives on the mouse node.

The GUI's "Detect" feature writes the captured codes to `[quit_combo] buttons`.
Default combo = the Wii U +&- (BTN_SELECT 314 + BTN_START 315), verified live.

Usage:
    quit-combo-watcher.py --quit-cmd "flatpak kill app; pkill -TERM -f app" \
                          [--system wiiu]
Env overrides: QUIT_COMBO_BUTTONS="314,315"  QUIT_COMBO_HOLD=1.0  QUIT_COMBO_DEBUG=1
"""
import argparse
import os
import select
import struct
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY = HERE / "controller-policy.toml"
LOCAL_POLICY = HERE / "controller-policy.local.toml"
sys.path.insert(0, str(HERE))
from lib import devices, mad_paths  # noqa: E402
LOG = str(mad_paths.storage("sinden", "logs", "es-de-hooks.log"))

NAME_HINT = "Nintendo Wii Remote Pro Controller"   # default; any pad name matched too
EV_SIZE = struct.calcsize("llHHi")
EV_KEY = 0x01
RESCAN_SEC = 2.0
DEFAULT_BUTTONS = [314, 315]    # BTN_SELECT + BTN_START  (− + +)
DEFAULT_HOLD = 1.0
# After the quit cmd (SIGTERM), a detached backstop SIGKILLs any straggler this
# many seconds later — some emulators (notably Eden) hang on SIGTERM. Long enough
# that a clean exit finishes first (then the KILL matches nothing = harmless).
KILL_AFTER = float(os.environ.get("QUIT_COMBO_KILL_AFTER", "6"))


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] quit-combo-watcher: {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


def _read_quit_combo(system: str | None) -> tuple[set[int], float]:
    """Merge [quit_combo] from policy + local override + per-system table."""
    cfg: dict = {}
    for p in (POLICY, LOCAL_POLICY):
        if p.is_file():
            try:
                qc = tomllib.load(p.open("rb")).get("quit_combo", {})
                cfg.update({k: v for k, v in qc.items() if not isinstance(v, dict)})
                if system and isinstance(qc.get(system), dict):
                    cfg.update(qc[system])
            except (tomllib.TOMLDecodeError, OSError):
                pass
    buttons = cfg.get("buttons", DEFAULT_BUTTONS)
    # A malformed value (bad TOML type or garbage env var) must never crash the
    # watcher on startup — fall back to the verified default combo/hold instead.
    try:
        hold = float(cfg.get("hold_sec", DEFAULT_HOLD))
    except (TypeError, ValueError):
        log(f"bad hold_sec {cfg.get('hold_sec')!r}; using {DEFAULT_HOLD}")
        hold = DEFAULT_HOLD
    # env overrides win
    env_buttons = os.environ.get("QUIT_COMBO_BUTTONS")
    if env_buttons:
        try:
            buttons = [int(x) for x in env_buttons.split(",") if x.strip()]
        except ValueError:
            log(f"bad QUIT_COMBO_BUTTONS {env_buttons!r}; keeping {buttons}")
    if os.environ.get("QUIT_COMBO_HOLD"):
        try:
            hold = float(os.environ["QUIT_COMBO_HOLD"])
        except ValueError:
            log(f"bad QUIT_COMBO_HOLD {os.environ['QUIT_COMBO_HOLD']!r}; keeping {hold}")
    try:
        combo = set(int(b) for b in buttons)
    except (TypeError, ValueError):
        log(f"bad buttons {buttons!r}; using default {DEFAULT_BUTTONS}")
        combo = set(DEFAULT_BUTTONS)
    return combo, hold


_LAST_NODES: list[str] = []


def _all_input_event_nodes(want_kbd: bool = False) -> list[str]:
    """Every /dev/input/eventN that is a gamepad or a mouse (or a keyboard, when
    want_kbd — i.e. the active quit combo contains a key), via the canonical
    lib.devices classifier. Robust vs. the old hand-rolled capability bitmask,
    which read a FIXED word position (`words[-5]`) and so mis-classified composite
    keyboard+mouse devices (longer key bitmap) — e.g. the X-Arcade encoder. Mouse
    nodes are now included so a mouse-button quit combo (X-Arcade red button =
    BTN_MIDDLE) is watched. Returns the last-good set on a transient enumerate
    failure, so a hiccup never closes every open node."""
    global _LAST_NODES
    try:
        nodes = sorted({d.path for d in devices.enumerate_devices()
                        if d.is_joypad or d.is_mouse or (want_kbd and d.is_keyboard)})
        _LAST_NODES = nodes
        return nodes
    except Exception as exc:
        log(f"device enumerate failed ({exc}); keeping {len(_LAST_NODES)} node(s)")
        return _LAST_NODES


def _quit(quit_cmd: str) -> None:
    """Send the quit command (SIGTERM), and arm a DETACHED backstop that SIGKILLs
    any straggler after KILL_AFTER s — some emulators (notably Eden) hang on
    SIGTERM. Tricky bit: the quit pattern (e.g. 'Eden|Yuzu|…') also matches THIS
    watcher's own cmdline, so the quit cmd's `pkill -TERM` would kill the backstop
    too — hence `setsid` + `trap '' TERM` so it ignores SIGTERM and outlives us.
    The backstop's `pkill -KILL` is conditional by nature (matches nothing if the
    emulator already exited cleanly), so a normal quit is never force-killed."""
    kill_cmd = quit_cmd.replace("-TERM", "-KILL")
    if kill_cmd != quit_cmd:                      # only if there's a SIGTERM to escalate
        backstop = f"trap '' TERM; sleep {KILL_AFTER:g}; {kill_cmd}"
        try:
            subprocess.Popen(["setsid", "bash", "-c", backstop],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log(f"armed SIGKILL backstop (+{KILL_AFTER:g}s)")
        except OSError as exc:
            log(f"backstop spawn failed ({exc}); SIGTERM only")
    # Detach the quit_cmd into its own session AND make that session ignore SIGTERM,
    # exactly like the backstop above. Two distinct hazards, two parts:
    #   • setsid  — main() returns right after this, so the watcher process is gone
    #     before the quit_cmd's pkill fires (no self-kill of THIS watcher).
    #   • trap '' TERM — quit_cmd's own `pkill -TERM -f '<pat>'` ALSO matches the
    #     shell running quit_cmd (its cmdline carries '<pat>'); pkill -f matches by
    #     cmdline, NOT session, so setsid alone does NOT spare it. Without the trap
    #     the shell SIGTERMs itself and never reaches the `sleep N; pkill -KILL`
    #     fast-escalation — so a SIGTERM-ignoring emulator (Eden) would fall through
    #     to the 6 s global backstop instead of dying at the policy's 2 s. (Verified.)
    try:
        subprocess.Popen(["setsid", "bash", "-c", f"trap '' TERM; {quit_cmd}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("quit command sent (detached)")
    except OSError as exc:                        # mirror the backstop's guard above
        log(f"quit dispatch failed ({exc})"
            + ("; SIGKILL backstop will still fire" if kill_cmd != quit_cmd else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quit-cmd", required=True)
    ap.add_argument("--system", default=None)
    args = ap.parse_args()
    debug = os.environ.get("QUIT_COMBO_DEBUG") == "1"

    combo, hold = _read_quit_combo(args.system)
    log(f"start (system={args.system} combo={sorted(combo)} hold={hold}s "
        f"-> {args.quit_cmd!r}; debug={debug})")

    fds, held, since, last = {}, {}, {}, {}
    last_scan = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_scan >= RESCAN_SEC:
                last_scan = now
                # Watch keyboards only when the combo actually contains a key (the
                # capturable _RA_KEYMAP keys are all < BTN_MISC 0x100); otherwise the
                # no-key case is unchanged (no keyboard fds, no per-keystroke wakeups).
                current = set(_all_input_event_nodes(any(c < 0x100 for c in combo)))
                for path in current - set(fds):
                    try:
                        fds[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        held[path] = set()
                        since[path] = None
                        last[path] = None
                        log(f"opened {path} (co-read OK)")
                    except OSError:
                        pass
                for path in set(fds) - current:
                    try:
                        os.close(fds[path])
                    except OSError:
                        pass
                    for d in (fds, held, since, last):
                        d.pop(path, None)
                    log(f"closed {path} (gone)")

            if not fds:
                time.sleep(0.2)
                continue

            ready, _, _ = select.select(list(fds.values()), [], [], 0.2)
            fd2path = {fd: p for p, fd in fds.items()}
            for fd in ready:
                path = fd2path.get(fd)
                if path is None:
                    continue
                try:
                    data = os.read(fd, EV_SIZE * 64)
                except OSError:
                    continue
                cur = held[path]
                for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                    _, _, etype, code, val = struct.unpack("llHHi", data[off:off + EV_SIZE])
                    if etype != EV_KEY or code not in combo:
                        continue
                    if val:
                        cur.add(code)
                    else:
                        cur.discard(code)
                if debug and last.get(path) != frozenset(cur):
                    last[path] = frozenset(cur)
                    log(f"{path}: held={sorted(cur)}")
                if combo <= cur:                     # all combo buttons down
                    if since[path] is None:
                        since[path] = now
                    elif now - since[path] >= hold:
                        log(f"combo held {hold}s on {path} -> quitting")
                        _quit(args.quit_cmd)
                        return
                else:
                    since[path] = None
    except KeyboardInterrupt:
        pass
    finally:
        for fd in fds.values():
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    main()
