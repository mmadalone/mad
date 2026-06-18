"""rpcs3.input_* — per-button input mapping for the PlayStation 3 tile (RPCS3).

RPCS3 is special: the user's RESTING controller config (input_configs/global/Default.yml)
is usually RPCS3's NATIVE handler (DualSense / DualShock / Evdev), and the Standalones
launch wrapper is TRANSIENT for rpcs3 — it snapshots the resting config, writes an SDL
profile for the game session (so it can bind pads by name), and RESTORES the native config
on exit. So there is no stable on-disk SDL config the user could edit directly.

Therefore MAD stores button remaps in its OWN per-player override file
(rpcs3_cfg.load_overrides / save_overrides), and lib/rpcs3_cfg._player_block MERGES those
overrides into the transient SDL profile at launch. Result: a remap APPLIES in-game on
every ES-DE launch, the user's resting native config is never touched, and editing works
regardless of the resting handler (and even while RPCS3 is running — it takes effect next
launch). Maps are device-agnostic SDL source tokens, so they follow "Player N" whichever
pad the pads→players order assigns to that slot.

Buttons + d-pad remappable; sticks shown read-only (RPCS3 stores them as `LS X-` … axis
tokens — a separate vocabulary, a later pass).
"""
from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing → cannot read Default.yml
    yaml = None

from .. import rpcs3_cfg
from .input_translate import rpcs3_button, rpcs3_dpad, rpcs3_token_label
from .rpc import RpcError, method

_DEFAULT = Path.home() / ".config/rpcs3/input_configs/global/Default.yml"
# Canonical SDL-profile defaults, to show the effective binding for un-overridden keys.
_DEFAULT_CONFIG = rpcs3_cfg._SDL_PLAYER["Config"]

# (RPCS3 Config key, label) — the remappable digital buttons.
_BUTTONS = [
    ("Cross", "Cross  ✕"), ("Circle", "Circle  ○"),
    ("Triangle", "Triangle  △"), ("Square", "Square  ▢"),
    ("L1", "L1"), ("R1", "R1"), ("L2", "L2"), ("R2", "R2"),
    ("L3", "L3"), ("R3", "R3"), ("Select", "Select"), ("Start", "Start"),
    ("PS Button", "PS"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# D-pad directions — captured as a hat (kind="hat"); RPCS3 stores the literal direction.
_DPAD = [
    ("Up", "D-pad Up"), ("Down", "D-pad Down"),
    ("Left", "D-pad Left"), ("Right", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
# Analog sticks — read-only in v1 (RPCS3 stores them as `LS X-` … axis tokens).
_STICKS = [
    ("Left Stick Up", "L-stick Up"), ("Left Stick Down", "L-stick Down"),
    ("Left Stick Left", "L-stick Left"), ("Left Stick Right", "L-stick Right"),
    ("Right Stick Up", "R-stick Up"), ("Right Stick Down", "R-stick Down"),
    ("Right Stick Left", "R-stick Left"), ("Right Stick Right", "R-stick Right"),
]
_LABEL = dict(_BUTTONS + _DPAD + _STICKS)


def _resting() -> dict:
    """The resting Default.yml as a dict (for player count + device names), or {}."""
    if yaml is None or not _DEFAULT.is_file():
        return {}
    try:
        data = yaml.safe_load(_DEFAULT.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _player_count(data: dict) -> int:
    """How many players to offer = configured (non-Null) `Player N Input` blocks,
    walked densely from 1 (the launch wrapper assigns pads to players 1..N in order, so
    a dense Player index == the launch slot the override applies to). Min 1."""
    n = 0
    for k in range(1, 8):
        b = data.get(f"Player {k} Input")
        if isinstance(b, dict) and b.get("Handler") not in (None, "Null"):
            n = k
        else:
            break
    return max(n, 1)


def _resolve_player(params, count: int) -> int:
    raw = params.get("player")
    if raw in (None, ""):
        return 1                       # first load → Player 1
    try:
        i = int(raw)
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"invalid player {raw!r}")
    if not 1 <= i <= count:            # reject (don't silently misdirect to Player 1)
        raise RpcError("EINVAL", f"Player {i} isn't available (you have {count})")
    return i


@method("rpcs3.input_get", slow=True, cache=("config",))
def _input_get(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot read RPCS3 input config")
    data = _resting()
    count = _player_count(data)
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, count + 1)]
    player = _resolve_player(params, count)
    ovp = rpcs3_cfg.load_overrides().get(player, {})
    # Display base mirrors what _player_block binds in-game: a MAD override wins, else the
    # user's resting SDL Config (if they set RPCS3's SDL handler directly — it's preserved),
    # else the canonical template default.
    rblock = data.get(f"Player {player} Input")
    resting_cfg = (rblock.get("Config") if isinstance(rblock, dict)
                   and rblock.get("Handler") == "SDL"
                   and isinstance(rblock.get("Config"), dict) else None) or {}

    def row(key, label, kind, capturable):
        tok = ovp.get(key) or resting_cfg.get(key) or _DEFAULT_CONFIG.get(key)
        return {"id": key, "label": label, "kind": kind,
                "value": rpcs3_token_label(tok) if tok else "—",
                "capturable": capturable}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn", True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks (read-only)",
         "binds": [row(k, l, "axis", False) for k, l in _STICKS]},
    ]
    note = (f"Remaps Player {player} — applied when you launch a PS3 game from ES-DE "
            "(your normal RPCS3 controller setup is left untouched). "
            "Player → controller is set on the Controllers page.")
    return {"running": False, "note": note, "groups": groups,
            "players": players, "player": str(player)}


@method("rpcs3.input_set", slow=True)
def _input_set(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot save RPCS3 input overrides")
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key in _DPAD_KEYS and kind == "hat":
        token = rpcs3_dpad(str(params.get("value", "")))
        if token is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        token = rpcs3_button(code)
        if token is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                     "trigger, stick-click, Select, Start or PS button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable RPCS3 input")
    count = _player_count(_resting())
    player = _resolve_player(params, count)
    overrides = rpcs3_cfg.load_overrides()
    overrides.setdefault(player, {})[key] = token
    rpcs3_cfg.save_overrides(overrides)
    from .. import staterev
    staterev.bump("config")
    disp = rpcs3_token_label(token)
    return {"id": key, "value": disp, "message": f"{_LABEL.get(key, key)} → {disp}"}
