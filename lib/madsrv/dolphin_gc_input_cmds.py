"""dolphin.input_* -- GameCube controller mapping for the "Wii / GameCube" tile's
GameCube -> Button mapping page.

Two edit targets, chosen by the "Edit profile" selector:
  * LIVE PAD (default): the live GCPadNew.ini [GCPad1..4] port sections; a "Player N"
    stepper picks the port. This is what Dolphin reads when no controller routing is
    active. A ROUTED launch overwrites these ports with the assigned profile (reverted
    at game-end), so a live-pad edit is shadowed in-game for a routed port -- pick a
    profile below to edit what the launch actually loads.
  * A PROFILE: a Profiles/GCPad/<name>.ini [Profile] section -- exactly what the launch
    swap loads onto a port, so an edit here takes effect in-game. A profile is
    device-specific, not port-specific, so the Player stepper is hidden in this mode.

A remap replaces ONLY the RHS of one binding line inside the target section, keeping
Device / *Calibration / Rumble / *Modifier byte-for-byte (cfgutil.ini_replace). Tokens
follow the target section's Device backend (evdev names vs SDL names) and are
backtick-wrapped iff not all-ASCII-alpha. Sticks/analog-triggers use Dolphin's legacy
`Axis <rank><sign>` / `Full Axis <rank>+` form; a d-pad direction mirrors the section's
existing D-Pad token. The Device line is NEVER edited (launch routing keys on it).
Buffered X=Save / Y=Cancel; Start clears a binding. Refused while Dolphin runs (it grabs
the pad and rewrites GCPadNew.ini on exit).
"""
from __future__ import annotations

from pathlib import Path

from .. import dolphin_profiles, proc_guard
from . import cfgutil, dolphin_input_core as _core
from .input_buffer import InputBuffer
from .rpc import RpcError, method

_FILE = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/GCPadNew.ini"
_PROC = "dolphin"
_PORTS = (1, 2, 3, 4)
_PROFILE_SEC = "Profile"

_BUTTONS = [("Buttons/A", "A"), ("Buttons/B", "B"), ("Buttons/X", "X"), ("Buttons/Y", "Y"),
            ("Buttons/Z", "Z"), ("Buttons/Start", "Start"),
            ("Triggers/L", "L (digital)"), ("Triggers/R", "R (digital)")]
_DPAD = [("D-Pad/Up", "D-pad Up"), ("D-Pad/Down", "D-pad Down"),
         ("D-Pad/Left", "D-pad Left"), ("D-Pad/Right", "D-pad Right")]
_MAIN_STICK = [("Main Stick/Up", "Main stick Up"), ("Main Stick/Down", "Main stick Down"),
               ("Main Stick/Left", "Main stick Left"), ("Main Stick/Right", "Main stick Right")]
_C_STICK = [("C-Stick/Up", "C-stick Up"), ("C-Stick/Down", "C-stick Down"),
            ("C-Stick/Left", "C-stick Left"), ("C-Stick/Right", "C-stick Right")]
_TRIGGERS_ANALOG = [("Triggers/L-Analog", "L trigger (analog)"),
                    ("Triggers/R-Analog", "R trigger (analog)")]

_BUTTON_KEYS = {k for k, _ in _BUTTONS}
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICK_KEYS = {k for k, _ in _MAIN_STICK + _C_STICK}
_TRIGGER_KEYS = {k for k, _ in _TRIGGERS_ANALOG}
_ALL_KEYS = _BUTTON_KEYS | _DPAD_KEYS | _STICK_KEYS | _TRIGGER_KEYS
_LABEL = dict(_BUTTONS + _DPAD + _MAIN_STICK + _C_STICK + _TRIGGERS_ANALOG)
_DPAD_ROW_FOR_DIR = {"up": "D-Pad/Up", "down": "D-Pad/Down",
                     "left": "D-Pad/Left", "right": "D-Pad/Right"}

# The current edit target, also the InputBuffer ctx: ("port",) = the live GCPadNew.ini
# (a Player stepper picks the [GCPadN] section); ("profile", <name>) = a
# Profiles/GCPad/<name>.ini [Profile]. Resets to the live pad on backend restart. The
# "Edit profile" selector switches it (refused while there are unsaved edits).
_edit_target: tuple = ("port",)


