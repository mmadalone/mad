"""pcsx2x6.input_* — per-button input mapping for pcsx2x6 (Namco System 246/256 fork).

Identical to pcsx2_input_cmds, but pcsx2x6 runs `-portable` so its `[PadN]` button
bindings live in the portable ini `~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini`
(NOT ~/.config/PCSX2), and the running-check matches the pcsx2x6 AppImage (its inner
binary is `pcsx2-qt`, shared with regular PCSX2, so match the path via `pgrep -f`).

Reads/writes `[PadN]` for the player the page's picker selects (Player i → [PadN] via
pcsx2_cfg._slot_plan order). Each PS2 action is `Action = SDL-<idx>/<source>`; the
launch binder (switch_bind → pcsx2_cfg.assign_devices) abstracts `<idx>` per launch
AND preserves each slot's OWN sources (lib/pcsx2_cfg._slot_template), so a per-button
remap — changing the `<source>` — PERSISTS across launches. We edit only the SOURCE and
keep whatever SDL index that [PadN] already uses. (pcsx2x6's guncon2 lightgun binds live
in [USB1]/[USB2], a separate region this page never touches.)

pcsx2x6 rewrites its ini on EXIT, so editing while it's running would be clobbered —
input_get flags it and input_set refuses.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import inifile, pcsx2_cfg, proc_guard
from . import cfgutil
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .rpc import RpcError, method

_INI = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
# [PadN] slot numbers in PLAYER order (port-1 group, then port-2), taken from the
# router's own pad↔port mapping so the picker's "Player i" lines up with how the
# launch wrapper assigns pads (pcsx2_cfg._slot_plan; see deck-docs/pcsx2-ini-encodings.md).
# = (1, 3, 4, 5, 2, 6, 7, 8). A standalone-launched config has DualShock2 on exactly
# the slots for its player count, so enumerating configured slots in this order
# reconstructs the player sequence the binder wrote.
_PAD_ORDER = tuple(pcsx2_cfg._slot_plan(8)[0])

# (key in [Pad1], label) — the remappable digital buttons.
_BUTTONS = [
    ("Cross", "Cross  ✕"), ("Circle", "Circle  ○"),
    ("Triangle", "Triangle  △"), ("Square", "Square  ▢"),
    ("L1", "L1"), ("R1", "R1"), ("L2", "L2"), ("R2", "R2"),
    ("L3", "L3"), ("R3", "R3"), ("Select", "Select"), ("Start", "Start"),
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
    # pcsx2x6's inner binary is `pcsx2-qt` (shared with regular PCSX2), so match the
    # AppImage/mount path via `pgrep -f` (exact=False), same as proc_guard's pcsx2x6
    # entry — an exact `pcsx2-qt` match couldn't tell the two builds apart.
    return proc_guard.process_running("pcsx2x6", exact=False)


def _source_of(text: str, section: str, key: str) -> str:
    """The bound SDL source for a [PadN] key, e.g. 'SDL-2/FaceWest' → 'FaceWest'."""
    v = cfgutil.ini_read(text, section, key)
    if not v:
        return ""
    m = re.match(r"SDL-\d+/(.+)", v.strip())
    return m.group(1) if m else v.strip()


def _cur_index(text: str, section: str) -> int:
    """The SDL index [PadN] currently binds to (the binder re-points it each
    launch); reuse it so the immediate state stays consistent. Default 0."""
    v = cfgutil.ini_read(text, section, "Cross") or ""
    m = re.search(r"SDL-(\d+)/", v)
    return int(m.group(1)) if m else 0


def _sdl_configured(text: str, section: str) -> bool:
    """True once [section] holds a real SDL-bound DualShock2 block (what the launch
    binder writes). The portable ini ships [Pad1] as Type=DualShock2 but KEYBOARD-
    bound (no SDL sources) and missing some button keys, so per-button remapping is
    meaningless until a game has launched once and bound a pad. We gate the page on
    this so a keyboard [Pad1] shows a uniform 'launch once' note instead of a
    half-editable mix of SDL + keyboard rows."""
    return "SDL-" in (inifile.section_body(text, section) or "")


def _player_sections(text: str) -> list[str]:
    """Configured pcsx2x6 pad sections in player order — every [PadN] whose
    ``Type = DualShock2``, walked in `_PAD_ORDER` so player 1,2,3… matches the
    launch wrapper's pad assignment (incl. multitap). Always ≥1 entry ([Pad1]) so
    the page still renders before any pad is configured.

    LIMITATION: a remap is stored in a PHYSICAL [PadN] slot, and which slot a player
    maps to depends on the connected-pad count via pcsx2_cfg._slot_plan. The mapping is
    stable for 1–2 players (Pad1/Pad2) and for 3–4 (Pad1/Pad3/Pad4/Pad5), but the 2↔3
    boundary shifts Player 2 from Pad2 to Pad3 — so a Player-2+ remap made with N pads
    may not follow that player if a DIFFERENT pad count binds at launch. Stable rigs are
    unaffected; the robust-but-heavier fix would key remaps by player number, not slot."""
    pads = [f"Pad{n}" for n in _PAD_ORDER
            if (cfgutil.ini_read(text, f"Pad{n}", "Type") or "").strip() == "DualShock2"]
    return pads or ["Pad1"]


def _resolve_player(params, sections: list[str]) -> tuple[str, str]:
    """(player id "1".."K", section "PadN") from the C++ player param ('' = first)."""
    try:
        i = int(params.get("player") or "1")
    except (TypeError, ValueError):
        i = 1
    i = max(1, min(i, len(sections)))
    return str(i), sections[i - 1]


def _configured_pad(text: str, section: str) -> str:
    """Best-effort friendly name of the pad [PadN] is bound to: the connected SDL
    device at [PadN]'s index (the binder re-points this per launch), via KNOWN_PADS.
    '' if that index isn't a currently-connected known pad."""
    from ..devices import sdl_devices
    from ..mad_config import pad_name
    idx = _cur_index(text, section)
    for d in sdl_devices(pump=True):  # input_get is slow=True → afford the warm wait
        if d.index == idx:
            return pad_name(d.vidpid)
    return ""


