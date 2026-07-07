"""Shared Yuzu-fork (Eden + Citron) per-button input page.

Eden and Citron store identical Switch bindings ([Controls] of qt-config.ini, each
`player_N_button_<x>="engine:sdl,port:N,guid:G,button:M"`), so this is the ONE copy of
their per-button map. `eden_input_cmds` / `citron_input_cmds` are thin shims that
instantiate `YuzuInputPage` and register the `<emu>.input_*` RPC methods. Previously the
two were hand-mirrored byte-clones; this module retires that "mirror every edit" hazard.

Device-exact numbering (fixes C1/C2)
------------------------------------
`button:M` is a RAW per-device SDL joystick index that DIFFERS by pad: L-stick click is
`button:7` on a DualSense, `button:9` on the Steam Deck, `button:11` on a Wii U Pro. The
old page guessed one of two static tables ("gc" if the pad's template L3==button:7, else
"raw") and captured/labelled from that guess -- wrong for a GameController pad with no
matching template (mislabels + corrupts: C1) and for the Deck, whose real layout matches
neither table (C2).

The fix reads the exact index straight from the pad's own clean device profile
(`~/.config/<emu>/input/*.ini`), the same source `eden_cfg.dpad_index` already uses for the
d-pad -- so capture and display are per-device correct. The static gc/raw tables remain only
as a fallback when a captured func is absent from the template (or no template matches at
all); in that fallback a consensus across the pad's live shoulder/stick-click bindings recognises
an untemplated GameController pad as "gc" (C1) -- robustly, so remapping any single row cannot flip
the pad's scheme. This mirrors how RetroArch already reads a pad's autoconfig instead of guessing.

Switch is `router_skip = true`, so the controller-router never rewrites this at launch (no
clobber). The emulator DOES rewrite qt-config.ini on exit, so we refuse edits while it runs.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .input_buffer import InputBuffer
from .input_translate import (axis_invert, axis_token_rank, canonical_is_trigger,
                              eden_hat_button_index, hat_token_parts, parse_axis_token,
                              sdl_button_index, sdl_index_label,
                              xemu_axis_index, xemu_axis_label,
                              xemu_button_index, xemu_index_label)
from .rpc import RpcError

_SECTION = "Controls"
_SYSTEM_SECTION = "System"   # use_docked_mode lives here, not in [Controls]

# Eden/yuzu Settings::ControllerType -- player_N_type is the enum's integer index.
_CTYPES = [("0", "Pro Controller"), ("1", "Dual Joycons"), ("2", "Left Joycon"),
           ("3", "Right Joycon"), ("4", "Handheld"), ("5", "GameCube")]
_CTYPE_VALUES = {v for v, _ in _CTYPES}
# Console docked mode (global): use_docked_mode 1=docked, 0=handheld.
_CONSOLE = [("1", "Docked"), ("0", "Handheld")]
_CONSOLE_VALUES = {v for v, _ in _CONSOLE}
_PLAYERS = [{"id": f"player_{n}", "label": f"Player {n + 1}"} for n in range(8)]
_PLAYER_IDS = {p["id"] for p in _PLAYERS}
_DEFAULT_PLAYER = "player_0"

# (Switch-button key suffix, label) -- the remappable digital buttons.
_BUTTONS = [
    ("button_a", "A"), ("button_b", "B"), ("button_x", "X"), ("button_y", "Y"),
    ("button_l", "L"), ("button_r", "R"), ("button_zl", "ZL"), ("button_zr", "ZR"),
    ("button_minus", "Minus −"), ("button_plus", "Plus +"),
    ("button_lstick", "L-stick click"), ("button_rstick", "R-stick click"),
    ("button_home", "Home"),
]
_BUTTON_KEYS = {k for k, _ in _BUTTONS}
# ZL/ZR are analog triggers on most pads (DS/DS4/Deck report axis); on the Wii U Pro adapter
# they are plain buttons. The capture KIND is chosen per device (see _btn_kind).
_TRIGGER_KEYS = {"button_zl", "button_zr"}
_DPAD = [
    ("button_dup", "D-pad Up"), ("button_ddown", "D-pad Down"),
    ("button_dleft", "D-pad Left"), ("button_dright", "D-pad Right"),
]
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICKS = [
    ("lstick_x", "L-stick X — push right"), ("lstick_y", "L-stick Y — push down"),
    ("rstick_x", "R-stick X — push right"), ("rstick_y", "R-stick Y — push down"),
]
_STICK_KEYS = {k for k, _ in _STICKS}

_BTN_RE = re.compile(r"button:(\d+)")
_GUID_RE = re.compile(r"guid:([0-9A-Fa-f]+)")


# ---------------------------------------------------------------------------
# Device-exact numbering: read the pressed physical button's index straight from
# the pad's own clean device template, inverting it for display.
# ---------------------------------------------------------------------------
# A captured evdev button code identifies WHICH physical button was pressed. Map it to the
# Switch func whose NATURAL physical button that is (Switch A = physical East, B = South, ...),
# then read that func's index from the device template = the pad's own index for that button.
# Verified against the DS / DS4 / Wii U Pro / Handheld templates on this rig.
_EVDEV_TO_FUNC = {
    0x131: "button_a",       # BTN_EAST   -> Switch A (right face)
    0x130: "button_b",       # BTN_SOUTH  -> Switch B (bottom face)
    0x133: "button_x",       # BTN_NORTH  -> Switch X (top face)
    0x134: "button_y",       # BTN_WEST   -> Switch Y (left face)
    0x136: "button_l",       # BTN_TL     -> L
    0x137: "button_r",       # BTN_TR     -> R
    0x138: "button_zl",      # BTN_TL2    -> ZL (digital, button-style pads)
    0x139: "button_zr",      # BTN_TR2    -> ZR
    0x13A: "button_minus",   # BTN_SELECT -> Minus
    0x13B: "button_plus",    # BTN_START  -> Plus
    0x13C: "button_home",    # BTN_MODE   -> Home
    0x13D: "button_lstick",  # BTN_THUMBL -> L3
    0x13E: "button_rstick",  # BTN_THUMBR -> R3
}
# Friendly physical-button label per func, for inverting a stored index back to a label.
_FUNC_LABEL = {
    "button_a": "A", "button_b": "B", "button_x": "X", "button_y": "Y",
    "button_l": "L", "button_r": "R", "button_zl": "ZL", "button_zr": "ZR",
    "button_minus": "Minus", "button_plus": "Plus", "button_home": "Home",
    "button_lstick": "L3", "button_rstick": "R3",
}


def _btn_idx(value: str):
    """The int in `button:N` of a binding string, else None."""
    m = _BTN_RE.search(value or "")
    return int(m.group(1)) if m else None


def _guid_of(value: str) -> str:
    m = _GUID_RE.search(value or "")
    return m.group(1) if m else ""


def _template(input_dir, guid: str) -> dict:
    """The pad's clean device template block (exact-guid then vid:pid), or {}."""
    from .. import eden_cfg   # lazy (eden_cfg pulls in SDL); matches the shims' lazy style
    return eden_cfg._match_template(input_dir, guid)


