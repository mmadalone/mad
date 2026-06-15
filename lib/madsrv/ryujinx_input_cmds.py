"""ryujinx.input_* — per-button input mapping for Ryujinx (Switch).

Edits Player 1's joycon button bindings in `input_config[]` of
~/.config/Ryujinx/Config.json. Each button lives in `left_joycon`/`right_joycon`
as KEY = Switch button, VALUE = `GamepadButtonInputId` token (the physical SDL
button). A remap sets that KEY's VALUE to the captured physical button's token
(input_translate.ryujinx_button) — re-pointing which physical button drives the
Switch action.

Ryujinx is NOT router-managed (Switch is `router_skip = true`, no ryujinx_cfg /
[backends.ryujinx]) → no launch-time clobber. Ryujinx rewrites Config.json on
exit, so we refuse while it's running.
"""
from __future__ import annotations

import copy

from .. import proc_guard
from . import ryujinx_json
from .input_translate import ryujinx_button
from .rpc import RpcError, method

_PROC = "ryujinx"

# (Switch-button key, label, which joycon object it lives in). The remappable
# digital buttons; sticks/d-pad are read-only (capture skips hats).
_BUTTONS = [
    ("button_a", "A", "right_joycon"), ("button_b", "B", "right_joycon"),
    ("button_x", "X", "right_joycon"), ("button_y", "Y", "right_joycon"),
    ("button_l", "L", "left_joycon"), ("button_r", "R", "right_joycon"),
    ("button_zl", "ZL", "left_joycon"), ("button_zr", "ZR", "right_joycon"),
    ("button_minus", "Minus −", "left_joycon"), ("button_plus", "Plus +", "right_joycon"),
]
_BUTTON_MAP = {k: jc for k, _, jc in _BUTTONS}

# Players exposed in the mapping page (Ryujinx supports Player1..Player8 + the
# Handheld slot). Selected via the page's player stepper.
_PLAYERS = [{"id": f"Player{n}", "label": f"Player {n}"} for n in range(1, 9)] + \
           [{"id": "Handheld", "label": "Handheld"}]
_PLAYER_IDS = {p["id"] for p in _PLAYERS}
_DEFAULT_PLAYER = "Player1"
# Ryujinx ControllerType tokens (stored as the enum NAME in Config.json; verified
# against Ryujinx ControllerType.cs). Exposed as the page's "Type" selector.
_CTYPES = [("ProController", "Pro Controller"), ("JoyconPair", "JoyCon Pair"),
           ("JoyconLeft", "Left JoyCon"), ("JoyconRight", "Right JoyCon"),
           ("Handheld", "Handheld")]
_CTYPE_IDS = {t for t, _ in _CTYPES}
# An id that matches no live joystick → the slot is "unbound" until the launch
# wrapper assigns it a real device. Used when a new player is created here just to
# hold a button layout (the button maps are the point; the device is wrapper-managed).
_UNBOUND_ID = "0-00000000-0000-0000-0000-000000000000"


def _player_param(params) -> str:
    p = params.get("player") or _DEFAULT_PLAYER
    if p not in _PLAYER_IDS:
        raise RpcError("EINVAL", f"unknown player {p!r}")
    return p


def _plabel(player: str) -> str:
    return next((p["label"] for p in _PLAYERS if p["id"] == player), player)


def _find(data: dict, pidx: str) -> dict | None:
    for ic in (data.get("input_config") or []):
        if ic.get("player_index") == pidx:
            return ic
    return None


def _player1(data: dict) -> dict | None:
    """The 'Player1' entry (fallback: the first entry) — the layout template."""
    return _find(data, "Player1") or (data.get("input_config") or [None])[0]


