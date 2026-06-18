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
from .input_translate import (axis_invert, axis_token_rank, canonical_is_trigger,
                              eden_hat_button_index, parse_axis_token, sdl_button_index,
                              sdl_index_label)
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
# D-pad directions — captured as a hat (kind="hat"); Eden stores them as
# player_N_button_d* with a button:N index (see input_translate caveat).
_DPAD = [
    ("button_dup", "D-pad Up"), ("button_ddown", "D-pad Down"),
    ("button_dleft", "D-pad Left"), ("button_dright", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
# Analog sticks — captured per-axis (kind="axis"); Eden stores axis_x/axis_y = the
# RAW SDL joystick axis index in the player_N_lstick/rstick line, with invert_x/y
# (+/-) and offset_* (calibration, PRESERVED). Directed-push labels (mirror xemu).
_STICKS = [
    ("lstick_x", "L-stick X — push right"), ("lstick_y", "L-stick Y — push down"),
    ("rstick_x", "R-stick X — push right"), ("rstick_y", "R-stick Y — push down"),
]
_STICK_KEYS = {k for k, _ in _STICKS}

_BTN_RE = re.compile(r"button:(\d+)")
_GUID_RE = re.compile(r"guid:([0-9A-Fa-f]+)")


def _value(text: str, key: str, player: str) -> str:
    return cfgutil.ini_read(text, _SECTION, f"{player}_{key}") or ""


def _configured_pad(text: str, player: str) -> str:
    """Friendly name of the controller this player's bindings point at (from the
    `guid:` token in any button line), or '' if none/unknown."""
    from ..mad_config import pad_name, vidpid_from_sdl_guid
    for key, _ in _BUTTONS:
        m = _GUID_RE.search(_value(text, key, player))
        if m:
            return pad_name(vidpid_from_sdl_guid(m.group(1)))
    return ""


def _shown(text: str, key: str, player: str) -> str:
    m = _BTN_RE.search(_value(text, key, player))
    return sdl_index_label(int(m.group(1))) if m else "—"


def _shown_stick(text: str, key: str, player: str) -> str:
    """Stored raw axis index for a stick-axis row ('lstick_x' → axis_x:N in the
    player_N_lstick line)."""
    stick, axis = key.rsplit("_", 1)
    m = re.search(rf"axis_{axis}:(\d+)", _value(text, stick, player))
    return f"axis {m.group(1)}" if m else "—"


def _set_stick(player: str, key: str, value: str):
    """Remap one stick axis: rewrite ONLY axis_<dir> + invert_<dir> in the
    player_N_lstick/rstick line, preserving offset_* (calibration) + the rest."""
    parsed = parse_axis_token(value)
    rank = axis_token_rank(value)
    if parsed is None or rank is None or canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "push the stick the way the row says")
    sign, canonical = parsed
    inv = "-" if axis_invert(sign, canonical) else "+"   # Eden: '+' = normal, '-' = inverted
    stick, axis = key.rsplit("_", 1)                      # 'lstick'/'rstick', 'x'/'y'
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Eden first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    cur = _value(text, stick, player)
    if f"axis_{axis}:" not in cur:
        raise RpcError("EINVAL", f"{_plabel(player)} has no stick configured — set its "
                                 "pad on the Controllers page first")
    new_val = re.sub(rf"axis_{axis}:\d+", f"axis_{axis}:{rank}", cur, count=1)
    new_val = re.sub(rf"invert_{axis}:[+-]", f"invert_{axis}:{inv}", new_val, count=1)
    new = cfgutil.ini_replace(text, _SECTION, f"{player}_{stick}", new_val)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{player}_{stick}' line in [{_SECTION}]")
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": f"axis {rank}", "message": f"{key} → physical axis {rank}"}


@method("eden.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Eden config not found at {_FILE} — launch a game once")
    player = _player(params)
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    run = proc_guard.emulator_running(_PROC)
    plabel = _plabel(player)

    def row(key, label, kind, capturable):
        value = _shown_stick(text, key, player) if kind == "axis" else _shown(text, key, player)
        return {"id": key, "label": label, "kind": kind, "value": value,
                "capturable": capturable and not run}

    groups = [
        {"title": f"Buttons ({plabel})", "binds": [row(k, l, "btn", True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
    ]
    cname = _configured_pad(text, player)
    note = ("Close Eden first — it rewrites its config on exit." if run else
            f"Remaps {plabel}'s configured controller (set its pad on the "
            "Controllers page first).")
    if cname:
        note = f"Controller: {cname}.  " + note
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
    kind = params.get("kind", "btn")
    if key in _STICK_KEYS and kind == "axis":
        return _set_stick(player, key, str(params.get("value", "")))
    if key in _DPAD_KEYS and kind == "hat":
        idx = eden_hat_button_index(str(params.get("value", "")))
        if idx is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        idx = sdl_button_index(code)
        if idx is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, "
                                     "shoulder, trigger, stick-click, Minus or Plus button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Eden input")
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
