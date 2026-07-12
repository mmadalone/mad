"""pcsx2.input_* — per-button input mapping for PCSX2 (Phase-0 reference emu).

Reads/writes the `[PadN]` button bindings in ~/.config/PCSX2/inis/PCSX2.ini for the
player the page's picker selects (Player i → [PadN] via pcsx2_cfg._slot_plan order).
Each PS2 action is `Action = SDL-<idx>/<source>`; the launch binder abstracts `<idx>`
per launch AND now preserves each slot's OWN sources (lib/pcsx2_cfg._slot_template),
so a per-button remap — changing the `<source>` — PERSISTS across launches for EVERY
player, not just Player 1. We therefore edit only the SOURCE and keep whatever SDL
index that [PadN] already uses.

v1 maps the digital buttons (face / shoulders / triggers' digital click / L3·R3
/ Select·Start), which map cleanly onto SDL GameController source names via
input_translate. The d-pad (a hat) and analog sticks are shown read-only until a
later pass adds hat capture + SDL-axis correlation.

PCSX2 rewrites its ini on EXIT, so editing while pcsx2-qt is running would be
clobbered — input_get flags it and input_set refuses.
"""
from __future__ import annotations

from pathlib import Path

from .. import handheld_input, pcsx2_cfg, proc_guard
from . import cfgutil
from .input_buffer import InputBuffer
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .rpc import RpcError, method

_INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()
# [PadN] slot numbers in PLAYER order (port-1 group, then port-2), taken from the
# router's own pad↔port mapping so the picker's "Player i" lines up with how the
# launch wrapper assigns pads (pcsx2_cfg._slot_plan; see deck-docs/pcsx2-ini-encodings.md).
# = (1, 3, 4, 5, 2, 6, 7, 8). A standalone-launched config has DualShock2 on exactly
# the slots for its player count, so enumerating configured slots in this order
# reconstructs the player sequence the router wrote.
_PAD_ORDER = tuple(pcsx2_cfg._slot_plan(8)[0])

