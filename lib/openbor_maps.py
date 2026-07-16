"""OpenBOR logical control maps — the canonical-pad vocabulary + the JSON store.

OpenBOR (all 34 bundled Windows engines under Proton) binds each control to one
int32 keycode = 601 + port*64 + offset, where `offset` indexes the pad's
controls buttons-then-axes-then-hat. Every pad an OpenBOR game sees on this rig
is CANONICAL — either Steam's Deck pad (28de:11ff) or a MAD merger vpad
(4d41:0002), both winebus-normalized to XInput 11 buttons / 6 axes / 1 hat —
so ONE logical map per game serves every player, pad and context. Offsets and
button order were measured on-device 2026-07-16 (deck-docs/openbor.md,
"winebus" section; memory `openbor-mad-input`).

This module owns:
- the token vocabulary (btn:a .. hat:left, ax:lt/rt, kb:<scancode>, none) and
  its token -> canonical-offset table,
- DEFAULT_MAP (the proven working map, == the verified MIW Deck map),
- the per-device-class evdev -> canonical translation tables shared by the
  P2 merger daemon (mad-openbor-pads.py) and P3 MAD capture,
- the per-game override store (~/Emulation/storage/openbor/input-maps.json).

lib/openbor_cfg.py turns a token map into the binary Saves/<pak>.cfg write.
"""
from __future__ import annotations

import json
import shutil
import sys

from . import mad_paths
from .fsutil import atomic_write_text

# ── slots (cfg order: keys[player][13]) ───────────────────────────────────────
SLOTS = ["up", "down", "left", "right",
         "atk1", "atk2", "atk3", "atk4",
         "jump", "special", "start", "sshot", "esc"]

UNMAPPED = -999          # the 3.0 line's "no binding" sentinel (4.0-7530 uses 6937)
_JOY_BASE = 601          # keycode = 600 + port*JOY_MAX_INPUTS + (1 + input)
MAX_PLAYERS = 4          # JOY_LIST_TOTAL — XInput caps OpenBOR at 4 pads

# JOY_MAX_INPUTS (the per-port keycode stride) is GENERATION-DEPENDENT:
# 32 in engines compiled before ~June 2018, 64 after (verified in source:
# still 32 at 2018-05-01, 64 by 2018-07-01). Port 0 is stride-independent.
STRIDE_OLD = 32
STRIDE_NEW = 64
STRIDE_FLIP = (2018, 6)  # (year, month): compile date >= this -> STRIDE_NEW

# ── canonical offsets (winebus-normalized XInput pad) ─────────────────────────
# Buttons in Wine's XInput order (NOT raw uinput declaration order — verified
# via the banked keycode 610 = ThumbR at offset 9): A,B,X,Y,LB,RB,Back,Start,
# ThumbL,ThumbR,Guide. Axes: 2 dirs each, minus then plus, from offset 11.
# Triggers only ever fire their positive travel. Hat base = 11 + 2*6 = 23.
_BTN_OFFSET = {"a": 0, "b": 1, "x": 2, "y": 3, "lb": 4, "rb": 5,
               "back": 6, "start": 7, "thumbl": 8, "thumbr": 9, "guide": 10}
_AX_OFFSET = {"lx-": 11, "lx+": 12, "ly-": 13, "ly+": 14, "lt": 16,
              "rx-": 17, "rx+": 18, "ry-": 19, "ry+": 20, "rt": 22}
_HAT_OFFSET = {"up": 23, "right": 24, "down": 25, "left": 26}

_BTN_LABEL = {"a": "A", "b": "B", "x": "X", "y": "Y", "lb": "LB", "rb": "RB",
              "back": "Back", "start": "Start", "thumbl": "L-stick click",
              "thumbr": "R-stick click", "guide": "Guide"}
_AX_LABEL = {"lt": "Left trigger", "rt": "Right trigger",
             "lx-": "L-stick left", "lx+": "L-stick right",
             "ly-": "L-stick up", "ly+": "L-stick down",
             "rx-": "R-stick left", "rx+": "R-stick right",
             "ry-": "R-stick up", "ry+": "R-stick down"}


def token_offset(token: str) -> int | None:
    """Canonical offset for a btn:/ax:/hat: token; None for kb:/none/unknown."""
    kind, _, name = token.partition(":")
    if kind == "btn":
        return _BTN_OFFSET.get(name)
    if kind == "ax":
        return _AX_OFFSET.get(name)
    if kind == "hat":
        return _HAT_OFFSET.get(name)
    return None


def keycode(token: str, port: int, stride: int = STRIDE_NEW) -> int:
    """The int32 OpenBOR stores for `token` bound on joystick `port` (0-3),
    under the engine generation's per-port `stride` (JOY_MAX_INPUTS).

    `none` -> -999; `kb:<n>` -> the raw keyboard scancode (port-independent);
    unknown tokens -> -999 (never guess a binding). All canonical offsets are
    <= 26, so every token is expressible under both strides."""
    if not token or token == "none":
        return UNMAPPED
    kind, _, name = token.partition(":")
    if kind == "kb":
        try:
            return int(name)
        except ValueError:
            return UNMAPPED
    off = token_offset(token)
    if off is None:
        return UNMAPPED
    return _JOY_BASE + port * stride + off


