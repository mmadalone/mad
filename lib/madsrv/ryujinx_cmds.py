"""ryujinx.* — Ryujinx (Switch) settings editor: global AND per-game.

Ryujinx stores config as JSON (~/.config/Ryujinx/Config.json). A per-game override
lives at ~/.config/Ryujinx/games/<titleid>/Config.json — a FULL clone of the global
config that wholly replaces it, so to create one we clone global then edit the one
key (exactly what Ryujinx's own UI does). GET reuses cfgutil.get_groups with a JSON
read_fn (top-level key -> normalized string); SET writes the value back with the
right JSON type. `titleid` (optional) targets the per-game file. Consumed by the
generic GuiMadPageEmuSettings (global) + the per-game picker -> same page w/ titleid.

NOTE: string enums MUST set write_mode:"option" so the stored token (e.g.
"Fixed16x9") round-trips; without it cfgutil read every string enum as index 0.
"""
from __future__ import annotations

import json

from .. import proc_guard
from . import cfgutil, ryujinx_json
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
    tid = params.get("titleid")
    if tid:
        pg = _pergame_path(tid)
        if pg.is_file():
            return cfgutil.do_get(GROUPS, pg, _json_read, proc=_PROC,
                                  label="Per-game settings")
        # No override yet — show the global values as the inherited baseline.
        res = cfgutil.do_get(GROUPS, ryujinx_json.CONFIG, _json_read, proc=_PROC,
                             label="Per-game")
        res["note"] = ("Inherits the global Ryujinx settings — the first change you "
                       "make creates this game's own override.")
        return res
    return cfgutil.do_get(GROUPS, ryujinx_json.CONFIG, _json_read, proc=_PROC, label=_LABEL)


def _apply_key(data: dict, item: dict, raw) -> object:
    """Set one typed value into `data` for `item`; return the C++-shaped value."""
    key, typ = item["key"], item["type"]
    if key not in data:
        raise RpcError("ENOKEY", f"{key!r} not present in Config.json")
    if typ == "bool":
        data[key] = bool(raw)
        return bool(raw)
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


@method("ryujinx.set", slow=True)
def _set(params):
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close Ryujinx first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    item = cfgutil.item_by_key(GROUPS, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    tid = params.get("titleid")
    path = ryujinx_json.CONFIG
    if tid:
        path = _pergame_path(tid)
        if not path.is_file():
            try:                              # lazily create the override = clone global
                gdata = ryujinx_json.load()
            except (OSError, ValueError):
                raise RpcError("ENOENT", "global Ryujinx config not found — launch a game once")
            path.parent.mkdir(parents=True, exist_ok=True)
            ryujinx_json.write(gdata, path)
    try:
        data = ryujinx_json.load(path)
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    out = _apply_key(data, item, params["value"])
    cfgutil.ensure_bak(path)                  # one-time .bak before first edit
    ryujinx_json.write(data, path)
    return {"key": key, "value": out}


@method("ryujinx.games", slow=True)
def _games(params):
    """Switch games for the per-game picker: [{titleid,name,override}]."""
    from . import switch_games
    return {"games": switch_games.listing(lambda tid: _pergame_path(tid).is_file())}
