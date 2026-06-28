#!/usr/bin/env python3
"""Capture ONE physical press / axis move as the lindbergh-loader EVDEV token (MAD binder).

Reads every input device WITHOUT grab and prints the first matching event as the loader-exact
token: san(device.name) + "_" + code-name, identical to guncap.py / xarcade-cap.py (whose tokens
are proven to work in the loader, including the literal "UNKNOWN_" prefix that is part of the raw
Sinden device names). The smoother already EVIOCGRABs the RAW Sinden mice, so those are silent and
we naturally capture the SMOOTHED mouse + the keyboards + any pad/wheel. Devices are dedup-suffixed
(_2, _3, …) in /dev/input path order, matching the loader's enumeration.

  (default)  capture a button: first EV_KEY value==1  -> BTN_/KEY_ token
  --axis     capture an analog channel (ANALOGUE_n: steer/aim/pedal/throttle): first axis moved
             > 25% of its range  -> bare ABS token (no _MIN/_MAX)
  --timeout SECONDS  (default 10)

Output on success (rc 0): one JSON line {"token","name","device"}.
rc 2 = timeout, rc 3 = python-evdev missing, rc 4 = no usable devices.
"""
import argparse
import json
import select
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except Exception:
    sys.exit(3)

_SKIP = ("accel", "gyro", "motion", "video bus", "power button", "sleep button",
         "hdmi", "consumer control")


# The loader's normaliseName() (evdevInput.c) uppercases and replaces ONLY these chars
# with '_', preserving '.', '+', ':', "'", '&' etc. Matching it exactly is required: the
# loader strcmp's the ini token against its own normalised runtime name, so a device like
# "8BitDo SN30 Pro+" or "T.16000M" must keep its '+'/'.' to match.
_SAN_REPL = frozenset(" /(,=-)")


def san(s: str) -> str:
    return "".join("_" if c in _SAN_REPL else c for c in s).upper()


# The loader's codename() names these gamepad codes by their CARDINAL alias on modern
# kernels (BTN_SOUTH/EAST/NORTH/WEST), not the legacy A/B/X/Y that python-evdev lists
# first. Emit the cardinal name so a re-bound face button matches what the loader reads.
_GAMEPAD_FACE = {0x130: "BTN_SOUTH", 0x131: "BTN_EAST", 0x133: "BTN_NORTH", 0x134: "BTN_WEST"}


def kname(code: int) -> str:
    if code in _GAMEPAD_FACE:
        return _GAMEPAD_FACE[code]
    n = ecodes.bytype[ecodes.EV_KEY].get(code)
    if isinstance(n, (list, tuple)):
        for x in n:
            if x.startswith("BTN_"):
                return x
        return n[0]
    return n or f"KEYCODE_{code}"


def aname(code: int) -> str:
    n = ecodes.ABS.get(code)
    if isinstance(n, (list, tuple)):
        n = n[0]
    return n or f"ABS_{code}"


def _open(axis: bool) -> dict:
    """fd -> (dev, tag, absinfo). Tag = san(name) + path-order _2/_3 dedup."""
    devs, seen = {}, {}
    for p in sorted(list_devices()):
        try:
            d = InputDevice(p)
        except Exception:
            continue
        caps = d.capabilities()
        if ecodes.EV_KEY not in caps and ecodes.EV_ABS not in caps:
            continue
        if any(b in d.name.lower() for b in _SKIP):
            continue
        base = san(d.name)
        seen[base] = seen.get(base, 0) + 1
        tag = base if seen[base] == 1 else f"{base}_{seen[base]}"
        # Always capture ABS info + resting baselines — button mode needs them too, to bind
        # an analog trigger (e.g. X-Arcade LT/RT = ABS_Z/ABS_RZ) driven to its extreme.
        am, bl = {}, {}
        try:
            for code, info in caps.get(ecodes.EV_ABS, []):
                am[code], bl[code] = info, info.value
        except Exception:
            pass
        devs[d.fd] = (d, tag, (am, bl))
    return devs


def loader_tags() -> list:
    """[{path, name, tag}] for every device the loader/capture sees, in /dev/input
    string-sort order with the SAME san()+dup-suffix tagging as _open — so a `tag`
    here is exactly what the loader reads and what a capture wrote into lindbergh.ini.
    Lightweight (opens only to read caps + name, closes immediately; no grab/read)."""
    out, seen = [], {}
    for p in sorted(list_devices()):
        try:
            d = InputDevice(p)
        except Exception:
            continue
        try:
            caps = d.capabilities()
            name = d.name
        except Exception:
            try:
                d.close()
            except Exception:
                pass
            continue
        try:
            if ecodes.EV_KEY not in caps and ecodes.EV_ABS not in caps:
                continue
            if any(b in name.lower() for b in _SKIP):
                continue
            base = san(name)
            seen[base] = seen.get(base, 0) + 1
            tag = base if seen[base] == 1 else f"{base}_{seen[base]}"
            out.append({"path": p, "name": name, "tag": tag})
        finally:
            try:
                d.close()
            except Exception:
                pass
    return out


