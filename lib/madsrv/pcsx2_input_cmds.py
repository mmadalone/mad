"""pcsx2.input_* — per-button input mapping for PCSX2 (Phase-0 reference emu).

Reads/writes the `[Pad1]` button bindings in ~/.config/PCSX2/inis/PCSX2.ini.
Each PS2 action is `Action = SDL-<idx>/<source>`; the controller-router
abstracts `<idx>` at launch (lib/pcsx2_cfg._bind_template clones the live [Pad1]
button layout, swapping only the SDL index), so a per-button remap — changing
the `<source>` — PERSISTS across launches. We therefore edit only the SOURCE and
keep whatever SDL index [Pad1] already uses.

v1 maps the digital buttons (face / shoulders / triggers' digital click / L3·R3
/ Select·Start), which map cleanly onto SDL GameController source names via
input_translate. The d-pad (a hat) and analog sticks are shown read-only until a
later pass adds hat capture + SDL-axis correlation.

PCSX2 rewrites its ini on EXIT, so editing while pcsx2-qt is running would be
clobbered — input_get flags it and input_set refuses.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .input_translate import (parse_axis_token, pcsx2_axis_source, pcsx2_dpad_source,
                              sdl_button_source, sdl_source_label)
from .rpc import RpcError, method

_INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()
_SECTION = "Pad1"

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
    # exact=True → `pgrep -x pcsx2-qt` (process NAME match), like
    # systems_cmds._retroarch_running. The loose default (`pgrep -f`) matched any
    # command line CONTAINING "pcsx2-qt" — a false positive that wrongly marked
    # PCSX2 "running" and suppressed every button chip.
    return proc_guard.process_running("pcsx2-qt", exact=True)


def _source_of(text: str, key: str) -> str:
    """The bound SDL source for a [Pad1] key, e.g. 'SDL-2/FaceWest' → 'FaceWest'."""
    v = cfgutil.ini_read(text, _SECTION, key)
    if not v:
        return ""
    m = re.match(r"SDL-\d+/(.+)", v.strip())
    return m.group(1) if m else v.strip()


def _cur_index(text: str) -> int:
    """The SDL index [Pad1] currently binds to (the router re-points it each
    launch); reuse it so the immediate state stays consistent. Default 0."""
    v = cfgutil.ini_read(text, _SECTION, "Cross") or ""
    m = re.search(r"SDL-(\d+)/", v)
    return int(m.group(1)) if m else 0


def _configured_pad(text: str) -> str:
    """Best-effort friendly name of the pad [Pad1] is bound to: the connected SDL
    device at [Pad1]'s index (the router re-points this per launch), via KNOWN_PADS.
    '' if that index isn't a currently-connected known pad."""
    from ..devices import sdl_devices
    from ..mad_config import pad_name
    idx = _cur_index(text)
    for d in sdl_devices(pump=True):  # pcsx2.input_get is slow=True → afford the warm wait
        if d.index == idx:
            return pad_name(d.vidpid)
    return ""


@method("pcsx2.input_get", slow=True, cache=("config",))
def _input_get(params):
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    run = _running()

    def row(key, label, kind, capturable):
        src = _source_of(text, key)
        return {"id": key, "label": label, "kind": kind,
                "value": sdl_source_label(src) if src else "—",
                "capturable": capturable and not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn", True) for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
        {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
    ]
    if run:
        note = ("Close PCSX2 first — it rewrites this file on exit and would discard "
                "changes made while it's open.")
    else:
        cname = _configured_pad(text)
        note = f"Controller: {cname}." if cname else ""
    return {"running": run, "note": note, "groups": groups}


@method("pcsx2.input_set", slow=True)
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
        raise RpcError("EINVAL", f"{key!r} is not a remappable PCSX2 input")
    if not _INI.is_file():
        raise RpcError("ENOENT", f"PCSX2 config not found at {_INI}")
    if _running():
        raise RpcError("EBUSY", "close PCSX2 first — it rewrites its config on exit")
    text = _INI.read_text(encoding="utf-8", errors="replace")
    idx = _cur_index(text)
    new = cfgutil.ini_replace(text, _SECTION, key, f"SDL-{idx}/{source}")
    if new is None:
        raise RpcError("EINTERNAL", f"no '{key}' line in [{_SECTION}] to update")
    cfgutil.ensure_bak(_INI)
    cfgutil.atomic_write(_INI, new)
    from .. import staterev
    staterev.bump("config")
    return {"id": key, "value": sdl_source_label(source),
            "message": f"{key} → {sdl_source_label(source)}"}
