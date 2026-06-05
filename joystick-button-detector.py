#!/usr/bin/env python3
"""Read Linux joystick events from every /dev/input/jsN and print
button presses as they happen. Run, press the X-Arcade buttons one
by one, watch which (device, button) reports.

Usage:  joystick-button-detector.py            # all joysticks
        joystick-button-detector.py 0 1        # only js0 and js1
"""
import fcntl
import os
import select
import struct
import sys
import glob

# Linux joystick event struct: u32 time, s16 value, u8 type, u8 number
JS_EVENT_FORMAT = "IhBB"
JS_EVENT_SIZE = struct.calcsize(JS_EVENT_FORMAT)
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80  # synthetic on open — ignore

# JSIOCGNAME ioctl to read joystick name
def get_name(fd):
    JSIOCGNAME = 0x80006a13  # 0x80 (read) | (0x6a << 8) | 0x13 | (size << 16)
    JSIOCGNAME = (2 << 30) | (128 << 16) | (0x6a << 8) | 0x13
    buf = bytearray(128)
    try:
        fcntl.ioctl(fd, JSIOCGNAME, buf, True)
        return buf.split(b'\x00', 1)[0].decode('utf-8', errors='replace')
    except OSError:
        return "?"

def main():
    if len(sys.argv) > 1:
        paths = [f"/dev/input/js{n}" for n in sys.argv[1:]]
    else:
        paths = sorted(glob.glob("/dev/input/js[0-9]*"))

    fds = {}
    for p in paths:
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
            name = get_name(fd)
            fds[fd] = (p, name)
            print(f"opened {p:20s}  \"{name}\"")
        except OSError as e:
            print(f"skip {p}: {e}")

    if not fds:
        print("no joysticks", file=sys.stderr); return 1

    print()
    print("Press buttons on the X-Arcade. Ctrl+C to stop.")
    print("─" * 60)

    poll = select.poll()
    for fd in fds:
        poll.register(fd, select.POLLIN)

    try:
        while True:
            for fd, ev in poll.poll(-1):
                while True:
                    try:
                        buf = os.read(fd, JS_EVENT_SIZE)
                    except BlockingIOError:
                        break
                    if not buf or len(buf) < JS_EVENT_SIZE:
                        break
                    t, value, ev_type, number = struct.unpack(JS_EVENT_FORMAT, buf)
                    if ev_type & JS_EVENT_INIT:
                        continue
                    path, name = fds[fd]
                    if ev_type == JS_EVENT_BUTTON:
                        action = "PRESS" if value else "release"
                        # In Supermodel.ini, JOY[N]_BUTTON# uses 1-based numbering
                        # but jsX's number is 0-based. Show both.
                        print(f"  {path}  [{name}]  button {number}  ({action})  → Supermodel: JOY?_BUTTON{number+1}")
                    elif ev_type == JS_EVENT_AXIS:
                        if abs(value) > 8000:
                            print(f"  {path}  [{name}]  axis {number} = {value:+}")
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        for fd in fds:
            os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
