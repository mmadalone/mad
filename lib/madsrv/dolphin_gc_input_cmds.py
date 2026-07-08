"""dolphin.input_* -- GameCube pad remap (Dolphin GCPadNew.ini) for the "Wii / GameCube"
tile's GameCube -> Button mapping page.

Each [GCPad1..4] section = one GameCube port. A remap replaces ONLY the value (RHS) of one
binding line inside the selected port's section, keeping Device / *Calibration / Rumble /
*Modifier byte-for-byte. Tokens are written in the vocabulary of THAT slot's Device backend and
backtick-wrapped iff not all-ASCII-alpha (MappingCommon::GetExpressionForControl / IsAlpha):
  * BUTTONS (A/B/X/Y/Z/Start + L/R digital): evdev names (EAST…) vs SDL names (`Button E`…).
  * STICKS + analog TRIGGERS: the LEGACY `Axis <rank><sign>` / `Full Axis <rank>+` form, which is
    source-verified to resolve on BOTH evdev and SDL (Dolphin's SDL backend always adds legacy
    `Axis N` inputs alongside the recognized names). rank = captured axis rank among non-hat ABS
    axes (== Dolphin's axis index for sticks/triggers).
  * D-PAD: device-specific (BTN_DPAD `DPAD_*` vs ABS_HAT `Axis N` vs SDL `Pad *`/`Hat N`), so a
    captured d-pad direction MIRRORS the slot's existing D-Pad token for that physical direction
    (a captured button/stick on a d-pad row writes the button/axis token instead). On-device verify.

PROFILES: a "Load profile" selector copies a Profiles/GCPad/<name>.ini `[Profile]` body into the
selected [GCPadN] (lib/dolphin_profiles; byte-safe block replace).

Nothing writes GCPadNew.ini at launch (except the optional undocked-profile swap), so a remap
PERSISTS. Refused while Dolphin runs (it rewrites its config on exit). Buffered X=Save / Y=Cancel;
Start clears a binding. A per-game GameSettings/<id>.ini `[Controls] PadProfileN` can override this.
"""
from __future__ import annotations

from pathlib import Path

from .. import dolphin_profiles, proc_guard
from . import cfgutil
from .input_buffer import InputBuffer
from .input_translate import (axis_token_rank, dolphin_gc_axis_token, dolphin_gc_button,
                              hat_token_parts, parse_axis_token)
from .rpc import RpcError, method

_FILE = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/GCPadNew.ini"
_PROC = "dolphin"
_PORTS = (1, 2, 3, 4)

_BUTTONS = [("Buttons/A", "A"), ("Buttons/B", "B"), ("Buttons/X", "X"), ("Buttons/Y", "Y"),
            ("Buttons/Z", "Z"), ("Buttons/Start", "Start"),
            ("Triggers/L", "L (digital)"), ("Triggers/R", "R (digital)")]
_DPAD = [("D-Pad/Up", "D-pad Up"), ("D-Pad/Down", "D-pad Down"),
         ("D-Pad/Left", "D-pad Left"), ("D-Pad/Right", "D-pad Right")]
_MAIN_STICK = [("Main Stick/Up", "Main stick Up"), ("Main Stick/Down", "Main stick Down"),
               ("Main Stick/Left", "Main stick Left"), ("Main Stick/Right", "Main stick Right")]
_C_STICK = [("C-Stick/Up", "C-stick Up"), ("C-Stick/Down", "C-stick Down"),
            ("C-Stick/Left", "C-stick Left"), ("C-Stick/Right", "C-stick Right")]
_TRIGGERS_ANALOG = [("Triggers/L-Analog", "L trigger (analog)"),
                    ("Triggers/R-Analog", "R trigger (analog)")]

