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
_SYSTEM_SECTION = "System"   # use_docked_mode lives here, not in [Controls]
_PROC = "eden"
# Eden/yuzu Settings::ControllerType — player_N_type is the enum's integer index
# (verified against eden settings_input.h). Exposed as the page's "Type" selector.
_CTYPES = [("0", "Pro Controller"), ("1", "Dual Joycons"), ("2", "Left Joycon"),
           ("3", "Right Joycon"), ("4", "Handheld"), ("5", "GameCube")]
_CTYPE_VALUES = {v for v, _ in _CTYPES}
# Console docked mode (global): use_docked_mode 1=docked, 0=handheld.
_CONSOLE = [("1", "Docked"), ("0", "Handheld")]
_CONSOLE_VALUES = {v for v, _ in _CONSOLE}
# Eden's input config supports player_0..player_7. The page exposes them via a
# player selector; remap targets `player_{n}_button_*`. A player must already have
# a controller (its pad set on the Controllers page) for its button line to exist.
_PLAYERS = [{"id": f"player_{n}", "label": f"Player {n + 1}"} for n in range(8)]
_PLAYER_IDS = {p["id"] for p in _PLAYERS}
_DEFAULT_PLAYER = "player_0"


def _player(params) -> str:
    p = params.get("player") or _DEFAULT_PLAYER
    if p not in _PLAYER_IDS:
        raise RpcError("EINVAL", f"unknown player {p!r}")
    return p


def _plabel(player: str) -> str:
    return next((p["label"] for p in _PLAYERS if p["id"] == player), player)

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


def _value(text: str, key: str, player: str) -> str:
    return cfgutil.ini_read(text, _SECTION, f"{player}_{key}") or ""


def _shown(text: str, key: str, player: str) -> str:
    m = _BTN_RE.search(_value(text, key, player))
    return sdl_index_label(int(m.group(1))) if m else "—"


@method("eden.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE} — launch a game once")
    player = _player(params)
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    run = proc_guard.emulator_running(_PROC)
    plabel = _plabel(player)

    def row(key, label, capturable):
        return {"id": key, "label": label, "kind": "btn",
                "value": _shown(text, key, player), "capturable": capturable and not run}

    groups = [
        {"title": f"Buttons ({plabel})", "binds": [row(k, l, True) for k, l in _BUTTONS]},
        {"title": "D-pad (remap in Eden itself for now)",
         "binds": [row(k, l, False) for k, l in _READONLY]},
    ]
    note = ("Close Eden first — it rewrites its config on exit." if run else
            f"Remaps {plabel}'s configured controller (set its pad on the "
            "Controllers page first).")
    ptype = (cfgutil.ini_read(text, _SECTION, f"{player}_type") or "0").strip()
    raw_docked = (cfgutil.ini_read(text, _SYSTEM_SECTION, "use_docked_mode") or "1").strip()
    docked = "1" if raw_docked.lower() in ("1", "true", "yes", "on") else "0"
    type_opts = list(_CTYPES)
    if ptype not in _CTYPE_VALUES:                 # surface an unlisted on-disk value
        type_opts = [(ptype, ptype)] + type_opts
    selectors = [
        {"key": "controller_type", "label": "Type", "scope": "player", "value": ptype,
         "options": [{"value": v, "label": l} for v, l in type_opts]},
        {"key": "console_mode", "label": "Console", "scope": "global", "value": docked,
         "options": [{"value": v, "label": l} for v, l in _CONSOLE]},
    ]
    return {"running": run, "note": note, "groups": groups, "selectors": selectors,
            "players": _PLAYERS, "player": player}


@method("eden.input_set", slow=True)
def _input_set(params):
    player = _player(params)
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
    cur = _value(text, key, player)
    if "button:" not in cur:
        raise RpcError("EINVAL", f"{_plabel(player)} has no controller configured "
                                 "for that button — set its pad on the Controllers "
                                 "page first")
    new_val = _BTN_RE.sub(f"button:{idx}", cur, count=1)
    new = cfgutil.ini_replace(text, _SECTION, f"{player}_{key}", new_val)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{player}_{key}' line in [{_SECTION}]")
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": sdl_index_label(idx),
            "message": f"{key.replace('button_', '').upper()} → {sdl_index_label(idx)}"}


@method("eden.selector_set", slow=True)
def _selector_set(params):
    key = params.get("key")
    value = str(params.get("value", "")).strip()
    if key == "controller_type":
        player = _player(params)
        if value not in _CTYPE_VALUES:
            raise RpcError("EINVAL", f"unknown controller type {value!r}")
        section, name, label = _SECTION, f"{player}_type", _plabel(player)
    elif key == "console_mode":
        if value not in _CONSOLE_VALUES:
            raise RpcError("EINVAL", "console mode must be Docked or Handheld")
        section, name, label = _SYSTEM_SECTION, "use_docked_mode", "Console mode"
    else:
        raise RpcError("EINVAL", f"unknown selector {key!r}")
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Eden first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    new = cfgutil.ini_replace(text, section, name, value)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{name}' line in [{section}]")
    # Eden ignores a stored value while its `<key>\default` is true — flip it so
    # our choice is honored (the line exists in the live config).
    flipped = cfgutil.ini_replace(new, section, name + "\\default", "false")
    if flipped is not None:
        new = flipped
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    disp = next((l for v, l in (_CTYPES + _CONSOLE) if v == value), value)
    return {"key": key, "value": value, "message": f"{label} → {disp}"}
