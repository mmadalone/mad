"""pcsx2pgin.* — PER-GAME input for standard PCSX2, keyed by disc serial+CRC.

PCSX2 does not honor `[Pad]`/`[USB1]`/`[USB2]` in a per-game gamesettings ini (input is
global-only, verified in VMManager.cpp/InputManager.cpp), and its native per-game route
(Input Profiles) would replace the whole input layer and bypass our launch-time pad
calibration. So per-game input intent lives in OUR store and the router applies it to the
GLOBAL ini at launch, transiently (snapshotted + reverted on exit) — see lib/switch_bind.py.

This module is only the editor. It mirrors the global input page (pcsx2_input_cmds): the
same button / d-pad / stick capture rows via input_translate, plus three global-scope
SELECTORS the input-map page renders — USB Port 1, USB Port 2 (device or None=off) and
Player 2 (on/off). Everything is per game (titleid = "<SERIAL>_<CRC>"); nothing here writes
a PCSX2 file. v1 covers Players 1-2; pad->player physical assignment is a later pass.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
from pathlib import Path

from .. import mad_paths, pcsx2_cfg, staterev
from . import cfgutil, pcsx2_games
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .pcsx2_input_cmds import _BUTTONS, _DPAD, _DPAD_KEYS, _STICK_KEYS, _STICKS
from .rpc import RpcError, method

_STORE = mad_paths.storage("pcsx2", "pergame-input.json")
_GLOBAL_INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()
_KEY_RE = re.compile(r"^[A-Z]{3,4}-\d{3,5}_[0-9A-F]{8}$")
_LOCK = threading.Lock()

_PLAYERS = [{"id": "1", "label": "Player 1"}, {"id": "2", "label": "Player 2"}]
_PLAYER_IDS = {"1", "2"}

# Bind rows: reuse the global page's buttons / d-pad / sticks, but present L2/R2 as ANALOG
# TRIGGER rows (pull the trigger -> +LeftTrigger / +RightTrigger; also how X-Arcade LT/RT and
# most pads expose them) rather than digital buttons, so a full pull registers and sticks +
# triggers are both rebindable per game. L2/R2 dropped from Buttons to avoid a same-key double row.
_PG_BUTTONS = [(k, l) for k, l in _BUTTONS if k not in ("L2", "R2")]
_PG_BUTTON_KEYS = {k for k, _ in _PG_BUTTONS}
_TRIGGERS = [("L2", "L2 (analog)"), ("R2", "R2 (analog)")]
_AXIS_KEYS = _STICK_KEYS | {"L2", "R2"}

# USB port selector = enable/disable the port. "" = inherit the global (which already carries the
# device AND its bind block, e.g. a globally-configured GunCon2 on USB1); "None" = force the port
# OFF for this game. We deliberately do NOT offer enabling a specific device here: PCSX2 needs that
# device's full bind block, which the dedicated lightgun pages (pcsx2x6/ps2guncon) own; writing only
# Type= would leave an unbound, unusable device. So v1 is a clean per-game port on/off.
_USB_OPTS = [{"value": "", "label": "Inherit global"},
             {"value": "None", "label": "None (port off)"}]
_USB_VALUES = {o["value"] for o in _USB_OPTS}
_PAD2_OPTS = [{"value": "", "label": "Inherit global"},
             {"value": "on", "label": "On"},
             {"value": "off", "label": "Off"}]
_PAD2_VALUES = {o["value"] for o in _PAD2_OPTS}
_SELECTOR_KEYS = {"usb1", "usb2", "pad2"}


# ── store ─────────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # Corrupt store (external / hand edit): preserve it for recovery instead of silently
        # overwriting every other game's overrides on the next save (rule #5: never destroy data).
        try:
            bad = _STORE.with_name(_STORE.name + ".bad")
            if not bad.exists():
                shutil.copy2(_STORE, bad)
            print(f"pcsx2pgin: {_STORE.name} is corrupt; backed up to {bad.name}, starting fresh",
                  file=sys.stderr)
        except OSError:
            pass
        return {}


def _save(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.atomic_write(_STORE, json.dumps(data, indent=2, sort_keys=True))


def _entry_binds(e: dict) -> dict:
    """The entry's per-player binds as a clean {player: {key: source}}, ignoring any non-dict
    cruft from a hand-edited store (mirrors pcsx2_cfg.load_input_overrides' isinstance filtering)."""
    binds = e.get("binds")
    if not isinstance(binds, dict):
        return {}
    return {p: v for p, v in binds.items() if isinstance(v, dict) and v}


def _is_empty(e: dict) -> bool:
    return (e.get("usb1") is None and e.get("usb2") is None
            and e.get("pad2") is None and not _entry_binds(e))


def load_entry(titleid: str) -> dict | None:
    """The per-game input override for one game (or None). Public: the launch-time router
    (lib/switch_bind.py) reads it to apply USB/Pad2/binds to the global ini at game start."""
    if not titleid or not _KEY_RE.match(titleid):
        return None
    e = _load().get(titleid)
    return e if isinstance(e, dict) and not _is_empty(e) else None


# ── helpers ────────────────────────────────────────────────────────────────────
def _titleid(params) -> str:
    tid = params.get("titleid") or ""
    if not _KEY_RE.match(tid):
        raise RpcError("EINVAL", f"bad game id {tid!r}")
    return tid


def _player(params) -> str:
    p = str(params.get("player") or "1")
    return p if p in _PLAYER_IDS else "1"


def _global_source(player: int, key: str) -> str:
    """The resolved GLOBAL binding for this player+button = the baked DualShock2 default
    layered with any global per-player remap. This is the value a per-game row inherits."""
    ov = pcsx2_cfg.load_input_overrides(_GLOBAL_INI).get(player, {})
    return ov.get(key) or pcsx2_cfg.baked_default_sources().get(key, "")


def _selectors(entry: dict) -> list:
    usb1, usb2, pad2 = entry.get("usb1"), entry.get("usb2"), entry.get("pad2")
    return [
        {"key": "usb1", "label": "USB Port 1", "scope": "global", "dependent": False,
         "value": usb1 or "", "options": _USB_OPTS},
        {"key": "usb2", "label": "USB Port 2", "scope": "global", "dependent": False,
         "value": usb2 or "", "options": _USB_OPTS},
        {"key": "pad2", "label": "Player 2 pad", "scope": "global", "dependent": False,
         "value": ("" if pad2 is None else ("on" if pad2 else "off")), "options": _PAD2_OPTS},
    ]


# ── RPC ─────────────────────────────────────────────────────────────────────────
@method("pcsx2pgin.input_get", slow=True)
def _input_get(params):
    tid = _titleid(params)
    player = _player(params)
    pint = int(player)
    entry = load_entry(tid) or {}
    binds = _entry_binds(entry).get(player, {})

    def row(key, label, kind):
        src = binds.get(key) or _global_source(pint, key)
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—", "capturable": True}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _PG_BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis") for k, l in _STICKS]},
        {"title": "Triggers", "binds": [row(k, l, "axis") for k, l in _TRIGGERS]},
    ]
    # No running/EBUSY gate: this writes only our own JSON store (never PCSX2's config); it is
    # applied by the router at the game's NEXT launch, so editing it any time is harmless.
    note = (f"Per-game input for Player {player}. USB ports, Player 2 and button remaps here apply "
            "only to this game, set at launch and reverted on exit. Blank = inherit the global. It "
            "takes effect at the game's next launch.")
    return {"running": False, "note": note, "groups": groups,
            "selectors": _selectors(entry), "players": _PLAYERS, "player": player}


