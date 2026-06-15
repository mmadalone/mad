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


def _player1(data: dict) -> dict | None:
    """The first 'Player1' input_config entry (fallback: the first entry)."""
    ics = data.get("input_config") or []
    for ic in ics:
        if ic.get("player_index") == "Player1":
            return ic
    return ics[0] if ics else None


@method("ryujinx.input_get", slow=True, cache=("config",))
def _input_get(params):
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable — launch a game once")
    p1 = _player1(data)
    if p1 is None:
        raise RpcError("EINVAL", "no controller configured in Ryujinx yet")
    run = proc_guard.emulator_running(_PROC)
    ctype = p1.get("controller_type", "controller")

    def row(key, label, jc):
        return {"id": key, "label": label, "kind": "btn",
                "value": p1.get(jc, {}).get(key, "—") or "—",
                "capturable": run is False}

    binds = [row(k, l, jc) for k, l, jc in _BUTTONS]
    note = ("Close Ryujinx first — it rewrites its config on exit." if run else
            f"Remaps Player 1 ({ctype}).")
    return {"running": run, "note": note,
            "groups": [{"title": "Buttons (Player 1)", "binds": binds}]}


@method("ryujinx.input_set", slow=True)
def _input_set(params):
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
    if p1 is None or jc not in p1:
        raise RpcError("EINVAL", "Player 1 controller not configured for that button")
    p1[jc][key] = token
    ryujinx_json.write(data)
    return {"id": key, "value": token,
            "message": f"{key.replace('button_', '').upper()} → {token}"}