def token_label(token: str) -> str:
    """Human label for MAD chips ("A", "Right trigger", "D-pad up", "Key 27")."""
    if not token or token == "none":
        return "—"
    kind, _, name = token.partition(":")
    if kind == "btn":
        return _BTN_LABEL.get(name, name.upper())
    if kind == "ax":
        return _AX_LABEL.get(name, name)
    if kind == "hat":
        return f"D-pad {name}"
    if kind == "kb":
        return f"Key {name}"
    return token


# ── the default map (== the proven MIW Deck map, incl. the two slots recovered
#    from the live cfg: SSHOT unmapped, ESC = raw scancode 0) ──────────────────
DEFAULT_MAP = {
    "up": "hat:up", "down": "hat:down", "left": "hat:left", "right": "hat:right",
    "atk1": "btn:x", "atk2": "btn:rb", "atk3": "btn:lb", "atk4": "btn:y",
    "jump": "btn:a", "special": "ax:rt", "start": "btn:start",
    "sshot": "none", "esc": "kb:0",
}


def map_to_keys(token_map: dict, stride: int = STRIDE_NEW) -> list[list[int]]:
    """token map -> keys[MAX_PLAYERS][13] int32s, same map on every port."""
    merged = {**DEFAULT_MAP, **{k: v for k, v in token_map.items() if k in SLOTS}}
    return [[keycode(merged[s], port, stride) for s in SLOTS]
            for port in range(MAX_PLAYERS)]


# ── per-device-class evdev -> canonical tables (merger + MAD capture) ─────────
# Class tags: "xpad" = X-Arcade half / real 360 pads (045e:02a1);
#             "ps"   = DualSense / DualShock 4 (hid-playstation driver).
# Face buttons: the kernel xpad driver maps X->0x133(BTN_NORTH) Y->0x134(BTN_WEST)
# while hid-playstation is positional (Triangle=NORTH=top, Square=WEST=left) —
# so 0x133/0x134 mean OPPOSITE canonical faces on the two families. A fixed
# device-agnostic table would misread one of them; always pick by class.
CLASS_OF_VIDPID = {"045e:02a1": "xpad", "054c:0ce6": "ps", "054c:09cc": "ps"}

EVDEV_BTN = {
    "xpad": {0x130: "btn:a", 0x131: "btn:b", 0x133: "btn:x", 0x134: "btn:y",
             0x136: "btn:lb", 0x137: "btn:rb", 0x13a: "btn:back",
             0x13b: "btn:start", 0x13c: "btn:guide", 0x13d: "btn:thumbl",
             0x13e: "btn:thumbr"},
    "ps":   {0x130: "btn:a",      # Cross (south)
             0x131: "btn:b",      # Circle (east)
             0x134: "btn:x",      # Square (west)  — swapped vs xpad
             0x133: "btn:y",      # Triangle (north)
             0x136: "btn:lb", 0x137: "btn:rb",
             # BTN_TL2/TR2 (0x138/0x139) digital trigger clicks: dropped —
             # the analog ABS_Z/ABS_RZ travel carries lt/rt.
             0x13a: "btn:back", 0x13b: "btn:start", 0x13c: "btn:guide",
             0x13d: "btn:thumbl", 0x13e: "btn:thumbr"},
}

# The X-Arcade's arcade stick = four BTN_TRIGGER_HAPPY buttons (its ABS_HAT is a
# dead phantom that never fires). Same direction map as capture_cmds._HAPPY_DIR.
HAPPY_HAT = {0x2c0: "left", 0x2c1: "right", 0x2c2: "up", 0x2c3: "down"}

# Axis roles are identical across both families (evdev code -> role).
# lt/rt = analog trigger travel; hatx/haty = the real d-pad.
EVDEV_ABS_ROLE = {0x00: "lx", 0x01: "ly", 0x03: "rx", 0x04: "ry",
                  0x02: "lt", 0x05: "rt", 0x10: "hatx", 0x11: "haty"}


# ── the per-game override store ───────────────────────────────────────────────
_STORE = mad_paths.storage("openbor", "input-maps.json")


def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # Corrupt store (external / hand edit): preserve it for recovery instead
        # of silently overwriting every game's overrides on the next save.
        try:
            bad = _STORE.with_name(_STORE.name + ".bad")
            if not bad.exists():
                shutil.copy2(_STORE, bad)
            print(f"openbor_maps: {_STORE.name} is corrupt; backed up to "
                  f"{bad.name}, starting fresh", file=sys.stderr)
        except OSError:
            pass
        return {}


def _save(data: dict) -> None:
    atomic_write_text(_STORE, json.dumps(data, indent=2, sort_keys=True))


def effective_map(dir_key: str) -> dict:
    """DEFAULT_MAP overlaid with the game's stored override (by manifest DIR)."""
    games = _load().get("games", {})
    override = games.get(dir_key, {})
    if not isinstance(override, dict):
        override = {}
    return {**DEFAULT_MAP, **{k: v for k, v in override.items() if k in SLOTS}}


def game_override(dir_key: str) -> dict:
    """The raw stored override for a game ({} if none)."""
    o = _load().get("games", {}).get(dir_key, {})
    return o if isinstance(o, dict) else {}


def set_game_override(dir_key: str, override: dict | None) -> None:
    """Store (or with None/empty: remove -> inherit default) a game's override."""
    data = _load()
    games = data.setdefault("games", {})
    clean = {k: v for k, v in (override or {}).items()
             if k in SLOTS and isinstance(v, str)}
    if clean:
        games[dir_key] = clean
    else:
        games.pop(dir_key, None)
    _save(data)