@method("pcsx2pgin.input_set", slow=True)
def _input_set(params):
    tid = _titleid(params)
    key, kind = params.get("id", ""), params.get("kind", "btn")
    if key in _DPAD_KEYS and kind == "hat":
        source = pcsx2_dpad_source(str(params.get("value", "")))
        if source is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _AXIS_KEYS and kind == "axis":         # sticks + analog triggers (L2/R2)
        parsed = parse_axis_token(str(params.get("value", "")))
        if parsed is None:
            raise RpcError("EINVAL", "push the stick, or pull the trigger, in that direction")
        source = pcsx2_axis_source(*parsed)
        if source is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
    elif key in _PG_BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        source = sdl_button_source(code)
        if source is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                    "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    player = _player(params)
    with _LOCK:
        data = _load()
        entry = data.setdefault(tid, {})
        if not isinstance(entry.get("binds"), dict):        # heal a hand-corrupted entry
            entry["binds"] = {}
        if not isinstance(entry["binds"].get(player), dict):
            entry["binds"][player] = {}
        entry["binds"][player][key] = source
        _save(data)
    staterev.bump("config")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}"}


@method("pcsx2pgin.selector_set", slow=True)
def _selector_set(params):
    tid = _titleid(params)
    key = params.get("key", "")
    value = str(params.get("value", "")).strip()
    if key not in _SELECTOR_KEYS:
        raise RpcError("EINVAL", f"unknown selector {key!r}")
    if key in ("usb1", "usb2"):
        if value not in _USB_VALUES:
            raise RpcError("EINVAL", f"bad USB type {value!r}")
        store_val = value or None
    else:                                             # pad2
        if value not in _PAD2_VALUES:
            raise RpcError("EINVAL", f"bad Player 2 value {value!r}")
        store_val = None if value == "" else (value == "on")
    with _LOCK:
        data = _load()
        e = data.setdefault(tid, {})
        if store_val is None:
            e.pop(key, None)
        else:
            e[key] = store_val
        if _is_empty(e):                              # keep the picker badge accurate
            data.pop(tid, None)
        _save(data)
    staterev.bump("config")
    return {"key": key, "value": value}


@method("pcsx2pgin.games", slow=True)
def _games(params):
    store = _load()
    return {"games": [{"titleid": g["key"], "name": g["name"], "override": g["key"] in store}
                      for g in pcsx2_games.games()]}
