#!/usr/bin/env python3
"""Live Wii monitor for the Mayflash DolphinBar in MODE 4 — replicates Dolphin's WiimoteReal
init so we get real input on Linux: blocking RW hidraw, EPIPE empty-slot test, set reporting
mode 0x32 (core + extension), and CRUCIALLY re-send 0x12 whenever a 0x20 status arrives or the
stream goes quiet (else the remote stops reporting). Decodes core buttons + Nunchuk + Classic and
logs every change. Read/writes the slot fds only; safe while Dolphin is NOT running.
Log: ~/wii-monitor.log   Stop: Ctrl-C / kill."""
import os, sys, glob, re, select, time, errno

LOG = os.path.expanduser("~/wii-monitor.log")
KEEPALIVE = 1.5   # s of silence -> re-affirm reporting mode

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except OSError: pass
    print(line); sys.stdout.flush()

def db_nodes():
    out = []
    for d in glob.glob("/sys/class/hidraw/hidraw*"):
        try: txt = open(os.path.join(d, "device", "uevent")).read()
        except OSError: continue
        if "v0000057Ep00000306" not in txt: continue
        idx = 99
        for ln in txt.splitlines():
            if ln.startswith("HID_PHYS="):
                m = re.search(r"input(\d+)", ln); idx = int(m.group(1)) if m else 99
        out.append((idx, "/dev/" + os.path.basename(d)))
    return [n for _, n in sorted(out)]

def w(fd, b):
    try: os.write(fd, bytes(b)); return True
    except OSError: return False

def init_ext(fd):
    # enable extension (unencrypted) + request its ID — do this ONCE per extension, not per tick
    # (re-writing F0/FB mid-stream re-inits the extension and wipes its stick/button state).
    w(fd, [0x16, 0x04, 0xa4, 0x00, 0xf0, 0x01, 0x55])
    w(fd, [0x16, 0x04, 0xa4, 0x00, 0xfb, 0x01, 0x00])
    w(fd, [0x17, 0x04, 0xa4, 0x00, 0xfa, 0x00, 0x06])

def set_mode(fd):
    # (re-)set reporting mode 0x32 (core + 8 ext) — safe to repeat; required after a 0x20 status
    w(fd, [0x12, 0x04, 0x32])

def affirm(fd):
    init_ext(fd); set_mode(fd)

def core(b1, b2):
    s = []
    for bit, n in [(0x01,"Left"),(0x02,"Right"),(0x04,"Down"),(0x08,"Up"),(0x10,"Plus")]:
        if b1 & bit: s.append(n)
    for bit, n in [(0x01,"Two"),(0x02,"One"),(0x04,"B"),(0x08,"A"),(0x10,"Minus"),(0x80,"Home")]:
        if b2 & bit: s.append(n)
    return s

def nunchuk(e):
    s = []
    if not (e[5] & 0x02): s.append("C")
    if not (e[5] & 0x01): s.append("Z")
    sx, sy = e[0], e[1]
    if sx < 100: s.append("stick<")
    elif sx > 156: s.append("stick>")
    if sy < 100: s.append("stickv")
    elif sy > 156: s.append("stick^")
    return s

def classic(e):
    s = []; b4, b5 = e[4], e[5]
    for bit, n in [(0x80,"D-right"),(0x40,"D-down"),(0x20,"L"),(0x10,"Minus"),(0x08,"Home"),(0x04,"Plus"),(0x02,"R")]:
        if not (b4 & bit): s.append(n)
    for bit, n in [(0x80,"ZL"),(0x40,"B"),(0x20,"Y"),(0x10,"A"),(0x08,"X"),(0x04,"ZR"),(0x02,"D-left"),(0x01,"D-up")]:
        if not (b5 & bit): s.append(n)
    lx, ly = e[0] & 0x3f, e[1] & 0x3f
    if lx < 24: s.append("Lstick<")
    elif lx > 40: s.append("Lstick>")
    if ly < 24: s.append("Lstickv")
    elif ly > 40: s.append("Lstick^")
    rx = ((e[0] >> 3) & 0x18) | ((e[1] >> 5) & 0x06) | ((e[2] >> 7) & 0x01); ry = e[2] & 0x1f
    if rx < 12: s.append("Rstick<")
    elif rx > 20: s.append("Rstick>")
    if ry < 12: s.append("Rstickv")
    elif ry > 20: s.append("Rstick^")
    return s

