"""xemu.input_* — per-button input mapping for the Xbox tile (xemu).

Edits the `[input] gamepad_mappings` array of
~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml. Each entry is keyed by
`gamepad_id` (an SDL GUID = the same string as `[input.bindings] portN`); its
`controller_mapping` table maps each Xbox control NAME → the
SDL_GameControllerButton index of the physical button that drives it. A per-
button remap sets `controller_mapping.<xbox_key> = <sdl_index>` on the pad bound
to port1, seeding the entry if absent (xemu finds pre-seeded entries by
gamepad_id and uses them as-is).

xemu >= v0.8.133 is required (older builds had hardcoded, GUI-less mappings); we
version-gate and otherwise show a read-only "remap in xemu's Settings → Input"
note. The Standalones launch wrapper (switch_bind) only snapshots/restores
[input.bindings], so a controller_mapping remap PERSISTS and is never clobbered
at launch. xemu rewrites xemu.toml (delta TOML) on exit, so we refuse while it is
running. Triggers and sticks are SDL axes (the button-capture path emits only
buttons/hats), so v1 keeps them — and the d-pad (a hat) — read-only.
"""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

from .. import proc_guard
from .. import xemu_cfg
from . import cfgutil
from .input_translate import (axis_invert, parse_axis_token, xemu_axis_index,
                              xemu_axis_label, xemu_button_index, xemu_hat_dpad_index,
                              xemu_index_label)
from .rpc import RpcError, method

_FILE = Path.home() / ".var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml"
_PROC = "xemu"
_MIN_VERSION = (0, 8, 133)

