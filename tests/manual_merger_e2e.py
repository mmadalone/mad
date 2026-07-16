#!/usr/bin/env python3
"""End-to-end merger check with a SYNTHETIC source pad (no real hardware).

Creates a fake "DualSense-shaped" uinput pad, runs the merger against it, then
drives the fake pad and reads what the twin emits. Proves the real chain:
    fake real pad -> merger grab -> translate -> twin -> observable events
"""
import subprocess, sys, time, os
sys.path.insert(0, "/home/deck/Emulation/tools/launchers")
from evdev import UInput, AbsInfo, InputDevice, ecodes as e, list_devices

LAUNCHERS = "/home/deck/Emulation/tools/launchers"

# A fake pad that CLASS_OF_VIDPID knows: 054c:0ce6 = DualSense = class "ps"
STICK = AbsInfo(0, -32768, 32767, 16, 128, 0)
HAT = AbsInfo(0, -1, 1, 0, 0, 0)
TRIG = AbsInfo(0, 0, 255, 0, 0, 0)
CAPS = {
    e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL,
               e.BTN_TR, e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL,
               e.BTN_THUMBR],
    e.EV_ABS: [(e.ABS_X, STICK), (e.ABS_Y, STICK), (e.ABS_Z, TRIG),
               (e.ABS_RX, STICK), (e.ABS_RY, STICK), (e.ABS_RZ, TRIG),
               (e.ABS_HAT0X, HAT), (e.ABS_HAT0Y, HAT)],
}

def find(vid, pid, name_sub=""):
    for p in list_devices():
        try:
            d = InputDevice(p)
            if d.info.vendor == vid and d.info.product == pid and name_sub in d.name:
                return d
        except OSError:
            pass
    return None

def main():
    src = UInput(CAPS, name="Wireless Controller", vendor=0x054c, product=0x0ce6,
                 version=1, bustype=e.BUS_USB)
    time.sleep(0.5)
    print(f"fake source pad created (054c:0ce6 'Wireless Controller')")

    probe = subprocess.run([sys.executable, "mad-openbor-pads.py", "--probe"],
                           cwd=LAUNCHERS, capture_output=True, text=True)
    print("probe:", probe.stdout.strip(), f"(rc={probe.returncode})")
    if probe.returncode != 0:
        print("FAIL: merger did not plan our fake pad"); src.close(); return 1

    merger = subprocess.Popen([sys.executable, "mad-openbor-pads.py"],
                             cwd=LAUNCHERS, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    line = merger.stdout.readline().strip()
    print(f"merger says: {line!r}")
    if line != "READY":
        merger.kill(); src.close(); print("FAIL: no READY"); return 1
    time.sleep(0.5)

    twin = find(0x4d41, 0x0002, "OpenBOR")
    if not twin:
        merger.terminate(); src.close(); print("FAIL: no twin device"); return 1
    caps = twin.capabilities()
    nbtn = len(caps.get(e.EV_KEY, []))
    nax = len([a for a, _ in caps.get(e.EV_ABS, []) if a not in (e.ABS_HAT0X, e.ABS_HAT0Y)])
    print(f"twin: {twin.name!r} {nbtn} buttons / {nax} axes  -> hat base {nbtn + 2*nax}")

    os.set_blocking(twin.fd, False)
    def drain():
        out = []
        time.sleep(0.35)
        try:
            for ev in twin.read():
                if ev.type in (e.EV_KEY, e.EV_ABS):
                    out.append((ev.type, ev.code, ev.value))
        except BlockingIOError:
            pass
        return out

    drain()
    results = []

    # 1. PS Square (BTN_WEST 0x134) must become canonical X == twin BTN_NORTH
    src.write(e.EV_KEY, e.BTN_WEST, 1); src.syn()
    got = drain()
    ok = (e.EV_KEY, e.BTN_NORTH, 1) in got
    results.append(("PS Square -> canonical X (BTN_NORTH)", ok, got))
    src.write(e.EV_KEY, e.BTN_WEST, 0); src.syn(); drain()

    # 2. real d-pad up -> twin hat up
    src.write(e.EV_ABS, e.ABS_HAT0Y, -1); src.syn()
    got = drain()
    ok = (e.EV_ABS, e.ABS_HAT0Y, -1) in got
    results.append(("d-pad up -> twin hat up", ok, got))
    src.write(e.EV_ABS, e.ABS_HAT0Y, 0); src.syn(); drain()

    # 3. THE HEADLINE: left stick pushed up ALSO drives the twin's hat
    src.write(e.EV_ABS, e.ABS_Y, -30000); src.syn()
    got = drain()
    ok = (e.EV_ABS, e.ABS_HAT0Y, -1) in got
    results.append(("left STICK up -> twin hat up (stick+dpad!)", ok, got))
    src.write(e.EV_ABS, e.ABS_Y, 0); src.syn(); drain()

    # 4. stick inside the deadzone must NOT move the hat
    src.write(e.EV_ABS, e.ABS_Y, -8000); src.syn()   # ~24% < ENGAGE 40%
    got = drain()
    ok = not any(c == e.ABS_HAT0Y and v != 0 for _, c, v in got)
    results.append(("small stick tilt -> hat stays put", ok, got))
    src.write(e.EV_ABS, e.ABS_Y, 0); src.syn(); drain()

    # 5. analog trigger travel passes through
    src.write(e.EV_ABS, e.ABS_RZ, 255); src.syn()
    got = drain()
    ok = any(c == e.ABS_RZ and v > 200 for _, c, v in got)
    results.append(("right trigger -> twin RZ (SPECIAL)", ok, got))

    merger.terminate()
    try: merger.wait(timeout=5)
    except subprocess.TimeoutExpired: merger.kill()
    src.close()
    time.sleep(0.4)
    left = find(0x4d41, 0x0002, "OpenBOR")
    results.append(("twin removed on merger exit", left is None, left))

    print("\n=============== MERGER E2E ===============")
    bad = 0
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            bad += 1
            print(f"         got: {detail}")
    print("=========================================")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
