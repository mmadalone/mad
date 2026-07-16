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
KB_LIMIT = 512           # SDL_NUM_SCANCODES: valid keyboard codes are < this
                         # (openbor_cfg accepts < 600 = JOY_LIST_FIRST when
                         # READING a file the engine wrote; we only ever WRITE
                         # real scancodes)

# JOY_MAX_INPUTS (the per-port keycode stride) is GENERATION-DEPENDENT:
# 32 in engines compiled before ~June 2018, 64 after (verified in source:
# still 32 at 2018-05-01, 64 by 2018-07-01). Port 0 is stride-independent.
STRIDE_OLD = 32
STRIDE_NEW = 64
STRIDE_FLIP = (2018, 6)  # (year, month): compile date >= this -> STRIDE_NEW

# ── device layout ─────────────────────────────────────────────────────────────
# Buttons in Wine's XInput order (NOT raw uinput declaration order — verified
# via the banked keycode 610 = ThumbR at offset 9). Buttons 0..9 hold the same
# order under Wine's older joystick driver too (verified from a hand-made
# Contrav2 map: A=0, X=2, Y=3, RB=5, Start=7); only Guide(10) is XInput-only.
_BTN_ORDER = ["a", "b", "x", "y", "lb", "rb", "back", "start",
              "thumbl", "thumbr", "guide"]
# Axis order as the engine enumerates them; each contributes a -/+ pair.
# (a2/a5 are the analog triggers, which only ever fire their positive travel.)
_AXIS_ORDER = [("lx-", "lx+"), ("ly-", "ly+"), ("lt-", "lt"),
               ("rx-", "rx+"), ("ry-", "ry+"), ("rt-", "rt")]

# The canonical (SDL2/XInput) geometry: 11 buttons, 6 axes -> hat base 23.
# This is what every modern-engine game sees, and what the DEFAULT_MAP targets.
GEOM_XINPUT = (11, 6)

_BTN_LABEL = {"a": "A", "b": "B", "x": "X", "y": "Y", "lb": "LB", "rb": "RB",
              "back": "Back", "start": "Start", "thumbl": "L-stick click",
              "thumbr": "R-stick click", "guide": "Guide"}
_AX_LABEL = {"lt": "Left trigger", "rt": "Right trigger",
             "lx-": "L-stick left", "lx+": "L-stick right",
             "ly-": "L-stick up", "ly+": "L-stick down",
             "rx-": "R-stick left", "rx+": "R-stick right",
             "ry-": "R-stick up", "ry+": "R-stick down"}


def offsets_for(buttons: int, axes: int) -> dict:
    """The canonical token -> within-device offset table for a pad the engine
    reports as `buttons`/`axes`. OpenBOR lays a device out as
    buttons [0..B-1], then each axis as a -/+ pair, then each hat as 4 dirs:
        axis i  -> B + 2i (negative), B + 2i + 1 (positive)
        hat dir -> B + 2A + {0:up, 1:right, 2:down, 3:left}

    The geometry is NOT constant across our library: the SDL2-era engines see
    an XInput pad (11 buttons, 6 axes -> hat base 23), while the pre-SDL2
    engines route the SAME pad through Wine's joystick driver and see 10
    buttons / 5 axes -> hat base 20. Hardcoding one base silently mis-binds the
    other generation (proven on-device 2026-07-16: Contrav2's hand-made d-pad
    sits at 20-23). Buttons 0..9 are in the same order under both drivers.

    Tokens the geometry cannot express (e.g. ax:rt on a 5-axis view, which has
    no axis 5) are simply absent -> keycode() maps them to UNMAPPED."""
    off = {name: i for i, name in enumerate(_BTN_ORDER) if i < buttons}
    for i, (lo, hi) in enumerate(_AXIS_ORDER):
        if i < axes:
            off[lo] = buttons + 2 * i
            off[hi] = buttons + 2 * i + 1
    base = buttons + 2 * axes
    for i, d in enumerate(("up", "right", "down", "left")):
        off[f"hat:{d}"] = base + i
    return off


