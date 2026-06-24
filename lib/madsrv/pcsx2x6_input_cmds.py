"""pcsx2x6.input_* — per-button input mapping for pcsx2x6 (Namco System 246/256 fork).

Remaps are stored in a per-PLAYER override store (pcsx2_cfg.load/save_input_overrides,
a JSON sidecar beside the portable ini), NOT in a physical [PadN] slot. The launch binder
(switch_bind -> pcsx2_cfg.assign_devices(overrides=...)) re-applies each player's sources
to whatever slot that player lands in, so a remap follows the player across any pad count
and survives pcsx2x6's non-transient single-pad launches. (Same design as the RPCS3 input
backend.) The page never touches [PadN], [USB1/2] (guncon2) or [JVS].

pcsx2x6 is a fixed 2-player system (Pad1/Pad2, no multitap), so the page always offers
Player 1 and Player 2. Buttons + d-pad + sticks are all remappable.

pcsx2x6 rewrites its ini on EXIT, so input_set refuses while it's running (cache safety).
"""
from __future__ import annotations

from pathlib import Path

from .. import pcsx2_cfg, proc_guard
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .rpc import RpcError, method

_INI = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
_PLAYERS = 2                       # Namco System 246/256 is a fixed 2-player system
_SLOT_SECTIONS = ["Pad1", "Pad2"]  # player i+1 -> slot (for the one-time migration)

# (key in [PadN], label) — the remappable digital buttons.
_BUTTONS = [
    ("Cross", "Cross  ✕"), ("Circle", "Circle  ○"),
    ("Triangle", "Triangle  △"), ("Square", "Square  ▢"),
    ("L1", "L1"), ("R1", "R1"), ("L2", "L2"), ("R2", "R2"),
    ("L3", "L3"), ("R3", "R3"), ("Select", "Select"), ("Start", "Start"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
_DPAD = [
    ("Up", "D-pad Up"), ("Down", "D-pad Down"),
    ("Left", "D-pad Left"), ("Right", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICKS = [
    ("LUp", "L-stick Up"), ("LDown", "L-stick Down"),
    ("LLeft", "L-stick Left"), ("LRight", "L-stick Right"),
    ("RUp", "R-stick Up"), ("RDown", "R-stick Down"),
    ("RLeft", "R-stick Left"), ("RRight", "R-stick Right"),
]
_STICK_KEYS = {k for k, _ in _STICKS}


def _running() -> bool:
    # pcsx2x6's inner binary is `pcsx2-qt` (shared with regular PCSX2), so match the
    # AppImage path via `pgrep -f` (exact=False) to tell the two builds apart.
    return proc_guard.process_running("pcsx2x6", exact=False)


def _player(params) -> int:
    try:
        i = int(params.get("player") or "1")
    except (TypeError, ValueError):
        i = 1
    return max(1, min(i, _PLAYERS))


@method("pcsx2x6.input_get", slow=True, cache=("config",))
def _input_get(params):
    run = _running()
    # Migrate any legacy [PadN] SDL remaps into the store on first read (no-op for
    # pcsx2x6 — its [Pad1] ships keyboard-bound, [Pad2] is Type=None).
    ovr = pcsx2_cfg.migrate_overrides_from_ini(_INI, _SLOT_SECTIONS)
    defaults = pcsx2_cfg.baked_default_sources()
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, _PLAYERS + 1)]
    player = _player(params)
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
    note = ("Close pcsx2x6 first, it rewrites this file on exit and would discard "
            "changes made while it's open." if run else
            "Remaps Player " + str(player) + "; applied at launch to whichever pad the "
            "Controllers page assigns to this player.")
    return {"running": run, "note": note, "groups": groups,
            "players": players, "player": str(player)}


@method("pcsx2x6.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
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
                           "that input can't be mapped; press a face, shoulder, "
                           "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable pcsx2x6 input")
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first; it rewrites its config on exit")
    player = _player(params)
    ovr = pcsx2_cfg.load_input_overrides(_INI)
    ovr.setdefault(player, {})[key] = source
    pcsx2_cfg.save_input_overrides(_INI, ovr)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}"}
