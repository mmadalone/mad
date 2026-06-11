"""MAD page mixin: the generic Gamepad tester ("Gamepad" sidebar page) —
evdev pads + DolphinBar mode-4 Wii Remotes with nunchuk/classic panels.

Extracted verbatim from router-config-gui.py (MAD task #13 modularization).
GamepadTesterMixin is NOT standalone — it must be mixed into MAD's App class
TOGETHER WITH XArcadeTesterMixin (lib/mad_xarcade_tester.py): _gp_pads/
gamepad()/_gp_test_page call self._xa_xport/_xa_port_of to drop the X-Arcade
from the picker (one-way dependency, GP → XA). Expects the host to provide:
self.root / self.c / self.font / self._ui_q / self.sections, the App helpers
_title/_scroll/_lbl/_btn/_textwrap/_tile_grid/_grid_cols/_mad_art_dirs/
show_section/_replace, and App._clear()/quit() calling self._gp_cleanup()
(internally getattr-guarded, so it is safe before the page has ever run).
"""
from __future__ import annotations

import os
import select
import threading
import time
from pathlib import Path

import tkinter as tk

from .wii_slot_reader import WiiSlotReader


class GamepadTesterMixin:
    # ════════════════ Generic "Gamepad" tester (self-contained _gp_*) ════════════════
    GP_PROFILES = [
        (0x2dc8, 0x2810, "fc30",       "8BitDo FC30",   "8bitdofc30-tester",           "8bitdofc30.png"),
        (0x2dc8, 0x3820, "n30",        "8BitDo N30 Pro","8bitdon30-tester",            "8bitdon30pro.png"),
        (0x054c, 0x0ce6, "dualsense",  "DualSense",     "dualsense-tester",            "dualsense.png"),
        (0x054c, 0x09cc, "dualshock4", "DualShock 4",   "dualshock4-tester",           "dualshock.png"),
        (0x057e, 0x0330, "wiiupro",    "Wii U Pro",     "wiiupro-tester",              "wiiupro.png"),
        (0x045e, 0x02a1, "xbox360",    "Xbox 360",      "xbox360-tester",              "xbox360.png"),
        (0x28de, 0x1205, "steamdeck",  "Steam Deck",    "steamdeck-controller-tester", "steamdeck.png"),
        (0x057e, 0x0306, "wiimote",    "Wii Remote",    "wiimote-tester",              "wiimote.png"),
    ]

    def _gp_profile_for(self, vid, pid, name):
        for v, p, key, label, d, icon in self.GP_PROFILES:
            if (vid, pid) == (v, p):
                return {"key": key, "label": label, "dir": d, "icon": icon}
        n = (name or "").lower()
        for needle, key in (("8bitdo", "n30"), ("dualsense", "dualsense"),
                            ("wireless controller", "dualshock4"), ("wii u pro", "wiiupro"),
                            ("wii remote pro", "wiiupro"), ("xbox", "xbox360"),
                            ("wii remote", "wiimote"), ("wiimote", "wiimote")):
            if needle in n:
                for v, p, k, label, d, icon in self.GP_PROFILES:
                    if k == key:
                        return {"key": k, "label": label, "dir": d, "icon": icon}
        return None

    def _gp_pads(self):
        """Connected, supported pads — read from the CACHED device walk (lib.devices), never by
        opening nodes here: closing an evdev fd costs ~37 ms on this kernel, so a 33-node
        open/close sweep froze the UI for >1 s (the 2026-06-11 "MAD lags everywhere" bug).
        Excludes Sinden, the Steam virtual pad, and the X-Arcade."""
        from lib.devices import enumerate_devices
        xport = self._xa_xport()
        out, seen = [], set()
        for d in enumerate_devices():
            vid, pid = d.vid, d.pid
            # Skip Sinden (16c0) + ALL Steam/Deck nodes (28de): the Deck in lizard mode is a
            # keyboard+mouse, not a gamepad, so it isn't a testable pad here.
            skip = (vid == 0x16c0) or (vid == 0x28de)
            if vid == 0x045e and pid == 0x02a1 and xport and self._xa_port_of(d.phys) == xport:
                skip = True
            prof = self._gp_profile_for(vid, pid, d.name) if (d.has_face_btn and not skip) else None
            if prof:
                # dedupe a device's interfaces: BT MAC if present, else USB port (one Xbox receiver =
                # 2 interfaces, same port), else per-node (BT Wii-U-Pros share empty uniq+port).
                key = d.uniq or self._xa_port_of(d.phys) or d.path
                if key not in seen:
                    seen.add(key)
                    out.append({"path": d.path, "vid": vid, "pid": pid, "name": d.name,
                                "uniq": d.uniq or "", "phys": d.phys or "", "prof": prof})
        # Real Wii Remotes on a Mayflash DolphinBar (mode 4) are raw hidraw, NOT evdev. They're
        # found by the off-thread _gp_scan_wii probe and cached in _gp_wii_awake (no I/O here).
        wprof = self._gp_profile_for(0x057e, 0x0306, "Wii Remote")
        if wprof:
            for slot, node, kind in (getattr(self, "_gp_wii_awake", None) or []):
                acc = {"nunchuk": " + Nunchuk", "classic": " + Classic"}.get(kind, "")
                out.append({"path": node, "node": node, "transport": "hidraw", "slot": slot,
                            "vid": 0x057e, "pid": 0x0306, "ext": kind,
                            "name": f"Wii Remote{acc} (DolphinBar {slot})",
                            "uniq": "", "phys": node, "prof": wprof})
        return out

    def _gp_db_slots(self):
        """[(slot_number, node), ...] for the DolphinBar's fixed hidraw slots, ordered by the
        bar's input index (stable per slot, so two remotes don't swap). [] if no bar."""
        import os, re
        try:
            from lib import devices as _dev
            nodes = _dev._dolphinbar_slot_nodes()
        except Exception:
            nodes = []
        ranked = []
        for node in nodes:
            base = os.path.basename(node); idx = 99
            try:
                for ln in open(f"/sys/class/hidraw/{base}/device/uevent"):
                    if ln.startswith("HID_PHYS="):
                        m = re.search(r"input(\d+)", ln)
                        if m: idx = int(m.group(1))
                        break
            except OSError:
                pass
            ranked.append((idx, node))
        ranked.sort()
        return [(i + 1, node) for i, (_idx, node) in enumerate(ranked)]

    def _gp_scan_wii(self):
        """Probe the DolphinBar slots for LIVE remotes off the Tk thread (the presence write can
        block multiple seconds on a stale link). Result -> self._gp_wii_awake via the UI queue."""
        if getattr(self, "_gp_wii_scanning", False):
            self._gp_wii_rescan = True                   # a refresh landed mid-scan; redo once it finishes
            return
        slots = self._gp_db_slots()
        if not slots:
            self._gp_wii_awake = []
            return
        self._gp_wii_scanning = True
        self._gp_wii_rescan = False
        gen = getattr(self, "_gp_db_gen", 0)
        def worker():
            awake = []
            for slot, node in slots:
                try:
                    fd = os.open(node, os.O_RDWR)
                except OSError:
                    continue
                try:
                    os.write(fd, WiiSlotReader.RPT_STATUS)   # ok=live; EPIPE empty / ETIMEDOUT asleep raise
                    awake.append((slot, node, self._gp_probe_kind(fd)))
                except OSError:
                    pass
                finally:
                    try: os.close(fd)
                    except OSError: pass
            self._ui_q.put(lambda: self._gp_scan_wii_done(gen, awake))
        threading.Thread(target=worker, name="wiiscan", daemon=True).start()

    @staticmethod
    def _gp_probe_kind(fd):
        """Identify the attached extension during the picker scan (worker thread, <=0.7s).
        Returns "nunchuk"/"classic"/"" — same proven init sequence the WiiSlotReader uses; the
        remote is restored to quiet non-continuous mode before the fd closes."""
        try:
            os.write(fd, WiiSlotReader.EXT_F0); os.write(fd, WiiSlotReader.EXT_FB)
            os.write(fd, WiiSlotReader.EXT_ID); os.write(fd, WiiSlotReader.SET_MODE)
        except OSError:
            return ""
        kind = ""
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.7:
            try:
                r, _, _ = select.select([fd], [], [], 0.15)
            except OSError:
                break
            if not r:
                continue
            try:
                buf = os.read(fd, 64)
            except OSError:
                break
            if not buf:
                break
            if buf[0] == 0x21 and len(buf) >= 12:
                kind = {(0x00, 0x00): "nunchuk", (0x01, 0x01): "classic"}.get((buf[10], buf[11]), "")
                break
            if buf[0] == 0x20 and len(buf) > 3 and not (buf[3] & 0x02):
                break                                   # no extension attached — don't wait for an id
        try:
            os.write(fd, bytes([0x12, 0x00, 0x30]))     # back to quiet non-continuous core reporting
        except OSError:
            pass
        return kind

    def _gp_scan_wii_done(self, gen, awake):
        self._gp_wii_scanning = False
        if getattr(self, "_gp_wii_rescan", False):       # a Refresh superseded this scan -> redo once
            self._gp_wii_rescan = False
            if getattr(self, "_gp_on_picker", False) and getattr(self, "_gp_wii_awake", None) is None:
                self._gp_scan_wii()                      # scanning flag is now False, so this launches
                return
        if gen != getattr(self, "_gp_db_gen", 0):
            return                                       # a newer page superseded this one
        changed = (awake != getattr(self, "_gp_wii_awake", None))
        self._gp_wii_awake = awake
        if changed and getattr(self, "_gp_on_picker", False):
            # Repaint IN PLACE (won't re-scan: cache now set). NOT _gp_show/show_section —
            # that is a full section re-switch (sound, stack reset) whose teardown also
            # destroyed the focused widget ~1s after page entry, leaving the D-pad to
            # recover onto the sidebar and yank the user to another section.
            self._replace(self.gamepad)

    def _gp_wii_refresh(self):
        self._gp_wii_awake = None                        # force a fresh scan on the rebuild
        self._gp_show()

    def _gp_reap_readers(self):
        self._gp_dead_readers = [r for r in getattr(self, "_gp_dead_readers", []) if not r.is_done()]

    def _gp_show(self):
        self.show_section(next(i for i, (n, _) in enumerate(self.sections) if n == "Gamepads"))

    def _gp_open(self, o, sid=None):
        self._gp_sel = o
        self._gp_last_sid = sid                  # picker tile index, to restore focus on back
        self._gp_show()

    def _gp_back(self):
        self._gp_cleanup()
        self._gp_sel = None
        self._gp_focus_sid = getattr(self, "_gp_last_sid", None)
        self._gp_show()

    def _gp_cleanup(self):
        for att in ("_gp_after",):
            if getattr(self, att, None):
                try: self.root.after_cancel(getattr(self, att))
                except Exception: pass
                setattr(self, att, None)
        for d in getattr(self, "_gp_devs", []) or []:
            try: d.ungrab()
            except Exception: pass
            try: d.close()
            except Exception: pass
        self._gp_devs = []
        self._gp_transport = None
        # Stop the Wii reader without freezing the GUI: short join; if it's still blocked in a
        # multi-second ETIMEDOUT write, hand it to _gp_dead_readers (the daemon closes its own fd
        # in finally) and refuse to reopen that node until it's done — see _gp_start_hid.
        r = getattr(self, "_gp_reader", None)
        if r is not None:
            r.stop(timeout=0.6)
            if not r.is_done():
                self._gp_dead_readers = getattr(self, "_gp_dead_readers", []) + [r]
            self._gp_reader = None
        self._gp_reap_readers()
        self._gp_db_gen = getattr(self, "_gp_db_gen", 0) + 1     # invalidate any in-flight scan callback
        self._gp_on_picker = False

    def gamepad(self):
        self._gp_cleanup()
        if getattr(self, "_gp_sel", None):
            return self._gp_test_page()
        self._gp_on_picker = True
        self._title("Gamepad tester")
        inner = self._scroll()
        self._lbl(inner, "Pick a connected controller, then press its controls and watch them light up. "
                  "Two identical pads (e.g. FC30 P1/P2) are told apart by their Bluetooth address. Real "
                  "Wii Remotes on a DolphinBar (mode 4) show up per live slot; the X-Arcade has its own page.",
                  role="text", size=13, anchor="w",
                  pady=(0, 12), wraplength=self._textwrap(), justify="left")
        self._btn(inner, "↻ Refresh list", self._gp_wii_refresh).pack(anchor="w", pady=(0, 8))
        # Kick off the off-thread DolphinBar probe the first time (cache is None); it repaints when done.
        scanning = False
        if getattr(self, "_gp_wii_awake", None) is None and self._gp_db_slots():
            self._gp_scan_wii(); scanning = True
        pads = self._gp_pads()
        if not pads:
            msg = ("Detecting Wii Remotes on the DolphinBar…" if scanning else
                   "No supported controllers detected — wake a pad (press a button; BT pads sleep when "
                   "idle; Wii Remotes need a 1+2 re-sync), then hit ↻ Refresh.")
            self._lbl(inner, msg, role="dim", size=12, anchor="w",
                      wraplength=self._textwrap(), justify="left")
            return
        if scanning:
            self._lbl(inner, "Detecting Wii Remotes on the DolphinBar…", role="dim", size=12,
                      anchor="w", pady=(0, 8), wraplength=self._textwrap(), justify="left")
        items = []
        for i, o in enumerate(pads):
            if o.get("transport") == "hidraw":
                idtail = f"slot {o['slot']}"
            else:
                idtail = o["uniq"][-8:] if o["uniq"] else (self._xa_port_of(o["phys"]) or o["path"].split("/")[-1])
            items.append((str(i), o["name"], idtail, [f"icons/{o['prof']['icon']}"]))
        tiles = self._tile_grid(inner, items, lambda sid: self._gp_open(pads[int(sid)], int(sid)),
                                cols=self._grid_cols())
        fsid = getattr(self, "_gp_focus_sid", None)          # back from a test page → refocus that tile
        self._gp_focus_sid = None
        if fsid is not None and tiles and 0 <= fsid < len(tiles):
            t = tiles[fsid]
            t.focus_set()
            self.root.after(60, lambda: t.winfo_exists() and t.focus_set())

    # ---- per-pad test page ----
    @staticmethod
    def _gp_png_w(path):
        import struct
        try:
            with open(path, "rb") as f:
                f.read(16); return struct.unpack(">II", f.read(8))[0]
        except Exception:
            return 0

    @staticmethod
    def _gp_png_h(path):
        import struct
        try:
            with open(path, "rb") as f:
                f.read(16); return struct.unpack(">II", f.read(8))[1]
        except Exception:
            return 0

    def _gp_base_path(self, sprite_dir):
        for ad in self._mad_art_dirs():
            cand = Path(ad) / "icons" / sprite_dir / "base.png"
            if cand.is_file():
                return cand
        return None

    def _gp_fit_factor(self, w, h, avail_w, avail_h, floor=1):
        """Smallest integer PhotoImage.subsample factor that fits w x h into the viewport box
        (so a tall portrait base — e.g. the 842x3767 wiimote — doesn't overflow + scroll).
        `floor` keeps the existing per-pad width scaling as a minimum so pads that already fit
        are unchanged (the fit only ever shrinks an oversized base, never enlarges)."""
        import math
        if not w or not h:
            return int(floor)
        return max(int(floor), math.ceil(w / max(1, avail_w)), math.ceil(h / max(1, avail_h)))

    def _gp_avail_box(self, x_used=0):
        """(avail_w, avail_h) for a sprite panel on the test page (Deck ~1280x800 fullscreen).
        Height capped to ~60% so the canvas + button bars never need vertical scrolling."""
        aw = max(300, self.root.winfo_screenwidth() - 280 - int(x_used))
        ah = max(360, int(self.root.winfo_screenheight() * 0.60))
        return aw, ah

    def _gp_load_into(self, sprite_dir, dest, factor=None):
        """Load icons/<sprite_dir>/*.png into a named sprite dict attr (self.<dest>); return
        (base, back, factor). `factor` overrides the default width-based subsample (used to fit a
        tall base into the viewport). Used for the core pad (_gp_sprites) and an accessory."""
        d = None
        for ad in self._mad_art_dirs():
            cand = Path(ad) / "icons" / sprite_dir
            if cand.is_dir():
                d = cand; break
        sprites = {}
        setattr(self, dest, sprites)
        if d is None:
            return None, None, 1
        basep = d / "base.png"
        if factor is None:
            factor = max(1, round((self._gp_png_w(basep) or 1500) / 560))
        factor = max(1, int(factor))
        def load(p):
            try:
                img = tk.PhotoImage(file=str(p))
                return img.subsample(factor, factor) if factor > 1 else img
            except Exception:
                return None
        base = load(basep)
        back = load(d / "back.png") if (d / "back.png").is_file() else None
        for f in sorted(d.iterdir()):
            if f.suffix == ".png" and f.stem not in ("base", "back"):
                img = load(f)
                if img is not None:
                    sprites[f.stem] = img
        return base, back, factor

    def _gp_load(self, sprite_dir, factor=None):
        return self._gp_load_into(sprite_dir, "_gp_sprites", factor=factor)

    def _gp_p2_file(self):
        return Path.home() / "Emulation" / "storage" / "control-panel" / "gp-p2-units.json"

    def _gp_p2_overrides(self):
        import json
        try:
            p = self._gp_p2_file()
            if p.is_file():
                d = json.loads(p.read_text())
                if isinstance(d, dict):
                    return {k: bool(v) for k, v in d.items()}
        except Exception:
            pass
        return {}

    @staticmethod
    def _gp_name_is_p2(name):
        """A pad whose name says it's player 2 (e.g. '… FC30 II', '… P2') is auto-assigned P2."""
        low = (name or "").lower()
        toks = low.replace("#", " ").split()
        return any(t in ("p2", "ii", "2", "player2") for t in toks) or "player 2" in low

    def _gp_is_p2(self):
        o = getattr(self, "_gp_sel", None)
        if not o:
            return False
        auto = self._gp_name_is_p2(o.get("name", ""))
        return self._gp_p2_overrides().get(o.get("uniq", ""), auto)   # manual toggle overrides the name

    def _gp_toggle_p2(self):
        import json
        o = getattr(self, "_gp_sel", None)
        if not (o and o.get("uniq")):
            return
        ov = self._gp_p2_overrides()
        ov[o["uniq"]] = not self._gp_is_p2()
        try:
            p = self._gp_p2_file(); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(ov))
        except Exception:
            pass
        self._gp_show()                          # rebuild → the P2 badge appears/disappears

    def _gp_spots(self):
        stems = set(getattr(self, "_gp_sprites", {}).keys())
        # p2indicator is a P2 badge, not a calibratable control — shown only for a P2-marked unit
        spots = [s for s in sorted(stems) if not s.startswith("lstick_") and s != "p2indicator"]
        if "p2indicator" in stems and self._gp_is_p2():
            spots.append("p2indicator")
        if any(s.startswith("lstick_") for s in stems):
            spots += ["lstick", "rstick"]
        return spots

    def _gp_default_spot(self, code):
        """Default gamepad-code → spot (adapts to the pad's face/shoulder naming); cal overrides it."""
        from evdev import ecodes as e
        stems = set(getattr(self, "_gp_sprites", {}).keys())
        def pk(*c):
            return next((x for x in c if x in stems), None)
        sony = "circle" in stems or "triangle" in stems
        if sony:
            m = {e.BTN_SOUTH: pk("x"), e.BTN_EAST: pk("circle"), e.BTN_NORTH: pk("triangle"),
                 e.BTN_WEST: pk("square")}
        else:
            # 8BitDo / Wii U / xpad report by LABEL (the X button = BTN_X = 0x133, NOT position), so
            # map the A/B/X/Y aliases straight to a/b/x/y sprites — calibration fixes any pad that differs.
            m = {e.BTN_A: pk("a"), e.BTN_B: pk("b"), e.BTN_X: pk("x"), e.BTN_Y: pk("y")}
        m.update({e.BTN_TL: pk("l1", "l"), e.BTN_TR: pk("r1", "r"),
                  e.BTN_TL2: pk("l2", "zl"), e.BTN_TR2: pk("r2", "zr"),
                  e.BTN_SELECT: pk("select", "minus", "back"), e.BTN_START: pk("start", "plus"),
                  e.BTN_MODE: pk("guide", "home", "steam"),
                  e.BTN_THUMBL: pk("l3"), e.BTN_THUMBR: pk("r3"),
                  e.BTN_DPAD_UP: pk("dpadup"), e.BTN_DPAD_DOWN: pk("dpaddown"),
                  e.BTN_DPAD_LEFT: pk("dpadleft"), e.BTN_DPAD_RIGHT: pk("dpadright")})
        return m.get(code)

    def _gp_pos_file(self, key=None):
        k = key or self._gp_sel["prof"]["key"]
        return (Path.home() / "Emulation" / "storage" / "control-panel"
                / f"gp-{k}-positions.json")

    def _gp_load_positions(self):
        import json
        try:
            p = self._gp_pos_file()
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _gp_baked_positions(self, key):
        """Repo-shipped default sprite layout (data/gp-defaults/, same per-key file format as
        the control-panel saves) — used when the user hasn't drag-aligned + 💾 Saved their own;
        a local control-panel file always wins. `key` is a profile key or an accessory kind."""
        import json
        try:
            p = Path(__file__).resolve().parent.parent / "data" / "gp-defaults" / f"gp-{key}-positions.json"
            if p.is_file():
                d = json.loads(p.read_text())
                if isinstance(d, dict):
                    return {k: v for k, v in d.items() if isinstance(v, list) and len(v) == 2}
        except Exception:
            pass
        return {}

    def _gp_default_pos(self, spots):
        pos = {}
        n = max(1, len(spots))
        cols = int(n ** 0.5) + 1
        rows = (n + cols - 1) // cols
        cw = getattr(self, "_gp_core_w", self._gp_cw)
        xmax = (self._gp_base.width() / cw) if getattr(self, "_gp_back_img", None) else 0.96
        for i, k in enumerate(spots):
            r, c = divmod(i, cols)
            pos[k] = [0.04 + (xmax - 0.08) * (c / max(1, cols - 1)),
                      0.10 + 0.80 * (r / max(1, rows - 1))]
        return pos

    def _gp_make_sprites(self):
        cv = self._gp_canvas
        self._gp_items = {}
        self._gp_of = {}
        self._gp_cal_hl = None
        if cv is None:
            return
        spr = self._gp_sprites
        spots = self._gp_spots()
        saved = self._gp_load_positions()
        baked = self._gp_baked_positions(self._gp_sel["prof"]["key"])
        defpos = self._gp_default_pos(spots)
        cw, ch = self._gp_cw, self._gp_ch
        for k in spots:
            nx, ny = saved.get(k) or baked.get(k) or defpos.get(k, [0.5, 0.5])
            img = spr.get("lstick_rest") if k in ("lstick", "rstick") else spr.get(k)
            if img is None:
                continue
            st = "normal" if k in ("lstick", "rstick", "p2indicator") else "hidden"
            oid = cv.create_image(nx * cw, ny * ch, image=img, anchor="center", state=st, tags=("gpspr",))
            self._gp_items[k] = oid
            self._gp_of[oid] = k
        cv.tag_bind("gpspr", "<ButtonPress-1>", self._gp_drag_start)
        cv.tag_bind("gpspr", "<B1-Motion>", self._gp_drag_move)
        cv.tag_bind("gpspr", "<ButtonRelease-1>", self._gp_drag_end)

    def _gp_test_page(self):
        o = self._gp_sel
        prof = o["prof"]
        self._title(f"{prof['label']} tester")
        inner = self._scroll()
        bar0 = tk.Frame(inner, bg=self.c["bg"]); bar0.pack(anchor="w", pady=(0, 6))
        self._btn(bar0, "← Pads", self._gp_back).pack(side="left", padx=(0, 10))
        idtail = (f"slot {o['slot']}" if o.get("transport") == "hidraw"
                  else (o["uniq"][-8:] if o["uniq"] else (self._xa_port_of(o["phys"]) or "")))
        self._lbl(bar0, f"{o['name']}   ·   {idtail}", role="dim", size=12, anchor="w").pack(side="left")
        bp = self._gp_base_path(prof["dir"])            # fit a tall base into the viewport (no scroll)
        fit = None
        if bp is not None:
            bw, bh = self._gp_png_w(bp), self._gp_png_h(bp)
            backw = self._gp_png_w(bp.parent / "back.png") if (bp.parent / "back.png").is_file() else 0
            reserve = 1000 if o.get("transport") == "hidraw" else 0   # room for a Nunchuk/Classic panel
            aw, ah = self._gp_avail_box()
            fit = self._gp_fit_factor(bw + backw + reserve, bh, aw, ah, floor=max(1, round(bw / 560)))
        base, back, factor = self._gp_load(prof["dir"], factor=fit)
        self._gp_canvas = None
        if base is None:
            self._lbl(inner, f"(sprites not found — expected icons/{prof['dir']}/base.png)",
                      role="dim", size=12, anchor="w")
            return
        self._gp_base = base
        gap = 18
        cw = base.width() + (gap + back.width() if back is not None else 0)
        ch = max(base.height(), back.height() if back is not None else 0)
        cv = tk.Canvas(inner, width=cw, height=ch, bg=self.c["bg"], highlightthickness=0)
        cv.pack(anchor="w", pady=(0, 8))
        cv.create_image(0, 0, anchor="nw", image=base)
        self._gp_back_img = None
        if back is not None:
            self._gp_back_img = back
            cv.create_image(base.width() + gap, 0, anchor="nw", image=back)
        self._gp_canvas, self._gp_cw, self._gp_ch = cv, cw, ch
        self._gp_core_w, self._gp_core_h = cw, ch           # core region (fixed; canvas may grow for an accessory)
        self._gp_ext_x0 = cw + gap                          # where an accessory panel begins
        self._gp_ext_kind = "none"; self._gp_ext_oids = []
        self._gp_ext_sprites = {}; self._gp_ext_baseimg = None
        self._gp_ext_bw = self._gp_ext_bh = 1
        self._gp_ext_cache = {}                          # kind -> (base, sprites); held for page lifetime
        self._gp_make_sprites()
        if o.get("transport") == "hidraw" and o.get("ext") in ("nunchuk", "classic"):
            self._gp_ext_build(o["ext"])                 # show the scanned accessory right away
                                                         # (the live test re-syncs if it was swapped)
        self._gp_live = tk.Label(inner, text="—", bg=self.c["bg"], fg=self.c["text"],
                                 font=self.font(13, mono=True), anchor="w", justify="left",
                                 wraplength=self._textwrap())
        self._gp_live.pack(anchor="w", pady=(0, 6))
        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(2, 4))
        sb = self._btn(bar, "▶ Start test", self._gp_start); sb.pack(side="left", padx=(0, 10))
        self._btn(bar, "■ Stop", self._gp_stop).pack(side="left", padx=(0, 10))
        if o.get("transport") != "hidraw":      # Wii Remotes have a fixed bitmap — no calibration
            self._btn(bar, "◉ Calibrate", self._gp_calibrate).pack(side="left", padx=(0, 10))
        bar2 = tk.Frame(inner, bg=self.c["bg"]); bar2.pack(anchor="w", pady=(0, 12))
        self._btn(bar2, "✛ Edit positions", self._gp_edit).pack(side="left", padx=(0, 10))
        self._btn(bar2, "\U0001f4be Save", self._gp_save_positions).pack(side="left", padx=(0, 10))
        self._btn(bar2, "↺ Reset", self._gp_reset_positions).pack(side="left", padx=(0, 10))
        if "p2indicator" in getattr(self, "_gp_sprites", {}):
            self._btn(bar2, "P2 ✓" if self._gp_is_p2() else "Mark P2",
                      self._gp_toggle_p2).pack(side="left", padx=(0, 10))
        self._gp_status_lbl = self._lbl(inner, "", role="dim", size=12, anchor="w",
                                        wraplength=self._textwrap(), justify="left")
        self._gp_devs = []
        self._gp_after = None
        self._gp_transport = None
        self._gp_reader = None
        self._gp_wii_core = frozenset(); self._gp_wii_ext = frozenset()
        self._gp_wii_seq = -1; self._gp_wii_status = None
        self._gp_show_flag = False
        self._gp_cal = False
        self._gp_cal_sel = None
        self._gp_quit_held = set(); self._gp_quit_t0 = None
        sb.focus_set()                                   # land on ▶ Start test, not ← Pads
        self.root.after(60, lambda: sb.winfo_exists() and sb.focus_set())
        if prof["key"] == "steamdeck":
            self._gp_status("Heads-up: testing the Deck pad grabs it, so you can't navigate while testing "
                            "— hold Start (6 s), use the touchscreen ■ Stop, or it auto-stops "
                            "after ~20 s idle.")
        elif o.get("transport") == "hidraw":
            self._gp_status("Real Wii Remote via the DolphinBar (mode 4). ▶ Start, then press its buttons. "
                            "A Nunchuk or Classic Controller is detected automatically and lights up beside it.")

    # ---- show / drag / edit ----
    def _gp_show_sprite(self, key, on):
        cv = getattr(self, "_gp_canvas", None)
        oid = getattr(self, "_gp_items", {}).get(key)
        if cv is not None and oid is not None:
            try: cv.itemconfigure(oid, state=("normal" if on else "hidden"))
            except Exception: pass

    def _gp_set_visible(self, on):
        cv = getattr(self, "_gp_canvas", None)
        if cv is None:
            return
        keep = ("lstick", "rstick", "p2indicator", "x:lstick", "x:rstick")
        for k, oid in getattr(self, "_gp_items", {}).items():
            st = "normal" if (on or k in keep) else "hidden"
            try: cv.itemconfigure(oid, state=st)
            except Exception: pass

    def _gp_center(self, oid):
        c = self._gp_canvas.coords(oid)
        return (c[0], c[1]) if len(c) < 4 else ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)

    def _gp_drag_start(self, ev):
        cur = self._gp_canvas.find_withtag("current")
        oid = cur[0] if cur else None
        if getattr(self, "_gp_cal", False):
            k = self._gp_of.get(oid)
            if k:
                self._gp_cal_select(k)
            self._gp_drag = None
            return
        self._gp_drag = oid if getattr(self, "_gp_show_flag", False) else None

    def _gp_drag_move(self, ev):
        if not getattr(self, "_gp_drag", None):
            return
        cv = self._gp_canvas
        x, y = cv.canvasx(ev.x), cv.canvasy(ev.y)
        cx, cy = self._gp_center(self._gp_drag)
        cv.move(self._gp_drag, x - cx, y - cy)

    def _gp_drag_end(self, ev):
        self._gp_drag = None

    def _gp_edit(self):
        self._gp_show_flag = not getattr(self, "_gp_show_flag", False)
        self._gp_set_visible(self._gp_show_flag)
        self._gp_status("Edit ON — drag each sprite onto its control (use the pad's stick as a pointer, or "
                        "the Deck trackpad/touchscreen), then 💾 Save." if self._gp_show_flag
                        else "Edit off.")

    def _gp_save_positions(self):
        import json
        cv = getattr(self, "_gp_canvas", None)
        if cv is None or not getattr(self, "_gp_items", None):
            return
        cw, ch = self._gp_core_w, self._gp_core_h          # core normalizes to the fixed core region
        x0 = getattr(self, "_gp_ext_x0", cw)
        bw = getattr(self, "_gp_ext_bw", 1) or 1
        bh = getattr(self, "_gp_ext_bh", 1) or 1
        core, ext = {}, {}
        for k, o in self._gp_items.items():
            cx, cy = self._gp_center(o)
            if k.startswith("x:"):                          # accessory normalizes to its own panel box
                ext[k[2:]] = [round((cx - x0) / bw, 4), round(cy / bh, 4)]
            else:
                core[k] = [round(cx / cw, 4), round(cy / ch, 4)]
        n = 0
        try:
            if core:
                p = self._gp_pos_file(); p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(core, indent=2)); n += len(core)
            kind = getattr(self, "_gp_ext_kind", "none")
            if ext and kind in ("nunchuk", "classic"):
                p = self._gp_pos_file(kind); p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(ext, indent=2)); n += len(ext)
            self._gp_status(f"Saved {n} positions.")
        except Exception as ex:
            self._gp_status(f"Couldn't save: {ex}", warn=True)

    def _gp_reset_positions(self):
        cv = getattr(self, "_gp_canvas", None)
        if cv is None:
            return
        cw, ch = self._gp_core_w, self._gp_core_h
        core_keys = [k for k in self._gp_items if not k.startswith("x:")]
        defpos = self._gp_default_pos(core_keys)
        defpos.update(self._gp_baked_positions(self._gp_sel["prof"]["key"]))
        for k in core_keys:
            nx, ny = defpos.get(k, [0.5, 0.5])
            cv.coords(self._gp_items[k], nx * cw, ny * ch)
        xkeys = [k for k in self._gp_items if k.startswith("x:")]
        if xkeys:
            x0 = getattr(self, "_gp_ext_x0", cw)
            bw = getattr(self, "_gp_ext_bw", 1) or 1
            bh = getattr(self, "_gp_ext_bh", 1) or 1
            boxdef = self._gp_default_pos_box([k[2:] for k in xkeys], bw, bh)
            kind = getattr(self, "_gp_ext_kind", "none")
            if kind in ("nunchuk", "classic"):
                boxdef.update(self._gp_baked_positions(kind))
            for k in xkeys:
                nx, ny = boxdef.get(k[2:], [0.5, 0.5])
                cv.coords(self._gp_items[k], x0 + nx * bw, ny * bh)
        self._gp_status("Reset to default layout — drag to fine-tune, then 💾 Save.")

    # ---- accessory panel (Nunchuk / Classic, drawn beside the wiimote; sprites keyed "x:<stem>") ----
    def _gp_default_pos_box(self, spots, bw, bh):
        """Default grid layout normalized to the accessory panel's own [0..1] box."""
        pos = {}
        n = max(1, len(spots))
        cols = int(n ** 0.5) + 1
        for i, k in enumerate(spots):
            r, c = divmod(i, cols)
            rows = (n + cols - 1) // cols
            pos[k] = [0.12 + 0.76 * (c / max(1, cols - 1)),
                      0.12 + 0.76 * (r / max(1, rows - 1))]
        return pos

    def _gp_ext_load_positions(self, kind):
        import json
        try:
            p = self._gp_pos_file(kind)
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    # accessory buttons the decoders actually emit — whitelist so a stray PNG can't become a phantom
    _GP_EXT_BTNS = {
        "nunchuk": frozenset({"c", "z"}),
        "classic": frozenset({"a", "b", "x", "y", "dpadup", "dpaddown", "dpadleft", "dpadright",
                              "l", "r", "zl", "zr", "plus", "minus", "home"}),
    }

    def _gp_ext_build(self, kind):
        """Draw the accessory base panel to the right of the wiimote and create its x:* sprites."""
        cv = getattr(self, "_gp_canvas", None)
        if cv is None or not cv.winfo_exists():
            self._gp_ext_kind = kind                    # record attempt so a transient miss won't retry/frame
            return
        self._gp_ext_kind = kind                        # at most one (re)build attempt per kind
        cache = getattr(self, "_gp_ext_cache", None)
        if cache is None:
            cache = self._gp_ext_cache = {}
        hit = cache.get(kind)
        if hit is not None:                             # reuse decoded PhotoImages (no disk re-read on swap)
            base, self._gp_ext_sprites = hit
        else:
            subdir = f"{self._gp_sel['prof']['dir']}/{kind}-tester"   # e.g. wiimote-tester/nunchuk-tester
            bp = self._gp_base_path(subdir)
            accfit = None
            if bp is not None:                          # fit the accessory panel into the leftover width/height;
                                                        # floor = the standard width/560 rule, so its scale stays
                                                        # stable across displays/crops (never blows up to factor 1)
                aw, ah = self._gp_avail_box(self._gp_ext_x0)
                bw = self._gp_png_w(bp)
                accfit = self._gp_fit_factor(bw, self._gp_png_h(bp), aw, ah,
                                             floor=max(1, round(bw / 560)))
            base, _back, _f = self._gp_load_into(subdir, "_gp_ext_sprites", factor=accfit)
            if base is None:
                return
            cache[kind] = (base, self._gp_ext_sprites)  # held for the page lifetime -> no GC blanks
        self._gp_ext_baseimg = base
        x0 = self._gp_ext_x0
        bw, bh = base.width(), base.height()
        self._gp_ext_bw, self._gp_ext_bh = bw, bh
        self._gp_ext_oids = []
        self._gp_ext_oids.append(cv.create_image(x0, 0, anchor="nw", image=base, tags=("gpext",)))
        need_w, need_h = int(x0 + bw), int(max(self._gp_ch, bh))
        if need_w > self._gp_cw or need_h > self._gp_ch:
            self._gp_cw = max(self._gp_cw, need_w); self._gp_ch = max(self._gp_ch, need_h)
            try: cv.config(width=self._gp_cw, height=self._gp_ch)
            except Exception: pass
        spr = self._gp_ext_sprites
        allowed = self._GP_EXT_BTNS.get(kind, frozenset())
        btns = [s for s in spr if s in allowed]
        sticks = []
        if any(s.startswith("lstick_") for s in spr):
            sticks.append("lstick")
            if kind == "classic":                       # classic has 2 sticks; both reuse the lstick_* set
                sticks.append("rstick")
        spots = sorted(btns) + sticks
        saved = self._gp_ext_load_positions(kind)
        baked = self._gp_baked_positions(kind)
        defpos = self._gp_default_pos_box(spots, bw, bh)
        for s in spots:
            img = spr.get("lstick_rest") if s in ("lstick", "rstick") else spr.get(s)
            if img is None:
                continue
            nx, ny = saved.get(s) or baked.get(s) or defpos.get(s, [0.5, 0.5])
            oid = cv.create_image(x0 + nx * bw, ny * bh, image=img, anchor="center",
                                  state=("normal" if s in ("lstick", "rstick") else "hidden"),
                                  tags=("gpspr", "gpext"))
            key = "x:" + s
            self._gp_items[key] = oid
            self._gp_of[oid] = key
            self._gp_ext_oids.append(oid)

    def _gp_ext_clear(self):
        """Tear down the accessory panel — delete canvas items FIRST, then drop the x:* item
        registrations, then release the image refs last (strict order avoids PhotoImage-GC blanks)."""
        cv = getattr(self, "_gp_canvas", None)
        if cv is not None:
            try: exists = bool(cv.winfo_exists())
            except Exception: exists = False
            if exists:
                for oid in getattr(self, "_gp_ext_oids", []):
                    try: cv.delete(oid)
                    except Exception: pass
        for key in [k for k in getattr(self, "_gp_items", {}) if k.startswith("x:")]:
            oid = self._gp_items.pop(key, None)
            getattr(self, "_gp_of", {}).pop(oid, None)
        self._gp_ext_oids = []
        self._gp_ext_sprites = {}
        self._gp_ext_baseimg = None
        self._gp_ext_kind = "none"
        self._gp_wii_ext = frozenset()                 # so a re-attach re-lights from scratch

    def _gp_ext_stick(self, key, token):
        cv = getattr(self, "_gp_canvas", None)
        oid = getattr(self, "_gp_items", {}).get(key)
        if cv is None or oid is None:
            return
        spr = getattr(self, "_gp_ext_sprites", {})
        img = spr.get(f"lstick_{token}") or spr.get("lstick_rest")
        if img is not None:
            try: cv.itemconfigure(oid, image=img)
            except Exception: pass

    def _gp_apply_wii_snapshot(self, snap):
        """Drive sprites from one WiiSlotReader snapshot (called only when seq advances)."""
        cv = getattr(self, "_gp_canvas", None)
        if cv is None or not cv.winfo_exists():
            return
        if snap["kind"] != getattr(self, "_gp_ext_kind", "none"):   # accessory plugged/unplugged
            self._gp_ext_clear()
            if snap["kind"] in ("nunchuk", "classic"):
                self._gp_ext_build(snap["kind"])
        core = snap["core"]; prev = getattr(self, "_gp_wii_core", frozenset())
        for stem in core - prev:
            self._gp_show_sprite(stem, True)
        for stem in prev - core:
            self._gp_show_sprite(stem, False)
        self._gp_wii_core = core
        ext = snap["ext"]; preve = getattr(self, "_gp_wii_ext", frozenset())
        for stem in ext - preve:
            self._gp_show_sprite("x:" + stem, True)
        for stem in preve - ext:
            self._gp_show_sprite("x:" + stem, False)
        self._gp_wii_ext = ext
        self._gp_ext_stick("x:lstick", snap["lstick"])
        self._gp_ext_stick("x:rstick", snap["rstick"])
        self._gp_wii_label(snap)

    _GP_WII_LABELS = {"dpadup": "↑", "dpaddown": "↓", "dpadleft": "←", "dpadright": "→",
                      "plus": "+", "minus": "−", "one": "1", "two": "2", "home": "Home",
                      "a": "A", "b": "B", "x": "X", "y": "Y", "l": "L", "r": "R",
                      "zl": "ZL", "zr": "ZR", "c": "C", "z": "Z"}

    def _gp_wii_label(self, snap):
        lbl = getattr(self, "_gp_live", None)
        if lbl is None or not lbl.winfo_exists():
            return
        lab = self._GP_WII_LABELS
        parts = [lab.get(s, s.upper()) for s in sorted(snap["core"])]
        parts += [lab.get(s, s.upper()) for s in sorted(snap["ext"])]
        if snap["lstick"] != "rest":
            parts.append("stick " + snap["lstick"])
        if snap["kind"] == "classic" and snap["rstick"] != "rest":
            parts.append("R-stick " + snap["rstick"])
        lbl.config(text=("   ·   ".join(parts)) if parts else "—")

    # ---- grab / poll / live sprites ----
    def _gp_start(self):
        import os, evdev
        from evdev import ecodes as e
        self._gp_stop()
        o = self._gp_sel
        if o.get("transport") == "hidraw":
            return self._gp_start_hid(o)
        try:
            d = evdev.InputDevice(o["path"])
        except Exception:
            self._gp_status("Couldn't open that pad — reconnect and reopen.", warn=True)
            return
        try:
            os.set_blocking(d.fd, False); d.grab()
        except Exception:
            try: d.close()
            except Exception: pass
            self._gp_status("Couldn't grab the pad (in use elsewhere?). Close other apps + retry.", warn=True)
            return
        self._gp_devs = [d]
        self._gp_ainfo = dict(d.capabilities().get(e.EV_ABS, []))
        self._gp_abs = {}; self._gp_pressed = {}; self._gp_idle = 0
        self._gp_quit_held = set(); self._gp_quit_t0 = None
        self._gp_cal_map = self._gp_cal_load()
        self._gp_status("Testing — press the controls. ■ Stop or hold Start (6 s) to end.")
        self._gp_after = self.root.after(30, self._gp_poll)

    def _gp_start_hid(self, o):
        """DolphinBar Wii Remote: drive it on a WiiSlotReader thread; the poll only reads snapshots."""
        slot = o.get("slot")
        node = dict(self._gp_db_slots()).get(slot, o.get("node"))   # re-resolve (slot<->node can change)
        self._gp_reap_readers()
        if any(r.node == node and not r.is_done() for r in getattr(self, "_gp_dead_readers", [])):
            self._gp_status("Still releasing the previous Wii session — press ▶ again in a moment.", warn=True)
            return
        self._gp_transport = "hidraw"
        self._gp_quit_t0 = None
        self._gp_wii_core = frozenset(); self._gp_wii_ext = frozenset()
        self._gp_wii_seq = -1; self._gp_wii_status = None
        self._gp_reader = WiiSlotReader(node)
        self._gp_reader.start()
        self._gp_status("Waking the Wii Remote… if nothing lights up, press 1+2 on the remote.")
        self._gp_after = self.root.after(30, self._gp_poll)

    _GP_WII_STATUS_MSG = {
        "opening": ("Waking the Wii Remote…", False),
        "empty":   ("That slot is empty now — press 1+2 on the remote, then ← Pads → ↻ Refresh.", True),
        "asleep":  ("Wii Remote asleep — press 1+2 to re-sync. (Reconnecting…)", True),
        "live":    ("Testing — press the Wii Remote (and Nunchuk/Classic if attached). "
                    "■ Stop or hold + (6 s) to end.", False),
        "error":   ("Couldn't read that slot — try ← Pads → ↻ Refresh.", True),
    }

    def _gp_poll_hid(self):
        cv = getattr(self, "_gp_canvas", None)
        if cv is None or not cv.winfo_exists():
            return                                          # page gone — drop the chain (no re-arm)
        r = getattr(self, "_gp_reader", None)
        if r is None:
            return
        snap = r.snapshot()
        if snap["status"] != getattr(self, "_gp_wii_status", None):
            self._gp_wii_status = snap["status"]
            msg, warn = self._GP_WII_STATUS_MSG.get(snap["status"], (snap["status"], False))
            self._gp_status(msg, warn=warn)
        if snap["seq"] != getattr(self, "_gp_wii_seq", -1):
            self._gp_wii_seq = snap["seq"]
            self._gp_apply_wii_snapshot(snap)
        if self._gp_quit_check("plus" in (snap.get("core") or ())):
            return                                       # test just ended — don't re-arm
        self._gp_after = self.root.after(30, self._gp_poll)

    def _gp_quit_check(self, active):
        """Hold Start (pads) or + (Wii Remote) for 6 s DURING a test to end it — the
        on-pad way to release a grabbed controller (cousin of the X-Arcade tester's
        P1+P2 Start). Safe vs the global Start+Select quit-MAD combo: Start alone
        never arms it, the nav never saw the press (the pad was grabbed), and the
        release after ungrab can't arm a timer.
        Returns True when the test was just ended (caller must not re-arm the poll)."""
        if active:
            if getattr(self, "_gp_quit_t0", None) is None:
                self._gp_quit_t0 = time.monotonic()
            rem = 6.0 - (time.monotonic() - self._gp_quit_t0)
            if rem <= 0:
                self._gp_quit_t0 = None
                self._gp_stop()
                self._gp_status("Test ended (held Start) — controller released. Navigate freely.")
                return True
            self._gp_status(f"Keep holding to end the test…  {int(rem) + 1}")
        elif getattr(self, "_gp_quit_t0", None) is not None:
            self._gp_quit_t0 = None
            self._gp_status("Testing — press the controls. ■ Stop or hold Start (6 s) to end.")
        return False

    def _gp_stop(self):
        self._gp_cleanup()
        self._gp_reset_sprites()
        lbl = getattr(self, "_gp_live", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.config(text="—")
        if getattr(self, "_gp_status_lbl", None) is not None:
            self._gp_status("Stopped.")

    def _gp_reset_sprites(self):
        cv = getattr(self, "_gp_canvas", None)
        if cv is None:
            return
        rest = getattr(self, "_gp_sprites", {}).get("lstick_rest")
        erest = getattr(self, "_gp_ext_sprites", {}).get("lstick_rest")
        for k, oid in getattr(self, "_gp_items", {}).items():
            try:
                if k in ("lstick", "rstick"):
                    if rest is not None: cv.itemconfigure(oid, image=rest)
                    cv.itemconfigure(oid, state="normal")
                elif k in ("x:lstick", "x:rstick"):
                    if erest is not None: cv.itemconfigure(oid, image=erest)
                    cv.itemconfigure(oid, state="normal")
                elif k == "p2indicator":
                    cv.itemconfigure(oid, state="normal")     # always-on P2 badge
                else:
                    cv.itemconfigure(oid, state="hidden")
            except Exception: pass
        self._gp_wii_core = frozenset(); self._gp_wii_ext = frozenset()

    def _gp_poll(self):
        if getattr(self, "_gp_transport", None) == "hidraw":
            return self._gp_poll_hid()
        cv = getattr(self, "_gp_canvas", None)
        if cv is None or not cv.winfo_exists():          # canvas destroyed under a stray tick — don't re-arm
            self._gp_after = None
            return
        if not getattr(self, "_gp_devs", None):
            return
        d = self._gp_devs[0]
        from evdev import ecodes as e
        changed = False
        try:
            events = list(d.read())
        except (BlockingIOError, OSError):
            events = []
        for ev in events:
            changed = True
            if ev.type == e.EV_KEY and ev.code == e.BTN_START:
                held = getattr(self, "_gp_quit_held", None)
                if held is None:
                    held = self._gp_quit_held = set()
                (held.add if ev.value == 1 else held.discard)(ev.code)
            if getattr(self, "_gp_cal", False):
                self._gp_cal_capture(ev)
            self._gp_event_sprite(ev)
        if changed:
            self._gp_idle = 0
            lbl = getattr(self, "_gp_live", None)
            if lbl is not None and lbl.winfo_exists():
                act = sorted(set(self._gp_pressed.values()))
                lbl.config(text=("   ·   ".join(act)) if act else "—")
        else:
            self._gp_idle = getattr(self, "_gp_idle", 0) + 1
            if self._gp_sel["prof"]["key"] == "steamdeck" and self._gp_idle > 666:
                self._gp_stop(); return
        if self._gp_quit_check(e.BTN_START in getattr(self, "_gp_quit_held", set())):
            return                                       # test just ended — don't re-arm
        self._gp_after = self.root.after(30, self._gp_poll)

    def _gp_norm(self, code):
        ai = getattr(self, "_gp_ainfo", {}).get(code); v = self._gp_abs.get(code)
        if ai is None or v is None or ai.max == ai.min:
            return 0.0
        mid = (ai.max + ai.min) / 2
        return max(-1.0, min(1.0, (v - mid) / ((ai.max - ai.min) / 2)))

    def _gp_event_sprite(self, ev):
        from evdev import ecodes as e
        cal = getattr(self, "_gp_cal_map", {})
        if ev.type == e.EV_KEY:
            spot = cal.get(f"k{ev.code}") or self._gp_default_spot(ev.code)
            if spot:
                self._gp_show_sprite(spot, bool(ev.value)); self._gp_track(spot, bool(ev.value))
        elif ev.type == e.EV_ABS:
            self._gp_abs[ev.code] = ev.value
            if ev.code in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY, e.ABS_HAT0X, e.ABS_HAT0Y):
                self._gp_update_sticks()
            else:
                spot = cal.get(f"a{ev.code}")
                if spot is None and ev.code in (e.ABS_Z, e.ABS_RZ):       # analog triggers default
                    cands = ("l2", "zl") if ev.code == e.ABS_Z else ("r2", "zr")
                    spot = next((s for s in cands if s in self._gp_sprites), None)
                if spot:
                    ai = getattr(self, "_gp_ainfo", {}).get(ev.code)
                    on = ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.4
                    self._gp_show_sprite(spot, on); self._gp_track(spot, on)

    def _gp_track(self, spot, on):
        if on:
            self._gp_pressed[spot] = spot
        else:
            self._gp_pressed.pop(spot, None)

    def _gp_update_sticks(self):
        from evdev import ecodes as e
        cv = self._gp_canvas
        for spot, ax, ay in (("lstick", e.ABS_X, e.ABS_Y), ("rstick", e.ABS_RX, e.ABS_RY)):
            oid = getattr(self, "_gp_items", {}).get(spot)
            if oid is None:
                continue
            nx, ny = self._gp_norm(ax), self._gp_norm(ay)
            T = 0.5
            dx = -1 if nx < -T else (1 if nx > T else 0)
            dy = -1 if ny < -T else (1 if ny > T else 0)
            d = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right",
                 (-1, -1): "ul", (1, -1): "ur", (-1, 1): "dl", (1, 1): "dr"}.get((dx, dy))
            img = self._gp_sprites.get(f"lstick_{d}") if d else self._gp_sprites.get("lstick_rest")
            if img is not None:
                try: cv.itemconfigure(oid, image=img)
                except Exception: pass
            self._gp_track(spot, d is not None)
        # d-pad: from the hat; on stickless pads (FC30) the d-pad rides ABS_X/Y instead
        stickless = "lstick" not in getattr(self, "_gp_items", {})
        def dsgn(hat, ax):
            h = self._gp_abs.get(hat, 0)
            if h:
                return -1 if h < 0 else 1
            if stickless:
                v = self._gp_norm(ax)
                return -1 if v < -0.5 else (1 if v > 0.5 else 0)
            return 0
        hx, hy = dsgn(e.ABS_HAT0X, e.ABS_X), dsgn(e.ABS_HAT0Y, e.ABS_Y)
        for spot, on in (("dpadleft", hx < 0), ("dpadright", hx > 0),
                         ("dpadup", hy < 0), ("dpaddown", hy > 0)):
            self._gp_show_sprite(spot, on); self._gp_track(spot, on)

    # ---- calibration ----
    def _gp_cal_file(self):
        return (Path.home() / "Emulation" / "storage" / "control-panel"
                / f"gp-{self._gp_sel['prof']['key']}-calib.json")

    def _gp_cal_load(self):
        import json
        try:
            p = self._gp_cal_file()
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _gp_cal_save(self):
        import json
        try:
            p = self._gp_cal_file(); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(getattr(self, "_gp_cal_map", {}), indent=2))
        except Exception:
            pass

    def _gp_calibrate(self):
        if getattr(self, "_gp_cal", False):
            self._gp_cal = False; self._gp_cal_sel = None; self._gp_cal_highlight(None)
            self._gp_cal_save()
            n = len(getattr(self, "_gp_cal_map", {}))
            self._gp_stop()
            self._gp_status(f"Calibration saved ({n} button{'' if n == 1 else 's'} bound).")
            return
        self._gp_cal_map = self._gp_cal_load()
        self._gp_cal = True; self._gp_cal_sel = None
        self._gp_start()
        if not getattr(self, "_gp_devs", None):
            self._gp_cal = False
            return
        self._gp_set_visible(True)
        self._gp_status("Calibrate — tap a control on screen (touchscreen/trackpad), then press it on the "
                        "pad. Repeat each button; tap “Calibrate” to save. (Sticks + d-pad are automatic.)")

    def _gp_cal_select(self, key):
        self._gp_cal_sel = key
        self._gp_cal_highlight(key)
        self._gp_status(f"Now press “{key}” on the pad…")

    def _gp_cal_highlight(self, key):
        cv = getattr(self, "_gp_canvas", None)
        if cv is None:
            return
        hid = getattr(self, "_gp_cal_hl", None)
        oid = getattr(self, "_gp_items", {}).get(key) if key else None
        bb = cv.bbox(oid) if oid is not None else None
        if bb is None:
            if hid is not None: cv.itemconfigure(hid, state="hidden")
            return
        x0, y0, x1, y1 = bb; m = 4
        if hid is None:
            self._gp_cal_hl = cv.create_rectangle(x0 - m, y0 - m, x1 + m, y1 + m, outline="#39ff14", width=3)
        else:
            cv.coords(hid, x0 - m, y0 - m, x1 + m, y1 + m); cv.itemconfigure(hid, state="normal")
        cv.tag_raise(self._gp_cal_hl)

    def _gp_cal_capture(self, ev):
        from evdev import ecodes as e
        sel = getattr(self, "_gp_cal_sel", None)
        if not sel:
            return
        ikey = None
        if ev.type == e.EV_KEY and ev.value == 1:
            ikey = f"k{ev.code}"
        elif (ev.type == e.EV_ABS and ev.code not in
              (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY, e.ABS_HAT0X, e.ABS_HAT0Y)):
            ai = getattr(self, "_gp_ainfo", {}).get(ev.code)
            if ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.5:
                ikey = f"a{ev.code}"
        if ikey is None:
            return
        self._gp_cal_map = {k: v for k, v in getattr(self, "_gp_cal_map", {}).items() if k != ikey}
        self._gp_cal_map[ikey] = sel
        self._gp_cal_sel = None
        self._gp_cal_highlight(None)
        self._gp_status(f"✓ bound → “{sel}”. Tap the next, or “Calibrate” to save.")

    def _gp_status(self, text, *, warn=False):
        lbl = getattr(self, "_gp_status_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.config(text=text, fg=(self.c.get("warn", "#ff6b5e") if warn else self.c.get("dim", "#9aa")))
