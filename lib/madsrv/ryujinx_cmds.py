"""ryujinx.* — Ryujinx (Switch) settings editor: global AND per-game.

Ryujinx stores config as JSON (~/.config/Ryujinx/Config.json). GLOBAL edits write that file
directly. PER-GAME (`titleid`) also writes DIRECTLY: Ryujinx has NO per-key inherit -- its per-game
games/<titleid>/Config.json is a COMPLETE file that wholly replaces global (an absent key resets to a
compiled default, not global; source-verified against the Ryubing loader) -- so MAD edits that real
file in place, exactly like Ryujinx's own per-game config. A per-game file is an independent FROZEN
snapshot: it does NOT track later global changes (Ryujinx's own native behavior), which gives full
interop -- values set in Ryujinx are read + preserved, never clobbered. GET renders inherit-aware
("Inherit global" at index 0) by LIVE-diffing the file against global (no pin-map): a managed key
present AND differing from global is an override, everything else inherits. SET writes the one key
into the file (creating a complete global clone on first use, topping up any keys missing vs global);
"Inherit" copies the CURRENT global value back in; when no managed override remains a pure clone is
removed for a clean inherit, but a file that also holds Ryujinx-authored content or a per-game
input_config is KEPT (house rule #5). Only per-game-CAPABLE keys are in GROUPS (never the emulator's
(Global)-only rows).

NOTE: string enums MUST set write_mode:"option" so the stored token (e.g.
"Fixed16x9") round-trips; without it cfgutil read every string enum as index 0.
"""
from __future__ import annotations

import copy
import json

from .. import fsutil, proc_guard
from . import cfgutil, ryujinx_json
from . import yuzu_pergame as yp   # shared inherit-aware row renderer (render_item / tid / is_inherit)
from .rpc import RpcError, method

_PROC = "ryujinx"
_F = ryujinx_json.CONFIG.name   # "Config.json"
_LABEL = "Ryujinx graphics"
_GAMES_DIR = ryujinx_json.CONFIG.parent / "games"

# ── descriptor helpers (file always Config.json, section always "" = top-level JSON key) ──
def _bool(key, label):
    return {"key": key, "label": label, "file": _F, "section": "", "type": "bool",
            "bool_true": "true", "bool_false": "false"}


def _enum(key, label, display, stored, *, stored_int=False):
    it = {"key": key, "label": label, "file": _F, "section": "", "type": "enum",
          "write_mode": "option", "options_display": display, "options_stored": stored}
    if stored_int:
        it["stored_int"] = True
    return it


def _int(key, label, lo, hi, step=1):
    return {"key": key, "label": label, "file": _F, "section": "", "type": "int",
            "min": lo, "max": hi, "step": step}


def _float(key, label, lo, hi, step):
    return {"key": key, "label": label, "file": _F, "section": "", "type": "float",
            "min": lo, "max": hi, "step": step}


# Enum token sets are Ryubing-source-verified (deck-docs/ryubing-config.md). String enums store
# the EXACT member NAME (bad casing silently reverts to member 0); vsync_mode/dram_size are INT
# enums whose stored value IS the index (stored_int -> Config.json holds the integer).
_REGION = ["Japan", "USA", "Europe", "Australia", "China", "Korea", "Taiwan"]
_LANGUAGE_D = ["Japanese", "American English", "French", "German", "Italian", "Spanish",
               "Chinese", "Korean", "Dutch", "Portuguese", "Russian", "Taiwanese",
               "British English", "Canadian French", "Latin American Spanish",
               "Simplified Chinese", "Traditional Chinese", "Brazilian Portuguese"]
_LANGUAGE_S = ["Japanese", "AmericanEnglish", "French", "German", "Italian", "Spanish",
               "Chinese", "Korean", "Dutch", "Portuguese", "Russian", "Taiwanese",
               "BritishEnglish", "CanadianFrench", "LatinAmericanSpanish",
               "SimplifiedChinese", "TraditionalChinese", "BrazilianPortuguese"]