_BUTTON_KEYS = {k for k, _ in _BUTTONS}
_DPAD_KEYS = {k for k, _ in _DPAD}
_STICK_KEYS = {k for k, _ in _MAIN_STICK + _C_STICK}
_TRIGGER_KEYS = {k for k, _ in _TRIGGERS_ANALOG}
_ALL_KEYS = _BUTTON_KEYS | _DPAD_KEYS | _STICK_KEYS | _TRIGGER_KEYS
_LABEL = dict(_BUTTONS + _DPAD + _MAIN_STICK + _C_STICK + _TRIGGERS_ANALOG)
_DPAD_ROW_FOR_DIR = {"up": "D-Pad/Up", "down": "D-Pad/Down",
                     "left": "D-Pad/Left", "right": "D-Pad/Right"}

# Section -> the profile name currently loaded into it, so the "Load profile" selector keeps showing
# the pick instead of snapping back to "— pick —" on the dependent rebuild. Cleared on cancel; KEPT
# after save (so the picker keeps showing the loaded profile — Dolphin records no per-port profile);
# reset only when the backend restarts. The profile's bindings are applied to the buffer's working
# copy, so the button/stick/d-pad rows below the picker reflect the picked profile immediately.
_pending: dict[str, str] = {}


def _section(player) -> str:
    return f"GCPad{player}"


def _ports_present(text: str) -> list[int]:
    return [n for n in _PORTS if cfgutil._ini_span(text, _section(n))]


def _is_sdl(text: str, sec: str) -> bool:
    """A slot's button vocabulary follows its Device backend: evdev/… -> evdev names,
    anything else (SDL/…) -> SDL semantic names."""
    return not (cfgutil.ini_read(text, sec, "Device") or "").startswith("evdev/")


def _fmt_token(tok: str) -> str:
    """Serialize a control token the way Dolphin does: bare iff every char is ASCII alpha,
    else backtick-wrapped."""
    return tok if (tok and tok.isascii() and tok.isalpha()) else f"`{tok}`"


def _token_label(raw: str | None) -> str:
    if not raw:
        return "(unbound)"
    r = raw.strip()
    if len(r) >= 2 and r[0] == "`" and r[-1] == "`" and "`" not in r[1:-1]:
        return r[1:-1] or "(unbound)"
    return r


def _players_and_target(text: str, params) -> tuple[list[dict], str]:
    present = _ports_present(text) or [1]
    players = [{"id": str(n), "label": f"Player {n}"} for n in present]
    valid = {str(n) for n in present}
    sel = params.get("player") or str(present[0])
    if sel not in valid:
        sel = str(present[0])
    return players, sel


def _pad_name(text: str, sec: str) -> str:
    dev = cfgutil.ini_read(text, sec, "Device") or ""
    return dev.split("/", 2)[2] if dev.count("/") >= 2 else dev


