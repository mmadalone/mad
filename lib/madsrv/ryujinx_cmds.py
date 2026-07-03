"""ryujinx.* — Ryujinx (Switch) settings editor: global AND per-game.

Ryujinx stores config as JSON (~/.config/Ryujinx/Config.json). GLOBAL edits write that file
directly. PER-GAME (`titleid`) is inherit-aware: Ryujinx has NO per-key inherit -- its per-game
games/<titleid>/Config.json is a COMPLETE file that wholly replaces global, and an absent key
resets to a compiled default, not global (source-verified against the Ryubing loader). So MAD
tracks the user's actual overrides in a sidecar pin-map (Config.json.mad-pins = {key: value}) and
REGENERATES the complete Config.json = the existing file (or a live-global clone) with the pinned
keys overridden and every OTHER managed key refreshed from LIVE global. Un-pinned keys therefore
keep tracking global (fixing the old whole-clone freeze); non-managed keys (input_config, caches)
are preserved untouched. GET renders inherit-aware ("Inherit global" at index 0); picking Inherit
removes the pin. A legacy full-clone with no sidecar is migrated once (pins = the keys that differ
from global). Only per-game-CAPABLE keys are in GROUPS (never the emulator's (Global)-only rows).

NOTE: string enums MUST set write_mode:"option" so the stored token (e.g.
"Fixed16x9") round-trips; without it cfgutil read every string enum as index 0.
"""
from __future__ import annotations

import json

from .. import fsutil, proc_guard
from . import cfgutil, ryujinx_json
from . import yuzu_pergame as yp   # shared inherit-aware row renderer (render_item / tid / is_inherit)
from .rpc import RpcError, method

_PROC = "ryujinx"
_F = ryujinx_json.CONFIG.name   # "Config.json"
_LABEL = "Ryujinx graphics"
_GAMES_DIR = ryujinx_json.CONFIG.parent / "games"

GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "graphics_backend", "label": "Graphics API", "file": _F, "section": "",
         "type": "enum", "write_mode": "option",
         "options_display": ["Vulkan", "OpenGL"], "options_stored": ["Vulkan", "OpenGl"]},
        {"key": "res_scale", "label": "Resolution scale (x native)", "file": _F,
         "section": "", "type": "int", "min": 1, "max": 4, "step": 1},
        {"key": "aspect_ratio", "label": "Aspect ratio", "file": _F, "section": "",
         "type": "enum", "write_mode": "option",
         "options_display": ["4:3", "16:9", "16:10", "21:9", "32:9", "Stretched"],
         "options_stored": ["Fixed4x3", "Fixed16x9", "Fixed16x10", "Fixed21x9",
                            "Fixed32x9", "Stretched"]},
        {"key": "anti_aliasing", "label": "Anti-aliasing", "file": _F, "section": "",
         "type": "enum", "write_mode": "option",
         "options_display": ["None", "FXAA", "SMAA Low", "SMAA Medium", "SMAA High",
                             "SMAA Ultra"],
         "options_stored": ["None", "Fxaa", "SmaaLow", "SmaaMedium", "SmaaHigh",
                            "SmaaUltra"]},
        {"key": "scaling_filter", "label": "Scaling filter", "file": _F, "section": "",
         "type": "enum", "write_mode": "option",
         "options_display": ["Bilinear", "Nearest", "FSR"],
         "options_stored": ["Bilinear", "Nearest", "Fsr"]},
        {"key": "max_anisotropy", "label": "Anisotropic filtering", "file": _F,
         "section": "", "type": "enum", "write_mode": "option", "stored_int": True,
         "options_display": ["Auto", "2x", "4x", "8x", "16x"],
         "options_stored": ["-1", "2", "4", "8", "16"]},
        {"key": "enable_texture_recompression", "label": "Texture recompression",
         "file": _F, "section": "", "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "enable_vsync", "label": "VSync", "file": _F, "section": "",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "backend_threading", "label": "Backend multithreading", "file": _F,
         "section": "", "type": "enum", "write_mode": "option",
         "options_display": ["Auto", "Off", "On"], "options_stored": ["Auto", "Off", "On"]},
    ]},
    {"title": "Mode", "note": "", "items": [
        {"key": "docked_mode", "label": "Docked mode (off = handheld)", "file": _F,
         "section": "", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "System / performance", "note": "", "items": [
        {"key": "enable_ptc", "label": "PPTC cache", "file": _F, "section": "",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "enable_shader_cache", "label": "Shader cache", "file": _F, "section": "",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "memory_manager_mode", "label": "Memory manager", "file": _F, "section": "",
         "type": "enum", "write_mode": "option",
         "options_display": ["Software (accurate)", "Host", "Host unchecked (fast)"],
         "options_stored": ["SoftwarePageTable", "HostMapped", "HostMappedUnsafe"]},
        {"key": "enable_macro_hle", "label": "Macro HLE", "file": _F, "section": "",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
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
    if isinstance(v, (int, float)):
        return str(int(v))
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
    # enum: C++ sends the option index
    opts = item.get("options_stored") or item["options_display"]
    try:
        chosen = opts[int(raw)]
    except (ValueError, IndexError, TypeError):
        raise RpcError("EINVAL", f"bad enum index {raw!r} for {key}")
    data[key] = int(chosen) if item.get("stored_int") else chosen
    return int(raw)


# ── per-game inherit layer (sidecar pin-map + complete-file regen) ────────────
_PG_NOTE = ("Per-game overrides for Ryujinx. Pick 'Inherit global' to clear an override so this "
            "game follows your global Ryujinx setting. Only your overrides are pinned -- everything "
            "else tracks global. Each change saves instantly and only affects this game.")


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
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v)


def _pins_path(tid: str):
    return _pergame_path(tid).parent / "Config.json.mad-pins"


def _load_pins(tid: str) -> dict:
    try:
        d = json.loads(_pins_path(tid).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pins(tid: str, pins: dict) -> None:
    p = _pins_path(tid)
    if pins:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
    elif p.is_file():
        p.unlink()                            # no overrides left -> no sidecar (badge clears)


def _ensure_pins(tid: str) -> dict:
    """Current pin-map, migrating a legacy full-clone Config.json (no sidecar) ONCE: pins = the
    managed keys whose value differs from live global (its real per-game overrides). Migration is
    SKIPPED when global is unreadable -- otherwise every key would falsely 'differ' from an empty
    global and get permanently pinned."""
    if _pins_path(tid).is_file():
        return _load_pins(tid)
    pg = _pergame_path(tid)
    if not pg.is_file():
        return {}
    gdata = _global_data()
    if not gdata:                             # cannot distinguish overrides from clones -> don't guess
        return {}
    try:
        fdata = ryujinx_json.load(pg)
    except (OSError, ValueError):
        return {}
    pins = {it["key"]: fdata[it["key"]]
            for g in GROUPS for it in g["items"]
            if it["key"] in fdata and fdata.get(it["key"]) != gdata.get(it["key"])}
    if pins:
        _save_pins(tid, pins)
    return pins


def _typed(item: dict, value):
    """C++ inherit-aware value -> the JSON-typed override to pin / write."""
    typ = item["type"]
    if typ == "bool":                         # 3-way: 0=Inherit, 1=Off, 2=On
        return int(float(value)) >= 2
    if typ == "int":
        v = int(float(value))
        return max(item.get("min", v), min(item.get("max", v), v))
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


def _regen(tid: str, pins: dict) -> None:
    """Rewrite games/<tid>/Config.json COMPLETE = a fresh LIVE-global clone with the pinned keys
    overridden, so every un-pinned key (managed AND non-managed, incl. input_config at rest) tracks
    live global. REQUIRES a readable global: Ryujinx needs a complete file (a partial one resets
    keys to defaults, source-verified), so we REFUSE rather than ever write an incomplete file."""
    gdata = _global_data()
    if not gdata:
        raise RpcError("ENOENT", "global Ryujinx Config.json is unreadable — launch Ryujinx once "
                                 "so it writes its config, then set per-game overrides.")
    pg = _pergame_path(tid)
    # Preserve a GENUINE per-game input_config (set via Ryujinx's own per-game Input tab) when it
    # diverges from global; every other key tracks live global. (The router's launch-time device
    # binds are transient + restored, so at rest a non-divergent input_config just equals global's.)
    if pg.is_file():
        try:
            existing = ryujinx_json.load(pg)
            if "input_config" in existing and existing.get("input_config") != gdata.get("input_config"):
                gdata["input_config"] = existing["input_config"]
        except (OSError, ValueError):
            pass
    for key, val in pins.items():
        gdata[key] = val                       # gdata is a fresh load (not shared); mutate in place
    pg.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.ensure_bak(pg)                     # one-time .bak
    ryujinx_json.write(gdata, pg)


def refresh_pergame(tid: str) -> None:
    """Launch-time hook (switch_bind): if this game has pinned overrides, regenerate its complete
    Config.json from LIVE global + the pins so a global change since the last edit is reflected at
    run time. Best-effort -- must NEVER break a launch."""
    try:
        pins = _ensure_pins(tid)
        if pins:
            _regen(tid, pins)
    except Exception:
        pass


def _pergame_get(tid: str) -> dict:
    pins = _ensure_pins(tid)
    out = []
    for g in GROUPS:
        rows = [row for it in g["items"]
                if (row := yp.render_item(it, _norm(pins[it["key"]]) if it["key"] in pins else None))]
        if rows:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": rows})
    return {"exists": True, "running": _running(), "note": _PG_NOTE, "groups": out}


def _pergame_set(item: dict, params: dict) -> dict:
    tid = yp.tid(params)
    key = item["key"]
    pins = _ensure_pins(tid)
    if _is_inherit_value(item, params["value"]):
        pins.pop(key, None)
    else:
        pins[key] = _typed(item, params["value"])
    if pins:
        _regen(tid, pins)                      # may raise ENOENT if global unreadable (refuses)
        _save_pins(tid, pins)
    else:
        # no overrides left: remove the per-game file entirely so the game cleanly INHERITS global
        # (switch_bind._target falls back to global when games/<tid>/Config.json is absent).
        _pergame_path(tid).unlink(missing_ok=True)
        _save_pins(tid, {})                    # deletes the sidecar
        from .. import staterev
        staterev.bump("config")
    row = yp.render_item(item, _norm(pins.get(key)))
    return {"key": key, "value": row["value"] if row else 0}


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
    # global: edit ~/.config/Ryujinx/Config.json directly.
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    out = _apply_key(data, item, params["value"])
    cfgutil.ensure_bak(ryujinx_json.CONFIG)   # one-time .bak before first edit
    ryujinx_json.write(data, ryujinx_json.CONFIG)
    return {"key": key, "value": out}


def _summary(tid: str) -> str:
    n = len(_ensure_pins(tid))
    return f"Custom: {n} setting{'' if n == 1 else 's'}" if n else ""


@method("ryujinx.games", slow=True)
def _games(params):
    """Switch games for the per-game media browser: [{titleid,name,stem,override,summary}]. An
    override = a non-empty pin-map (legacy full-clones are migrated to pins on first touch)."""
    from . import switch_games
    return {"games": switch_games.listing(lambda tid: bool(_ensure_pins(tid)), _summary),
            "system": "switch"}