def scheme(input_dir, guid: str) -> str:
    """'gc' (SDL GameController numbering: L3=button:7) or 'raw' (SDL joystick rank: L3=button:11)
    for the device a binding points at, from its CLEAN template (matched by vid:pid). 'raw' when
    no template matches. Only a FALLBACK now -- capture/label read the template directly."""
    if not guid:
        return "raw"
    return "gc" if _btn_idx(_template(input_dir, guid).get("button_lstick", "")) == 7 else "raw"


# Anchors that distinguish GameController numbering from raw joystick rank, per func:
# (func, gc_index, raw_index). Used ONLY to classify an untemplated pad -- by consensus across
# whatever it has bound, so remapping any single row cannot flip the whole pad's scheme.
_SCHEME_ANCHORS = [
    ("button_lstick", 7, 11), ("button_rstick", 8, 12),
    ("button_l", 9, 4), ("button_r", 10, 5),
    ("button_minus", 4, 8), ("button_plus", 6, 9), ("button_home", 5, 10),
]


def _fallback_scheme(binds: dict) -> str:
    """'gc' vs 'raw' for an UNTEMPLATED pad, by majority vote across its stable anchor bindings.
    Voting across several anchors (not one discriminator row) means remapping a single button
    cannot flip the scheme of a pad that still has another gc anchor -- which every fully-mapped
    GameController pad does (L3=7, R3=8, L=9, R=10, ...). Only a strict gc MAJORITY yields 'gc'; a
    tie or no gc evidence stays 'raw' (the historical default for a pad we cannot identify), so a
    genuine raw pad is never promoted. (Edge: a degenerate pad down to its LAST gc anchor can still
    shift once that anchor is remapped -- byte stays correct, only the label reads raw.)"""
    gc = raw = 0
    for func, gc_idx, raw_idx in _SCHEME_ANCHORS:
        n = _btn_idx(binds.get(func, ""))
        if n == gc_idx:
            gc += 1
        elif n == raw_idx:
            raw += 1
    return "gc" if gc > raw else "raw"


