"""pcsx2x6.* — Namco System 246/256 (pcsx2x6 fork) settings editor.

pcsx2x6 is a PCSX2 fork run with `-portable`, so its config lives in the AppImage
dir, NOT ~/.config/PCSX2 (that's the separate, regular PCSX2 build):
    ~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini

Byte-preserving single-key edits via cfgutil.ini_* (same engine as pcsx2_cmds).
Standard PCSX2 [EmuCore/GS] keys behave identically to mainline (Renderer is a
sparse signed enum CODE: Auto=-1, OGL=12, SW=13, VK=14). This Settings page covers
graphics, boot, the [JVS] Test-menu DIP, and the per-port USB controller-type picker
([USB1]/[USB2] Type: None / hidmouse / guncon2). The lightgun bits (crosshair scale,
the Sinden white-border overlay, and the Start Sinden guns button) live on the
separate pcsx2x6_lightgun page (shown only when a port = guncon2). The diagnostic
[JVS] keys (P2TriggerBit/P2SensorBit/DumpRam/SysByteOr/ScreenposTrig) are DEAD in
deck-patches (read by nothing), not exposed.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / "Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini"
_PROC = "pcsx2x6"
_LABEL = "Namco 246/256 (pcsx2x6)"
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
        {"key": "AspectRatio", "label": "Aspect ratio", "file": _F, "section": "EmuCore/GS",
         "type": "enum", "write_mode": "option",
         "options_display": ["Stretch", "Auto 4:3/3:2", "4:3", "16:9"],
         "options_stored": ["Stretch", "Auto 4:3/3:2", "4:3", "16:9"]},
        {"key": "VsyncEnable", "label": "VSync", "file": _F, "section": "EmuCore/GS",
         "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Boot", "note": "", "items": [
        {"key": "EnableFastBoot", "label": "Fast boot (skip BIOS logo)", "file": _F,
         "section": "EmuCore", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Lightgun / JVS",
     "note": "Test menu boots the operator I/O-TEST screen (run Gun Adjust to "
             "calibrate aim), then turn it back OFF to play.", "items": [
        {"key": "TestMode", "label": "Test menu (gun calibration)", "file": _F,
         "section": "JVS", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    # NOTE: [JVS] SuppressDaemon, VideoVoltage, MonitorSyncFrequency, VideoSyncSplit
    # are boot-critical arcade DIPs/workarounds that must stay at their defaults
    # (SuppressDaemon=ON dodges a dongle-open race; flipping it breaks booting) —
    # deliberately NOT exposed, same as the dead diagnostic keys.
    # Per-port USB device type. Selecting "Light Gun" (guncon2) reveals the separate
    # "Lightgun" tile section (crosshair/border + Start Sinden guns); see standalones_cmds
    # _pcsx2x6_has_guncon2. The crosshair + Sinden-border settings live on that page. NOTE:
    # the Sinden gun only works on the Light Gun type; None / HID Mouse disable it in-game.
    {"title": "Controller type",
     "note": "What each USB port presents to the game. The Sinden gun needs Light Gun "
             "(GunCon2); None / HID Mouse disable it.", "items": [
        {"key": "Type", "label": "Port 1 controller", "file": _F, "section": "USB1",
         "type": "enum", "write_mode": "option",
         "options_display": ["None", "HID Mouse", "Light Gun"],
         "options_stored": ["None", "hidmouse", "guncon2"]},
        {"key": "Type_p2", "name": "Type", "label": "Port 2 controller", "file": _F,
         "section": "USB2", "type": "enum", "write_mode": "option",
         "options_display": ["None", "HID Mouse", "Light Gun"],
         "options_stored": ["None", "hidmouse", "guncon2"]},
    ]},
]


@method("pcsx2x6.get", slow=True)
def _get(params):
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("pcsx2x6.set", slow=True)
def _set(params):
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)