# GROUPS is the SHARED managed-key registry for BOTH the granular global pages (ryujinx_settings
# filters by "page") AND the per-game engine below (the union of every managed key). Every key
# here is per-game-capable (Ryujinx per-game = a complete-file clone). "page" = the ryujinx_*
# settings namespace this group renders under.
GROUPS = [
    {"title": "Console", "note": "", "page": "ryujinx_system", "items": [
        _enum("system_region", "Console region", _REGION, _REGION),
        _enum("system_language", "Console language", _LANGUAGE_D, _LANGUAGE_S),
        _bool("docked_mode", "Docked mode (off = handheld)"),
    ]},
    {"title": "Options", "note": "", "page": "ryujinx_system", "items": [
        _bool("match_system_time", "Match system time to host clock"),
        _bool("ignore_missing_services", "Ignore missing services (compatibility)"),
    ]},
    {"title": "CPU", "note": "", "page": "ryujinx_cpu", "items": [
        _enum("memory_manager_mode", "Memory manager",
              ["Software (accurate)", "Host", "Host unchecked (fast)"],
              ["SoftwarePageTable", "HostMapped", "HostMappedUnsafe"]),
        _enum("dram_size", "Emulated console memory",
              ["4 GiB (default)", "6 GiB", "8 GiB", "12 GiB"], ["0", "1", "2", "3"],
              stored_int=True),
        _bool("enable_ptc", "PPTC cache"),
        _bool("enable_low_power_ptc", "Low-power PPTC"),
    ]},
    {"title": "Renderer", "note": "", "page": "ryujinx_gfx", "items": [
        _enum("graphics_backend", "Graphics API", ["Vulkan", "OpenGL"], ["Vulkan", "OpenGl"]),
        _int("res_scale", "Resolution scale (x native)", 1, 4),
        _enum("aspect_ratio", "Aspect ratio",
              ["4:3", "16:9", "16:10", "21:9", "32:9", "Stretched"],
              ["Fixed4x3", "Fixed16x9", "Fixed16x10", "Fixed21x9", "Fixed32x9", "Stretched"]),
        _enum("scaling_filter", "Scaling filter",
              ["Bilinear", "Nearest", "FSR", "Area"], ["Bilinear", "Nearest", "Fsr", "Area"]),
        _int("scaling_filter_level", "FSR sharpness (%)", 0, 100, 5),
        _enum("anti_aliasing", "Anti-aliasing",
              ["None", "FXAA", "SMAA Low", "SMAA Medium", "SMAA High", "SMAA Ultra"],
              ["None", "Fxaa", "SmaaLow", "SmaaMedium", "SmaaHigh", "SmaaUltra"]),
        _enum("max_anisotropy", "Anisotropic filtering",
              ["Auto", "2x", "4x", "8x", "16x"], ["-1", "2", "4", "8", "16"], stored_int=True),
        _enum("vsync_mode", "VSync",
              ["On (60 fps)", "Off (unlimited)", "Custom"], ["0", "1", "2"], stored_int=True),
    ]},
    {"title": "Performance", "note": "", "page": "ryujinx_gfxadv", "items": [
        _bool("enable_shader_cache", "Shader cache"),
        _bool("enable_texture_recompression", "Texture recompression"),
        _bool("enable_macro_hle", "Macro HLE"),
        _enum("backend_threading", "Backend multithreading",
              ["Auto", "Off", "On"], ["Auto", "Off", "On"]),
        _bool("enable_color_space_passthrough", "Color-space passthrough"),
    ]},
    {"title": "Audio", "note": "", "page": "ryujinx_audio", "items": [
        _enum("audio_backend", "Audio backend",
              ["Dummy (silent)", "OpenAL", "SoundIO", "SDL2", "SDL3"],
              ["Dummy", "OpenAl", "SoundIo", "SDL2", "SDL3"]),
        _float("audio_volume", "Output volume (0.0 = mute, 1.0 = full)", 0.0, 1.0, 0.05),
    ]},
]


def _json_read(text: str, _section: str, key: str) -> str | None:
    """Top-level JSON key → normalized string (bool→true/false, number→int str)."""
    try:
        v = json.loads(text).get(key)
    except (ValueError, AttributeError):
        return None
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else ("%g" % v)   # preserve fractional (e.g. volume)
    return str(v)