def capture_button_index(input_dir, guid: str, code: int, sch: str):
    """The pad's OWN SDL button index for a captured physical button `code`: the device
    template's value for the func that physical button naturally drives (device-exact), else the
    static scheme table. None if the code is not a mappable digital button."""
    func = _EVDEV_TO_FUNC.get(code)
    if func:
        idx = _btn_idx(_template(input_dir, guid).get(func, ""))
        if idx is not None:
            return idx
    return xemu_button_index(code) if sch == "gc" else sdl_button_index(code)


def label_button_index(input_dir, guid: str, n: int, sch: str) -> str:
    """Friendly label for a stored `button:n` on this pad: invert the device template (the first
    primary func whose index is n -> its physical label), else the static scheme label. Iterates
    only the primary _BUTTONS funcs, so a pad's SL/SR aliases (same index) never shadow the real
    button."""
    tmpl = _template(input_dir, guid)
    if tmpl:
        for func, _ in _BUTTONS:
            if _btn_idx(tmpl.get(func, "")) == n:
                return _FUNC_LABEL.get(func, func)
    return xemu_index_label(n) if sch == "gc" else sdl_index_label(n)


def capture_axis_index(input_dir, guid: str, canonical: str, token: str, sch: str):
    """The pad's OWN axis index for a captured trigger/stick push: the device template's axis for
    the matching func (button_zl/zr for triggers; lstick/rstick axis_x/axis_y for sticks),
    device-exact; else the static scheme index (gc SDL_GameControllerAxis) or the measured @rank
    (raw). None if nothing resolves."""
    tmpl = _template(input_dir, guid)
    if tmpl:
        if canonical_is_trigger(canonical):
            func = "button_zl" if canonical == "trigger_left" else "button_zr"
            m = re.search(r"axis:(\d+)", tmpl.get(func, ""))
            if m:
                return int(m.group(1))
        else:
            stick = "lstick" if canonical.startswith("left") else "rstick"
            axdir = "axis_x" if canonical.endswith("_x") else "axis_y"
            m = re.search(rf"{axdir}:(\d+)", tmpl.get(stick, ""))
            if m:
                return int(m.group(1))
    if sch == "gc":
        return xemu_axis_index(canonical)
    return axis_token_rank(token)


def _axis_label(idx: int, sch: str) -> str:
    return xemu_axis_label(idx) if sch == "gc" else f"axis {idx}"


# ---------------------------------------------------------------------------
# Pure text helpers (shared; deterministic functions of on-disk text + the pad's
# template glob, so InputBuffer replay-onto-fresh-disk stays correct).
# ---------------------------------------------------------------------------
def _value(text: str, key: str, player: str) -> str:
    return cfgutil.ini_read(text, _SECTION, f"{player}_{key}") or ""


def _configured_pad(text: str, player: str) -> str:
    """Friendly name of the controller this player's bindings point at, or '' if none."""
    from ..mad_config import pad_name, vidpid_from_sdl_guid
    for key, _ in _BUTTONS:
        m = _GUID_RE.search(_value(text, key, player))
        if m:
            return pad_name(vidpid_from_sdl_guid(m.group(1)))
    return ""


