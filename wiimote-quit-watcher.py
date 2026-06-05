#!/usr/bin/env python3
"""
Wii Remote "+ & -" quit-game watcher for Dolphin (REAL Wiimotes via DolphinBar).

Dolphin can't bind a hotkey to a real Wii Remote (its buttons are read by the
WiimoteReal backend and go straight to the game), so this reads the Wiimote's HID
button stream directly — co-reading the SAME /dev/hidraw* node Dolphin uses (Linux
delivers HID input reports to every reader; this does NOT steal input from Dolphin).
When BOTH + and - are held together for ~1s it quits Dolphin and ES-DE returns.

Started by the ES-DE game-start hook for real-Wiimote Wii games; stopped on game-end.
READS ONLY — never writes to the device. Robust to the Wiimote HID nodes appearing
and disappearing (remotes sleep / reconnect) via periodic re-enumeration.

Env overrides:
  WIIMOTE_WATCHER_DEBUG=1   log every +/- state change + report id (verification)
  WIIMOTE_WATCHER_QUITCMD   override the quit command
                            (default: pkill -TERM -f dolphin-emu)
  WIIMOTE_WATCHER_HOLD      hold seconds (default 1.0)
"""
import os, sys, glob, select, time, subprocess

WII_VID, WII_PID = 0x057E, 0x0306
HOLD_SEC   = float(os.environ.get("WIIMOTE_WATCHER_HOLD", "1.0"))
RESCAN_SEC = 2.0
LOG    = os.path.expanduser("~/Emulation/storage/sinden/logs/es-de-hooks.log")
DEBUG  = os.environ.get("WIIMOTE_WATCHER_DEBUG") == "1"
QUITCMD = os.environ.get("WIIMOTE_WATCHER_QUITCMD", "pkill -TERM -f dolphin-emu")

# Wii Remote core-button bits (wiibrew). In any data report (id >= 0x20) the two
# core-button bytes follow the report-id byte:  buf[1] & 0x10 = Plus (+)
#                                               buf[2] & 0x10 = Minus (-)
PLUS_BYTE, PLUS_BIT   = 1, 0x10
MINUS_BYTE, MINUS_BIT = 2, 0x10


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] wiimote-quit-watcher: {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


def find_wiimote_hidraws():
    out = []
    for ue in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            txt = open(ue).read()
        except OSError:
            continue
        for ln in txt.splitlines():
            if ln.startswith("HID_ID="):
                p = ln.split("=", 1)[1].split(":")
                if len(p) == 3:
                    try:
                        vid, pid = int(p[1], 16), int(p[2], 16)
                    except ValueError:
                        break
                    if vid == WII_VID and pid == WII_PID:
                        out.append("/dev/" + ue.split("/")[4])  # hidrawN
                break
    return sorted(set(out))


def main():
    log(f"start (hold +&- {HOLD_SEC}s -> '{QUITCMD}'; debug={DEBUG})")
    fds, held_since, last_dbg = {}, {}, {}
    last_scan = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_scan >= RESCAN_SEC:
                last_scan = now
                current = set(find_wiimote_hidraws())
                for path in current - set(fds):
                    try:
                        fds[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        held_since[path] = None
                        last_dbg[path] = None
                        log(f"opened {path} (co-read OK)")
                    except OSError as e:
                        if DEBUG:
                            log(f"open {path} failed: {e}")
                for path in set(fds) - current:
                    try:
                        os.close(fds[path])
                    except OSError:
                        pass
                    log(f"closed {path} (gone)")
                    fds.pop(path); held_since.pop(path, None); last_dbg.pop(path, None)

            if not fds:
                time.sleep(0.2)
                continue

            ready, _, _ = select.select(list(fds.values()), [], [], 0.2)
            for path, fd in list(fds.items()):
                if fd not in ready:
                    continue
                try:
                    buf = os.read(fd, 64)
                except OSError:
                    continue
                if not buf or len(buf) < 3 or buf[0] < 0x20:
                    continue
                plus  = bool(buf[PLUS_BYTE]  & PLUS_BIT)
                minus = bool(buf[MINUS_BYTE] & MINUS_BIT)
                if DEBUG and last_dbg.get(path) != (plus, minus):
                    last_dbg[path] = (plus, minus)
                    log(f"{path}: +={plus} -={minus} (report 0x{buf[0]:02x})")
                if plus and minus:
                    if held_since[path] is None:
                        held_since[path] = now
                    elif now - held_since[path] >= HOLD_SEC:
                        log(f"+&- held {HOLD_SEC}s on {path} -> quitting Dolphin")
                        subprocess.run(QUITCMD, shell=True)
                        log("quit command sent; exiting")
                        return
                else:
                    held_since[path] = None
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