def _read(devs: dict, axis: bool):
    """Yield {token,name,device} for presses/axis-moves seen in a ~0.5s window."""
    r, _, _ = select.select(list(devs), [], [], 0.5)
    for fd in r:
        d, tag, absinfo = devs[fd]
        try:
            for ev in d.read():
                if not axis and ev.type == ecodes.EV_KEY and ev.value == 1:
                    yield {"token": f"{tag}_{kname(ev.code)}", "name": kname(ev.code), "device": d.name}
                elif ev.type == ecodes.EV_ABS:
                    info_map, baseline = absinfo
                    info = info_map.get(ev.code)
                    if not info or info.max - info.min <= 0:
                        continue
                    rng = info.max - info.min
                    base = baseline.get(ev.code, info.value)
                    nm = aname(ev.code)
                    if axis:
                        # A hat (D-pad) is never a valid ANALOGUE_n channel; ignore it so a stray
                        # d-pad bump doesn't bind a bogus (asymmetric) bare hat token — the user
                        # just re-actuates the real wheel/pedal/stick.
                        if 0x10 <= ev.code <= 0x17:
                            continue
                        # ANALOGUE_n bind (wheel / pedal / aim): any real move -> bare axis token
                        if abs(ev.value - base) > rng * 0.25:
                            baseline[ev.code] = ev.value  # re-arm: fire again only on the next move
                            yield {"token": f"{tag}_{nm}", "name": nm, "device": d.name}
                    # D-pad as a hat axis (ABS_HAT0X..HAT3Y = codes 0x10-0x17). A hat rests at its
                    # MIDPOINT (0) and moves only +-1, so the trigger move-from-rest guard below never
                    # fires for it. Bind by DIRECTION: value -1 -> _MIN (up/left), +1 -> _MAX (down/right),
                    # value 0 (release) emits nothing. Hats rest at the midpoint, so the loader's
                    # _MIN/_MAX release correctly (no stick bug); the bare token is asymmetric for a hat
                    # (fires only at +1), so a hat MUST bind via _MIN/_MAX, never bare.
                    elif 0x10 <= ev.code <= 0x17:
                        if ev.value <= info.min:
                            yield {"token": f"{tag}_{nm}_MIN", "name": f"{nm}_MIN", "device": d.name}
                        elif ev.value >= info.max:
                            yield {"token": f"{tag}_{nm}_MAX", "name": f"{nm}_MAX", "device": d.name}
                    # button bind: an analog trigger driven to its MAX extreme -> the loader's BARE
                    # axis token (X-Arcade LT=ABS_Z / RT=ABS_RZ -> ..._ABS_Z / ..._ABS_RZ, NO suffix).
                    # The bare token binds the loader's NO_SPECIAL_FUNCTION digital path
                    # (evdevInput.c:1276,1341-1344): setSwitch(scaled<0.8?0:1) on EVERY event, so it
                    # presses at the same point (scaled>=0.8) but releases cleanly at rest. The _MAX
                    # token instead binds ANALOGUE_TO_DIGITAL_MAX, whose release is gated behind
                    # value>=midpoint (evdevInput.c:1366-1382); a trigger snapping 255->0 skips that
                    # gate so the button STICKS on. So we deliberately emit the bare token, not _MAX.
                    elif ev.value >= info.max - rng * 0.25 and ev.value - base > rng * 0.6:
                        yield {"token": f"{tag}_{nm}", "name": nm, "device": d.name}
                    # MIN extreme (an axis driven DOWN past its rest): the loader has NO clean stable
                    # named token for the negative direction (only the unstable path-based ABS_NEG tech
                    # name; the bare positive token would read pressed-at-rest). _MIN binds the mirror
                    # buggy ANALOGUE_TO_DIGITAL_MIN path. Not reachable by X-Arcade LT/RT (they rest at
                    # 0, so only the MAX branch fires); left as-is for the unusual min-resting case.
                    elif ev.value <= info.min + rng * 0.25 and base - ev.value > rng * 0.6:
                        yield {"token": f"{tag}_{nm}_MIN", "name": f"{nm}_MIN", "device": d.name}
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", action="store_true")
    ap.add_argument("--monitor", action="store_true",
                    help="live readout: emit EVERY press as a JSON line until --timeout")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()
    deadline = time.monotonic() + args.timeout

    if args.monitor:
        # Re-scan devices each pass so the gun's SMOOTHED node is picked up once the
        # Sinden pipeline finishes coming up after capture mode is started.
        while time.monotonic() < deadline:
            devs = _open(args.axis)
            if not devs:
                time.sleep(0.5)
                continue
            stop = min(deadline, time.monotonic() + 2.0)
            while time.monotonic() < stop:
                for tok in _read(devs, args.axis):
                    print(json.dumps(tok), flush=True)
            for d, _, _ in devs.values():
                try:
                    d.close()
                except Exception:
                    pass
        return 0

    devs = _open(args.axis)
    if not devs:
        return 4
    while time.monotonic() < deadline:
        for tok in _read(devs, args.axis):
            print(json.dumps(tok))
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
