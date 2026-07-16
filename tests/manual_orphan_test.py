#!/usr/bin/env python3
"""Regression proof for the P2 HIGH finding: a launcher SIGKILL must not leave
the merger orphaned holding EVIOCGRAB on the user's pads.

Uses a synthetic DualSense so no real hardware is touched. Replicates
openbor.sh's exact start structure (redirects + cd &&), kill -9's the launcher,
and checks whether the pad is freed.
"""
import os, subprocess, sys, tempfile, time
from evdev import UInput, AbsInfo, InputDevice, ecodes as e, list_devices

L = "/home/deck/Emulation/tools/launchers"
STICK = AbsInfo(0, -32768, 32767, 16, 128, 0); HAT = AbsInfo(0, -1, 1, 0, 0, 0)
TRIG = AbsInfo(0, 0, 255, 0, 0, 0)
CAPS = {e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL,
                   e.BTN_TR, e.BTN_SELECT, e.BTN_START, e.BTN_MODE,
                   e.BTN_THUMBL, e.BTN_THUMBR],
        e.EV_ABS: [(e.ABS_X, STICK), (e.ABS_Y, STICK), (e.ABS_Z, TRIG),
                   (e.ABS_RX, STICK), (e.ABS_RY, STICK), (e.ABS_RZ, TRIG),
                   (e.ABS_HAT0X, HAT), (e.ABS_HAT0Y, HAT)]}

def find(vid, pid):
    for p in list_devices():
        try:
            d = InputDevice(p)
            if d.info.vendor == vid and d.info.product == pid:
                return d
        except OSError: pass
    return None

def grabbable(path):
    """True if nobody else holds an EVIOCGRAB on this node."""
    try:
        d = InputDevice(path); d.grab(); d.ungrab(); d.close(); return True
    except OSError:
        return False

def run(use_exec: bool) -> bool:
    src = UInput(CAPS, name="Wireless Controller", vendor=0x054c, product=0x0ce6,
                 version=1, bustype=e.BUS_USB)
    time.sleep(0.5)
    node = find(0x054c, 0x0ce6).path
    rf = tempfile.mktemp()
    ex = "exec " if use_exec else ""
    # the exact shape of openbor.sh's merger start
    script = f'(cd {L} && {ex}python3 mad-openbor-pads.py > "{rf}" 2>/dev/null) &\nMERGER_PID=$!\necho $MERGER_PID\nwait $MERGER_PID\n'
    sp = tempfile.mktemp(suffix=".sh"); open(sp, "w").write(script)
    launcher = subprocess.Popen(["bash", sp], stdout=subprocess.PIPE, text=True)
    merger_pid = int(launcher.stdout.readline().strip())

    for _ in range(60):
        if os.path.exists(rf) and "READY" in open(rf).read(): break
        time.sleep(0.1)
    time.sleep(0.3)
    grabbed = not grabbable(node)
    twin = find(0x4d41, 0x0002)
    print(f"    merger up: MERGER_PID={merger_pid} "
          f"comm={subprocess.run(['ps','-o','comm=','-p',str(merger_pid)],capture_output=True,text=True).stdout.strip()!r} "
          f"pad_grabbed={grabbed} twin={'yes' if twin else 'NO'}")

    launcher.kill()                      # SIGKILL: the trap can never run
    launcher.wait(); time.sleep(1.5)

    survivors = subprocess.run(["pgrep", "-f", "mad-openbor-pads.py"],
                               capture_output=True, text=True).stdout.split()
    freed = grabbable(node)
    twin_left = find(0x4d41, 0x0002)
    print(f"    after kill -9 launcher: survivors={survivors or 'none'} "
          f"pad_freed={freed} twin_left={'YES' if twin_left else 'no'}")
    for p in survivors:
        try: os.kill(int(p), 9)
        except OSError: pass
    src.close(); time.sleep(0.4)
    for f in (rf, sp):
        try: os.unlink(f)
        except OSError: pass
    return freed and not survivors

if __name__ == "__main__":
    print("\n  WITHOUT exec (the shipped bug):")
    bad = run(False)
    print("\n  WITH exec (the fix):")
    good = run(True)
    print("\n================ ORPHAN TEST ================")
    print(f"  without exec -> pads freed? {bad}   (expect False = the bug)")
    print(f"  with exec    -> pads freed? {good}  (expect True  = fixed)")
    print("  RESULT:", "FIX CONFIRMED" if (good and not bad) else
          ("inconclusive — both behaved the same" if good == bad else "UNEXPECTED"))
    print("=============================================")
    sys.exit(0 if good else 1)