# (controller_mapping key, label) — the remappable digital buttons, display order.
# KEY = the Xbox control name xemu uses in controller_mapping.
_BUTTONS = [
    ("a", "A"), ("b", "B"), ("x", "X"), ("y", "Y"),
    ("lshoulder", "L (LB)"), ("rshoulder", "R (RB)"),
    ("lstick_btn", "L-stick click"), ("rstick_btn", "R-stick click"),
    ("back", "Back"), ("start", "Start"), ("guide", "Guide"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# D-pad directions — captured as a hat (kind="hat") and stored in controller_mapping
# (dpad_* = SDL_GameControllerButton index 11..14).
_DPAD = [
    ("dpad_up", "D-pad Up"), ("dpad_down", "D-pad Down"),
    ("dpad_left", "D-pad Left"), ("dpad_right", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
# Analog sticks + triggers — captured as an axis (kind="axis"); stored in
# controller_mapping as axis_* = SDL_GameControllerAxis index (0..5), with an
# invert_axis_* bool for the sticks. Labels carry the directed-push instruction.
_STICKS = [
    ("axis_left_x", "L-stick X — push right"), ("axis_left_y", "L-stick Y — push down"),
    ("axis_right_x", "R-stick X — push right"), ("axis_right_y", "R-stick Y — push down"),
]
_TRIGGERS = [("axis_trigger_left", "L trigger — pull"),
             ("axis_trigger_right", "R trigger — pull")]
_STICK_KEYS = {k for k, _ in _STICKS}
_AXIS_KEYS = _STICK_KEYS | {k for k, _ in _TRIGGERS}
_LABEL = dict(_BUTTONS + _DPAD + _STICKS + _TRIGGERS)
# xemu's DEFAULT controller_mapping is the identity (Xbox A ← SDL button A=0, …).
# Used to show the EFFECTIVE binding for keys absent from the delta config.
_DEFAULT_GC = {
    "a": 0, "b": 1, "x": 2, "y": 3, "back": 4, "guide": 5, "start": 6,
    "lstick_btn": 7, "rstick_btn": 8, "lshoulder": 9, "rshoulder": 10,
    "dpad_up": 11, "dpad_down": 12, "dpad_left": 13, "dpad_right": 14,
    "axis_left_x": 0, "axis_left_y": 1, "axis_right_x": 2, "axis_right_y": 3,
    "axis_trigger_left": 4, "axis_trigger_right": 5,
}

# portN = 'GUID' lines in [input.bindings] (the launch wrapper writes these per launch).
_PORT_RE = re.compile(r"(?m)^\s*port(\d+)\s*=\s*'([^']*)'")


@functools.lru_cache(maxsize=1)
def _supports_remap() -> bool:
    """True iff the installed xemu can file-configure controller_mapping
    (>= v0.8.133). Falls back to 'an [input] gamepad_mappings array exists' when
    the flatpak version query fails."""
    try:
        out = subprocess.run(["flatpak", "info", "app.xemu.xemu"],
                             capture_output=True, text=True, timeout=8)
        m = re.search(r"(?im)^\s*Version:\s*v?(\d+)\.(\d+)\.(\d+)", out.stdout)
        if m:
            return tuple(int(g) for g in m.groups()) >= _MIN_VERSION
    except Exception:
        pass
    try:
        if _FILE.is_file():
            return bool(xemu_cfg.read_gamepad_mappings(
                _FILE.read_text(encoding="utf-8", errors="replace")))
    except Exception:
        pass
    return False


def _bound_ports(text: str) -> list[tuple[int, str]]:
    """[(port number, GUID)] for ports with a non-empty GUID, in port order. xemu
    binds by GUID (device CLASS), so two identical pads share one GUID across ports."""
    found: dict[int, str] = {}
    for m in _PORT_RE.finditer(text):
        n, g = int(m.group(1)), m.group(2)
        if g and n not in found:
            found[n] = g
    return [(n, found[n]) for n in sorted(found)]


def _players_and_target(text: str, params) -> tuple[list[dict], str, str]:
    """(players list, selected player id, target GUID). Players are keyed/labelled by
    the REAL console port number (Player 1 / Player 3 if port2 is unbound) — not a
    dense 1..K index — so "Player N" always edits the pad on xemu port N. With nothing
    bound, a single pseudo Player 1 falls back to the first gamepad_mappings entry so
    the page still shows a layout."""
    ports = _bound_ports(text)
    if not ports:
        guid = next((e["gamepad_id"] for e in xemu_cfg.read_gamepad_mappings(text)
                     if isinstance(e, dict) and e.get("gamepad_id")), "")
        return [{"id": "1", "label": "Player 1"}], "1", guid
    players = [{"id": str(n), "label": f"Player {n}"} for n, _g in ports]
    by_port = {str(n): g for n, g in ports}
    sel = params.get("player") or str(ports[0][0])
    if sel not in by_port:
        sel = str(ports[0][0])
    return players, sel, by_port[sel]


def _pad_name(guid: str) -> str:
    if not guid:
        return ""
    try:
        from ..mad_config import pad_name, vidpid_from_sdl_guid
        return pad_name(vidpid_from_sdl_guid(guid))
    except Exception:
        return ""


def _current_map(text: str, guid: str) -> dict:
    """controller_mapping of the entry for `guid` (empty if none)."""
    for e in xemu_cfg.read_gamepad_mappings(text):
        if isinstance(e, dict) and e.get("gamepad_id") == guid:
            cm = e.get("controller_mapping")
            return cm if isinstance(cm, dict) else {}
    return {}


@method("xemu.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"xemu config not found at {_FILE} — launch an Xbox game once")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    run = proc_guard.emulator_running(_PROC)
    supported = _supports_remap()
    players, player, guid = _players_and_target(text, params)
    cm = _current_map(text, guid) if supported else {}

    def row(key, label, kind, capturable):
        cur = cm.get(key, _DEFAULT_GC[key])
        value = xemu_axis_label(cur) if kind == "axis" else xemu_index_label(cur)
        return {"id": key, "label": label, "kind": kind, "value": value,
                "capturable": capturable and supported and not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn", True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
        {"title": "Triggers", "binds": [row(k, l, "axis", True) for k, l in _TRIGGERS]},
    ]
    cname = _pad_name(guid)
    # Two ports on the same GUID (two identical pads) share one controller_mapping —
    # xemu can't tell them apart, so flag it so a "Player 2" edit isn't a surprise.
    shared = guid and sum(1 for _, g in _bound_ports(text) if g == guid) > 1
    if not supported:
        note = "This xemu is older than v0.8.133 — remap in xemu's Settings → Input."
    elif run:
        note = "Close xemu first — it rewrites xemu.toml on exit."
    elif not guid:
        note = "Bind a controller to a port on the Controllers page first."
    else:
        note = (f"Controller: {cname}.  " if cname else "") + \
               ("This controller is on more than one port — they share one mapping.  "
                if shared else "") + \
               "For sticks/triggers, push the stick the way the row says (or pull the trigger)."
    return {"running": run, "note": note, "groups": groups, "players": players, "player": player}


@method("xemu.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key in _DPAD_KEYS and kind == "hat":
        idx = xemu_hat_dpad_index(str(params.get("value", "")))
        if idx is None:
            raise RpcError("EINVAL", "press a d-pad direction")
        updates, disp = {key: idx}, xemu_index_label(idx)
    elif key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        idx = xemu_button_index(code)
        if idx is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, "
                                     "shoulder, stick-click, Back, Start or Guide button")
        updates, disp = {key: idx}, xemu_index_label(idx)
    elif key in _AXIS_KEYS and kind == "axis":
        parsed = parse_axis_token(str(params.get("value", "")))
        if parsed is None:
            raise RpcError("EINVAL", "push the stick the way the row says (or pull the trigger)")
        sign, canonical = parsed
        idx = xemu_axis_index(canonical)
        if idx is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
        updates = {key: idx}
        if key in _STICK_KEYS:        # sticks carry an invert flag; triggers don't
            updates[key.replace("axis_", "invert_axis_", 1)] = axis_invert(sign, canonical)
        disp = xemu_axis_label(idx)
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable xemu input")
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"xemu config not found at {_FILE}")
    if not _supports_remap():
        raise RpcError("EINVAL", "update xemu to v0.8.133+ to remap inputs here")
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close xemu first — it rewrites xemu.toml on exit")
    text = _FILE.read_text(encoding="utf-8", errors="replace")
    _, player, guid = _players_and_target(text, params)
    if not guid:
        raise RpcError("EINVAL", f"no controller bound to Player {player} — set its pad on "
                                 "the Controllers page first")
    new = xemu_cfg.set_controller_mappings(text, guid, updates)
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": disp, "message": f"{_LABEL.get(key, key)} ← {disp}"}
