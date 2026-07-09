"""dolphin_pg_*.* -- Dolphin (GameCube/Wii) PER-GAME settings overrides.

Writes ONLY the user's `GameSettings/<GameID>.ini` (partial overrides layered over the global config +
the bundled DB); the global Dolphin.ini/GFX.ini are NEVER touched. Reuses the GLOBAL descriptor tree
(`dolphin_settings`) but translates each item's real Config section to its per-game GameINI section
(Dolphin.ini [Core]->[Core]/[DSP]/[Display]; GFX.ini [Settings]->[Video_Settings],
[Enhancements]->[Video_Enhancements], [Hacks]->[Video_Hacks], [Hardware]->[Video_Hardware]) and drops
items whose section isn't per-game-overridable (e.g. [Interface]). Verified vs Dolphin
GameConfigLoader.cpp (deck-docs/dolphin-ini-encodings.md).

Every control is inherit-aware (presence-of-key = override, absent = inherit global):
  bool -> a 3-way enum [Inherit global, Off, On]; enum -> index 0 = Inherit global;
  int/float -> numeric with an "Inherit global" slot (C++ sends the string "inherit" to clear).
Instant-save (each change writes the per-game file, atomic + one-time .bak); refused while Dolphin runs.
Pages: dolphin_pg_general + dolphin_pg_gfx_general/_enh/_hacks/_adv (rendered by GuiMadPageEmuSettings
with ctxKey="titleid").
"""
from __future__ import annotations

import re

from .. import dolphin_gameids as gids
from .. import proc_guard, staterev
from . import cfgutil
from . import dolphin_settings as ds
from .rpc import RpcError, method

_PROC = "dolphin"
_ID_RE = re.compile(r"^[A-Z0-9]{6}$")
_NOTE = ("Per-game overrides for this game only. Pick 'Inherit global' to clear an override so the "
         "game uses your global GameCube/Wii setting. Nothing here touches the global config or "
         "other games. Changes save instantly.")

# real (file, Config-section) -> per-game GameINI section. Items whose (file, section) isn't here
# are NOT per-game-overridable and are dropped from the per-game pages.
_GAMEINI = {
    (ds.DOLPHIN, "Core"): "Core",
    (ds.DOLPHIN, "DSP"): "DSP",
    (ds.DOLPHIN, "Display"): "Display",
    (ds.GFX, "Settings"): "Video_Settings",
    (ds.GFX, "Enhancements"): "Video_Enhancements",
    (ds.GFX, "Hacks"): "Video_Hacks",
    (ds.GFX, "Hardware"): "Video_Hardware",
}


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip()
    if not _ID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


# ── translate the global descriptor tree to per-game (single-file) items ─────
def _pg_item(it: dict) -> dict | None:
    if it["type"] == "aa":                                # MSAA+SSAA composite -> [Video_Settings]
        return {**it, "section": "Video_Settings"}
    secs = it.get("sections") or [it["section"]]
    tsecs = [s for s in (_GAMEINI.get((it["file"], s)) for s in secs) if s]
    if not tsecs:
        return None                                       # not per-game-overridable
    out = {k: v for k, v in it.items() if k not in ("file", "create", "default", "sections")}
    out["section"] = tsecs[0]                             # primary write target
    if len(tsecs) > 1:
        out["sections"] = tsecs                           # read candidates (e.g. MaxAnisotropy)
    return out


def _pg_groups(*group_lists) -> list:
    out = []
    for groups in group_lists:
        for g in groups:
            items = [pi for it in g["items"] if (pi := _pg_item(it))]
            if items:
                out.append({"title": g["title"], "note": g.get("note", ""), "items": items})
    return out


# "General" = the per-game-overridable Core / CPU / Clock / Audio settings (Interface drops out:
# [Interface] is not a per-game section). The 4 Graphics tabs mirror the global GFX pages.
_GENERAL = _pg_groups(ds.GENERAL_GROUPS, ds.ADVANCED_GROUPS, ds.AUDIO_GROUPS)
_GFX_GENERAL = _pg_groups(ds.GFX_GENERAL_GROUPS)
_GFX_ENH = _pg_groups(ds.GFX_ENH_GROUPS)
_GFX_HACKS = _pg_groups(ds.GFX_HACKS_GROUPS)
_GFX_ADV = _pg_groups(ds.GFX_ADV_GROUPS)

