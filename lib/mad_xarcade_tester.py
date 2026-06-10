"""MAD page mixin: the X-Arcade visual tester ("X-Arcade" sidebar page).

Extracted verbatim from router-config-gui.py (MAD task #13 modularization).
XArcadeTesterMixin is NOT standalone — it must be mixed into MAD's App class.
GamepadTesterMixin (lib/mad_gamepad_tester.py) depends on it one-way: the
Gamepad tester calls self._xa_xport/_xa_port_of to identify the X-Arcade.
Expects the host to provide: self.root / self.c / self.font, the App helpers
_title/_scroll/_lbl/_btn/_textwrap/_mad_img/_mad_art_dirs, and getattr-guarded
cleanup of _xa_after/_xa_mode_after/_xa_devs/_xat_edit_devs in App._clear().
"""
from __future__ import annotations

import time
from pathlib import Path

import tkinter as tk

from .policy import load_merged


class XArcadeTesterMixin:
    # ============================ X-Arcade tester ============================
    # The X-Arcade in Xbox mode spans THREE evdev nodes: two 045e:02a1 GAMEPADs (P1/P2 sticks +
    # buttons) and one 1241:1111 MOUSE (the trackball + the top-left/top-right/red buttons mapped
    # to mouse1/2/3). We open+GRAB those nodes so test inputs don't navigate MAD or move the cursor;
    # the Deck's own pad is left ungrabbed so it still drives this page.
    XARCADE_VIDPIDS = {(0x045e, 0x02a1), (0x1241, 0x1111)}

    def xarcade(self):
        """Live-test the X-Arcade in Xbox mode: press cabinet controls and see them register.
        (Pass 1: device read + grab + mode indicator + live readout; on-cabinet glows +
        calibration come next.)"""
        if getattr(self, "_xa_mode_after", None):
            try: self.root.after_cancel(self._xa_mode_after)
            except Exception: pass
            self._xa_mode_after = None
        self._title("X-Arcade tester")
        inner = self._scroll()
        self._xa_mode_lbl = self._lbl(inner, "Detecting…", role="accent", size=14, bold=True,
                                      anchor="w", pady=(0, 6))
        self._lbl(inner,
                  "Tests the X-Arcade while it's in Xbox 360 (gamepad) mode. Press “Start "
                  "test”, then press every control on the cabinet — each lights up below. Your Deck "
                  "pad still drives this page (the X-Arcade is captured while testing).",
                  role="text", size=13, anchor="w", pady=(0, 10),
                  wraplength=self._textwrap(), justify="left")

        # the cabinet overlay — themeable (active theme's router-config/icons/), scaled on load
        self._xa_canvas = None
        self._xa_img = self._mad_img(["icons/x-arcade-tester/base.png",
                                      "icons/x-arcade-tester-overlay.png"], 1000)
        if self._xa_img is not None:
            cv = tk.Canvas(inner, width=self._xa_img.width(), height=self._xa_img.height(),
                           bg=self.c["bg"], highlightthickness=0)
            cv.pack(anchor="w", pady=(0, 8))
            cv.create_image(0, 0, anchor="nw", image=self._xa_img)
            self._xa_canvas = cv
        else:
            self._lbl(inner, "(overlay not found — put x-arcade-tester-overlay.png in the active "
                      "theme's router-config/icons/)", role="dim", size=11, anchor="w")
        self._xat_make_glows()                  # alignment rings (Edit mode / fallback)
        self._xat_make_sprites()                # your pressed-state art (shown on press)

        self._xa_live_lbl = tk.Label(inner, text="—", bg=self.c["bg"], fg=self.c["text"],
                                     font=self.font(14, mono=True), anchor="w", justify="left",
                                     wraplength=self._textwrap())
        self._xa_live_lbl.pack(anchor="w", pady=(0, 8))

        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(2, 6))
        self._btn(bar, "▶ Start test", self._xa_start).pack(side="left", padx=(0, 10))
        self._btn(bar, "■ Stop", self._xa_stop).pack(side="left", padx=(0, 10))
        self._btn(bar, "◉ Calibrate", self._xat_calibrate).pack(side="left", padx=(0, 10))
        bar2 = tk.Frame(inner, bg=self.c["bg"]); bar2.pack(anchor="w", pady=(0, 16))
        self._btn(bar2, "✛ Edit positions", self._xat_show_positions).pack(side="left", padx=(0, 10))
        self._btn(bar2, "\U0001f4be Save", self._xat_save_positions).pack(side="left", padx=(0, 10))
        self._btn(bar2, "↺ Reset", self._xat_reset_positions).pack(side="left", padx=(0, 10))
        self._btn(bar2, "▦ Preview sprites", self._xat_preview_sprites).pack(side="left", padx=(0, 10))
        self._xa_status_lbl = self._lbl(inner, "", role="dim", size=12, anchor="w",
                                        wraplength=self._textwrap(), justify="left")

        self._xa_devs = []
        self._xa_pressed = {}
        self._xa_after = None
        self._xa_mode_after = None
        self._xa_mode_poll()

    # ---- highlight spots on the cabinet overlay (draggable + savable) ----
    def _xat_pos_file(self):
        return Path.home() / "Emulation" / "storage" / "control-panel" / "xarcade-positions.json"

    def _xat_load_positions(self):
        import json
        try:
            p = self._xat_pos_file()
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _xat_default_spots(self):
        """Built-in default spot layout (normalized 0-1, symmetric Tankstick)."""
        spots = [("p1_stick", "P1 stick", 0.115, 0.34),
                 ("p2_stick", "P2 stick", 0.885, 0.34)]
        p1cols = (0.205, 0.252, 0.299, 0.346)         # 8 buttons = 4 cols x 2 rows
        rows = (0.36, 0.52)
        for r, ry in enumerate(rows):
            for c, cx in enumerate(p1cols):
                bn = r * 4 + c + 1
                spots.append((f"p1_b{bn}", f"P1 b{bn}", cx, ry))
                spots.append((f"p2_b{bn}", f"P2 b{bn}", 1.0 - cx, ry))   # mirror for P2
        spots += [("mouse1", "Mouse1 (top-left)", 0.475, 0.135),
                  ("mouse2", "Mouse2 (top-right)", 0.525, 0.135),
                  ("mouse3", "Mouse3 (red)", 0.905, 0.135),
                  ("trackball", "Trackball", 0.50, 0.42),
                  ("side_l1", "L side 1", 0.045, 0.30), ("side_l2", "L side 2", 0.045, 0.52),
                  ("side_r1", "R side 1", 0.955, 0.30), ("side_r2", "R side 2", 0.955, 0.52)]
        return spots

    def _xat_spots(self):
        """Default layout overridden by any saved drag-aligned positions."""
        saved = self._xat_load_positions()
        out = []
        for k, lbl, nx, ny in self._xat_default_spots():
            s = saved.get(k)
            out.append((k, lbl, s[0] if s else nx, s[1] if s else ny))
        return out

    def _xat_make_glows(self):
        """Create a hidden glow ring per spot; draggable while Edit-positions is on."""
        self._xat_glows = {}
        self._xat_glow_of = {}
        self._xat_show = False
        self._xat_drag = None
        cv = getattr(self, "_xa_canvas", None)
        if cv is None or not cv.winfo_exists() or getattr(self, "_xa_img", None) is None:
            return
        W, H = self._xa_img.width(), self._xa_img.height()
        self._xat_r = max(9, int(W * 0.017))
        r = self._xat_r
        for key, _label, nx, ny in self._xat_spots():
            x, y = nx * W, ny * H
            oid = cv.create_oval(x - r, y - r, x + r, y + r, outline="#39ff14",
                                 width=3, state="hidden", tags=("xaglow",))
            self._xat_glows[key] = oid
            self._xat_glow_of[oid] = key
        cv.tag_bind("xaglow", "<ButtonPress-1>", self._xat_drag_start)
        cv.tag_bind("xaglow", "<B1-Motion>", self._xat_drag_move)
        cv.tag_bind("xaglow", "<ButtonRelease-1>", self._xat_drag_end)

    def _xat_glow(self, key, on):
        cv = getattr(self, "_xa_canvas", None)
        oid = getattr(self, "_xat_glows", {}).get(key)
        if cv is not None and oid is not None and cv.winfo_exists():
            try: cv.itemconfigure(oid, state=("normal" if on else "hidden"))
            except Exception: pass

    def _xat_oval_center(self, oid):
        c = self._xa_canvas.coords(oid)        # oval → [x0,y0,x1,y1]; image (anchor=center) → [x,y]
        return ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2) if len(c) >= 4 else (c[0], c[1])

    def _xat_drag_start(self, ev):
        cur = self._xa_canvas.find_withtag("current")
        oid = cur[0] if cur else None
        if getattr(self, "_xat_cal", False):              # calibration: tap a spot to bind it next
            key = getattr(self, "_xat_sprite_of", {}).get(oid)
            if key:
                self._xat_cal_select(key)
            return
        if not getattr(self, "_xat_show", False):
            return
        self._xat_drag = oid

    def _xat_drag_move(self, ev):
        if not getattr(self, "_xat_show", False) or not getattr(self, "_xat_drag", None):
            return
        cv = self._xa_canvas
        x, y = cv.canvasx(ev.x), cv.canvasy(ev.y)
        oid = self._xat_drag
        cx, cy = self._xat_oval_center(oid)
        cv.move(oid, x - cx, y - cy)           # drag the sprite (or ring) itself

    def _xat_drag_end(self, ev):
        self._xat_drag = None

    def _xat_show_positions(self):
        """Edit-positions: show all sprites and DRAG them directly onto their controls."""
        self._xat_show = not getattr(self, "_xat_show", False)
        if self._xat_show:
            self._xa_stop()                          # don't run a test while aligning
        self._xat_set_sprites_visible(self._xat_show)
        self._xat_edit_grab(self._xat_show)          # gamepad grabbed (no nav); trackball stays live
        self._xat_status("Edit ON — drag each control's sprite onto it with the X-Arcade trackball "
                         "(or Deck trackpad/touchscreen), then 💾 Save." if self._xat_show
                         else "Edit off.")

    def _xat_save_positions(self):
        import json
        cv = getattr(self, "_xa_canvas", None)
        if cv is None or getattr(self, "_xa_img", None) is None or not self._xat_glows:
            self._xat_status("Nothing to save (no overlay loaded).", warn=True); return
        W, H = self._xa_img.width(), self._xa_img.height()
        items = getattr(self, "_xat_sprite_items", None) or self._xat_glows   # save the sprite spots
        pos = {k: [round(self._xat_oval_center(o)[0] / W, 4), round(self._xat_oval_center(o)[1] / H, 4)]
               for k, o in items.items()}
        try:
            p = self._xat_pos_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(pos, indent=2))
            self._xat_status(f"Saved {len(pos)} positions.")
        except Exception as ex:
            self._xat_status(f"Couldn't save: {ex}", warn=True)

    def _xat_reset_positions(self):
        """Move every ring back to the built-in default layout (Save to keep)."""
        cv = getattr(self, "_xa_canvas", None)
        if cv is None or getattr(self, "_xa_img", None) is None:
            return
        W, H = self._xa_img.width(), self._xa_img.height()
        r = getattr(self, "_xat_r", max(9, int(W * 0.017)))
        defaults = {k: (nx, ny) for (k, _l, nx, ny) in self._xat_default_spots()}
        for key, (nx, ny) in defaults.items():
            x, y = nx * W, ny * H
            ring = self._xat_glows.get(key)
            if ring is not None:
                cv.coords(ring, x - r, y - r, x + r, y + r)
            sid = getattr(self, "_xat_sprite_items", {}).get(key)
            if sid is not None:
                cv.coords(sid, x, y)           # image item: coords = its centre
        self._xat_status("Reset to default layout — drag to fine-tune, then 💾 Save.")

    # ---- pressed-state sprites (your custom art, shown on press) ----
    XAT_SPRITE_DIV = 3        # base + sprites are authored ~3104px wide; shown subsampled by this

    def _xat_sprite_dir(self):
        for d in self._mad_art_dirs():
            cand = Path(d) / "icons" / "x-arcade-tester"
            if cand.is_dir():
                return cand
        return None

    def _xat_load_sprites(self):
        self._xat_sprites = {}
        base = self._xat_sprite_dir()
        if base is None:
            return
        files = {"button": "pressed button.png", "rest": "joystickrest.png",
                 "U": "JoystickU.png", "D": "JoystickD.png", "L": "JoystickL.png", "R": "JoystickR.png",
                 "UL": "JoystickUL.png", "UR": "JoystickUR.png", "DL": "JoystickDL.png", "DR": "JoystickDR.png",
                 "p1pressed": "P1pressed.png", "p2pressed": "P2pressed.png", "red": "redbuttonpressed.png",
                 "lside": "LSidebuttonpressed.png", "rside": "RSidebuttonpressed.png",
                 "trackball": "trackballiactivity.png"}
        div = self.XAT_SPRITE_DIV
        for name, fn in files.items():
            try:
                img = tk.PhotoImage(file=str(base / fn))
                self._xat_sprites[name] = img.subsample(div, div) if div > 1 else img
            except Exception:
                pass

    def _xat_spot_sprite(self, key):
        if key.endswith("_stick"):
            return "rest"
        if key[:4] in ("p1_b", "p2_b"):
            return "button"
        return {"mouse1": "p1pressed", "mouse2": "p2pressed", "mouse3": "red", "trackball": "trackball",
                "side_l1": "lside", "side_l2": "lside", "side_r1": "rside", "side_r2": "rside"}.get(key)

    def _xat_make_sprites(self):
        """Create a canvas image item per spot (your pressed-state art), centred at its spot. The
        joystick REST sprite is always visible (stick at rest); the rest are hidden until pressed
        (or shown in Edit/Preview)."""
        self._xat_sprite_items = {}
        self._xat_sprite_of = {}
        self._xat_cal_hl = None
        self._xat_preview = False
        self._xat_cal = False
        self._xat_cal_sel = None
        self._xat_cal_map = self._xat_cal_load()
        cv = getattr(self, "_xa_canvas", None)
        if cv is None or not cv.winfo_exists() or getattr(self, "_xa_img", None) is None:
            return
        if not getattr(self, "_xat_sprites", None):
            self._xat_load_sprites()
        W, H = self._xa_img.width(), self._xa_img.height()
        for key, _label, nx, ny in self._xat_spots():
            nm = self._xat_spot_sprite(key)
            spr = self._xat_sprites.get(nm) if nm else None
            if spr is not None:
                st = "normal" if key.endswith("_stick") else "hidden"
                oid = cv.create_image(nx * W, ny * H, image=spr, anchor="center", state=st,
                                      tags=("xasprite",))
                self._xat_sprite_items[key] = oid
                self._xat_sprite_of[oid] = key
        cv.tag_bind("xasprite", "<ButtonPress-1>", self._xat_drag_start)   # sprites are draggable in Edit
        cv.tag_bind("xasprite", "<B1-Motion>", self._xat_drag_move)
        cv.tag_bind("xasprite", "<ButtonRelease-1>", self._xat_drag_end)

    def _xat_set_sprites_visible(self, on):
        """Show/hide non-stick sprites (the stick REST sprite stays visible)."""
        cv = getattr(self, "_xa_canvas", None)
        if cv is None:
            return
        for key, oid in getattr(self, "_xat_sprite_items", {}).items():
            st = "normal" if (on or key.endswith("_stick")) else "hidden"
            try: cv.itemconfigure(oid, state=st)
            except Exception: pass

    def _xat_preview_sprites(self):
        """Toggle ALL sprites visible — to check their look/scale/position on the cabinet."""
        self._xat_preview = not getattr(self, "_xat_preview", False)
        self._xat_set_sprites_visible(self._xat_preview)
        self._xat_status("Previewing all sprites — tell me what's off (scale/position/which art)."
                         if self._xat_preview else "Sprite preview off.")

    # ---- live: drive the sprites from presses ----
    def _xat_input_spot(self, tag, code):
        """X-Arcade Xbox-mode gamepad input → spot: cluster 1..8 plus the per-side specials."""
        from evdev import ecodes as e
        if tag not in ("P1", "P2"):
            return None
        # cluster 1..8 (on-cabinet top-left..bottom-right). Buttons 3/4 are the analog triggers
        # (handled as ABS_Z/ABS_RZ in _xat_event_sprite); BTN_TL2/TR2 cover a digital-trigger unit.
        order = {e.BTN_EAST: 1, e.BTN_NORTH: 2, e.BTN_TL2: 3, e.BTN_TR2: 4,
                 e.BTN_SOUTH: 5, e.BTN_WEST: 6, e.BTN_TL: 7, e.BTN_TR: 8}
        n = order.get(code)
        if n:
            return f"{tag.lower()}_b{n}"
        if code == e.BTN_SELECT:                        # "coin" → the FORWARD side button
            return "side_l1" if tag == "P1" else "side_r1"
        if code == e.BTN_START:                         # "start" → the centre P1/P2 icon button
            return "mouse1" if tag == "P1" else "mouse2"
        return None

    def _xat_show_sprite(self, key, on):
        cv = getattr(self, "_xa_canvas", None)
        oid = getattr(self, "_xat_sprite_items", {}).get(key)
        if cv is not None and oid is not None:
            try: cv.itemconfigure(oid, state=("normal" if on else "hidden"))
            except Exception: pass

    def _xat_event_sprite(self, od, ev):
        """Show/hide a pressed-state sprite for a live evdev event. A saved calibration
        (input → spot) overrides the built-in default per input."""
        from evdev import ecodes as e
        tag = od["tag"]
        cal = getattr(self, "_xat_cal_map", {})
        if ev.type == e.EV_KEY:
            # mouse node: BTN_LEFT/RIGHT = the BACK side buttons, BTN_MIDDLE = the red button
            spot = cal.get(f"{tag}:k{ev.code}") or (
                {e.BTN_LEFT: "side_l2", e.BTN_RIGHT: "side_r2", e.BTN_MIDDLE: "mouse3"}.get(ev.code)
                if tag == "M" else self._xat_input_spot(tag, ev.code))
            if spot:
                self._xat_show_sprite(spot, bool(ev.value))
        elif ev.type == e.EV_ABS and tag in ("P1", "P2"):
            od.setdefault("axval", {})[ev.code] = ev.value
            if ev.code in (e.ABS_Z, e.ABS_RZ):                 # analog triggers → buttons 3/4
                ai = od["absinfo"].get(ev.code)
                on = ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.4
                spot = cal.get(f"{tag}:a{ev.code}") or f"{tag.lower()}_b{3 if ev.code == e.ABS_Z else 4}"
                self._xat_show_sprite(spot, on)
            else:
                self._xat_update_stick(od)
        elif ev.type == e.EV_REL and tag == "M":
            self._xat_flash_trackball()

    def _xat_update_stick(self, od):
        from evdev import ecodes as e
        cv = getattr(self, "_xa_canvas", None)
        sid = getattr(self, "_xat_sprite_items", {}).get(f"{od['tag'].lower()}_stick")
        if cv is None or sid is None:
            return
        ax = od.get("axval", {})
        def norm(code):
            ai = od["absinfo"].get(code); v = ax.get(code)
            if ai is None or v is None or ai.max == ai.min:
                return 0.0
            mid = (ai.max + ai.min) / 2
            return max(-1.0, min(1.0, (v - mid) / ((ai.max - ai.min) / 2)))
        T = 0.5
        nx = norm(e.ABS_X)
        if abs(nx) < T: nx = norm(e.ABS_HAT0X)             # stick or, if centred, the d-pad
        ny = norm(e.ABS_Y)
        if abs(ny) < T: ny = norm(e.ABS_HAT0Y)
        dx = -1 if nx < -T else (1 if nx > T else 0)
        dy = -1 if ny < -T else (1 if ny > T else 0)
        d = {(0, -1): "U", (0, 1): "D", (-1, 0): "L", (1, 0): "R",
             (-1, -1): "UL", (1, -1): "UR", (-1, 1): "DL", (1, 1): "DR"}.get((dx, dy))
        spr = self._xat_sprites.get(d) if d else self._xat_sprites.get("rest")
        if spr is not None:
            try: cv.itemconfigure(sid, image=spr, state="normal")
            except Exception: pass

    def _xat_flash_trackball(self):
        cv = getattr(self, "_xa_canvas", None)
        oid = getattr(self, "_xat_sprite_items", {}).get("trackball")
        if cv is None or oid is None:
            return
        try: cv.itemconfigure(oid, state="normal")
        except Exception: pass
        if getattr(self, "_xat_ball_after", None):
            try: self.root.after_cancel(self._xat_ball_after)
            except Exception: pass
        self._xat_ball_after = self.root.after(
            160, lambda: cv.winfo_exists() and cv.itemconfigure(oid, state="hidden"))

    def _xat_reset_sprites(self):
        """Back to resting: non-stick sprites hidden, sticks showing the rest sprite."""
        cv = getattr(self, "_xa_canvas", None)
        if cv is None:
            return
        rest = getattr(self, "_xat_sprites", {}).get("rest")
        for key, oid in getattr(self, "_xat_sprite_items", {}).items():
            try:
                if key.endswith("_stick"):
                    if rest is not None: cv.itemconfigure(oid, image=rest)
                    cv.itemconfigure(oid, state="normal")
                else:
                    cv.itemconfigure(oid, state="hidden")
            except Exception: pass

    def _xa_xport(self):
        """The X-Arcade's identified USB port ([hardware].xarcade_port), '' if unset."""
        try:
            return str((load_merged().get("hardware") or {}).get("xarcade_port", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _xa_port_of(phys):
        import re
        m = re.search(r"-([0-9]+(?:\.[0-9]+)*)/input", phys or "")
        return m.group(1) if m else ""

    def _xa_is_xarcade(self, d, xport):
        """True if d belongs to THE X-Arcade, not another 045e Xbox-lookalike. The 045e:02a1 gamepad
        interfaces are byte-identical to a real Xbox pad → filter by the identified USB port; the
        1241:1111 trackball (a different port) matches by vid:pid alone."""
        vp = (d.info.vendor, d.info.product)
        if vp not in self.XARCADE_VIDPIDS:
            return False
        if vp == (0x045e, 0x02a1) and xport:
            return self._xa_port_of(d.phys) == xport
        return True

    def _xat_edit_grab(self, on):
        """Edit-positions: grab the X-Arcade GAMEPAD nodes so their buttons can't navigate MAD, but
        leave the trackball/mouse FREE so you can drag the sprites with the cabinet itself."""
        import os, evdev
        for d in getattr(self, "_xat_edit_devs", []) or []:
            try: d.ungrab()
            except Exception: pass
            try: d.close()
            except Exception: pass
        self._xat_edit_devs = []
        if not on:
            return
        xport = self._xa_xport()
        for path in sorted(evdev.list_devices()):
            try:
                d = evdev.InputDevice(path)
            except Exception:
                continue
            if (d.info.vendor == 0x045e and d.info.product == 0x02a1
                    and self._xa_is_xarcade(d, xport)):
                try:
                    os.set_blocking(d.fd, False)
                    d.grab()
                    self._xat_edit_devs.append(d)
                except Exception:
                    try: d.close()
                    except Exception: pass
            else:
                d.close()

    def _xa_mode_poll(self):
        import evdev
        xport = self._xa_xport()
        xbox = False
        try:
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                except Exception:
                    continue
                if (d.info.vendor == 0x045e and d.info.product == 0x02a1
                        and self._xa_is_xarcade(d, xport)):
                    xbox = True
                d.close()
                if xbox:
                    break
        except Exception:
            pass
        lbl = getattr(self, "_xa_mode_lbl", None)
        if lbl is None or not lbl.winfo_exists():
            return
        if xbox:
            lbl.config(text="●  Xbox 360 mode  (gamepad + trackball detected)", fg=self.c["accent"])
        else:
            lbl.config(text="○  Not in gamepad mode — set the X-Arcade to Xbox 360 mode (or it's unplugged)",
                       fg=self.c.get("warn", "#ff6b5e"))
        self._xa_mode_after = self.root.after(1500, self._xa_mode_poll)

    def _xa_start(self):
        import os, evdev
        from evdev import ecodes as e
        self._xa_stop()
        self._xat_edit_grab(False)                   # release any Edit-positions grab first
        opened = []
        failed = 0
        try:
            paths = sorted(evdev.list_devices())
        except Exception:
            paths = []
        xport = self._xa_xport()
        for path in paths:
            try:
                d = evdev.InputDevice(path)
            except Exception:
                continue
            if not self._xa_is_xarcade(d, xport):     # port-filter 045e so a 2nd Xbox pad isn't grabbed
                d.close(); continue
            try:
                os.set_blocking(d.fd, False)
                if (d.info.vendor, d.info.product) != (0x1241, 0x1111):   # grab the GAMEPAD nodes
                    d.grab()                                              # (no MAD navigation) but
                                                                          # leave the trackball/mouse
                                                                          # UNGRABBED so it still drives
                                                                          # MAD's cursor — we still READ
                                                                          # it, so its sprites light too.
            except Exception:
                failed += 1
                try: d.close()
                except Exception: pass
                continue
            caps = d.capabilities()
            is_mouse = e.BTN_LEFT in caps.get(e.EV_KEY, [])
            opened.append({"dev": d, "mouse": is_mouse, "path": path,
                           "absinfo": dict(caps.get(e.EV_ABS, []))})   # cache: NOT per-event
        # Label gamepad nodes P1/P2 by ASCENDING event-node number (lower = P1). The two X-Arcade
        # pad interfaces are otherwise identical (same vid:pid/phys, empty uniq), so this is a
        # best-effort readout label — calibration will be the definitive P1/P2 map.
        def _evnum(p):
            s = "".join(ch for ch in p.rsplit("/", 1)[-1] if ch.isdigit())
            return int(s) if s else 0
        n = 0
        for od in sorted(opened, key=lambda o: (o["mouse"], _evnum(o["path"]))):
            if od["mouse"]:
                od["tag"] = "M"
            else:
                n += 1
                od["tag"] = f"P{n}"
        self._xa_devs = opened
        self._xa_quit_t0 = None
        if not opened:
            self._xat_status("No X-Arcade nodes found — is it connected and in Xbox mode?", warn=True)
            return
        if failed:
            self._xat_status(f"⚠ Captured {len(opened)} node(s) but {failed} wouldn't grab — those may "
                             "still navigate. Close anything else using the X-Arcade, then retry.", warn=True)
        else:
            self._xat_status("Testing — press any control. Hold P1 + P2 Start together for 3s to end "
                             "the test (or ■ Stop with the Deck pad).")
        self._xa_pressed = {}
        self._xa_after = self.root.after(40, self._xa_poll)

    # ---- calibration: tap a spot on screen, then press it on the cabinet ----
    def _xat_cal_file(self):
        return Path.home() / "Emulation" / "storage" / "control-panel" / "xarcade-calib.json"

    def _xat_cal_load(self):
        import json
        try:
            p = self._xat_cal_file()
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _xat_cal_save(self):
        import json
        try:
            p = self._xat_cal_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(getattr(self, "_xat_cal_map", {}), indent=2))
        except Exception:
            pass

    def _xat_spot_label(self, key):
        for k, lbl, _x, _y in self._xat_default_spots():
            if k == key:
                return lbl
        return key

    def _xat_calibrate(self):
        """Toggle calibration: grab the X-Arcade, tap a spot on screen, press it on the cabinet."""
        if getattr(self, "_xat_cal", False):                  # exit → save
            self._xat_cal = False
            self._xat_cal_sel = None
            self._xat_cal_highlight(None)
            self._xat_cal_save()
            n = len(getattr(self, "_xat_cal_map", {}))
            self._xa_stop()
            self._xat_status(f"Calibration saved ({n} control{'' if n == 1 else 's'} bound).")
            return
        self._xat_cal_map = self._xat_cal_load()
        self._xat_cal = True
        self._xat_cal_sel = None
        self._xa_start()                                      # grab + poll (poll captures while cal)
        if not getattr(self, "_xa_devs", None):
            self._xat_cal = False
            return                                            # _xa_start already warned
        self._xat_set_sprites_visible(True)                   # show all spots to tap
        self._xat_status("Calibrate — tap a control on screen (Deck touchscreen / trackpad), then press "
                         "that control on the cabinet. Repeat any wrong ones; tap “Calibrate” to save.")

    def _xat_cal_select(self, key):
        self._xat_cal_sel = key
        self._xat_cal_highlight(key)
        self._xat_status(f"Now press “{self._xat_spot_label(key)}” on the cabinet…")

    def _xat_cal_highlight(self, key):
        """Draw a bright box around the picked spot's sprite (raised above it so it's visible)."""
        cv = getattr(self, "_xa_canvas", None)
        if cv is None:
            return
        hid = getattr(self, "_xat_cal_hl", None)
        oid = getattr(self, "_xat_sprite_items", {}).get(key) if key else None
        bb = cv.bbox(oid) if oid is not None else None
        if bb is None:
            if hid is not None:
                cv.itemconfigure(hid, state="hidden")
            return
        x0, y0, x1, y1 = bb; m = 4
        if hid is None:
            self._xat_cal_hl = cv.create_rectangle(x0 - m, y0 - m, x1 + m, y1 + m,
                                                   outline="#39ff14", width=3)
        else:
            cv.coords(hid, x0 - m, y0 - m, x1 + m, y1 + m)
            cv.itemconfigure(hid, state="normal")
        cv.tag_raise(self._xat_cal_hl)

    def _xat_cal_capture(self, od, ev):
        from evdev import ecodes as e
        sel = getattr(self, "_xat_cal_sel", None)
        if not sel:
            return
        tag = od["tag"]; ikey = None
        if ev.type == e.EV_KEY and ev.value == 1:
            ikey = f"{tag}:k{ev.code}"
        elif ev.type == e.EV_ABS and tag in ("P1", "P2") and ev.code in (e.ABS_Z, e.ABS_RZ):
            ai = od["absinfo"].get(ev.code)
            if ai is not None and ev.value > ai.min + (ai.max - ai.min) * 0.5:
                ikey = f"{tag}:a{ev.code}"
        if ikey is None:
            return
        # rebind: this input now points only at the picked spot
        self._xat_cal_map = {k: v for k, v in getattr(self, "_xat_cal_map", {}).items() if k != ikey}
        self._xat_cal_map[ikey] = sel
        self._xat_cal_sel = None
        self._xat_cal_highlight(None)
        self._xat_status(f"✓ bound → “{self._xat_spot_label(sel)}”. Tap the next control, or "
                         "“Calibrate” again to save.")

    def _xa_poll(self):
        from evdev import ecodes as e
        if not getattr(self, "_xa_devs", None):
            return
        changed = False
        for od in self._xa_devs:
            d = od["dev"]
            try:
                events = list(d.read())
            except (BlockingIOError, OSError):
                events = []
            for ev in events:
                changed = True
                if ev.type == e.EV_KEY:
                    k = f"{od['tag']}:k{ev.code}"
                    if ev.value:
                        self._xa_pressed[k] = self._xa_keyname(ev.code, od)
                    else:
                        self._xa_pressed.pop(k, None)
                elif ev.type == e.EV_ABS:
                    k = f"{od['tag']}:a{ev.code}"
                    nm = self._xa_absname(ev.code, ev.value, od)
                    if nm:
                        self._xa_pressed[k] = nm
                    else:
                        self._xa_pressed.pop(k, None)
                elif ev.type == e.EV_REL:
                    self._xa_pressed[f"{od['tag']}:ball"] = "Trackball"
                if getattr(self, "_xat_cal", False):
                    self._xat_cal_capture(od, ev)     # calibration: bind the press to the picked spot
                self._xat_event_sprite(od, ev)        # drive your pressed-state sprites
        if changed:
            lbl = getattr(self, "_xa_live_lbl", None)
            if lbl is not None and lbl.winfo_exists():
                active = sorted(set(self._xa_pressed.values()))
                lbl.config(text=("   ·   ".join(active)) if active else "—")
            for k in [k for k in self._xa_pressed if k.endswith(":ball")]:
                self._xa_pressed.pop(k, None)            # trackball is momentary
        if not getattr(self, "_xat_cal", False):         # the exit-combo is off during calibration
            self._xa_quit_check()
        if not getattr(self, "_xa_devs", None):          # the combo above may have ended the test
            return
        self._xa_after = self.root.after(40, self._xa_poll)

    def _xa_quit_check(self):
        """Hold P1 Start + P2 Start together for 3 s to END the test — the on-cabinet way to
        release the grabbed X-Arcade when another pad can't reach ■ Stop."""
        from evdev import ecodes as e
        gpads = [od for od in (self._xa_devs or []) if not od["mouse"]]
        held = sum(1 for od in gpads if f"{od['tag']}:k{e.BTN_START}" in self._xa_pressed)
        if len(gpads) >= 2 and held >= 2:
            if getattr(self, "_xa_quit_t0", None) is None:
                self._xa_quit_t0 = time.monotonic()
            rem = 3.0 - (time.monotonic() - self._xa_quit_t0)
            if rem <= 0:
                self._xat_status("Test ended (held P1 + P2 Start) — X-Arcade released. Navigate freely.")
                self._xa_stop()
            else:
                self._xat_status(f"Hold P1 + P2 Start to end test…  {int(rem) + 1}")
        elif getattr(self, "_xa_quit_t0", None) is not None:
            self._xa_quit_t0 = None
            self._xat_status("Testing — press any control; hold P1 + P2 Start (3s) to end.")

    def _xa_keyname(self, code, od):
        from evdev import ecodes as e
        if od["mouse"]:
            return {e.BTN_LEFT: "Mouse1 (top-left)", e.BTN_RIGHT: "Mouse2 (top-right)",
                    e.BTN_MIDDLE: "Mouse3 (red)"}.get(code, f"mouse btn {code}")
        nm = {e.BTN_SOUTH: "A", e.BTN_EAST: "B", e.BTN_NORTH: "X", e.BTN_WEST: "Y", e.BTN_TL: "LB",
              e.BTN_TR: "RB", e.BTN_SELECT: "Coin/Back", e.BTN_START: "Start", e.BTN_MODE: "Guide",
              e.BTN_THUMBL: "L3", e.BTN_THUMBR: "R3"}.get(code)
        return f"{od['tag']} {nm or 'btn' + str(code)}"

    def _xa_absname(self, code, value, od):
        from evdev import ecodes as e
        ai = od.get("absinfo", {}).get(code)        # cached in _xa_start (no per-event ioctl)
        if ai is None:
            return None
        if code in (e.ABS_Z, e.ABS_RZ):                  # analog triggers
            return (f"{od['tag']} {'LT' if code == e.ABS_Z else 'RT'}"
                    if value > ai.min + (ai.max - ai.min) * 0.4 else None)
        mid = (ai.max + ai.min) / 2
        span = (ai.max - ai.min) / 2 or 1
        n = (value - mid) / span
        if abs(n) < 0.5:
            return None
        pair = {e.ABS_X: ("Left", "Right"), e.ABS_Y: ("Up", "Down"),
                e.ABS_HAT0X: ("Left", "Right"), e.ABS_HAT0Y: ("Up", "Down"),
                e.ABS_RX: ("RStk L", "RStk R"), e.ABS_RY: ("RStk U", "RStk D")}.get(code)
        if not pair:
            return None
        return f"{od['tag']} {pair[0] if n < 0 else pair[1]}"

    def _xa_stop(self):
        if getattr(self, "_xa_after", None):
            try: self.root.after_cancel(self._xa_after)
            except Exception: pass
            self._xa_after = None
        for od in getattr(self, "_xa_devs", []) or []:
            try: od["dev"].ungrab()
            except Exception: pass
            try: od["dev"].close()
            except Exception: pass
        self._xa_devs = []
        self._xa_pressed = {}
        self._xa_quit_t0 = None
        self._xat_reset_sprites()
        lbl = getattr(self, "_xa_live_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.config(text="—")
        self._xat_status("Stopped.")

    def _xat_status(self, text, *, warn=False):
        lbl = getattr(self, "_xa_status_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.config(text=text, fg=self.c.get("warn", "#ff6b5e") if warn else self.c["text_dim"])