def _section(player) -> str:
    return f"GCPad{player}"


def _profile_path(name: str) -> Path:
    return dolphin_profiles.profiles_dir() / f"{name}.ini"


def _ports_present(text: str) -> list[int]:
    return [n for n in _PORTS if cfgutil._ini_span(text, _section(n))]


def _players_and_target(text: str, params) -> tuple[list[dict], str]:
    present = _ports_present(text) or [1]
    players = [{"id": str(n), "label": f"Player {n}"} for n in present]
    valid = {str(n) for n in present}
    sel = params.get("player") or str(present[0])
    if sel not in valid:
        sel = str(present[0])
    return players, sel


def _pad_name(text: str, sec: str) -> str:
    dev = cfgutil.ini_read(text, sec, "Device") or ""
    return dev.split("/", 2)[2] if dev.count("/") >= 2 else dev


def _token_for(key: str, kind: str, params, text: str, sec: str) -> str:
    return _core.token_for(key, kind, params, text, sec,
                           button_keys=_BUTTON_KEYS, dpad_keys=_DPAD_KEYS,
                           stick_keys=_STICK_KEYS, trigger_keys=_TRIGGER_KEYS,
                           dpad_row_for_dir=_DPAD_ROW_FOR_DIR)


def _target_section(params, text: str) -> str:
    """The config section the current mode edits: [Profile] in profile mode, else the
    selected [GCPadN] port in live-pad mode."""
    if _edit_target[0] == "profile":
        return _PROFILE_SEC
    _, player = _players_and_target(text, params)
    return _section(player)


def _heal_target() -> None:
    """Self-heal a selected profile that vanished externally (deleted/renamed in Dolphin's own UI)
    so the page always renders and the "Edit profile" selector stays reachable: fall back to the
    always-present live pad. Only heals when the profile is truly gone, so it never drops a
    still-saveable edit (switching ctx reloads the buffer, clearing the now-uncommittable one)."""
    global _edit_target
    if _edit_target[0] == "profile" and _edit_target[1] not in dolphin_profiles.list_profiles():
        _edit_target = ("port",)


