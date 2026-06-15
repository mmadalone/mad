"""eden.input_* — per-button input mapping for Eden (Switch).

Edits the Player 1 bindings in `[Controls]` of ~/.config/eden/qt-config.ini. Each
is `player_0_button_<x>="engine:sdl,port:N,guid:G,button:M"` — KEY = Switch
button, `button:M` = the SDL joystick button index of the physical button. A
per-button remap changes ONLY the `button:M` token (the device guid/port stay),
so it re-points which physical button drives that Switch action on Player 1's
configured controller. M = input_translate.sdl_button_index(captured code) — the
standard modern-pad rank, identical across standard pads, so it's correct even
when Player 1's bound controller isn't the one being pressed.

Switch is `router_skip = true`, so the controller-router never rewrites this at
launch → no clobber (eden_cfg.assign is never run for Switch). Eden DOES rewrite
qt-config.ini on exit, so we refuse while it's running.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .input_translate import sdl_button_index, sdl_index_label
from .rpc import RpcError, method

_FILE = Path.home() / ".config/eden/qt-config.ini"
_SECTION = "Controls"
_PROC = "eden"
_PLAYER = "player_0"   # Player 1 (v1 remaps Player 1 only)

# (Switch-button key suffix, label) — the remappable digital buttons.
_BUTTONS = [
    ("button_a", "A"), ("button_b", "B"), ("button_x", "X"), ("button_y", "Y"),
    ("button_l", "L"), ("button_r", "R"), ("button_zl", "ZL"), ("button_zr", "ZR"),
    ("button_minus", "Minus −"), ("button_plus", "Plus +"),
    ("button_lstick", "L-stick click"), ("button_rstick", "R-stick click"),
    ("button_home", "Home"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# Shown read-only for now (d-pad = hat; capture skips hats).
_READONLY = [
    ("button_dup", "D-pad Up"), ("button_ddown", "D-pad Down"),
    ("button_dleft", "D-pad Left"), ("button_dright", "D-pad Right"),
]

_BTN_RE = re.compile(r"button:(\d+)")


def _value(text: str, key: str) -> str:
    return cfgutil.ini_read(text, _SECTION, f"{_PLAYER}_{key}") or ""


def _shown(text: str, key: str) -> str:
    m = _BTN_RE.search(_value(text, key))
    return sdl_index_label(int(m.group(1))) if m else "—"


@method("eden.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE} — launch a game once")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    run = proc_guard.emulator_running(_PROC)

    def row(key, label, capturable):
        return {"id": key, "label": label, "kind": "btn",
                "value": _shown(text, key), "capturable": capturable and not run}

    groups = [
        {"title": "Buttons (Player 1)", "binds": [row(k, l, True) for k, l in _BUTTONS]},
        {"title": "D-pad (remap in Eden itself for now)",
         "binds": [row(k, l, False) for k, l in _READONLY]},
    ]
    note = ("Close Eden first — it rewrites its config on exit." if run else
            "Remaps Player 1's configured controller (button layout is shared "
            "across standard pads).")
    return {"running": run, "note": note, "groups": groups}


@method("eden.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    if key not in _BUTTON_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Eden button")
    if params.get("kind", "btn") != "btn":
        raise RpcError("EINVAL", "Eden mapping supports buttons only")
    try:
        code = int(params["value"])
    except (KeyError, ValueError, TypeError):
        raise RpcError("EINVAL", "missing or invalid button code")
    idx = sdl_button_index(code)
    if idx is None:
        raise RpcError("EINVAL", "that input can't be mapped — press a face, "
                                 "shoulder, trigger, stick-click, Minus or Plus button")
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Eden first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    cur = _value(text, key)
    if "button:" not in cur:
        raise RpcError("EINVAL", f"{key} isn't a simple button binding")
    new_val = _BTN_RE.sub(f"button:{idx}", cur, count=1)
    new = cfgutil.ini_replace(text, _SECTION, f"{_PLAYER}_{key}", new_val)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{_PLAYER}_{key}' line in [{_SECTION}]")
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": sdl_index_label(idx),
            "message": f"{key.replace('button_', '').upper()} → {sdl_index_label(idx)}"}
