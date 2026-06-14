"""eden.* — Switch (Eden) settings editor (~/.config/eden/qt-config.ini, Qt INI).

Byte-preserving single-value edits via cfgutil. Eden-specific encodings verified
against source + the live file (see the phase-3 verification): bools are lowercase
`true`/`false`, EXCEPT use_docked_mode which is a ConsoleMode stored as integer
`1`/`0`. Qt prints a `key\\default=` twin line before each real `key=value`; the
anchored regex only matches `key=` so the twin is left untouched.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/eden/qt-config.ini"
_PROC = "eden"
_LABEL = "Eden (Switch)"
_F = _FILE.name

GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "resolution_setup", "label": "Internal resolution", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["0.5x (360p)", "0.75x (540p)", "1x (720p, native)",
                             "1.5x (1080p)", "2x (1440p)", "3x", "4x", "5x", "6x", "7x", "8x"]},
        {"key": "use_vsync", "label": "VSync mode", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Immediate (Off)", "Mailbox", "FIFO (On)", "FIFO Relaxed"]},
        {"key": "scaling_filter", "label": "Scaling filter", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Nearest Neighbor", "Bilinear", "Bicubic", "Gaussian",
                             "ScaleForce", "AMD FSR"]},
        {"key": "gpu_accuracy", "label": "GPU accuracy", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Normal", "High", "Extreme"]},
    ]},
    {"title": "Display", "note": "", "items": [
        {"key": "use_docked_mode", "label": "Docked mode", "file": _F,
         "section": "System", "type": "bool", "bool_true": "1", "bool_false": "0"},
        {"key": "fullscreen", "label": "Start in fullscreen", "file": _F,
         "section": "UI", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "output_engine", "label": "Audio output engine", "file": _F,
         "section": "Audio", "type": "enum", "write_mode": "option",
         "options_display": ["Auto", "cubeb", "SDL2", "Null (no audio)", "oboe (Android)"],
         "options_stored": ["auto", "cubeb", "sdl2", "null", "oboe"]},
        {"key": "volume", "label": "Audio volume (%)", "file": _F,
         "section": "Audio", "type": "int", "min": 0, "max": 200, "step": 5},
    ]},
]


@method("eden.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("eden.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)
