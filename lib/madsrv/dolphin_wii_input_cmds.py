"""dolphin_wii.input_* -- Wii Classic-Controller PROFILE mapping for the "Wii / GameCube"
tile's Wii -> Button mapping page.

Profile-first editor: pick a Classic-Controller profile (Profiles/Wiimote/<name>.ini whose
[Profile] body carries `Extension = Classic`) and rebind its Classic bindings. A routed Wii
launch loads that profile onto a [WiimoteN] slot (lib/dolphin_wii_pads / dolphin_wii_source),
so an edit here takes effect in-game -- unlike editing the live [WiimoteN], which the swap
overwrites. Only Classic bindings are exposed: the bare Wiimote buttons + IR pointer live in the
live config for emulated-Wiimote use, and real Wiimotes (DolphinBar) need no mapping at all.

A remap replaces ONLY the RHS of one binding line inside the profile's [Profile] section, keeping
Device / Extension / *Calibration / Rumble byte-for-byte (cfgutil.ini_replace). Tokens follow the
profile's Device backend (evdev vs SDL names) and are backtick-wrapped iff not all-ASCII-alpha;
sticks/analog-triggers use Dolphin's legacy Axis form; a d-pad direction mirrors the profile's
existing token. The Device / Extension / Source lines are NEVER edited (routing keys on them).
Buffered X=Save / Y=Cancel; Start clears a binding. Refused while Dolphin runs.
"""
from __future__ import annotations

from pathlib import Path

from .. import dolphin_wii_profiles, proc_guard
from . import cfgutil, dolphin_input_core as _core
from .input_buffer import InputBuffer
from .rpc import RpcError, method

_PROC = "dolphin"
_PROFILE_SEC = "Profile"

_BUTTONS = [("Classic/Buttons/A", "A"), ("Classic/Buttons/B", "B"), ("Classic/Buttons/X", "X"),
            ("Classic/Buttons/Y", "Y"), ("Classic/Buttons/ZL", "ZL"), ("Classic/Buttons/ZR", "ZR"),
            ("Classic/Buttons/-", "Minus (-)"), ("Classic/Buttons/+", "Plus (+)"),
            ("Classic/Buttons/Home", "Home")]
_DPAD = [("Classic/D-Pad/Up", "D-pad Up"), ("Classic/D-Pad/Down", "D-pad Down"),
         ("Classic/D-Pad/Left", "D-pad Left"), ("Classic/D-Pad/Right", "D-pad Right")]
_LSTICK = [("Classic/Left Stick/Up", "Left stick Up"), ("Classic/Left Stick/Down", "Left stick Down"),
           ("Classic/Left Stick/Left", "Left stick Left"), ("Classic/Left Stick/Right", "Left stick Right")]
_RSTICK = [("Classic/Right Stick/Up", "Right stick Up"), ("Classic/Right Stick/Down", "Right stick Down"),
           ("Classic/Right Stick/Left", "Right stick Left"), ("Classic/Right Stick/Right", "Right stick Right")]
_TRIGGERS = [("Classic/Triggers/L", "L trigger (analog)"), ("Classic/Triggers/R", "R trigger (analog)")]

_BUTTON_KEYS = {k for k, _ in _BUTTONS}
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICK_KEYS = {k for k, _ in _LSTICK + _RSTICK}
_TRIGGER_KEYS = {k for k, _ in _TRIGGERS}
_ALL_KEYS = _BUTTON_KEYS | _DPAD_KEYS | _STICK_KEYS | _TRIGGER_KEYS
_LABEL = dict(_BUTTONS + _DPAD + _LSTICK + _RSTICK + _TRIGGERS)
_DPAD_ROW_FOR_DIR = {"up": "Classic/D-Pad/Up", "down": "Classic/D-Pad/Down",
                     "left": "Classic/D-Pad/Left", "right": "Classic/D-Pad/Right"}

# Current edit target = the InputBuffer ctx: ("profile", <name>) or ("none",) when the user
# has no Classic-Controller profiles yet. Resets on backend restart.
_edit_target: tuple = ("none",)


def _profile_path(name: str) -> Path:
    return dolphin_wii_profiles.profiles_dir() / f"{name}.ini"


def _pad_name(text: str) -> str:
    dev = cfgutil.ini_read(text, _PROFILE_SEC, "Device") or ""
    return dev.split("/", 2)[2] if dev.count("/") >= 2 else dev


def _token_for(key: str, kind: str, params, text: str) -> str:
    return _core.token_for(key, kind, params, text, _PROFILE_SEC,
                           button_keys=_BUTTON_KEYS, dpad_keys=_DPAD_KEYS,
                           stick_keys=_STICK_KEYS, trigger_keys=_TRIGGER_KEYS,
                           dpad_row_for_dir=_DPAD_ROW_FOR_DIR)


def _resolve_target(profiles: list) -> None:
    """Self-heal the target: keep a valid selected profile, else the first available (or
    ("none",) when the user has no Classic-Controller profiles). When it lands on the none-state
    with a stale dirty buffer (every profile vanished mid-edit), drop that buffer so the page does
    not strand an unclearable "unsaved changes" indicator on edits that can no longer be saved."""
    global _edit_target
    if _edit_target[0] == "profile" and _edit_target[1] in profiles:
        return
    _edit_target = ("profile", profiles[0]) if profiles else ("none",)
    if _edit_target[0] == "none" and _buf.dirty:
        _buf.reset()


