#!/usr/bin/env python3
# Live capture: prints what each gun button emits. Reads ALL Sinden gun devices
# directly (no smoother needed) -- raw mice carry trigger/pump, keyboards carry the
# side buttons + d-pad.
import sys, select
from evdev import InputDevice, list_devices, ecodes

def san(s):
    return ''.join(c if c.isalnum() else '_' for c in s).upper()

def ename(code):
    n = ecodes.bytype[ecodes.EV_KEY].get(code)
    if isinstance(n, (list, tuple)):
        for x in n:
            if x.startswith('BTN_'):
                return x
        return n[0]
    return n or ('CODE_%d' % code)

devs = {}
seen = {}
for p in sorted(list_devices()):
    try:
        d = InputDevice(p)
    except Exception:
        continue
    if 'sindenlightgun' not in d.name.lower():
        continue
    base = san(d.name)
    seen[base] = seen.get(base, 0) + 1
    tag = base if seen[base] == 1 else base + ('_%d' % seen[base])
    devs[d.fd] = (d, tag)
    print('LISTEN  %-42s' % d.name, flush=True)

if not devs:
    print('NO SINDEN GUN DEVICES FOUND', flush=True)
    sys.exit(1)
print('READY -- press one P1 gun button at a time, in the given order', flush=True)

while True:
    r, _, _ = select.select(list(devs), [], [], 1.0)
    for fd in r:
        d, tag = devs[fd]
        try:
            for ev in d.read():
                if ev.type == ecodes.EV_KEY and ev.value == 1:
                    print('PRESS  %s_%s' % (tag, ename(ev.code)), flush=True)
        except OSError:
            pass