@method("dolphin.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    text = _buf.get(_CTX)
    run = proc_guard.emulator_running(_PROC)
    players, player = _players_and_target(text, params)
    sec = _section(player)

    def row(key, label, kind):
        return {"id": key, "label": label, "kind": kind,
                "value": _token_label(cfgutil.ini_read(text, sec, key)),
                "capturable": not run}

    groups = [
        {"title": "Buttons", "binds": [row(k, l, "btn") for k, l in _BUTTONS]},
        {"title": "D-pad", "binds": [row(k, l, "hat") for k, l in _DPAD]},
        {"title": "Main stick", "binds": [row(k, l, "axis") for k, l in _MAIN_STICK]},
        {"title": "C-stick", "binds": [row(k, l, "axis") for k, l in _C_STICK]},
        {"title": "Analog triggers", "binds": [row(k, l, "trigger") for k, l in _TRIGGERS_ANALOG]},
    ]
    selectors = [{
        "key": "profile", "label": "Load profile", "scope": "player", "dependent": True,
        "options": [{"value": "", "label": "— pick a profile —"}]
                   + [{"value": n, "label": n} for n in dolphin_profiles.list_profiles()],
        "value": _pending.get(sec, ""),   # stay on the staged pick across the dependent rebuild
    }]
    name = _pad_name(text, sec)
    if run:
        note = "Close Dolphin first — it rewrites GCPadNew.ini on exit."
    else:
        note = ((f"Port {player}: {name}.  " if name else "") +
                "Press a button / stick / d-pad to rebind, Start to clear, or load a saved profile. "
                "Sticks use Dolphin's legacy Axis form; d-pad tokens are device-specific — verify in a game.")
    return {"running": run, "note": note, "groups": groups, "selectors": selectors,
            "clearable": True, "players": players, "player": player,
            "buffered": True, "dirty": _buf.dirty}


def _dpad_mirror(hat_token: str, text: str, sec: str) -> str:
    """The token for a captured physical d-pad direction: the slot's EXISTING D-Pad binding for
    that direction (device-specific tokens can't be synthesized, so we re-use what already works)."""
    parts = hat_token_parts(hat_token)
    if parts is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    existing = cfgutil.ini_read(text, sec, _DPAD_ROW_FOR_DIR[parts[1]])
    if not existing or not existing.strip():
        raise RpcError("EINVAL", "this pad's d-pad isn't bound yet — set it in Dolphin first")
    return existing.strip()                     # already Dolphin-formatted -> written verbatim


def _token_for(key: str, kind: str, params, text: str, sec: str) -> str:
    """Resolve one captured input to its Dolphin token, dispatching by control group + capture
    kind. Raises EINVAL on a mismatch (pure; no I/O beyond reading `text`)."""
    if key in _BUTTON_KEYS or (key in _DPAD_KEYS and kind == "btn"):
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        tok = dolphin_gc_button(code, _is_sdl(text, sec))
        if tok is None:
            raise RpcError("EINVAL", "that button can't be mapped — press a face, shoulder, L/R, "
                                     "Z, Start, Select, Guide or stick-click button")
        return tok
    if key in _STICK_KEYS or key in _TRIGGER_KEYS or (key in _DPAD_KEYS and kind == "axis"):
        val = str(params.get("value", ""))
        parsed = parse_axis_token(val)
        rank = axis_token_rank(val)
        if parsed is None or rank is None:
            raise RpcError("EINVAL", "push the stick the way the row says (or pull the trigger)")
        sign, _canonical = parsed
        return dolphin_gc_axis_token(sign, rank, trigger=key in _TRIGGER_KEYS)
    if key in _DPAD_KEYS:                       # a captured physical d-pad (hat)
        return _dpad_mirror(str(params.get("value", "")), text, sec)
    raise RpcError("EINVAL", f"{key!r} is not a remappable input")


@method("dolphin.input_set", slow=True)
def _input_set(params):
    key = params.get("id", "")
    kind = params.get("kind", "btn")
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    text = _buf.get(_CTX)
    _, player = _players_and_target(text, params)
    sec = _section(player)
    tok = _token_for(key, kind, params, text, sec)
    # A d-pad mirror reuses an EXISTING (already Dolphin-formatted) token -> write it verbatim;
    # every other capture is a fresh bare token that _fmt_token wraps.
    is_raw = key in _DPAD_KEYS and kind == "hat"
    write_value = tok if is_raw else _fmt_token(tok)
    _buf.set(_CTX, {"section": sec, "key": key, "value": write_value})
    display = _token_label(write_value)
    return {"id": key, "value": display, "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} ← {display}"}


@method("dolphin.input_clear", slow=True)
def _input_clear(params):
    """Start-to-clear a GameCube binding (blank the value = unbound)."""
    key = params.get("id") or params.get("key") or ""
    if key not in _ALL_KEYS:
        raise RpcError("EINVAL", f"{key!r} is not a remappable input")
    text = _buf.get(_CTX)
    _, player = _players_and_target(text, params)
    _buf.set(_CTX, {"section": _section(player), "key": key, "value": ""})
    return {"id": key, "value": "(unbound)", "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} cleared"}


@method("dolphin.selector_set", slow=True)
def _selector_set(params):
    """Load a named GCPad profile into the selected port (block-replace of the [GCPadN] body)."""
    if params.get("key") != "profile":
        raise RpcError("EINVAL", f"{params.get('key')!r} is not a selector here")
    name = params.get("value", "")
    text = _buf.get(_CTX)
    _, player = _players_and_target(text, params)
    sec = _section(player)
    if not name:                                # "— pick —": revert this port to its saved mapping
        if _pending.pop(sec, None) is not None:
            span = cfgutil._ini_span(_buf.disk, sec)
            orig = _buf.disk[span[0]:span[1]] if span else ""
            _buf.set(_CTX, {"op": "profile", "section": sec, "body": orig})
        return {"key": "profile", "value": "", "dirty": _buf.dirty,
                "message": f"Player {player} reverted to its saved mapping"}
    body = dolphin_profiles.profile_body(name)
    if body is None:
        raise RpcError("EINVAL", f"profile {name!r} not found")
    _buf.set(_CTX, {"op": "profile", "section": sec, "body": body})
    _pending[sec] = name
    return {"key": "profile", "value": name, "dirty": _buf.dirty,
            "message": f"loaded profile '{name}' into Player {player} (X to save)"}


# ---------------------------------------------------------------------------
# Buffered editor plumbing (X=Save / Y=Cancel). ctx = () — one global GCPadNew.ini.
# ---------------------------------------------------------------------------
_CTX: tuple = ()


def _apply(text: str, edit: dict) -> str:
    """Apply one staged edit to GCPadNew.ini `text`. Pure. Refuses while Dolphin runs (fires at
    BOTH stage and save-replay)."""
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "close Dolphin first — it rewrites GCPadNew.ini on exit")
    if edit.get("op") == "profile":
        nt = dolphin_profiles.apply_profile_body(text, edit["section"], edit["body"])
        if nt is None:
            raise RpcError("ENOKEY", f"[{edit['section']}] not present")
        return nt
    nt = cfgutil.ini_replace(text, edit["section"], edit["key"], edit.get("value", ""))
    if nt is None:
        raise RpcError("ENOKEY", f"{edit['key']!r} not present in [{edit['section']}]")
    return nt