@method("dolphin_wii.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    profiles = dolphin_wii_profiles.list_profiles()
    _resolve_target(profiles)
    run = proc_guard.emulator_running(_PROC)
    has = _edit_target[0] == "profile"
    text = _buf.get(_edit_target) if has else ""

    def row(key, label, kind):
        return {"id": key, "label": label, "kind": kind,
                "value": _core.token_label(cfgutil.ini_read(text, _PROFILE_SEC, key)) if has else "(unbound)",
                "capturable": not run and has}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Left stick", "binds": [row(k, l, "axis") for k, l in _LSTICK]},
        {"title": "Right stick", "binds": [row(k, l, "axis") for k, l in _RSTICK]},
        {"title": "Analog triggers", "binds": [row(k, l, "trigger") for k, l in _TRIGGERS]},
    ]
    selectors = [{
        "key": "profile", "label": "Edit profile", "scope": "global", "dependent": True,
        "options": [{"value": n, "label": n} for n in profiles] or [{"value": "", "label": "— none —"}],
        "value": _edit_target[1] if has else "",
    }]
    if run:
        note = "Close Dolphin first — it grabs the pad and rewrites its config on exit."
    elif not has:
        note = ("No Classic Controller profiles yet. Create one in Dolphin (Controllers → Wii Remote "
                "→ Emulated Wii Remote → Configure → Extension: Classic → Profile → Save), then it "
                "appears here to edit.")
    else:
        dev = _pad_name(text)
        note = (f"Editing profile '{_edit_target[1]}'"
                + (f" — device: {dev}.  " if dev else ".  ")
                + "A routed Wii launch loads this Classic Controller mapping, so the edit takes effect "
                  "in-game. Buttons rebind from any pad; capture sticks / triggers on that device. "
                  "Start clears a binding.")
    return {"running": run, "note": note, "groups": groups, "selectors": selectors,
            "clearable": has, "players": [], "buffered": True, "dirty": _buf.dirty}


@method("dolphin_wii.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    if _edit_target[0] != "profile":
        raise RpcError("EINVAL", "no Classic Controller profile selected")
    text = _buf.get(_edit_target)
    tok = _token_for(key, kind, params, text)
    is_raw = key in _DPAD_KEYS and kind == "hat"
    write_value = tok if is_raw else _core.fmt_token(tok)
    _buf.set(_edit_target, {"section": _PROFILE_SEC, "key": key, "value": write_value})
    display = _core.token_label(write_value)
    return {"id": key, "value": display, "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} ← {display}"}


@method("dolphin_wii.input_clear", slow=True)
def _input_clear(params):
    """Start-to-clear a Classic Controller binding (blank the value = unbound)."""
    key = params.get("id") or params.get("key") or ""
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    if _edit_target[0] != "profile":
        raise RpcError("EINVAL", "no Classic Controller profile selected")
    _buf.set(_edit_target, {"section": _PROFILE_SEC, "key": key, "value": ""})
    return {"id": key, "value": "(unbound)", "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} cleared"}


@method("dolphin_wii.selector_set", slow=True)
def _selector_set(params):
    """Switch which Classic Controller profile the page edits. Refused with unsaved edits so a
    switch never silently drops them; the C++ dependent selector re-fetches the profile's binds."""
    if params.get("key") != "profile":
        raise RpcError("EINVAL", f"{params.get('key')!r} is not a selector here")
    global _edit_target
    if _buf.dirty:
        raise RpcError("EBUSY", "save (X) or cancel (Y) before switching profiles")
    name = params.get("value", "")
    if not name or name not in dolphin_wii_profiles.list_profiles():
        raise RpcError("EINVAL", f"profile {name!r} not found")
    _edit_target = ("profile", name)
    return {"key": "profile", "value": name, "dirty": _buf.dirty,
            "message": f"editing profile '{name}'"}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). ctx = ("profile", <name>).
# ---------------------------------------------------------------------------
def _apply(text: str, edit: dict) -> str:
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Dolphin first — it grabs the pad and rewrites config on exit")
    nt = cfgutil.ini_replace(text, edit["section"], edit["key"], edit.get("value", ""))
    if nt is None:
        raise RpcError("ENOKEY", f"{edit['key']!r} not present in [{edit['section']}]")
    return nt


def _load(ctx: tuple) -> str:
    if not ctx or ctx[0] != "profile":
        raise RpcError("EINVAL", "no Classic Controller profile selected")
    p = _profile_path(ctx[1])
    text = cfgutil.read_text(p)
    if text is None:
        raise RpcError("ENOENT", f"{p} not found")
    return text


def _apply_edit(text: str, edit: dict):
    return _apply(text, edit), edit


def _flush(ctx: tuple, disk: str, edits: list) -> str:
    p = _profile_path(ctx[1])
    text = cfgutil.read_text(p)
    if text is None:
        raise RpcError("ENOENT", f"{p} not found")
    for edit in edits:
        text = _apply(text, edit)
    cfgutil.ensure_bak(p)
    cfgutil.atomic_write(p, text)
    return text


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("dolphin_wii.input_save", slow=True)
def _input_save(params):
    if _edit_target[0] != "profile":
        return {"saved": False, "dirty": _buf.dirty}
    return {"saved": _buf.save(_edit_target), "dirty": _buf.dirty}


@method("dolphin_wii.input_cancel", slow=True)
def _input_cancel(params):
    if _edit_target[0] == "profile":
        _buf.cancel(_edit_target)
    elif _buf.dirty:
        _buf.reset()   # none-state: clear a phantom dirty left by a vanished profile
    return {"cancelled": True, "dirty": _buf.dirty}
