#!/usr/bin/env python3
"""
Sinden evdev jitter smoother (Linux).

Reads raw Sinden P1/P2 events from /dev/input/sinden-gun-p[12]-event,
applies an EMA low-pass filter + deadzone snap-to-rest on ABS_X/Y (same
algorithm as the user's Windows HOTDOV.ahk and EPOCH Sinden.ahk scripts),
and re-emits via uinput virtual mouse devices.

Other evdev consumers (ManyMouse → Supermodel etc.) read the smoothed
virtual devices instead of the jittery raw ones. The raw Sinden devices
are exclusively grabbed (EVIOCGRAB) so no one downstream sees the raw
events while this daemon is running.

Settings file: ~/Emulation/storage/sinden/smoother.ini
   [smoothing]
   alpha    = 0.12   ; EMA factor — lower = smoother, higher = snappier
   deadzone = 1.6    ; when |raw - filtered| < this, snap to raw (avoids
                     ; "swimming" cursor when holding the gun still)

Signals:
   SIGTERM/SIGINT — release grabs, destroy uinput, exit
   SIGHUP         — reload settings file

Stdout:
   Prints "READY" once both virtual devices are live, so a parent
   launcher can synchronise.
"""
import configparser
import os
import signal
import sys
from pathlib import Path
from select import select

import evdev
from evdev import UInput, ecodes, AbsInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import mad_paths  # noqa: E402

SETTINGS_PATH = mad_paths.storage("sinden", "smoother.ini")
DEFAULT_ALPHA = 0.12
DEFAULT_DEADZONE = 1.6
# When |raw - filtered| exceeds snap_threshold (in ABS units, range 0..32767),
# snap the filter directly to raw instead of catching up over many frames.
# This kills the "cursor sticks" feel on fast aim — the smoother is only
# active for slow tracking, where EMA actually helps.
DEFAULT_SNAP_THRESHOLD = 1000.0

P1_PATH = "/dev/input/sinden-gun-p1-event"
P2_PATH = "/dev/input/sinden-gun-p2-event"

# Set by reload_settings(); read by main loop. Modified by SIGHUP handler.
ALPHA = DEFAULT_ALPHA
DEADZONE = DEFAULT_DEADZONE
SNAP_THRESHOLD = DEFAULT_SNAP_THRESHOLD


def load_settings():
    """Parse smoother.ini. Returns (alpha, deadzone, snap_threshold)."""
    a, d, s_thr = DEFAULT_ALPHA, DEFAULT_DEADZONE, DEFAULT_SNAP_THRESHOLD
    if SETTINGS_PATH.exists():
        cfg = configparser.ConfigParser()
        try:
            cfg.read(SETTINGS_PATH)
            s = cfg["smoothing"]
            a = float(s.get("alpha", a))
            d = float(s.get("deadzone", d))
            s_thr = float(s.get("snap_threshold", s_thr))
        except (KeyError, ValueError, configparser.Error) as e:
            print(f"[smoother] settings parse error, using defaults: {e}",
                  file=sys.stderr)
    return a, d, s_thr


def make_virtual(name, src):
    """Build a uinput device cloning src's ABS_X/Y range + EV_KEY caps."""
    abs_x = src.absinfo(ecodes.ABS_X)
    abs_y = src.absinfo(ecodes.ABS_Y)
    caps = {
        ecodes.EV_KEY: src.capabilities().get(ecodes.EV_KEY, []),
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(value=0, min=abs_x.min, max=abs_x.max,
                                   fuzz=abs_x.fuzz, flat=abs_x.flat,
                                   resolution=abs_x.resolution)),
            (ecodes.ABS_Y, AbsInfo(value=0, min=abs_y.min, max=abs_y.max,
                                   fuzz=abs_y.fuzz, flat=abs_y.flat,
                                   resolution=abs_y.resolution)),
        ],
    }
    return UInput(caps, name=name,
                  vendor=src.info.vendor, product=src.info.product,
                  version=src.info.version)


def smooth(filtered, raw, alpha, deadzone, snap_threshold, abs_min, abs_max,
           edge_margin=30):
    """Apply adaptive smoothing. Returns (new_filtered, emit_value).

    Edge bypass: if raw is at or within edge_margin of the device's ABS_X/Y
    min/max, snap filter to raw and emit raw. This ensures the cursor can
    reach the screen corners/edges — EMA approaches raw asymptotically and
    would otherwise stop short by a few units when tracking slowly to an edge.

    Otherwise effective alpha blends gradually from base alpha (slow motion,
    max smoothing) up to 1.0 (very fast motion, no smoothing/instant snap),
    so motion that crosses the snap_threshold boundary doesn't stutter:
       delta <=     snap_threshold: effective_alpha = alpha
       delta == 2 * snap_threshold: effective_alpha ≈ (alpha + 1) / 2
       delta >= 3 * snap_threshold: effective_alpha = 1.0

    Deadzone snaps emit to raw when filter is sub-pixel-close, killing
    jitter at rest.
    """
    if raw <= abs_min + edge_margin or raw >= abs_max - edge_margin:
        return float(raw), raw
    delta = abs(raw - filtered)
    if delta > snap_threshold:
        boost = min(1.0, (delta - snap_threshold) / (2.0 * snap_threshold))
        effective_alpha = alpha + (1.0 - alpha) * boost
    else:
        effective_alpha = alpha
    new = filtered + effective_alpha * (raw - filtered)
    emit = raw if abs(raw - new) < deadzone else int(round(new))
    return new, emit