def _append_token(cur: str, token: str) -> str:
    """Add `token` to a binding value, INSIDE its closing quote when quoted -- so a synthesised
    skeleton stays a VALID quoted binding and is not written unquoted/malformed."""
    if cur.endswith('"'):
        return f'{cur[:-1].rstrip(",")},{token}"'
    return f'{cur.rstrip(",")},{token}'


def _player_device(text: str, player: str) -> tuple[str, str]:
    """(guid, port) of the pad this player is configured for, from ANY binding still carrying a
    `guid:` -- so a single cleared key does not read as 'no pad'. ('', '') only when the player has
    no controller at all."""
    for key, _ in _BUTTONS + _DPAD:
        m = _GUID_RE.search(_value(text, key, player))
        if m:
            p = re.search(r"port:(\d+)", _value(text, key, player))
            return m.group(1), (p.group(1) if p else "0")
    for stick in ("lstick", "rstick"):
        v = _value(text, stick, player)
        m = _GUID_RE.search(v)
        if m:
            p = re.search(r"port:(\d+)", v)
            return m.group(1), (p.group(1) if p else "0")
    return "", ""


def _player_scheme(text: str, input_dir, player: str, guid: str) -> str:
    """The pad's numbering scheme for the fallback path: from its clean template if one matches,
    else a consensus across the player's live anchor bindings -- stable under a single-row remap as
    long as the pad keeps another gc anchor (every fully-mapped pad does), so editing L-stick-click
    no longer flips the whole pad's labels (see _fallback_scheme for the degenerate exception)."""
    if not guid:
        return "raw"
    tmpl = _template(input_dir, guid)
    if tmpl:
        return "gc" if _btn_idx(tmpl.get("button_lstick", "")) == 7 else "raw"
    binds = {func: _value(text, func, player) for func, _, _ in _SCHEME_ANCHORS}
    return _fallback_scheme(binds)


def _shown(text: str, input_dir, key: str, player: str) -> str:
    """The current binding rendered for display: a plain button, an analog-trigger axis, or a
    d-pad hat direction. Labels a button via device-exact template inversion."""
    v = _value(text, key, player)
    guid = _guid_of(v)
    sch = _player_scheme(text, input_dir, player, guid)
    m = _BTN_RE.search(v)
    if m:
        return label_button_index(input_dir, guid, int(m.group(1)), sch)
    ma = re.search(r"axis:(\d+)", v)
    if ma:
        return _axis_label(int(ma.group(1)), sch)
    md = re.search(r"direction:(\w+)", v)
    if md:
        return f"D-pad {md.group(1)}"
    return "—"


def _btn_kind(text: str, input_dir, key: str, player: str) -> str:
    """Capture KIND for a Button-group row: ZL/ZR bound as an analog axis (DS/DS4/Deck) capture as
    "trigger"; everything else "btn". If a ZL/ZR value was CLEARED, consult the device template so
    an analog-trigger pad still captures as a trigger."""
    if key in _TRIGGER_KEYS:
        if "axis:" in _value(text, key, player):
            return "trigger"
        m = _GUID_RE.search(_value(text, "button_a", player)) or _GUID_RE.search(_value(text, "button_dup", player))
        if m and "axis:" in _template(input_dir, m.group(1)).get(key, ""):
            return "trigger"
    return "btn"


def _shown_stick(text: str, input_dir, key: str, player: str) -> str:
    """Stored raw axis index for a stick-axis row ('lstick_x' -> axis_x:N in the lstick line)."""
    stick, axis = key.rsplit("_", 1)
    cur = _value(text, stick, player)
    m = re.search(rf"axis_{axis}:(\d+)", cur)
    if not m:
        return "—"
    guid = _guid_of(cur)
    return _axis_label(int(m.group(1)), _player_scheme(text, input_dir, player, guid))


