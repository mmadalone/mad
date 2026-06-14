"""model3.* — Sega Model 3 (Supermodel) settings editor (~/.supermodel/Config/Supermodel.ini).

Byte-preserving single-key edits via cfgutil.ini_* SCOPED to the [ Global ] section
(per-game sections precede [Global] in this file, so an unscoped first-match would
corrupt them). cfgutil's INI helpers tolerate the `[ Global ]` spaces and edit the
LAST match in the section — required because FullScreen is duplicated in [Global]
and Supermodel takes last-wins. All booleans are stored as integer 1/0.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".supermodel/Config/Supermodel.ini"
_PROC = "supermodel"
_LABEL = "Supermodel (Model 3)"
_F = _FILE.name
_S = "Global"


def _b(key, label):
    return {"key": key, "label": label, "file": _F, "section": _S, "type": "bool",
            "bool_true": "1", "bool_false": "0"}


GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        _b("New3DEngine", "New 3D engine"),
        _b("QuadRendering", "Quad rendering"),
        _b("WideScreen", "Widescreen"),
        _b("Stretch", "Stretch to fill"),
        _b("FullScreen", "Fullscreen"),
        {"key": "XResolution", "label": "Horizontal resolution", "file": _F, "section": _S,
         "type": "int", "min": 320, "max": 3840, "step": 16},
        {"key": "YResolution", "label": "Vertical resolution", "file": _F, "section": _S,
         "type": "int", "min": 240, "max": 2160, "step": 16},
    ]},
    {"title": "System", "note": "", "items": [
        _b("Throttle", "Throttle to 60fps"),
        _b("MultiThreaded", "Multi-threaded"),
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "SoundVolume", "label": "Sound volume (%)", "file": _F, "section": _S,
         "type": "int", "min": 0, "max": 200, "step": 5},
        {"key": "MusicVolume", "label": "Music volume (%)", "file": _F, "section": _S,
         "type": "int", "min": 0, "max": 200, "step": 5},
    ]},
]


@method("model3.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("model3.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)