def _load(ctx: tuple) -> str:
    # Consume a leftover dock swap (a crash-orphaned undocked-profile snapshot) so edits land on the
    # TRUE resting config, not a transient profile. No-op if none; skipped while Dolphin runs.
    try:
        from .. import dolphin_gc_dock
        if not proc_guard.emulator_running(_PROC):
            dolphin_gc_dock.restore()
    except Exception:
        pass
    # NOTE: do NOT clear _pending here — a no-op profile pick (choosing the profile a port already
    # matches) leaves the buffer non-dirty, which makes _buf.get reload via this path; clearing
    # _pending here would then snap the selector back to "— pick —". _pending is cleared on save/cancel.
    text = cfgutil.read_text(_FILE)
    if text is None:
        raise RpcError("ENOENT", f"GCPadNew.ini not found at {_FILE} — launch a game once")
    return text


def _apply_edit(text: str, edit: dict):
    return _apply(text, edit), edit


def _flush(ctx: tuple, disk: str, edits: list) -> str:
    text = cfgutil.read_text(_FILE)
    if text is None:
        raise RpcError("ENOENT", f"GCPadNew.ini not found at {_FILE}")
    for edit in edits:
        text = _apply(text, edit)
    cfgutil.ensure_bak(_FILE)
    cfgutil.atomic_write(_FILE, text)
    return text


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("dolphin.input_save", slow=True)
def _input_save(params):
    # KEEP _pending after save so the "Load profile" selector keeps showing the loaded profile
    # (Dolphin records no per-port profile, so this in-session memory is the only indicator).
    return {"saved": _buf.save(_CTX), "dirty": _buf.dirty}


@method("dolphin.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_CTX)
    _pending.clear()
    return {"cancelled": True, "dirty": _buf.dirty}