def main():
    open(LOG, "w").close()
    log("=== wii-monitor (mode-4 keep-alive) start ===")
    slots = {}     # fd -> dict(label,node,kind,last_frame,last_act)
    last_scan = 0.0
    stale_until = {}    # node -> monotonic time before which we won't retry (stale-link backoff)
    stale_logged = set()
    while True:
        now = time.monotonic()
        if now - last_scan >= 3.0:
            last_scan = now
            present_nodes = {s["node"] for s in slots.values()}
            for i, node in enumerate(db_nodes()):
                if node in present_nodes: continue
                if now < stale_until.get(node, 0):   # backoff: don't re-stall on a stale link
                    continue
                try: fd = os.open(node, os.O_RDWR)
                except OSError: continue
                try:
                    os.write(fd, bytes([0x15, 0x00]))   # presence test
                except BrokenPipeError:                 # EPIPE = empty slot (no remote) — silent
                    os.close(fd); continue
                except OSError as ex:                   # ETIMEDOUT = stale link — needs a re-sync
                    if ex.errno == errno.ETIMEDOUT and node not in stale_logged:
                        stale_logged.add(node)
                        log(f"slot{i+1} {node}: remote ASLEEP/stale (timeout) — press 1+2 to reconnect")
                    stale_until[node] = time.monotonic() + 5.0
                    os.close(fd); continue
                stale_logged.discard(node)
                affirm(fd)
                slots[fd] = {"label": f"slot{i+1}", "node": node, "kind": "?",
                             "last_frame": now, "last_act": None}
                log(f"slot{i+1} {node}: remote PRESENT — initialised")
        if not slots:
            time.sleep(0.4); continue
        ready, _, _ = select.select(list(slots), [], [], 0.3)
        now = time.monotonic()
        for fd in ready:
            st = slots[fd]
            try: buf = os.read(fd, 64)
            except OSError:
                log(f"{st['label']}: read error — dropping"); os.close(fd); slots.pop(fd); break
            if not buf or len(buf) < 3: continue
            st["last_frame"] = now
            rid = buf[0]
            if rid == 0x20:                                   # status — MUST re-set mode or stream dies
                ext = bool(buf[3] & 0x02) if len(buf) > 3 else False
                if ext != st.get("ext"):                      # only (re)init the extension on change
                    st["ext"] = ext
                    log(f"{st['label']}: status ext={'yes' if ext else 'no'} — re-init extension")
                    if ext: init_ext(fd)
                    else: st["kind"] = "none"
                set_mode(fd)
            elif rid == 0x21 and len(buf) >= 12:               # ext ID reply
                idb = (buf[10], buf[11])
                st["kind"] = {(0x00,0x00):"nunchuk",(0x01,0x01):"classic"}.get(idb, "none")
                log(f"{st['label']}: extension = {st['kind']}  (id ...{idb[0]:02x} {idb[1]:02x})")
            elif rid in (0x30, 0x31, 0x32):
                act = core(buf[1], buf[2])
                if rid == 0x32 and len(buf) >= 11:
                    e = buf[3:11]
                    if st["kind"] == "nunchuk": act += ["N:" + x for x in nunchuk(e)]
                    elif st["kind"] == "classic": act += ["C:" + x for x in classic(e)]
                key = tuple(act)
                if key != st["last_act"]:
                    st["last_act"] = key
                    if act: log(f"{st['label']}({st['kind']}): " + "  ".join(act))
        # keep-alive: revive any quiet stream
        for fd, st in list(slots.items()):
            if now - st["last_frame"] > KEEPALIVE:
                set_mode(fd); st["last_frame"] = now

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: log("=== stop ===")
    except Exception as ex: log(f"=== crashed: {ex!r} ===")
