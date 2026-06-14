"""pcsx2.* — PlayStation 2 (PCSX2) settings editor (~/.config/PCSX2/inis/PCSX2.ini).

Byte-preserving single-key edits via cfgutil.ini_* (NOT lib/pcsx2_cfg.py's
whole-[PadN]-section rewrite). Encodings verified on the live file: bools lowercase
true/false; Renderer is a SPARSE signed enum CODE (Auto=-1, OGL=12, SW=13, VK=14)
so it writes the code token; upscale_multiplier writes the bare int (1..8);
MaxAnisotropy writes the degree (0/2/4/8/16); EECycleRate writes the signed value
(-3..3); deinterlace_mode is a 0-based code == index. VsyncEnable lives in
[EmuCore/GS] (NOT [EmuCore]).
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/PCSX2/inis/PCSX2.ini"
_PROC = "pcsx2"
_LABEL = "PCSX2 (PS2)"
_F = _FILE.name

GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "Renderer", "label": "Renderer", "file": _F, "section": "EmuCore/GS",
         "type": "enum", "write_mode": "option",
         "options_display": ["Automatic", "Vulkan", "OpenGL", "Software"],
         "options_stored": ["-1", "14", "12", "13"]},
        {"key": "upscale_multiplier", "label": "Internal resolution", "file": _F,
         "section": "EmuCore/GS", "type": "enum", "write_mode": "option",
         "options_display": ["Native (1x)", "2x", "3x", "4x", "5x", "6x", "7x", "8x"],
         "options_stored": ["1", "2", "3", "4", "5", "6", "7", "8"]},
        {"key": "MaxAnisotropy", "label": "Anisotropic filtering", "file": _F,
         "section": "EmuCore/GS", "type": "enum", "write_mode": "option",
         "options_display": ["Off", "2x", "4x", "8x", "16x"],
         "options_stored": ["0", "2", "4", "8", "16"]},
        {"key": "deinterlace_mode", "label": "Deinterlacing", "file": _F,
         "section": "EmuCore/GS", "type": "enum", "write_mode": "index",
         "options_display": ["Automatic", "Off", "Weave (TFF)", "Weave (BFF)", "Bob (TFF)",
                             "Bob (BFF)", "Blend (TFF)", "Blend (BFF)", "Adaptive (TFF)",
                             "Adaptive (BFF)"]},
        {"key": "VsyncEnable", "label": "VSync", "file": _F, "section": "EmuCore/GS",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Speed & boot", "note": "", "items": [
        {"key": "EECycleRate", "label": "EE cycle rate", "file": _F,
         "section": "EmuCore/Speedhacks", "type": "enum", "write_mode": "option",
         "options_display": ["50% (under)", "60% (under)", "75% (under)", "100% (normal)",
                             "130% (over)", "180% (over)", "300% (over)"],
         "options_stored": ["-3", "-2", "-1", "0", "1", "2", "3"]},
        {"key": "EnableFastBoot", "label": "Fast boot (skip BIOS)", "file": _F,
         "section": "EmuCore", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
]


@method("pcsx2.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("pcsx2.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)