@method("ryujinx.input_get", slow=True, cache=("config",))
def _input_get(params):
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable — launch a game once")
    if _player1(data) is None:
        raise RpcError("EINVAL", "no controller configured in Ryujinx yet")
    player = _player_param(params)
    entry = _find(data, player)
    run = proc_guard.emulator_running(_PROC)
    plabel = _plabel(player)

    def row(key, label, jc):
        return {"id": key, "label": label, "kind": "btn",
                "value": (entry.get(jc, {}).get(key) if entry else None) or "—",
                "capturable": run is False}

    binds = [row(k, l, jc) for k, l, jc in _BUTTONS]
    if run:
        note = "Close Ryujinx first — it rewrites its config on exit."
    elif entry is None:
        note = f"{plabel} not configured yet — remap a button to create it (layout cloned from Player 1)."
    else:
        note = f"Remaps {plabel} ({entry.get('controller_type', 'controller')})."
    ctype = (entry.get("controller_type") if entry else None) or "ProController"
    opts = list(_CTYPES)
    if ctype not in _CTYPE_IDS:                    # surface an unlisted on-disk type
        opts = [(ctype, ctype)] + opts
    selectors = [{"key": "controller_type", "label": "Type", "scope": "player",
                  "value": ctype, "options": [{"value": t, "label": l} for t, l in opts]}]
    return {"running": run, "note": note, "players": _PLAYERS, "player": player,
            "selectors": selectors,
            "groups": [{"title": f"Buttons ({plabel})", "binds": binds}]}


@method("ryujinx.input_set", slow=True)
def _input_set(params):
    player = _player_param(params)
    key = params.get("id", "")
    jc = _BUTTON_MAP.get(key)
    if jc is None:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Ryujinx button")
    if params.get("kind", "btn") != "btn":
        raise RpcError("EINVAL", "Ryujinx mapping supports buttons only")
    try:
        code = int(params["value"])
    except (KeyError, ValueError, TypeError):
        raise RpcError("EINVAL", "missing or invalid button code")
    token = ryujinx_button(code)
    if token is None:
        raise RpcError("EINVAL", "that input can't be mapped — press a face, "
                                 "shoulder, trigger, Minus or Plus button")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Ryujinx first — it rewrites its config on exit")
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    p1 = _player1(data)
    if p1 is None:
        raise RpcError("EINVAL", "no controller configured in Ryujinx yet")
    entry = _find(data, player)
    if entry is None:
        # Create the player to hold a button layout (cloned from Player 1). Its
        # device id is left UNBOUND — the launch wrapper assigns the real pad; the
        # button maps are what we're editing here.
        entry = copy.deepcopy(p1)
        entry["player_index"] = player
        entry["id"] = _UNBOUND_ID
        data.setdefault("input_config", []).append(entry)
    if jc not in entry:
        raise RpcError("EINVAL", f"{_plabel(player)} has no {jc} bindings to remap")
    entry[jc][key] = token
    ryujinx_json.write(data)
    return {"id": key, "value": token,
            "message": f"{key.replace('button_', '').upper()} → {token}"}


@method("ryujinx.selector_set", slow=True)
def _selector_set(params):
    key = params.get("key")
    if key != "controller_type":
        raise RpcError("EINVAL", f"unknown selector {key!r}")
    player = _player_param(params)
    value = params.get("value", "")
    if value not in _CTYPE_IDS:
        raise RpcError("EINVAL", f"unknown controller type {value!r}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Ryujinx first — it rewrites its config on exit")
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    p1 = _player1(data)
    if p1 is None:
        raise RpcError("EINVAL", "no controller configured in Ryujinx yet")
    entry = _find(data, player)
    if entry is None:                # create the slot (cloned from Player 1, unbound device)
        entry = copy.deepcopy(p1)
        entry["player_index"] = player
        entry["id"] = _UNBOUND_ID
        data.setdefault("input_config", []).append(entry)
    entry["controller_type"] = value
    ryujinx_json.write(data)
    label = next((l for t, l in _CTYPES if t == value), value)
    return {"key": key, "value": value, "message": f"{_plabel(player)} → {label}"}
