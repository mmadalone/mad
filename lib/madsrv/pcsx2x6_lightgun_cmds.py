"""pcsx2x6_lightgun.* — the pcsx2x6 (Namco 246/256) Lightgun page.

A second settings namespace over the SAME portable ini as pcsx2x6_cmds, carrying only
the lightgun appearance/behaviour bits: per-gun crosshair IMAGE ([USB1]/[USB2]
guncon2_cursor_path) and SIZE (guncon2_cursor_scale), the Sinden white-border overlay
([JVS] SindenBorder*), plus a Start-Sinden-guns action button. (The gun BUTTON bindings
live on the input-mapping page.) The standalones tile shows this section ONLY when a USB
port's Type is guncon2 (see standalones_cmds._pcsx2x6_has_guncon2).

The crosshair IMAGE is a picker over the .png files in the portable crosshairs dir
(scanned live), so a gamepad user picks Green/Red rather than typing a path — and it
also lets them correct a stale/wrong on-disk path (the engine shows the current value
then the curated list). Same generic GuiMadPageEmuSettings renders it — no C++ change.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / "Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini"
_PROC = "pcsx2x6"
_LABEL = "Namco 246/256 lightgun"
_F = _FILE.name
_CROSSHAIR_DIR = _FILE.parent.parent / "crosshairs"   # ~/Applications/pcsx2x6/PCSX2x6/crosshairs

_SCALE = {"type": "enum", "write_mode": "option",
          "options_display": ["Small (0.05)", "Medium (0.08)", "Large (0.12)", "X-Large (0.2)"],
          "options_stored": ["0.05", "0.08", "0.12", "0.2"]}


def _crosshair_options() -> tuple[list[str], list[str]]:
    """(display names, stored absolute paths) for each .png in the portable crosshairs
    dir, sorted. Empty when the dir is missing — the picker is then omitted."""
    try:
        pngs = sorted(_CROSSHAIR_DIR.glob("*.png"))
    except OSError:
        pngs = []
    return [p.stem for p in pngs], [str(p) for p in pngs]


def _crosshair_items() -> list:
    disp, stored = _crosshair_options()
    items = []
    if stored:                                   # only offer the picker if images exist
        items += [
            {"key": "guncon2_cursor_path", "label": "Crosshair image - Gun 1", "file": _F,
             "section": "USB1", "type": "enum", "write_mode": "option",
             "options_display": disp, "options_stored": stored},
            {"key": "guncon2_cursor_path_p2", "name": "guncon2_cursor_path",
             "label": "Crosshair image - Gun 2", "file": _F,
             "section": "USB2", "type": "enum", "write_mode": "option",
             "options_display": disp, "options_stored": stored},
        ]
    items += [
        {"key": "guncon2_cursor_scale", "label": "Crosshair size - Gun 1", "file": _F,
         "section": "USB1", **_SCALE},
        {"key": "guncon2_cursor_scale_p2", "name": "guncon2_cursor_scale",
         "label": "Crosshair size - Gun 2", "file": _F, "section": "USB2", **_SCALE},
    ]
    return items


def _groups() -> list:
    return [
        {"title": "Crosshairs", "note": "", "items": _crosshair_items()},
        {"title": "Sinden border", "note": "White frame the Sinden camera tracks.", "items": [
            {"key": "SindenBorderEnabled", "label": "Show Sinden border", "file": _F,
             "section": "JVS", "type": "bool", "bool_true": "true", "bool_false": "false"},
            {"key": "SindenBorderMode", "label": "Border placement", "file": _F, "section": "JVS",
             "type": "enum", "write_mode": "index",
             "options_display": ["Around game image", "Around full window"]},
            {"key": "SindenBorderThickness", "label": "Border thickness (px)", "file": _F,
             "section": "JVS", "type": "int", "min": 1, "max": 50, "step": 1},
        ]},
    ]


# Start the Sinden driver. The C++ type:"action" button fires this RPC directly
# (sinden.driver, same as the global Lightgun page); not pcsx2x6_lightgun.set.
# (No Calibrate here: the Sinden *driver* calibration lives on the global Lightgun page,
# and the IN-GAME Namco gun calibration is the [JVS] Testmode toggle on the Settings
# page — a Calibrate button here would be the wrong, redundant one.)
_ACTION_GROUP = {
    "title": "Sinden guns",
    "note": "Starts the Sinden lightgun driver (smoother + LightgunMono).",
    "settings": [
        {"type": "action", "key": "start_sinden", "label": "▶ Start Sinden guns",
         "rpc": "sinden.driver", "args": {"action": "start"}},
    ],
}


@method("pcsx2x6_lightgun.get", slow=True)
def _get(params):
    res = cfgutil.do_get(_groups(), _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)
    if res.get("exists"):
        res["groups"].append(_ACTION_GROUP)
    return res


@method("pcsx2x6_lightgun.set", slow=True)
def _set(params):
    return cfgutil.do_set(_groups(), params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)
