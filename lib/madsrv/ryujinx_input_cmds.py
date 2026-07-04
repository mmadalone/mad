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
import json

from .. import proc_guard
from . import ryujinx_json
from .input_translate import ryujinx_button, ryujinx_hat_dpad
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
# D-pad directions — captured as a hat (kind="hat"); stored in left_joycon as the
# GamepadButtonInputId enum ("DpadUp"/"DpadDown"/"DpadLeft"/"DpadRight").
_DPAD = [
    ("dpad_up", "D-pad Up", "left_joycon"), ("dpad_down", "D-pad Down", "left_joycon"),
    ("dpad_left", "D-pad Left", "left_joycon"), ("dpad_right", "D-pad Right", "left_joycon"),
]
_DPAD_MAP = {k: jc for k, _, jc in _DPAD}

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
# Stick SELECTORS — Ryujinx sticks are a physical-stick CHOICE + invert flags, not a
# captured axis. key -> (joycon object, JSON field, kind 'source'|'invert').
_STICK_SELECTORS = {
    "left_stick_source":  ("left_joycon_stick", "joystick", "source"),
    "right_stick_source": ("right_joycon_stick", "joystick", "source"),
    "left_invert_x":      ("left_joycon_stick", "invert_stick_x", "invert"),
    "left_invert_y":      ("left_joycon_stick", "invert_stick_y", "invert"),
    "right_invert_x":     ("right_joycon_stick", "invert_stick_x", "invert"),
    "right_invert_y":     ("right_joycon_stick", "invert_stick_y", "invert"),
}
_STICK_SOURCE_OPTS = [("Left", "Left stick"), ("Right", "Right stick")]
_STICK_SOURCE_IDS = {s for s, _ in _STICK_SOURCE_OPTS}
_INVERT_OPTS = [("false", "Off"), ("true", "On")]
_STICK_LABELS = {
    "left_stick_source": "L-stick source", "right_stick_source": "R-stick source",
    "left_invert_x": "Invert L-stick X", "left_invert_y": "Invert L-stick Y",
    "right_invert_x": "Invert R-stick X", "right_invert_y": "Invert R-stick Y",
}
# An id that matches no live joystick → the slot is "unbound" until the launch
# wrapper assigns it a real device. Used when a new player is created here just to
# hold a button layout (the button maps are the point; the device is wrapper-managed).
_UNBOUND_ID = "0-00000000-0000-0000-0000-000000000000"

# ── named input profiles (Ryujinx's own profiles/controller/*.json = full InputConfig objects) ──
# The picker BAKES a profile's MAPPING subtree into the player's input_config entry, preserving the
# slot's own id/backend/player_index/controller_type (the profile's device IDENTITY is not copied).
# input_config is the runtime-authoritative layer, so baking inline is correct whether or not Ryujinx
# resolves a profile_name at boot (see deck-docs/ryubing-config.md). "Default" is a passive label
# (the picker does not track which profile was baked); picking a named profile loads its mapping.
_PROFILE_MAP_KEYS = ("left_joycon_stick", "right_joycon_stick", "deadzone_left", "deadzone_right",
                     "range_left", "range_right", "trigger_threshold", "motion", "rumble", "led",
                     "left_joycon", "right_joycon", "version")


def _profile_dir():
    return ryujinx_json.CONFIG.parent / "profiles" / "controller"


def _profiles() -> list:
    try:
        return sorted(p.stem for p in _profile_dir().glob("*.json"))
    except OSError:
        return []