def _pergame_path(titleid: str):
    if "/" in titleid or "\\" in titleid or ".." in titleid:   # path-traversal guard
        raise RpcError("EINVAL", f"invalid titleid {titleid!r}")
    return _GAMES_DIR / titleid.lower() / "Config.json"


@method("ryujinx.get", slow=True, cache=("config",))
def _get(params):
    if params.get("titleid"):
        return _pergame_get(yp.tid(params))
    return cfgutil.do_get(GROUPS, ryujinx_json.CONFIG, _json_read, proc=_PROC, label=_LABEL)


def _apply_key(data: dict, item: dict, raw) -> object:
    """Set one typed value into `data` for `item`; return the C++-shaped value."""
    key, typ = item["key"], item["type"]
    if key not in data:
        raise RpcError("ENOKEY", f"{key!r} not present in Config.json")
    if typ == "bool":
        # the C++ sends the toggle as the STRING "1"/"0" -- bool("0") is True, so parse it.
        on = str(raw).strip().lower() in ("1", "true", "yes", "on")
        data[key] = on
        return on
    if typ == "int":
        v = int(raw)
        v = max(item.get("min", v), min(item.get("max", v), v))
        data[key] = v
        return v
    if typ == "float":
        f = float(raw)
        f = max(item.get("min", f), min(item.get("max", f), f))
        data[key] = f
        return f
    # enum: the C++ sends the option index. Route through cfgutil._enum_write so an out-of-curated
    # on-disk value (which _enum_get PREPENDS to the displayed options) maps correctly -- else the
    # index is off by one and picking an option silently stores the neighbour's value.
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad enum index {raw!r} for {key}")
    tok = cfgutil._enum_write(item, idx, _norm(data.get(key)) or "")
    if tok is None:
        raise RpcError("EINVAL", f"bad enum index {raw!r} for {key}")
    data[key] = int(tok) if item.get("stored_int") else tok
    return idx


# ── per-game overrides: DIRECT read/write of games/<tid>/Config.json ──────────
# Ryujinx has no per-key inherit (a per-game file wholly replaces global), so MAD edits the game's
# real Config.json in place -- exactly like Ryujinx's own per-game config. A per-game file is an
# independent FROZEN snapshot: it does NOT track later global changes (Ryujinx's own native
# behavior), which gives full interop -- values set in Ryujinx are read + preserved. The page's
# override-vs-inherit view is a LIVE diff of the file against global; there is no pin-map / regen.
_PG_NOTE = ("Per-game overrides written into this game's own Ryujinx config; saves instantly. "
            "Pick 'Inherit global' to clear one.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _global_data() -> dict:
    try:
        return ryujinx_json.load()
    except (OSError, ValueError):
        return {}


def _norm(v) -> str | None:
    """A Ryujinx JSON value -> the normalized string render_item expects (matches _json_read)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else ("%g" % v)
    return str(v)


def _managed_keys() -> set:
    return {it["key"] for g in GROUPS for it in g["items"]}


def _pergame_data_or_clone(tid: str, gdata: dict):
    """Return (data, pg, existing): an editable per-game config `data` (the game's real Config.json,
    topped up with any keys missing vs live global so it is COMPLETE and _typed never lands on an
    absent key), or a fresh global clone when the file is ABSENT; `pg` = its path; `existing` = the
    pristine loaded dict (None only when the file is absent). setdefault only ADDS absent keys -- it
    NEVER overwrites a user/Ryujinx value. A present-but-UNPARSEABLE file is REFUSED (raises), never
    clobbered with a clone -- house rule #5 (ensure_bak may skip a fresh .bak when a .router-backup
    exists, so a clobber would be unrecoverable). The read-only GET/summary treat it as inherit."""
    pg = _pergame_path(tid)
    existing = None
    if pg.is_file():
        try:
            existing = ryujinx_json.load(pg)
        except (OSError, ValueError):
            raise RpcError("EINVAL", "this game's Ryujinx config is not readable JSON — fix or "
                                     "remove it in Ryujinx, then try again.")
    if existing is not None:
        data = copy.deepcopy(existing)
        for k, v in gdata.items():
            data.setdefault(k, v)             # completeness top-up (add-absent only)
    else:
        data = copy.deepcopy(gdata)           # a complete clone (Ryujinx needs a full file)
    return data, pg, existing


def _override_count_data(data: dict, gdata: dict) -> int:
    """How many MANAGED keys diverge from global == the game's real MAD-visible overrides."""
    return sum(1 for k in _managed_keys() if k in data and data.get(k) != gdata.get(k))


