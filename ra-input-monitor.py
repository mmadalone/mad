#!/usr/bin/env python3
"""RA-input monitor for the X-Arcade. Read-only (no grab). For every control you press it shows
the index I compute (replicating RA's udev enumeration) AND what YOUR RA's autoconfig actually
binds that index to — so any mismatch between my reconstruction and your machine's truth is
visible. It also dumps the full ordered map from your autoconfig at the top.

WHAT'S SOURCED vs. NOT:
  - BUTTON rank = the 4-loop scan (KEY_UP..KEY_DOWN, BTN_MISC..KEY_MAX, 0..KEY_UP,
    KEY_DOWN+1..BTN_MISC) — VERBATIM from libretro master udev_joypad.c. Trusted.
  - D-PAD as buttons (base+offset) = NOT in master (master keeps hats SEPARATE, queried via
    GET_HAT_DIR -> "h0up"). It comes from YOUR autoconfig, which is the only authority for your
    RA build. The autoconfig column below is the GROUND TRUTH; my computed column is the suspect.

Covers both X-Arcade devices: GAMEPAD (045e:02a1) -> RA joypad; TRACKBALL (1241:1111) -> RA mouse
buttons (mbtn 1=left/2=right/3=middle/4=side/5=extra).

Usage:  python3 ra-input-monitor.py [seconds]   (default 600). Press each control ONCE; Ctrl-C stops.
"""
import os
import re
import select
import sys
import time
from pathlib import Path

import evdev
from evdev import ecodes as e

GAMEPAD = (0x045E, 0x02A1)
TRACKBALL = (0x1241, 0x1111)
_MBTN = {e.BTN_LEFT: 1, e.BTN_RIGHT: 2, e.BTN_MIDDLE: 3, e.BTN_SIDE: 4, e.BTN_EXTRA: 5}
AUTOCONF_DIR = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/autoconfig/udev"
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0


def btn_name(code):
    names = e.BTN.get(code) or e.KEY.get(code)
    return (names[0] if isinstance(names, list) else names) or f"0x{code:x}"


def abs_name(code):
    n = e.ABS.get(code)
    return (n[0] if isinstance(n, list) else n) or f"0x{code:x}"


def btn_rank(keys):
    present = set(keys)
    m, idx = {}, 0
    for rng in (range(e.KEY_UP, e.KEY_DOWN + 1), range(e.BTN_MISC, e.KEY_MAX),
                range(0, e.KEY_UP), range(e.KEY_DOWN + 1, e.BTN_MISC)):
        for c in rng:
            if c in present:
                m[c] = idx
                idx += 1
    return m, idx


