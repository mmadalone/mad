r"""Shared Yuzu-fork PER-GAME settings engine (Citron / Eden -- byte-format-identical qt-config).

A key inherits the global value when `key\use_global` is true/absent; it is overridden by the
triple `key\use_global=false` + `key\default=false` + `key=<value>`. The fork tolerates a PARTIAL
custom ini (an absent key defaults to use_global=true -> inherit, frontend_common/config.cpp
ReadSettingGeneric), so we CREATE the file on demand writing ONLY the overrides -- no need to first
open the game's Properties in the emulator.

Rendered inherit-aware by GuiMadPageEmuSettings (option[0] == "Inherit global"):
  * enum -> a stepper whose option[0] is "Inherit global"; picking it clears the override triple.
  * bool -> a 3-way Inherit / Off / On.
  * int/float -> the numeric stepper's "Inherit global" slot (backend sends inherit:true +
    inherited:<bool>; a set of the string "inherit" clears it).

The engine is FORMAT-ONLY: the caller supplies the descriptor GROUPS (Citron and Eden keep their
OWN groups -- their enum indices diverge, so the descriptors must NOT be shared), a
pergame_path_fn, a running check, and the emulator label/note. Instant save; refuses while the
emulator runs (it rewrites config on exit).
"""
from __future__ import annotations

import re

from .. import staterev
from . import cfgutil
from .rpc import RpcError

_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")               # anti-traversal
_INHERIT_TRUE = {"true", "1", "yes", "on"}
# spaces tolerated: MAD-created files use `key = value` (ini_set_or_insert), emulator-authored
# ones may use `key=value`. Detection must match both.
_HAS_OVERRIDE_RE = re.compile(r"\\use_global\s*=\s*false")


def has_override(text: str | None) -> bool:
    """True if the per-game ini carries at least one settings override (a `\\use_global = false`
    line, spaces tolerated). The picker's `* custom` badge + summary key off this."""
    return bool(text) and bool(_HAS_OVERRIDE_RE.search(text))


def tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def item_by_key(groups: list, key: str) -> dict | None:
    for g in groups:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


# ── the per-game override VALUE for a key, or None when inherited/absent ──────
def _override(pg_text: str | None, sec: str, key: str) -> str | None:
    if pg_text is None:
        return None
    ug = cfgutil.ini_read(pg_text, sec, key + "\\use_global")
    if ug is None or ug.strip().lower() in _INHERIT_TRUE:
        return None
    return cfgutil.ini_read(pg_text, sec, key)


# ── inherit-aware enum view (Inherit global at index 0) ──────────────────────
def _curated_index(item: dict, raw: str) -> int | None:
    if item.get("write_mode") == "option":
        stored = list(item.get("options_stored") or item.get("options_display") or [])
        return stored.index(raw) if raw in stored else None
    try:
        i = int(float(raw))
    except (TypeError, ValueError):
        return None
    disp = item.get("options_display") or item.get("options_stored") or []
    return i if 0 <= i < len(disp) else None


def _enum_view(item: dict, raw: str | None) -> tuple[list[str], int]:
    disp0 = list(item.get("options_display") or item.get("options_stored") or [])
    options = ["Inherit global"] + disp0
    if raw is None:
        return options, 0
    ci = _curated_index(item, raw)
    if ci is not None:
        return options, 1 + ci
    return options + [f"(current: {raw})"], len(options)


def _enum_stored(item: dict, real: int, pg_raw: str | None) -> str | None:
    disp = item.get("options_display") or item.get("options_stored") or []
    if 0 <= real < len(disp):
        if item.get("write_mode") == "option":
            stored = list(item.get("options_stored") or item.get("options_display") or [])
            return stored[real]
        return str(real)
    return pg_raw


def is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


