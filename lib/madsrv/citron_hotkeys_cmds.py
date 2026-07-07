r"""citron_hk.* — Citron (Switch) Hotkeys remapper.

Citron stores each hotkey action under [UI] as
  Shortcuts\<Group>\<Action>\KeySeq              keyboard, a QKeySequence ("Ctrl+P", "F10")
  Shortcuts\<Group>\<Action>\Controller_KeySeq   controller, "+"-joined Switch tokens ("Home+X")
plus \Context and \Repeat (+ each key's \default twin). The RUNTIME binds from THIS store, NOT
from hotkey_profiles.json (which only Citron's own Hotkeys dialog reads) -- verified against
github.com/citron-neo/emulator (main.cpp InitializeHotkeys -> HotkeyRegistry::LoadHotkeys ->
QtConfig::ReadShortcutValues). The action list is ENUMERATED from the live store, so it tracks
whatever actions the installed build defines (no hardcoded list to drift).

FORMAT-ADAPTIVE: the installed nightly uses the legacy NESTED format above; current Citron main
has switched to a flat `shortcuts\<i>\...` ARRAY. We fully remap the nested format; on a flat
array we show the hotkeys READ-ONLY with a note to edit them in Citron (the array writer is a
follow-up, to be built + verified against a flat-format build on-device).

Rendered by the generic input_map page (arg "citron_hk"), kind "chord": a capture accumulates
held inputs; keyboard codes -> KeySeq (QKeySequence), controller codes -> Controller_KeySeq
(Switch tokens, best-effort Nintendo layout). Citron rewrites qt-config on exit, so writes
refuse while it runs. Every write flips the field's \default=false so Citron honours it.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from .. import proc_guard
from . import capture_cmds, cfgutil
from .input_buffer import InputBuffer
from .input_translate import sdl_button_source
from .rpc import RpcError, method

_FILE = Path.home() / ".config/citron/qt-config.ini"
_SECTION = "UI"
_PROC = "citron"

# A hotkey value line:  Shortcuts\<Group>\<Action>\KeySeq=<value>   (NOT the \default twin,
# whose line is ...\KeySeq\default=...). Group/Action carry no '\' or '=' (they are %-encoded).
_ACT_RE = re.compile(r"(?m)^(Shortcuts\\([^\\=]+)\\([^\\=]+))\\KeySeq=")

# Keyboard: a captured ra_keyname -> a Qt modifier / key name for a QKeySequence.
_MODS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "meta": "Meta", "super": "Meta"}
_QT_KEY = {"space": "Space", "enter": "Return", "return": "Return", "escape": "Escape",
           "esc": "Escape", "tab": "Tab", "backspace": "Backspace", "delete": "Del",
           "insert": "Ins", "home": "Home", "end": "End", "pageup": "PgUp",
           "pagedown": "PgDown", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
           "minus": "-", "equal": "=", "plus": "+"}
# Controller: a captured SDL button-source name -> Citron's Controller_KeySeq token
# (best-effort Nintendo physical layout: A=right, B=bottom, X=top, Y=left).
_PAD_TOKEN = {"FaceEast": "A", "FaceSouth": "B", "FaceNorth": "X", "FaceWest": "Y",
              "LeftShoulder": "L", "RightShoulder": "R", "LeftTrigger": "ZL",
              "RightTrigger": "ZR", "Start": "Plus", "Back": "Minus", "Select": "Minus",
              "Guide": "Home", "DpadUp": "Dpad_Up", "DpadDown": "Dpad_Down",
              "DpadLeft": "Dpad_Left", "DpadRight": "Dpad_Right",
              "LeftStick": "Left_Stick", "RightStick": "Right_Stick"}


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _decode(s: str) -> str:
    return urllib.parse.unquote(s.replace("%20", " "))


def _is_flat(text: str) -> bool:
    """The newer Citron flat-array shortcut store (shortcuts\\size=N)."""
    return cfgutil.ini_read(text, _SECTION, "shortcuts\\size") is not None


def _actions(text: str) -> list[tuple[str, str, str]]:
    """[(base_key, group_display, action_display)] enumerated from the live nested store, in
    file order. base_key = `Shortcuts\\<Group>\\<Action>` (append \\KeySeq / \\Controller_KeySeq)."""
    span = cfgutil._ini_span(text, _SECTION)
    if not span:
        return []
    body = text[span[0]:span[1]]
    out, seen = [], set()
    for m in _ACT_RE.finditer(body):
        base = m.group(1)
        if base in seen:
            continue
        seen.add(base)
        out.append((base, _decode(m.group(2)), _decode(m.group(3))))
    return out


def _qt_key(ra: str) -> str | None:
    if ra in _MODS:
        return None                         # a modifier, handled separately
    if len(ra) == 1:
        return ra.upper()                   # letter / digit
    if re.fullmatch(r"f\d+", ra):
        return ra.upper()                   # F1..F12
    return _QT_KEY.get(ra)


def _keyseq(names: list[str]) -> str:
    """A QKeySequence string from captured keyboard names: ordered modifiers + one key."""
    mods, keys = [], []
    for n in names:
        if n in _MODS:
            if _MODS[n] not in mods:
                mods.append(_MODS[n])
        else:
            k = _qt_key(n)
            if k and k not in keys:
                keys.append(k)
    order = [m for m in ("Ctrl", "Alt", "Shift", "Meta") if m in mods]
    return "+".join(order + keys[:1])


def _ctrl_seq(names: list[str]) -> str:
    """A Controller_KeySeq token string from captured pad button-source names. sdl_button_source
    sign-prefixes trigger/axis sources (e.g. '+LeftTrigger'), so strip a leading +/-/~ before
    the token lookup (else ZL/ZR would never map)."""
    out = []
    for n in names:
        t = _PAD_TOKEN.get(n.lstrip("+-~"))
        if t and t not in out:
            out.append(t)
    return "+".join(out)


def _render(text: str, base: str) -> str:
    kb = (cfgutil.ini_read(text, _SECTION, base + "\\KeySeq") or "").strip()
    pad = (cfgutil.ini_read(text, _SECTION, base + "\\Controller_KeySeq") or "").strip()
    parts = [p for p in (kb, pad) if p]
    return "  ·  ".join(parts) if parts else "—"


@method("citron_hk.input_get", slow=True)   # buffered: NO cache=("config",) — the in-memory buffer is truth
def _input_get(params):
    text = _buf.get(_CTX)                    # buffer-over-disk: reflects staged, unsaved edits
    run = _running()
    flat = _is_flat(text)
    if flat:
        # Newer flat-array format: enumerate read-only (name/group/keyseq/controller_keyseq).
        binds = []
        try:
            n = int(cfgutil.ini_read(text, _SECTION, "shortcuts\\size") or "0")
        except ValueError:
            n = 0
        for i in range(1, n + 1):
            name = _decode(cfgutil.ini_read(text, _SECTION, f"shortcuts\\{i}\\name") or "")
            kb = (cfgutil.ini_read(text, _SECTION, f"shortcuts\\{i}\\keyseq") or "").strip()
            pad = (cfgutil.ini_read(text, _SECTION, f"shortcuts\\{i}\\controller_keyseq") or "").strip()
            val = "  ·  ".join(p for p in (kb, pad) if p) or "—"
            binds.append({"id": f"flat:{i}", "label": name or f"#{i}", "kind": "chord",
                          "value": val, "capturable": False})
        return {"running": run, "note": "This Citron build uses a newer hotkey format — view "
                "only here; change hotkeys in Citron's Configure > Hotkeys dialog.",
                "groups": [{"title": "Hotkeys (read-only)", "binds": binds}], "clearable": False,
                "buffered": True, "dirty": _buf.dirty}

    def row(base, act):
        return {"id": base, "label": act, "kind": "chord",
                "value": _render(text, base), "capturable": not run}

    # Group by the (single) hotkey group, in file order.
    groups: list = []
    by_group: dict = {}
    for base, grp, act in _actions(text):
        by_group.setdefault(grp, []).append(row(base, act))
    for grp, binds in by_group.items():
        groups.append({"title": grp, "binds": binds})
    note = ("Close Citron first — it rewrites its config on exit." if run else
            "Bind each action to a keyboard key/combo and/or a controller button "
            "(shown as keyboard · controller). Highlight a row and press Start to clear it. "
            "Controller tokens are best-effort — verify in Citron if a mapping looks off.")
    return {"running": run, "note": note, "groups": groups, "clearable": True,
            "buffered": True, "dirty": _buf.dirty}


def _flip_default(text: str, key: str) -> str:
    r = cfgutil.ini_replace(text, _SECTION, key + "\\default", "false")
    return r if r is not None else text


def _write_field(text: str, base: str, field: str, value: str) -> str | None:
    """Replace `<base>\\<field>` and flip its \\default twin. None if the field line is absent
    (never create a hotkey key that Citron didn't write)."""
    key = base + "\\" + field
    new = cfgutil.ini_replace(text, _SECTION, key, value)
    if new is None:
        return None
    return _flip_default(new, key)


def _apply_set(text: str, edit: dict) -> str:
    """New text for a hotkey remap: derive KeySeq (keyboard) + Controller_KeySeq (pad) from
    the captured codes and rewrite ONLY those [UI] fields (flipping each \\default). Pure."""
    base = edit.get("id", "")
    if base not in {b for b, _, _ in _actions(text)}:
        raise RpcError("EINVAL", f"{base!r} is not a Citron hotkey action")
    kb_names, pad_names = [], []
    for c in edit.get("codes") or []:
        try:
            ci = int(c)
        except (TypeError, ValueError):
            continue
        kn = capture_cmds.ra_keyname(ci)
        if kn:
            kb_names.append(kn)
            continue
        pn = sdl_button_source(ci)
        if pn:
            pad_names.append(pn)
    keyseq = _keyseq(kb_names)
    ctrlseq = _ctrl_seq(pad_names)
    if not keyseq and not ctrlseq:
        raise RpcError("EINVAL", "that input can't be bound as a Citron hotkey")
    new = text
    if keyseq:
        w = _write_field(new, base, "KeySeq", keyseq)
        if w is not None:
            new = w
    if ctrlseq:
        w = _write_field(new, base, "Controller_KeySeq", ctrlseq)
        if w is not None:
            new = w
    return new


def _apply_clear(text: str, edit: dict) -> str:
    """New text for clearing a hotkey: blank both [UI] fields. Pure."""
    base = edit.get("id") or edit.get("key") or ""
    if base not in {b for b, _, _ in _actions(text)}:
        raise RpcError("EINVAL", f"{base!r} is not a Citron hotkey action")
    new = text
    for field in ("KeySeq", "Controller_KeySeq"):
        w = _write_field(new, base, field, "")
        if w is not None:
            new = w
    return new


def _apply(text: str, edit: dict) -> str:
    """Apply one staged edit to `text`, returning the new text. Pure (no I/O, no bump).
    Replayed verbatim by the buffer's flush onto a FRESH disk read, so foreign [Controls]/
    [System] edits (written by eden.*/citron.*) survive — we rewrite ONLY the [UI] hotkey
    fields. Refuses while Citron runs (it rewrites its config on exit) and on the read-only
    flat-array format, so both guards fire at stage AND at save."""
    if _running():
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit")
    if _is_flat(text):
        raise RpcError("EINVAL", "this Citron build's hotkeys are read-only in MAD — "
                                 "change them in Citron's Configure > Hotkeys dialog.")
    if edit.get("op") == "clear":
        return _apply_clear(text, edit)
    return _apply_set(text, edit)


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). Edits stage in the module-level
# InputBuffer and only reach disk on citron_hk.input_save; citron_hk.input_cancel drops them.
# ctx = () because Citron's hotkeys live in the single global qt-config.ini [UI] section.
# CRITICAL: _flush re-reads FRESH disk and replays only the [UI] hotkey edits, so the
# [Controls]/[System] blocks that citron.* (also buffered) rewrites are never clobbered.
# ---------------------------------------------------------------------------
_CTX: tuple = ()