def load_autoconfig(name, vid, pid):
    """Parse the udev autoconfig matching this pad (name first, else vid:pid — same rule RA +
    device_binds use). Returns (btn_idx{int->[ctrl]}, btn_hat{token->[ctrl]}, axis{val->[ctrl]}, path)."""
    if not AUTOCONF_DIR.is_dir():
        return {}, {}, {}, None
    chosen = by_id = None
    for f in sorted(AUTOCONF_DIR.glob("*.cfg")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        nm = re.search(r'input_device\s*=\s*"([^"]*)"', txt)
        if nm and nm.group(1) == name:
            chosen = (f, txt)
            break
        vm = re.search(r'input_vendor_id\s*=\s*"?(\d+)"?', txt)
        pm = re.search(r'input_product_id\s*=\s*"?(\d+)"?', txt)
        if vm and pm and int(vm.group(1)) == vid and int(pm.group(1)) == pid and by_id is None:
            by_id = (f, txt)
    if chosen is None:
        chosen = by_id
    if chosen is None:
        return {}, {}, {}, None
    f, txt = chosen
    btn_idx, btn_hat, axis = {}, {}, {}
    for m in re.finditer(r'^[ \t]*input_([a-z0-9_]+?)_btn\s*=\s*"([^"]*)"', txt, re.M):
        ctrl, val = m.group(1), m.group(2)
        if val.lstrip("-").isdigit():
            btn_idx.setdefault(int(val), []).append(ctrl)
        elif val and val[0] == "h":
            btn_hat.setdefault(val, []).append(ctrl)
    for m in re.finditer(r'^[ \t]*input_([a-z0-9_]+?)_axis\s*=\s*"([^"]*)"', txt, re.M):
        axis.setdefault(m.group(2), []).append(m.group(1))
    return btn_idx, btn_hat, axis, f


def open_devs():
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except Exception:
            continue
        vp = (d.info.vendor, d.info.product)
        if vp not in (GAMEPAD, TRACKBALL):
            d.close()                        # not a target device; release the fd, don't leak it
            continue
        os.set_blocking(d.fd, False)
        tag = "ev" + path.rsplit("event", 1)[-1]
        if vp == TRACKBALL:
            devs.append({"d": d, "kind": "mouse", "tag": tag})
            continue
        caps = d.capabilities(absinfo=True)
        absinfo = dict(caps.get(e.EV_ABS, []))
        brank, base = btn_rank(caps.get(e.EV_KEY, []))
        axis_codes = sorted(c for c in absinfo if not (e.ABS_HAT0X <= c <= e.ABS_HAT3Y))
        bi, bh, ax, acf = load_autoconfig(d.name, d.info.vendor, d.info.product)
        devs.append({"d": d, "kind": "pad", "tag": tag, "brank": brank, "base": base,
                     "arank": {c: i for i, c in enumerate(axis_codes)}, "absinfo": absinfo,
                     "btn_idx": bi, "btn_hat": bh, "axis_cfg": ax, "acf": acf})
    return devs


def auto_btn(p, idx):
    c = p["btn_idx"].get(idx)
    return f"autoconfig[{idx}] = {','.join(c)}  ✓" if c else f"autoconfig: NO control at {idx}  ⚠"


def classify(p, ev, axis_rest):
    if p["kind"] == "mouse":
        if ev.type == e.EV_KEY and ev.value == 1 and ev.code in _MBTN:
            return (p["tag"], "m", ev.code), (
                f"[{p['tag']}] MOUSE  {btn_name(ev.code):<13} ->  RA mouse button {_MBTN[ev.code]}")
        return None, None
    if ev.type == e.EV_KEY and ev.value == 1:
        idx = p["brank"].get(ev.code)
        return (p["tag"], "k", ev.code), (
            f"[{p['tag']}] BUTTON {btn_name(ev.code):<13} ->  RA button {str(idx):<3}  | {auto_btn(p, idx)}")
    if ev.type == e.EV_ABS and e.ABS_HAT0X <= ev.code <= e.ABS_HAT0Y and ev.value != 0:
        if ev.code == e.ABS_HAT0X:
            off, d = (0, "LEFT") if ev.value < 0 else (1, "RIGHT")
        else:
            off, d = (2, "UP") if ev.value < 0 else (3, "DOWN")
        idx = p["base"] + off
        return (p["tag"], "h", ev.code, ev.value < 0), (
            f"[{p['tag']}] D-PAD  {d:<13} ->  RA button {str(idx):<3}  | {auto_btn(p, idx)}")
    if ev.type == e.EV_ABS and ev.code in p["arank"]:
        info = p["absinfo"].get(ev.code)
        rest = axis_rest.setdefault((p["tag"], ev.code), info.value if info else 0)
        span = (info.max - info.min) if info else 65535
        if abs(ev.value - rest) < span * 0.45:
            return None, None
        sign = "+" if ev.value > rest else "-"
        idx = p["arank"][ev.code]
        c = p["axis_cfg"].get(f"{sign}{idx}")
        auto = f"autoconfig[{sign}{idx}] = {','.join(c)}  ✓" if c else f"autoconfig: NO control at {sign}{idx}  ⚠"
        return (p["tag"], "a", ev.code, sign), (
            f"[{p['tag']}] AXIS   {abs_name(ev.code):<13} ->  RA axis {sign}{idx:<2}  | {auto}")
    return None, None


def dump_autoconfig(p):
    print(f"# GROUND TRUTH — your RA autoconfig: {p['acf']}")
    print("#   Buttons (RA index = bound control):")
    for idx in sorted(p["btn_idx"]):
        print(f"#     {idx:>2} = {', '.join(p['btn_idx'][idx])}")
    for tok in sorted(p["btn_hat"]):
        print(f"#     {tok:>4} = {', '.join(p['btn_hat'][tok])}   (HAT TOKEN — not a numbered button)")
    print("#   Axes (RA axis = bound control):")
    for val in sorted(p["axis_cfg"]):
        print(f"#     {val:>3} = {', '.join(p['axis_cfg'][val])}")


def main():
    devs = open_devs()
    if not devs:
        print("No X-Arcade gamepad (045e:02a1) or trackball (1241:1111) found — plugged + Xbox "
              "mode, and NOT grabbed by ES-DE/MAD/a game?")
        return
    for p in devs:
        if p["kind"] == "mouse":
            print(f"# {p['tag']}: {p['d'].name} (TRACKBALL) — clicks -> RA mouse buttons 1..5")
        else:
            print(f"# {p['tag']}: {p['d'].name} (GAMEPAD) — {len(p['brank'])} buttons "
                  f"(idx 0..{p['base']-1}), d-pad hat -> my-computed buttons {p['base']}..{p['base']+3}, "
                  f"{len(p['arank'])} axes")
            if p["acf"]:
                dump_autoconfig(p)
            else:
                print("#   ⚠ no matching RA autoconfig found — can't cross-check.")
    print("\n# Press each control ONCE. 'computed -> RA index' | 'what your autoconfig binds it to'.\n",
          flush=True)

    seen, axis_rest = set(), {}
    fds = {p["d"].fd: p for p in devs}
    deadline = time.monotonic() + SECONDS
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in r:
                p = fds[fd]
                try:
                    for ev in p["d"].read():
                        key, line = classify(p, ev, axis_rest)
                        if line and key not in seen:
                            seen.add(key)
                            print(line, flush=True)
                except BlockingIOError:
                    pass
                except Exception:
                    continue
    except KeyboardInterrupt:
        pass
    print(f"\n# done — {len(seen)} distinct controls captured.")


if __name__ == "__main__":
    main()