def _override_count(tid: str) -> int:
    pg = _pergame_path(tid)
    gdata = _global_data()
    if not pg.is_file() or not gdata:
        return 0
    try:
        return _override_count_data(ryujinx_json.load(pg), gdata)
    except (OSError, ValueError):
        return 0


def _typed(item: dict, value):
    """C++ inherit-aware value -> the JSON-typed override to pin / write."""
    typ = item["type"]
    if typ == "bool":                         # 3-way: 0=Inherit, 1=Off, 2=On
        return int(float(value)) >= 2
    if typ == "int":
        v = int(float(value))
        return max(item.get("min", v), min(item.get("max", v), v))
    if typ == "float":
        f = float(value)
        return max(item.get("min", f), min(item.get("max", f), f))
    idx = int(float(value)) - 1               # enum: option[0] was "Inherit global"
    opts = item.get("options_stored") or item["options_display"]
    if idx < 0 or idx >= len(opts):
        raise RpcError("EINVAL", f"bad enum index {value!r} for {item['key']}")
    tok = opts[idx]
    return int(tok) if item.get("stored_int") else tok


def _is_inherit_value(item: dict, value) -> bool:
    if yp.is_inherit(value):                  # numeric inherit slot sends the "inherit" sentinel
        return True
    if item["type"] in ("bool", "enum"):
        try:
            return int(float(value)) <= 0     # index 0 == "Inherit global"
        except (TypeError, ValueError):
            return False
    return False


def _pergame_get(tid: str, groups: list | None = None) -> dict:
    """Inherit-aware per-game rows for `groups` (a GROUPS slice; default = the whole registry).
    Override-vs-inherit is a LIVE diff of the game's real Config.json against global: a managed key
    present in the file AND differing from global renders as an override; everything else renders as
    'Inherit global'. No pin-map -- the file is the source of truth (values set in Ryujinx show up)."""
    pg = _pergame_path(tid)
    gdata = _global_data()
    try:
        fdata = ryujinx_json.load(pg) if pg.is_file() else {}
    except (OSError, ValueError):
        fdata = {}
    out = []
    for g in (groups or GROUPS):
        rows = []
        for it in g["items"]:
            k = it["key"]
            raw = _norm(fdata[k]) if (k in fdata and fdata.get(k) != gdata.get(k)) else None
            row = yp.render_item(it, raw)
            if row:
                rows.append(row)
        if rows:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": rows})
    return {"exists": True, "running": _running(), "note": _PG_NOTE, "groups": out}


def _clear_state(tid: str) -> str:
    """How to treat the per-game Config.json when the LAST MAD override is cleared (house rule #5:
    never delete/clobber a user file MAD did not author):
      'delete' -> a pure MAD clone (differs from live global ONLY in MAD-managed keys, input_config
                  == global) -> safe to remove so the game cleanly inherits global.
      'regen'  -> ONLY input_config diverges (a genuine per-game controller setup) -> keep the file
                  and regen it (resets the cleared managed keys to global, preserves input_config).
      'keep'   -> ANY other user content (a Ryujinx-GUI key MAD does not manage), or the file/global
                  is unparseable/unreadable -> leave it untouched; NEVER delete or clobber it."""
    pg = _pergame_path(tid)
    if not pg.is_file():
        return "delete"                        # nothing on disk -> clean inherit
    gdata = _global_data()
    if not gdata:
        return "keep"                          # can't verify against global -> don't risk it
    try:
        existing = ryujinx_json.load(pg)
    except (OSError, ValueError):
        return "keep"                          # unparseable user file -> never touch it
    managed = {it["key"] for g in GROUPS for it in g["items"]}
    for k in set(existing) | set(gdata):
        if k in managed or k == "input_config":
            continue
        if existing.get(k) != gdata.get(k):
            return "keep"                      # a non-managed user key diverges -> user content
    if existing.get("input_config") != gdata.get("input_config"):
        return "regen"                         # only input_config diverges -> keep + preserve it
    return "delete"                            # pure MAD clone


