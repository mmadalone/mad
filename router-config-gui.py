#!/usr/bin/env python3
"""
Gamepad-navigable configuration GUI for the controller-router.

No pip on SteamOS, so this uses ONLY tkinter + python-evdev (both present). It
themes itself to match the active ES-DE theme (palette + font) and plays ES-DE's
navigation sounds, with a graceful fallback to a built-in dark theme. Runs
windowed in Desktop Mode and fullscreen when launched from ES-DE in Game Mode
(env ROUTER_GUI_FULLSCREEN=1 or --fullscreen) — navigable entirely with a pad.

Layout (10-foot): a left SIDEBAR of sections, a CONTENT pane, and a FOOTER hint
bar. L1/R1 switch sections; D-pad/stick move focus within the content; A select;
B back; Start quit.

Edits are written to controller-policy.local.toml (machine-owned overrides); the
documented controller-policy.toml is never touched. GUI-only prefs live under a
`[gui]` table there (the router ignores it). Sections:
  • Preview    — connected controllers + what each standalone system would route
  • Systems    — per-system: hands-off + require/warn flags
  • Priority   — per-system / per-collection preferred controller order
  • Quit combo — Detect a hold-to-quit button combo + hold time
  • Backends   — per-backend controller settings (every sane policy knob)
  • GUI        — theme/sound preferences for this GUI
  • Backup     — snapshot & revert emulator + policy configs
"""
import os
import queue
import sys
import time
import tomllib
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib import localpolicy                                       # noqa: E402
from lib import es_systems                                        # noqa: E402
from lib import es_collections as collections                     # noqa: E402
from lib import gui_theme, gui_sound, gui_widgets                 # noqa: E402
from lib import esde_settings                                     # noqa: E402
from lib import sinden_cfg                                       # noqa: E402
from lib.devices import (enumerate_devices, sdl_devices, joypads,  # noqa: E402
                         vidpid, dolphinbar_wiimotes, dolphinbar_present,
                         _dolphinbar_slot_nodes, pin_id, pin_kind, battery_pct,
                         sdl_index_of, port_of)

try:
    import evdev
    from evdev import ecodes as e
except Exception:
    evdev = None
    e = None

# Crash diagnosis: MAD has been segfaulting at the C level (Tk/evdev) with NO Python traceback —
# report_callback_exception / excepthook only catch PYTHON exceptions. faulthandler installs a
# SIGSEGV/SIGABRT/SIGBUS/SIGFPE handler that dumps the Python stack of ALL threads to a file at the
# instant of the fatal signal — so the next crash reveals the EXACT call site (which thread, which
# line) even though it dies in C. Kept open for the app's lifetime.
try:
    import faulthandler as _faulthandler
    _fault_log = open(os.path.expanduser(
        "~/Emulation/storage/controller-router/mad-faulthandler.log"), "a", buffering=1)
    _fault_log.write(f"\n==== faulthandler armed {time.strftime('%F %T')} (pid {os.getpid()}) ====\n")
    _faulthandler.enable(file=_fault_log, all_threads=True)
    import atexit as _atexit
    import signal as _signal
    for _sig in (_signal.SIGTERM, _signal.SIGHUP, _signal.SIGINT):   # external KILL → dump + chain
        try:
            _faulthandler.register(_sig, file=_fault_log, all_threads=True, chain=True)
        except Exception:
            pass
    # Tells apart the THREE ways MAD can vanish: SIGSEGV/ABRT (faulthandler dump) = real crash;
    # SIGTERM dump = external kill; "atexit clean exit" with no signal = normal mainloop end /
    # self-quit (e.g. the 650 ms hold-to-quit timer firing — see _diag in quit()).
    _atexit.register(lambda: (_fault_log.write(
        f"==== atexit: clean process exit {time.strftime('%F %T')} ====\n"), _fault_log.flush()))
except Exception:
    pass


def _diag(msg):
    """Append a flushed line to mad-quit.log — records WHY MAD exited (quit-timer arm/fire). This
    is how we proved the apparent 'crash on controller (dis)connect' was actually MAD's
    hold-to-quit firing (the FC30's Start = its Bluetooth connect button)."""
    try:
        with open(os.path.expanduser(
                "~/Emulation/storage/controller-router/mad-quit.log"), "a") as _f:
            _f.write(f"{time.strftime('%F %T')} {msg}\n")
            _f.flush()
    except Exception:
        pass

POLICY = HERE / "controller-policy.toml"
LOCAL = HERE / "controller-policy.local.toml"

# Cosmetic vid:pid -> friendly label, for display only (every lookup falls back
# to the raw vid:pid). NOT a routing input.
KNOWN_PADS = {"054c:0ce6": "DualSense", "054c:09cc": "DualShock 4",
              "057e:0330": "Wii U Pro", "28de:1205": "Steam Deck",
              "28de:11ff": "Steam Deck (Steam Input)",
              "2dc8:2810": "8BitDo FC30", "2dc8:3820": "8BitDo N30 Pro",
              "045e:02a1": "Xbox 360"}   # X-Arcade in Xbox mode shares this id; only the
                                         # IDENTIFIED-port one is shown as X-Arcade (_pad_label)
# Compact labels so several toggles fit one row.
PAD_SHORT = {"054c:0ce6": "DualSense", "054c:09cc": "DS4", "045e:02a1": "Xbox 360",
             "2dc8:2810": "8BitDo", "2dc8:3820": "8BitDo N30", "057e:0330": "WiiU Pro", "28de:1205": "Deck",
             "28de:11ff": "Deck(SI)"}

# Detected install presets per backend config path knob (AppImage / Flatpak / …).
# Marked with which exist at render time; a path not listed here stays TOML-only.
CONFIG_PRESETS = {
    ("cemu", "config_dir"): [
        "~/.config/Cemu/controllerProfiles",
        "~/.var/app/info.cemu.Cemu/config/Cemu/controllerProfiles"],
    ("pcsx2", "config_file"): [
        "~/.config/PCSX2/inis/PCSX2.ini",
        "~/.var/app/net.pcsx2.PCSX2/config/PCSX2/inis/PCSX2.ini"],
    ("xemu", "config_file"): [
        "~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml",
        "~/.config/xemu/xemu.toml"],
    ("eden", "config_file"): [
        "~/.config/eden/qt-config.ini"],
    ("rpcs3", "config_file"): [
        "~/.config/rpcs3/input_configs/global/Default.yml",
        "~/.var/app/net.rpcs3.RPCS3/config/rpcs3/input_configs/global/Default.yml"],
}

# Per-knob one-line captions shown under the control on a backend page.
KNOB_HELP = {
    "sdl_priority": "ON = expose only the top connected pad (strict Player 1). "
                    "off = expose all listed pads (multiplayer).",
    "pad_classes": "Pad families that count as players (left→right = P1 preference). "
                   "Pads not listed are hidden from this emulator.",
    "manage_players": "How many player slots the router configures.",
    "manage_pads": "How many pad slots the router configures.",
    "manage_ports_int": "How many controller ports the router configures.",
    "manage_ports_list": "Which controller slots the router manages "
                         "(Cemu Controller 1 = the Deck GamePad, left untouched).",
    "real2_min_wiimotes": "Use 2-remote mode when at least this many Wii Remotes connect.",
    "handheld_class": "Pad used when no listed player pad is connected (solo / handheld).",
    "respect_user_config_classes": "If any of these pads is connected, leave this "
                                    "emulator's input config untouched.",
    "keep_extra": "Extra pad families to always keep visible to the emulator.",
    "templates": "Emulator profile cloned for each pad family.",
    "p1_gamepad_template": "Profile forced onto the first managed slot (none = per-family).",
    "handheld_profile": "Profile written when no external pad is connected "
                        "(none = just clear the managed slots).",
    "template_profile": "Reference profile cloned for each player.",
    "config_dir": "Where this emulator keeps its controller config (AppImage vs Flatpak).",
    "config_file": "Where this emulator keeps its config file (AppImage vs Flatpak).",
}
# Knobs intentionally NOT exposed (shown as an Advanced note).
ADVANCED_KNOBS = ("quit_cmd", "wii_mode_tool", "name_overrides",
                  "backend", "category", "inherits")


def load_merged() -> dict:
    base = {"systems": {}, "backends": {}}
    if POLICY.is_file():
        base = tomllib.load(POLICY.open("rb"))
    over = localpolicy.load(LOCAL)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(base[k].get(kk), dict):
                    base[k][kk].update(vv)
                else:
                    base[k][kk] = vv
        else:
            base[k] = v
    return base


def gui_flags() -> dict:
    """GUI-only prefs from the [gui] table of local.toml (router ignores it)."""
    g = localpolicy.load(LOCAL).get("gui", {})
    return {"sound_muted": bool(g.get("sound_muted", False)),
            "theme_colors": bool(g.get("theme_colors", True)),
            "theme_font": bool(g.get("theme_font", True)),
            "font_scale": str(g.get("font_scale", "auto"))}


def set_gui_flag(key, value):
    data = localpolicy.load(LOCAL)
    data.setdefault("gui", {})[key] = value
    localpolicy.dump(LOCAL, data)


# ── ES-DE startup-splash config ([esde_splash] in local.toml; read by
#    esde-splash-gen.sh at launch). Images only — ES-DE's splash is a static SVG,
#    so no video/animations. The ES-DE binary is patched (esde-bigsplash-patch.sh)
#    to render the splash full-screen, and the generator cover-fills it.
ESDE_SPLASH_DIR = esde_settings.APPDATA / "splashscreens"   # honors $ESDE_APPDATA_DIR
SPLASH_MODES = [("off", "Off (stock ES-DE splash)"),
                ("fixed_image", "Fixed image"),
                ("random_image", "Random image")]
SPLASH_FITS = [("contain", "Contain — whole image, letterboxed"),
               ("cover", "Cover — zoom + crop to fill"),
               ("tile", "Tile — repeat a pattern to fill")]
# Cap on-screen rows — a gamepad list of thousands is unusable + slow to build.
SPLASH_PICKER_CAP = 200


def splash_cfg() -> dict:
    return localpolicy.load(LOCAL).get("esde_splash", {})


def set_splash(key, value):
    data = localpolicy.load(LOCAL)
    data.setdefault("esde_splash", {})[key] = value
    localpolicy.dump(LOCAL, data)


def list_splash_images() -> list:
    if not ESDE_SPLASH_DIR.is_dir():
        return []
    exts = {".png", ".jpg", ".jpeg", ".svg"}
    return sorted((p.name for p in ESDE_SPLASH_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in exts
                   and not p.name.startswith(".")), key=str.lower)


def toggle_splash_image(name, on):
    """Add/remove an image from the random pool ([esde_splash].images; empty=all)."""
    data = localpolicy.load(LOCAL)
    sp = data.setdefault("esde_splash", {})
    cur = list(sp.get("images") or [])
    if on and name not in cur:
        cur.append(name)
    elif not on and name in cur:
        cur.remove(name)
    sp["images"] = cur
    localpolicy.dump(LOCAL, data)


def backend_systems(merged: dict) -> list:
    sysd = merged.get("systems", {})
    out = []
    for s, ent in sysd.items():
        if not isinstance(ent, dict):
            continue
        be = ent.get("backend")
        if not be and isinstance(ent.get("inherits"), str):
            be = sysd.get(ent["inherits"], {}).get("backend")
        if be:
            out.append(s)
    return sorted(out)


def controller_families(merged: dict) -> list:
    fams: list = []
    sysd = merged.get("systems", {})
    for s in sorted(sysd):
        ent = sysd[s]
        if not isinstance(ent, dict):
            continue
        for port in (ent.get("ports") or []):
            for tok in port:
                if tok not in fams:
                    fams.append(tok)
    return fams or ["8BitDo", "DualSense", "Xbox", "X-Arcade",
                    "Steam Deck", "Wii Remote Pro"]


def pad_class_candidates(merged: dict, *extra) -> list:
    """Union of every backend's pad_classes (+ any extra current values), so a
    class-toggle row offers all known player families and shows current picks."""
    out: list = []
    for c in merged.get("backends", {}).values():
        if isinstance(c, dict):
            for cls in c.get("pad_classes", []):
                if cls not in out:
                    out.append(cls)
    for cls in extra:
        if cls and cls not in out:
            out.append(cls)
    return out


def backup_targets(merged: dict) -> dict:
    out: dict = {}
    for bname, c in merged.get("backends", {}).items():
        if not isinstance(c, dict):
            continue
        p = c.get("config_dir") or c.get("config_file")
        if p:
            out[bname] = Path(p).expanduser()
    for name, p in (merged.get("backups", {}).get("extra_configs", {}) or {}).items():
        out[name] = Path(p).expanduser()
    return out


def list_profiles(config_dir_or_file: str, pattern: str) -> list:
    """Profile names found next to an emulator config (stems for *.xml, full
    paths for eden .ini). Returns [] if the dir is missing."""
    if not config_dir_or_file:
        return []
    p = Path(config_dir_or_file).expanduser()
    base = p if p.is_dir() else p.parent
    if not base.is_dir():
        return []
    return sorted(base.glob(pattern))


# ---------------------------------------------------------------------------
# Gamepad navigation: poll evdev, drive tk focus + activation + section switch.
# ---------------------------------------------------------------------------
class GamepadNav:
    NAV_DEBOUNCE = 0.18
    PAGE_STEPS = 6          # fallback focus-stops to jump if no viewport pager
    REPEAT_DELAY_MS = 400   # held direction: delay before auto-repeat kicks in
    REPEAT_RATE_MS = 90     # held direction: repeat interval (~11/sec)
    QUIT_HOLD_MS = 650      # Start must be HELD this long to quit (fat-finger guard)

    def __init__(self, root, on_back, on_quit, on_section,
                 content_getter=None, on_page=None):
        self.root, self.on_back, self.on_quit = root, on_back, on_quit
        self.on_section = on_section
        # content_getter() -> the widget whose Button descendants are the
        # navigable content (focus is CONTAINED here; the sidebar is reached only
        # via L1/R1). on_page(fwd) does real viewport paging (App-provided).
        self.content_getter = content_getter
        self.on_page_cb = on_page
        # auto-repeat (held direction) + hold-to-quit timers
        self._rep_after = None      # after-id of the pending repeat tick
        self._rep_action = None     # callable repeated while a direction is held
        self._rep_key = None        # which input owns the active repeat
        self._quit_after = None     # after-id of the Start+Select hold-to-quit timer
        self._quit_combo_held = set()   # which of {BTN_START, BTN_SELECT} are currently held
        self.devs = []
        self._last = 0.0
        self.capture = None
        self._held = set()
        self._paths = set()
        self._last_scan = 0.0
        # LT/RT pagination: per-device press thresholds for the analog trigger
        # axes (ABS_Z=LT, ABS_RZ=RT) + their latched pressed-state for edge
        # detection (fire once per pull, not continuously while held).
        self._trig_thresh = {}     # path -> {ABS_Z: thresh, ABS_RZ: thresh}
        self._trig_down = {}       # (path, code) -> bool
        # Directional axes/hats -> focus move. Thresholds derived per-device from
        # each axis's own min/max (D-pads report as a -1..1 hat OR a 0..255 axis;
        # a fixed threshold breaks the latter), with a latched zone for edge fire.
        self._dir_thresh = {}      # path -> {code: (lo, hi)}
        self._dir_zone = {}        # (path, code) -> -1/0/1
        # Wii Remotes have NO evdev node (DolphinBar speaks raw hidraw), so we
        # co-read the bar's slot nodes and decode the core button report (0x30)
        # for menu navigation.
        self._wii_fds = {}         # fd -> /dev/hidrawN
        self._wii_btn = {}         # path -> (byte1, byte2) last state
        self._scan()
        self.root.after(20, self._poll)

    def _scan(self):
        if not evdev:
            return
        try:
            present = set(evdev.list_devices())
        except Exception:
            return
        before = set(self._paths)
        for d in list(self.devs):
            if d.path not in present:
                try:
                    d.close()
                except Exception:
                    pass
                self.devs.remove(d); self._paths.discard(d.path)
                self._trig_thresh.pop(d.path, None)
                self._dir_thresh.pop(d.path, None)
                for k in [k for k in self._trig_down if k[0] == d.path]:
                    self._trig_down.pop(k, None)
                for k in [k for k in self._dir_zone if k[0] == d.path]:
                    self._dir_zone.pop(k, None)
                # A pad can vanish mid-press (no release event) — clear the quit-combo held set
                # and cancel any armed quit so a 'stuck' Start/Select from an unplug can't later
                # combine with a real press to quit MAD unexpectedly.
                self._quit_combo_held.clear()
                if self._quit_after is not None:
                    try:
                        self.root.after_cancel(self._quit_after)
                    except Exception:
                        pass
                    self._quit_after = None
        for path in present - self._paths:
            try:
                d = evdev.InputDevice(path)
                caps = d.capabilities()
                keys = set(caps.get(e.EV_KEY, []))
                gamepad = any(0x130 <= k <= 0x13f for k in keys)
                # Also admit the Sinden gun's MOUSE interface (vendor 0x16c0, trigger =
                # BTN_LEFT) so HOLDING the trigger can quit MAD. Tagged _mad_sinden so
                # _handle reads ONLY its trigger (not motion/other → no stray nav).
                sinden = (d.info.vendor == 0x16c0 and e.BTN_LEFT in keys and not gamepad)
                if gamepad or sinden:
                    self.devs.append(d); self._paths.add(path)
                    d._mad_sinden = sinden
                    absinfo = dict(caps.get(e.EV_ABS, []))
                    # LT/RT press thresholds (pulled ≥60% of the axis range).
                    th = {}
                    for code in (e.ABS_Z, e.ABS_RZ):
                        ai = absinfo.get(code)
                        if ai is not None and ai.max > ai.min:
                            th[code] = ai.min + 0.6 * (ai.max - ai.min)
                    if th:
                        self._trig_thresh[path] = th
                    # Directional axes/hats: lo/hi zone edges at 35%/65% of each
                    # axis's own range (works for ±32768 sticks AND 0..255 pads
                    # AND -1..1 hats).
                    dirth = {}
                    for code in (e.ABS_X, e.ABS_Y, e.ABS_HAT0X, e.ABS_HAT0Y):
                        ai = absinfo.get(code)
                        if ai is not None and ai.max > ai.min:
                            span = ai.max - ai.min
                            dirth[code] = (ai.min + 0.35 * span, ai.min + 0.65 * span)
                    if dirth:
                        self._dir_thresh[path] = dirth
                else:
                    d.close()
            except Exception:
                pass
        self._scan_wiimotes()
        if self._paths != before:                # a gamepad (dis)connected this scan
            cb = getattr(self, "_on_devices_changed", None)
            if cb and self.capture is None:       # don't refresh mid press-to-identify
                self.root.after(0, cb)

    def _scan_wiimotes(self):
        """Open/close the DolphinBar hidraw slot nodes for Wii Remote menu nav."""
        try:
            want = set(_dolphinbar_slot_nodes())
        except Exception:
            want = set()
        for fd, p in list(self._wii_fds.items()):
            if p not in want:
                try:
                    os.close(fd)
                except OSError:
                    pass
                del self._wii_fds[fd]; self._wii_btn.pop(p, None)
        have = set(self._wii_fds.values())
        for p in want - have:
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
                self._wii_fds[fd] = p; self._wii_btn[p] = (0, 0)
            except OSError:
                pass

    def _content_focusables(self):
        """The navigable Button descendants of the content region, in visual
        (tree) order. Focus is CONTAINED to these — the sidebar/footer are never
        reached by the D-pad (only L1/R1 switches sections)."""
        root = (self.content_getter() or self.root) if self.content_getter else self.root
        out = []

        def walk(w):
            for ch in w.winfo_children():
                try:
                    cls = ch.winfo_class()
                    btn_ok = cls in ("Button", "TButton") and str(ch.cget("state")) != "disabled"
                    if (btn_ok or getattr(ch, "_mad_focusable", False)) and ch.winfo_ismapped():
                        out.append(ch)                       # incl. slide switches
                except Exception:
                    pass
                walk(ch)

        try:
            walk(root)
        except Exception:
            pass
        return out

    def _all_focusables(self):
        """Every navigable Button across the WHOLE window (sidebar + content), so
        the D-pad/stick moves 360° between ANY controls (geometric XY-focus)."""
        out = []

        def walk(w):
            for ch in w.winfo_children():
                try:
                    btn_ok = (ch.winfo_class() in ("Button", "TButton")
                              and str(ch.cget("state")) != "disabled")
                    if (btn_ok or getattr(ch, "_mad_focusable", False)) and ch.winfo_ismapped():
                        out.append(ch)
                except Exception:
                    pass
                walk(ch)

        try:
            walk(self.root)
        except Exception:
            pass
        return out

    @staticmethod
    def _rect(w):
        # A widget may delegate its nav geometry to a wider container (e.g. a stepper's ‹/›
        # report their whole row via _mad_navrect) so spatial nav treats it as that footprint.
        g = getattr(w, "_mad_navrect", None) or w
        try:
            return (g.winfo_rootx(), g.winfo_rooty(),
                    g.winfo_width(), g.winfo_height())
        except Exception:
            return (0, 0, 0, 0)

    def _nearest(self, cur, pool, direction):
        """Geometric nearest control in `direction` within `pool` (excludes cur). Candidates that
        OVERLAP cur on the cross axis (same row for Left/Right, same column for Up/Down) are
        preferred over any that don't — so L/R stays in the row and U/D in the column rather than
        diagonal-hopping to a closer-but-off-axis control."""
        x, y, w, h = self._rect(cur)
        cx, cy = x + w / 2, y + h / 2
        aligned, other = [], []
        for o in pool:
            if o is cur:
                continue
            ox, oy, ow, oh = self._rect(o)
            dx, dy = (ox + ow / 2) - cx, (oy + oh / 2) - cy
            # Perpendicular MISALIGNMENT = the gap between the two rects on the cross axis
            # (0 when they overlap) — NOT centre distance. So a wide control and a narrow one
            # that share a column/row count as aligned (fixes Down skipping wide buttons, and
            # makes 2-column nav clean).
            xgap = max(0, max(x, ox) - min(x + w, ox + ow))
            ygap = max(0, max(y, oy) - min(y + h, oy + oh))
            if direction == "right":
                if dx <= 4:
                    continue
                along, perp = dx, ygap
            elif direction == "left":
                if dx >= -4:
                    continue
                along, perp = -dx, ygap
            elif direction == "down":
                if dy <= 4:
                    continue
                along, perp = dy, xgap
            else:  # up
                if dy >= -4:
                    continue
                along, perp = -dy, xgap
            (aligned if perp == 0 else other).append((along + perp * 2, o))
        pool2 = aligned or other              # cross-axis-aligned wins; else nearest overall
        return min(pool2, key=lambda t: t[0])[1] if pool2 else None

    def _nearest_by_y(self, cur, pool):
        """Closest control by vertical centre — used when CROSSING sidebar<->content."""
        if not pool:
            return None
        _, y, _, h = self._rect(cur)
        cy = y + h / 2
        return min(pool, key=lambda o: abs((self._rect(o)[1] + self._rect(o)[3] / 2) - cy))

    def _spatial_move(self, direction: str):
        """Group-aware focus move. Up/Down stay WITHIN the current region (content OR
        sidebar) so vertical nav never leaks into the sidebar; Left/Right are the only
        way to cross — Left off content's left edge → sidebar, Right → content."""
        items = self._all_focusables()
        if not items:
            return
        cur = self.root.focus_get()
        if cur not in items:
            items[0].focus_set()
            return
        side = [o for o in items if getattr(o, "_mad_sidebar", False)]
        content = [o for o in items if not getattr(o, "_mad_sidebar", False)]
        cur_side = getattr(cur, "_mad_sidebar", False)
        if direction in ("up", "down"):
            pool = side if cur_side else content
            tgt = self._nearest(cur, pool, direction)
            if tgt is None and cur_side and side:
                # Carousel: the sidebar is a single column, so Down off the bottom wraps
                # to the top section and Up off the top wraps to the bottom.
                ordered = sorted(side, key=lambda o: getattr(o, "_mad_sidebar_idx", 0))
                tgt = ordered[0] if direction == "down" else ordered[-1]
        elif direction == "left":
            tgt = (self._nearest(cur, side, "left") if cur_side
                   else self._nearest(cur, content, "left") or self._nearest_by_y(cur, side))
        else:  # right
            tgt = (self._nearest_by_y(cur, content) if cur_side
                   else self._nearest(cur, content, "right"))
        if tgt is not None:
            tgt.focus_set()

    # Back-compat shim — the LT/RT pager fallback (_page) calls this.
    def _move(self, fwd: bool):
        self._spatial_move("down" if fwd else "up")

    def _page(self, fwd: bool):
        """One viewport of scrolling (LT/RT). Uses the App's real pager when
        available; else falls back to jumping several focus stops."""
        if self.on_page_cb:
            try:
                self.on_page_cb(fwd)
                return
            except Exception:
                pass
        for _ in range(self.PAGE_STEPS):
            self._move(fwd)

    # ---- auto-repeat (held direction) ----
    def _start_repeat(self, key, action):
        self._stop_repeat()
        self._rep_key, self._rep_action = key, action
        action()                                            # immediate first fire
        self._rep_after = self.root.after(self.REPEAT_DELAY_MS, self._do_repeat)

    def _do_repeat(self):
        if self._rep_action:
            self._rep_action()
            self._rep_after = self.root.after(self.REPEAT_RATE_MS, self._do_repeat)

    def _stop_repeat(self, key=None):
        if key is not None and self._rep_key != key:
            return                                          # a different input owns it
        if self._rep_after:
            try:
                self.root.after_cancel(self._rep_after)
            except Exception:
                pass
        self._rep_after = self._rep_action = self._rep_key = None

    def _activate(self):
        w = self.root.focus_get()
        act = getattr(w, "_mad_activate", None)      # slide switches + custom controls
        if callable(act):
            act()
        elif isinstance(w, (tk.Button, ttk.Button)):
            w.invoke()

    def _handle(self, x, now, dev):
        if x.type == e.EV_KEY:
            code, val = x.code, x.value
            if self.capture is not None and 0x130 <= code <= 0x13f:
                if val:
                    self._held.add(code)
                elif self._held:
                    held = set(self._held)
                    self._held = set()
                    cb = self.capture
                    try:                            # dispatch by ARITY, not by catching
                        import inspect               # TypeError (which could mask a real one)
                        two_arg = len(inspect.signature(cb).parameters) >= 2
                    except (TypeError, ValueError):
                        two_arg = False
                    cb(held, dev) if two_arg else cb(held)  # 2-arg = press-to-identify
                return
            if self.capture is not None:
                return                          # captured: ignore dpad + every other nav button
            # Quit MAD = HOLD Start + Select TOGETHER ~0.65s. A bare Start hold is NOT enough:
            # the 8BitDo FC30's Start doubles as its Bluetooth connect/power button, so holding it
            # to (dis)connect the pad was quitting MAD (looked like a crash). The gun trigger no
            # longer quits either (it collides with testing gun buttons). Arm when both are held,
            # cancel the moment either releases.
            if code in (e.BTN_START, e.BTN_SELECT):
                (self._quit_combo_held.add if val == 1 else self._quit_combo_held.discard)(code)
                both = (e.BTN_START in self._quit_combo_held
                        and e.BTN_SELECT in self._quit_combo_held)
                if both and self._quit_after is None:
                    _diag(f"quit-timer ARMED by Start+Select ({self.QUIT_HOLD_MS}ms)")
                    self._quit_after = self.root.after(self.QUIT_HOLD_MS, self.on_quit)
                elif not both and self._quit_after is not None:
                    try:
                        self.root.after_cancel(self._quit_after)
                    except Exception:
                        pass
                    self._quit_after = None
                return
            # D-pad: 360° spatial focus move, auto-repeating while held.
            _dpad = {e.BTN_DPAD_DOWN: "down", e.BTN_DPAD_UP: "up",
                     getattr(e, "BTN_DPAD_RIGHT", -1): "right",
                     getattr(e, "BTN_DPAD_LEFT", -1): "left"}
            if code in _dpad:
                if val == 1:
                    d = _dpad[code]
                    self._start_repeat(("k", code), lambda d=d: self._spatial_move(d))
                elif val == 0:
                    self._stop_repeat(("k", code))
                return
            if val != 1:
                return
            if code == e.BTN_SOUTH:
                self._activate()
            elif code == e.BTN_EAST:
                self.on_back()
            elif code == e.BTN_TL:                     # L bumper -> previous section
                self.on_section(-1)
            elif code == e.BTN_TR:                     # R bumper -> next section
                self.on_section(1)
            elif code == getattr(e, "BTN_TL2", -1):    # LT (digital) -> page up
                self._page(False)
            elif code == getattr(e, "BTN_TR2", -1):    # RT (digital) -> page down
                self._page(True)
        elif x.type == e.EV_ABS:
            if self.capture is not None:
                return                          # locked (press-to-identify / live-input test): no axis nav
            # LT/RT pagination — analog triggers (ABS_Z=LT, ABS_RZ=RT). Fire once
            # on the press edge (released → pulled past threshold), per device.
            th = self._trig_thresh.get(dev.path)
            if th and x.code in th:
                key = (dev.path, x.code)
                down = x.value >= th[x.code]
                was = self._trig_down.get(key, False)
                self._trig_down[key] = down
                if down and not was and now - self._last > self.NAV_DEBOUNCE:
                    self._page(x.code == e.ABS_RZ)   # LT(ABS_Z)=up, RT(ABS_RZ)=down
                    self._last = now
                return
            # Directional axes/hats -> move focus. Range-aware (per-device lo/hi)
            # + zone-latched so a held direction fires once and a centred axis
            # doesn't drift-trigger. neg = up/left = prev, pos = down/right = next.
            dt = self._dir_thresh.get(dev.path)
            if dt and x.code in dt:
                lo, hi = dt[x.code]
                z = -1 if x.value <= lo else (1 if x.value >= hi else 0)
                key = (dev.path, x.code)
                prev = self._dir_zone.get(key, 0)
                if z == prev:
                    return
                self._dir_zone[key] = z
                horizontal = x.code in (e.ABS_X, e.ABS_HAT0X)
                if z == 0:
                    self._stop_repeat(key)                  # axis re-centred
                else:
                    if horizontal:
                        d = "right" if z > 0 else "left"
                    else:
                        d = "down" if z > 0 else "up"
                    self._start_repeat(key, lambda d=d: self._spatial_move(d))

    def _poll(self):
        now = time.monotonic()
        if now - self._last_scan >= 2.0:
            self._last_scan = now
            self._scan()
        try:
            for d in list(self.devs):
                while True:
                    try:
                        x = d.read_one()
                    except (BlockingIOError, OSError):
                        x = None
                    if x is None:
                        break
                    self._handle(x, now, d)
            # Wii Remotes (DolphinBar hidraw) — decode core button reports.
            for fd, p in list(self._wii_fds.items()):
                while True:
                    try:
                        data = os.read(fd, 32)
                    except (BlockingIOError, OSError):
                        data = b""
                    if not data:
                        break
                    if len(data) >= 3 and data[0] == 0x30:
                        self._handle_wiimote(p, data[1], data[2], now)
        except Exception:
            pass
        finally:
            try:
                self.root.after(20, self._poll)
            except Exception:
                pass            # root destroyed during shutdown — stop polling quietly

    # Wii Remote core-button bitmasks in report 0x30 (byte1 = dpad+Plus,
    # byte2 = face + Minus/Home). The remote is held SIDEWAYS for menu use, but
    # we map by logical dpad bits regardless of orientation.
    def _handle_wiimote(self, path, b1, b2, now):
        if self.capture is not None:
            return                              # don't navigate while Detecting
        prev1, prev2 = self._wii_btn.get(path, (0, 0))
        self._wii_btn[path] = (b1, b2)
        new1, new2 = b1 & ~prev1, b2 & ~prev2   # newly-pressed bits (edge)
        if new1 & (0x08 | 0x01):                # D-pad Up or Left -> prev
            self._move(False)
        elif new1 & (0x04 | 0x02):              # D-pad Down or Right -> next
            self._move(True)
        if new2 & 0x08:                         # A -> activate
            self._activate()
        if new2 & 0x04:                         # B -> back
            self.on_back()
        if new1 & 0x10:                         # Plus -> next page
            self.on_section(1)
        if new2 & 0x10:                         # Minus -> previous page
            self.on_section(-1)