@method("pcsx2x6.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _INI.is_file():
        raise RpcError("ENOENT", f"pcsx2x6 config not found at {_INI}")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    run = _running()
    sections = _player_sections(text)
    players = [{"id": str(i + 1), "label": f"Player {i + 1}"} for i in range(len(sections))]
    player, section = _resolve_player(params, sections)
    configured = _sdl_configured(text, section)

    def row(key, label, kind, capturable):
        src = _source_of(text, section, key)
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—",
                "capturable": capturable and not run and configured}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn", True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
    ]
    if run:
        note = ("Close pcsx2x6 first, it rewrites this file on exit and would discard "
                "changes made while it's open.")
    elif not configured:
        note = ("Connect a controller and launch a Namco 246/256 game once; the pad is "
                "bound at launch, after which you can remap its buttons here. (Pick which "
                "pad on the Controllers page.)")
    else:
        cname = _configured_pad(text, section)
        note = f"Controller: {cname}." if cname else ""
    return {"running": run, "note": note, "groups": groups,
            "players": players, "player": player}


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
                           "that input can't be mapped — press a face, shoulder, "
                           "trigger, stick-click, Select or Start button")
    else:
        raise RpcError("EINVAL", f"{key!r} is not a remappable pcsx2x6 input")
    if not _INI.is_file():
        raise RpcError("ENOENT", f"pcsx2x6 config not found at {_INI}")
    if _running():
        raise RpcError("EBUSY", "close pcsx2x6 first — it rewrites its config on exit")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    sections = _player_sections(text)
    player, section = _resolve_player(params, sections)
    if not _sdl_configured(text, section):
        # Pre-launch [Pad1] is keyboard-bound (no SDL sources, some button keys absent);
        # refuse uniformly rather than half-write SDL over the keyboard defaults.
        raise RpcError("EINVAL", "Launch a Namco 246/256 game once with a controller "
                       "connected before remapping. The pad's buttons are set up at "
                       "launch, then editable here.")
    idx = _cur_index(text, section)
    new = cfgutil.ini_replace(text, section, key, f"SDL-{idx}/{source}")
    if new is None:
        raise RpcError("EINVAL", f"Player {player}: couldn't write {key} (its slot may "
                       "not be configured for this pad count). Launch a game once first.")
    cfgutil.ensure_bak(_INI)
    cfgutil.atomic_write(_INI, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}"}