def _pergame_set(item: dict, params: dict) -> dict:
    """Write ONE setting straight into the game's real Config.json (created as a complete global
    clone on first use). 'Inherit global' copies the CURRENT global value back in (a frozen copy --
    per-game does not track later global changes). When no managed override remains, reuse
    _clear_state to remove a pure MAD clone (clean inherit) while NEVER deleting a file that also
    holds Ryujinx-authored content or a per-game input_config (house rule #5)."""
    tid = yp.tid(params)
    key = item["key"]
    gdata = _global_data()
    if not gdata:
        raise RpcError("ENOENT", "global Ryujinx Config.json is unreadable — launch Ryujinx once "
                                 "so it writes its config, then set per-game overrides.")
    data, pg, existing = _pergame_data_or_clone(tid, gdata)   # refuses an unparseable file
    inherit = _is_inherit_value(item, params["value"])
    if inherit and existing is None:
        return {"key": key, "value": 0}        # no file -> inherit no-op (nothing to clear)
    if inherit:
        data[key] = gdata.get(key)             # copy CURRENT global (frozen) -> renders as inherit
    else:
        data[key] = _typed(item, params["value"])   # a concrete override into the game's own file
    wrote = False
    if existing is None or data != existing:   # write only when the file content actually changes
        pg.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.ensure_bak(pg)                 # one-time .bak (no-op on a brand-new file)
        ryujinx_json.write(data, pg)           # complete file; bumps staterev('config')
        wrote = True
    if _override_count_data(data, gdata) == 0 and _clear_state(tid) == "delete":
        pg.unlink(missing_ok=True)             # pure global clone -> remove for clean inherit
        if not wrote:
            from .. import staterev
            staterev.bump("config")
    raw = _norm(data.get(key)) if data.get(key) != gdata.get(key) else None
    row = yp.render_item(item, raw)
    return {"key": key, "value": row["value"] if row else 0}


def set_global(item: dict, params: dict) -> dict:
    """Edit ~/.config/Ryujinx/Config.json directly for one managed key. Shared by ryujinx.set and
    the granular ryujinx_settings pages. ryujinx_json.write bumps staterev('config') at the atomic
    write chokepoint, so no explicit bump is needed here."""
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    out = _apply_key(data, item, params["value"])
    cfgutil.ensure_bak(ryujinx_json.CONFIG)   # one-time .bak before first edit
    ryujinx_json.write(data, ryujinx_json.CONFIG)
    return {"key": item["key"], "value": out}


@method("ryujinx.set", slow=True)
def _set(params):
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close Ryujinx first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    item = cfgutil.item_by_key(GROUPS, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    if params.get("titleid"):
        return _pergame_set(item, params)
    return set_global(item, params)


def _summary(tid: str) -> str:
    n = _override_count(tid)
    return f"Custom: {n} setting{'' if n == 1 else 's'}" if n else ""


@method("ryujinx.games", slow=True)
def _games(params):
    """Switch games for the per-game media browser: [{titleid,name,stem,override,summary}]. An
    override = the game's own Config.json diverges from global on at least one managed key."""
    from . import switch_games, ryujinx_addons_cmds as _ad, ryujinx_cheats_cmds as _ch

    def _hide(tid):
        # Drop the per-game Add-Ons / Cheats tile for a game that has none (nothing to configure).
        hide = []
        if not _ad.has_content(tid):
            hide.append("addons")
        if not _ch.has_content(tid):
            hide.append("cheats")
        return hide

    return {"games": switch_games.listing(lambda tid: _override_count(tid) > 0, _summary, _hide),
            "system": "switch"}
