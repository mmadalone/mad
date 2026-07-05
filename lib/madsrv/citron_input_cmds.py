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
                              sdl_button_index, sdl_index_label,
                              xemu_axis_index, xemu_axis_label,
                              xemu_button_index, xemu_index_label)
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


def _scheme(guid: str) -> str:
    """'gc' (SDL GameController numbering: L3=button:7, ZL=axis:4, d-pad=button:11..14) or 'raw'
    (SDL joystick rank: L3=button:11, ZL=axis:2) for the device a binding points at. Keyed on the
    device's CLEAN input TEMPLATE (matched by the guid's vid:pid), NOT the live [Controls] block --
    a wrong-numbering remap can corrupt the live block, while the template is the emulator's own
    clean output. 'raw' when no template matches (the historical behaviour + non-GameController pads
    like the Wii U Pro adapter and the Steam Deck pad)."""
    if not guid:
        return "raw"
    from .. import eden_cfg   # lazy (pulls in SDL); matches this module's lazy-import style
    l3 = eden_cfg._match_template(_FILE.parent / "input", guid).get("button_lstick", "")
    return "gc" if "button:7" in l3 else "raw"


def _scheme_of(value: str) -> str:
    """The pad scheme for the device a binding string points at (via its `guid:`)."""
    m = _GUID_RE.search(value or "")
    return _scheme(m.group(1)) if m else "raw"


def _btn_label(idx: int, scheme: str) -> str:
    return xemu_index_label(idx) if scheme == "gc" else sdl_index_label(idx)


def _axis_label(idx: int, scheme: str) -> str:
    return xemu_axis_label(idx) if scheme == "gc" else f"axis {idx}"


def _append_token(cur: str, token: str) -> str:
    """Add `token` (e.g. 'button:9', 'hat:0,direction:up') to a binding value, INSIDE its closing
    quote when the value is quoted -- so a synthesised skeleton stays a VALID quoted binding
    `"engine:sdl,...,token"` and isn't written unquoted/malformed (Citron's values are quoted)."""
    if cur.endswith('"'):
        return f'{cur[:-1].rstrip(",")},{token}"'
    return f'{cur.rstrip(",")},{token}'


def _player_device(text: str, player: str) -> tuple[str, str]:
    """(guid, port) of the pad this player is configured for, taken from ANY of its bindings that
    still carry a `guid:` -- so a single missing/cleared key (e.g. after a Start-clear, or the old
    corruption) does NOT read as 'no pad'. ('', '') only when the player has no controller at all."""
    for key, _ in _BUTTONS + _DPAD:
        m = _GUID_RE.search(_value(text, key, player))
        if m:
            p = re.search(r"port:(\d+)", _value(text, key, player))
            return m.group(1), (p.group(1) if p else "0")
    for stick in ("lstick", "rstick"):
        v = _value(text, stick, player)
        m = _GUID_RE.search(v)
        if m:
            p = re.search(r"port:(\d+)", v)
            return m.group(1), (p.group(1) if p else "0")
    return "", ""


def _shown(text: str, key: str, player: str) -> str:
    """The current binding rendered for display, for any token structure: a plain button
    (`button:N`), an analog trigger axis (`axis:N`), or a d-pad hat (`direction:D`)."""
    v = _value(text, key, player)
    scheme = _scheme_of(v)
    m = _BTN_RE.search(v)
    if m:
        return _btn_label(int(m.group(1)), scheme)
    ma = re.search(r"axis:(\d+)", v)
    if ma:
        return _axis_label(int(ma.group(1)), scheme)
    md = re.search(r"direction:(\w+)", v)
    if md:
        return f"D-pad {md.group(1)}"
    return "—"


def _btn_kind(text: str, key: str, player: str) -> str:
    """Capture KIND for a Button-group row: ZL/ZR bound as an analog axis (DS/DS4/Deck) capture as a
    "trigger" (axisname mode); everything else "btn". If the ZL/ZR value was CLEARED (Start-clear left
    no axis:), consult the device template so an analog-trigger pad still captures as a trigger, not a
    button (else a cleared ZL could never be re-bound as an analog trigger)."""
    if key in _TRIGGER_KEYS:
        if "axis:" in _value(text, key, player):
            return "trigger"
        m = _GUID_RE.search(_value(text, "button_a", player)) or _GUID_RE.search(_value(text, "button_dup", player))
        if m:
            from .. import eden_cfg
            if "axis:" in eden_cfg._match_template(_FILE.parent / "input", m.group(1)).get(key, ""):
                return "trigger"
    return "btn"


def _shown_stick(text: str, key: str, player: str) -> str:
    stick, axis = key.rsplit("_", 1)
    cur = _value(text, stick, player)
    m = re.search(rf"axis_{axis}:(\d+)", cur)
    return _axis_label(int(m.group(1)), _scheme_of(cur)) if m else "—"