def main():
    global ALPHA, DEADZONE, SNAP_THRESHOLD
    ALPHA, DEADZONE, SNAP_THRESHOLD = load_settings()
    print(f"[smoother] starting: alpha={ALPHA} deadzone={DEADZONE} "
          f"snap_threshold={SNAP_THRESHOLD}", file=sys.stderr)

    try:
        src_p1 = evdev.InputDevice(P1_PATH)
        src_p2 = evdev.InputDevice(P2_PATH)
    except (FileNotFoundError, PermissionError) as e:
        print(f"[smoother] FATAL: cannot open Sinden devices: {e}",
              file=sys.stderr)
        sys.exit(1)

    src_p1.grab()
    src_p2.grab()

    v_p1 = make_virtual("SindenLightgun Mouse (Smoothed P1)", src_p1)
    v_p2 = make_virtual("SindenLightgun Mouse (Smoothed P2)", src_p2)

    # Stash per-device ABS_X/Y ranges so smooth() can detect edge bypasses.
    def mk_state(v, src):
        ax = src.absinfo(ecodes.ABS_X)
        ay = src.absinfo(ecodes.ABS_Y)
        return {
            "v": v, "fx": None, "fy": None,
            "x_min": ax.min, "x_max": ax.max,
            "y_min": ay.min, "y_max": ay.max,
        }

    state = {
        src_p1.fd: mk_state(v_p1, src_p1),
        src_p2.fd: mk_state(v_p2, src_p2),
    }

    def reload(*_):
        global ALPHA, DEADZONE, SNAP_THRESHOLD
        ALPHA, DEADZONE, SNAP_THRESHOLD = load_settings()
        print(f"[smoother] reloaded: alpha={ALPHA} deadzone={DEADZONE} "
              f"snap_threshold={SNAP_THRESHOLD}", file=sys.stderr)

    def shutdown(*_):
        print("[smoother] shutting down", file=sys.stderr)
        try:
            src_p1.ungrab(); src_p2.ungrab()
        except Exception:
            pass
        try:
            v_p1.close(); v_p2.close()
        except Exception:
            pass
        sys.exit(0)

    # SIGHUP/SIGUSR2 reload settings from smoother.ini.
    # SIGUSR1 toggles alpha to 1.0 (passthrough) ↔ saved value.
    saved_alpha = [None]  # captured by closure when toggled off

    def toggle(*_):
        global ALPHA
        if saved_alpha[0] is None:
            saved_alpha[0] = ALPHA
            ALPHA = 1.0  # no filtering
            print(f"[smoother] SIGUSR1 — bypass ON (alpha=1.0)", file=sys.stderr)
        else:
            ALPHA = saved_alpha[0]
            saved_alpha[0] = None
            print(f"[smoother] SIGUSR1 — bypass OFF (alpha={ALPHA})", file=sys.stderr)

    signal.signal(signal.SIGHUP, reload)
    signal.signal(signal.SIGUSR2, reload)
    signal.signal(signal.SIGUSR1, toggle)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Signal readiness to parent launcher
    print("READY", flush=True)

    devices = [src_p1, src_p2]
    try:
        while True:
            r, _, _ = select(devices, [], [])
            for d in r:
                s = state[d.fd]
                v = s["v"]
                for event in d.read():
                    if event.type == ecodes.EV_ABS:
                        if event.code == ecodes.ABS_X:
                            if s["fx"] is None:
                                s["fx"] = float(event.value)
                                out = event.value
                            else:
                                s["fx"], out = smooth(s["fx"], event.value,
                                                       ALPHA, DEADZONE,
                                                       SNAP_THRESHOLD,
                                                       s["x_min"], s["x_max"])
                            v.write(ecodes.EV_ABS, ecodes.ABS_X, out)
                        elif event.code == ecodes.ABS_Y:
                            if s["fy"] is None:
                                s["fy"] = float(event.value)
                                out = event.value
                            else:
                                s["fy"], out = smooth(s["fy"], event.value,
                                                       ALPHA, DEADZONE,
                                                       SNAP_THRESHOLD,
                                                       s["y_min"], s["y_max"])
                            v.write(ecodes.EV_ABS, ecodes.ABS_Y, out)
                        else:
                            v.write(event.type, event.code, event.value)
                    elif event.type == ecodes.EV_SYN:
                        v.syn()
                    else:
                        v.write(event.type, event.code, event.value)
    except OSError as e:
        print(f"[smoother] read error: {e}", file=sys.stderr)
        shutdown()


if __name__ == "__main__":
    main()
