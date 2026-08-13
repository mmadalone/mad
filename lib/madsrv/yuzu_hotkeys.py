r"""Shared Yuzu-fork (Citron/Eden) Hotkeys engine (citron_hk.* / eden_hk.*).

The fork stores each hotkey action under [UI] as
  Shortcuts\<Group>\<Action>\KeySeq              keyboard, a QKeySequence ("Ctrl+P", "F10")
  Shortcuts\<Group>\<Action>\Controller_KeySeq   controller, "+"-joined Switch tokens ("Home+X")
plus \Context and \Repeat (+ each key's \default twin). The RUNTIME binds from THIS store,
NOT from hotkey_profiles.json (which only the fork's own Hotkeys dialog reads) -- verified
against the shared Yuzu-fork path main.cpp InitializeHotkeys -> HotkeyRegistry::LoadHotkeys
-> QtConfig::ReadShortcutValues (originally traced on citron-neo/emulator). The action list
is ENUMERATED from the live store, so it tracks whatever actions the installed build
defines (no hardcoded list to drift).

FORMAT-ADAPTIVE: legacy builds use the NESTED format above; newer Yuzu-fork main has
switched to a flat `shortcuts\<i>\...` ARRAY. We fully remap the nested format; on a flat
array we show the hotkeys READ-ONLY with a note to edit them in the emulator (the array
writer is a follow-up, to be built + verified against a flat-format build on-device).

Rendered by the generic input_map page (arg = the ns), kind "chord": a capture accumulates
held inputs; keyboard codes -> KeySeq (QKeySequence), controller codes -> Controller_KeySeq
(Switch tokens, best-effort Nintendo layout). The fork rewrites qt-config on exit, so
writes refuse while it runs. Every write flips the field's \default=false so the fork
honours it.

One HotkeysEngine instance per fork; constructing it registers the five <ns>.input_*
methods and owns its OWN InputBuffer (staged edits must never cross forks - pinned by
test_switch_ns_parity.test_hotkey_buffers_are_per_fork). file_getter is read PER CALL so
a test repointing the shim's _FILE global is honored.
"""
from __future__ import annotations

import re
import urllib.parse

from .. import proc_guard
from . import capture_cmds, cfgutil
from .input_buffer import InputBuffer
from .input_translate import sdl_button_source
from .rpc import RpcError, method

_SECTION = "UI"

# DISPLAY-VALUE CONTRACT with the input_map page: the unbound sentinel and the
# keyboard-vs-controller separator are rendered verbatim by the panel and are pinned by
# the per-fork test suites. Do NOT "fix" them to ASCII - the no-em-dash house rule covers
# prose, not these two payload glyphs.
_UNBOUND = "—"          # em-dash: an unbound hotkey's value
_SEP = "  ·  "          # "  .  " middle-dot separator between keyboard and pad

# A hotkey value line:  Shortcuts\<Group>\<Action>\KeySeq=<value>   (NOT the \default twin,
# whose line is ...\KeySeq\default=...). Group/Action carry no '\' or '=' (%-encoded).
_ACT_RE = re.compile(r"(?m)^(Shortcuts\\([^\\=]+)\\([^\\=]+))\\KeySeq=")

# Keyboard: a captured ra_keyname -> a Qt modifier / key name for a QKeySequence.
_MODS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "meta": "Meta", "super": "Meta"}
_QT_KEY = {"space": "Space", "enter": "Return", "return": "Return", "escape": "Escape",
           "esc": "Escape", "tab": "Tab", "backspace": "Backspace", "delete": "Del",
           "insert": "Ins", "home": "Home", "end": "End", "pageup": "PgUp",
           "pagedown": "PgDown", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
           "minus": "-", "equal": "=", "plus": "+"}
# Controller: a captured SDL button-source name -> the fork's Controller_KeySeq token
# (best-effort Nintendo physical layout: A=right, B=bottom, X=top, Y=left).
_PAD_TOKEN = {"FaceEast": "A", "FaceSouth": "B", "FaceNorth": "X", "FaceWest": "Y",
              "LeftShoulder": "L", "RightShoulder": "R", "LeftTrigger": "ZL",
              "RightTrigger": "ZR", "Start": "Plus", "Back": "Minus", "Select": "Minus",
              "Guide": "Home", "DpadUp": "Dpad_Up", "DpadDown": "Dpad_Down",
              "DpadLeft": "Dpad_Left", "DpadRight": "Dpad_Right",
              "LeftStick": "Left_Stick", "RightStick": "Right_Stick"}