def token_offset(token: str, geom: tuple[int, int] = GEOM_XINPUT) -> int | None:
    """Offset for a btn:/ax:/hat: token under the pad geometry `geom`
    (buttons, axes); None for kb:/none/unknown/inexpressible."""
    kind, _, name = token.partition(":")
    if kind not in ("btn", "ax", "hat"):
        return None
    key = token if kind == "hat" else name
    return offsets_for(*geom).get(key)


def keycode(token: str, port: int, stride: int = STRIDE_NEW,
            geom: tuple[int, int] = GEOM_XINPUT) -> int:
    """The int32 OpenBOR stores for `token` bound on joystick `port` (0-3),
    under the engine's per-port `stride` (JOY_MAX_INPUTS) and the pad geometry
    `geom` the engine reports (buttons, axes).

    `none` -> -999; `kb:<n>` -> the raw keyboard scancode (port-independent);
    unknown / out-of-range / geometry-inexpressible tokens -> -999 (never guess
    a binding).

    kb values are RANGE-CHECKED: the engine's keyboard space is scancodes
    < SDL_NUM_SCANCODES, well below JOY_LIST_FIRST. An unchecked value would
    either land in the joystick space (binding a phantom pad control) or, if
    huge (e.g. someone writes an SDLK_* constant instead of a scancode), poison
    the cfg — the next launch's validation would reject the whole file and the
    map would be silently dead until an in-game rebind."""
    if not token or not isinstance(token, str) or token == "none":
        return UNMAPPED
    kind, _, name = token.partition(":")
    if kind == "kb":
        try:
            n = int(name)
        except ValueError:
            return UNMAPPED
        return n if 0 <= n < KB_LIMIT else UNMAPPED
    off = token_offset(token, geom)
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


# (No map_to_keys helper: openbor_cfg.apply_map owns row building because only
# it knows the file's resolved slot count and stride. A convenience wrapper with
# defaults for either would be a trap — a caller omitting stride would silently
# write ports 1-3 for the wrong engine generation.)


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


# ── seeding ───────────────────────────────────────────────────────────────────
# We give a game our default map ONCE and then never touch its cfg again. The
# game's own Options -> Controls menu is the editor from that point on, and the
# engine rewrites the cfg from memory on quit, so an edit made in there is what
# survives. Writing every launch (what this used to do) silently undid it.
#
# Seeding still earns its keep: a game's bundled map is usually wrong for this
# rig (MIW_Definitive shipped a mixed Deck/X-Arcade one), and the default
# configures all 4 players at once, which the in-game menu would take four
# passes to do.

def is_seeded(dir_key: str) -> bool:
    seeded = _load().get("seeded", {})
    return isinstance(seeded, dict) and bool(seeded.get(dir_key))


def mark_seeded(dir_key: str) -> None:
    data = _load()
    seeded = data.get("seeded")
    if not isinstance(seeded, dict):
        seeded = {}
    seeded[dir_key] = True
    data["seeded"] = seeded
    _save(data)


def clear_seeded(dir_key: str | None = None) -> list[str]:
    """Forget that a game was seeded, so the next launch re-applies the default.

    The escape hatch for an in-game config edited into a corner: there is no
    other way back, because the engine owns the file once we hand off. Returns
    the keys cleared. `None` clears every game."""
    data = _load()
    seeded = data.get("seeded")
    if not isinstance(seeded, dict):
        return []
    gone = sorted(seeded) if dir_key is None else ([dir_key] if dir_key in seeded else [])
    for k in gone:
        seeded.pop(k, None)
    if gone:
        data["seeded"] = seeded
        _save(data)
    return gone


def effective_map(dir_key: str) -> dict:
    """DEFAULT_MAP overlaid with the game's stored override (by manifest DIR).

    Non-str values are dropped: the store is hand-editable, and a bare number
    would otherwise reach keycode() and raise, aborting the whole map write."""
    games = _load().get("games", {})
    override = games.get(dir_key, {})
    if not isinstance(override, dict):
        override = {}
    return {**DEFAULT_MAP,
            **{k: v for k, v in override.items()
               if k in SLOTS and isinstance(v, str)}}


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
