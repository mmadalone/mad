"""ryujinx.* — Ryujinx (Switch) graphics-settings editor.

Parity with the Eden settings page, but Ryujinx stores config as JSON
(~/.config/Ryujinx/Config.json), not INI. GET reuses the schema-driven
cfgutil.get_groups with a JSON read_fn (returns each top-level key's value as a
normalized string); SET writes the value back with the correct JSON type
(bool/int/enum-string) via ryujinx_json. Consumed by the generic schema-driven
GuiMadPageEmuSettings, same as every other emulator's Settings page.
"""
from __future__ import annotations

import json

from .. import proc_guard
from . import cfgutil, ryujinx_json
from .rpc import RpcError, method

_PROC = "ryujinx"
_F = ryujinx_json.CONFIG.name   # "Config.json"
_LABEL = "Ryujinx graphics"

GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "graphics_backend", "label": "Graphics API", "file": _F, "section": "",
         "type": "enum", "options_display": ["Vulkan", "OpenGL"],
         "options_stored": ["Vulkan", "OpenGL"]},
        {"key": "res_scale", "label": "Resolution scale (x native)", "file": _F,
         "section": "", "type": "int", "min": 1, "max": 4, "step": 1},
        {"key": "aspect_ratio", "label": "Aspect ratio", "file": _F, "section": "",
         "type": "enum",
         "options_display": ["4:3", "16:9", "16:10", "21:9", "32:9", "Stretched"],
         "options_stored": ["Fixed4x3", "Fixed16x9", "Fixed16x10", "Fixed21x9",
                            "Fixed32x9", "Stretched"]},
        {"key": "anti_aliasing", "label": "Anti-aliasing", "file": _F, "section": "",
         "type": "enum",
         "options_display": ["None", "FXAA", "SMAA Low", "SMAA Medium", "SMAA High",
                             "SMAA Ultra"],
         "options_stored": ["None", "Fxaa", "SmaaLow", "SmaaMedium", "SmaaHigh",
                            "SmaaUltra"]},
        {"key": "scaling_filter", "label": "Scaling filter", "file": _F, "section": "",
         "type": "enum", "options_display": ["Bilinear", "Nearest", "FSR"],
         "options_stored": ["Bilinear", "Nearest", "Fsr"]},
        {"key": "enable_vsync", "label": "VSync", "file": _F, "section": "",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "backend_threading", "label": "Backend multithreading", "file": _F,
         "section": "", "type": "enum", "options_display": ["Auto", "Off", "On"],
         "options_stored": ["Auto", "Off", "On"]},
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


@method("ryujinx.get", slow=True, cache=("config",))
def _get(params):
    return cfgutil.do_get(GROUPS, ryujinx_json.CONFIG, _json_read,
                          proc=_PROC, label=_LABEL)


@method("ryujinx.set", slow=True)
def _set(params):
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close Ryujinx first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    item = cfgutil.item_by_key(GROUPS, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    if key not in data:
        raise RpcError("ENOKEY", f"{key!r} not present in Config.json")
    raw, typ = params["value"], item["type"]
    if typ == "bool":
        data[key] = bool(raw)
        out = bool(raw)
    elif typ == "int":
        v = int(raw)
        v = max(item.get("min", v), min(item.get("max", v), v))
        data[key] = v
        out = v
    else:  # enum: C++ sends the option index
        opts = item.get("options_stored") or item["options_display"]
        try:
            data[key] = opts[int(raw)]
        except (ValueError, IndexError, TypeError):
            raise RpcError("EINVAL", f"bad enum index {raw!r} for {key}")
        out = int(raw)
    ryujinx_json.write(data)
    return {"key": key, "value": out}