def _bake_profile(entry: dict, name: str) -> None:
    """Load profiles/controller/<name>.json and copy ONLY its mapping subtree into `entry`,
    preserving the slot's device identity (id/backend/player_index/controller_type)."""
    try:
        prof = json.loads((_profile_dir() / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RpcError("ENOENT", f"input profile {name!r} not found or unreadable")
    for k in _PROFILE_MAP_KEYS:
        if k in prof:
            entry[k] = prof[k]


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


def _configured_pad(entry: dict | None) -> str:
    """Friendly name of the pad bound to this player, from its Ryujinx `id`
    ('<idx>-<8>-<vid>-0000-<pid swapped>-<12>'); '' if unbound/unknown. Often '' for
    the migrated standalones — the launch wrapper assigns the real pad per launch."""
    rid = (entry or {}).get("id") or ""
    parts = rid.split("-")
    if len(parts) >= 5 and len(parts[2]) == 4 and len(parts[4]) == 4:
        from ..mad_config import pad_name
        return pad_name(f"{parts[2]}:{parts[4][2:4]}{parts[4][0:2]}")
    return ""


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

    def row(key, label, jc, kind):
        return {"id": key, "label": label, "kind": kind,
                "value": (entry.get(jc, {}).get(key) if entry else None) or "—",
                "capturable": run is False}

    binds = [row(k, l, jc, "btn") for k, l, jc in _BUTTONS]
    dpad = [row(k, l, jc, "hat") for k, l, jc in _DPAD]
    cname = _configured_pad(entry)
    if run:
        note = "Close Ryujinx first — it rewrites its config on exit."
    elif entry is None:
        note = f"{plabel} not configured yet — remap a button to create it (layout cloned from Player 1)."
    else:
        note = f"Remaps {plabel} ({entry.get('controller_type', 'controller')})."
    if cname:
        note = f"Controller: {cname}.  " + note
    ctype = (entry.get("controller_type") if entry else None) or "ProController"
    opts = list(_CTYPES)
    if ctype not in _CTYPE_IDS:                    # surface an unlisted on-disk type
        opts = [(ctype, ctype)] + opts
    prof_opts = [{"value": "Default", "label": "Default"}] + \
                [{"value": p, "label": p} for p in _profiles()]
    selectors = [{"key": "profile", "label": "Profile", "scope": "player",
                  "value": "Default", "options": prof_opts},
                 {"key": "controller_type", "label": "Type", "scope": "player",
                  "value": ctype, "options": [{"value": t, "label": l} for t, l in opts]}]
    for skey, (obj, field, skind) in _STICK_SELECTORS.items():
        cur = entry.get(obj, {}).get(field) if entry else None
        if skind == "source":
            val = cur or ("Left" if obj.startswith("left") else "Right")
            sopts = _STICK_SOURCE_OPTS
        else:
            val = "true" if cur else "false"
            sopts = _INVERT_OPTS
        selectors.append({"key": skey, "label": _STICK_LABELS[skey], "scope": "player",
                          "value": val, "options": [{"value": v, "label": l} for v, l in sopts]})
    return {"running": run, "note": note, "players": _PLAYERS, "player": player,
            "selectors": selectors,
            "groups": [{"title": f"Buttons ({plabel})", "binds": binds},
                       {"title": "D-pad", "binds": dpad}]}


@method("ryujinx.input_set", slow=True)
def _input_set(params):
    player = _player_param(params)
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    jc = _BUTTON_MAP.get(key) or _DPAD_MAP.get(key)
    if jc is None:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Ryujinx input")
    if key in _DPAD_MAP and kind == "hat":
        token = ryujinx_hat_dpad(str(params.get("value", "")))
        if token is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _BUTTON_MAP and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        token = ryujinx_button(code)
        if token is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, "
                                     "shoulder, trigger, Minus or Plus button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Ryujinx input")
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
    player = _player_param(params)
    value = str(params.get("value", ""))
    if key == "controller_type":
        if value not in _CTYPE_IDS:
            raise RpcError("EINVAL", f"unknown controller type {value!r}")
    elif key == "profile":
        if value != "Default" and value not in _profiles():
            raise RpcError("EINVAL", f"unknown input profile {value!r}")
    elif key in _STICK_SELECTORS:
        _obj, _field, skind = _STICK_SELECTORS[key]
        if skind == "source" and value not in _STICK_SOURCE_IDS:
            raise RpcError("EINVAL", f"stick source must be Left or Right, got {value!r}")
        if skind == "invert" and value not in ("true", "false"):
            raise RpcError("EINVAL", "invert must be on or off")
    else:
        raise RpcError("EINVAL", f"unknown selector {key!r}")
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
    if key == "controller_type":
        entry["controller_type"] = value
        disp, label = next((l for t, l in _CTYPES if t == value), value), "Type"
    elif key == "profile":
        if value == "Default":
            disp, label = "pick a named profile to load its mapping", "Profile"
        else:
            _bake_profile(entry, value)          # copy the mapping subtree; slot identity preserved
            disp, label = f"loaded '{value}'", "Profile"
        value = "Default"                        # the picker does not track which profile is baked
    else:
        obj, field, skind = _STICK_SELECTORS[key]
        entry.setdefault(obj, {})[field] = (value == "true") if skind == "invert" else value
        disp = value if skind == "source" else ("On" if value == "true" else "Off")
        label = _STICK_LABELS[key]
    ryujinx_json.write(data)
    return {"key": key, "value": value, "message": f"{label} → {disp}"}
