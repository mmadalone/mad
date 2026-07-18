"""Console-agnostic helpers shared by the Dolphin controller-mapping editors
(GameCube: dolphin_gc_input_cmds; Wii: dolphin_wii_input_cmds).

Every function here is pure text / fixed-table work — no I/O beyond reading the
`text` argument. A binding "section" is a Dolphin config block ([GCPadN] /
[WiimoteN] live, or [Profile] in a saved profile file); the same key schema and
token vocabulary apply regardless, so the same capture->token logic serves both
consoles. Callers supply their own per-console key-group sets and d-pad row map.

Token vocabulary follows the target section's Device backend: evdev/... -> evdev
names, anything else (SDL/...) -> SDL semantic names. Sticks/analog-triggers use
Dolphin's legacy `Axis <rank><sign>` / `Full Axis <rank>+` form (resolves on both
backends). A d-pad direction MIRRORS the section's existing D-Pad token (the
device-specific token can't be synthesized).
"""
from __future__ import annotations

from . import cfgutil
from .input_translate import (axis_token_rank, dolphin_gc_axis_token, dolphin_gc_button,
                              hat_token_parts, parse_axis_token)
from .rpc import RpcError


def is_sdl(text: str, sec: str) -> bool:
    """A slot's button vocabulary follows its Device backend: evdev/... -> evdev
    names, anything else (SDL/...) -> SDL semantic names."""
    return not (cfgutil.ini_read(text, sec, "Device") or "").startswith("evdev/")


def fmt_token(tok: str) -> str:
    """Serialize a control token the way Dolphin does: bare iff every char is ASCII
    alpha, else backtick-wrapped."""
    return tok if (tok and tok.isascii() and tok.isalpha()) else f"`{tok}`"


def token_label(raw: str | None) -> str:
    if not raw:
        return "(unbound)"
    r = raw.strip()
    if len(r) >= 2 and r[0] == "`" and r[-1] == "`" and "`" not in r[1:-1]:
        return r[1:-1] or "(unbound)"
    return r


def dpad_mirror(hat_token: str, text: str, sec: str, dpad_row_for_dir: dict) -> str:
    """The token for a captured physical d-pad direction: the slot's EXISTING D-Pad
    binding for that direction (device-specific tokens can't be synthesized, so we
    re-use what already works)."""
    parts = hat_token_parts(hat_token)
    if parts is None:
        raise RpcError("EINVAL", "press a d-pad direction")
    existing = cfgutil.ini_read(text, sec, dpad_row_for_dir[parts[1]])
    if not existing or not existing.strip():
        raise RpcError("EINVAL", "this pad's d-pad isn't bound yet — set it in Dolphin first")
    return existing.strip()                     # already Dolphin-formatted -> written verbatim


def token_for(key: str, kind: str, params, text: str, sec: str, *,
              button_keys: set, dpad_keys: set, stick_keys: set, trigger_keys: set,
              dpad_row_for_dir: dict) -> str:
    """Resolve one captured input to its Dolphin token, dispatching by control group
    + capture kind. Raises EINVAL on a mismatch (pure; no I/O beyond reading `text`)."""
    if key in button_keys or (key in dpad_keys and kind == "btn"):
        try:
            code = int(params["value"])
        except (KeyError, ValueError, TypeError):
            raise RpcError("EINVAL", "missing or invalid button code")
        tok = dolphin_gc_button(code, is_sdl(text, sec))
        if tok is None:
            raise RpcError("EINVAL", "that button can't be mapped — press a face, shoulder, L/R, "
                                     "Z, Start, Select, Guide or stick-click button")
        return tok
    if key in stick_keys or key in trigger_keys or (key in dpad_keys and kind == "axis"):
        val = str(params.get("value", ""))
        parsed = parse_axis_token(val)
        rank = axis_token_rank(val)
        if parsed is None or rank is None:
            raise RpcError("EINVAL", "push the stick the way the row says (or pull the trigger)")
        sign, _canonical = parsed
        return dolphin_gc_axis_token(sign, rank, trigger=key in trigger_keys)
    if key in dpad_keys:                        # a captured physical d-pad (hat)
        return dpad_mirror(str(params.get("value", "")), text, sec, dpad_row_for_dir)
    raise RpcError("EINVAL", f"{key!r} is not a remappable input")
