"""cemu.* — Wii U (Cemu) settings editor (~/.config/Cemu/settings.xml, pugixml XML).

Byte-preserving single-element edits via cfgutil.xml_* (NEVER parse+reserialize —
that would reflow indentation, the <?xml?> decl, self-closing tags, and the big
<GraphicPack>/<RecentLaunchFiles> blocks). `section` here is the XML PARENT tag,
which isolates non-unique tags: <api> exists under BOTH <Graphic> and <Audio>.
Bools are lowercase true/false; the rest are integer enum codes. mlc_path is NOT
exposed (no string control in the generic page).
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/Cemu/settings.xml"
_PROC = "cemu"
_LABEL = "Cemu (Wii U)"
_F = _FILE.name

GROUPS = [
    {"title": "Graphics", "note": "VSync labels assume the current graphics API.", "items": [
        {"key": "graphic_api", "name": "api", "label": "Graphics API", "file": _F,
         "section": "Graphic", "type": "enum", "write_mode": "index",
         "options_display": ["OpenGL", "Vulkan"]},
        {"key": "VSync", "label": "VSync", "file": _F, "section": "Graphic",
         "type": "enum", "write_mode": "index",
         "options_display": ["Off", "Double buffering", "Triple buffering",
                             "Match emulated display"]},
        {"key": "UpscaleFilter", "label": "Upscale filter", "file": _F, "section": "Graphic",
         "type": "enum", "write_mode": "index",
         "options_display": ["Bilinear", "Bicubic", "Hermite", "Nearest Neighbor"]},
        {"key": "DownscaleFilter", "label": "Downscale filter", "file": _F, "section": "Graphic",
         "type": "enum", "write_mode": "index",
         "options_display": ["Bilinear", "Bicubic", "Hermite", "Nearest Neighbor"]},
        {"key": "AsyncCompile", "label": "Async shader compile", "file": _F,
         "section": "Graphic", "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "fullscreen", "label": "Fullscreen", "file": _F, "section": "content",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "TVVolume", "label": "TV volume (%)", "file": _F, "section": "Audio",
         "type": "int", "min": 0, "max": 100, "step": 5},
        # AudioAPI stored as the enum integer (Cubeb=3), not a 0-based index; only
        # Cubeb is available on Linux -> single curated option mapping idx0 -> "3".
        {"key": "audio_api", "name": "api", "label": "Audio API", "file": _F,
         "section": "Audio", "type": "enum", "write_mode": "option",
         "options_display": ["Cubeb"], "options_stored": ["3"]},
    ]},
]


@method("cemu.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.xml_read, proc=_PROC, label=_LABEL)


@method("cemu.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.xml_read, cfgutil.xml_replace,
                          proc=_PROC, label=_LABEL)
