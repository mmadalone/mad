r"""citron.input_* — per-button input mapping for Citron (Switch, a Yuzu fork).

Edits the per-player bindings in [Controls] of ~/.config/citron/qt-config.ini. Each is
`player_N_button_<x>="engine:sdl,port:N,guid:G,button:M"` — a per-button remap changes ONLY
the `button:M` token (device guid/port preserved), re-pointing which physical button drives
that Switch action. Identical binding format to Eden (same Yuzu lineage), so this is a clone
of eden_input_cmds re-pointed at Citron + the two behaviours Eden's page lacks:
  • citron.input_clear — the "focus a row, press Start" clear (Eden never registered it, so
    its clear button errors; Citron unbinds the row to Yuzu's `[empty]` token).
  • every write flips the `<key>\default=false` twin — Citron discards a stored value whose
    `\default` is true/absent (frontend_common/config.cpp ReadSettingGeneric). Configured
    binding lines are already `\default=false`, so this is defensive, but Citron is strict.

Switch is `router_skip = true`, so the controller-router never rewrites this at launch. Citron
rewrites qt-config.ini on exit, so we refuse while it's running. The named-profile PICKER is a
PER-GAME feature (a global profile is reset by the launch router each launch); this global page
is the per-button map only.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .input_translate import (axis_invert, axis_token_rank, canonical_is_trigger,
                              eden_hat_button_index, hat_token_parts, parse_axis_token,
                              sdl_button_index, sdl_index_label)
from .rpc import RpcError, method

_FILE = Path.home() / ".config/citron/qt-config.ini"
_SECTION = "Controls"
_SYSTEM_SECTION = "System"   # use_docked_mode lives here, not in [Controls]
_PROC = "citron"
# Yuzu Settings::ControllerType — player_N_type is the enum's integer index (shared Switch
# hardware model; identical to Eden). Exposed as the page's "Type" selector.
_CTYPES = [("0", "Pro Controller"), ("1", "Dual Joycons"), ("2", "Left Joycon"),
           ("3", "Right Joycon"), ("4", "Handheld"), ("5", "GameCube")]
_CTYPE_VALUES = {v for v, _ in _CTYPES}
# Console docked mode (global): use_docked_mode 1=docked, 0=handheld.
_CONSOLE = [("1", "Docked"), ("0", "Handheld")]
_CONSOLE_VALUES = {v for v, _ in _CONSOLE}
# player_0..player_7. A player must already have a controller (its pad set on the Controllers
# page) for its button line to exist.
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


_BUTTONS = [
    ("button_a", "A"), ("button_b", "B"), ("button_x", "X"), ("button_y", "Y"),
    ("button_l", "L"), ("button_r", "R"), ("button_zl", "ZL"), ("button_zr", "ZR"),
    ("button_minus", "Minus −"), ("button_plus", "Plus +"),
    ("button_lstick", "L-stick click"), ("button_rstick", "R-stick click"),
    ("button_home", "Home"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# ZL/ZR are analog triggers on most pads (DS/DS4/Deck report axis:4/5); on the Wii U Pro
# adapter they are plain buttons. The row's capture KIND is chosen per device from the
# on-disk binding (see _btn_kind), so each pad captures the right thing.
_TRIGGER_KEYS = {"button_zl", "button_zr"}
_DPAD = [
    ("button_dup", "D-pad Up"), ("button_ddown", "D-pad Down"),
    ("button_dleft", "D-pad Left"), ("button_dright", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICKS = [
    ("lstick_x", "L-stick X — push right"), ("lstick_y", "L-stick Y — push down"),
    ("rstick_x", "R-stick X — push right"), ("rstick_y", "R-stick Y — push down"),
]
_STICK_KEYS = {k for k, _ in _STICKS}

_BTN_RE = re.compile(r"button:(\d+)")
_GUID_RE = re.compile(r"guid:([0-9A-Fa-f]+)")


def _value(text: str, key: str, player: str) -> str:
    return cfgutil.ini_read(text, _SECTION, f"{player}_{key}") or ""


def _flip_default(text: str, name: str) -> str:
    """Flip `<name>\\default=false` (Citron discards a value whose \\default is true). No-op
    when the twin line is absent."""
    r = cfgutil.ini_replace(text, _SECTION, name + "\\default", "false")
    return r if r is not None else text


def _configured_pad(text: str, player: str) -> str:
    from ..mad_config import pad_name, vidpid_from_sdl_guid
    for key, _ in _BUTTONS:
        m = _GUID_RE.search(_value(text, key, player))
        if m:
            return pad_name(vidpid_from_sdl_guid(m.group(1)))
    return ""


def _shown(text: str, key: str, player: str) -> str:
    """The current binding rendered for display, for any token structure: a plain button
    (`button:N`), an analog trigger axis (`axis:N`), or a d-pad hat (`direction:D`)."""
    v = _value(text, key, player)
    m = _BTN_RE.search(v)
    if m:
        return sdl_index_label(int(m.group(1)))
    ma = re.search(r"axis:(\d+)", v)
    if ma:
        return f"axis {ma.group(1)}"
    md = re.search(r"direction:(\w+)", v)
    if md:
        return f"D-pad {md.group(1)}"
    return "—"


def _btn_kind(text: str, key: str, player: str) -> str:
    """Capture KIND for a Button-group row: ZL/ZR bound as an analog axis (DS/DS4/Deck)
    capture as a "trigger" (axisname mode); everything else as a plain "btn"."""
    if key in _TRIGGER_KEYS and "axis:" in _value(text, key, player):
        return "trigger"
    return "btn"


def _shown_stick(text: str, key: str, player: str) -> str:
    stick, axis = key.rsplit("_", 1)
    m = re.search(rf"axis_{axis}:(\d+)", _value(text, stick, player))
    return f"axis {m.group(1)}" if m else "—"


def _set_stick(player: str, key: str, value: str):
    parsed = parse_axis_token(value)
    rank = axis_token_rank(value)
    if parsed is None or rank is None or canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "push the stick the way the row says")
    sign, canonical = parsed
    inv = "-" if axis_invert(sign, canonical) else "+"   # '+' = normal, '-' = inverted
    stick, axis = key.rsplit("_", 1)
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit")
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
    new = _flip_default(new, f"{player}_{stick}")
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": f"axis {rank}", "message": f"{key} → physical axis {rank}"}


@method("citron.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE} — launch a game once")
    player = _player(params)
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    run = proc_guard.emulator_running(_PROC)
    plabel = _plabel(player)

    def row(key, label, kind, capturable):
        value = _shown_stick(text, key, player) if kind == "axis" else _shown(text, key, player)
        return {"id": key, "label": label, "kind": kind, "value": value,
                "capturable": capturable and not run}

    groups = [
        {"title": f"Buttons ({plabel})",
         "binds": [row(k, l, _btn_kind(text, k, player), True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
    ]
    cname = _configured_pad(text, player)
    note = ("Close Citron first — it rewrites its config on exit." if run else
            f"Remaps {plabel}'s configured controller (set its pad on the "
            "Controllers page first). Per-game named profiles live under Per-game settings.")
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


def _remap_dpad(cur: str, token: str, key: str, input_dir):
    """New d-pad value from a captured hat token, preserving the device (guid/port/engine).
    Hat-style pad -> re-point `hat:N` + `direction:D`. Button-style pad -> the CORRECT per-device
    `button:idx`: from the pad's input template (matched by vid:pid via eden_cfg.dpad_index), else
    derived from the pad's own base, else the Wii U rank (today's last-resort). This is what stops a
    DualSense d-pad (base 11) from being stamped with the Wii U Pro's base 13. Returns (value, label)."""
    parts = hat_token_parts(token)
    if parts is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    n, d = parts
    if "hat:" in cur:
        v = re.sub(r"hat:\d+", f"hat:{n}", cur, count=1)
        v = re.sub(r"direction:\w+", f"direction:{d}", v, count=1)
        return v, f"D-pad {d}"
    if "button:" not in cur:      # neither hat nor button (e.g. an axis d-pad): nothing to re-point
        raise RpcError("EINVAL", "this pad's d-pad can't be remapped here")
    from .. import eden_cfg       # lazy (eden_cfg pulls in SDL); matches this module's lazy imports
    idx = eden_cfg.dpad_index(input_dir, cur, d, key)
    if idx is None:
        idx = eden_hat_button_index(token)   # last resort: the historical Wii U rank
    if idx is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    return _BTN_RE.sub(f"button:{idx}", cur, count=1), sdl_index_label(idx)


