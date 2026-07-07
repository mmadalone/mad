"""pcsx2x6.input_* — input mapping for pcsx2x6 (Namco System 246/256 fork).

Two kinds of controller, chosen in the page's player picker:

  • Controller Port 1 / 2 — DualShock2 PADS (Tekken, Soul Calibur, …). Remaps live in
    the per-PLAYER override store (pcsx2_cfg.load/save_input_overrides) and are applied
    at launch to whatever slot the player lands in (same design as the RPCS3 page).

  • USB Port 1 / 2 — each presents None / HID Mouse / Light Gun (GunCon2). A *dependent*
    Type selector swaps the rows; the bindings (mouse buttons, or the gun's trigger /
    pedal / start / coins / relative-aim — all keyboard/mouse InputManager sources) are
    written DIRECTLY to [USB1]/[USB2]. USB ports are FIXED (not reassigned at launch),
    so they need no override store.

The gun/mouse rows use kind="gun" (the C++ page's pointer-capture, which reads a mouse
button OR a key); the pad rows use btn/hat/axis. pcsx2x6 rewrites its ini on EXIT, so
staging refuses while it's running.

Buffered X=Save / Y=Cancel editor. The page carries a MIXED store: the pad ports write a
JSON override sidecar, the USB ports write [USBn] in the fork ini. Each store gets its OWN
InputBuffer (both keyed on ctx = the arcade ini PATH, the store identity): _pad_buf over the
override dict, _usb_buf over the ini text. A pad edit routes to _pad_buf, a USB edit to
_usb_buf; save/cancel flush/discard the buffer(s) the page touched. Nothing reaches disk
until input_save; input_cancel drops the staged edits. The EBUSY running-guard lives in each
_apply so it fires at BOTH stage and save.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .. import pcsx2_cfg, proc_guard
from . import cfgutil
from .input_buffer import InputBuffer
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label,
                              usb_keyboard_source, usb_mouse_button_source, usb_source_label)
from .rpc import RpcError, method

_INI = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
_SLOT_SECTIONS = ["Pad1", "Pad2"]   # player i+1 -> slot (for the one-time pad migration)

# DualShock2 pad rows -----------------------------------------------------------
_BUTTONS = [
    ("Cross", "Cross  ✕"), ("Circle", "Circle  ○"),
    ("Triangle", "Triangle  △"), ("Square", "Square  ▢"),
    ("L1", "L1"), ("R1", "R1"), ("L2", "L2"), ("R2", "R2"),
    ("L3", "L3"), ("R3", "R3"), ("Select", "Select"), ("Start", "Start"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
_DPAD = [("Up", "D-pad Up"), ("Down", "D-pad Down"),
         ("Left", "D-pad Left"), ("Right", "D-pad Right")]
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICKS = [
    ("LUp", "L-stick Up"), ("LDown", "L-stick Down"),
    ("LLeft", "L-stick Left"), ("LRight", "L-stick Right"),
    ("RUp", "R-stick Up"), ("RDown", "R-stick Down"),
    ("RLeft", "R-stick Left"), ("RRight", "R-stick Right"),
]
_STICK_KEYS = {k for k, _ in _STICKS}

# USB device rows ---------------------------------------------------------------
_GUNCON2_BINDS = [
    ("guncon2_Trigger", "Trigger"), ("guncon2_A", "Foot Pedal"),
    ("guncon2_Start", "Start"), ("guncon2_Select", "Coins"),
    # The relative-aim ("Aim …") keys are intentionally NOT offered: binding ANY of
    # guncon2_Relative{Up,Down,Left,Right} flips the GunCon2 cursor to the relative path
    # and FREEZES the lightgun crosshair (guncon2.cpp has_relative_binds). S246/256 always
    # uses the absolute gun; switch_bind strips these at launch as a backstop.
]
_HIDMOUSE_BTNS = [
    ("hidmouse_LeftButton", "Left Button"), ("hidmouse_RightButton", "Right Button"),
    ("hidmouse_MiddleButton", "Middle Button"),
]
_USB_BIND_KEYS = {k for k, _ in _GUNCON2_BINDS} | {k for k, _ in _HIDMOUSE_BTNS}
_USB_LABELS = dict(_GUNCON2_BINDS + _HIDMOUSE_BTNS + [("hidmouse_Pointer", "Pointer")])
_TYPE_OPTS = [("None", "None"), ("hidmouse", "HID Mouse"), ("guncon2", "Light Gun")]
_TYPE_VALUES = {v for v, _ in _TYPE_OPTS}

# The page's player picker: two DualShock2 ports + two USB ports.
_PLAYER_PICK = [
    {"id": "pad1", "label": "Controller Port 1"},
    {"id": "pad2", "label": "Controller Port 2"},
    {"id": "usb1", "label": "USB Port 1"},
    {"id": "usb2", "label": "USB Port 2"},
]
_PICK_IDS = {p["id"] for p in _PLAYER_PICK}


def _running() -> bool:
    return proc_guard.process_running("pcsx2x6", exact=False)


def _sel(params) -> str:
    s = (params.get("player") or "pad1").strip()
    return s if s in _PICK_IDS else "pad1"


def _usb_section(sel: str) -> str:
    return "USB" + sel[-1]            # usb1 -> USB1


# ── DualShock2 pad page (per-player override store) ──────────────────────────
def _pad_get(sel: str, run: bool) -> dict:
    ovr = _pad_buf.get(_INI)           # buffer-over-store: reflects staged, unsaved remaps
    defaults = pcsx2_cfg.baked_default_sources()
    player = int(sel[-1])
    pov = ovr.get(player, {})

    def row(key, label, kind):
        src = pov.get(key) or defaults.get(key, "")
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—", "capturable": not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis") for k, l in _STICKS]},
    ]
    note = ("Close pcsx2x6 first, it rewrites this file on exit." if run else
            f"Remaps Controller Port {player}; applied at launch to whichever pad the "
            "Controllers page assigns to this player.")
    return {"running": run, "note": note, "groups": groups,
            "players": _PLAYER_PICK, "player": sel}


# ── USB device page (Light Gun / HID Mouse) ──────────────────────────────────
def _usb_get(sel: str, run: bool) -> dict:
    text = _usb_buf.get(_INI)          # buffer-over-disk: a STAGED Type change swaps the rows
    section = _usb_section(sel)
    cur = (cfgutil.ini_read(text, section, "Type") or "None").strip() or "None"
    type_opts = list(_TYPE_OPTS)
    if cur not in _TYPE_VALUES:                    # surface an unlisted on-disk value
        type_opts = [(cur, cur)] + type_opts
    selectors = [{"key": "usb_type", "label": "Controller", "scope": "player",
                  "value": cur, "dependent": True,
                  "options": [{"value": v, "label": l} for v, l in type_opts]}]

    def gun_row(key, label, capturable=True):
        src = (cfgutil.ini_read(text, section, key) or "").strip()
        return {"id": key, "label": label, "kind": "gun",
                "value": usb_source_label(src) if src else "—",
                "capturable": capturable and not run}

    groups = []
    if cur == "guncon2":
        groups = [{"title": "Light gun", "binds": [gun_row(k, l) for k, l in _GUNCON2_BINDS]}]
    elif cur == "hidmouse":
        groups = [{"title": "HID mouse", "binds":
                   [gun_row("hidmouse_Pointer", "Pointer (aim)", capturable=False)]
                   + [gun_row(k, l) for k, l in _HIDMOUSE_BTNS]}]
    note = ("Close pcsx2x6 first, it rewrites this file on exit." if run else {
        "None": "Pick HID Mouse or Light Gun to configure this USB port.",
        "hidmouse": "HID mouse: bind the buttons (aim uses this port's pointer device). "
                    "A binding writes USB Port " + sel[-1] + "'s pointer slot, not a "
                    "specific mouse, so press any mouse button.",
        "guncon2": "Light gun (GunCon2): bind trigger / pedal / start / coins. The Sinden "
                   "gun uses this port; a binding targets USB Port " + sel[-1] + "'s "
                   "pointer slot, so pull any gun's trigger.",
    }.get(cur, "USB Port " + sel[-1] + "."))
    # When this port is a Light Gun, surface the one-press "Start Sinden guns" action
    # right here (it moved off the Lightgun page). The C++ input page renders an
    # "actions" entry as a button that fires its rpc directly (sinden.driver). Derived
    # from the STAGED type (cur), not the driver state, so it is fine over the buffer.
    actions = ([{"type": "action", "key": "start_sinden", "label": "▶ Start Sinden guns",
                 "rpc": "sinden.driver", "args": {"action": "start"}}]
               if cur == "guncon2" else [])
    return {"running": run, "note": note, "groups": groups, "selectors": selectors,
            "actions": actions, "players": _PLAYER_PICK, "player": sel}


@method("pcsx2x6.input_get", slow=True)   # buffered: NO cache=("config",) — the buffers ARE the cache
def _input_get(params):
    sel = _sel(params)
    run = _running()
    pay = _usb_get(sel, run) if sel.startswith("usb") else _pad_get(sel, run)
    pay["buffered"] = True
    pay["dirty"] = _pad_buf.dirty or _usb_buf.dirty    # combined page spans both stores
    return pay


# ── set ──────────────────────────────────────────────────────────────────────
def _pad_set(params, sel: str) -> dict:
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key in _DPAD_KEYS and kind == "hat":
        source = pcsx2_dpad_source(str(params.get("value", "")))
        if source is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _STICK_KEYS and kind == "axis":
        parsed = parse_axis_token(str(params.get("value", "")))
        if parsed is None:
            raise RpcError("EINVAL", "push the stick in that direction")
        source = pcsx2_axis_source(*parsed)
        if source is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
    elif key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        source = sdl_button_source(code)
        if source is None:
            raise RpcError("EINVAL", "that input can't be mapped; press a face, shoulder, "
                                     "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable pad input")
    player = int(sel[-1])
    # stage in the pad-override buffer; the EBUSY guard fires inside _pad_apply.
    _pad_buf.set(_INI, {"player": player, "id": key, "source": source})
    return {"id": key, "value": sdl_source_label(source), "dirty": _pad_buf.dirty,
            "message": f"{key} → {sdl_source_label(source)}"}


def _usb_set(params, sel: str) -> dict:
    key = params.get("id", "")
    if key not in _USB_BIND_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable USB input")
    gun_kind = params.get("gun_kind", "")
    value = params.get("value", "")
    if gun_kind == "mouse":
        try:
            source = usb_mouse_button_source(int(value), int(sel[-1]) - 1)
        except (TypeError, ValueError):
            source = None
    elif gun_kind == "key":
        source = usb_keyboard_source(str(value))
    else:
        raise RpcError("EINVAL", "press a mouse button or a key")
    if source is None:
        raise RpcError("EINVAL", "that input can't be mapped to this control")
    if not _INI.is_file():
        raise RpcError("ENOENT", "pcsx2x6 config not found — launch a game once")
    section = _usb_section(sel)
    # stage the [USBn] binding; the EBUSY guard + section-present check fire inside _usb_apply.
    _usb_buf.set(_INI, {"section": section, "key": key, "value": source})
    label = _USB_LABELS.get(key, key)
    return {"id": key, "value": usb_source_label(source), "dirty": _usb_buf.dirty,
            "message": f"{label} → {usb_source_label(source)}"}


@method("pcsx2x6.input_set", slow=True)
def _input_set(params):
    sel = _sel(params)
    return _usb_set(params, sel) if sel.startswith("usb") else _pad_set(params, sel)


# ── buffered store plumbing (X=Save / Y=Cancel) ──────────────────────────────
# Two stores share this page, so two buffers, each keyed on ctx = the arcade ini PATH:
#   _pad_buf — the per-player JSON override sidecar (a dict working copy);
#   _usb_buf — the fork ini [USBn] sections (a text working copy).
# Both _apply functions are pure (NO disk I/O, NO staterev bump) and keep the EBUSY guard at
# the top so it fires at BOTH stage and save; flush RE-READS fresh disk and REPLAYS the staged
# edits, so a foreign edit to untouched keys survives.
def _pad_load(ctx) -> dict:
    """The override store as `{player(int): {ps2_button: sdl_source}}` (seeded once from any
    existing [PadN] SDL block; a no-op for pcsx2x6's keyboard [Pad1])."""
    return pcsx2_cfg.migrate_overrides_from_ini(Path(ctx), _SLOT_SECTIONS)


def _pad_apply(ovr: dict, edit: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    ovr = copy.deepcopy(ovr)                       # pure: never mutate the caller's working/disk
    ovr.setdefault(int(edit["player"]), {})[edit["id"]] = edit["source"]
    return ovr


def _pad_apply_edit(ovr, edit):
    return _pad_apply(ovr, edit), edit


def _pad_flush(ctx, disk, edits) -> dict:
    ini = Path(ctx)
    fresh = pcsx2_cfg.migrate_overrides_from_ini(ini, _SLOT_SECTIONS)   # replay onto FRESH store
    for edit in edits:
        fresh = _pad_apply(fresh, edit)
    pcsx2_cfg.save_input_overrides(ini, fresh)
    return fresh


def _usb_load(ctx) -> str:
    ini = Path(ctx)                                # tolerant of a missing ini so get() still renders
    return ini.read_text(encoding="utf-8", errors="replace") if ini.is_file() else ""


def _usb_apply(text: str, edit: dict) -> str:
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    section = edit["section"]
    new = cfgutil.ini_set_or_insert(text, section, edit["key"], edit["value"])
    if new is None:
        raise RpcError("ENOKEY", f"[{section}] section not found in the config")
    return new


def _usb_apply_edit(text, edit):
    return _usb_apply(text, edit), edit


def _usb_flush(ctx, disk, edits) -> str:
    ini = Path(ctx)
    if not ini.is_file():
        raise RpcError("ENOENT", "pcsx2x6 config not found — launch a game once")
    text = ini.read_text(encoding="utf-8", errors="replace")           # replay onto FRESH disk
    for edit in edits:
        text = _usb_apply(text, edit)
    cfgutil.ensure_bak(ini)
    cfgutil.atomic_write(ini, text)
    return text


_pad_buf = InputBuffer(load=_pad_load, apply_edit=_pad_apply_edit, flush=_pad_flush)
_usb_buf = InputBuffer(load=_usb_load, apply_edit=_usb_apply_edit, flush=_usb_flush)


# ── per-port pages (Input group leaves: Controller Port 1/2 + USB Port 1/2) ──────
# Same arcade logic as pcsx2x6.input_*, but each namespace is PINNED to one port and omits the
# player picker (the C++ shows the picker only when >1 player). The combined pcsx2x6.input_* page
# stays registered (unchanged) for anything still using it. Each per-port page routes its
# save/cancel to the ONE buffer its store lives in (pad -> _pad_buf, usb -> _usb_buf).
def _single_port(sel: str) -> dict:
    run = _running()
    pay = _usb_get(sel, run) if sel.startswith("usb") else _pad_get(sel, run)
    pay.pop("players", None)          # no picker -> a single-port page
    pay.pop("player", None)
    buf = _usb_buf if sel.startswith("usb") else _pad_buf
    pay["buffered"] = True
    pay["dirty"] = buf.dirty
    return pay


def _register_port(ns: str, sel: str) -> None:
    buf = _usb_buf if sel.startswith("usb") else _pad_buf

    @method(f"{ns}.input_get", slow=True)   # buffered: NO cache=("config",)
    def _g(params, sel=sel):
        return _single_port(sel)

    @method(f"{ns}.input_set", slow=True)
    def _s(params, sel=sel):
        p = dict(params)
        p["player"] = sel
        return _usb_set(p, sel) if sel.startswith("usb") else _pad_set(p, sel)

    @method(f"{ns}.selector_set", slow=True)
    def _sv(params, sel=sel):
        p = dict(params)
        p["player"] = sel
        return _selector_set(p)

    @method(f"{ns}.input_save", slow=True)
    def _save(params, buf=buf):
        return {"saved": buf.save(_INI), "dirty": buf.dirty}

    @method(f"{ns}.input_cancel", slow=True)
    def _cancel(params, buf=buf):
        buf.cancel(_INI)
        return {"cancelled": True, "dirty": buf.dirty}


for _pn, _ps in (("x6a_pad1", "pad1"), ("x6a_pad2", "pad2"),
                 ("x6a_usb1", "usb1"), ("x6a_usb2", "usb2")):
    _register_port(_pn, _ps)


@method("pcsx2x6.selector_set", slow=True)
def _selector_set(params):
    if params.get("key") != "usb_type":
        raise RpcError("EINVAL", f"unknown selector {params.get('key')!r}")
    value = str(params.get("value", "")).strip()
    if value not in _TYPE_VALUES:
        raise RpcError("EINVAL", f"unknown controller type {value!r}")
    sel = _sel(params)
    if not sel.startswith("usb"):
        raise RpcError("EINVAL", "controller type only applies to a USB port")
    if not _INI.is_file():
        raise RpcError("ENOENT", "pcsx2x6 config not found — launch a game once")
    section = _usb_section(sel)
    # stage the dependent Type change; the EBUSY guard fires inside _usb_apply.
    _usb_buf.set(_INI, {"section": section, "key": "Type", "value": value})
    disp = dict(_TYPE_OPTS).get(value, value)
    return {"key": "usb_type", "value": value, "dirty": _usb_buf.dirty,
            "message": f"USB Port {sel[-1]} → {disp}"}


# ── combined-page save/cancel: the picker spans BOTH stores, so commit / discard both ──
@method("pcsx2x6.input_save", slow=True)
def _input_save(params):
    saved_pad = _pad_buf.save(_INI)
    saved_usb = _usb_buf.save(_INI)
    return {"saved": bool(saved_pad or saved_usb),
            "dirty": _pad_buf.dirty or _usb_buf.dirty}


@method("pcsx2x6.input_cancel", slow=True)
def _input_cancel(params):
    _pad_buf.cancel(_INI)
    _usb_buf.cancel(_INI)
    return {"cancelled": True, "dirty": _pad_buf.dirty or _usb_buf.dirty}
