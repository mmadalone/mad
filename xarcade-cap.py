#!/usr/bin/env python3
# Capture X-Arcade / gamepad tokens (buttons AND joystick axes) as lindbergh-loader sees them.
import sys, select
from evdev import InputDevice, list_devices, ecodes

def san(s):
    return ''.join(c if c.isalnum() else '_' for c in s).upper()

def kname(code):
    n = ecodes.bytype[ecodes.EV_KEY].get(code)
    if isinstance(n, (list, tuple)):
        for x in n:
            if x.startswith('BTN_'):
                return x
        return n[0]
    return n or ('KEYCODE_%d' % code)

def aname(code):
    n = ecodes.ABS.get(code)
    if isinstance(n, (list, tuple)):
        n = n[0]
    return n or ('ABS_%d' % code)

devs = {}
for p in sorted(list_devices()):
    try:
        d = InputDevice(p)
    except Exception:
        continue
    nl = d.name.lower()
    if any(k in nl for k in ('x-box', 'xbox', 'microsoft', 'x-arcade', 'tankstick', '360')):
        absinfo = {}
        try:
            for code, info in d.capabilities().get(ecodes.EV_ABS, []):
                absinfo[code] = info
        except Exception:
            pass
        devs[d.fd] = (d, san(d.name), absinfo)
        print('LISTEN  %-40s' % d.name, flush=True)

if not devs:
    print('NO X-ARCADE / GAMEPAD DEVICES FOUND', flush=True)
    sys.exit(1)
print('READY -- press: joystick UP, joystick DOWN, then the A button (pause between each)', flush=True)

last = {}
while True:
    r, _, _ = select.select(list(devs), [], [], 1.0)
    for fd in r:
        d, tag, absinfo = devs[fd]
        try:
            for ev in d.read():
                if ev.type == ecodes.EV_KEY and ev.value == 1:
                    print('BUTTON  %s_%s' % (tag, kname(ev.code)), flush=True)
                elif ev.type == ecodes.EV_ABS:
                    info = absinfo.get(ev.code)
                    if not info:
                        continue
                    rng = info.max - info.min
                    if rng <= 0:
                        continue
                    if ev.value <= info.min + rng * 0.25:
                        if last.get(ev.code) != 'min':
                            last[ev.code] = 'min'
                            print('AXIS    %s_%s -> MIN (push UP/LEFT)  bind token: %s_%s_MIN'
                                  % (tag, aname(ev.code), tag, aname(ev.code)), flush=True)
                    elif ev.value >= info.max - rng * 0.25:
                        if last.get(ev.code) != 'max':
                            last[ev.code] = 'max'
                            print('AXIS    %s_%s -> MAX (push DOWN/RIGHT)  bind token: %s_%s_MAX'
                                  % (tag, aname(ev.code), tag, aname(ev.code)), flush=True)
                    else:
                        last[ev.code] = None
        except OSError:
            pass