def _decode(s: str) -> str:
    return urllib.parse.unquote(s.replace("%20", " "))


def _is_flat(text: str) -> bool:
    """The newer Yuzu-fork flat-array shortcut store (shortcuts\\size=N)."""
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
    """A Controller_KeySeq token string from captured pad button-source names.
    sdl_button_source sign-prefixes trigger/axis sources (e.g. '+LeftTrigger'), so strip a
    leading +/-/~ before the token lookup (else ZL/ZR would never map)."""
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
    return _SEP.join(parts) if parts else _UNBOUND


def _flip_default(text: str, key: str) -> str:
    r = cfgutil.ini_replace(text, _SECTION, key + "\\default", "false")
    return r if r is not None else text


def _write_field(text: str, base: str, field: str, value: str) -> str | None:
    """Replace `<base>\\<field>` and flip its \\default twin. None if the field line is
    absent (never create a hotkey key that the fork didn't write)."""
    key = base + "\\" + field
    new = cfgutil.ini_replace(text, _SECTION, key, value)
    if new is None:
        return None
    return _flip_default(new, key)


class HotkeysEngine:
    """One instance per fork; constructing it registers <ns>.input_{get,set,clear,save,cancel}.

    an = the display name with its article ("a Citron" / "an Eden") - per-fork DATA so the
    grammar can never drift under a blanket rename."""

    def __init__(self, *, ns: str, display: str, an: str, file_getter, proc: str):
        self.ns, self.display, self.an = ns, display, an
        self._file, self._proc = file_getter, proc
        # Buffered editor plumbing (X=Save / Y=Cancel). Edits stage in THIS engine's
        # InputBuffer and only reach disk on <ns>.input_save; <ns>.input_cancel drops
        # them. ctx = () because the fork's hotkeys live in the single global
        # qt-config.ini [UI] section. CRITICAL: _flush re-reads FRESH disk and replays
        # only the [UI] hotkey edits, so the [Controls]/[System] blocks that the fork's
        # settings pages (also buffered) rewrite are never clobbered.
        self._ctx: tuple = ()
        self.buf = InputBuffer(load=self._load, apply_edit=self._apply_edit,
                               flush=self._flush)
        self._register()

    def _running(self) -> bool:
        return proc_guard.emulator_running(self._proc)

    # -- buffer callbacks -------------------------------------------------------
    def _load(self, ctx: tuple) -> str:
        f = self._file()
        if not f.is_file():
            raise RpcError("ENOENT",
                           f"{self.display} config not found at {f} - launch a game once")
        return f.read_text(encoding="utf-8", errors="replace")

    def _apply_edit(self, text: str, edit: dict):
        return self._apply(text, edit), edit

    def _flush(self, ctx: tuple, disk: str, edits: list) -> str:
        f = self._file()
        if not f.is_file():
            raise RpcError("ENOENT", f"{self.display} config not found at {f}")
        text = f.read_text(encoding="utf-8", errors="replace")   # replay onto FRESH disk
        for edit in edits:
            text = self._apply(text, edit)
        cfgutil.ensure_bak(f)
        cfgutil.atomic_write(f, text)
        return text

    # -- pure edit application --------------------------------------------------
    def _apply(self, text: str, edit: dict) -> str:
        """Apply one staged edit to `text`, returning the new text. Pure (no I/O, no
        bump). Replayed verbatim by the buffer's flush onto a fresh disk read. Refuses
        while the fork runs (it rewrites its config on exit) and on the read-only
        flat-array format, so both guards fire at stage AND at save."""
        if self._running():
            raise RpcError("EBUSY",
                           f"close {self.display} first - it rewrites its config on exit")
        if _is_flat(text):
            raise RpcError("EINVAL",
                           f"this {self.display} build's hotkeys are read-only in MAD - "
                           f"change them in {self.display}'s Configure > Hotkeys dialog.")
        if edit.get("op") == "clear":
            return self._apply_clear(text, edit)
        return self._apply_set(text, edit)

    def _apply_set(self, text: str, edit: dict) -> str:
        """New text for a hotkey remap: derive KeySeq (keyboard) + Controller_KeySeq (pad)
        from the captured codes and rewrite ONLY those [UI] fields (flipping each
        \\default). Pure."""
        base = edit.get("id", "")
        if base not in {b for b, _, _ in _actions(text)}:
            raise RpcError("EINVAL", f"{base!r} is not {self.an} hotkey action")
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
            raise RpcError("EINVAL", f"that input can't be bound as {self.an} hotkey")
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

    def _apply_clear(self, text: str, edit: dict) -> str:
        """New text for clearing a hotkey: blank both [UI] fields. Pure."""
        base = edit.get("id") or edit.get("key") or ""
        if base not in {b for b, _, _ in _actions(text)}:
            raise RpcError("EINVAL", f"{base!r} is not {self.an} hotkey action")
        new = text
        for field in ("KeySeq", "Controller_KeySeq"):
            w = _write_field(new, base, field, "")
            if w is not None:
                new = w
        return new

    # -- RPC bodies -------------------------------------------------------------
    def _do_input_get(self, params):
        text = self.buf.get(self._ctx)       # buffer-over-disk: reflects staged edits
        run = self._running()
        if _is_flat(text):
            # Newer flat-array format: enumerate read-only (name/group/keyseq/controller_keyseq).
            binds = []
            try:
                n = int(cfgutil.ini_read(text, _SECTION, "shortcuts\\size") or "0")
            except ValueError:
                n = 0
            for i in range(1, n + 1):
                name = _decode(cfgutil.ini_read(text, _SECTION, f"shortcuts\\{i}\\name") or "")
                kb = (cfgutil.ini_read(text, _SECTION, f"shortcuts\\{i}\\keyseq") or "").strip()
                pad = (cfgutil.ini_read(text, _SECTION,
                                        f"shortcuts\\{i}\\controller_keyseq") or "").strip()
                val = _SEP.join(p for p in (kb, pad) if p) or _UNBOUND
                binds.append({"id": f"flat:{i}", "label": name or f"#{i}", "kind": "chord",
                              "value": val, "capturable": False})
            return {"running": run,
                    "note": f"This {self.display} build uses a newer hotkey format - view "
                            f"only here; change hotkeys in {self.display}'s Configure > "
                            "Hotkeys dialog.",
                    "groups": [{"title": "Hotkeys (read-only)", "binds": binds}],
                    "clearable": False, "buffered": True, "dirty": self.buf.dirty}

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
        note = (f"Close {self.display} first - it rewrites its config on exit." if run else
                "Bind each action to a keyboard key/combo and/or a controller button "
                "(shown as keyboard · controller). Highlight a row and press Start to "
                "clear it. Controller tokens are best-effort - verify in "
                f"{self.display} if a mapping looks off.")
        return {"running": run, "note": note, "groups": groups, "clearable": True,
                "buffered": True, "dirty": self.buf.dirty}

    def _register(self) -> None:
        @method(f"{self.ns}.input_get", slow=True)   # buffered: NO cache= - the buffer is truth
        def _input_get(params, _s=self):
            return _s._do_input_get(params)

        @method(f"{self.ns}.input_set", slow=True)
        def _input_set(params, _s=self):
            base = params.get("id", "")
            codes = params.get("codes")
            if codes is None and str(params.get("value", "")).strip():
                try:
                    codes = [int(params.get("value"))]
                except (TypeError, ValueError):
                    codes = None
            if not codes:
                raise RpcError("EINVAL", "press a key or button (or hold a combo)")
            _s.buf.set(_s._ctx, {"op": "set", "id": base, "codes": list(codes)})  # stage only
            new = _s.buf.working
            return {"id": base, "value": _render(new, base), "dirty": _s.buf.dirty,
                    "message": f"{_decode(base.rsplit(chr(92), 1)[-1])} -> {_render(new, base)}"}

        @method(f"{self.ns}.input_clear", slow=True)
        def _input_clear(params, _s=self):
            base = params.get("id") or params.get("key") or ""
            _s.buf.set(_s._ctx, {"op": "clear", "id": base})                      # stage only
            return {"id": base, "value": _UNBOUND, "dirty": _s.buf.dirty,
                    "message": "hotkey cleared"}

        @method(f"{self.ns}.input_save", slow=True)
        def _input_save(params, _s=self):
            return {"saved": _s.buf.save(_s._ctx), "dirty": _s.buf.dirty}

        @method(f"{self.ns}.input_cancel", slow=True)
        def _input_cancel(params, _s=self):
            _s.buf.cancel(_s._ctx)
            return {"cancelled": True, "dirty": _s.buf.dirty}