# (key in [Pad1], label) — the remappable digital buttons.
_BUTTONS = [
    ("Cross", "Cross  ✕"), ("Circle", "Circle  ○"),
    ("Triangle", "Triangle  △"), ("Square", "Square  ▢"),
    ("L1", "L1"), ("R1", "R1"), ("L2", "L2"), ("R2", "R2"),
    ("L3", "L stick press"), ("R3", "R stick press"), ("Select", "Select"), ("Start", "Start"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# D-pad directions — captured as a hat (kind="hat"); stored as the SDL source
# "DPad<Dir>" in [Pad1] (e.g. Up = SDL-2/DPadUp).
_DPAD = [
    ("Up", "D-pad Up"), ("Down", "D-pad Down"),
    ("Left", "D-pad Left"), ("Right", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
# Analog sticks — captured per-direction as an axis (kind="axis"); stored as the
# SDL source "±LeftX"/"±LeftY"/… (the sign encodes the direction, so inversion is
# automatic). Push the stick in the direction the row names.
_STICKS = [
    ("LUp", "L-stick Up"), ("LDown", "L-stick Down"),
    ("LLeft", "L-stick Left"), ("LRight", "L-stick Right"),
    ("RUp", "R-stick Up"), ("RDown", "R-stick Down"),
    ("RLeft", "R-stick Left"), ("RRight", "R-stick Right"),
]
_STICK_KEYS = {k for k, _ in _STICKS}


def _running() -> bool:
    # exact=True → `pgrep -x pcsx2-qt` (process NAME match), like
    # systems_cmds._retroarch_running. The loose default (`pgrep -f`) matched any
    # command line CONTAINING "pcsx2-qt" — a false positive that wrongly marked
    # PCSX2 "running" and suppressed every button chip.
    return proc_guard.process_running("pcsx2-qt", exact=True)


def _player_sections(text: str) -> list[str]:
    """Configured PCSX2 pad sections in player order — every [PadN] whose
    ``Type = DualShock2``, walked in `_PAD_ORDER`. Used for the player COUNT shown on the
    page and the one-time migration order (slot i -> player i+1). Always ≥1 entry ([Pad1]).
    Remaps themselves are no longer stored here: they live in the per-PLAYER override store
    (pcsx2_cfg.load/save_input_overrides) and follow the player across any pad count."""
    pads = [f"Pad{n}" for n in _PAD_ORDER
            if (cfgutil.ini_read(text, f"Pad{n}", "Type") or "").strip() == "DualShock2"]
    return pads or ["Pad1"]


def _player(params, count: int) -> int:
    try:
        i = int(params.get("player") or "1")
    except (TypeError, ValueError):
        i = 1
    return max(1, min(i, count))


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). Edits stage in the module-level
# InputBuffer (the WORKING copy is the per-player override store, {player: {key: source}})
# and only reach the .mad-input-overrides.json sidecar on pcsx2.input_save;
# pcsx2.input_cancel drops them. The buffer ctx = (context,) — "docked" | "handheld", the
# slice of the context-keyed store this page edits (the door decides it; params["context"]).
# The working copy spans every player, so the Player picker is a pure render filter.
# ---------------------------------------------------------------------------
def _ctx(params) -> tuple:
    """Buffer identity = the docked/handheld slice this page targets (from params["context"],
    default docked). Switching context reloads a separate working copy."""
    return (handheld_input.normalize(params.get("context", "docked")),)


def _compute_source(key: str, kind: str, params) -> str:
    """Validate one capture and return its SDL `<source>` string (pure). Raises on an
    unmappable input, preserving every original message. Shared by stage (_apply) and the
    save-time replay so validation fires at BOTH stage and save."""
    if key in _DPAD_KEYS and kind == "hat":
        source = pcsx2_dpad_source(str(params.get("value", "")))
        if source is None:
            raise RpcError("EINVAL", "press a d-pad direction")
    elif key in _STICK_KEYS and kind == "axis":
        parsed = parse_axis_token(str(params.get("value", "")))
        if parsed is None:
            raise RpcError("EINVAL", "push the stick in that direction")
        source = pcsx2_axis_source(*parsed)
        if source is None:
            raise RpcError("EINVAL", "that axis can't be mapped")
    elif key in _BUTTON_KEYS and kind == "btn":
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        source = sdl_button_source(code)
        if source is None:
            raise RpcError("EINVAL",
                           "that input can't be mapped — press a face, shoulder, "
                           "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    return source


def _apply(working: dict, edit: dict) -> dict:
    """Apply one staged edit to the per-player override store `working` ({int player: {key:
    source}}), in memory only. Pure: no disk I/O, no staterev bump. Replayed verbatim onto a
    FRESH disk read by _flush, so a foreign remap to other players/keys survives. Refuses while
    PCSX2 runs (it rewrites its config on exit) — the EBUSY guard fires at stage AND save."""
    if _running():
        raise RpcError("EBUSY", "close PCSX2 first; it rewrites its config on exit")
    player, key = edit["player"], edit["id"]
    if edit.get("op") == "clear":
        # Mirror pcsx2_cfg.clear_input_override: WRITE the baked default (so a reset actually
        # takes effect at launch even over a non-baked GUI binding), else delete the entry.
        baked = pcsx2_cfg.baked_default_sources().get(key)
        if baked is not None:
            working.setdefault(player, {})[key] = baked
        elif player in working and key in working[player]:
            del working[player][key]
            if not working[player]:
                del working[player]
        return working
    working.setdefault(player, {})[key] = _compute_source(key, edit["kind"], edit)  # op == "set"
    return working


def _load(ctx: tuple) -> dict:
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    context = ctx[0] if ctx else "docked"
    sections = _player_sections(_INI.read_text(encoding="utf-8", errors="replace"))
    # One-time: seed the store from any pre-existing [PadN] SDL remaps (docked only; the ini is the
    # docked config). This side-effect ran on the page's first read before buffering, so it stays
    # here (in the buffer's load path). Handheld loads its own — possibly empty — slice.
    return pcsx2_cfg.migrate_overrides_from_ini(_INI, sections, context)


def _apply_edit(working: dict, edit: dict):
    return _apply(working, edit), edit


def _flush(ctx: tuple, disk: dict, edits: list) -> dict:
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    context = ctx[0] if ctx else "docked"
    sections = _player_sections(_INI.read_text(encoding="utf-8", errors="replace"))
    fresh = pcsx2_cfg.migrate_overrides_from_ini(_INI, sections, context)   # replay onto FRESH disk
    for edit in edits:
        fresh = _apply(fresh, edit)
    pcsx2_cfg.save_input_overrides(_INI, fresh, context)           # single write; buffer bumps once
    return fresh


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("pcsx2.input_get", slow=True)   # buffered: NO cache=("config",) — the in-memory buffer is truth
def _input_get(params):
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    run = _running()
    sections = _player_sections(text)          # configured slots, in player order
    ovr = _buf.get(_ctx(params))                        # buffer-over-disk: reflects staged edits + runs migration
    defaults = pcsx2_cfg.baked_default_sources()
    count = len(sections)
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, count + 1)]
    player = _player(params, count)
    pov = ovr.get(player, {})

    def row(key, label, kind):
        src = pov.get(key) or defaults.get(key, "")
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—",
                "capturable": not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis") for k, l in _STICKS]},
    ]
    note = ("Close PCSX2 first, it rewrites this file on exit and would discard changes "
            "made while it's open." if run else
            f"Remaps Player {player}; applied at launch to whichever pad the Controllers "
            "page assigns to this player.")
    return {"running": run, "note": note, "groups": groups, "clearable": True,
            "players": players, "player": str(player),
            "buffered": True, "dirty": _buf.dirty}


@method("pcsx2.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    count = len(_player_sections(_INI.read_text(encoding="utf-8", errors="replace")))
    player = _player(params, count)
    edit = {"op": "set", "player": player, "id": key, "kind": kind,
            "value": str(params.get("value", ""))}
    _buf.set(_ctx(params), edit)          # stage in memory (validated by _apply, incl. EBUSY); no disk write
    source = _buf.working.get(player, {}).get(key, "")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}", "dirty": _buf.dirty}


@method("pcsx2.input_clear", slow=True)
def _input_clear(params):
    """Unbind one button — the page's "focus a row, press Start" clear. Stages a reset of the
    per-player override to the baked DualShock2 default; committed on Save."""
    key = params.get("id") or params.get("key") or ""
    if key not in _BUTTON_KEYS and key not in _DPAD_KEYS and key not in _STICK_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    count = len(_player_sections(_INI.read_text(encoding="utf-8", errors="replace")))
    player = _player(params, count)
    _buf.set(_ctx(params), {"op": "clear", "player": player, "id": key})  # stage (EBUSY via _apply); no disk write
    default = pcsx2_cfg.baked_default_sources().get(key, "")
    return {"id": key, "value": sdl_source_label(default) if default else "—",
            "message": f"{key} reset to default", "dirty": _buf.dirty}


@method("pcsx2.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_ctx(params)), "dirty": _buf.dirty}


@method("pcsx2.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_ctx(params))
    return {"cancelled": True, "dirty": _buf.dirty}
