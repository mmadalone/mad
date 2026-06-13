"""Wii Remote slot reader (Mayflash DolphinBar, mode 4) — raw hidraw.

Extracted verbatim from router-config-gui.py (MAD task #13 modularization).
Pure stdlib (no tkinter, no evdev): replicates Dolphin's WiimoteReal HID init
on a daemon thread and publishes immutable input snapshots, so it is safe to
import from any tool. Consumed by the Gamepad tester (lib/mad_gamepad_tester.py)
and the MAD GUI.
"""
from __future__ import annotations

import errno
import os
import select
import threading
import time

# ---------------------------------------------------------------------------
# Wii Remote slot reader (Mayflash DolphinBar, mode 4)
# ---------------------------------------------------------------------------
class WiiSlotReader:
    """Reads ONE DolphinBar mode-4 Wii Remote slot on a daemon thread, replicating Dolphin's
    WiimoteReal init so real input streams over raw hidraw on Linux. Publishes an immutable
    snapshot dict (swapped atomically under a lock) and holds NO Tk/App reference, so nothing in
    here can ever touch the GUI off-thread. Recipe + decode verified live — see deck-docs/wiimote.md
    and the standalone wii-monitor.py. Output reports are written WITHOUT the 0xa2 BT header."""
    RPT_STATUS = bytes([0x15, 0x00])                               # presence probe / status request
    EXT_F0     = bytes([0x16, 0x04, 0xa4, 0x00, 0xf0, 0x01, 0x55]) # enable extension (unencrypted)
    EXT_FB     = bytes([0x16, 0x04, 0xa4, 0x00, 0xfb, 0x01, 0x00])
    EXT_ID     = bytes([0x17, 0x04, 0xa4, 0x00, 0xfa, 0x00, 0x06]) # read 6-byte extension id
    SET_MODE   = bytes([0x12, 0x04, 0x32])                         # continuous core + 8 ext bytes
    KEEPALIVE = 1.5
    RECONNECT = 4.0

    def __init__(self, node):
        self.node = node
        self._stop = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._snap = self._blank("opening")
        self._fd = None
        self._ext_bit = None
        self._kind = "none"
        self._last_content = None
        self._thread = threading.Thread(target=self._run, name="wiislot", daemon=True)

    # ---- public, MAIN-THREAD only ----
    def start(self):
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._snap                         # immutable -> safe to read without copy

    def stop(self, timeout=0.6):
        self._stop.set()
        self._thread.join(timeout)

    def is_done(self):
        return self._done.is_set()

    # ---- snapshot publish (atomic) ----
    @staticmethod
    def _blank(status, present=False, kind="none"):
        return {"present": present, "status": status, "kind": kind,
                "core": frozenset(), "ext": frozenset(),
                "lstick": "rest", "rstick": "rest", "seq": 0}

    def _publish(self, **kw):
        with self._lock:
            nxt = dict(self._snap)
            nxt.update(kw)
            nxt["seq"] = self._snap["seq"] + 1
            self._snap = nxt                          # single atomic swap of a complete frame

    # ---- worker thread ----
    def _run(self):
        try:
            while not self._stop.is_set():
                if not self._open_probe():
                    self._stop.wait(self.RECONNECT)   # interruptible backoff
                    continue
                self._affirm_once()
                self._pump()                          # returns when stream ends / stop
                self._drop()
        finally:
            self._drop()
            self._done.set()

    def _open_probe(self):
        try:
            self._fd = os.open(self.node, os.O_RDWR)  # BLOCKING (O_NONBLOCK drops writes)
        except OSError:
            self._publish(**self._blank("empty"))
            return False
        try:
            os.write(self._fd, self.RPT_STATUS)       # EPIPE=empty, ETIMEDOUT=asleep, ok=live
        except BrokenPipeError:
            self._drop(); self._publish(**self._blank("empty")); return False
        except OSError as ex:
            self._drop()
            self._publish(**self._blank("asleep" if ex.errno == errno.ETIMEDOUT else "error"))
            return False
        self._ext_bit = None
        self._kind = "none"
        self._last_content = None
        self._publish(**self._blank("live", present=True))
        return True

    def _w(self, frame):
        try:
            os.write(self._fd, frame); return True
        except OSError:
            return False

    def _affirm_once(self):
        self._w(self.EXT_F0); self._w(self.EXT_FB); self._w(self.EXT_ID); self._w(self.SET_MODE)

    def _pump(self):
        last = time.monotonic()
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.3)
            except OSError:
                return
            if ready:
                try:
                    buf = os.read(self._fd, 64)
                except OSError:
                    self._on_lost(); return
                if not buf:
                    self._on_lost(); return
                last = time.monotonic()
                self._handle(buf)
            elif time.monotonic() - last > self.KEEPALIVE:
                if not self._w(self.SET_MODE):        # keep-alive: set-mode ONLY, never re-init
                    self._on_lost(); return
                last = time.monotonic()

    def _on_lost(self):
        # present True->False: clear everything in the SAME frame so the GUI can't keep sprites lit
        self._last_content = None
        self._publish(present=False, status="asleep", kind="none",
                      core=frozenset(), ext=frozenset(), lstick="rest", rstick="rest")

    def _handle(self, buf):
        if not buf:
            return
        rid = buf[0]
        if rid == 0x20:                               # status report
            if len(buf) > 3:
                bit = bool(buf[3] & 0x02)
                if bit != self._ext_bit:              # (re)init the extension ONLY on a real change
                    self._ext_bit = bit
                    if bit:
                        self._w(self.EXT_F0); self._w(self.EXT_FB); self._w(self.EXT_ID)
                    else:
                        self._kind = "none"
            self._w(self.SET_MODE)                     # ALWAYS re-set mode (else the stream stops)
        elif rid == 0x21 and len(buf) >= 12:          # extension id reply
            # The 6-byte ID is data[0..5] = buf[6..11]. Per WiiBrew (deck-docs/
            # wiimote.md): Nunchuk = 00 00 A4 20 00 00, Classic = 00 00 A4 20 01
            # 01 — every real extension carries the A4 20 signature at data[2],
            # [3] (buf[8],buf[9]) and is identified by data[4],[5] (buf[10],[11]).
            # A BARE remote's failed/empty read returns ALL ZEROS, whose last two
            # bytes (00,00) are indistinguishable from a Nunchuk by buf[10],[11]
            # alone — so it used to misdetect as Nunchuk and decode the all-zero
            # ext bytes as a full-corner stick (stuck "up-right" cursor drift).
            # Require the A4 20 signature first.
            if buf[8] == 0xA4 and buf[9] == 0x20:
                self._kind = {(0x00, 0x00): "nunchuk",
                              (0x01, 0x01): "classic"}.get((buf[10], buf[11]), "none")
            else:
                self._kind = "none"                   # no valid extension present
        elif rid in (0x30, 0x31, 0x32):
            self._decode_publish(buf)

    def _decode_publish(self, buf):
        if len(buf) < 3:
            return
        core = self._decode_core(buf[1], buf[2])
        kind = self._kind
        ext, ls, rs = frozenset(), "rest", "rest"
        if buf[0] == 0x32 and len(buf) >= 11:
            e = buf[3:11]
            if kind == "nunchuk":
                ext, ls = self._decode_nunchuk(e)
            elif kind == "classic":
                ext, ls, rs = self._decode_classic(e)
        content = (kind, core, ext, ls, rs)
        if content == self._last_content:
            return                                        # no change -> don't churn the GUI poll
        self._last_content = content
        self._publish(present=True, status="live", kind=kind,
                      core=core, ext=ext, lstick=ls, rstick=rs)

    def _drop(self):
        fd = self._fd
        self._fd = None
        if fd is not None:
            try: os.close(fd)
            except OSError: pass

    # ---- decoders (ported verbatim from wii-monitor.py, verified live) ----
    @staticmethod
    def _decode_core(b1, b2):
        s = set()
        if b1 & 0x01: s.add("dpadleft")
        if b1 & 0x02: s.add("dpadright")
        if b1 & 0x04: s.add("dpaddown")
        if b1 & 0x08: s.add("dpadup")
        if b1 & 0x10: s.add("plus")
        if b2 & 0x01: s.add("two")
        if b2 & 0x02: s.add("one")
        if b2 & 0x04: s.add("b")
        if b2 & 0x08: s.add("a")
        if b2 & 0x10: s.add("minus")
        if b2 & 0x80: s.add("home")
        return frozenset(s)

    @staticmethod
    def _dir8(x, y, cx, cy, dead):
        """8-way + rest token matching lstick_<token>. Higher Y = up (wiimote stick convention)."""
        dx = -1 if x < cx - dead else (1 if x > cx + dead else 0)
        dy = 1 if y > cy + dead else (-1 if y < cy - dead else 0)
        return {(0, 0): "rest",
                (0, 1): "up", (0, -1): "down", (-1, 0): "left", (1, 0): "right",
                (-1, 1): "ul", (1, 1): "ur", (-1, -1): "dl", (1, -1): "dr"}[(dx, dy)]

    @staticmethod
    def _decode_nunchuk(e):
        s = set()
        if not (e[5] & 0x02): s.add("c")              # active-low
        if not (e[5] & 0x01): s.add("z")
        return frozenset(s), WiiSlotReader._dir8(e[0], e[1], 128, 128, 28)

    @staticmethod
    def _decode_classic(e):
        b4, b5 = e[4], e[5]
        s = set()
        if not (b4 & 0x80): s.add("dpadright")
        if not (b4 & 0x40): s.add("dpaddown")
        if not (b4 & 0x20): s.add("l")
        if not (b4 & 0x10): s.add("minus")
        if not (b4 & 0x08): s.add("home")
        if not (b4 & 0x04): s.add("plus")
        if not (b4 & 0x02): s.add("r")
        if not (b5 & 0x80): s.add("zl")
        if not (b5 & 0x40): s.add("b")
        if not (b5 & 0x20): s.add("y")
        if not (b5 & 0x10): s.add("a")
        if not (b5 & 0x08): s.add("x")
        if not (b5 & 0x04): s.add("zr")
        if not (b5 & 0x02): s.add("dpadleft")
        if not (b5 & 0x01): s.add("dpadup")
        lx, ly = e[0] & 0x3f, e[1] & 0x3f
        rx = ((e[0] >> 3) & 0x18) | ((e[1] >> 5) & 0x06) | ((e[2] >> 7) & 0x01)
        ry = e[2] & 0x1f
        return (frozenset(s),
                WiiSlotReader._dir8(lx, ly, 32, 32, 8),
                WiiSlotReader._dir8(rx, ry, 16, 16, 4))