PAGES = {
    "dolphin_pg_general":     ("General", _GENERAL),
    "dolphin_pg_gfx_general": ("Graphics: General", _GFX_GENERAL),
    "dolphin_pg_gfx_enh":     ("Graphics: Enhancements", _GFX_ENH),
    "dolphin_pg_gfx_hacks":   ("Graphics: Hacks", _GFX_HACKS),
    "dolphin_pg_gfx_adv":     ("Graphics: Advanced", _GFX_ADV),
}


# ── inherit-aware enum view ───────────────────────────────────────────────────
def _curated_index(it: dict, raw: str) -> int | None:
    raw = raw.strip()
    if it.get("write_mode") == "option":
        stored = list(it.get("options_stored") or it.get("options_display") or [])
        return stored.index(raw) if raw in stored else None
    try:
        i = int(float(raw))
    except (TypeError, ValueError):
        return None
    disp = it.get("options_display") or it.get("options_stored") or []
    return i if 0 <= i < len(disp) else None


def _enum_view(it: dict, raw: str | None) -> tuple[list[str], int]:
    disp = list(it.get("options_display") or it.get("options_stored") or [])
    options = ["Inherit global"] + disp
    if raw is None:
        return options, 0
    ci = _curated_index(it, raw)
    if ci is not None:
        return options, 1 + ci
    return options + [f"(current: {raw})"], len(options)


def _enum_stored(it: dict, real: int) -> str | None:
    disp = it.get("options_display") or it.get("options_stored") or []
    if 0 <= real < len(disp):
        if it.get("write_mode") == "option":
            stored = list(it.get("options_stored") or it.get("options_display") or [])
            return stored[real]
        return str(real)                                  # index write_mode: stored int == index
    return None


# ── anti-aliasing composite (MSAA hex + SSAA bool), inherit-aware ─────────────
def _aa_read(text: str | None) -> dict:
    options = ["Inherit global"] + ds._AA_OPTIONS
    msaa = cfgutil.ini_read(text, "Video_Settings", "MSAA") if text else None
    ssaa = cfgutil.ini_read(text, "Video_Settings", "SSAA") if text else None
    if msaa is None and ssaa is None:
        return {"key": "_aa", "label": "Anti-aliasing", "type": "enum", "options": options, "value": 0}
    try:
        count = int((msaa or "1").strip(), 0)
    except (TypeError, ValueError):
        count = 1
    is_ssaa = (ssaa or "").strip().lower() in cfgutil._TRUE
    try:
        return {"key": "_aa", "label": "Anti-aliasing", "type": "enum",
                "options": options, "value": 1 + ds._AA_MAP.index((count, is_ssaa))}
    except ValueError:
        options = options + [f"{count}x {'SSAA' if is_ssaa else 'MSAA'} (current)"]
        return {"key": "_aa", "label": "Anti-aliasing", "type": "enum",
                "options": options, "value": len(options) - 1}


def _aa_write(text: str, value) -> str:
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad anti-aliasing index {value!r}")
    if idx <= 0:                                          # Inherit global -> drop both keys
        text = cfgutil.ini_remove(cfgutil.ini_remove(text, "Video_Settings", "MSAA"),
                                  "Video_Settings", "SSAA")
        return cfgutil.ini_drop_empty_section(text, "Video_Settings")
    if idx - 1 >= len(ds._AA_MAP):                        # the synthetic "(current)" slot -> no-op
        return text
    count, is_ssaa = ds._AA_MAP[idx - 1]
    text = _ensure_section(text, "Video_Settings")
    text = cfgutil.ini_set_or_insert(text, "Video_Settings", "MSAA", f"0x{count:08x}")
    return cfgutil.ini_set_or_insert(text, "Video_Settings", "SSAA", "True" if is_ssaa else "False")


# ── read / write one item (inherit-aware) ─────────────────────────────────────
def _candidates(it: dict) -> list:
    return it.get("sections") or [it["section"]]


def _find_sec(text: str, it: dict) -> str | None:
    for s in _candidates(it):
        if cfgutil.ini_read(text, s, it["key"]) is not None:
            return s
    return None


