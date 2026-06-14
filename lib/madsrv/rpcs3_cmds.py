"""rpcs3.* — PlayStation 3 (RPCS3) settings editor (~/.config/rpcs3/config.yml).

Byte-preserving single-value edits via cfgutil.yaml_* scoped to the top-level
`Video:` block (so Video/Renderer ≠ Audio/Renderer, and no PyYAML reserialize that
would reorder keys / drop quoting / add a trailing newline). RPCS3 bools are
lowercase true/false. Enum values are the exact token strings; the current
'Async Shader Recompiler' Shader-Mode token predates current master and is
prepended automatically so its index round-trips. NB: a per-game config under
config/<TITLEID>/ overrides these globals for that title.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/rpcs3/config.yml"
_PROC = "rpcs3"
_LABEL = "RPCS3 (PS3)"
_F = _FILE.name
_S = "Video"

GROUPS = [
    {"title": "Video", "note": "Per-game overrides (if set) take priority over these globals.",
     "items": [
        {"key": "Renderer", "label": "Renderer", "file": _F, "section": _S, "type": "enum",
         "write_mode": "option", "options_display": ["Null", "OpenGL", "Vulkan"],
         "options_stored": ["Null", "OpenGL", "Vulkan"]},
        {"key": "Resolution", "label": "Resolution", "file": _F, "section": _S, "type": "enum",
         "write_mode": "option",
         "options_display": ["1920x1080", "1280x720", "720x480", "720x576",
                             "1600x1080", "1440x1080", "1280x1080", "960x1080"],
         "options_stored": ["1920x1080", "1280x720", "720x480", "720x576",
                            "1600x1080", "1440x1080", "1280x1080", "960x1080"]},
        {"key": "Resolution Scale", "label": "Resolution scale (%)", "file": _F, "section": _S,
         "type": "int", "min": 25, "max": 800, "step": 25},
        {"key": "Frame limit", "label": "Frame limit", "file": _F, "section": _S, "type": "enum",
         "write_mode": "option",
         "options_display": ["Off", "30", "50", "60", "120", "Display", "Auto",
                             "PS3 Native", "Infinite"],
         "options_stored": ["Off", "30", "50", "60", "120", "Display", "Auto",
                            "PS3 Native", "Infinite"]},
        {"key": "VSync", "label": "VSync", "file": _F, "section": _S, "type": "bool",
         "bool_true": "true", "bool_false": "false"},
        {"key": "Write Color Buffers", "label": "Write Color Buffers", "file": _F,
         "section": _S, "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
]


@method("rpcs3.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.yaml_read, proc=_PROC, label=_LABEL)


@method("rpcs3.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.yaml_read, cfgutil.yaml_replace,
                          proc=_PROC, label=_LABEL)