def _remap_button(cur: str, params) -> tuple:
    """New value for a plain digital button row (face/shoulder/thumb/Minus/Plus, and ZL/ZR on
    a button-trigger pad). Re-points `button:idx`, preserving the device."""
    try:
        code = int(params["value"])
    except (KeyError, ValueError, TypeError):
        raise RpcError("EINVAL", "missing or invalid button code")
    idx = sdl_button_index(code)
    if idx is None:
        raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                 "trigger, stick-click, Minus or Plus button")
    if "button:" not in cur:
        raise RpcError("EINVAL", "this control is an axis on this pad — use the trigger row")
    return _BTN_RE.sub(f"button:{idx}", cur, count=1), sdl_index_label(idx)


def _remap_trigger(cur: str, token: str):
    """New value for an analog-trigger row (ZL/ZR on a DS/DS4/Deck) from an axisname trigger
    token. Re-points `axis:N` (keeping the existing threshold/invert), preserving the device;
    synthesises a fresh axis binding if the slot somehow was not an axis."""
    parsed = parse_axis_token(token)
    rank = axis_token_rank(token)
    if parsed is None or rank is None or not canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "pull the trigger")
    if "axis:" in cur:
        return re.sub(r"axis:\d+", f"axis:{rank}", cur, count=1), f"trigger axis {rank}"
    m = _GUID_RE.search(cur)
    pm = re.search(r"port:(\d+)", cur)
    guid = m.group(1) if m else ""
    port = pm.group(1) if pm else "0"
    return (f"engine:sdl,invert:+,port:{port},guid:{guid},axis:{rank},threshold:0.500000",
            f"trigger axis {rank}")