def _remap_dpad(cur: str, token: str, key: str, input_dir):
    """New d-pad value from a captured hat token, preserving the device. Hat-style pad -> re-point
    hat:N + direction:D; button-style pad -> the per-device button:idx from the pad's template
    (eden_cfg.dpad_index), else derived from its own base, else the Wii U rank. Returns (value,
    label)."""
    parts = hat_token_parts(token)
    if parts is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    n, d = parts
    if "hat:" in cur:
        v = re.sub(r"hat:\d+", f"hat:{n}", cur, count=1)
        v = re.sub(r"direction:\w+", f"direction:{d}", v, count=1)
        return v, f"D-pad {d}"
    from .. import eden_cfg
    if "button:" not in cur:
        if "axis:" in cur:        # a guid-ful axis d-pad: not re-mappable as a hat/button here
            raise RpcError("EINVAL", "this pad's d-pad can't be remapped here")
        m = _GUID_RE.search(cur)
        tv = eden_cfg._match_template(input_dir, m.group(1) if m else "").get(key, "")
        hm = re.search(r"hat:(\d+)", tv)
        if hm:                    # hat-style pad: synthesise the hat binding
            return _append_token(cur, f"hat:{hm.group(1)},direction:{d}"), f"D-pad {d}"
        idx = eden_cfg.dpad_index(input_dir, cur, d, key)
        if idx is None:
            idx = eden_hat_button_index(token)
        if idx is None:
            raise RpcError("EINVAL", "press a d-pad direction")
        return _append_token(cur, f"button:{idx}"), f"D-pad {d}"
    idx = eden_cfg.dpad_index(input_dir, cur, d, key)
    if idx is None:
        idx = eden_hat_button_index(token)   # last resort: the historical Wii U rank
    if idx is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    return _BTN_RE.sub(f"button:{idx}", cur, count=1), f"D-pad {d}"


def _remap_button(cur: str, code: int, input_dir, sch: str) -> tuple:
    """New value for a plain digital button row. Re-points `button:idx` (the pad's OWN index for
    the captured physical button), preserving the device."""
    guid = _guid_of(cur)
    idx = capture_button_index(input_dir, guid, code, sch)
    if idx is None:
        raise RpcError("EINVAL", "that input can't be mapped -- press a face, shoulder, "
                                 "trigger, stick-click, Minus or Plus button")
    if "axis:" in cur:                           # a ZL/ZR analog row -> not a plain button
        raise RpcError("EINVAL", "this control is an axis on this pad -- use the trigger row")
    label = label_button_index(input_dir, guid, idx, sch)
    if "button:" in cur:
        return _BTN_RE.sub(f"button:{idx}", cur, count=1), label
    return _append_token(cur, f"button:{idx}"), label   # cleared/missing binding -> add the button


def _remap_trigger(cur: str, token: str, input_dir, sch: str):
    """New value for an analog-trigger row (ZL/ZR) from an axisname trigger token. Re-points
    `axis:N` (the pad's OWN trigger axis, template-exact), preserving the device; synthesises a
    fresh axis binding if the slot was not an axis."""
    parsed = parse_axis_token(token)
    if parsed is None or not canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "pull the trigger")
    guid = _guid_of(cur)
    rank = capture_axis_index(input_dir, guid, parsed[1], token, sch)
    if rank is None:
        raise RpcError("EINVAL", "pull the trigger")
    label = _axis_label(rank, sch)
    if "axis:" in cur:
        return re.sub(r"axis:\d+", f"axis:{rank}", cur, count=1), label
    pm = re.search(r"port:(\d+)", cur)
    port = pm.group(1) if pm else "0"
    return (f'"engine:sdl,invert:+,port:{port},guid:{guid},axis:{rank},threshold:0.500000"',
            label)