# Sinden action-code → Tk keysym, for the button-map live-press indicators. Mirrors sinden_cfg's
# value scheme (8-17 digits, 18-43 A-Z, 44-69 a-z, 70-80 specials, 82-93 F-keys). Codes 1-6 are
# mouse/special — mouse 1/2/3 are matched via event.num; 4/5/6 (Pause/Turbo/Reload) have no plain
# event so those rows can't light (noted in the page help).
_BP_CODE_KEYSYM = {70: "Return", 71: "space", 72: "Escape", 73: "Tab",
                   74: "Up", 75: "Down", 76: "Left", 77: "Right",
                   78: "plus", 79: "minus", 80: "period"}
for _i in range(10):
    _BP_CODE_KEYSYM[8 + _i] = str(_i)                 # 8-17  → '0'-'9'
for _i in range(26):
    _BP_CODE_KEYSYM[18 + _i] = chr(65 + _i)           # 18-43 → 'A'-'Z'
    _BP_CODE_KEYSYM[44 + _i] = chr(97 + _i)           # 44-69 → 'a'-'z'
for _i in range(12):
    _BP_CODE_KEYSYM[82 + _i] = f"F{_i + 1}"           # 82-93 → F1-F12
_BP_KEYSYM_CODE = {sym: code for code, sym in _BP_CODE_KEYSYM.items()}
_BP_MOD_BIT = {1: 0x1, 2: 0x4, 3: 0x8}                # Shift / Ctrl / Alt — Tk event.state bits


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk, fullscreen: bool):
        self.root = root
        flags = gui_flags()
        self.theme = gui_theme.Theme(use_theme_colors=flags["theme_colors"],
                                     use_theme_font=flags["theme_font"],
                                     font_scale=flags["font_scale"])
        self.sound = gui_sound.Sound(muted=flags["sound_muted"])
        self.style = gui_widgets.Style(self.theme, self.sound)
        self.c = self.theme.colors
        self.font = self.theme.font

        root.configure(bg=self.c["bg"])
        root.title("MAD — Steam Deck control panel")
        self._imgs = []                         # keep PhotoImage refs alive (Tk GC)
        self._img_cache = {}                    # memo: avoid reloading/recompositing per render
        self._sb_after = None                   # pending sidebar browse-switch (debounce)
        self._wii_after = None                  # pending Preview Wii-Remote re-poll (cancelled on leave)
        self._reload_after = None               # pending theme-reload (debounce)
        self._page_refresh = None               # current page's live device-change refresh (auto-refresh)
        self._backup_gen = None                 # Backup page generation (stale-thread-callback guard)
        _ico = self._mad_img(["icon.png"], 64)
        if _ico:
            try:
                root.iconphoto(True, _ico)
            except Exception:
                pass
        if fullscreen:
            root.attributes("-fullscreen", True)
            # cursor kept visible (the Sinden gun / mouse drives it; user request)
        else:
            root.geometry("1100x720")

        self.sections = [("Preview", self.preview),
                         ("Systems", self.systems),
                         ("Priority", self.priority),
                         ("Players", self.players),
                         ("Quit combo", self.quitcombo),
                         ("Backends", self.backends),
                         ("Lightgun", self.lightgun),
                         ("Splash", self.splash),
                         ("GUI", self.guisettings),
                         ("Backup", self.backup)]
        self.section_idx = 0
        self.stack = []
        self._back_focus = []        # parallel to self.stack: content-focus index to restore on back()
        self._suppress_nav = False

        # ── three-region shell ──
        outer = tk.Frame(root, bg=self.c["bg"]); outer.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(outer, bg=self.c["surface"], width=200)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.body = tk.Frame(outer, bg=self.c["bg"])
        self.body.pack(side="left", fill="both", expand=True)
        self.footer = tk.Label(
            root, bg=gui_theme._mix(self.c["bg"], "#000000", 0.4),
            fg=self.c["text_dim"], anchor="w",
            font=self.font(12),
            text="  A select   •   B back   •   ◂ L1/R1 ▸ sections   •   LT/RT scroll   •   hold Start+Select to quit  ")
        self.footer.pack(fill="x", side="bottom")
        self._sidebar_btns = []
        self._build_sidebar()

        self.nav = GamepadNav(root, self.back, self.quit, self.switch_section,
                              content_getter=lambda: self.body,
                              on_page=self._page_view)
        self.nav._on_devices_changed = self._on_devices_changed   # auto-refresh on (dis)connect
        # MAD is driven by GAMEPAD + MOUSE only — the KEYBOARD does nothing for browsing. A
        # lightgun mapped to Esc/Enter/arrows synthesizes those keys; if MAD reacted to them the
        # gun would back out / activate controls. So we swallow every key (return "break") and,
        # while the button-map page is open, feed the keypress to its live indicators instead.
        # Bound on our only focusable classes (Button, Canvas toggles/steppers) AND on "all" so
        # it fires whether or not something is focused; same handler everywhere → no bindtag-order
        # surprises. Mouse is NOT swallowed (it still browses — see _bp_feed_mouse).
        for _cls in ("Button", "Canvas"):
            for _seq in ("<KeyPress>", "<KeyRelease>", "<space>", "<KeyRelease-space>"):
                root.bind_class(_cls, _seq, self._global_key)   # replace tk.Button's invoke-on-space default
        for _seq in ("<KeyPress>", "<KeyRelease>", "<Tab>", "<Shift-Tab>", "<ISO_Left_Tab>"):
            root.bind_all(_seq, self._global_key, add="+")
        # Mouse presses feed the button-map indicators too (no break — clicks still browse).
        root.bind_all("<ButtonPress>", self._bp_feed_mouse, add="+")
        root.bind_all("<ButtonRelease>", self._bp_feed_mouse, add="+")

        self._cv = self._inner = self._cv_win = None
        self._bp_active = False                  # button-map indicator listener gate (set per page)
        root.bind_all("<FocusIn>", self._on_focus)
        # Block the sidebar browse-on-focus until startup settles, else a WM-assigned
        # initial focus on a sidebar button live-switches the section (MAD would open on
        # the wrong page instead of Preview). Re-enabled shortly after the first render.
        self._allow_sidebar_browse = False
        # Worker threads must NEVER touch Tk (not thread-safe → segfault). They drop UI
        # callbacks on this queue; the main-thread pump runs them. Crash-logging captures
        # any uncaught exception (main, Tk callback, or pump) for diagnosis.
        self._ui_q = queue.Queue()
        self.root.report_callback_exception = lambda et, e, tb: self._log_exc("tk-callback", et, e, tb)
        sys.excepthook = lambda et, e, tb: self._log_exc("uncaught", et, e, tb)
        self.root.after(60, self._ui_pump)
        self.show_section(0)
        self.root.after(800, lambda: setattr(self, "_allow_sidebar_browse", True))

    def _ui_pump(self):
        """Run UI callbacks queued by worker threads ON THE MAIN THREAD (Tkinter is not
        thread-safe). Drains the queue, then re-arms itself for the App's lifetime."""
        try:
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    self._log_exc("ui-callback", *sys.exc_info())
        finally:
            try:
                self.root.after(60, self._ui_pump)
            except Exception:
                pass

    def _log_exc(self, where, et, e, tb):
        """Append a full traceback to mad-gui.log (the GUI's crashes are otherwise lost —
        its stderr isn't captured). Best-effort."""
        try:
            log = Path.home() / "Emulation/storage/controller-router/mad-gui.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a") as f:
                f.write(f"\n[{time.strftime('%F %T')}] {where}:\n"
                        + "".join(traceback.format_exception(et, e, tb)) + "\n")
        except Exception:
            pass


    # ---- sidebar / sections ----
    def _mad_art_dirs(self):
        """Where MAD looks for art/icons: the ACTIVE THEME's router-config/ first
        (themeable, ships with the theme), then MAD's bundled art/, then the
        esde-build/art project dir (where Madalone drops new assets)."""
        dirs = []
        td = getattr(self.theme, "theme_dir", None)
        if td:
            dirs.append(Path(td) / "router-config")
        dirs.append(Path(__file__).resolve().parent / "art")
        dirs.append(Path.home() / "esde-build" / "art")
        return dirs

    def _load_png(self, path, target_w):
        """Load a PNG at `path`, integer-subsampled toward `target_w` px (Tk has no
        smooth scaling). Memoized per render (cleared on theme change). None if bad."""
        key = ("png", str(path), target_w)
        if key in self._img_cache:
            return self._img_cache[key]
        try:
            p = Path(path)
            if not p.is_file():
                self._img_cache[key] = None
                return None
            img = tk.PhotoImage(file=str(p))
            w = img.width()
            if w > target_w * 1.4:
                f = max(1, round(w / target_w))
                img = img.subsample(f, f)
            elif w and w < target_w * 0.6:          # tiny art → integer upscale
                img = img.zoom(max(1, round(target_w / w)))
            self._imgs.append(img)
            self._img_cache[key] = img
            return img
        except Exception:
            self._img_cache[key] = None
            return None

    def _blank_img(self, w, h):
        """Transparent placeholder so tiles lacking console.png keep a uniform
        top-image footprint (collections, or a system whose theme has no art)."""
        key = ("blank", int(w), int(h))
        if key in self._img_cache:
            return self._img_cache[key]
        try:
            img = tk.PhotoImage(width=max(1, int(w)), height=max(1, int(h)))
            self._imgs.append(img)
            self._img_cache[key] = img
            return img
        except Exception:
            self._img_cache[key] = None
            return None

    def _mad_img(self, names, target_w):
        """First existing PNG among `names` across the art dirs (theme first → themeable)."""
        for base in self._mad_art_dirs():
            for nm in names:
                img = self._load_png(base / nm, target_w)
                if img is not None:
                    return img
        return None

    def _console_img(self, sysname, target_w=130):
        """The active ES-DE theme's per-system console.png (themed art, natural size).
        Tries the name as-is then lowercased — ES-DE lowercases collection theme dirs
        (collection 'Fighter' → themes/<t>/fighter/)."""
        td = getattr(self.theme, "theme_dir", None)
        if not td:
            return None
        return (self._load_png(Path(td) / sysname / "console.png", target_w)
                or self._load_png(Path(td) / sysname.lower() / "console.png", target_w))

    def _fit_img(self, path, box_w, box_h):
        """Letterbox a PNG CENTERED onto a fixed box_w×box_h transparent image, so
        every tile is identical size regardless of the source art's dimensions
        (Tk only scales by integer factors, so this 'contains' then centres)."""
        key = ("fit", str(path), box_w, box_h)
        if key in self._img_cache:
            return self._img_cache[key]
        try:
            p = Path(path)
            if not p.is_file():
                self._img_cache[key] = None
                return None
            src = tk.PhotoImage(file=str(p))
            sw, sh = src.width(), src.height()
            if sw < 1 or sh < 1:
                self._img_cache[key] = None
                return None
            if sw > box_w or sh > box_h:                          # contain: downscale
                f = max(-(-sw // box_w), -(-sh // box_h))         # ceil division
                src = src.subsample(f, f); sw, sh = src.width(), src.height()
            else:                                                # small: integer upscale
                z = min(box_w // sw, box_h // sh)
                if z > 1:
                    src = src.zoom(z); sw, sh = src.width(), src.height()
            base = tk.PhotoImage(width=box_w, height=box_h)       # transparent canvas
            base.tk.call(str(base), "copy", str(src), "-to",
                         max(0, (box_w - sw) // 2), max(0, (box_h - sh) // 2))
            self._imgs.append(base); self._imgs.append(src)       # retain both
            self._img_cache[key] = base
            return base
        except Exception:
            self._img_cache[key] = None
            return None

    def _console_fit(self, sysname, bw, bh):
        """Console art for `sysname` fitted to a uniform bw×bh box (or a blank box).
        Tries name then lowercased (ES-DE lowercases collection theme dirs)."""
        td = getattr(self.theme, "theme_dir", None)
        img = (self._fit_img(Path(td) / sysname / "console.png", bw, bh)
               or self._fit_img(Path(td) / sysname.lower() / "console.png", bw, bh)) if td else None
        return img or self._blank_img(bw, bh)

    def _fit_art(self, names, bw, bh):
        """First existing art among `names` (across art dirs, theme first) fitted to bw×bh."""
        for base in self._mad_art_dirs():
            for nm in names:
                img = self._fit_img(base / nm, bw, bh)
                if img is not None:
                    return img
        return None

    def _console_fit_or(self, sysname, bw, bh, fallback_names=None):
        """Console art for sysname (name then lowercased, for collection theme dirs) →
        else a fallback art (e.g. lightgun for a lightgun collection) → else a blank
        box. Always a uniform bw×bh image."""
        td = getattr(self.theme, "theme_dir", None)
        img = (self._fit_img(Path(td) / sysname / "console.png", bw, bh)
               or self._fit_img(Path(td) / sysname.lower() / "console.png", bw, bh)) if td else None
        if img is None and fallback_names:
            img = self._fit_art(fallback_names, bw, bh)
        return img or self._blank_img(bw, bh)

    def _route_est(self, key, merged):
        """Rough # of would-route rows for a system — used only to greedy-balance the Preview
        columns so the tall Cemu/Eden lists don't shove later systems off-screen."""
        ent = merged.get("systems", {}).get(key, {}) or {}
        be = ent.get("backend")
        if be in ("cemu", "eden"):
            return 8
        ports = ent.get("ports")
        if isinstance(ports, list):
            return max(1, len(ports))
        if be:
            mp = (merged.get("backends", {}).get(be, {}) or {}).get("manage_players")
            return mp if isinstance(mp, int) else 4
        return 1                                            # collection / single

    def _device_icon(self, name, target=44, vidpid=None, fallback=None):
        """A device-SPECIFIC themeable icon. Tries several filename forms of the name —
        hyphenated, flattened, and first-word — so 'X-Arcade'→xarcade.png, 'DualShock 4'
        →dualshock.png, 'Steam Deck (Steam Input)'→steamdeck.png all resolve regardless
        of how the icon was named. If `vidpid` is given it ALSO tries <vid>-<pid>.png /
        <vid>_<pid>.png — so a brand-NEW controller's icon 'just works' by dropping an
        asset named after its USB id (no code; KNOWN_PADS only supplies a friendly name).
        Guns fall back to sinden/lightgun. None if no match (no misleading generic — an
        X-Arcade must never show a gamepad)."""
        n = name.lower().split("(")[0].strip()           # drop "(Steam Input)" etc.
        forms = []
        if vidpid:                                       # USB-id assets for unknown pads
            forms += [vidpid.replace(":", "-"), vidpid.replace(":", "_")]
        for s in (n.replace(" ", "-"), n.replace(" ", "").replace("-", ""),
                  (n.split()[0] if n.split() else n)):
            if s and s not in forms:
                forms.append(s)
        cand = []
        for s in forms:
            cand += [f"icons/{s}.png", f"{s}.png"]
        if any(k in n for k in ("sinden", "lightgun", "gun")):
            cand += ["icons/sinden.png", "sinden.png", "icons/lightgun.png", "lightgun.png"]
        if fallback:                                     # generic icon LAST — specific forms win
            cand += [f"icons/{fallback}.png", f"{fallback}.png"]
        return self._mad_img(cand, target)

    def _section_icon(self, name):
        key = name.lower().replace(" ", "-").replace("/", "-")
        return self._mad_img([f"icons/{key}.png", f"{key}.png"], 30)

    def _build_sidebar(self):
        # No logo here — it was overlapping the first (Preview) button; MAD branding
        # lives on the Preview page banner instead.
        import tkinter.font as _tkfont
        _f = _tkfont.Font(font=self.font(12))
        # Uniform, square-ish nav buttons: BIG icon on top, label centred below.
        # fill="x" makes every button the same width (= sidebar) so they're all even.
        _need = max([_f.measure(n) for n, _fn in self.sections] + [128]) + 28
        self.sidebar.config(width=_need)
        # Scrollable button column — big icons overflow the screen height, so the
        # sidebar scrolls and auto-follows the selected section (see _highlight_sidebar).
        sc = tk.Canvas(self.sidebar, bg=self.c["surface"], highlightthickness=0)
        col = tk.Frame(sc, bg=self.c["surface"])
        win = sc.create_window((0, 0), window=col, anchor="nw")

        def _sbfit(_e=None):
            sc.itemconfigure(win, width=sc.winfo_width())
            sc.configure(scrollregion=sc.bbox("all"))
        col.bind("<Configure>", _sbfit)
        sc.bind("<Configure>", _sbfit)
        sc.pack(fill="both", expand=True)
        self._sb_canvas = sc
        for i, (name, _fn) in enumerate(self.sections):
            key = name.lower().replace(" ", "-").replace("/", "-")
            ic = self._mad_img([f"icons/{key}.png", f"{key}.png"], 112)  # big, visible icons
            b = gui_widgets.button(col, self.style, name, size=12,
                                   cmd=lambda x=i: self.show_section(x),
                                   image=ic, compound="top", wraplength=_need)
            b.pack(fill="x", padx=8, pady=4)
            b._mad_sidebar = True              # nav: vertical stays here; cross only via L/R
            b._mad_sidebar_idx = i
            b.bind("<FocusIn>", lambda _e: self._sidebar_browse(), add="+")  # browse = live-switch
            self._sidebar_btns.append(b)
        # Exit button removed — hold Start+Select to quit (see footer hint).

    def _highlight_sidebar(self):
        for i, b in enumerate(self._sidebar_btns):
            on = (i == self.section_idx)
            b.config(fg=self.c["accent"] if on else self.c["text"],
                     bg=self.c["row"] if on else self.c["surface"],
                     highlightbackground=self.c["accent"] if on else self.c["border"])
        self._scroll_sidebar_to_active()

    def _scroll_sidebar_to_active(self):
        """Scroll the sidebar so the selected section is visible (big icons overflow)."""
        sc = getattr(self, "_sb_canvas", None)
        if not (sc and self._sidebar_btns):
            return
        try:
            b = self._sidebar_btns[self.section_idx]
            sc.update_idletasks()
            col = b.master
            total, ch = col.winfo_height(), sc.winfo_height()
            # At startup the canvas isn't laid out yet (height ~1); scrolling then would
            # push the selected top item (Preview) off-screen. Skip until it's real.
            if ch < 60 or total <= ch:
                return
            by, bh = b.winfo_y(), b.winfo_height()
            top = sc.canvasy(0)
            if by < top:
                sc.yview_moveto(max(0, by) / total)
            elif by + bh > top + ch:
                sc.yview_moveto(min(total - ch, by + bh - ch) / total)
        except Exception:
            pass

    def switch_section(self, delta):
        self.show_section((self.section_idx + delta) % len(self.sections))

    def _sidebar_browse(self):
        """Focus landing on a sidebar button live-switches the section after a short
        settle (no A press), keeping focus on the sidebar so you can keep browsing or
        press Right to enter. Debounced so fast scrolling doesn't rebuild every step."""
        if not getattr(self, "_allow_sidebar_browse", False):
            return                               # ignore WM-assigned focus during startup
        if getattr(self, "_sb_after", None):
            try:
                self.root.after_cancel(self._sb_after)
            except Exception:
                pass
        self._sb_after = self.root.after(130, self._do_sidebar_switch)

    def _do_sidebar_switch(self):
        self._sb_after = None
        btn = self.root.focus_get()
        idx = getattr(btn, "_mad_sidebar_idx", None)
        if idx is None or idx == self.section_idx:
            return
        self.section_idx = idx
        self.stack = [self.sections[idx][1]]
        self._back_focus = []
        self._highlight_sidebar()
        self._clear(); self._render(self.sections[idx][1])   # _clear FIRST (don't stack pages)
        # Browsing only PREVIEWS the section — keep focus on the sidebar (press Right or A
        # to enter). Re-assert AFTER the builder's own focus_set + _ensure_initial_focus.
        self.root.after(60, lambda b=btn: b.focus_set() if b.winfo_exists() else None)

    def show_section(self, idx):
        for _attr in ("_sb_after", "_reload_after"):   # cancel pending browse-switch / theme reload
            if getattr(self, _attr, None):
                try:
                    self.root.after_cancel(getattr(self, _attr))
                except Exception:
                    pass
                setattr(self, _attr, None)
        self.section_idx = idx % len(self.sections)
        fn = self.sections[self.section_idx][1]
        self.stack = [fn]
        self._back_focus = []
        self._highlight_sidebar()
        self.sound.play("select")
        self._clear(); self._render(fn)

    # ---- frame helpers ----
    def _clear(self):
        if getattr(self, "_wii_after", None):    # stop the Preview Wii-Remote poll on leave
            try:
                self.root.after_cancel(self._wii_after)
            except Exception:
                pass
        self._wii_after = None
        if getattr(self, "_it_after", None):     # stop the live controller-test poll on leave
            try:
                self.root.after_cancel(self._it_after)
            except Exception:
                pass
        self._it_after = None
        if getattr(self, "_it_devs", None):     # leaving the live-input test
            self.nav.capture = None             # release the focus lock
            for _it in self._it_devs:           # close the evdev fds it opened
                try:
                    _it["dev"].close()
                except Exception:
                    pass
        self._it_devs = []
        self._bp_active = False                  # leaving the button-map page → disarm live indicators
        self._bp_dots = {}
        self._bp_cells = []
        for _aid in ("_cam_after", "_cam_led_after"):   # leaving the camera-tuning page
            if getattr(self, _aid, None):
                try:
                    self.root.after_cancel(getattr(self, _aid))
                except Exception:
                    pass
                setattr(self, _aid, None)
        if getattr(self, "_cam_proc", None) or getattr(self, "_cam_driver_paused", False):
            self._cam_kill_ffmpeg()               # stop the preview feed
            self._cam_restore_driver()            # restore pre-preview driver/LED on EVERY exit route
        self._cam_lbl = self._cam_img = None
        self._cv = self._inner = self._cv_win = None
        self._page_refresh = None        # the torn-down page no longer wants auto-refresh
        for w in self.body.winfo_children():
            w.destroy()

    def _on_devices_changed(self):
        """Nav saw a pad (dis)connect — let the current page live-refresh its
        device-dependent widgets (Preview/Players set self._page_refresh; it's
        None on other pages). Skipped mid press-to-identify (gated in _scan)."""
        if self._page_refresh:
            try:
                self._page_refresh()
            except Exception:
                pass

    def _render(self, fn, focus_idx=None):
        # Suppress the nav 'tick' for the auto-focus that happens during build.
        self._suppress_nav = True
        fn()
        self.root.after(20, lambda: self._ensure_initial_focus(focus_idx))
        self.root.after(150, lambda: setattr(self, "_suppress_nav", False))

    def _ensure_initial_focus(self, focus_idx=None):
        """Every page opens with exactly one content control focused, even if the
        page didn't focus_set() one itself (e.g. empty lists). EXCEPT while browsing
        the sidebar (focus on a section button) — don't yank focus into content.
        On a back() navigation, focus_idx restores the control the user had selected
        (e.g. the system tile they entered); _on_focus then scrolls it into view."""
        if getattr(self.root.focus_get(), "_mad_sidebar", False):
            return
        items = self.nav._content_focusables()
        if not items:
            return
        if focus_idx is not None:
            items[max(0, min(focus_idx, len(items) - 1))].focus_set()
        elif self.root.focus_get() not in items:
            items[0].focus_set()

    def _page_view(self, fwd):
        """LT/RT paging. ALWAYS scroll the content canvas by one viewport when the page
        is taller than the viewport — even if there is no focusable control below to move
        the cursor to (e.g. the read-only Preview overview, which only has buttons up top).
        Then, as a nicety, move the focus ring onto a now-visible control if there is one;
        if none is visible, just leave the view scrolled. Falls back to a multi-step focus
        jump when there's no scrollable canvas or the content already fits."""
        cv, inner = getattr(self, "_cv", None), getattr(self, "_inner", None)

        def _focus_jump():
            for _ in range(self.nav.PAGE_STEPS):
                self.nav._move(fwd)
        if not (cv and inner):
            _focus_jump()
            return
        try:
            self._refit_canvas()                 # re-measure first (async-filled pages: Preview)
            total, ch = inner.winfo_height(), cv.winfo_height()
            if total <= ch:                      # nothing to scroll → just move focus
                _focus_jump()
                return
            cv.yview_scroll(1 if fwd else -1, "pages")
            cv.update_idletasks()
            items = self.nav._content_focusables()
            top = cv.canvasy(0)
            bottom = top + ch
            base = inner.winfo_rooty()
            visible = sorted(((w.winfo_rooty() - base, w) for w in items
                              if top <= (w.winfo_rooty() - base) <= bottom),
                             key=lambda t: t[0])      # sort by y only (avoid widget cmp)
            if visible:
                # Page DOWN → focus the LOWEST visible control (drive toward the bottom);
                # page UP → the highest. Focusing the TOP-most on page-down made
                # _ensure_visible nudge the view back UP to fully reveal it, so RT looked
                # like it bounced to a top control ("Configure a system") instead of
                # reaching the bottom of the page.
                (visible[-1] if fwd else visible[0])[1].focus_set()
        except Exception:
            _focus_jump()

    def _on_focus(self, _ev=None):
        if not self._suppress_nav:
            self.sound.play("nav")
        self._ensure_visible()

    def _ensure_visible(self):
        cv, inner = getattr(self, "_cv", None), getattr(self, "_inner", None)
        w = self.root.focus_get()
        if not (cv and inner and w):
            return
        wp, ip = str(w), str(inner)
        if wp != ip and not wp.startswith(ip + "."):
            return
        try:
            cv.update_idletasks()
            total, ch = inner.winfo_height(), cv.winfo_height()
            if total <= ch:
                return
            wy = w.winfo_rooty() - inner.winfo_rooty()
            wh = w.winfo_height()
            top = cv.canvasy(0)
            if wy < top:
                cv.yview_moveto(max(0, wy) / total)
            elif wy + wh > top + ch:
                cv.yview_moveto(min(total - ch, wy + wh - ch) / total)
        except Exception:
            pass

    def _title(self, text):
        tk.Label(self.body, text=text, bg=self.c["bg"], fg=self.c["accent"],
                 font=self.font(26, bold=True)).pack(anchor="w", padx=28, pady=(20, 12))

    GLYPH_ICON = {"💾": "save", "⤴": "restore", "♻": "restore-input", "↺": "reset",
                  "▶": "play", "■": "stop", "◎": "calibrate", "⟳": "smoother",
                  "📷": "camera", "📁": "folder", "↻": "refresh", "✎": "edit",
                  "➕": "add", "●": "detect", "✔": "confirm", "✓": "confirm"}

    def _action_icon(self, slug, target=26):
        """A themeable inline action icon (icons/<slug>.png → <slug>.png)."""
        return self._mad_img([f"icons/{slug}.png", f"{slug}.png"], target)

    def _btn(self, parent, text, cmd, *, sound_event="select", **kw):
        # Auto-swap a leading action glyph (💾 ◎ ▶ …) for its themeable PNG icon,
        # so every action button gets an icon without touching each call site.
        if "image" not in kw and text:
            t = text.lstrip()
            for g, slug in self.GLYPH_ICON.items():
                if t.startswith(g):
                    img = self._action_icon(slug)
                    if img is not None:
                        kw["image"] = img
                        text = t[len(g):].strip()
                    break
        return gui_widgets.button(parent, self.style, text, cmd,
                                  sound_event=sound_event, **kw)

    def _toggle(self, parent, label, value, on_change, width=16):
        b = gui_widgets.toggle(parent, self.style, label, value, on_change, width=width)
        b.pack(side="left", padx=4)
        return b

    def _set_debug(self, v):
        """🐛 Debug toggle (Preview): when on, each rescan dumps the SDL + evdev device
        lists to controller-router/preview-devices.log for troubleshooting. Persisted in
        [gui] debug so it survives restarts."""
        self._debug = bool(v)
        set_gui_flag("debug", self._debug)

    def _lbl(self, parent, text, *, role="text", size=14, bold=False, mono=False,
             wraplength=None, justify=None, bg=None, **pk):
        # NOTE: wraplength/justify are LABEL options, NOT pack() options — they
        # must be set on the widget, never forwarded to pack() (which raises
        # TclError on them). Only geometry kwargs (anchor/pady/padx/fill/…) go to pack.
        fg = {"text": self.c["text"], "dim": self.c["text_dim"],
              "accent": self.c["accent"],
              "warn": self.c.get("warn", "#ff6b5e")}.get(role, self.c["text"])
        cfg = {}
        if wraplength is not None:
            cfg["wraplength"] = wraplength
        if justify is not None:
            cfg["justify"] = justify
        lab = tk.Label(parent, text=text, bg=(bg or self.c["bg"]), fg=fg,
                       font=self.font(size, bold=bold, mono=mono), **cfg)
        lab.pack(**pk)
        return lab

    def _textwrap(self):
        """Wrap width for under-title help text — fills the content column on
        handheld (1280) and docked (1920) alike (sidebar 200 + scroll margins ~60)."""
        return max(700, self.root.winfo_screenwidth() - 260)

    def _scroll(self):
        wrap = tk.Frame(self.body, bg=self.c["bg"]); wrap.pack(fill="both", expand=True, padx=22)
        cv = tk.Canvas(wrap, bg=self.c["bg"], highlightthickness=0)
        inner = tk.Frame(cv, bg=self.c["bg"])
        win = cv.create_window((0, 0), window=inner, anchor="nw")

        def _resize(_e=None):
            self._refit_canvas()

        inner.bind("<Configure>", _resize)
        cv.bind("<Configure>", _resize)
        # Scrollbar intentionally omitted (hidden) — scroll via LT/RT / the gamepad.
        cv.pack(side="left", fill="both", expand=True)
        self._cv, self._inner, self._cv_win = cv, inner, win
        return inner

    def _refit_canvas(self):
        """Resize the scroll canvas's inner window to fit the CURRENT content height + refresh the
        scrollregion. Width tracks the viewport; height = max(content reqheight, viewport) so short
        content stays pinned to the TOP (no centering) yet tall content scrolls.

        Must be callable on demand (not just on <Configure>): a page that fills its body
        ASYNCHRONOUSLY (Preview's background scan adds device rows after layout) keeps the inner
        frame pinned to this window item's height, so adding children fires NO <Configure> on it —
        the scrollregion would stay stale at the pre-fill height and LT/RT couldn't reach the new
        content. _preview_fit_scroll and _page_view call this to re-measure."""
        cv = getattr(self, "_cv", None)
        inner = getattr(self, "_inner", None)
        win = getattr(self, "_cv_win", None)
        if not (cv and inner and win):
            return
        try:
            cv.update_idletasks()                        # settle pending layout → fresh reqheight
            vw, vh = cv.winfo_width(), cv.winfo_height()
            cv.itemconfigure(win, width=vw, height=max(inner.winfo_reqheight(), vh))
            cv.update_idletasks()                        # apply the new window size before bbox
            cv.configure(scrollregion=cv.bbox("all"))
        except tk.TclError:
            pass

    def _content_focus_index(self):
        """Index of the currently-focused content control (or None) — captured on
        goto() so back() can restore the cursor where it was."""
        items = self.nav._content_focusables()
        try:
            return items.index(self.root.focus_get())
        except (ValueError, AttributeError):
            return None

    def goto(self, fn):
        self._back_focus.append(self._content_focus_index())   # remember cursor for back()
        self.stack.append(fn)
        self._clear(); self._render(fn)

    def back(self):
        if len(self.stack) > 1:
            self.sound.play("back")
            self.stack.pop()
            idx = self._back_focus.pop() if self._back_focus else None
            self._clear(); self._render(self.stack[-1], focus_idx=idx)

    def quit(self):
        _diag("MAD quit → ES-DE")                         # audit: why/when MAD closed (see mad-quit.log)
        self._cam_restore_driver()                       # never leave the guns dead / LED on after quit
        self.root.destroy()

    def _replace(self, fn):
        """Re-render the current page as fn WITHOUT growing the stack — for in-place option
        toggles (e.g. show-offscreen on the button-map page)."""
        if self.stack:
            self.stack[-1] = fn
        self._clear()
        self._render(fn)

    def _sinden_restart(self, status=None):
        """Stop then (re)start the Sinden driver so it re-reads the config. Detached + sequenced."""
        self._run(["bash", "-c", f"{HERE}/sinden-stop.sh; sleep 1; {HERE}/sinden-start.sh"],
                  status, "sinden-restart")
        self._cam_driver_paused = False
        if status:
            status.config(text="↻ restarting driver… (~3 s)")

    def _sinden_apply(self, status=None):
        """Apply saved button/recoil settings: restart the driver ONLY if it's currently
        running (so the change takes effect now). If it's stopped, leave it stopped — the
        config is already saved (every pick auto-writes) and will load on the next Start."""
        if self._driver_running():
            self._sinden_restart(status)
        elif status:
            status.config(text="✓ saved — driver not running (applies on next Start)")

    def _arow(self, parent):
        """A left-anchored wrapper row — so a fill='x' stepper or a side='left' toggle sizes to
        its own content instead of the full canvas width (which mixes pack sides + cascades)."""
        f = tk.Frame(parent, bg=self.c["bg"])
        f.pack(anchor="w")
        return f

    def _sinden_led(self, which):
        """Fire the TV-border LED webhook (Home-Assistant) — 'start' (border on) / 'stop' (off).
        Sources sinden.conf for the base URL + webhook IDs, same as sinden-calibrate.sh."""
        var = "SINDEN_LED_WEBHOOK_START" if which == "start" else "SINDEN_LED_WEBHOOK_STOP"
        cmd = ('. "' + str(HERE) + '/sinden.conf" 2>/dev/null; '
               '[ "${SINDEN_LED_ENABLED:-0}" = "1" ] && [ -n "${SINDEN_LED_HA_BASE:-}" ] && '
               'curl -fsS -m 3 -X POST "$SINDEN_LED_HA_BASE/api/webhook/$' + var + '" >/dev/null 2>&1')
        self._run(["bash", "-c", cmd], None, "sinden-led")

    def _driver_running(self):
        import subprocess
        try:
            return subprocess.run(["pgrep", "-f", "LightgunMono.exe"],
                                  capture_output=True, timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _cam_restore_driver(self, status=None):
        """Leaving the camera preview: restore the PRE-preview state. Driver WAS running → restart
        it (guns + border LED back). Driver was NOT running → leave it off and turn the border LED
        OFF, so tuning never leaves the LED stuck on."""
        if not getattr(self, "_cam_driver_paused", False):
            return
        if getattr(self, "_cam_driver_was_running", True):
            self._sinden_restart(status)
        else:
            self._cam_driver_paused = False
            self._sinden_led("stop")
            if status:
                status.config(text="Preview stopped — driver left off, LED off.")

    def _sinden_pause_driver(self, status=None):
        """Stop the Sinden driver so the raw gun evdev nodes are free AND the gun's normal
        aim/click can't move the cursor or click MAD widgets while a test screen is open.
        Reuses the camera page's pause flags so the existing _clear() teardown restarts the
        driver/LED to its prior state on EVERY exit route. No-op if already paused."""
        import subprocess
        if getattr(self, "_cam_driver_paused", False):
            return
        self._cam_driver_was_running = self._driver_running()   # remember, to restore on leave
        if status:
            status.config(text="Pausing driver…"); self.root.update_idletasks()
        try:
            subprocess.run([str(HERE / "sinden-stop.sh")], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
        except Exception:
            pass
        self._cam_driver_paused = True

    def _run(self, argv, status=None, label=None, interactive=False):
        """Launch an external tool detached (background daemon OR a tool that draws
        its own window), logging to ~/Emulation/storage/control-panel/<label>.log and
        updating the optional `status` label. MAD only CALLS the scripts — it never
        absorbs their logic. argv is a list (handles paths with spaces). interactive=
        True keeps the parent's stdin (for tools that read it, e.g. calibration)."""
        import subprocess
        logdir = Path.home() / "Emulation" / "storage" / "control-panel"
        nm = label or Path(argv[0]).stem
        try:
            logdir.mkdir(parents=True, exist_ok=True)
            with open(logdir / f"{nm}.log", "ab") as lf:      # closed in parent after spawn
                subprocess.Popen(argv, stdout=lf, stderr=lf,
                                 stdin=(None if interactive else subprocess.DEVNULL),
                                 start_new_session=True)
            if status:
                status.config(text=f"▶ {nm} started   (log: control-panel/{nm}.log)")
        except Exception as ex:
            if status:
                status.config(text=f"⚠ couldn't launch {nm}: {ex}")

    # ---- a reusable single-choice picker sub-page ----
    def _select_page(self, title, caption, options, on_choose):
        """options = list of (value, label). Choosing persists via on_choose then
        returns to the previous page."""
        def page():
            self._title(title)
            inner = self._scroll()
            if caption:
                self._lbl(inner, caption, role="dim", size=12, anchor="w",
                          pady=(0, 8))
            first = None
            for value, label in options:
                b = self._btn(inner, f"  {label}",
                              lambda v=value: (on_choose(v), self.back()), width=40)
                b.pack(anchor="w", pady=2)
                first = first or b
            if first:
                first.focus_set()
        self.goto(page)

    # ---- pages ----
    def lightgun(self):
        self._title("Lightgun / Sinden")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        sin_tools = Path.home() / "ROMs" / "sinden"
        self._lbl(inner, "Sinden lightgun: driver, calibration, live camera tuning, button "
                  "mapping, recoil, and pointer smoothing.",
                  role="text", size=13, anchor="w", pady=(0, 10), wraplength=self._textwrap(), justify="left")

        # two columns: actions on the LEFT, smoother + LED on the RIGHT (uses the wide screen)
        twocol = tk.Frame(inner, bg=self.c["bg"]); twocol.pack(anchor="w", fill="x")
        left = tk.Frame(twocol, bg=self.c["bg"]); left.pack(side="left", anchor="n", padx=(0, 48))
        right = tk.Frame(twocol, bg=self.c["bg"]); right.pack(side="left", anchor="n")

        self._lbl(left, "Driver", role="accent", size=14, bold=True, anchor="w", pady=(2, 2))
        r = tk.Frame(left, bg=self.c["bg"]); r.pack(anchor="w")
        self._btn(r, "▶ Start", lambda: self._run([str(HERE / "sinden-start.sh")],
                  status, "sinden-start"), width=12).pack(side="left", padx=4)
        self._btn(r, "■ Stop", lambda: self._run([str(HERE / "sinden-stop.sh")],
                  status, "sinden-stop"), width=12).pack(side="left", padx=4)
        self._btn(left, "◎  Calibrate guns (opens on-screen)",
                  lambda: self._run([str(HERE / "sinden-calibrate.sh")], status,
                                    "sinden-calibrate", interactive=True),
                  width=36).pack(anchor="w", pady=6)
        def _test_guns():
            self._run([str(HERE / "sinden-test.sh")], status, "sinden-test")
            status.config(text="Both guns active (driver + MPX up). Aim in a game, or use "
                          "Calibrate, to SEE both cursors — they don't render over this panel "
                          "in Game Mode. Stop when done.")
        self._btn(left, "Start both guns (driver + MPX)", _test_guns,
                  width=36).pack(anchor="w", pady=6)

        # Camera tuning (live preview) — one page, both guns
        self._lbl(left, "Camera", role="accent", size=14, bold=True, anchor="w", pady=(10, 2))
        self._btn(left, "📷  Camera tuning",
                  lambda: self.goto(self._camera_tune_page), width=36).pack(anchor="w", pady=4)

        # Buttons (mouse mode)
        self._lbl(left, "Buttons", role="accent", size=14, bold=True, anchor="w", pady=(10, 2))
        br = tk.Frame(left, bg=self.c["bg"]); br.pack(anchor="w")
        self._btn(br, "🎮  P1 buttons", lambda: self.goto(lambda: self._button_map_page(1)),
                  width=18).pack(side="left", padx=4)
        self._btn(br, "🎮  P2 buttons", lambda: self.goto(lambda: self._button_map_page(2)),
                  width=18).pack(side="left", padx=4)

        # Recoil & gun behavior
        self._lbl(left, "Recoil & gun behavior", role="accent", size=14, bold=True, anchor="w", pady=(10, 2))
        gr = tk.Frame(left, bg=self.c["bg"]); gr.pack(anchor="w")
        self._btn(gr, "P1 recoil & behavior", lambda: self.goto(lambda: self._gun_behavior_page(1)),
                  width=18).pack(anchor="w", pady=2)
        self._btn(gr, "P2 recoil & behavior", lambda: self.goto(lambda: self._gun_behavior_page(2)),
                  width=18).pack(anchor="w", pady=2)

        self._lbl(right, "Pointer smoother", role="accent", size=14, bold=True, anchor="w", pady=(2, 2))
        self._lbl(right, "More smoothing = steadier slow aim; less = snappier. Pick a preset, or "
                  "fine-tune below (applies instantly).",
                  role="dim", size=11, anchor="w", pady=(0, 4), wraplength=680, justify="left")
        presets = [("Off", "1.0 0.0"), ("Snappy", "0.30 0.8 500"),
                   ("Default", "0.12 1.6 1000"), ("Smooth", "0.08 2.5 1200"),
                   ("Heavy", "0.04 3.5 1500")]
        _a0, _dz0, _sn0 = sinden_cfg.smoother_get()
        _sm = {"a": _a0, "dz": _dz0, "sn": _sn0}
        _st = {}                                   # stepper handles, filled below (preset → sliders)

        def _apply_sm():
            self._run([str(HERE / "sinden-smoother-preset.sh"),
                       f"{_sm['a']:.2f}", f"{_sm['dz']:.1f}", str(int(_sm['sn']))], status, "smoother-tune")

        def _apply_preset(vals):
            parts = vals.split()
            self._run([str(HERE / "sinden-smoother-preset.sh")] + parts, status, "smoother-preset")
            _sm["a"], _sm["dz"] = float(parts[0]), float(parts[1])      # reflect the preset live …
            _st["a"]._mad_set(_sm["a"]); _st["dz"]._mad_set(_sm["dz"])  # … in the sliders below
            if len(parts) >= 3:                    # "Off" omits snap → leave that slider as-is
                _sm["sn"] = float(parts[2]); _st["sn"]._mad_set(_sm["sn"])
        pr = tk.Frame(right, bg=self.c["bg"]); pr.pack(anchor="w")
        for nm, vals in presets:
            self._btn(pr, nm, lambda v=vals: _apply_preset(v), width=9).pack(side="left", padx=3)
        _st["a"] = gui_widgets.stepper(self._arow(right), self.style, "alpha (← smoother)",
                            min(1.0, max(0.04, round(_a0, 2))),
                            lo=0.04, hi=1.0, step=0.01, fmt=lambda v: f"{v:.2f}",
                            on_change=lambda v: (_sm.update(a=v), _apply_sm()))
        _st["dz"] = gui_widgets.stepper(self._arow(right), self.style, "deadzone (jitter)",
                            min(6.0, max(0.0, round(_dz0, 1))),
                            lo=0.0, hi=6.0, step=0.1, fmt=lambda v: f"{v:.1f}",
                            on_change=lambda v: (_sm.update(dz=v), _apply_sm()))
        _st["sn"] = gui_widgets.stepper(self._arow(right), self.style, "snap threshold",
                            min(2000, max(200, int(_sn0))),
                            lo=200, hi=2000, step=50,
                            on_change=lambda v: (_sm.update(sn=v), _apply_sm()))
        # Cursor smoother ON/OFF as a stateful switch (the .smoothing-off marker = OFF; the
        # canonical "Toggle Cursor Smoother.sh" flips it, matching the switch's new position).
        _smoff = Path.home() / "Emulation" / "storage" / "sinden" / ".smoothing-off"
        self._toggle(self._arow(right), "Cursor smoother", not _smoff.exists(),
                     lambda v: self._run([str(sin_tools / "Toggle Cursor Smoother.sh")],
                                         status, "smoother-toggle"), width=22)

        # TV LED strip (Home Assistant) — toggles SINDEN_LED_ENABLED in sinden.conf.
        self._lbl(right, "TV LED strip", role="accent", size=14, bold=True, anchor="w", pady=(10, 2))
        conf = HERE / "sinden.conf"

        def _led_get():
            import re
            try:
                m = re.search(r'^\s*SINDEN_LED_ENABLED\s*=\s*(\d+)', conf.read_text(), re.M)
                return bool(m) and m.group(1) != "0"
            except Exception:
                return False

        def _led_set(v):
            import re
            try:
                t = conf.read_text()
                t2 = re.sub(r'^\s*SINDEN_LED_ENABLED\s*=\s*\d+',
                            f'SINDEN_LED_ENABLED={1 if v else 0}', t, flags=re.M)
                if t2 == t:
                    status.config(text="⚠ sinden.conf: SINDEN_LED_ENABLED line not found")
                    return
                conf.write_text(t2)
                status.config(text=f"TV LED strip {'ON' if v else 'OFF'} on driver start/stop")
            except Exception as ex:
                status.config(text=f"⚠ {ex}")
        lr = tk.Frame(right, bg=self.c["bg"]); lr.pack(anchor="w")
        self._toggle(lr, "LED strip on start/stop", _led_get(), _led_set, width=24)
        self._lbl(right, "Fires your Home-Assistant webhooks when the driver starts/stops. Base "
                  "URL + webhook IDs live in sinden.conf (edit there).", role="dim", size=11,
                  anchor="w", pady=(0, 4), wraplength=500, justify="left")

        status.pack_configure(pady=12)

    # ---- Sinden button mapping (mouse mode only — never the JoystickMode* keys) ----
    def _button_map_page(self, player, show_off=False, show_mods=False):
        self._title(f"P{player} buttons (mouse mode)")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 4))
        self._lbl(inner, "Remap each gun button. Picks save immediately; press Save to restart the "
                  "driver so they take effect.", role="text", size=12, anchor="w",
                  pady=(0, 2), wraplength=self._textwrap(), justify="left")
        live = self._driver_running()
        self._lbl(inner, ("● dots light live as you press the gun's buttons (Trigger/Pump on a click)."
                          if live else
                          "Start the driver (run a Pew-Pew game, or Start it) to see the ● live-press dots."),
                  role=("accent" if live else "dim"), size=12, anchor="w",
                  pady=(0, 6), wraplength=self._textwrap(), justify="left")
        tr = tk.Frame(inner, bg=self.c["bg"]); tr.pack(anchor="w", pady=(0, 6))
        self._toggle(tr, "Show offscreen actions", show_off,
                     lambda v: self._replace(lambda: self._button_map_page(player, v, show_mods)), width=22)
        self._toggle(tr, "Modifiers (advanced)", show_mods,
                     lambda v: self._replace(lambda: self._button_map_page(player, show_off, v)), width=22)

        def _ci(s):                                  # config value → int code (blank/garbage → 0)
            try:
                return int(s)
            except (TypeError, ValueError):
                return 0

        def _dot(parent):                            # a live-press indicator
            return tk.Label(parent, text="○", bg=self.c["bg"], fg=self.c["text_dim"],
                            font=self.font(14, bold=True), width=2)
        self._bp_dots = {}
        self._bp_cells = []
        first = None
        for base in sinden_cfg.BUTTONS:
            row = tk.Frame(inner, bg=self.c["bg"]); row.pack(anchor="w", fill="x", pady=2)
            on_code = _ci(sinden_cfg.get(sinden_cfg.key(base, player)))
            d = _dot(row); d.pack(side="left", padx=(2, 4))
            self._bp_dots[(base, "on")] = d
            self._bp_cells.append({"base": base, "kind": "on", "code": on_code,
                                   "row_code": on_code, "mod_val": 0})
            tk.Label(row, text=sinden_cfg.BUTTON_LABELS[base], bg=self.c["bg"], fg=self.c["text"],
                     font=self.font(13), width=13, anchor="w").pack(side="left")
            b = self._btn(row, sinden_cfg.label_for(sinden_cfg.get(sinden_cfg.key(base, player))),
                          lambda base=base: self.goto(lambda: self._action_pick_page(player, base, False)),
                          width=16)
            b.pack(side="left", padx=4)
            first = first or b
            if show_off:
                ko = sinden_cfg.key(base, player, offscreen=True)
                d = _dot(row); d.pack(side="left", padx=(8, 2))
                self._bp_dots[(base, "off")] = d
                self._bp_cells.append({"base": base, "kind": "off", "code": _ci(sinden_cfg.get(ko)),
                                       "row_code": on_code, "mod_val": 0})
                self._btn(row, "off: " + sinden_cfg.label_for(sinden_cfg.get(ko)),
                          lambda base=base: self.goto(lambda: self._action_pick_page(player, base, True)),
                          width=16).pack(side="left", padx=4)
            if show_mods:
                km = sinden_cfg.key(base, player, mod=True)
                mod_val = _ci(sinden_cfg.get(km, "0"))
                d = _dot(row); d.pack(side="left", padx=(8, 2))
                self._bp_dots[(base, "mod")] = d
                self._bp_cells.append({"base": base, "kind": "mod", "code": None,
                                       "row_code": on_code, "mod_val": mod_val})
                modlbl = dict(sinden_cfg.MODIFIERS).get(mod_val, "None")
                self._btn(row, "mod: " + modlbl,
                          lambda km=km, nm=base: self._select_page(
                              f"{sinden_cfg.BUTTON_LABELS[nm]} modifier", "",
                              [(str(v), lbl) for v, lbl in sinden_cfg.MODIFIERS],
                              lambda val, km=km: (sinden_cfg.backup_once(), sinden_cfg.set_many({km: val}))),
                          width=12).pack(side="left", padx=4)
        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(10, 6))
        self._btn(bar, "💾  Save", lambda: self._sinden_apply(status), width=14).pack(side="left")
        status.pack_configure(pady=8)
        self._bp_active = True                       # arm the live-press indicators for this page
        if first:
            first.focus_set()

    # ---- live-press indicators (button-map page) + global keyboard swallow ----
    # MAD ignores the keyboard for browsing (see __init__): a gun mapped to Esc/Enter etc. must
    # not navigate MAD. The same synthesized keystrokes DO feed the button-map page's per-cell ●
    # dots while it's open, so you can confirm each physical button registers and which mapping it
    # hits. Needs the driver running (it's what translates a gun press into the mapped key/click).
    def _global_key(self, e):
        if getattr(self, "_bp_active", False):
            self._bp_feed_key(e)
        return "break"                               # keyboard never navigates/activates MAD

    @staticmethod
    def _mod_held(mod_val, state):
        bit = _BP_MOD_BIT.get(mod_val)
        return bool(bit and (state & bit))

    def _bp_match(self, code, state, on):
        """Light (on) / unlight (off) every visible cell that this event matches."""
        for cell in getattr(self, "_bp_cells", ()):
            if cell["kind"] in ("on", "off"):
                hit = code is not None and cell["code"] == code
            else:                                    # modifier cell: row's action fired WITH the modifier
                hit = code is not None and cell["row_code"] == code and self._mod_held(cell["mod_val"], state)
            if hit:
                dot = self._bp_dots.get((cell["base"], cell["kind"]))
                if dot is not None:
                    try:
                        dot.config(text=("●" if on else "○"),
                                   fg=(self.c["accent"] if on else self.c["text_dim"]))
                    except tk.TclError:
                        pass                         # page torn down between event and handler

    def _bp_feed_key(self, e):
        if not getattr(self, "_bp_active", False):
            return
        press = str(e.type) == "2"                       # 2 = KeyPress
        self._bp_match(_BP_KEYSYM_CODE.get(e.keysym), e.state, press)
        # The gun may synthesize an UPPERCASE keysym for a lowercase-mapped action
        # (Shift / CapsLock) or vice-versa — also light the swapped-case cell.
        alt = e.keysym.swapcase()
        if alt != e.keysym:
            self._bp_match(_BP_KEYSYM_CODE.get(alt), e.state, press)

    def _bp_feed_mouse(self, e):
        if not getattr(self, "_bp_active", False):
            return
        self._bp_match({1: 1, 2: 2, 3: 3}.get(e.num), e.state, str(e.type) == "4")  # 4 = ButtonPress

    def _action_pick_page(self, player, base, offscreen):
        """Grouped action picker for one gun button (mouse mode). A pick saves to config + back()."""
        k = sinden_cfg.key(base, player, offscreen=offscreen)
        scope = "offscreen" if offscreen else "on-screen"
        self._title(f"{sinden_cfg.BUTTON_LABELS[base]} — {scope}")
        inner = self._scroll()
        self._lbl(inner, f"current: {sinden_cfg.label_for(sinden_cfg.get(k))}",
                  role="dim", size=12, anchor="w", pady=(0, 6))

        def choose(val):
            sinden_cfg.backup_once()
            sinden_cfg.set_many({k: str(val)})
            self.back()
        first = None
        for gname, opts in sinden_cfg.ACTION_GROUPS:
            self._lbl(inner, gname, role="accent", size=12, bold=True, anchor="w", pady=(8, 2))
            gf = tk.Frame(inner, bg=self.c["bg"]); gf.pack(anchor="w", fill="x")
            for i, (val, lbl) in enumerate(opts):
                b = self._btn(gf, lbl, lambda val=val: choose(val), width=10)
                b.grid(row=i // 8, column=i % 8, padx=2, pady=2, sticky="w")
                first = first or b
        if first:
            first.focus_set()

    # ---- Sinden recoil & gun behavior ----
    def _gun_behavior_page(self, player):
        self._title(f"P{player} recoil & behavior")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 4))
        sfx = "P2" if player == 2 else ""

        def setk(base, val):
            sinden_cfg.backup_once()
            sinden_cfg.set_many({base + sfx: val})

        def gi(base, default):
            try:
                return int(sinden_cfg.get(base + sfx) or default)
            except ValueError:
                return default
        self._toggle(self._arow(inner), "Enable recoil", sinden_cfg.get("EnableRecoil" + sfx) == "1",
                     lambda v: setk("EnableRecoil", "1" if v else "0"), width=26)
        gui_widgets.stepper(self._arow(inner), self.style, "Recoil strength", gi("RecoilStrength", 100),
                            lo=0, hi=100, step=1, on_change=lambda v: setk("RecoilStrength", v))
        self._toggle(self._arow(inner), "Auto-fire recoil (machine-gun)",
                     sinden_cfg.get("TriggerRecoilNormalOrRepeat" + sfx) == "1",
                     lambda v: setk("TriggerRecoilNormalOrRepeat", "1" if v else "0"), width=30)
        gui_widgets.stepper(self._arow(inner), self.style, "Auto recoil strength", gi("AutoRecoilStrength", 40),
                            lo=0, hi=100, step=1, on_change=lambda v: setk("AutoRecoilStrength", v))
        gui_widgets.stepper(self._arow(inner), self.style, "Auto recoil speed",
                            gi("AutoRecoilDelayBetweenPulses", 13),
                            lo=1, hi=60, step=1, on_change=lambda v: setk("AutoRecoilDelayBetweenPulses", v))
        self._lbl(inner, "Other", role="accent", size=13, bold=True, anchor="w", pady=(8, 2))
        hand = {"0": "Off", "1": "Left-handed", "2": "Right-handed"}
        self._btn(inner, f"  Handedness: {hand.get(sinden_cfg.get('GangstaSetting' + sfx) or '2', '?')}",
                  lambda: self._select_page("Handedness", "",
                          [("0", "Off"), ("1", "Left-handed"), ("2", "Right-handed")],
                          lambda v: setk("GangstaSetting", v)), width=28).pack(anchor="w", pady=2)
        self._toggle(self._arow(inner), "Offscreen reload", sinden_cfg.get("OffscreenReload" + sfx) == "1",
                     lambda v: setk("OffscreenReload", "1" if v else "0"), width=26)
        self._btn(inner, "💾  Save & apply", lambda: self._sinden_apply(status),
                  width=30).pack(anchor="w", pady=(10, 6))
        status.pack_configure(pady=8)

    # ---- Sinden camera tuning (live preview) ----
    # Driver holds the camera (UVC single opener), so previewing PAUSES the driver (guns dead
    # while tuning) and restarts it on Save / on any page-exit (see _clear + quit). Frames come
    # from ffmpeg (-update 1 PPM tmpfile) shown via Tk PhotoImage — no PIL/cv2 needed. Sliders
    # set V4L2 controls live so the feed reflects them; Save persists to the config.
    def _camera_tune_page(self):
        self._title("Camera tuning")
        inner = self._scroll()
        self._cam_after = None
        self._cam_proc = None
        self._cam_player = None
        self._cam_driver_paused = False
        self._cam_driver_was_running = False
        self._cam_led_after = None
        self._cam_img = None
        self._cam_status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 4))
        self._lbl(inner, "Aiming is OFF while tuning (the driver is paused so the camera is free). "
                  "Press a Preview button, adjust the sliders while watching the feed, then Save. "
                  "Goal: the white screen-border bright, the rest dark.",
                  role="text", size=12, anchor="w", pady=(0, 8), wraplength=self._textwrap(), justify="left")
        # video on the LEFT, controls on the RIGHT — uses the wide screen, no scrolling
        twocol = tk.Frame(inner, bg=self.c["bg"]); twocol.pack(anchor="w", fill="x")
        left = tk.Frame(twocol, bg=self.c["bg"]); left.pack(side="left", anchor="n", padx=(0, 28))
        right = tk.Frame(twocol, bg=self.c["bg"]); right.pack(side="left", anchor="n")
        holder = tk.Frame(left, width=640, height=480, bg=self.c["bg"],
                          highlightthickness=1, highlightbackground=self.c["text_dim"])
        holder.pack(anchor="w")
        holder.pack_propagate(False)                 # fixed 640×480 video box (image won't be cropped)
        self._cam_lbl = tk.Label(holder, text="( press a Preview button )",
                                 bg=self.c["bg"], fg=self.c["text_dim"], font=self.font(12))
        self._cam_lbl.pack(expand=True)
        # seed per-player slider values from the config
        self._cam_vals = {}
        for p in (1, 2):
            sfx = "P2" if p == 2 else ""

            def _i(b, d, sfx=sfx):
                try:
                    return int(sinden_cfg.get(b + sfx) or d)
                except ValueError:
                    return d
            self._cam_vals[p] = {
                "Brightness": _i("CameraBrightness", 100),
                "Contrast": _i("CameraContrast", 50),
                "auto": (sinden_cfg.get("CameraExposureAuto" + sfx) or "1") == "3",
                "Exposure": _i("CameraExposure", 80),
            }
        first = None
        for p in (1, 2):
            self._lbl(right, f"Player {p} gun  ({sinden_cfg.CAM[p]})", role="accent", size=13,
                      bold=True, anchor="w", pady=(0 if p == 1 else 10, 2))
            b = self._btn(right, f"▶  Preview P{p} gun", lambda p=p: self._cam_preview(p), width=24)
            b.pack(anchor="w", pady=2)
            first = first or b
            v = self._cam_vals[p]
            gui_widgets.stepper(self._arow(right), self.style, "Brightness", v["Brightness"], lo=0, hi=255,
                                step=1, on_change=lambda val, p=p: self._cam_set(p, "Brightness", val))
            gui_widgets.stepper(self._arow(right), self.style, "Contrast", v["Contrast"], lo=0, hi=255,
                                step=1, on_change=lambda val, p=p: self._cam_set(p, "Contrast", val))
            self._toggle(self._arow(right), "Auto exposure", v["auto"],
                         lambda val, p=p: self._cam_set(p, "auto", val), width=20)
            gui_widgets.stepper(self._arow(right), self.style, "Exposure (manual)", v["Exposure"], lo=10,
                                hi=2500, step=20, on_change=lambda val, p=p: self._cam_set(p, "Exposure", val))
        self._btn(right, "💾  Save", self._cam_save,
                  width=30).pack(anchor="w", pady=(12, 6))
        self._cam_status.pack_configure(pady=8)
        if first:
            first.focus_set()

    def _cam_kill_ffmpeg(self):
        p = getattr(self, "_cam_proc", None)
        if p:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._cam_proc = None

    def _cam_apply_live(self, player):
        dev, v = sinden_cfg.CAM[player], self._cam_vals[player]
        sinden_cfg.set_ctrl(dev, "brightness", v["Brightness"])
        sinden_cfg.set_ctrl(dev, "contrast", v["Contrast"])
        sinden_cfg.set_ctrl(dev, "auto_exposure", 3 if v["auto"] else 1)
        if not v["auto"]:
            sinden_cfg.set_ctrl(dev, "exposure_time_absolute", v["Exposure"])

    def _cam_stop_preview(self):
        """Stop the live feed (2nd press of the same Preview button, or programmatically) and
        bring the driver back (which also restores the border LED)."""
        for _aid in ("_cam_after", "_cam_led_after"):
            if getattr(self, _aid, None):
                try:
                    self.root.after_cancel(getattr(self, _aid))
                except Exception:
                    pass
                setattr(self, _aid, None)
        self._cam_kill_ffmpeg()
        self._cam_player = None
        try:
            self._cam_lbl.config(image="", text="( press a Preview button )")
        except Exception:
            pass
        self._cam_img = None
        self._cam_restore_driver(self._cam_status)    # restore pre-preview driver/LED state

    def _cam_preview(self, player):
        import subprocess
        if self._cam_player == player and self._cam_proc:   # 2nd press on the live gun → stop
            self._cam_stop_preview()
            return
        self._sinden_pause_driver(self._cam_status)   # free the cameras (guns go dead — by design)
        self._cam_kill_ffmpeg()
        self._cam_player = player
        self._cam_apply_live(player)              # so the first frame already reflects the sliders
        self._cam_tmp = Path("/tmp/mad-cam.ppm")
        try:
            self._cam_tmp.unlink()
        except OSError:
            pass
        logdir = Path.home() / "Emulation" / "storage" / "control-panel"
        logdir.mkdir(parents=True, exist_ok=True)
        try:
            self._cam_proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2",
                 "-input_format", "mjpeg", "-video_size", "640x480", "-i", sinden_cfg.CAM[player],
                 "-pix_fmt", "rgb24", "-f", "image2", "-update", "1", "-y", str(self._cam_tmp)],
                stdout=subprocess.DEVNULL, stderr=open(logdir / "sinden-preview.log", "ab"),
                start_new_session=True)
        except Exception as e:
            self._cam_status.config(text=f"⚠ ffmpeg failed: {e}")
            return
        self._cam_status.config(text=f"Preview P{player} live — adjust the sliders, then Save. "
                                "(Press the button again to stop.)")
        # Border LED on for tuning — on EVERY preview-start (P1 or P2), and DELAYED so it reliably
        # beats sinden-stop.sh's backgrounded LED-off webhook (the race that fired it only sometimes).
        # Cancelled on teardown so it can't switch the LED on after you leave.
        if self._cam_led_after:
            try:
                self.root.after_cancel(self._cam_led_after)
            except Exception:
                pass
        self._cam_led_after = self.root.after(700, lambda: self._sinden_led("start"))
        if not self._cam_after:
            self._cam_after = self.root.after(150, self._cam_tick)   # give ffmpeg a moment

    def _cam_tick(self):
        self._cam_after = None
        if not self._cam_proc:
            return
        try:
            img = tk.PhotoImage(file=str(self._cam_tmp))
            self._cam_lbl.config(image=img, text="")
            self._cam_img = img                   # keep a ref or Tk GCs it
        except Exception:
            pass                                  # frame not ready / mid-write — skip this tick
        self._cam_after = self.root.after(66, self._cam_tick)

    def _cam_set(self, player, ctrl, val):
        self._cam_vals[player][ctrl] = val
        if self._cam_player == player and self._cam_proc:       # live only while previewing this gun
            dev = sinden_cfg.CAM[player]
            if ctrl == "auto":
                sinden_cfg.set_ctrl(dev, "auto_exposure", 3 if val else 1)
                if not val:
                    sinden_cfg.set_ctrl(dev, "exposure_time_absolute", self._cam_vals[player]["Exposure"])
            elif ctrl == "Exposure":
                if not self._cam_vals[player]["auto"]:
                    sinden_cfg.set_ctrl(dev, "exposure_time_absolute", val)
            else:
                sinden_cfg.set_ctrl(dev, sinden_cfg.CAM_CTRL[ctrl], val)

    def _cam_save(self):
        sinden_cfg.backup_once()
        pairs = {}
        for p in (1, 2):
            sfx = "P2" if p == 2 else ""
            v = self._cam_vals[p]
            pairs[f"CameraBrightness{sfx}"] = v["Brightness"]
            pairs[f"CameraContrast{sfx}"] = v["Contrast"]
            pairs[f"CameraExposureAuto{sfx}"] = 3 if v["auto"] else 1
            pairs[f"CameraExposure{sfx}"] = "" if v["auto"] else v["Exposure"]
        sinden_cfg.set_many(pairs)
        self._cam_kill_ffmpeg()
        self._cam_status.config(text="Saved camera settings.")
        # Restore the driver to its PRE-tuning state — never force-start a driver that was off.
        if self._cam_driver_paused:
            self._cam_restore_driver(self._cam_status)   # restart iff it was running before tuning; else LED off
        else:
            self._sinden_apply(self._cam_status)         # never previewed → restart iff currently running

    def splash(self):
        self._title("ES-DE startup splash")
        inner = self._scroll()
        cfg = splash_cfg()
        mode = cfg.get("mode", "off")
        self._lbl(inner,
                  "Custom ES-DE boot splash, sized to the screen by the Fit mode below "
                  "(Contain/Cover/Tile). (ES-DE's binary is patched for full-screen by "
                  "esde-bigsplash-patch.sh — re-run that after an ES-DE update.) Images "
                  "only; ES-DE draws its progress bar on top. PNG/JPG auto-embedded; "
                  "SVGs used as-is.",
                  role="dim", size=12, anchor="w", wraplength=self._textwrap(), justify="left",
                  pady=(0, 6))
        self._lbl(inner, f"📁  Place your splash images here:  {ESDE_SPLASH_DIR}",
                  role="accent", size=12, anchor="w", wraplength=820, justify="left",
                  pady=(0, 12))
        first = self._btn(
            inner, f"  Mode:  {dict(SPLASH_MODES).get(mode, mode)}",
            lambda: self._select_page(
                "Splash mode", "", SPLASH_MODES,
                lambda v: (set_splash("mode", v), self.show_section(self.section_idx))),
            width=44)
        first.pack(anchor="w", pady=3)

        if mode != "off":
            fitv = cfg.get("fit", "contain")
            self._btn(inner, f"  Fit:  {dict(SPLASH_FITS).get(fitv, fitv)}",
                      lambda: self._select_page(
                          "Splash fit", "How each image is sized to the screen. Contain "
                          "shows the whole image (posters / 16:9 / 4:3); Tile repeats a "
                          "pattern.", SPLASH_FITS,
                          lambda v: (set_splash("fit", v), self.show_section(self.section_idx))),
                      width=44).pack(anchor="w", pady=3)

        if mode == "fixed_image":
            imgs = list_splash_images()
            cur = cfg.get("image", "") or "(none)"
            shown = imgs[:SPLASH_PICKER_CAP]
            cap = (f"{len(imgs)} in ~/ES-DE/splashscreens (png/jpg/svg)"
                   if len(imgs) <= SPLASH_PICKER_CAP else
                   f"showing first {SPLASH_PICKER_CAP} of {len(imgs)} — for others set "
                   f"[esde_splash].image in the config, or keep fewer files")
            self._btn(inner, f"  Image:  {cur}",
                      lambda: self._select_page("Choose splash image", cap,
                          [(n, n) for n in shown],
                          lambda v: (set_splash("image", v),
                                     self.show_section(self.section_idx))),
                      width=44).pack(anchor="w", pady=3)

        elif mode == "random_image":
            sel = cfg.get("images") or []
            n_all = len(list_splash_images())
            label = f"{len(sel)} selected" if sel else f"all {n_all}"
            self._btn(inner, f"  Pool:  {label}",
                      self._image_pool_page, width=44).pack(anchor="w", pady=3)

        first.focus_set()

    def _image_pool_page(self):
        """Multi-select which splashscreens random_image draws from (persist each
        toggle immediately; none ticked = all)."""
        def page():
            self._title("Random image pool")
            inner = self._scroll()
            imgs = list_splash_images()
            if len(imgs) > SPLASH_PICKER_CAP:
                self._lbl(inner,
                          f"Pool has {len(imgs)} images — too many to tick one-by-one "
                          f"here. Random already uses ALL of them, which is the point of "
                          f"a big pool. To curate, keep fewer files in "
                          f"~/ES-DE/splashscreens (or edit [esde_splash].images).",
                          role="dim", size=13, anchor="w", wraplength=self._textwrap(),
                          justify="left", pady=(2, 8))
                return
            self._lbl(inner, f"Tick which images the random splash may pick. "
                      f"None ticked = all {len(imgs)}.", role="dim", size=12,
                      anchor="w", wraplength=self._textwrap(), justify="left", pady=(0, 8))
            sel = set(splash_cfg().get("images") or [])
            first = None
            for n in imgs:
                b = gui_widgets.toggle(inner, self.style, n, n in sel,
                                       lambda v, name=n: toggle_splash_image(name, v),
                                       width=44, size=13)
                b.pack(anchor="w", pady=1)
                first = first or b
            if first:
                first.focus_set()
        self.goto(page)

    def preview(self):
        self._title("Live routing preview")
        inner = self._scroll()
        # Static shell — the Refresh button keeps focus and never flashes; the body's
        # rows update INCREMENTALLY (see _preview_render), so device changes don't flash.
        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(0, 4))
        rb = self._btn(bar, "↻  Refresh", lambda: self._preview_rescan())
        rb.pack(side="left", padx=(0, 8))
        self._btn(bar, "◎  Identify X-Arcade", self._identify_xarcade).pack(side="left", padx=(0, 8))
        self._btn(bar, "✖  Clear", self._clear_xarcade).pack(side="left")
        self._debug = bool(localpolicy.load(LOCAL).get("gui", {}).get("debug", False))
        self._toggle(bar, "🐛 Debug", self._debug, self._set_debug)
        xport = (load_merged().get("hardware") or {}).get("xarcade_port", "")
        self._xa_status = self._lbl(
            inner, f"X-Arcade = USB port {xport}" if xport
            else "X-Arcade: not identified — press Identify (045e pads shown as Xbox 360 until then)",
            role="dim", size=12, anchor="w", pady=(0, 12))
        rb.focus_set()
        self._preview_body = tk.Frame(inner, bg=self.c["bg"])
        self._preview_body.pack(anchor="w", fill="x")
        self._preview_build()                        # one-time scaffolding + first (threaded) scan
        # Evdev pad (dis)connect → incremental in-place diff (no rebuild, no flash). (The earlier
        # "rebuild to avoid a crash" was chasing a ghost — the real cause of the apparent crash was
        # MAD's hold-Start-to-quit firing from the FC30's Start=connect button, now Start+Select.)
        self._page_refresh = lambda: self._preview_rescan()
        # Wii-Remote count has no evdev event → poll the active probe every ~2s WHILE THIS
        # PAGE IS UP and sync in place on a change. _clear() cancels it the moment you leave.
        self._wii_after = self.root.after(2000, self._wii_poll)

    def _preview_build(self):
        """One-time scaffolding of the Preview body: persistent two columns, the controller
        container + Wii label, and a per-system row (console art + name + an empty 'slot').
        Console art / system rows are built ONCE and never recreated — only the dynamic
        bits (controller rows, Wii count, per-system slot) update in _preview_render, so a
        device change never flashes the page."""
        body = self._preview_body
        merged = load_merged()
        twocol = tk.Frame(body, bg=self.c["bg"]); twocol.pack(anchor="w", fill="x")
        left = tk.Frame(twocol, bg=self.c["bg"]); left.pack(side="left", anchor="n", padx=(0, 16))
        right = tk.Frame(twocol, bg=self.c["bg"]); right.pack(side="left", anchor="n")

        # LEFT — connected controllers (rows added/removed/updated incrementally)
        self._lbl(left, "Connected controllers (SDL order):", role="accent",
                  size=14, bold=True, anchor="w", pady=(2, 6))
        self._ctrl_box = tk.Frame(left, bg=self.c["bg"]); self._ctrl_box.pack(anchor="w", fill="x")
        self._ctrl_rows = {}                         # sdl_index -> (row_frame, text_label, vidpid)
        self._ctrl_none = self._lbl(self._ctrl_box, "  (none detected)", anchor="w")
        self._ctrl_none.pack_forget()                # shown only if the scan finds nothing
        self._ctrl_loading = self._lbl(self._ctrl_box, "  Scanning controllers…", anchor="w")
        dbrow = tk.Frame(left, bg=self.c["bg"]); dbrow.pack(anchor="w", fill="x", pady=(6, 2))
        dbic = self._device_icon("dolphinbar", 80)
        if dbic:
            tk.Label(dbrow, image=dbic, bg=self.c["bg"]).pack(side="left", padx=(4, 10))
        self._wm_label = tk.Label(dbrow, text="DolphinBar Wii Remotes: …", bg=self.c["bg"],
                                  fg=self.c["text"], font=self.font(12, mono=True), anchor="w")
        self._wm_label.pack(side="left")

        # RIGHT — would-route: one PERSISTENT row per system (console art + name + slot);
        # only each system's `slot` content is rebuilt, and only when its routing changes.
        self._lbl(right, "Would route (read-only preview):", role="accent",
                  size=14, bold=True, anchor="w", pady=(2, 6))
        self._route_slots = {}                       # key -> slot frame (dynamic content)
        self._route_last = {}                        # key -> last (kind, data) for diffing
        grid = tk.Frame(right, bg=self.c["bg"]); grid.pack(anchor="w", fill="x")
        esde = self._esde_systems()
        # Mirror the Priority page: standalone-backend systems + Priority-configured RetroArch
        # systems + configured collections, so the preview shows everything that gets routed.
        sysxml = es_systems.load_systems()
        items = []; seen = set()                      # (key, label, art_system | None)
        for sysname in backend_systems(merged):
            if esde and sysname not in esde:          # skip configured-but-no-games (xbox, model3…)
                continue
            if sysname not in seen:
                seen.add(sysname); items.append((sysname, sysname, sysname))
        for s in sorted(merged.get("systems", {})):
            ent = merged["systems"][s]
            if not (isinstance(ent, dict) and ent.get("ports")) or s in seen:
                continue
            if es_systems.is_standalone(es_systems.default_command(s, sysxml)):
                continue                              # standalone ones came from backend_systems
            seen.add(s); items.append((s, s, s))
        cfg_c = merged.get("collections", {})
        for c in collections.enabled_collections():
            if isinstance(cfg_c.get(c), dict) and cfg_c[c].get("ports") and c not in seen:
                seen.add(c); items.append((c, f"▣ {c}", None))
        # Independent, greedy-balanced columns: each system drops into the SHORTEST column, so a
        # tall slot list (Cemu/Eden = 8 slots) only grows its own column instead of inflating a
        # whole grid row — that row-alignment waste was pushing the last systems (snes / Pew-Pew)
        # off-screen. 3 columns on a wide screen, 2 on a narrow one.
        # SINGLE column: each system STACKS as  [art] NAME  then its device rows full-width below
        # (was a 2-col grid with the device list indented to the right of the icon, which clipped
        # long device/profile names off the screen edge). One tall column gives the rows full width
        # AND makes the page taller than the viewport so LT/RT vertical scroll shows everything.
        ncols = 1
        cols = []
        for _ in range(ncols):
            cf = tk.Frame(grid, bg=self.c["bg"]); cf.pack(side="left", anchor="n", padx=(0, 28))
            cols.append(cf)
        colh = [0] * ncols
        for key, label, art in items:
            c = min(range(ncols), key=lambda j: colh[j])   # shortest column wins
            colh[c] += 3 + self._route_est(key, merged)    # +3 ≈ icon + title baseline
            block = tk.Frame(cols[c], bg=self.c["bg"]); block.pack(anchor="w", pady=(4, 8))
            req_sinden = art is None and bool(cfg_c.get(key, {}).get("require_sinden"))
            if art:
                img = self._console_fit(art, 80, 52)
            else:                                     # collection → its own logo, gun fallback
                img = self._console_fit_or(key, 80, 52,
                                           ["lightgun.png"] if req_sinden else ["controllers.png"])
            header = tk.Frame(block, bg=self.c["bg"]); header.pack(anchor="w")
            tk.Label(header, image=img, bg=self.c["bg"]).pack(side="left", padx=(0, 10), anchor="n")
            tk.Label(header, text=label, bg=self.c["bg"], fg=self.c["accent"],
                     font=self.font(13, mono=True), anchor="w").pack(side="left", anchor="w")
            if req_sinden:
                tk.Label(block, text="requires Sinden gun", bg=self.c["bg"],
                         fg=self.c["text_dim"], font=self.font(11), anchor="w").pack(anchor="w")
            slot = tk.Frame(block, bg=self.c["bg"]); slot.pack(anchor="w", pady=(2, 0))   # device rows, full-width
            self._route_slots[key] = slot
        self._preview_merged = merged                # system set + policy fixed for this visit
        self._sdl_mac = {}                           # SDL index -> BT MAC (for inline battery)
        self._preview_rescan()                       # initial fill via a background scan

    def _pad_label(self, d):
        """Display name — port-aware for 045e:02a1 (the X-Arcade in Xbox mode shares this id
        with a real Xbox 360 pad). ONLY the device at the user-IDENTIFIED USB port is shown
        as 'X-Arcade'; every other 045e shows 'Xbox 360' (the KNOWN_PADS default). Not
        identified → nothing is the X-Arcade."""
        if d.vidpid == "045e:02a1":
            xport = getattr(self, "_xarcade_port", "")
            if xport and getattr(self, "_sdl_port", {}).get(d.index) == xport:
                return "X-Arcade"
        if d.vidpid == "28de:11ff":
            # In Game Mode the Deck's built-in controls appear as a 28de:11ff virtual gamepad
            # whose SDL name varies between sessions ("Steam Deck Controller" or "Microsoft
            # X-Box 360 pad N"). It's the only 11ff in our setup (real pads enumerate as their
            # own vid:pid), so always show it as the Deck. _preview_render collapses dupes.
            return "Steam Deck"
        return KNOWN_PADS.get(d.vidpid, d.name)

    def _ctrl_row_text(self, d):
        # Battery shown INLINE (next to the device). Keyed by MAC via _sdl_mac; pads that
        # expose no battery (8BitDo, wired) get nothing. ⚡=charging, ⚠=low.
        batt = ""
        mac = getattr(self, "_sdl_mac", {}).get(d.index)
        if mac:
            pct, st = battery_pct(mac)
            if pct is not None:
                batt = f"  🔋{pct}%" + (" ⚡" if st == "Charging" else (" ⚠" if pct <= 20 else ""))
        return f"SDL-{d.index}  {d.vidpid}  {self._pad_label(d)}{batt}"

    def _identify_xarcade(self):
        """Press-to-identify: capture the next pad button → store its USB port as the
        X-Arcade's ([hardware].xarcade_port in local.toml), so it's told apart from real
        Xbox 360 pads (same 045e:02a1). Reuses the nav capture used by Players' Identify."""
        self._xa_status.config(text="Press a button on your X-Arcade…")
        self.nav._held = set()
        def grab(held, dev=None):
            self.nav.capture = None
            port = ""
            try:
                if dev is not None:
                    cur = [x for x in enumerate_devices() if x.path == dev.path]
                    port = port_of(cur[0].phys if cur else getattr(dev, "phys", ""))
            except Exception:
                port = ""
            if not port:
                self._xa_status.config(text="Couldn't read a USB port for that pad — use the wired X-Arcade.")
                return
            data = localpolicy.load(LOCAL)
            data.setdefault("hardware", {})["xarcade_port"] = port
            localpolicy.dump(LOCAL, data)
            self._xa_status.config(text=f"X-Arcade set to USB port {port}.")
            self._preview_rescan()
        self.nav.capture = grab

    def _clear_xarcade(self):
        data = localpolicy.load(LOCAL)
        if (data.get("hardware") or {}).pop("xarcade_port", None) is not None:
            if not data.get("hardware"):
                data.pop("hardware", None)
            localpolicy.dump(LOCAL, data)
        self._xa_status.config(text="X-Arcade port cleared — 045e pads shown as Xbox 360 until you Identify.")
        self._preview_rescan()

    def _preview_rescan(self):
        """Run the slow (~3-4s) device scan OFF the main thread, then render via the UI queue
        so MAD/Preview never freezes on it. A token guards stale results (page left, or a
        newer rescan superseded this one). Only ONE scan worker runs at a time: a (dis)connect
        burst sets _preview_rescan_pending so exactly one more scan runs after the current one —
        this coalesces a (dis)connect burst into one extra scan instead of piling up workers."""
        if not getattr(self, "_preview_body", None) or not self._preview_body.winfo_exists():
            return
        self._preview_token = getattr(self, "_preview_token", 0) + 1
        token = self._preview_token
        if getattr(self, "_preview_scanning", False):
            self._preview_rescan_pending = True       # coalesce → one fresh scan after this one
            return
        self._preview_scanning = True
        def worker():
            try:
                scan = (sdl_devices(), enumerate_devices(),
                        dolphinbar_wiimotes(active=True) if dolphinbar_present() else 0)
            except Exception:
                scan = ([], [], 0)
            if getattr(self, "_debug", False):   # 🐛 Debug toggle → dump device lists for inspection
                try:
                    import os as _os
                    _p = _os.path.expanduser("~/Emulation/storage/controller-router/preview-devices.log")
                    with open(_p, "w") as _f:
                        _f.write("=== sdl_devices() — the source of the Preview's controller rows ===\n")
                        for _s in scan[0]:
                            _f.write(f"  SDL-{_s.index}  {_s.vidpid}  {_s.name}\n")
                        _f.write("=== enumerate_devices() — raw evdev (joypad / 28de / 045e only) ===\n")
                        for _d in (scan[1] or []):
                            if getattr(_d, "is_joypad", False) or _d.vid in (0x28DE, 0x045E):
                                _f.write(f"  {_d.vid:04x}:{_d.pid:04x}  jp={_d.is_joypad} "
                                         f"sind={_d.is_sinden} virt={getattr(_d,'is_steam_virtual',False)}  "
                                         f"{_d.name!r}  phys={getattr(_d,'phys','')!r}\n")
                except Exception:
                    pass
            self._ui_q.put(lambda: self._preview_render_done(scan, token))   # main thread runs it
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _preview_render_done(self, scan, token):
        """Main-thread completion of a rescan worker: clear the in-flight flag, render, then run
        one more scan if a (dis)connect arrived while this one was working (coalesced burst)."""
        self._preview_scanning = False                # cleared FIRST so a render error can't strand it
        self._preview_render(scan, token)
        if getattr(self, "_preview_rescan_pending", False):
            self._preview_rescan_pending = False
            self._preview_rescan()

    def _preview_render(self, scan, token):
        """Apply a finished scan: incremental diff of controller rows (inline battery), the
        Wii count, and each would-route slot whose routing changed. No body rebuild → no
        flash. No-op if stale. `scan` = (sdl_devs, evdev_devs_or_None, wii); evdev=None (the
        Wii poll) keeps the existing SDL→MAC map so battery doesn't blink off."""
        if token != getattr(self, "_preview_token", 0):
            return
        if not getattr(self, "_preview_body", None) or not self._preview_body.winfo_exists():
            return
        devs, evdevs, wm = scan
        # The Deck's built-in controls appear as a 28de:11ff virtual gamepad whose SDL name
        # varies between sessions, and switching a controller's mode can spawn extra 11ff
        # ghosts. Collapse ALL 28de:11ff to ONE entry (the Deck) so neither the relabel nor
        # the ghosts break the list.
        _seen11ff = False
        _kept = []
        for _d in devs:
            if _d.vidpid == "28de:11ff":
                if _seen11ff:
                    continue
                _seen11ff = True
            _kept.append(_d)
        devs = _kept
        self._preview_cache = (devs, wm)
        self._wii_poll_n = wm
        merged = getattr(self, "_preview_merged", None) or load_merged()
        if evdevs is not None:                       # full rescan → rebuild SDL→MAC + SDL→port
            self._sdl_mac = {}
            self._sdl_port = {}
            self._xarcade_port = (load_merged().get("hardware") or {}).get("xarcade_port", "")
            for d in evdevs:
                if getattr(d, "is_joypad", False) and not getattr(d, "is_sinden", False):
                    idx = sdl_index_of(d, evdevs, devs)
                    if idx is None:
                        continue
                    if getattr(d, "uniq", "") or "":
                        self._sdl_mac[idx] = d.uniq
                    p = port_of(getattr(d, "phys", "") or "")
                    if p:
                        self._sdl_port[idx] = p
        if getattr(self, "_ctrl_loading", None) and self._ctrl_loading.winfo_exists():
            self._ctrl_loading.destroy(); self._ctrl_loading = None

        if self._wm_label.winfo_exists():
            self._wm_label.config(text=f"DolphinBar Wii Remote{'s' if wm > 1 else ''}: {wm}")

        # ── controllers diff (keyed by SDL index) ──
        want = {d.index: d for d in devs}
        changed = False
        for idx in list(self._ctrl_rows):
            if idx not in want:                      # device gone
                self._ctrl_rows[idx][0].destroy(); del self._ctrl_rows[idx]; changed = True
        for idx, d in want.items():
            label = self._pad_label(d)               # port-aware (X-Arcade vs Xbox 360)
            existing = self._ctrl_rows.get(idx)
            if existing and existing[2] == label:    # same device + same label → text only (battery)
                existing[1].config(text=self._ctrl_row_text(d))
                continue
            if existing:                             # device changed OR label flipped → rebuild (icon!)
                existing[0].destroy()
            row = tk.Frame(self._ctrl_box, bg=self.c["bg"])   # packed below, in index order
            ic = self._device_icon(label, 80, vidpid=d.vidpid, fallback="genericgamepad")
            if ic:
                tk.Label(row, image=ic, bg=self.c["bg"]).pack(side="left", padx=(4, 10))
            tlab = tk.Label(row, text=self._ctrl_row_text(d), bg=self.c["bg"],
                            fg=self.c["text"], font=self.font(12, mono=True), anchor="w")
            tlab.pack(side="left")
            self._ctrl_rows[idx] = (row, tlab, label); changed = True
        if changed:                                  # re-pack rows in SDL order (widgets persist)
            for idx in sorted(self._ctrl_rows):
                self._ctrl_rows[idx][0].pack_forget()
                self._ctrl_rows[idx][0].pack(anchor="w", fill="x", pady=3)
            if self._ctrl_rows:
                self._ctrl_none.pack_forget()
            else:
                self._ctrl_none.pack(anchor="w")

        # ── would-route diff (system set fixed; rebuild a slot only when its routing changed) ──
        for sysname, slot in self._route_slots.items():
            if not slot.winfo_exists():
                continue
            new = self._preview_route(sysname, merged, devs, wm)
            if new == self._route_last.get(sysname):
                continue
            self._route_last[sysname] = new
            for w in slot.winfo_children():
                w.destroy()
            kind, data = new
            if kind == "pads":
                for _r in data:
                    plabel, dname = _r[0], _r[1]
                    iconnm = _r[2] if len(_r) > 2 else dname   # 3rd elem = icon name (≠ display)
                    pr = tk.Frame(slot, bg=self.c["bg"]); pr.pack(anchor="w")
                    tk.Label(pr, text=f"{plabel} = ", bg=self.c["bg"], fg=self.c["text_dim"],
                             font=self.font(12, mono=True)).pack(side="left")
                    di = self._device_icon(iconnm, 96, fallback="genericgamepad")
                    if di:
                        tk.Label(pr, image=di, bg=self.c["bg"]).pack(side="left", padx=(0, 4))
                    tk.Label(pr, text=dname, bg=self.c["bg"], fg=self.c["text"],
                             font=self.font(12, mono=True)).pack(side="left")
            else:
                tk.Label(slot, text=data, bg=self.c["bg"], fg=self.c["text"],
                         font=self.font(12, mono=True), anchor="w",
                         wraplength=440, justify="left").pack(anchor="w")
        # Content height changed (rows added/removed) → refresh the scroll region so the
        # page can scroll to all of it (the "can't scroll down" report). after_idle so the
        # layout is settled first. LT/RT page-scrolls; D-pad has no focusable below Refresh.
        self.root.after_idle(self._preview_fit_scroll)

    def _preview_fit_scroll(self):
        # Re-grow the inner window to the now-taller content (async device-row fill) so the
        # scrollregion covers it — just setting scrollregion=bbox wasn't enough because bbox
        # equalled the STALE pinned window height. See _refit_canvas.
        self._refit_canvas()

    def _wii_poll(self):
        """Every ~2s while Preview is up, re-probe the Wii count on a BACKGROUND thread (the
        ~0.27s active probe must not hitch the UI); _wii_apply applies a change in place.
        Only while this page shows; _clear() cancels it on leave. Skipped mid press-to-id."""
        self._wii_after = None
        if self.nav.capture is not None:             # mid press-to-identify → retry later
            self._wii_after = self.root.after(2000, self._wii_poll)
            return
        def worker():
            try:
                n = dolphinbar_wiimotes(active=True) if dolphinbar_present() else 0
            except Exception:
                n = None
            self._ui_q.put(lambda: self._wii_apply(n))   # main thread runs it
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _wii_apply(self, n):
        if not getattr(self, "_preview_body", None) or not self._preview_body.winfo_exists():
            return                                   # left Preview → stop the poll
        self._wii_after = self.root.after(2000, self._wii_poll)   # keep polling
        if n is None or n == getattr(self, "_wii_poll_n", n):
            return
        devs = self._preview_cache[0] if getattr(self, "_preview_cache", None) else []
        self._preview_cache = (devs, n)
        self._wii_poll_n = n
        # evdevs=None → keep _sdl_mac (battery stays); just the Wii label + dolphin route line.
        self._preview_render((devs, None, n), getattr(self, "_preview_token", 0))

    def _class_token(self, d):
        """Connected pad → the Priority-page class name (X-Arcade / Xbox / DualSense /
        8BitDo / Steam Deck / Wii Remote Pro), mirroring _pad_label + the router, so the
        preview can resolve a system/collection's `ports` priority list to real pads."""
        lbl = self._pad_label(d)
        if lbl == "X-Arcade":             return "X-Arcade"
        if lbl.startswith("Xbox"):        return "Xbox"
        if lbl in ("DualSense", "DualShock 4"): return "DualSense"
        if lbl.startswith("8BitDo"):      return "8BitDo"
        if lbl == "Steam Deck":           return "Steam Deck"
        if lbl in ("Wii U Pro", "Wii Remote Pro"): return "Wii Remote Pro"
        return lbl

    def _preview_route(self, sysname, merged, devs, wm=0):
        """Returns ('text', msg) or ('pads', [(playerLabel, deviceName), ...]). `sysname`
        may be a standalone system, a RetroArch system, OR a configured collection."""
        ent = (merged.get("systems", {}).get(sysname)
               or merged.get("collections", {}).get(sysname) or {})
        be = ent.get("backend")
        if be in ("cemu", "eden", "rpcs3", "pcsx2"):
            return self._standalone_profile_preview(be, merged, devs)
        if be == "dolphin":
            if not dolphinbar_present():
                return ("text", "⚠ no DolphinBar connected")
            if not _dolphinbar_slot_nodes():
                # USB-present but no hidraw slots = the bar half-enumerated (common on
                # a deep hub chain) — re-plugging its USB fixes it. Without this the
                # "press a button" note below misdirects (the remotes aren't the issue).
                return ("text", "⚠ DolphinBar connected but exposing 0 slots — re-plug its USB")
            n = wm                                  # cached count (no repeated 0.8s poll)
            return ("text", f"DolphinBar: {n} Wiimote{'s' if n > 1 else ''}")
        if be and be != "retroarch":
            # standalone backend → vid:pid pad_classes
            bcfg = merged.get("backends", {}).get(be or "", {})
            classes = list(bcfg.get("pad_classes", []))
            if be == "cemu":
                classes = list(bcfg.get("templates", {}).keys())
            prio = {c: i for i, c in enumerate(classes)}
            ps = sorted((d for d in devs if d.vidpid in prio),
                        key=lambda d: (prio[d.vidpid], d.index))
            if not ps:
                hh = bcfg.get("handheld_class") or bcfg.get("handheld_profile")
                return ("text", f"(no player pad → {('handheld: '+str(hh)) if hh else 'unchanged'})")
            picks = [self._pad_label(d) for d in ps[:4]]   # port-aware: X-Arcade vs Xbox 360
            return ("pads", [(f"P{i+1}", nm) for i, nm in enumerate(picks)])
        # RetroArch system OR collection → resolve the ports priority (class names) to pads
        ports = ent.get("ports") or []
        if not ports:
            return ("text", "(not configured)")
        used = set(); pads = []
        for plist in ports:
            chosen = None
            for cls in plist:
                for d in devs:
                    if d.index in used:
                        continue
                    if self._class_token(d) == cls:
                        chosen = d; used.add(d.index); break
                if chosen:
                    break
            if chosen:
                pads.append((f"P{len(pads) + 1}", self._pad_label(chosen)))
        if not pads:
            return ("text", "(no matching pad connected)")
        return ("pads", pads)

    def _standalone_profile_preview(self, be, merged, devs=None):
        """Read-only Preview for hands-off standalone backends (cemu/eden/rpcs3/pcsx2): the profile loaded on
        each player slot + its device, read from the ACTIVE config files. Profile name (if
        chosen) comes from [backends.<be>].slot_profiles; the device is read live from the
        slot file so it can't lie. MAD never reads/writes the named profile files here."""
        import os
        import re
        bcfg = merged.get("backends", {}).get(be, {})
        sp = bcfg.get("slot_profiles", {}) or {}
        rows = []   # (slot label, display text, icon-device name) → rendered with a pad icon
        if be == "cemu":
            cdir = os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
            for s in range(8):
                dev = ""
                try:
                    txt = open(os.path.join(cdir, f"controller{s}.xml"),
                               encoding="utf-8", errors="replace").read()
                    md = re.search(r"<display_name>([^<]*)</display_name>", txt)
                    dev = md.group(1).strip() if md else ""
                except OSError:
                    pass
                prof = sp.get(str(s))
                if not (dev or prof):
                    continue
                short = self._short_dev(dev)
                rows.append((f"C{s + 1}", prof or short or "(empty)", short or "genericgamepad"))
        elif be == "eden":
            try:
                body = open(os.path.expanduser(bcfg.get("config_file", "~/.config/eden/qt-config.ini")),
                            encoding="utf-8", errors="replace").read()
            except OSError:
                body = ""
            for p in range(8):
                conn = re.search(rf"player_{p}_connected=(\w+)", body)
                connected = bool(conn and conn.group(1) == "true")
                prof = sp.get(str(p))
                if not (connected or prof):
                    continue
                dev = ""
                mg = re.search(rf'player_{p}_button_a="[^"]*guid:([0-9a-fA-F]{{32}})', body)
                if mg:
                    g = mg.group(1)
                    try:
                        vid = int(g[10:12] + g[8:10], 16)
                        pid = int(g[18:20] + g[16:18], 16)
                        dev = KNOWN_PADS.get(f"{vid:04x}:{pid:04x}", f"{vid:04x}:{pid:04x}")
                    except ValueError:
                        dev = ""
                rows.append((f"P{p + 1}", prof or dev or ("on" if connected else "off"),
                             dev or "genericgamepad"))
        elif be == "rpcs3":
            # PS3 — read RPCS3's global input yml; show every non-Null player + its device.
            try:
                body = open(os.path.expanduser(bcfg.get(
                    "config_file", "~/.config/rpcs3/input_configs/global/Default.yml")),
                    encoding="utf-8", errors="replace").read()
            except OSError:
                body = ""
            for p in range(1, 8):
                blk = re.search(rf"Player {p} Input:\n(.*?)(?=\nPlayer \d+ Input:|\Z)", body, re.S)
                if not blk:
                    continue
                mh = re.search(r'Handler:\s*"?([^"\n]+?)"?\s*$', blk.group(1), re.M)
                md = re.search(r'Device:\s*"?([^"\n]*?)"?\s*$', blk.group(1), re.M)
                handler = mh.group(1).strip() if mh else ""
                dev = md.group(1).strip() if md else ""
                if not handler or handler == "Null":
                    continue
                rows.append((f"P{p}", dev or handler, self._short_dev(handler)))
        elif be == "pcsx2":
            # PS2 — PCSX2 binds each pad to an SDL *index* (no stable device identity, unlike
            # RPCS3's by-name), so resolve each [PadN]'s bound index to the live device, but only
            # NAME it when that device is a PlayStation-class pad. Otherwise the configured pad
            # isn't connected at that index right now (it's unplugged, or that index currently
            # holds a gun/other device) — naming it would be misleading.
            try:
                body = open(os.path.expanduser(bcfg.get(
                    "config_file", "~/.config/PCSX2/inis/PCSX2.ini")),
                    encoding="utf-8", errors="replace").read()
            except OSError:
                body = ""
            classes = set(bcfg.get("pad_classes", []))
            sdl_by_idx = {d.index: d for d in (devs or [])}
            for m in re.finditer(r"\[Pad(\d+)\]\n(.*?)(?=\n\[|\Z)", body, re.S):
                pn, blk = m.group(1), m.group(2)
                mt = re.search(r"Type\s*=\s*(\S+)", blk)
                typ = mt.group(1) if mt else ""
                if not typ or typ == "None":
                    continue
                ms = re.search(r"SDL-(\d+)/", blk)
                sd = sdl_by_idx.get(int(ms.group(1))) if ms else None
                if sd and sd.vidpid in classes:
                    nm = KNOWN_PADS.get(sd.vidpid, sd.name)
                    rows.append((f"P{pn}", nm, self._short_dev(nm)))
                else:
                    where = f" (SDL-{ms.group(1)})" if ms else ""
                    rows.append((f"P{pn}", f"PlayStation pad not connected{where}",
                                 "genericgamepad"))
        if not rows:
            return ("text", "hands-off — uses the emulator's own config")
        return ("pads", rows)

    @staticmethod
    def _short_dev(name):
        """Short label for a Cemu <display_name> (raw evdev names are long → blob)."""
        n = (name or "").lower()
        if "wii" in n and "pro" in n:
            return "Wii U Pro"
        if "dualsense" in n:
            return "DualSense"
        if "dualshock" in n or "ps4" in n:
            return "DualShock 4"
        if "360" in n or "xbox" in n:
            return "Xbox 360"
        if "steam deck" in n:
            return "Steam Deck"
        return name[:16] if name else ""

    # ---- players (device pins: global baseline + per-system overrides) ----
    _PIN_BADGE = {"uniq":   "✓ MAC",
                  "port":   "⚠ USB-port",
                  "vidpid": "⚠ model-only"}
    _PLAYER_SLOTS = 8

    def _players_systems(self, merged):
        """Routable systems you actually have games for (per-system pin scope)."""
        esde = self._esde_systems()
        return sorted(s for s in backend_systems(merged) if not esde or s in esde)

    @staticmethod
    def _pins_summary(pins):
        return ("P" + ",".join(sorted((str(k) for k in pins), key=lambda x: int(x)))
                if pins else "(none)")

    def players(self):
        self._title("Players — pin a pad")
        inner = self._scroll()
        self._lbl(inner, "Pin a pad to a player so it stays that player across reconnects. Identify a "
                  "slot, press a button on the pad, then Save.",
                  role="text", size=12, anchor="w", pady=(0, 6), wraplength=self._textwrap(), justify="left")
        self._lbl(inner, "Pin types —  ✓ MAC = port-agnostic (survives reconnects)  ·  ⚠ USB-port = re-pin "
                  "if moved to another port  ·  ⚠ model-only = can't tell two of the same model apart.",
                  role="dim", size=11, anchor="w", pady=(0, 6), wraplength=self._textwrap(), justify="left")
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))

        # Left = global pins (8 slots); right column = connected pads (top) then per-system overrides.
        rightcol = self._players_editor(inner, None, status)

        merged = load_merged()
        self._lbl(rightcol, "Per-system overrides (win over global)", role="accent", size=14,
                  bold=True, anchor="w", pady=(18, 2))
        overridden = [s for s in self._players_systems(merged)
                      if merged.get("systems", {}).get(s, {}).get("pins")]
        if overridden:
            self._tile_grid(
                rightcol,
                [(s, s, self._pins_summary(merged["systems"][s]["pins"])) for s in overridden],
                lambda s: self.goto(lambda: self._players_sys_detail(s)),
                cols=2)
        else:
            self._lbl(rightcol, "  (none — every system uses the global pins)",
                      role="dim", size=12, anchor="w")
        self._btn(rightcol, "➕ Add per-system pins",
                  lambda: self.goto(self._players_add_picker), width=24).pack(anchor="w", pady=(10, 4))

    def _players_sys_detail(self, sysname):
        self._title(f"Pins: {sysname}")
        inner = self._scroll()
        self._lbl(inner, f"Per-system pins for {sysname} — these OVERRIDE the global pins for this "
                  "system only. Clear them all to fall back to the global pins.", role="text",
                  size=13, anchor="w", pady=(0, 8), wraplength=900, justify="left")
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        self._players_editor(inner, sysname, status)

    def _players_add_picker(self):
        self._title("Add per-system pins")
        inner = self._scroll()
        self._lbl(inner, "Pick a system to give its own pin overrides (it then appears under "
                  "Per-system overrides).", role="text", size=13, anchor="w", pady=(0, 8),
                  wraplength=860, justify="left")
        first = None
        for s in self._players_systems(load_merged()):
            b = self._btn(inner, f"  {s}",
                          lambda x=s: self.goto(lambda: self._players_sys_detail(x)), width=26)
            b.pack(anchor="w", pady=2)
            first = first or b
        if first:
            first.focus_set()

    def _players_editor(self, parent, scope, status):
        """8-slot pin editor for `scope` (None = global [pins]; else a system name →
        [systems.<scope>.pins]). Player pins go in a LEFT column; a RIGHT column holds
        the (rebuildable) connected-pads panel at its TOP and is RETURNED so the caller
        can stack more under it (e.g. per-system overrides) — that extra content is NOT
        destroyed by a refresh. Registers self._page_refresh so a pad (dis)connect
        live-updates the names/badges + connected list WITHOUT losing unsaved edits."""
        merged = load_merged()
        pins0 = (merged.get("pins", {}) if scope is None
                 else merged.get("systems", {}).get(scope, {}).get("pins", {}))
        state: dict = {}
        for k, v in pins0.items():
            try:
                state[int(k)] = str(v)
            except (ValueError, TypeError):
                pass
        labels: dict = {}
        box = {"devs": [], "scanning": True, "tok": 0}

        def describe(pid_str):
            kind = pin_kind(pid_str)
            m = next((d for d in box["devs"] if pin_id(d) == pid_str), None)
            return (m.name if m else "(not connected)"), self._PIN_BADGE.get(kind, kind), kind

        cols = tk.Frame(parent, bg=self.c["bg"]); cols.pack(anchor="w", fill="x")
        left = tk.Frame(cols, bg=self.c["bg"]); left.pack(side="left", anchor="n", padx=(0, 34))
        rightcol = tk.Frame(cols, bg=self.c["bg"])
        rightcol.pack(side="left", anchor="n", fill="x", expand=True)
        # Connected-pads panel = TOP of the right column (rebuilt on refresh); whatever the
        # caller stacks under it (per-system overrides) lives in rightcol and survives refresh.
        conn = tk.Frame(rightcol, bg=self.c["bg"]); conn.pack(anchor="w", fill="x")

        def render():
            for p in sorted(labels):
                k = state.get(p)
                if k:
                    name, badge, kind = describe(k)
                    fg = self.c.get("warn", "#ff6b5e") if kind != "uniq" else self.c["accent"]
                    labels[p].config(text=f"  {name} · {badge}", fg=fg)
                else:
                    labels[p].config(text="  (unpinned)", fg=self.c["text_dim"])

        def rebuild_conn():
            for w in conn.winfo_children():
                w.destroy()
            self._lbl(conn, "Connected pads", role="accent", size=13, bold=True, anchor="w")
            ds = box["devs"]
            if not ds:
                self._lbl(conn, "  Scanning pads…" if box.get("scanning") else "  (none detected)",
                          role="dim", size=12, anchor="w")
                return
            seen = set()
            for d in ds:
                k = pin_id(d)
                if k in seen:
                    continue
                seen.add(k)
                _, badge, kind = describe(k)
                fg = self.c.get("warn", "#ff6b5e") if kind != "uniq" else self.c["text"]
                tk.Label(conn, text=f"  • {d.name}   {badge}", bg=self.c["bg"], fg=fg,
                         font=self.font(12, mono=True), anchor="w",
                         wraplength=560, justify="left").pack(anchor="w")

        def fetch_async():
            """Enumerate pads OFF the main thread (≈1.6s) so the Players page never freezes
            on a switch / pad change; refill box + re-render on the main thread when done.
            A token drops a stale fill (newer fetch, or the page was left)."""
            box["scanning"] = True
            box["tok"] = box.get("tok", 0) + 1
            tok = box["tok"]
            def work():
                try:
                    devs = joypads(enumerate_devices())
                except Exception:
                    devs = []
                self._ui_q.put(lambda: _filled(devs, tok))   # main thread runs it
            def _filled(devs, tok):
                if tok != box.get("tok") or not conn.winfo_exists():
                    return
                box["devs"] = devs; box["scanning"] = False
                render(); rebuild_conn()
            import threading
            threading.Thread(target=work, daemon=True).start()

        def mk_detect(pl):
            def detect():
                status.config(text=f"Player {pl}: press a button on the pad you want…")
                self.nav._held = set()
                def grab(held, dev=None):
                    self.nav.capture = None
                    try:
                        cur = joypads(enumerate_devices())
                        m = next((d for d in cur if dev is not None and d.path == dev.path), None)
                        if m is None:
                            status.config(text="Couldn't identify — try a face button.")
                            return
                        k = pin_id(m)
                        for q in [q for q, v in state.items() if v == k and q != pl]:
                            del state[q]            # one pad can't hold two slots
                        state[pl] = k
                        box["devs"] = cur
                        render(); rebuild_conn()
                        nm, badge, _ = describe(k)
                        status.config(text=f"Player {pl} → {nm}  [{badge}] — press Save.")
                    except Exception as ex:
                        status.config(text=f"identify failed: {ex!r}")
                self.nav.capture = grab
            return detect

        def mk_clear(pl):
            def clear():
                state.pop(pl, None); render()
                status.config(text=f"Player {pl} cleared — press Save.")
            return clear

        def save():
            data = localpolicy.load(LOCAL)
            tbl = {str(p): state[p] for p in sorted(state) if state[p]}
            if scope is None:
                if tbl:
                    data["pins"] = tbl
                else:
                    data.pop("pins", None)
            else:
                syst = data.setdefault("systems", {}).setdefault(scope, {})
                if tbl:
                    syst["pins"] = tbl
                else:
                    syst.pop("pins", None)
                    if not syst:                 # don't leave an empty [systems.<scope>] table
                        data["systems"].pop(scope, None)
            localpolicy.dump(LOCAL, data)
            status.config(text=f"Saved {len(tbl)} pin(s) [{scope or 'global'}] to {LOCAL.name}.")

        if scope is None:
            self._lbl(left, "Global pins (apply to every game)", role="accent", size=14,
                      bold=True, anchor="w", pady=(0, 4))
        sb = tk.Frame(left, bg=self.c["bg"]); sb.pack(anchor="w", pady=(0, 8))
        self._btn(sb, "✔ Save pins", save, width=14).pack(side="left", padx=2)
        first = None
        for p in range(1, self._PLAYER_SLOTS + 1):
            self._lbl(left, f"Player {p}", role="accent", size=13, bold=True, anchor="w", pady=(5, 0))
            # wraplength: long device names (e.g. "Valve Software Steam Deck Controller") used to
            # extend past the viewport and clip — wrap them within the left pin column instead.
            labels[p] = self._lbl(left, "", mono=True, size=12, anchor="w",
                                   wraplength=320, justify="left")
            bar = tk.Frame(left, bg=self.c["bg"]); bar.pack(anchor="w", pady=(1, 0))
            b = self._btn(bar, "● Identify", mk_detect(p), width=12); b.pack(side="left", padx=2)
            self._btn(bar, "✖", mk_clear(p), width=4).pack(side="left", padx=2)
            first = first or b
        render(); rebuild_conn(); fetch_async()   # show the page instantly; pads fill in async

        def refresh():
            if self.nav.capture is not None:     # don't disturb an in-progress identify
                return
            fetch_async()                        # threaded — never freezes on a pad change
        self._page_refresh = refresh
        if first:
            first.focus_set()
        return rightcol

    # ---- quit combo ----
    def _combo_str(self, buttons):
        return "+".join(self._btn_name(c) for c in buttons) or "(none)"

    def quitcombo(self):
        self._title("Quit-game combo")
        inner = self._scroll()
        self._lbl(inner, "Hold a gamepad combo ~1s to quit a standalone game → ES-DE. "
                  "Eligible systems are auto-discovered from ES-DE (standalone emulators you "
                  "have games for).", role="text", size=13, anchor="w", pady=(0, 8),
                  wraplength=self._textwrap(), justify="left")
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))

        merged = load_merged()
        qc = merged.get("quit_combo", {})
        gstate = {"buttons": list(qc.get("buttons", [314, 315])),
                  "hold": float(qc.get("hold_sec", 1.0))}

        self._lbl(inner, "Global default", role="accent", size=14, bold=True,
                  anchor="w", pady=(4, 2))
        gcur = self._lbl(inner, "", mono=True, size=15, bold=True, anchor="w")

        def grender():
            gcur.config(text=f"  {self._combo_str(gstate['buttons'])}   ·   hold {gstate['hold']:.1f}s")

        def gdetect():
            status.config(text="Detecting global… hold the combo, then release.")
            self.nav._held = set()
            def grab(held):
                self.nav.capture = None
                gstate["buttons"] = sorted(held); grender()
                status.config(text=f"Captured {len(held)} button(s) — press Save.")
            self.nav.capture = grab

        def gsave():
            data = localpolicy.load(LOCAL)
            data.setdefault("quit_combo", {})
            data["quit_combo"]["buttons"] = gstate["buttons"]
            data["quit_combo"]["hold_sec"] = gstate["hold"]
            localpolicy.dump(LOCAL, data)
            status.config(text=f"Saved global combo to {LOCAL.name}.")

        grender()
        # hold time as a stepper (0.3–3.0s)
        gui_widgets.stepper(inner, self.style, "hold time (s)", gstate["hold"],
                            lo=0.3, hi=3.0, step=0.1,
                            on_change=lambda v: (gstate.__setitem__("hold", v)),
                            fmt=lambda v: f"{v:.1f}")
        gbar = tk.Frame(inner, bg=self.c["bg"]); gbar.pack(anchor="w", pady=(2, 12))
        b1 = self._btn(gbar, "● Detect", gdetect, width=12); b1.pack(side="left", padx=3)
        self._btn(gbar, "✔ Save", gsave, width=10).pack(side="left", padx=3)

        eligible = es_systems.quit_combo_systems(merged)
        self._lbl(inner, "Per system (overrides the global)", role="accent",
                  size=14, bold=True, anchor="w", pady=(10, 4))
        self._lbl(inner, "wii: + & −  (real Wii Remotes via DolphinBar — HID, fixed)",
                  role="dim", size=12, mono=True, anchor="w", pady=(0, 6))
        self._btn(inner, "➕ Add per-system combo",
                  lambda: self.goto(self._quit_add_picker), width=24).pack(anchor="w", pady=(0, 8))
        overridden = [s for s in eligible
                      if isinstance(qc.get(s), dict) and "buttons" in qc[s]]
        if not overridden:
            self._lbl(inner, "  (none — every system uses the global combo)",
                      role="dim", size=12, anchor="w")
        else:
            self._tile_grid(
                inner,
                [(s, s, self._combo_str(list(qc[s]["buttons"]))) for s in overridden],
                lambda s: self.goto(lambda: self._quit_sys_detail(s)),
                cols=self._grid_cols())
        b1.focus_set()

    def _quit_sys_detail(self, sysname):
        self._title(f"Quit combo: {sysname}")
        merged = load_merged()
        btns = list(merged.get("quit_combo", {}).get(sysname, {}).get("buttons", []))
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        ic = self._console_img(sysname, 200)
        if ic:
            tk.Label(inner, image=ic, bg=self.c["bg"]).pack(anchor="w", pady=(0, 8))
        self._lbl(inner, f"Override combo:  {self._combo_str(btns)}    (B = back)",
                  role="accent", size=15, bold=True, mono=True, anchor="w", pady=(0, 10))
        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=4)
        b = self._btn(bar, "● Re-detect", lambda: self._detect_sys(sysname, status), width=14)
        b.pack(side="left", padx=3)
        self._btn(bar, "↺ Clear override",
                  lambda: self._clear_sys(sysname, status), width=16).pack(side="left", padx=3)
        b.focus_set()

    def _quit_add_picker(self):
        self._title("Add per-system quit combo")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        self._lbl(inner, "Pick a system, then hold the combo you want (~1s, then release). "
                  "B = back.", role="text", size=13, anchor="w", pady=(0, 8),
                  wraplength=860, justify="left")
        merged = load_merged()
        qc = merged.get("quit_combo", {})
        avail = [s for s in es_systems.quit_combo_systems(merged)
                 if not (isinstance(qc.get(s), dict) and "buttons" in qc.get(s, {}))]

        def arm(sysname):
            status.config(text=f"Detecting {sysname}… hold the combo, then release.")
            self.nav._held = set()
            def grab(held):
                self.nav.capture = None
                data = localpolicy.load(LOCAL)
                data.setdefault("quit_combo", {}).setdefault(sysname, {})["buttons"] = sorted(held)
                localpolicy.dump(LOCAL, data)
                self.back()
            self.nav.capture = grab

        if not avail:
            self._lbl(inner, "All eligible systems already have an override.",
                      role="dim", size=13, anchor="w")
        else:
            self._tile_grid(inner, [(s, s, "") for s in avail],
                            lambda s: arm(s), cols=self._grid_cols())

    def _detect_sys(self, sysname, status):
        status.config(text=f"Detecting {sysname}… hold the combo, then release.")
        self.nav._held = set()
        def grab(held):
            self.nav.capture = None
            data = localpolicy.load(LOCAL)
            data.setdefault("quit_combo", {}).setdefault(sysname, {})["buttons"] = sorted(held)
            localpolicy.dump(LOCAL, data)
            self.stack = [self.quitcombo]; self._clear(); self._render(self.quitcombo)
        self.nav.capture = grab

    def _clear_sys(self, sysname, status):
        data = localpolicy.load(LOCAL)
        if isinstance(data.get("quit_combo"), dict) and sysname in data["quit_combo"]:
            del data["quit_combo"][sysname]
            localpolicy.dump(LOCAL, data)
        self.stack = [self.quitcombo]; self._clear(); self._render(self.quitcombo)

    @staticmethod
    def _btn_name(code: int) -> str:
        if e:
            n = e.BTN.get(code) or e.KEY.get(code)
            if isinstance(n, (list, tuple)):
                n = n[0]
            if n:
                return n.replace("BTN_", "").replace("KEY_", "")
        return str(code)

    # ---- controller priority ----
    def priority(self):
        self._title("Controller priority")
        inner = self._scroll()
        self._lbl(inner, "Preferred controller per system (top = Player 1). "
                  "RetroArch systems only — standalone emulators are configured on the "
                  "Backends page. A custom COLLECTION rule overrides the system rule for its "
                  "member games (e.g. a lightgun collection).",
                  role="text", size=13, anchor="w", pady=(0, 8),
                  wraplength=self._textwrap(), justify="left")
        merged = load_merged()
        systems = es_systems.load_systems()
        cols = self._grid_cols()

        configured = sorted(
            s for s, ent in merged.get("systems", {}).items()
            if isinstance(ent, dict) and ent.get("ports")
            and not es_systems.is_standalone(es_systems.default_command(s, systems)))
        self._lbl(inner, "Configured systems", role="accent", size=14, bold=True,
                  anchor="w", pady=(4, 4))
        self._btn(inner, "➕ Configure a system",
                  lambda: self.goto(lambda: self._priority_picker("system")),
                  width=24).pack(anchor="w", pady=(0, 8))
        if not configured:
            self._lbl(inner, "  (none configured yet)",
                      role="dim", size=12, anchor="w")
        else:
            def sysitem(s):
                # Every system shown here is a RetroArch system, and ALL RetroArch
                # systems get routed at launch — wrapped ones via controller-router-wrap.sh,
                # unwrapped ones via the always-on game-start hook (04-controller-router-setup.sh).
                # So there's no "not wired" state to warn about; just show P1.
                order = (merged["systems"][s].get("ports") or [[]])[0]
                p1 = order[0] if order else "(empty)"
                return (s, s, f"P1: {p1}")
            self._tile_grid(inner, [sysitem(s) for s in configured],
                            lambda s: self.goto(lambda: self._priority_edit(s, "system")),
                            cols=cols)

        cfg_c = merged.get("collections", {})
        configured_c = [c for c in collections.enabled_collections()
                        if isinstance(cfg_c.get(c), dict) and cfg_c[c].get("ports")]
        self._lbl(inner, "Configured collections", role="accent", size=14, bold=True,
                  anchor="w", pady=(16, 4))
        self._btn(inner, "➕ Configure a collection",
                  lambda: self.goto(lambda: self._priority_picker("collection")),
                  width=26).pack(anchor="w", pady=(0, 8))
        if not configured_c:
            self._lbl(inner, "  (none configured yet)", role="dim", size=12, anchor="w")
        else:
            def colitem(c):
                ent = cfg_c[c]
                order = (ent.get("ports") or [[]])[0]
                p1 = order[0] if order else "(empty)"
                lg = "  [lightgun]" if ent.get("require_sinden") else ""
                fb = ["lightgun.png"] if ent.get("require_sinden") else ["controllers.png"]
                return (c, c, f"P1: {p1}{lg}", fb)
            self._tile_grid(inner, [colitem(c) for c in configured_c],
                            lambda c: self.goto(lambda: self._priority_edit(c, "collection")),
                            cols=cols)

    def _priority_picker(self, kind="system"):
        inner = self._scroll()
        merged = load_merged()
        if kind == "system":
            self._title("Pick a system")
            self._lbl(inner, "Pick a system to set its controller priority (systems you have "
                      "games for). B = back.", role="text", size=13, anchor="w",
                      pady=(0, 8), wraplength=860, justify="left")
            systems = es_systems.load_systems()
            have = {s for s, ent in merged.get("systems", {}).items()
                    if isinstance(ent, dict) and ent.get("ports")}
            avail = sorted(s for s in systems
                           if es_systems._has_gamelist(s) and s not in have
                           and not es_systems.is_standalone(es_systems.default_command(s, systems)))
            empty = "  (no other systems with games found)"
        else:
            self._title("Pick a collection")
            self._lbl(inner, "Pick an ES-DE custom collection to give it a controller rule that "
                      "overrides the system rule for its member games. Enable collections in "
                      "ES-DE first. B = back.", role="text", size=13, anchor="w",
                      pady=(0, 8), wraplength=860, justify="left")
            cfg = merged.get("collections", {})
            have = {c for c in cfg if isinstance(cfg.get(c), dict) and cfg[c].get("ports")}
            avail = [c for c in collections.enabled_collections() if c not in have]
            empty = "  (no enabled custom collections — create/enable one in ES-DE)"
        if not avail:
            self._lbl(inner, empty, role="dim", size=12, anchor="w")
        else:
            # Collections fall back to a controllers icon if their theme has no
            # console.png (a collection with theme art, e.g. tmnt, shows its logo).
            fb = None if kind == "system" else ["controllers.png"]
            items = [((s, s, "") if fb is None else (s, s, "", fb)) for s in avail]
            self._tile_grid(inner, items,
                            lambda x: self.goto(lambda: self._priority_edit(x, kind)),
                            cols=self._grid_cols())

    def _priority_edit(self, name, kind="system"):
        table = "systems" if kind == "system" else "collections"
        self._title(f"Priority: {name}")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        self._lbl(inner, "↑/↓ to reorder (top = Player 1), then Save. B = back.",
                  role="text", size=13, anchor="w", pady=(0, 10))
        merged = load_merged()
        fams = controller_families(merged)
        ent0 = merged.get(table, {}).get(name, {})
        existing = ent0.get("ports") or []
        cur = list(existing[0]) if existing and existing[0] else []
        order = [f for f in cur if f in fams]
        order += [f for f in fams if f not in order]
        state = {"order": order, "nports": (len(existing) if existing else 2)}

        lightgun_var = tk.BooleanVar(value=bool(ent0.get("require_sinden", False)))
        if kind == "collection":
            self._toggle(inner, "lightgun (Sinden)", lightgun_var.get(),
                         lambda v: lightgun_var.set(v))
            self._lbl(inner, "  lightgun = require a Sinden gun and pin its aim; the order "
                      "below is the menu / coin / start joypads.", role="dim", size=11,
                      anchor="w", wraplength=860, justify="left")

        listbox = tk.Frame(inner, bg=self.c["bg"]); listbox.pack(anchor="w", fill="x", pady=(8, 8))

        def render(focus=(0, 0)):
            for w in listbox.winfo_children():
                w.destroy()
            btns = []
            for i, fam in enumerate(state["order"]):
                row = tk.Frame(listbox, bg=self.c["surface"]); row.pack(fill="x", pady=2)
                tag = "P1" if i == 0 else ("P2" if i == 1 else f"#{i+1}")
                tk.Label(row, text=f"  {tag:>3}  {fam}", bg=self.c["surface"],
                         fg=(self.c["accent"] if i == 0 else self.c["text"]), width=22,
                         font=self.font(14, mono=True), anchor="w").pack(side="left", padx=6)
                up = self._btn(row, "↑", lambda x=i: move(x, -1), sound_event="nav", width=3); up.pack(side="left", padx=2)
                dn = self._btn(row, "↓", lambda x=i: move(x, 1), sound_event="nav", width=3); dn.pack(side="left", padx=2)
                btns.append((up, dn))
            if btns:
                fi, fc = focus
                fi = max(0, min(fi, len(btns) - 1))
                fc = 0 if fc < 0 else (1 if fc > 1 else fc)
                btns[fi][fc].focus_set()

        def move(i, d):
            j = i + d
            if 0 <= j < len(state["order"]):
                state["order"][i], state["order"][j] = state["order"][j], state["order"][i]
                render(focus=(j, 0 if d < 0 else 1))

        def save():
            ports = [list(state["order"]) for _ in range(state["nports"])]
            data = localpolicy.load(LOCAL)
            entry = data.setdefault(table, {}).setdefault(name, {})
            entry["ports"] = ports
            if kind == "collection":
                entry["require_sinden"] = bool(lightgun_var.get())
            localpolicy.dump(LOCAL, data)
            # No ES-DE restart / es_systems.xml wiring needed: the always-on game-start
            # hook (04-controller-router-setup.sh) runs the router for every RetroArch
            # system, reading this priority fresh at each launch.
            status.config(text=f"Saved {name}: P1 → {state['order'][0]}. "
                               "Applies on the next game launch (no ES-DE restart).")

        render()
        bar = tk.Frame(inner, bg=self.c["bg"]); bar.pack(anchor="w", pady=(8, 4))
        self._btn(bar, "✔ Save", save, width=12).pack(side="left", padx=3)
        self._btn(bar, "↺ Clear rule",
                  lambda: (self._priority_clear(name, kind), self.back()),
                  width=14).pack(side="left", padx=3)

    def _priority_clear(self, name, kind="system"):
        table = "systems" if kind == "system" else "collections"
        data = localpolicy.load(LOCAL)
        d = data.get(table, {})
        if name in d:
            d[name].pop("ports", None)
            if kind == "collection":
                d[name].pop("require_sinden", None)
            if not d[name]:
                del d[name]
            localpolicy.dump(LOCAL, data)
        # Re-render is the caller's job (the Clear button calls self.back()).

    # ---- systems ----
    def _esde_systems(self):
        """Systems you actually have in ES-DE — those with a gamelist.xml (the same
        signal ES-DE uses to hide empty systems). Read FRESH each call, so adding or
        removing a system in ES-DE is reflected next time the page opens (no restart)."""
        gl = esde_settings.APPDATA / "gamelists"
        if not gl.is_dir():
            return set()
        return {d.name for d in gl.iterdir()
                if d.is_dir() and (d / "gamelist.xml").is_file()}

    def _grid_cols(self):
        """Columns that fit the current display (handheld ~6, docked/TV ~9-10)."""
        return max(3, (self.root.winfo_screenwidth() - 210) // 175)

    def _tile_grid(self, inner, items, on_pick, *, cols=5, art=130):
        """Grid of console-art tiles. items = (sysname, label, sublabel) or
        (sysname, label, sublabel, fallback_art_names); the tile shows the system's
        ES-DE console.png (or the fallback art) with label/sublabel below, and runs
        on_pick(sysname) when activated. 360 nav moves between tiles."""
        grid = tk.Frame(inner, bg=self.c["bg"]); grid.pack(anchor="w", fill="x")
        bw, bh = art, int(art * 0.78)                 # uniform art box
        # Size the label area to the WIDEST label, MEASURED at the live font scale, so
        # system names never char-wrap mid-word (e.g. on a docked TV where the font is
        # scaled up and a name like "mastersystem" outgrows a fixed width). Capped so a
        # single long name can't blow the tile up.
        base = bw + 12
        try:
            import tkinter.font as _tkfont
            _f = _tkfont.Font(root=self.root, font=self.style.font(11))
            widest = 0
            for it in items:
                widest = max(widest, _f.measure(it[1]))
                if len(it) > 2 and it[2]:
                    widest = max(widest, _f.measure(it[2]))
            wrap = max(base, min(widest + 20, int(bw * 2.6)))
        except Exception:
            wrap = max(base, int(base * self.theme.scale))   # scale-aware fallback
        tile_w = wrap + 16
        tile_h = bh + int(78 * self.theme.scale) + 12  # art box + room for label/sublabel
        # Most columns that fit at this tile width, but never more than the caller's
        # estimate — so wider tiles drop the column count rather than overflow the row.
        avail = max(320, self.root.winfo_screenwidth() - 210)
        cols = max(1, min(cols, avail // (tile_w + 12)))
        for idx, item in enumerate(items):
            sysname, label, sublabel = item[0], item[1], item[2]
            fallback = item[3] if len(item) > 3 else None   # art names if no console.png
            r, c = divmod(idx, cols)
            # Fixed-size cell → every tile is identical regardless of console.png size.
            cell = tk.Frame(grid, bg=self.c["bg"], width=tile_w, height=tile_h)
            cell.grid(row=r, column=c, padx=6, pady=6)
            cell.grid_propagate(False)
            txt = f"{label}\n{sublabel}" if sublabel else label
            b = gui_widgets.button(cell, self.style, txt, size=11,
                                   cmd=lambda x=sysname: on_pick(x),
                                   image=self._console_fit_or(sysname, bw, bh, fallback),
                                   compound="top", wraplength=wrap)
            b.pack(fill="both", expand=True)

    def systems(self):
        TOOL = {"sinden", "steam", "desktop", "controllers", "sinden-tools"}   # not games
        names = [s for s in sorted(self._esde_systems()) if s not in TOOL]
        self._title(f"Systems ({len(names)})")
        merged = load_merged()
        inner = self._scroll()
        self._lbl(inner, "Pick one to set how the router treats its controllers.",
                  role="dim", size=12, anchor="w", pady=(0, 10),
                  wraplength=self._textwrap(), justify="left")

        local_sys = localpolicy.load(LOCAL).get("systems", {})

        def sub(s):
            e = merged.get("systems", {}).get(s, {})
            base = "hands-off" if e.get("router_skip") else e.get("backend", "retroarch")
            return f"● {base}" if local_sys.get(s) else base   # ● = you've configured this system
        # Populate from what's actually in ES-DE (gamelists), not the static policy — so
        # systems you don't have (e.g. xbox) don't show, and deletions drop off here too.
        cols = self._grid_cols()
        if not names:
            self._lbl(inner, "  (no ES-DE gamelists found)", role="dim", size=12, anchor="w")
        self._tile_grid(inner, [(s, s, sub(s)) for s in names],
                        lambda s: self.goto(lambda: self._system_detail(s)), cols=cols)

    def _system_detail(self, sysname):
        self._title(f"System: {sysname}")
        merged = load_merged()
        inner = self._scroll()
        ent = merged.get("systems", {}).get(sysname, {})
        ic = self._console_img(sysname, 200)
        if ic:
            tk.Label(inner, image=ic, bg=self.c["bg"]).pack(anchor="w", pady=(0, 8))
        self._lbl(inner, f"backend = {ent.get('backend', 'retroarch')}    (B = back)",
                  role="dim", size=12, anchor="w", pady=(0, 10))
        r = tk.Frame(inner, bg=self.c["bg"]); r.pack(anchor="w", pady=4)
        self._toggle(r, "Hands-off (router never touches this system)",
                     bool(ent.get("router_skip", False)),
                     lambda v: self._set_sys(sysname, "router_skip", v))
        for flag, lbl in (("require_dolphinbar", "Require a DolphinBar"),
                          ("require_sinden", "Require a Sinden gun")):
            if flag in ent or sysname == "wii":
                rr = tk.Frame(inner, bg=self.c["bg"]); rr.pack(anchor="w", pady=4)
                self._toggle(rr, lbl, bool(ent.get(flag, False)),
                             lambda v, f=flag: self._set_sys(sysname, f, v))
        # X-Arcade presence warning — shown per the system's category (mugen/openbor
        # count as arcade for this), default ON; toggling writes an explicit override.
        cat = self._resolve_category(sysname, merged)
        if sysname in ("mugen", "openbor") or cat == "arcade":
            wflag, wlbl = "warn_when_no_xarcade", "Warn when the X-Arcade is NOT present"
        elif cat == "console":
            wflag, wlbl = "warn_when_only_xarcade", "Warn when only the X-Arcade is present"
        else:
            wflag = None
        if wflag:
            rr = tk.Frame(inner, bg=self.c["bg"]); rr.pack(anchor="w", pady=4)
            self._toggle(rr, wlbl, bool(ent.get(wflag, True)),
                         lambda v, f=wflag: self._set_sys(sysname, f, v))

    def _set_sys(self, sysname, flag, value):
        data = localpolicy.load(LOCAL)
        data.setdefault("systems", {}).setdefault(sysname, {})[flag] = value
        localpolicy.dump(LOCAL, data)

    def _resolve_category(self, sysname, merged):
        """Walk the policy `inherits` chain to find a system's category."""
        sysd = merged.get("systems", {})
        s, seen = sysname, set()
        while s and s not in seen:
            seen.add(s)
            e = sysd.get(s, {})
            if e.get("category"):
                return e["category"]
            s = e.get("inherits")
        return None

    # ---- backends: list → per-backend page ----
    @staticmethod
    def _whitelist_empty(bcfg):
        """True when a backend USES the SDL-whitelist mechanism (it has a
        pad_classes or handheld_class key) but has NEITHER populated — i.e. the
        SDL allow-list is empty, so games launched on it get NO controllers.
        Mirrors the runtime guard in controller-router.py (sdl-ignore path)."""
        uses = ("pad_classes" in bcfg) or ("handheld_class" in bcfg)
        return uses and not bcfg.get("pad_classes") and not bcfg.get("handheld_class")

    def backends(self):
        self._title("Backends (controllers)")
        merged = load_merged()
        inner = self._scroll()
        self._lbl(inner, "Per-emulator controller settings. Pick a backend to edit which pads "
                  "are players, how many slots, profiles, and config location.",
                  role="dim", size=12, anchor="w", pady=(0, 10), wraplength=self._textwrap(), justify="left")
        # Each emulator backend → the console.png(s) of the system(s) it drives.
        BE_SYS = {"cemu": ["wiiu"], "dolphin": ["gc", "wii"], "eden": ["switch"],
                  "hypseus": ["daphne"], "openbor": ["openbor"], "pcsx2": ["ps2"],
                  "rpcs3": ["ps3"], "supermodel": ["model3"], "xemu": ["xbox"],
                  "xenia": ["xbox"], "flycast": ["dreamcast"]}
        esde = self._esde_systems()
        first, hidden = None, []
        for bname in sorted(b for b, c in merged.get("backends", {}).items()
                            if isinstance(c, dict)):
            syslist = BE_SYS.get(bname, [bname])
            # Only show backends whose system you actually have in ES-DE. (If gamelists
            # are unavailable — `esde` empty — the guard is skipped and all are shown.)
            if esde and not any(s in esde for s in syslist):
                hidden.append(bname); continue
            bcfg = merged["backends"][bname]
            keys = [k for k in bcfg if k not in ADVANCED_KNOBS]
            summ = ", ".join(keys[:4]) + ("…" if len(keys) > 4 else "")
            row = tk.Frame(inner, bg=self.c["surface"]); row.pack(fill="x", pady=3)
            iconbox = tk.Frame(row, bg=self.c["surface"], width=152, height=48)   # fixed width so EVERY
            iconbox.pack(side="left"); iconbox.pack_propagate(False)              # backend button starts
            for s in syslist:                                # dolphin → gc + wii  at the same x (Down works,
                tk.Label(iconbox, image=self._console_fit(s, 70, 48),            # not just diagonal)
                         bg=self.c["surface"]).pack(side="left", padx=(6, 0))
            b = self._btn(row, f"  {bname}", lambda x=bname: self.goto(lambda: self._backend_page(x)),
                          width=14)
            b.pack(side="left", padx=6, pady=4)
            tk.Label(row, text=summ, bg=self.c["surface"], fg=self.c["text_dim"],
                     anchor="w", font=self.font(11, mono=True)).pack(side="left", padx=8)
            if self._whitelist_empty(bcfg):
                tk.Label(row, text="⚠ no players", bg=self.c["surface"],
                         fg=self.c.get("warn", "#ff6b5e"),
                         anchor="w", font=self.font(11, bold=True)).pack(side="left", padx=8)
            first = first or b
        if hidden:
            self._lbl(inner, "Hidden (no games in ES-DE): " + ", ".join(hidden),
                      role="dim", size=11, anchor="w", pady=(12, 0), wraplength=820, justify="left")
        if first:
            first.focus_set()

    def _backend_page(self, bname):
        self._title(f"Backend: {bname}")
        merged = load_merged()
        bcfg = merged.get("backends", {}).get(bname, {})
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w", pady=(0, 6))
        if getattr(self, "_flash", None):     # result of a per-slot apply (survives the back() re-render)
            status.config(text=self._flash); self._flash = None

        if self._whitelist_empty(bcfg):
            self._lbl(inner, "⚠  No player pad families selected — this backend's SDL "
                      "whitelist is empty, so games launched on it will receive NO "
                      "controllers. Select at least one Player pad family below (or set a "
                      "handheld pad).", role="warn", size=12, bold=True, anchor="w",
                      wraplength=860, justify="left", pady=(0, 8))

        def caption(key):
            txt = KNOB_HELP.get(key)
            if txt:
                self._lbl(inner, "    " + txt, role="dim", size=11, anchor="w",
                          wraplength=820, justify="left", pady=(0, 6))

        # sdl_priority (bool)
        if "sdl_priority" in bcfg:
            self._lbl(inner, "Strict Player-1 priority", role="accent", size=14, bold=True, anchor="w", pady=(4, 0))
            r = tk.Frame(inner, bg=self.c["bg"]); r.pack(anchor="w")
            self._toggle(r, "strict P1", bool(bcfg["sdl_priority"]),
                         lambda v: self._set_backend(bname, "sdl_priority", v, status))
            caption("sdl_priority")

        # pad_classes (list)
        if "pad_classes" in bcfg:
            self._lbl(inner, "Player pad families", role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
            cands = pad_class_candidates(merged, *bcfg.get("pad_classes", []))
            gui_widgets.class_toggle_row(
                inner, self.style, "", cands, set(bcfg.get("pad_classes", [])),
                PAD_SHORT, lambda cls, v: self._set_list_member(bname, "pad_classes", cls, v, status))
            caption("pad_classes")

        # int managers (hidden for cemu/eden — their 8-slot profile picker is the slot UI)
        for key, lo, hi in (("manage_players", 1, 4), ("manage_pads", 1, 4)):
            if key in bcfg and isinstance(bcfg[key], int) and bname not in ("cemu", "eden"):
                self._lbl(inner, key.replace("_", " "), role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                gui_widgets.stepper(inner, self.style, key, int(bcfg[key]),
                                    lo=lo, hi=hi, step=1,
                                    on_change=lambda v, k=key: self._set_backend(bname, k, v, status))
                caption(key)

        # manage_ports: int (stepper) or list (slot toggles) — hidden for cemu (8-slot picker)
        if "manage_ports" in bcfg and bname not in ("cemu", "eden"):
            mp = bcfg["manage_ports"]
            if isinstance(mp, list):
                self._lbl(inner, "Managed controller slots", role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                r = tk.Frame(inner, bg=self.c["surface"]); r.pack(fill="x", pady=4)
                tk.Label(r, text="  slots", bg=self.c["surface"], fg=self.c["text"], width=10,
                         font=self.font(13), anchor="w").pack(side="left", padx=6)
                for slot in range(8):
                    self._toggle(r, f"C{slot+1}", slot in mp,
                                 lambda v, s=slot: self._set_list_member(bname, "manage_ports", s, v, status, is_int=True),
                                 width=6)
                caption("manage_ports_list")
            elif isinstance(mp, int):
                self._lbl(inner, "managed ports", role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                gui_widgets.stepper(inner, self.style, "managed ports", mp, lo=1, hi=4, step=1,
                                    on_change=lambda v: self._set_backend(bname, "manage_ports", v, status))
                caption("manage_ports_int")

        if "real2_min_wiimotes" in bcfg:
            self._lbl(inner, "2-remote threshold", role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
            gui_widgets.stepper(inner, self.style, "min Wii Remotes", int(bcfg["real2_min_wiimotes"]),
                                lo=1, hi=4, step=1,
                                on_change=lambda v: self._set_backend(bname, "real2_min_wiimotes", v, status))
            caption("real2_min_wiimotes")

        # class-list knobs
        for key in ("respect_user_config_classes", "keep_extra"):
            if key in bcfg:
                self._lbl(inner, key.replace("_", " "), role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                cands = pad_class_candidates(merged, *bcfg.get(key, []))
                gui_widgets.class_toggle_row(
                    inner, self.style, "", cands, set(bcfg.get(key, [])),
                    PAD_SHORT, lambda cls, v, k=key: self._set_list_member(bname, k, cls, v, status))
                caption(key)

        # handheld_class (single-choice pad)
        if "handheld_class" in bcfg:
            self._lbl(inner, "Handheld / fallback pad", role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
            cur = bcfg.get("handheld_class", "")
            cur_lbl = KNOWN_PADS.get(cur, cur) or "none"
            opts = [("", "none")] + [(k, KNOWN_PADS.get(k, k)) for k in KNOWN_PADS]
            self._btn(inner, f"  handheld pad: {cur_lbl}",
                      lambda: self._select_page("Handheld / fallback pad",
                                                KNOB_HELP["handheld_class"], opts,
                                                lambda v: self._set_backend(bname, "handheld_class", v, status)),
                      width=40).pack(anchor="w", pady=2)
            caption("handheld_class")

        # cemu profile pickers (from .xml in config_dir)
        cfg_path = bcfg.get("config_dir") or bcfg.get("config_file") or ""
        for key in ("p1_gamepad_template", "handheld_profile"):
            if key in bcfg:
                self._lbl(inner, key.replace("_", " "), role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                profs = [p.stem for p in list_profiles(cfg_path, "*.xml")]
                opts = [("", "none")] + [(s, s) for s in profs]
                cur = bcfg.get(key, "") or "none"
                self._btn(inner, f"  {key}: {cur}",
                          lambda k=key, o=opts: self._select_page(k, KNOB_HELP.get(k, ""), o,
                                                                  lambda v: self._set_backend(bname, k, v, status)),
                          width=40).pack(anchor="w", pady=2)
                caption(key)

        # Per-slot profile picker (cemu/eden): load YOUR named profiles per player slot.
        # These systems are router_skip (hands-off) — MAD applies your pick to the active
        # slot file and NEVER edits your named profiles. Replaces the old vid:pid template
        # editor (which couldn't do P1 vs P2 and pointed at stale names).
        if bname in ("cemu", "eden"):
            self._standalone_slot_picker(inner, bname, bcfg, status)

        # config path preset picker
        for key in ("config_dir", "config_file"):
            if key in bcfg:
                self._lbl(inner, key.replace("_", " "), role="accent", size=14, bold=True, anchor="w", pady=(8, 0))
                presets = CONFIG_PRESETS.get((bname, key), [])
                cur = bcfg.get(key, "")
                if cur and cur not in presets:
                    presets = [cur] + presets
                opts = []
                for p in presets:
                    exists = "✓" if Path(p).expanduser().exists() else "·"
                    opts.append((p, f"{exists} {p}"))
                self._btn(inner, f"  {key}: {cur}",
                          lambda k=key, o=opts: self._select_page(k, KNOB_HELP.get(k, ""), o,
                                                                  lambda v: self._set_backend(bname, k, v, status)),
                          width=46).pack(anchor="w", pady=2)
                caption(key)

        # Advanced (edit TOML) note for the pushed-back knobs present here.
        adv = [k for k in ADVANCED_KNOBS if k in bcfg]
        if adv:
            self._lbl(inner, "Advanced (edit controller-policy.toml): " + ", ".join(adv),
                      role="dim", size=11, anchor="w", pady=(14, 4), wraplength=820, justify="left")

    # ---- backend write helpers ----
    def _set_backend(self, bname, key, value, status=None):
        data = localpolicy.load(LOCAL)
        data.setdefault("backends", {}).setdefault(bname, {})[key] = value
        localpolicy.dump(LOCAL, data)
        if status:
            status.config(text=f"Saved {bname}.{key} = {value!r}")

    def _set_list_member(self, bname, key, member, present, status=None, is_int=False):
        merged = load_merged()
        cur = list(merged.get("backends", {}).get(bname, {}).get(key, []))
        if present and member not in cur:
            cur.append(member)
        elif not present and member in cur:
            cur.remove(member)
        if is_int:
            cur = sorted(set(cur))
        data = localpolicy.load(LOCAL)
        data.setdefault("backends", {}).setdefault(bname, {})[key] = cur
        localpolicy.dump(LOCAL, data)
        if status:
            status.config(text=f"Saved {bname}.{key} = {cur!r}")

    def _set_template(self, bname, cls, profile, status=None):
        merged = load_merged()
        tmpl = dict(merged.get("backends", {}).get(bname, {}).get("templates", {}))
        tmpl[cls] = profile
        data = localpolicy.load(LOCAL)
        data.setdefault("backends", {}).setdefault(bname, {})["templates"] = tmpl
        localpolicy.dump(LOCAL, data)
        if status:
            status.config(text=f"Saved {bname}.templates[{cls}] = {profile!r}")

    # ---- standalone per-slot profile picker (cemu/eden): load YOUR profiles ----
    def _standalone_slot_picker(self, inner, bname, bcfg, status):
        """Per-slot profile grid for cemu/eden. Lists YOUR named profiles (the active
        controllerN.xml are excluded); choosing one applies it to that slot's ACTIVE file
        via _apply_slot_profile. Named profiles are never modified."""
        import os
        import re as _re
        if bname == "cemu":
            pdir = os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
            profs = sorted(p.stem for p in list_profiles(pdir, "*.xml")
                           if not _re.fullmatch(r"controller\d+", p.stem))
            label = "Controller"
        else:
            pdir = os.path.expanduser("~/.config/eden/input")
            profs = sorted(p.stem for p in list_profiles(pdir, "*.ini"))
            label = "Player"
        sp = dict(bcfg.get("slot_profiles", {}) or {})
        self._lbl(inner, "Per-slot profiles  (your profiles — MAD never edits them)",
                  role="accent", size=14, bold=True, anchor="w", pady=(10, 2))
        self._lbl(inner, "Pick which of your named profiles loads on each slot — MAD saves it and "
                  "applies it to the active slot file the moment you choose (have the emulator "
                  "closed). C1 = the Steam Deck GamePad." if bname == "cemu" else
                  "Pick which of your named profiles loads on each player — applied to the active "
                  "config the moment you choose (have the emulator closed).",
                  role="dim", size=11, anchor="w", pady=(0, 4), wraplength=self._textwrap(), justify="left")
        self._btn(inner, "🎮  Test controllers (live input)",
                  lambda b=bname: self.goto(lambda: self._input_test_page(b)),
                  width=34).pack(anchor="w", pady=(0, 6))
        if not profs:
            self._lbl(inner, f"  (no profiles found in {pdir})", role="dim", size=12, anchor="w")
            return
        for s in range(8):
            cur = sp.get(str(s), "—")
            opts = [("", "(clear)")] + [(p, p) for p in profs]
            self._btn(inner, f"  {label} {s + 1}:  {cur}",
                      lambda s=s, o=opts: self._select_page(
                          f"{label} {s + 1} profile",
                          "Loads this profile onto the slot. Your profile file is not modified.",
                          o, lambda v, ss=s: self._apply_slot_profile(bname, ss, v, status)),
                      width=46).pack(anchor="w", pady=1)

    def _backup_active_once(self, backup, files, single=False):
        """One-time backup of the active slot file(s) before MAD's first write, so the
        current state is always recoverable. `backup` is a dir (cemu) or a file path
        (single=True, eden)."""
        import shutil
        try:
            if single:
                bp = Path(backup)
                if not bp.exists() and Path(files[0]).is_file():
                    shutil.copy2(files[0], bp)
                return
            Path(backup).mkdir(parents=True, exist_ok=True)
            for f in files:
                dest = Path(backup) / Path(f).name
                if Path(f).is_file() and not dest.exists():
                    shutil.copy2(f, dest)
        except Exception:
            pass

    def _apply_slot_profile(self, bname, slot, profile, status):
        """Save the per-slot choice to [backends.<bname>].slot_profiles AND apply it to the
        ACTIVE slot file. cemu = copy <profile>.xml -> controller<slot>.xml verbatim; eden =
        write <profile>.ini bindings -> qt-config player_<slot>. The NAMED profile is opened
        read-only and never modified."""
        import os
        import shutil
        label = "Controller" if bname == "cemu" else "Player"
        bcfg = load_merged().get("backends", {}).get(bname, {})
        # Result message is stashed on self._flash: _select_page calls back() right after this,
        # which rebuilds the page (+ a fresh status label), so a status.config() here is wiped.
        if not profile:                                   # clear the choice (active file left as-is)
            data = localpolicy.load(LOCAL)
            sp = data.get("backends", {}).get(bname, {}).get("slot_profiles", {})
            if isinstance(sp, dict) and sp.pop(str(slot), None) is not None:
                localpolicy.dump(LOCAL, data)
            self._flash = f"{bname} {label} {slot + 1}: choice cleared (active file left as-is)"
            return
        try:                                              # APPLY FIRST — persist only on success
            if bname == "cemu":
                cdir = Path(os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles")))
                src = cdir / f"{profile}.xml"
                if not src.is_file():
                    raise FileNotFoundError(src.name)
                dst = cdir / f"controller{slot}.xml"
                self._backup_active_once(cdir / ".router-backup", [dst])
                shutil.copy2(src, dst)                     # named profile is the SOURCE (read-only)
            else:
                from lib import eden_cfg, inifile
                src = Path(os.path.expanduser("~/.config/eden/input")) / f"{profile}.ini"
                if not src.is_file():
                    raise FileNotFoundError(src.name)
                ini = Path(os.path.expanduser(bcfg.get("config_file", "~/.config/eden/qt-config.ini")))
                self._backup_active_once(ini.with_name(ini.name + ".router-backup"), [ini], single=True)
                binds = eden_cfg._template_bindings(src)
                binds["connected"] = "true"; binds["type"] = "0"; binds["profile_name"] = ""
                text = ini.read_text(encoding="utf-8")
                body = eden_cfg._apply_player(inifile.section_body(text, "Controls") or "", slot, binds)
                ini.write_text(inifile.set_section(text, "Controls", body), encoding="utf-8")
        except Exception as e:                            # apply failed → DON'T record the choice
            self._flash = f"⚠ {bname} {label} {slot + 1}: apply failed, nothing changed ({e})"
            return
        data = localpolicy.load(LOCAL)                     # success → now persist the choice
        data.setdefault("backends", {}).setdefault(bname, {}).setdefault("slot_profiles", {})[str(slot)] = profile
        localpolicy.dump(LOCAL, data)
        self._flash = f"{bname} {label} {slot + 1} ← {profile}  (your profile file untouched)"

    # ---- live controller-input visualizer (Game-Mode native; no emulator launch) ----
    def _slot_binding(self, be, bcfg, slot):
        """For an emulator slot, return (profile name | None, short display device, vidpid class),
        read from the ACTIVE config so it reflects exactly what the emulator is bound to."""
        import os
        import re
        prof = (bcfg.get("slot_profiles", {}) or {}).get(str(slot))
        if be == "cemu":
            cdir = os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
            dev = cls = ""
            try:
                txt = open(os.path.join(cdir, f"controller{slot}.xml"),
                           encoding="utf-8", errors="replace").read()
                md = re.search(r"<display_name>([^<]*)</display_name>", txt)
                dev = md.group(1).strip() if md else ""
                mu = re.search(r"<uuid>([^<]*)</uuid>", txt)
                g = re.search(r"([0-9a-fA-F]{32})", mu.group(1)) if mu else None
                if g:
                    h = g.group(1)
                    cls = f"{int(h[10:12] + h[8:10], 16):04x}:{int(h[18:20] + h[16:18], 16):04x}"
            except OSError:
                pass
            return prof, self._short_dev(dev), cls
        # eden
        try:
            body = open(os.path.expanduser(bcfg.get("config_file", "~/.config/eden/qt-config.ini")),
                        encoding="utf-8", errors="replace").read()
        except OSError:
            body = ""
        conn = re.search(rf"player_{slot}_connected=(\w+)", body)
        if not prof and not (conn and conn.group(1) == "true"):
            return None, "", ""
        cls = dev = ""
        mg = re.search(rf'player_{slot}_button_a="[^"]*guid:([0-9a-fA-F]{{32}})', body)
        if mg:
            h = mg.group(1)
            try:
                cls = f"{int(h[10:12] + h[8:10], 16):04x}:{int(h[18:20] + h[16:18], 16):04x}"
                dev = KNOWN_PADS.get(cls, cls)
            except ValueError:
                cls = dev = ""
        return prof, dev, cls

    def _input_test_page(self, be):
        """Live-input test for ONE emulator (cemu/eden): one panel per slot that has a profile,
        showing the pad that slot is bound to — matched by device class + position, with the
        X-Arcade dropped by its identified USB port — so the list mirrors what the emulator uses."""
        import os
        import evdev
        from evdev import ecodes as e
        merged = load_merged()
        bcfg = merged.get("backends", {}).get(be, {})
        xport = (merged.get("hardware") or {}).get("xarcade_port", "")
        label = "Controller" if be == "cemu" else "Player"
        self._title(f"Controller test — {be}")
        inner = self._scroll()
        self._lbl(inner, f"The pads {be} has assigned — one panel per slot. Move sticks / press "
                  "buttons to confirm each slot's pad works. Reads raw evdev (what the emulator sees "
                  "with Steam Input off).", role="dim", size=12, anchor="w", pady=(0, 2),
                  wraplength=self._textwrap(), justify="left")
        self._lbl(inner, "Navigation is locked here — press the Guide / PS / Home (●) button to exit.",
                  role="accent", size=12, bold=True, anchor="w", pady=(0, 2),
                  wraplength=self._textwrap(), justify="left")
        if not xport:
            self._lbl(inner, "⚠ X-Arcade not identified — it shares 045e:02a1 with a real Xbox 360, so "
                      "it can't be excluded yet. Run 'Identify X-Arcade' on the Preview page.",
                      role="accent", size=12, anchor="w", pady=(0, 8),
                      wraplength=self._textwrap(), justify="left")
        # connected physical pads (X-Arcade + steam-virtual excluded), grouped by class, evdev order
        avail = {}
        seen = set()
        for d in enumerate_devices():
            vp = f"{d.vid:04x}:{d.pid:04x}"
            if d.is_steam_virtual or d.is_sinden or not (d.is_joypad or vp == "28de:1205"):
                continue
            if xport and port_of(getattr(d, "phys", "") or "") == xport:
                continue                                   # the X-Arcade — not an emulator pad
            key = getattr(d, "uniq", "") or port_of(getattr(d, "phys", "") or "") or d.path
            if key in seen:
                continue
            try:
                dev = evdev.InputDevice(d.path)
                os.set_blocking(dev.fd, False)
                absinfo = {c: ai for c, ai in dev.capabilities().get(e.EV_ABS, [])}
            except Exception:
                continue
            if e.ABS_X not in absinfo:                     # need the gamepad node (has sticks)
                dev.close()
                continue
            seen.add(key)
            avail.setdefault(vp, []).append((dev, absinfo))
        grid = tk.Frame(inner, bg=self.c["bg"]); grid.pack(anchor="w", fill="x")
        self._it_devs = []
        idx = 0
        for slot in range(8):
            prof, devname, cls = self._slot_binding(be, bcfg, slot)
            if not (prof or devname):
                continue
            pads = avail.get(cls) or []
            match = pads.pop(0) if pads else None
            row = tk.Frame(grid, bg=self.c["surface"])
            row.grid(row=idx // 2, column=idx % 2, sticky="nw", padx=(0, 18), pady=6)
            idx += 1
            tk.Label(row, text=f"  {label} {slot + 1}: {prof or devname}", bg=self.c["surface"],
                     fg=self.c["accent"], font=self.font(13, bold=True), anchor="w").pack(anchor="w")
            if not match:
                tk.Label(row, text="    (pad not connected)", bg=self.c["surface"],
                         fg=self.c["text_dim"], font=self.font(11), anchor="w").pack(anchor="w", padx=8, pady=(0, 6))
                continue
            dev, absinfo = match
            body = tk.Frame(row, bg=self.c["surface"]); body.pack(anchor="w", fill="x", padx=8, pady=(2, 6))

            def mk_stick(title, body=body):
                f = tk.Frame(body, bg=self.c["surface"]); f.pack(side="left", padx=8)
                tk.Label(f, text=title, bg=self.c["surface"], fg=self.c["text_dim"],
                         font=self.font(10)).pack()
                cv = tk.Canvas(f, width=70, height=70, bg=self.c["bg"], highlightthickness=1,
                               highlightbackground=self.c["text_dim"])
                cv.pack()
                cv.create_line(35, 0, 35, 70, fill=self.c["text_dim"])
                cv.create_line(0, 35, 70, 35, fill=self.c["text_dim"])
                return cv, cv.create_oval(31, 31, 39, 39, fill=self.c["accent"], outline="")
            cvL, dotL = mk_stick("L stick")
            cvR, dotR = mk_stick("R stick")
            info = tk.Frame(body, bg=self.c["surface"]); info.pack(side="left", padx=10, anchor="n")
            btnlbl = tk.Label(info, text="buttons: —", bg=self.c["surface"], fg=self.c["text"],
                              font=self.font(11, mono=True), anchor="w", justify="left", wraplength=300)
            btnlbl.pack(anchor="w")
            triglbl = tk.Label(info, text="LT — · RT — · dpad —", bg=self.c["surface"],
                               fg=self.c["text_dim"], font=self.font(11, mono=True), anchor="w")
            triglbl.pack(anchor="w")
            self._it_devs.append({
                "dev": dev, "absinfo": absinfo, "pressed": set(),
                "axes": {c: ai.value for c, ai in absinfo.items()},
                "dotL": (cvL, dotL), "dotR": (cvR, dotR), "btnlbl": btnlbl, "triglbl": triglbl,
            })
        for pads in avail.values():                        # close pads not matched to a slot
            for dev, _ in pads:
                try:
                    dev.close()
                except Exception:
                    pass
        if not self._it_devs:
            self._lbl(inner, f"  (no connected pads match {be}'s assigned slots)",
                      role="dim", size=12, anchor="w")
            return
        self.nav.capture = self._input_test_capture   # lock the nav so pads don't move the menu
        self._it_after = self.root.after(40, self._input_test_poll)

    def _input_test_capture(self, held, dev=None):
        """While the live-input test is open the nav is captured (locked) so moving a stick or
        pressing a button doesn't navigate the menu. Only Guide/PS/Home (BTN_MODE 0x13c) exits."""
        if 0x13c in held:        # BTN_MODE = Guide / PS / Home
            self.nav.capture = None
            self.back()

    _IT_BTN = None   # lazily built friendly button-name map

    def _input_test_poll(self):
        from evdev import ecodes as e
        if not getattr(self, "_it_devs", None):
            return
        if App._IT_BTN is None:
            App._IT_BTN = {
                e.BTN_SOUTH: "A", e.BTN_EAST: "B", e.BTN_NORTH: "X", e.BTN_WEST: "Y",
                e.BTN_TL: "L", e.BTN_TR: "R", e.BTN_TL2: "L2", e.BTN_TR2: "R2",
                e.BTN_SELECT: "Select", e.BTN_START: "Start", e.BTN_MODE: "Guide",
                e.BTN_THUMBL: "L3", e.BTN_THUMBR: "R3",
            }
        BTN = App._IT_BTN
        for it in self._it_devs:
            try:
                events = list(it["dev"].read())
            except (BlockingIOError, OSError):
                events = []
            if not events:          # idle pad → skip the Tk churn (this was the lag)
                continue
            for ev in events:
                if ev.type == e.EV_KEY:
                    nm = BTN.get(ev.code, f"b{ev.code}")
                    (it["pressed"].add if ev.value else it["pressed"].discard)(nm)
                elif ev.type == e.EV_ABS:
                    it["axes"][ev.code] = ev.value

            def norm(code):
                ai = it["absinfo"].get(code)
                v = it["axes"].get(code)
                if ai is None or v is None or ai.max == ai.min:
                    return 0.0
                mid = (ai.max + ai.min) / 2
                return max(-1.0, min(1.0, (v - mid) / ((ai.max - ai.min) / 2)))
            for (cv, dot), ax, ay in ((it["dotL"], e.ABS_X, e.ABS_Y), (it["dotR"], e.ABS_RX, e.ABS_RY)):
                px, py = 35 + norm(ax) * 30, 35 + norm(ay) * 30
                try:
                    cv.coords(dot, px - 4, py - 4, px + 4, py + 4)
                except Exception:
                    return                      # page torn down mid-poll
            it["btnlbl"].config(text="buttons: " + (" ".join(sorted(it["pressed"])) or "—"))
            it["triglbl"].config(text=f"LT {it['axes'].get(e.ABS_Z, 0)} · RT {it['axes'].get(e.ABS_RZ, 0)}"
                                      f" · dpad ({it['axes'].get(e.ABS_HAT0X, 0)},{it['axes'].get(e.ABS_HAT0Y, 0)})")
        self._it_after = self.root.after(40, self._input_test_poll)

    # ---- GUI settings ----
    def _reload_theme(self):
        """Re-resolve palette/font/scale and rebuild the sidebar + current page so
        GUI-settings changes apply IMMEDIATELY (no restart)."""
        self._reload_after = None
        # Remember which content control had focus so we can restore it after the
        # rebuild — keep the cursor on the toggle you just flipped, not jump to top.
        try:
            citems = self.nav._content_focusables()
            cur = self.root.focus_get()
            keep = citems.index(cur) if cur in citems else 0
        except Exception:
            keep = 0
        flags = gui_flags()
        self.theme = gui_theme.Theme(use_theme_colors=flags["theme_colors"],
                                     use_theme_font=flags["theme_font"],
                                     font_scale=flags["font_scale"])
        self.sound.set_muted(flags["sound_muted"])
        self.style = gui_widgets.Style(self.theme, self.sound)
        self.c = self.theme.colors
        self.font = self.theme.font
        self.root.configure(bg=self.c["bg"])
        self.sidebar.configure(bg=self.c["surface"])
        self.body.configure(bg=self.c["bg"])
        try:
            self.footer.config(bg=gui_theme._mix(self.c["bg"], "#000000", 0.4),
                               fg=self.c["text_dim"], font=self.font(12))
        except Exception:
            pass
        self._imgs = []
        self._img_cache = {}                    # theme changed → re-resolve art
        for w in self.sidebar.winfo_children():
            w.destroy()
        self._sidebar_btns = []
        self._build_sidebar()
        self.show_section(self.section_idx)      # re-render current page + highlight
        self.root.after(40, lambda: self._restore_content_focus(keep))

    def _restore_content_focus(self, idx):
        if getattr(self.root.focus_get(), "_mad_sidebar", False):
            return                          # browsing the sidebar — don't yank focus into content
        items = self.nav._content_focusables()
        if items:
            items[max(0, min(idx, len(items) - 1))].focus_set()

    def guisettings(self):
        self._title("GUI settings")
        inner = self._scroll()
        self._lbl(inner, "Preferences for THIS control panel (stored under [gui] in "
                  "controller-policy.local.toml; the router ignores them).",
                  role="dim", size=12, anchor="w",
                  pady=(0, 10), wraplength=self._textwrap(), justify="left")
        f = gui_flags()

        def setf(key, v):
            set_gui_flag(key, v)
            if key == "sound_muted":
                self.sound.set_muted(v)        # apply instantly — mute needs NO re-render
                return                          # (re-rendering mid-flip caused the pill/sound desync)
            # theme/colours/font/size → live re-render, DEBOUNCED so rapid toggles coalesce
            # into one rebuild (not one per flip). Deferred: the rebuild destroys the switch
            # whose callback we're in.
            if getattr(self, "_reload_after", None):
                try:
                    self.root.after_cancel(self._reload_after)
                except Exception:
                    pass
            self._reload_after = self.root.after(180, self._reload_theme)

        r1 = tk.Frame(inner, bg=self.c["bg"]); r1.pack(anchor="w", pady=3)
        self._toggle(r1, "sound", not f["sound_muted"], lambda v: setf("sound_muted", not v))
        self._lbl(inner, f"    Navigation sounds (ES-DE set: {gui_sound.esde_settings.read()['nav_sounds']}).",
                  role="dim", size=11, anchor="w")

        r2 = tk.Frame(inner, bg=self.c["bg"]); r2.pack(anchor="w", pady=3)
        self._toggle(r2, "theme colors", f["theme_colors"], lambda v: setf("theme_colors", v))
        r3 = tk.Frame(inner, bg=self.c["bg"]); r3.pack(anchor="w", pady=3)
        self._toggle(r3, "theme font", f["theme_font"], lambda v: setf("theme_font", v))

        scale_opts = [("auto", "Auto (TV → large, handheld → small)"),
                      ("xsmall", "Smallest"), ("small", "Small"), ("normal", "Normal"),
                      ("large", "Large"), ("xlarge", "Largest")]
        eff = (f"{self.theme.scale:.2f}× "
               + ("docked/TV" if gui_theme.external_display_connected() else "handheld"))
        self._btn(inner, f"Font size:  {dict(scale_opts).get(f['font_scale'], f['font_scale'].capitalize())}",
                  lambda: self._select_page(
                      "Font size", "Bigger for a TV across the room; smaller for handheld so text "
                      "doesn't clip. Auto picks by whether a TV/dock is connected. Applies instantly.",
                      scale_opts, lambda v: setf("font_scale", v))
                  ).pack(anchor="w", pady=(8, 2))
        self._lbl(inner, f"    Effective now: {eff}", role="dim", size=11, anchor="w", pady=(0, 4))

        self._lbl(inner, f"  Active ES-DE theme: {self.theme.theme_name or '(none)'}   "
                  f"colours matched: {self.theme.matched_colors}   font: "
                  f"{self.theme.family} ({'matched' if self.theme.matched_font else 'fallback'})",
                  role="dim", size=11, anchor="w", pady=(12, 0))

    # ---- backup ----
    def _compute_backup_sizes(self, cb, token):
        """Run deck-backup.sh --sizes in the background (du is slow on ROMs/media) and
        feed each '<key>\\t<bytes>' line to cb(key, bytes) on the UI thread. `token` is
        the Backup page's generation; if the user leaves the page (token changes) the
        stray callbacks no-op instead of touching destroyed widgets."""
        import threading
        import subprocess

        def worker():
            try:
                proc = subprocess.Popen([str(HERE / "deck-backup.sh"), "--sizes"],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True)
                for line in proc.stdout:
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1].isdigit():
                        k, n = parts[0], int(parts[1])
                        try:
                            self._ui_q.put(lambda kk=k, nn=n:
                                           (cb(kk, nn) if getattr(self, "_backup_gen", None) is token
                                            else None))
                        except Exception:
                            break
                proc.wait()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def backup(self):
        self._title("Backup / Restore")
        inner = self._scroll()
        status = self._lbl(inner, "", role="dim", size=12, anchor="w")
        targets = backup_targets(load_merged())
        snap = HERE / "data" / "gui-backup"

        def do_backup():
            import shutil
            n = 0
            snap.mkdir(parents=True, exist_ok=True)
            for name, p in targets.items():
                if p.is_file():
                    shutil.copy2(p, snap / (name + "_" + p.name)); n += 1
                elif p.is_dir():
                    shutil.copytree(p, snap / name, dirs_exist_ok=True); n += 1
            if LOCAL.is_file():
                shutil.copy2(LOCAL, snap / LOCAL.name)
            status.config(text=f"Backed up {n} emulator config(s) + GUI overrides → {snap}")

        def do_backup_mad():
            # Tar the whole MAD launchers tree (incl. controller-policy.local.toml) to an
            # EXTERNAL dir so it never recurses into itself. MAD also lives on GitHub
            # (mmadalone/mad); this is a self-contained local snapshot.
            import os
            import time
            import subprocess
            import threading
            status.config(text="Backing up MAD code…")
            def work():
                ts = time.strftime("%Y%m%d-%H%M%S")
                dest = os.path.expanduser(f"~/deck-config-backups/mad-code-{ts}.tar.gz")
                name = HERE.name
                ex = [f"--exclude={p}" for p in (
                    "*/__pycache__", "*.pyc", "*.log",
                    f"{name}/.git", f"{name}/data/gui-backup", f"{name}/squashfs-root",
                    f"{name}/AppDir", f"{name}/es-de", f"{name}/esde", f"{name}/srm")]
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    subprocess.run(["tar", "czf", dest, "-C", str(HERE.parent), *ex, name],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    mb = os.path.getsize(dest) // (1024 * 1024)
                    msg = f"MAD code → {dest}  ({mb} MB).  Also on GitHub: mmadalone/mad"
                except Exception as e:
                    msg = f"MAD-code backup failed: {e}"
                self._ui_q.put(lambda: status.config(text=msg))
            threading.Thread(target=work, daemon=True).start()

        def do_restore():
            import shutil
            if not snap.is_dir():
                status.config(text="No backup found — run Backup first."); return
            n = 0
            for name, p in targets.items():
                f = snap / (name + "_" + p.name)
                d = snap / name
                if f.is_file():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, p); n += 1
                elif d.is_dir():
                    shutil.copytree(d, p, dirs_exist_ok=True); n += 1
            lp = snap / LOCAL.name
            if lp.is_file():
                shutil.copy2(lp, LOCAL)
            status.config(text=f"Restored {n} emulator config(s) + GUI overrides. "
                          "Close emulators first if any were open.")

        def restore_router_backups():
            """Revert the one-time *.router-backup files each standalone backend
            writes the first time it edits an emulator's input config."""
            import shutil
            restored = []
            for _name, p in targets.items():
                cands = []
                if p.is_dir():
                    cands = list(p.glob("*.router-backup"))
                else:
                    cands = list(p.parent.glob(p.name + ".router-backup"))
                    cands += list(p.parent.glob(p.stem + ".*.router-backup"))
                for bk in cands:
                    target = bk.with_name(bk.name[:-len(".router-backup")])
                    try:
                        shutil.copy2(bk, target); restored.append(target.name)
                    except OSError:
                        pass
            status.config(text=(f"Restored {len(restored)} emulator input backup(s): "
                                + ", ".join(restored)) if restored
                          else "No *.router-backup files found.")

        def reset_local():
            if LOCAL.is_file():
                LOCAL.unlink()
            status.config(text="Cleared GUI overrides (reverted to documented defaults).")

        # ── Full system backup (deck-backup.sh) — exposes all its knobs ──
        self._lbl(inner, "Full backup", role="accent", size=15, bold=True,
                  anchor="w", pady=(2, 2))
        self._lbl(inner, "Archive your whole setup — toggle what to include, then Run. "
                  "Writes to ~/deck-config-backups in the background.",
                  role="dim", size=12, anchor="w", pady=(0, 6), wraplength=self._textwrap(), justify="left")
        bk = {"esde": True, "emu": True, "saves": True, "bios": True,
              "cores": True, "bezels": False, "rpcs3games": False, "pcsx2tex": False,
              "ryujinxgames": False, "roms": False, "media": False}
        knobs = [("ES-DE", "esde"), ("Emulator config + data", "emu"),
                 ("Saves", "saves"), ("BIOS", "bios"),
                 ("RetroArch cores", "cores"), ("Bezels", "bezels"),
                 ("RPCS3 installed games", "rpcs3games"), ("PCSX2 HD textures", "pcsx2tex"),
                 ("Ryujinx games", "ryujinxgames"),
                 ("ROMs", "roms"), ("Downloaded media", "media")]
        size_lbls = {}

        def _human(n):
            n = float(n)
            for u in ("B", "K", "M", "G", "T"):
                if n < 1024 or u == "T":
                    return f"{n:.0f}{u}" if u in ("B", "K") else f"{n:.1f}{u}"
                n /= 1024

        def update_tally():
            sz = getattr(self, "_backup_sizes", {}) or {}
            total = sum(sz.get(k, 0) for k, on in bk.items() if on)
            done = all(k in sz for k in bk)
            try:
                tally.config(text="Total selected: " + _human(total)
                             + ("" if done else "   (calculating…)"))
            except Exception:
                pass

        def apply_size(key, nbytes):
            sz = getattr(self, "_backup_sizes", None)
            if sz is None:
                sz = self._backup_sizes = {}
            sz[key] = nbytes
            lab = size_lbls.get(key)
            if lab is not None:
                try:
                    lab.config(text=_human(nbytes))
                except Exception:
                    pass
            update_tally()

        for i in range(0, len(knobs), 2):
            kr = tk.Frame(inner, bg=self.c["bg"]); kr.pack(anchor="w", pady=2)
            for lbl, key in knobs[i:i + 2]:
                cell = tk.Frame(kr, bg=self.c["bg"]); cell.pack(side="left", padx=(0, 18))
                self._toggle(cell, lbl, bk[key],
                             lambda v, k=key: (bk.__setitem__(k, v), update_tally()))
                size_lbls[key] = tk.Label(cell, text="…", bg=self.c["bg"],
                                          fg=self.c["text_dim"], font=self.font(11))
                size_lbls[key].pack(side="left", padx=(2, 0))
        tally = self._lbl(inner, "Total selected: …", role="accent", size=12,
                          anchor="w", pady=(2, 8))
        self._backup_gen = object()          # page generation — stray thread callbacks check this
        cached = getattr(self, "_backup_sizes", None)
        if cached:
            for _k, _v in cached.items():
                apply_size(_k, _v)
        else:
            self._compute_backup_sizes(apply_size, self._backup_gen)
        update_tally()

        def run_full():
            flag = {"esde": "esde", "emu": "emu", "saves": "saves", "bios": "bios",
                    "cores": "cores", "bezels": "bezels", "rpcs3games": "rpcs3",
                    "pcsx2tex": "pcsx2tex", "ryujinxgames": "ryujinx",
                    "roms": "roms", "media": "media"}
            argv = [str(HERE / "deck-backup.sh"), "--yes"]
            for _key, _fl in flag.items():
                argv.append(f"--{_fl}" if bk[_key] else f"--no-{_fl}")
            self._run(argv, status, "deck-backup")
        self._btn(inner, "💾  Run full backup now", run_full, width=30).pack(anchor="w", pady=(6, 14))

        self._lbl(inner, "Router config backup", role="accent", size=15,
                  bold=True, anchor="w", pady=(16, 4))
        self._lbl(inner, "Snapshot / revert the emulator controller configs the router writes, plus "
                  "the GUI's own overrides (controller-policy.local.toml).", role="text", size=13,
                  anchor="w", pady=(0, 8), wraplength=self._textwrap(), justify="left")
        rcb = tk.Frame(inner, bg=self.c["bg"]); rcb.pack(anchor="w", pady=6)
        self._btn(rcb, "💾  Backup", do_backup, width=14).pack(side="left", padx=(0, 6))
        self._btn(rcb, "⤴  Restore", do_restore, width=14).pack(side="left", padx=(0, 6))
        self._btn(rcb, "♻  Restore input backups", restore_router_backups,
                  width=24).pack(side="left", padx=(0, 6))
        self._btn(rcb, "↺  Reset overrides", reset_local, width=20).pack(side="left")
        self._btn(inner, "📦  Back up MAD code (launchers/ → ~/deck-config-backups)",
                  do_backup_mad, width=48).pack(anchor="w", pady=(8, 0))
        status.pack_configure(pady=12)


def main():
    fullscreen = (os.environ.get("ROUTER_GUI_FULLSCREEN") == "1"
                  or "--fullscreen" in sys.argv)
    root = tk.Tk()
    App(root, fullscreen)
    root.mainloop()


if __name__ == "__main__":
    main()