def _read_item(text: str | None, it: dict) -> dict | None:
    typ = it["type"]
    if typ == "aa":
        return _aa_read(text)
    sec = _find_sec(text, it) if text else None
    raw = cfgutil.ini_read(text, sec, it["key"]) if (text and sec) else None
    if typ == "bool":
        val = 0 if raw is None else (2 if cfgutil.bool_get(it, raw) else 1)
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": ["Inherit global", "Off", "On"], "value": val}
    if typ == "enum":
        options, value = _enum_view(it, raw)
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": options, "value": value}
    if typ in ("int", "float"):
        conv = int if typ == "int" else float
        if raw is None:
            value = conv(it.get("min", 0))
        else:
            try:
                value = conv(float(raw))
            except (TypeError, ValueError):
                value = conv(it.get("min", 0))
        row = {"key": it["key"], "label": it["label"], "type": typ, "value": value,
               "inherit": True, "inherited": raw is None}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def _ensure_section(text: str, sec: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    if cfgutil._ini_span(text, sec) is not None:
        return text
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + f"[{sec}]\n"


def _is_inherit(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "inherit"


def _clear(text: str, it: dict) -> str:
    for s in _candidates(it):
        text = cfgutil.ini_drop_empty_section(cfgutil.ini_remove(text, s, it["key"]), s)
    return text


def _write_item(text: str, it: dict, value) -> str:
    typ = it["type"]
    if _is_inherit(value):
        return _clear(text, it)
    sec = _find_sec(text, it) or it["section"]           # write the section already holding the key
    if typ in ("int", "float"):                          #   (else a version-drifted key duplicates)
        try:
            n = int(float(value)) if typ == "int" else float(value)
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad value {value!r} for {it['key']}")
        if "min" in it:
            n = max(it["min"], n)
        if "max" in it:
            n = min(it["max"], n)
        tok = str(n) if typ == "int" else cfgutil.fmt_float(n)
        return cfgutil.ini_set_or_insert(_ensure_section(text, sec), sec, it["key"], tok)
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad index {value!r} for {it['key']}")
    if idx <= 0:                                          # index 0 = Inherit global
        return _clear(text, it)
    if typ == "bool":
        tok = it.get("bool_true", "True") if idx >= 2 else it.get("bool_false", "False")
    else:
        disp = it.get("options_display") or it.get("options_stored") or []
        if idx - 1 >= len(disp):                          # the synthetic "(current: X)" slot -> no-op
            return text
        tok = _enum_stored(it, idx - 1)
        if tok is None:
            raise RpcError("EINVAL", f"index {idx} out of range for {it['key']}")
    return cfgutil.ini_set_or_insert(_ensure_section(text, sec), sec, it["key"], tok)


def _item_by_key(groups, key):
    for g in groups:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


# ── get / set ─────────────────────────────────────────────────────────────────
def _do_get(gid: str, groups) -> dict:
    text = cfgutil.read_text(gids.user_ini(gid))
    out = []
    for g in groups:
        settings = [row for it in g["items"] if (row := _read_item(text, it))]
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    return {"exists": True, "running": _running(), "note": _NOTE, "groups": out}


def _do_set(gid: str, groups, params) -> dict:
    if _running():
        raise RpcError("EBUSY", "Dolphin is running -- close it first (it rewrites config on exit).")
    key, value = params["key"], params["value"]
    path = gids.user_ini(gid)
    text = cfgutil.read_text(path) or ""
    if key == "_aa":
        new_text = _aa_write(text, value)
        echo_it = None
    else:
        it = _item_by_key(groups, key)
        if it is None:
            raise RpcError("EINVAL", f"{key!r} is not an editable per-game setting")
        new_text = _write_item(text, it, value)
        echo_it = it
    if new_text != (cfgutil.read_text(path) or ""):
        path.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(path)                          # no-op when the file is new
        cfgutil.atomic_write(path, new_text)
        staterev.bump("config")
    back = cfgutil.read_text(path)
    row = _aa_read(back) if key == "_aa" else _read_item(back, echo_it)
    return {"key": key, "value": row["value"] if row else value}


def _register(ns, groups):
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return _do_get(_tid(params), groups)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        return _do_set(_tid(params), groups, params)


for _ns, (_title, _groups) in PAGES.items():
    _register(_ns, _groups)