def _apply_stick(text: str, input_dir, player: str, key: str, value: str, plabel: str, emu: str) -> str:
    """New text for remapping one stick axis: rewrite ONLY axis_<dir> + invert_<dir> in the
    player_N_lstick/rstick line, preserving offset_* (calibration). Pure text->text."""
    parsed = parse_axis_token(value)
    if parsed is None or canonical_is_trigger(parsed[1]):
        raise RpcError("EINVAL", "push the stick the way the row says")
    sign, canonical = parsed
    inv = "-" if axis_invert(sign, canonical) else "+"   # '+' = normal, '-' = inverted
    stick, axis = key.rsplit("_", 1)                      # 'lstick'/'rstick', 'x'/'y'
    cur = _value(text, stick, player)
    guid = _guid_of(cur)
    if not _player_device(text, player)[0]:
        raise RpcError("EINVAL", f"{plabel} has no pad here. Configure it in {emu} first.")
    if f"axis_{axis}:" not in cur:
        raise RpcError("EINVAL", f"{plabel}'s stick is unset. Re-set it in {emu}.")
    sch = _player_scheme(text, input_dir, player, guid)
    rank = capture_axis_index(input_dir, guid, canonical, value, sch)
    if rank is None:
        raise RpcError("EINVAL", "push the stick the way the row says")
    new_val = re.sub(rf"axis_{axis}:\d+", f"axis_{axis}:{rank}", cur, count=1)
    new_val = re.sub(rf"invert_{axis}:[+-]", f"invert_{axis}:{inv}", new_val, count=1)
    new = cfgutil.ini_replace(text, _SECTION, f"{player}_{stick}", new_val)
    if new is None:
        raise RpcError("EINTERNAL", f"no '{player}_{stick}' line in [{_SECTION}]")
    return new


