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

Buttons, d-pad and analog sticks remappable; sticks are stored as RPCS3 `LS X-`/`RS Y+`
axis tokens (via input_translate.rpcs3_axis_source; RPCS3's Y axis is up-positive).
"""
from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing → cannot read Default.yml
    yaml = None

from .. import rpcs3_cfg
from .input_buffer import InputBuffer
from .input_translate import (parse_axis_token, rpcs3_axis_source, rpcs3_button, rpcs3_dpad,
                              rpcs3_token_label)
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
# Analog sticks — captured per-direction as an axis (kind="axis"); RPCS3 stores each direction
# key as an `LS/RS X/Y ±` token. Push the stick in the direction the row names.
_STICKS = [
    ("Left Stick Up", "L-stick Up"), ("Left Stick Down", "L-stick Down"),
    ("Left Stick Left", "L-stick Left"), ("Left Stick Right", "L-stick Right"),
    ("Right Stick Up", "R-stick Up"), ("Right Stick Down", "R-stick Down"),
    ("Right Stick Left", "R-stick Left"), ("Right Stick Right", "R-stick Right"),
]
_STICK_KEYS = {k for k, _ in _STICKS}
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


@method("rpcs3.input_get", slow=True)   # buffered: NO cache=("config",) — the in-memory buffer is truth
def _input_get(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot read RPCS3 input config")
    data = _resting()
    count = _player_count(data)
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, count + 1)]
    player = _resolve_player(params, count)
    ovp = _buf.get(_CTX).get(player, {})     # buffer-over-disk: reflects staged, unsaved edits
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
        {"title": "Analog sticks",
         "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
    ]
    note = (f"Remaps Player {player} — applied when you launch a PS3 game from ES-DE "
            "(your normal RPCS3 controller setup is left untouched). "
            "Player → controller is set on the Controllers page.")
    return {"running": False, "note": note, "groups": groups,
            "players": players, "player": str(player),
            "buffered": True, "dirty": _buf.dirty}


@method("rpcs3.input_set", slow=True)
def _input_set(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot save RPCS3 input overrides")
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    # Resolve/validate the target player up front (needs the resting player count), then STAGE
    # the edit — _apply computes + validates the token and raises EINVAL on a bad capture. No
    # disk write here; the override reaches the sidecar only on rpcs3.input_save.
    count = _player_count(_resting())
    player = _resolve_player(params, count)
    edit = {"player": player, "id": key, "kind": kind,
            "value": str(params.get("value", ""))}
    _buf.set(_CTX, edit)
    disp = rpcs3_token_label(_buf.working.get(player, {}).get(key))
    return {"id": key, "value": disp, "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} → {disp}"}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). Edits stage in the module-level
# InputBuffer and only reach the MAD override sidecar on rpcs3.input_save;
# rpcs3.input_cancel drops them. ctx = () because the overrides sidecar is a single
# global file; the whole-file working copy (a per-player dict) spans every player, so
# the Player stepper is a pure render filter (see input_buffer). Deliberately NO
# running-guard: RPCS3 can be edited while running (takes effect next launch).
# ---------------------------------------------------------------------------
_CTX: tuple = ()


def _token_for(key: str, kind: str, value: str) -> str:
    """The RPCS3 source token for one captured input, with the full per-kind validation.
    Pure (no I/O, no bump); raises RpcError('EINVAL', ...) on an unmappable capture."""
    if key in _DPAD_KEYS and kind == "hat":
        token = rpcs3_dpad(value)
        if token is None:
            raise RpcError("EINVAL", "press a d-pad direction")
        return token
    if key in _STICK_KEYS and kind == "axis":
        parsed = parse_axis_token(value)
        if parsed is None:
            raise RpcError("EINVAL", "push the stick in that direction")
        token = rpcs3_axis_source(*parsed)
        if token is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
        return token
    if key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(value)
        except (ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        token = rpcs3_button(code)
        if token is None:
            raise RpcError("EINVAL", "that input can't be mapped — press a face, shoulder, "
                                     "trigger, stick-click, Select, Start or PS button")
        return token
    raise RpcError("EINVAL", f"{key!r} is not a remappable RPCS3 input")


def _apply(overrides: dict, edit: dict) -> dict:
    """Apply one staged edit to the overrides dict, returning it. Pure (no disk write, no
    bump). Replayed verbatim by the buffer's flush onto a FRESH sidecar read, so a foreign
    override to a different player/key survives."""
    player = edit["player"]
    key = edit["id"]
    token = _token_for(key, edit["kind"], str(edit.get("value", "")))
    overrides.setdefault(player, {})[key] = token
    return overrides


def _load(ctx: tuple) -> dict:
    return rpcs3_cfg.load_overrides()


def _apply_edit(overrides: dict, edit: dict):
    return _apply(overrides, edit), edit


def _flush(ctx: tuple, disk: dict, edits: list) -> dict:
    overrides = rpcs3_cfg.load_overrides()       # replay onto FRESH sidecar
    for edit in edits:
        overrides = _apply(overrides, edit)
    rpcs3_cfg.save_overrides(overrides)
    return overrides


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("rpcs3.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_CTX), "dirty": _buf.dirty}


@method("rpcs3.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_CTX)
    return {"cancelled": True, "dirty": _buf.dirty}
