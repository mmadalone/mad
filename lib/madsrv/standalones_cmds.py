"""standalones.* methods — the Standalones hub's tile list.

The MAD "Standalones" page is the single home for every standalone emulator: each
tile opens that emulator's config. A tile can have one or more SECTIONS (e.g.
Dolphin = Settings + Controllers; Daphne = Button mapping + Controllers); the C++
opens a single-section tile directly and shows a small chooser for multi-section
tiles. Tiles are filtered to the systems present in ES-DE, with the system's
console.png as art (per user request).

Section `kind` tells the C++ which page to open (see madOpenStandaloneTarget):
  settings   -> GuiMadPageEmuSettings(arg = RPC namespace, title)
  gamepad    -> GuiMadPageBackendDetail(arg = backend name)
  model2     -> GuiMadPageModel2
  daphne_map -> GuiMadPageDaphne (button mapping)
"""
from __future__ import annotations

from .. import es_systems
from .rpc import method
from .systems_cmds import console_art

# Curated standalone emulators (the user's list). `systems` = ES-DE system names
# implying the emulator is present (the tile shows only if one exists).
# `settings_ns` (optional) = the RPC namespace of its Settings page (added per
# emulator as Phase 3 lands; Dolphin is first). `backend` (optional) = its
# controller backend. `kind` (optional) = a special single-section tile.
STANDALONES = [
    {"key": "model2",     "label": "Sega Model 2",       "systems": ["model2"],
     "kind": "model2"},
    {"key": "supermodel", "label": "Sega Model 3",       "systems": ["model3"],
     "backend": "supermodel", "settings_ns": "model3"},
    {"key": "dolphin",    "label": "GameCube / Wii",     "systems": ["gc", "wii"],
     "backend": "dolphin", "settings_ns": "dolphin"},
    {"key": "cemu",       "label": "Wii U",              "systems": ["wiiu"],
     "backend": "cemu", "settings_ns": "cemu"},
    {"key": "eden",       "label": "Switch",             "systems": ["switch"],
     "backend": "eden", "settings_ns": "eden"},
    {"key": "rpcs3",      "label": "PlayStation 3",      "systems": ["ps3"],
     "backend": "rpcs3", "settings_ns": "rpcs3"},
    {"key": "pcsx2",      "label": "PlayStation 2",      "systems": ["ps2"],
     "backend": "pcsx2", "settings_ns": "pcsx2"},
    {"key": "xemu",       "label": "Xbox",               "systems": ["xbox"],
     "backend": "xemu"},
    {"key": "openbor",    "label": "OpenBOR",            "systems": ["openbor"],
     "backend": "openbor"},
    {"key": "daphne",     "label": "LaserDisc (Daphne)", "systems": ["daphne"],
     "kind": "daphne"},
]


# Emulators with a native per-button input-map page ({emu}.input_get/.input_set).
# Grows as the phased rollout lands; Model2 stays out (binary config, XInput-only).
_INPUT_MAP_EMUS = {"pcsx2"}


def _sections_for(s: dict) -> list[dict]:
    """The config sections a tile offers, in display order."""
    if s.get("kind") == "model2":
        return [{"label": "Settings", "sublabel": "emulator settings", "kind": "model2"}]
    if s.get("kind") == "daphne":
        return [
            {"label": "Button mapping", "sublabel": "map keys/buttons to Daphne actions",
             "kind": "daphne_map"},
            {"label": "Controllers", "sublabel": "which pads Daphne uses",
             "kind": "gamepad", "arg": "hypseus"},
        ]
    secs = []
    if "settings_ns" in s:
        secs.append({"label": "Settings", "sublabel": "video / audio / render",
                     "kind": "settings", "arg": s["settings_ns"],
                     "title": s["label"] + " — Settings"})
    if s.get("key") in _INPUT_MAP_EMUS:
        secs.append({"label": "Input mapping", "sublabel": "remap controller buttons",
                     "kind": "input_map", "arg": s["key"],
                     "title": s["label"] + " — Input mapping"})
    if "backend" in s:
        secs.append({"label": "Controllers", "sublabel": "pads → players",
                     "kind": "gamepad", "arg": s["backend"]})
    return secs


@method("standalones.list", slow=True)
def _standalones_list(params):
    """Tiles for the standalone emulators present in ES-DE. Each tile carries its
    config `sections` and the system's console.png as art."""
    try:
        present = set(es_systems.load_systems().keys())
    except Exception:
        present = set()
    tiles = []
    for s in STANDALONES:
        syss = [sy for sy in s["systems"] if sy in present] if present else list(s["systems"])
        if not syss:
            continue
        sections = _sections_for(s)
        if not sections:
            continue
        art = None
        for sy in syss:
            art = console_art(sy)
            if art:
                break
        # Tiles show ONLY the system name (no sublabel) per user request; the
        # section breakdown is shown on the tile's chooser page after opening.
        tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                      "art": [art] if art else [], "sections": sections})
    return {"tiles": tiles}