@method("dolphin.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    _heal_target()
    text = _buf.get(_edit_target)
    run = proc_guard.emulator_running(_PROC)
    profile_mode = _edit_target[0] == "profile"
    if profile_mode:
        sec = _PROFILE_SEC
        players: list = []
        player = ""
    else:
        players, player = _players_and_target(text, params)
        sec = _section(player)

    def row(key, label, kind):
        return {"id": key, "label": label, "kind": kind,
                "value": _core.token_label(cfgutil.ini_read(text, sec, key)),
                "capturable": not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Main stick", "binds": [row(k, l, "axis") for k, l in _MAIN_STICK]},
        {"title": "C-stick", "binds": [row(k, l, "axis") for k, l in _C_STICK]},
        {"title": "Analog triggers", "binds": [row(k, l, "trigger") for k, l in _TRIGGERS_ANALOG]},
    ]
    selectors = [{
        "key": "profile", "label": "Edit profile", "scope": "global", "dependent": True,
        "options": [{"value": "", "label": "— live pad —"}]
                   + [{"value": n, "label": n} for n in dolphin_profiles.list_profiles()],
        "value": _edit_target[1] if profile_mode else "",
    }]
    dev = _pad_name(text, sec)
    if run:
        note = "Close Dolphin first — it grabs the pad and rewrites its config on exit."
    elif profile_mode:
        note = (f"Editing profile '{_edit_target[1]}'"
                + (f" — device: {dev}.  " if dev else ".  ")
                + "A routed launch loads this, so the edit takes effect in-game. Buttons rebind "
                  "from any pad; capture sticks / triggers on that device. Start clears a binding.")
    else:
        note = ((f"Port {player}: {dev}.  " if dev else "")
                + "Editing the live pad. Press a button / stick / d-pad to rebind, Start to clear, "
                  "or pick a profile above to edit one that survives a routed launch.")
    return {"running": run, "note": note, "groups": groups, "selectors": selectors,
            "clearable": True, "players": players, "player": player,
            "buffered": True, "dirty": _buf.dirty}


@method("dolphin.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    text = _buf.get(_edit_target)
    sec = _target_section(params, text)
    tok = _token_for(key, kind, params, text, sec)
    # A d-pad mirror reuses an EXISTING (already Dolphin-formatted) token -> write it verbatim;
    # every other capture is a fresh bare token that fmt_token wraps.
    is_raw = key in _DPAD_KEYS and kind == "hat"
    write_value = tok if is_raw else _core.fmt_token(tok)
    _buf.set(_edit_target, {"section": sec, "key": key, "value": write_value})
    display = _core.token_label(write_value)
    return {"id": key, "value": display, "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} ← {display}"}


@method("dolphin.input_clear", slow=True)
def _input_clear(params):
    """Start-to-clear a GameCube binding (blank the value = unbound)."""
    key = params.get("id") or params.get("key") or ""
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    text = _buf.get(_edit_target)
    sec = _target_section(params, text)
    _buf.set(_edit_target, {"section": sec, "key": key, "value": ""})
    return {"id": key, "value": "(unbound)", "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} cleared"}


@method("dolphin.selector_set", slow=True)
def _selector_set(params):
    """Switch which target the page edits: "" = the live pad (GCPadNew.ini ports), else a
    named profile (Profiles/GCPad/<name>.ini). Refused with unsaved edits so a switch never
    silently drops them; the C++ dependent selector re-fetches the new target's binds."""
    if params.get("key") != "profile":
        raise RpcError("EINVAL", f"{params.get('key')!r} is not a selector here")
    global _edit_target
    if _buf.dirty:
        raise RpcError("EBUSY", "save (X) or cancel (Y) before switching what you edit")
    name = params.get("value", "")
    if not name:
        _edit_target = ("port",)
        return {"key": "profile", "value": "", "dirty": _buf.dirty,
                "message": "editing the live pad"}
    if name not in dolphin_profiles.list_profiles():
        raise RpcError("EINVAL", f"profile {name!r} not found")
    _edit_target = ("profile", name)
    return {"key": "profile", "value": name, "dirty": _buf.dirty,
            "message": f"editing profile '{name}'"}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). ctx = _edit_target — the live
# GCPadNew.ini ("port",) or a profile file ("profile", <name>).
# ---------------------------------------------------------------------------
def _ctx_path(ctx: tuple) -> Path:
    return _profile_path(ctx[1]) if ctx and ctx[0] == "profile" else _FILE


def _apply(text: str, edit: dict) -> str:
    """Apply one staged binding edit to `text`. Pure. Refuses while Dolphin runs (fires at
    BOTH stage and save-replay)."""
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Dolphin first — it grabs the pad and rewrites config on exit")
    nt = cfgutil.ini_replace(text, edit["section"], edit["key"], edit.get("value", ""))
    if nt is None:
        raise RpcError("ENOKEY", f"{edit['key']!r} not present in [{edit['section']}]")
    return nt


def _load(ctx: tuple) -> str:
    p = _ctx_path(ctx)
    if not (ctx and ctx[0] == "profile"):
        # Live pad: consume a leftover dock swap (a crash-orphaned undocked-profile snapshot)
        # so edits land on the TRUE resting config, not a transient profile.
        try:
            from .. import dolphin_gc_dock
            if not proc_guard.emulator_running(_PROC):
                dolphin_gc_dock.restore()
        except Exception:
            pass
    text = cfgutil.read_text(p)
    if text is None:
        raise RpcError("ENOENT", f"{p} not found — launch a game once")
    return text


def _apply_edit(text: str, edit: dict):
    return _apply(text, edit), edit


def _flush(ctx: tuple, disk: str, edits: list) -> str:
    p = _ctx_path(ctx)
    text = cfgutil.read_text(p)
    if text is None:
        raise RpcError("ENOENT", f"{p} not found")
    for edit in edits:
        text = _apply(text, edit)
    cfgutil.ensure_bak(p)
    cfgutil.atomic_write(p, text)
    return text


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("dolphin.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_edit_target), "dirty": _buf.dirty}


@method("dolphin.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_edit_target)
    return {"cancelled": True, "dirty": _buf.dirty}
