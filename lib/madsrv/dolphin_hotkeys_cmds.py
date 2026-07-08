r"""dolphin_hk.* -- Dolphin (Wii / GameCube) Hotkeys REMAPPER.

Rebinds each [Hotkeys] `Category/Action` to a captured controller button or chord, writing a
Dolphin device-qualified expression. Source-verified (Dolphin master 485f5cae): a control token is
`` `evdev/0/<StripWhitespace(name)>:<control-name>` `` -- the control NAME (SOUTH/EAST/TL/THUMBL…,
libevdev code minus BTN_/KEY_), NOT the raw evdev code; index 0 resolves a uniquely-named evdev pad;
a chord of simultaneously-held buttons is `@(t1+t2)`; backticks are required (the token has `/` + `:`).

SCOPE: PAD buttons only. Keyboard keys are a separate XInput2 device in Dolphin, but MAD captures via
evdev, so a captured keyboard code can't be cleanly qualified -> rejected with a note (set those in
Dolphin). The chord capture emits held BUTTONS only (no axis / d-pad). Byte-preserving (rewrites only
the one action's value line, never creates a key), buffered X=Save / Y=Cancel, refuses while Dolphin
runs (it rewrites its config on exit). The captured device is forwarded by the fork's
GuiMadPageEmuInputMap.setChord (`device` in the input_set payload).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import capture_cmds, cfgutil
from .input_buffer import InputBuffer
from .input_translate import dolphin_gc_button
from .rpc import RpcError, method

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
_FILE = _DIR / "Hotkeys.ini"
_SECTION = "Hotkeys"
_PROC = "dolphin"


def _hotkeys(text: str) -> list[tuple[str, str, str]]:
    """(key_name, category, action) for each 'Category/Action = …' line in [Hotkeys], file order.
    Skips the 'Device =' header line."""
    span = cfgutil._ini_span(text, _SECTION)
    if not span:
        return []
    body = text[span[0]:span[1]]
    out = []
    for line in body.splitlines():
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        name = line.partition("=")[0].strip()
        if not name or name == "Device":
            continue
        cat, _, act = name.partition("/")
        out.append((name, cat if act else "General", act or cat))
    return out


def _token(dev_name: str, ctrl: str) -> str:
    """A backtick-wrapped device-qualified control token `evdev/0/<name>:<ctrl>`."""
    return f"`evdev/0/{dev_name.strip()}:{ctrl}`"


def _expr_from(codes, dev_name: str) -> str:
    """Build the Dolphin hotkey expression from captured pad codes + the emitting device name.
    Pure. Raises EINVAL if any code is not a mappable PAD button (keyboard / unknown)."""
    if not (dev_name or "").strip():
        raise RpcError("EINVAL", "couldn't identify the controller — try the capture again")
    tokens = []
    for c in codes or []:
        try:
            ci = int(c)
        except (TypeError, ValueError):
            continue
        ctrl = dolphin_gc_button(ci, sdl=False)          # evdev control name for a pad button
        if ctrl is None:
            if capture_cmds.ra_keyname(ci):
                raise RpcError("EINVAL", "keyboard hotkeys can't be set here — Dolphin binds the "
                                         "keyboard as a separate device; set a controller button.")
            raise RpcError("EINVAL", "that input can't be bound — press a controller button")
        tokens.append(_token(dev_name, ctrl))
    if not tokens:
        raise RpcError("EINVAL", "press a controller button (or hold a combo)")
    return tokens[0] if len(tokens) == 1 else "@(" + "+".join(tokens) + ")"


@method("dolphin_hk.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    text = _buf.get(_CTX)
    run = proc_guard.emulator_running(_PROC)
    groups: dict[str, list] = {}
    order: list[str] = []
    for name, cat, act in _hotkeys(text):
        cur = (cfgutil.ini_read(text, _SECTION, name) or "").strip()
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append({"id": name, "label": act, "kind": "chord",
                            "value": cur or "—", "capturable": not run})
    note = ("Close Dolphin first — it rewrites its config on exit." if run else
            "Press a controller button or hold a combo to rebind. Keyboard hotkeys must be set in "
            "Dolphin (it binds the keyboard as a separate device). Highlight a row + Start to clear.")
    return {"running": run, "note": note, "clearable": True,
            "groups": [{"title": c, "binds": groups[c]} for c in order],
            "buffered": True, "dirty": _buf.dirty}


@method("dolphin_hk.input_set", slow=True)
def _input_set(params):
    name = params.get("id", "")
    codes = params.get("codes")
    if codes is None and str(params.get("value", "")).strip():
        try:
            codes = [int(params.get("value"))]
        except (TypeError, ValueError):
            codes = None
    if not codes:
        raise RpcError("EINVAL", "press a controller button (or hold a combo)")
    expr = _expr_from(codes, params.get("device", ""))   # validation (pure); may raise EINVAL
    _buf.set(_CTX, {"op": "set", "id": name, "expr": expr})
    new = _buf.working
    return {"id": name, "value": (cfgutil.ini_read(new, _SECTION, name) or "—").strip() or "—",
            "dirty": _buf.dirty, "message": f"{name.rsplit('/', 1)[-1]} ← {expr}"}


@method("dolphin_hk.input_clear", slow=True)
def _input_clear(params):
    name = params.get("id") or params.get("key") or ""
    _buf.set(_CTX, {"op": "clear", "id": name})
    return {"id": name, "value": "—", "dirty": _buf.dirty, "message": "hotkey cleared"}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). ctx = () — Hotkeys.ini is a single global file.
# _flush replays the staged edits onto a FRESH disk read (byte-preserving, line-endings kept).
# ---------------------------------------------------------------------------
_CTX: tuple = ()


def _apply(text: str, edit: dict) -> str:
    """Apply one staged hotkey edit to `text`. Pure. Refuses while Dolphin runs (fires at BOTH
    stage and save-replay). Rewrites ONLY the one action's value line; never creates a key."""
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Dolphin first — it rewrites its config on exit")
    name = edit.get("id", "")
    if name not in {n for n, _, _ in _hotkeys(text)}:
        raise RpcError("EINVAL", f"{name!r} is not a Dolphin hotkey")
    value = "" if edit.get("op") == "clear" else edit.get("expr", "")
    nt = cfgutil.ini_replace(text, _SECTION, name, value)
    if nt is None:
        raise RpcError("ENOKEY", f"{name!r} not present in [{_SECTION}]")
    return nt


def _load(ctx: tuple) -> str:
    text = cfgutil.read_text(_FILE)          # newline="" -> preserve line endings
    if text is None:
        raise RpcError("ENOENT", f"Hotkeys.ini not found at {_FILE} — launch a game once")
    return text


def _apply_edit(text: str, edit: dict):
    return _apply(text, edit), edit


def _flush(ctx: tuple, disk: str, edits: list) -> str:
    text = cfgutil.read_text(_FILE)          # replay onto FRESH disk
    if text is None:
        raise RpcError("ENOENT", f"Hotkeys.ini not found at {_FILE}")
    for edit in edits:
        text = _apply(text, edit)
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, text)
    return text


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("dolphin_hk.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_CTX), "dirty": _buf.dirty}


@method("dolphin_hk.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_CTX)
    return {"cancelled": True, "dirty": _buf.dirty}