# ── inherit-aware row rendering (SHAPE only) ─────────────────────────────────
def render_item(it: dict, raw: str | None) -> dict | None:
    """One inherit-aware row from an override RAW string (or None == inherit). SHARED: the
    ini-marker forks (Citron/Eden, via read_item) AND Ryujinx (raw from its pin-map) render the
    same payload shape -- enum with "Inherit global" at index 0 / bool 3-way / numeric inherit
    slot -- so the C++ GuiMadPageEmuSettings treats them identically."""
    key, typ = it["key"], it["type"]
    if typ == "bool":
        val = 0 if raw is None else (2 if cfgutil.bool_get(it, raw) else 1)
        return {"key": key, "label": it["label"], "type": "enum",
                "options": ["Inherit global", "Off", "On"], "value": val}
    if typ == "enum":
        options, value = _enum_view(it, raw)
        return {"key": key, "label": it["label"], "type": "enum", "options": options, "value": value}
    if typ in ("int", "float"):
        out_type = "int" if typ == "int" else "float"
        if raw is None:
            value = it.get("min", 0)
        else:
            try:
                value = int(float(raw)) if typ == "int" else float(raw)
            except (TypeError, ValueError):
                value = it.get("min", 0)
        row = {"key": key, "label": it["label"], "type": out_type, "value": value,
               "inherit": True, "inherited": raw is None}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def read_item(pg_text: str | None, it: dict) -> dict | None:
    """Inherit-aware row for the ini-marker forks: raw = the per-game override or None (inherit)."""
    return render_item(it, _override(pg_text, it["section"], it["key"]))


# ── write one override (create-on-demand triple) / clear to inherit ──────────
def _ensure_section(text: str, section: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    if cfgutil._ini_span(text, section) is not None:
        return text
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + f"[{section}]\n"


def clear(text: str, sec: str, key: str) -> str:
    for suffix in ("", "\\default", "\\use_global"):
        text = cfgutil.ini_remove(text, sec, key + suffix)
    return cfgutil.ini_drop_empty_section(text, sec)


def set_override(text: str, sec: str, key: str, stored: str) -> str:
    text = _ensure_section(text, sec)
    text = cfgutil.ini_set_or_insert(text, sec, key + "\\use_global", "false")
    text = cfgutil.ini_set_or_insert(text, sec, key + "\\default", "false")
    text = cfgutil.ini_set_or_insert(text, sec, key, stored)
    return text


def write_item(text: str, it: dict, value) -> str:
    sec, typ, key = it["section"], it["type"], it["key"]
    if typ in ("int", "float"):
        if is_inherit(value):
            return clear(text, sec, key)
        stored = cfgutil.compute_write(it, value, _override(text, sec, key) or "")
        return set_override(text, sec, key, stored)
    # bool / enum: value is a display index; 0 (or "inherit") clears the override
    if is_inherit(value):
        return clear(text, sec, key)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:
        return clear(text, sec, key)
    if typ == "bool":
        stored = it.get("bool_true", "true") if n >= 2 else it.get("bool_false", "false")
    else:
        stored = _enum_stored(it, n - 1, _override(text, sec, key))
        if stored is None:
            raise RpcError("EINVAL", f"index {n} out of range for {key}")
    return set_override(text, sec, key, stored)


# ── get / set per page (instant save, create-on-demand) ──────────────────────
def pergame_get(groups: list, pg_text: str | None, note: str, running: bool) -> dict:
    out = []
    for g in groups:
        settings = [row for it in g["items"] if (row := read_item(pg_text, it))]
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # exists MUST be true (create-on-demand): the C++ hides all controls when exists=false.
    return {"exists": True, "running": running, "note": note, "groups": out}


def pergame_set(groups: list, params: dict, path_fn, running_fn, emu_label: str) -> dict:
    if running_fn():
        raise RpcError("EBUSY", f"close {emu_label} first — it rewrites its config on exit.")
    titleid = tid(params)
    key = params["key"]
    it = item_by_key(groups, key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    pg = path_fn(titleid)
    text = cfgutil.read_text(pg) or ""
    new = write_item(text, it, params["value"])
    if new != text:
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)                          # no-op when the file is new
        cfgutil.atomic_write(pg, new)
        staterev.bump("config")
    row = read_item(new, it)
    return {"key": key, "value": row["value"] if row else 0}