# ---------------------------------------------------------------------------
# The stateful page: config + buffered editor, one instance per emulator.
# ---------------------------------------------------------------------------
class YuzuInputPage:
    """One emulator's buffered per-button input page. `file_getter` returns the live config path
    (a callable so tests can repoint it); `flip_default_on_write` is True for Citron (it discards
    a value whose <key>\\default is true, so every write flips the twin) and False for Eden."""

    def __init__(self, *, file_getter, proc: str, flip_default_on_write: bool,
                 note_suffix: str = ""):
        self._file_getter = file_getter
        self.proc = proc
        self.flip_default_on_write = flip_default_on_write
        self.note_suffix = note_suffix
        # ctx = () because the config is a single global qt-config.ini; the whole-file working copy
        # spans every player, so the Player stepper is a pure render filter (see input_buffer).
        self._ctx: tuple = ()
        self.buf = InputBuffer(load=self._load, apply_edit=self._apply_edit, flush=self._flush)

    # -- config accessors -------------------------------------------------------
    @property
    def file(self) -> Path:
        return self._file_getter()

    @property
    def input_dir(self) -> Path:
        return self.file.parent / "input"

    @property
    def emu(self) -> str:
        return self.proc.capitalize()

    def scheme(self, guid: str) -> str:
        return scheme(self.input_dir, guid)

    # -- helpers ----------------------------------------------------------------
    def _player(self, params) -> str:
        p = params.get("player") or _DEFAULT_PLAYER
        if p not in _PLAYER_IDS:
            raise RpcError("EINVAL", f"unknown player {p!r}")
        return p

    def _plabel(self, player: str) -> str:
        return next((p["label"] for p in _PLAYERS if p["id"] == player), player)

    def _flip_default(self, text: str, name: str) -> str:
        r = cfgutil.ini_replace(text, _SECTION, name + "\\default", "false")
        return r if r is not None else text

    # -- RPC verbs --------------------------------------------------------------
    def input_get(self, params):
        player = self._player(params)
        text = self.buf.get(self._ctx)      # buffer-over-disk: reflects staged, unsaved edits
        run = proc_guard.emulator_running(self.proc)
        plabel = self._plabel(player)
        idir = self.input_dir

        def row(key, label, kind, capturable):
            value = (_shown_stick(text, idir, key, player) if kind == "axis"
                     else _shown(text, idir, key, player))
            return {"id": key, "label": label, "kind": kind, "value": value,
                    "capturable": capturable and not run}

        groups = [
            {"title": f"Buttons ({plabel})",
             "binds": [row(k, l, _btn_kind(text, idir, k, player), True) for k, l in _BUTTONS]},
            {"title": "D-pad", "binds": [row(k, l, "hat", True) for k, l in _DPAD]},
            {"title": "Analog sticks", "binds": [row(k, l, "axis", True) for k, l in _STICKS]},
        ]
        cname = _configured_pad(text, player)
        note = (f"Close {self.emu} first — it rewrites its config on exit." if run else
                f"Remaps {plabel}'s configured controller (set its pad on the "
                f"Controllers page first).{self.note_suffix}")
        if cname:
            note = f"Controller: {cname}.  " + note
        ptype = (cfgutil.ini_read(text, _SECTION, f"{player}_type") or "0").strip()
        raw_docked = (cfgutil.ini_read(text, _SYSTEM_SECTION, "use_docked_mode") or "1").strip()
        docked = "1" if raw_docked.lower() in ("1", "true", "yes", "on") else "0"
        type_opts = list(_CTYPES)
        if ptype not in _CTYPE_VALUES:                 # surface an unlisted on-disk value
            type_opts = [(ptype, ptype)] + type_opts
        selectors = [
            {"key": "controller_type", "label": "Type", "scope": "player", "value": ptype,
             "options": [{"value": v, "label": l} for v, l in type_opts]},
            {"key": "console_mode", "label": "Console", "scope": "global", "value": docked,
             "options": [{"value": v, "label": l} for v, l in _CONSOLE]},
        ]
        return {"running": run, "note": note, "groups": groups, "selectors": selectors,
                "players": _PLAYERS, "player": player, "clearable": True,
                "buffered": True, "dirty": self.buf.dirty}

    def input_set(self, params):
        player = self._player(params)
        key = params.get("id", "")
        kind = params.get("kind", "btn")
        edit = {"op": "set", "player": player, "id": key, "kind": kind,
                "value": str(params.get("value", ""))}
        self.buf.set(self._ctx, edit)               # stage in memory (validated by _apply)
        text = self.buf.working
        val = (_shown_stick(text, self.input_dir, key, player) if key in _STICK_KEYS and kind == "axis"
               else _shown(text, self.input_dir, key, player))
        return {"id": key, "value": val, "dirty": self.buf.dirty,
                "message": f"{key.replace('button_', '').upper()} → {val}"}

    def selector_set(self, params):
        key = params.get("key")
        value = str(params.get("value", "")).strip()
        edit = {"op": "selector", "key": key, "value": value}
        if key == "controller_type":
            edit["player"] = self._player(params)   # validates the player before staging
        self.buf.set(self._ctx, edit)
        disp = next((l for v, l in (_CTYPES + _CONSOLE) if v == value), value)
        label = self._plabel(edit["player"]) if key == "controller_type" else "Console mode"
        return {"key": key, "value": value, "dirty": self.buf.dirty, "message": f"{label} → {disp}"}

    def input_clear(self, params):
        player = self._player(params)
        key = params.get("id") or params.get("key") or ""
        edit = {"op": "clear", "player": player, "id": key}
        self.buf.set(self._ctx, edit)
        return {"id": key, "value": "—", "dirty": self.buf.dirty, "message": f"{key} cleared"}

    def input_save(self, params):
        return {"saved": self.buf.save(self._ctx), "dirty": self.buf.dirty}

    def input_cancel(self, params):
        self.buf.cancel(self._ctx)
        return {"cancelled": True, "dirty": self.buf.dirty}

    # -- apply / persist (pure; replayed by the buffer's flush) -----------------
    def _apply_selector(self, text: str, edit: dict) -> str:
        key = edit["key"]
        value = str(edit.get("value", "")).strip()
        defer_ctype = False
        if key == "controller_type":
            section, name = _SECTION, f"{edit['player']}_type"
            defer_ctype = value not in _CTYPE_VALUES
        elif key == "console_mode":
            if value not in _CONSOLE_VALUES:
                raise RpcError("EINVAL", "console mode must be Docked or Handheld")
            section, name = _SYSTEM_SECTION, "use_docked_mode"
        else:
            raise RpcError("EINVAL", f"unknown selector {key!r}")
        if defer_ctype and (cfgutil.ini_read(text, section, name) or "").strip() != value:
            raise RpcError("EINVAL", f"unknown controller type {value!r}")
        new = cfgutil.ini_replace(text, section, name, value)
        if new is None:
            raise RpcError("EINTERNAL", f"no '{name}' line in [{section}]")
        # The emulator ignores a stored value while its `<key>\default` is true -- flip it.
        flipped = cfgutil.ini_replace(new, section, name + "\\default", "false")
        return flipped if flipped is not None else new

    def _apply_clear(self, text: str, edit: dict) -> str:
        player = edit["player"]
        key = edit.get("id") or edit.get("key") or ""
        if key not in _BUTTON_KEYS and key not in _DPAD_KEYS and key not in _STICK_KEYS:
            raise RpcError("EINVAL", f"{key!r} is not a remappable {self.emu} input")
        name = f"{player}_{key.rsplit('_', 1)[0]}" if key in _STICK_KEYS else f"{player}_{key}"
        if cfgutil.ini_read(text, _SECTION, name) is None:
            raise RpcError("EINVAL", f"{self._plabel(player)} has no '{key}' binding to clear")
        new = cfgutil.ini_replace(text, _SECTION, name, "[empty]")
        if new is None:
            raise RpcError("EINTERNAL", f"no '{name}' line in [{_SECTION}]")
        return self._flip_default(new, name)

    def _apply(self, text: str, edit: dict) -> str:
        """Apply one staged edit to `text`. Pure (no I/O, no bump). Replayed verbatim by the
        buffer's flush onto a FRESH disk read, so foreign edits to other keys survive."""
        if proc_guard.emulator_running(self.proc):
            raise RpcError("EBUSY", f"close {self.emu} first — it rewrites its config on exit")
        op = edit.get("op")
        if op == "clear":
            return self._apply_clear(text, edit)
        if op == "selector":
            return self._apply_selector(text, edit)
        # op == "set"
        player, key, kind = edit["player"], edit["id"], edit["kind"]
        value = str(edit.get("value", ""))
        idir = self.input_dir
        plabel = self._plabel(player)
        if key in _STICK_KEYS and kind == "axis":
            return self._maybe_flip(
                _apply_stick(text, idir, player, key, value, plabel, self.emu),
                f"{player}_{key.rsplit('_', 1)[0]}")
        cur = _value(text, key, player)
        dev_guid, dev_port = _player_device(text, player)
        if not dev_guid:                             # the PLAYER has no controller at all
            raise RpcError("EINVAL", f"{plabel} has no pad here. Configure it in {self.emu} first.")
        if "guid:" not in cur:                       # this one binding is missing/cleared -> re-create
            cur = f'"engine:sdl,port:{dev_port},guid:{dev_guid}"'   # QUOTED skeleton
        sch = _player_scheme(text, idir, player, dev_guid)
        if key in _DPAD_KEYS and kind == "hat":
            new_val, _ = _remap_dpad(cur, value, key, idir)
        elif key in _TRIGGER_KEYS and kind == "trigger":
            new_val, _ = _remap_trigger(cur, value, idir, sch)
        elif key in _BUTTON_KEYS and kind == "btn":
            try:
                code = int(value)
            except (ValueError, TypeError):
                raise RpcError("EINVAL", "missing or invalid button code")
            new_val, _ = _remap_button(cur, code, idir, sch)
        else:
            raise RpcError("EINVAL", f"{key!r} is not a remappable {self.emu} input")
        new = cfgutil.ini_replace(text, _SECTION, f"{player}_{key}", new_val)
        if new is None:
            raise RpcError("EINTERNAL", f"no '{player}_{key}' line in [{_SECTION}]")
        return self._maybe_flip(new, f"{player}_{key}")

    def _maybe_flip(self, text: str, name: str) -> str:
        """Flip the `\\default` twin on a button/stick write only for emulators that need it
        (Citron); Eden relies on the line already being \\default=false."""
        return self._flip_default(text, name) if self.flip_default_on_write else text

    def _load(self, ctx: tuple) -> str:
        f = self.file
        if not f.is_file():
            raise RpcError("ENOENT", f"{self.emu} config not found at {f} -- launch a game once")
        return f.read_text(encoding="utf-8", errors="replace")

    def _apply_edit(self, text: str, edit: dict):
        return self._apply(text, edit), edit

    def _flush(self, ctx: tuple, disk: str, edits: list) -> str:
        f = self.file
        if not f.is_file():
            raise RpcError("ENOENT", f"{self.emu} config not found at {f}")
        text = f.read_text(encoding="utf-8", errors="replace")   # replay onto FRESH disk
        for edit in edits:
            text = self._apply(text, edit)
        cfgutil.ensure_bak(f)
        cfgutil.atomic_write(f, text)
        return text