def _set_stick(player: str, key: str, value: str):
    parsed = parse_axis_token(value)
    if parsed is None or canonical_is_trigger(parsed[1]):
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
    if not _player_device(text, player)[0]:
        raise RpcError("EINVAL", f"{_plabel(player)} has no pad here. Configure it in Citron first.")
    if f"axis_{axis}:" not in cur:
        raise RpcError("EINVAL", f"{_plabel(player)}'s stick is unset. Re-set it in Citron.")
    # GameController pads store the SDL_GameControllerAxis index (L-stick X=0, R-stick X=2); raw
    # joystick pads store the ABS rank the capture measured. Same physical push, right per-pad index.
    scheme = _scheme_of(cur)
    rank = xemu_axis_index(canonical) if scheme == "gc" else axis_token_rank(value)
    if rank is None:
        raise RpcError("EINVAL", "push the stick the way the row says")
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
    label = _axis_label(rank, scheme)
    return {"id": key, "value": label, "message": f"{key} → {label}"}


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
            "players": _PLAYERS, "player": player, "clearable": True}


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
    from .. import eden_cfg       # lazy (eden_cfg pulls in SDL); matches this module's lazy imports
    if "button:" not in cur:
        if "axis:" in cur:        # a guid-ful axis d-pad: not re-mappable as a hat/button here
            raise RpcError("EINVAL", "this pad's d-pad can't be remapped here")
        # cleared/missing binding (skeleton) -> build from the device TEMPLATE's own d-pad structure
        m = _GUID_RE.search(cur)
        tv = eden_cfg._match_template(input_dir, m.group(1) if m else "").get(key, "")
        hm = re.search(r"hat:(\d+)", tv)
        if hm:                    # hat-style pad (Citron DS, Deck): synthesise the hat binding
            return _append_token(cur, f"hat:{hm.group(1)},direction:{d}"), f"D-pad {d}"
        idx = eden_cfg.dpad_index(input_dir, cur, d, key)
        if idx is None:
            idx = eden_hat_button_index(token)
        if idx is None:
            raise RpcError("EINVAL", "press a d-pad direction")
        return _append_token(cur, f"button:{idx}"), f"D-pad {d}"
    idx = eden_cfg.dpad_index(input_dir, cur, d, key)
    if idx is None:
        idx = eden_hat_button_index(token)   # last resort: the historical Wii U rank
    if idx is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    return _BTN_RE.sub(f"button:{idx}", cur, count=1), f"D-pad {d}"


def _remap_button(cur: str, params) -> tuple:
    """New value for a plain digital button row (face/shoulder/thumb/Minus/Plus, and ZL/ZR on
    a button-trigger pad). Re-points `button:idx`, preserving the device."""
    try:
        code = int(params["value"])
    except (KeyError, ValueError, TypeError):
        raise RpcError("EINVAL", "missing or invalid button code")
    scheme = _scheme_of(cur)
    idx = xemu_button_index(code) if scheme == "gc" else sdl_button_index(code)
    if idx is None:
        raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                 "trigger, stick-click, Minus or Plus button")
    if "axis:" in cur:                           # a ZL/ZR analog row -> not a plain button
        raise RpcError("EINVAL", "this control is an axis on this pad — use the trigger row")
    label = _btn_label(idx, scheme)
    if "button:" in cur:
        return _BTN_RE.sub(f"button:{idx}", cur, count=1), label
    return _append_token(cur, f"button:{idx}"), label   # cleared/missing binding -> add the button


def _remap_trigger(cur: str, token: str):
    """New value for an analog-trigger row (ZL/ZR on a DS/DS4/Deck) from an axisname trigger
    token. Re-points `axis:N` (keeping the existing threshold/invert), preserving the device;
    synthesises a fresh axis binding if the slot somehow was not an axis."""
    parsed = parse_axis_token(token)
    if parsed is None or not canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "pull the trigger")
    scheme = _scheme_of(cur)
    rank = xemu_axis_index(parsed[1]) if scheme == "gc" else axis_token_rank(token)
    if rank is None:
        raise RpcError("EINVAL", "pull the trigger")
    label = _axis_label(rank, scheme)
    if "axis:" in cur:
        return re.sub(r"axis:\d+", f"axis:{rank}", cur, count=1), label
    m = _GUID_RE.search(cur)
    pm = re.search(r"port:(\d+)", cur)
    guid = m.group(1) if m else ""
    port = pm.group(1) if pm else "0"
    return (f'"engine:sdl,invert:+,port:{port},guid:{guid},axis:{rank},threshold:0.500000"',
            label)


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
    dev_guid, dev_port = _player_device(text, player)
    if not dev_guid:                             # the PLAYER has no controller at all -> genuinely unset
        raise RpcError("EINVAL", f"{_plabel(player)} has no pad here. Configure it in Citron first.")
    if "guid:" not in cur:                       # this one binding is missing/cleared -> re-create it
        cur = f'"engine:sdl,port:{dev_port},guid:{dev_guid}"'   # QUOTED skeleton (Citron values are quoted)
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