@method("citron.input_set", slow=True)
def _input_set(params):
    player = _player(params)
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key in _STICK_KEYS and kind == "axis":
        return _set_stick(player, key, str(params.get("value", "")))
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    cur = _value(text, key, player)
    if "guid:" not in cur:                       # a controller must be configured (any structure)
        raise RpcError("EINVAL", f"{_plabel(player)} has no pad here — set it on the "
                                 "Controllers page.")
    if key in _DPAD_KEYS and kind == "hat":
        new_val, label = _remap_dpad(cur, str(params.get("value", "")), key, _FILE.parent / "input")
    elif key in _TRIGGER_KEYS and kind == "trigger":
        new_val, label = _remap_trigger(cur, str(params.get("value", "")))
    elif key in _BUTTON_KEYS and kind == "btn":
        new_val, label = _remap_button(cur, params)
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Citron input")
    new = cfgutil.ini_replace(text, _SECTION, f"{player}_{key}", new_val)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{player}_{key}' line in [{_SECTION}]")
    new = _flip_default(new, f"{player}_{key}")
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": label,
            "message": f"{key.replace('button_', '').upper()} → {label}"}


@method("citron.input_clear", slow=True)
def _input_clear(params):
    """Unbind one row — the page's "focus a row, press Start" clear. Sets the binding to Yuzu's
    `[empty]` token (unbound) and flips its \\default twin so Citron honours it."""
    player = _player(params)
    key = params.get("id") or params.get("key") or ""
    if key not in _BUTTON_KEYS and key not in _DPAD_KEYS and key not in _STICK_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable Citron input")
    # sticks live in the player_N_lstick/rstick line; buttons/dpad are player_N_<key>.
    name = f"{player}_{key.rsplit('_', 1)[0]}" if key in _STICK_KEYS else f"{player}_{key}"
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    if cfgutil.ini_read(text, _SECTION, name) is None:
        raise RpcError("EINVAL", f"{_plabel(player)} has no '{key}' binding to clear")
    new = cfgutil.ini_replace(text, _SECTION, name, "[empty]")
    if new is None:
        raise RpcError("EINTERNAL", f"no '{name}' line in [{_SECTION}]")
    new = _flip_default(new, name)
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": "—", "message": f"{key} cleared"}


@method("citron.selector_set", slow=True)
def _selector_set(params):
    key = params.get("key")
    value = str(params.get("value", "")).strip()
    defer_ctype = False
    if key == "controller_type":
        player = _player(params)
        section, name, label = _SECTION, f"{player}_type", _plabel(player)
        defer_ctype = value not in _CTYPE_VALUES
    elif key == "console_mode":
        if value not in _CONSOLE_VALUES:
            raise RpcError("EINVAL", "console mode must be Docked or Handheld")
        section, name, label = _SYSTEM_SECTION, "use_docked_mode", "Console mode"
    else:
        raise RpcError("EINVAL", f"unknown selector {key!r}")
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE}")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    if defer_ctype and (cfgutil.ini_read(text, section, name) or "").strip() != value:
        raise RpcError("EINVAL", f"unknown controller type {value!r}")
    new = cfgutil.ini_replace(text, section, name, value)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{name}' line in [{section}]")
    # Citron ignores a stored value while its `<key>\default` is true — flip it so our choice
    # is honoured (the twin exists in the live config).
    flipped = cfgutil.ini_replace(new, section, name + "\\default", "false")
    if flipped is not None:
        new = flipped
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    disp = next((l for v, l in (_CTYPES + _CONSOLE) if v == value), value)
    return {"key": key, "value": value, "message": f"{label} → {disp}"}
