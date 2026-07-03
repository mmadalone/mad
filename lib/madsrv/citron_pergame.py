r"""citron_pg_*.* — Citron (Switch) PER-GAME settings (System / CPU / Graphics / Adv. Graphics /
Audio / Linux), inherit-aware over ~/.config/citron/custom/<TITLEID>.ini.

Same override model as Eden: a key inherits the global value when `key\use_global` is true/absent,
and is overridden by the triple `key\use_global=false` + `key\default=false` + `key=<value>`. Citron
tolerates a PARTIAL custom ini (an absent key defaults to use_global=true -> inherit,
frontend_common/config.cpp ReadSettingGeneric), so we CREATE the file on demand writing only the
overrides -- no need to first open the game's Properties in Citron.

Rendered inherit-aware by GuiMadPageEmuSettings (opened per game via the GamePicker settingsmenu,
titleid in the context):
  * enum -> a stepper whose option[0] is "Inherit global"; picking it clears the override triple.
  * bool -> a 3-way Inherit / Off / On.
  * int/float -> the numeric stepper's "Inherit global" slot (backend sends inherit:true +
    inherited:<bool>; a set of the string "inherit" clears it).
The descriptor GROUPS are reused from citron_settings (same keys/enums as the global pages). Instant
save (each edit writes the custom ini); refuses while Citron runs (it rewrites config on exit).
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc_guard, staterev
from . import cfgutil, citron_games
from . import citron_settings as cs
from .rpc import RpcError, method

_PROC = "citron"
_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")               # anti-traversal
_INHERIT_TRUE = {"true", "1", "yes", "on"}

# Per-game pages: reuse the global descriptor groups; the per-game dialog has no "General" tab, so
# [Core] rides on System and [Linux] is its own Linux page (both come from GENERAL_GROUPS).
_CORE_GROUP = {**cs.GENERAL_GROUPS[0], "title": "Core / performance"}   # use_multi_core, speed_limit, memory_layout_mode
_LINUX_GROUP = cs.GENERAL_GROUPS[1]                                     # enable_gamemode

PG_PAGES = {
    "citron_pg_system": ("System", cs.SYSTEM_GROUPS + [_CORE_GROUP]),
    "citron_pg_cpu":    ("CPU", cs.CPU_GROUPS),
    "citron_pg_gfx":    ("Graphics", cs.GFX_GROUPS),
    "citron_pg_gfxadv": ("Adv. Graphics", cs.GFXADV_GROUPS),
    "citron_pg_audio":  ("Audio", cs.AUDIO_GROUPS),
    "citron_pg_linux":  ("Linux", [_LINUX_GROUP]),
}

_NOTE = ("Per-game overrides for Citron. Pick 'Inherit global' to clear an override so this game "
         "uses your global Citron setting. Each change saves instantly and only affects this game.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _tid(params) -> str:
    t = params.get("titleid") or ""
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _item_by_key(groups: list, key: str) -> dict | None:
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


def _is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


# ── read one inherit-aware row ────────────────────────────────────────────────
def _read_item(pg_text: str | None, it: dict) -> dict | None:
    sec, key, typ = it["section"], it["key"], it["type"]
    raw = _override(pg_text, sec, key)                    # None = inherit
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


# ── write one override (create-on-demand triple) / clear to inherit ──────────
def _ensure_section(text: str, section: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    if cfgutil._ini_span(text, section) is not None:
        return text
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + f"[{section}]\n"


def _clear(text: str, sec: str, key: str) -> str:
    for suffix in ("", "\\default", "\\use_global"):
        text = cfgutil.ini_remove(text, sec, key + suffix)
    return cfgutil.ini_drop_empty_section(text, sec)


def _set_override(text: str, sec: str, key: str, stored: str) -> str:
    text = _ensure_section(text, sec)
    text = cfgutil.ini_set_or_insert(text, sec, key + "\\use_global", "false")
    text = cfgutil.ini_set_or_insert(text, sec, key + "\\default", "false")
    text = cfgutil.ini_set_or_insert(text, sec, key, stored)
    return text


def _write_item(text: str, it: dict, value) -> str:
    sec, typ, key = it["section"], it["type"], it["key"]
    if typ in ("int", "float"):
        if _is_inherit(value):
            return _clear(text, sec, key)
        stored = cfgutil.compute_write(it, value, _override(text, sec, key) or "")
        return _set_override(text, sec, key, stored)
    # bool / enum: value is a display index; 0 (or "inherit") clears the override
    if _is_inherit(value):
        return _clear(text, sec, key)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {key}")
    if n <= 0:
        return _clear(text, sec, key)
    if typ == "bool":
        stored = it.get("bool_true", "true") if n >= 2 else it.get("bool_false", "false")
    else:
        stored = _enum_stored(it, n - 1, _override(text, sec, key))
        if stored is None:
            raise RpcError("EINVAL", f"index {n} out of range for {key}")
    return _set_override(text, sec, key, stored)


# ── get / set per page (instant save, create-on-demand) ──────────────────────
def _pergame_get(groups: list, titleid: str) -> dict:
    pg_text = cfgutil.read_text(citron_games.pergame_path(titleid))
    out = []
    for g in groups:
        settings = [row for it in g["items"] if (row := _read_item(pg_text, it))]
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    # exists MUST be true (create-on-demand): the C++ hides all controls when exists=false.
    return {"exists": True, "running": _running(), "note": _NOTE, "groups": out}


def _pergame_set(groups: list, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "close Citron first — it rewrites its config on exit.")
    titleid = _tid(params)
    key = params["key"]
    it = _item_by_key(groups, key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    pg = citron_games.pergame_path(titleid)
    text = cfgutil.read_text(pg) or ""
    new = _write_item(text, it, params["value"])
    if new != text:
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)                          # no-op when the file is new
        cfgutil.atomic_write(pg, new)
        staterev.bump("config")
    row = _read_item(new, it)
    return {"key": key, "value": row["value"] if row else 0}


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return _pergame_get(groups, _tid(params))

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        return _pergame_set(groups, params)


for _ns, (_title, _groups) in PG_PAGES.items():
    _register(_ns, _groups)