def _load(ctx: tuple) -> str:
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE} — launch a game once")
    return _FILE.read_text(encoding="utf-8", errors="replace")


def _apply_edit(text: str, edit: dict):
    return _apply(text, edit), edit


def _flush(ctx: tuple, disk: str, edits: list) -> str:
    if not _FILE.is_file():
        raise RpcError("ENOENT", f"Citron config not found at {_FILE}")
    text = _FILE.read_text(encoding="utf-8", errors="replace")   # replay onto FRESH disk
    for edit in edits:
        text = _apply(text, edit)
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, text)
    return text


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("citron_hk.input_set", slow=True)
def _input_set(params):
    base = params.get("id", "")
    codes = params.get("codes")
    if codes is None and str(params.get("value", "")).strip():
        try:
            codes = [int(params.get("value"))]
        except (TypeError, ValueError):
            codes = None
    if not codes:
        raise RpcError("EINVAL", "press a key or button (or hold a combo)")
    _buf.set(_CTX, {"op": "set", "id": base, "codes": list(codes)})   # stage; no disk write
    new = _buf.working
    return {"id": base, "value": _render(new, base), "dirty": _buf.dirty,
            "message": f"{_decode(base.rsplit(chr(92), 1)[-1])} → {_render(new, base)}"}


@method("citron_hk.input_clear", slow=True)
def _input_clear(params):
    base = params.get("id") or params.get("key") or ""
    _buf.set(_CTX, {"op": "clear", "id": base})                      # stage; no disk write
    return {"id": base, "value": "—", "dirty": _buf.dirty, "message": "hotkey cleared"}


@method("citron_hk.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_CTX), "dirty": _buf.dirty}


@method("citron_hk.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_CTX)
    return {"cancelled": True, "dirty": _buf.dirty}
