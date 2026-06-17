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
from .systems_cmds import console_art, resolve_art

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
    # Switch is a GROUP: its tile opens a sub-grid of the two Switch emulators
    # (Eden + Ryujinx). Members are defined in _EMUS below.
    {"key": "switch",     "label": "Switch",             "systems": ["switch"],
     "members": ["eden", "ryujinx"]},
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


# Group members (not top-level tiles): emulator definitions used to build a group
# tile's sub-grid. `icon` is a router-config/icons/*.png (themable), resolved via
# resolve_art (the icons/ chain) — NOT console_art (which only does <system>/
# console.png). Ryujinx has no `backend` (un-routed → no Controllers section).
_EMUS = {
    "eden":    {"key": "eden",    "label": "Eden",    "backend": "eden",
                "settings_ns": "eden", "icon": "icons/eden.png"},
    "ryujinx": {"key": "ryujinx", "label": "Ryujinx", "settings_ns": "ryujinx",
                "icon": "icons/ryujinx.png"},
}

# Emulators with a native per-button input-map page ({emu}.input_get/.input_set).
# Grows as the phased rollout lands; Model2 stays out (binary config, XInput-only).
_INPUT_MAP_EMUS = {"pcsx2", "eden", "ryujinx"}

# Emulators whose "Controllers → pads → players" section is the per-emulator
# device-assignment page (pads.get/.set → GuiMadPagePadsPriority), NOT the router
# backend detail page. The Switch emulators are configure-once / router-skip, so
# the router-backend "gamepad" page is inert for them — this writes the emulator's
# own config directly (arg = emulator key, not a router backend name).
_PADS_MAP_EMUS = {"eden", "ryujinx", "pcsx2", "xemu"}

# Emulators with a per-game settings editor (a game picker → the settings page
# targeting that game's override). Switch only for now (Ryujinx clones global on
# create; Eden edits an existing custom/<TID>.ini). PCSX2/RPCS3/Dolphin = Phase 3.
_PERGAME_EMUS = {"eden", "ryujinx"}


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
    if s.get("key") in _PERGAME_EMUS:
        secs.append({"label": "Per-game settings", "sublabel": "override settings for one game",
                     "kind": "settings_pergame", "arg": s["key"],
                     "title": s["label"] + " — Per-game settings"})
    if s.get("key") in _INPUT_MAP_EMUS:
        secs.append({"label": "Input mapping", "sublabel": "remap controller buttons",
                     "kind": "input_map", "arg": s["key"],
                     "title": s["label"] + " — Input mapping"})
    if s.get("key") in _PADS_MAP_EMUS:
        # Per-emulator device assignment (writes the emulator's own config).
        secs.append({"label": "Controllers", "sublabel": "pads → players",
                     "kind": "pads_map", "arg": s["key"],
                     "title": s["label"] + " — Controllers"})
    elif "backend" in s:
        secs.append({"label": "Controllers", "sublabel": "pads → players",
                     "kind": "gamepad", "arg": s["backend"]})
    return secs


def _emu_tile(emu: dict) -> dict | None:
    """Build a member tile (icon from router-config/icons via resolve_art)."""
    secs = _sections_for(emu)
    if not secs:
        return None
    icon = resolve_art([emu["icon"]]) if emu.get("icon") else ""
    return {"key": emu["key"], "label": emu["label"], "sublabel": "",
            "art": [icon] if icon else [], "sections": secs}


@method("standalones.list", slow=True)
def _standalones_list(params):
    """Tiles for the standalone emulators present in ES-DE. A normal tile carries
    its config `sections`; a GROUP tile (e.g. Switch) carries `members` — a
    sub-grid of emulator tiles the C++ opens on tile press. Tiles use the
    system's console.png; member tiles use their router-config/icons art."""
    try:
        present = set(es_systems.load_systems().keys())
    except Exception:
        present = set()
    tiles = []
    for s in STANDALONES:
        syss = [sy for sy in s["systems"] if sy in present] if present else list(s["systems"])
        if not syss:
            continue
        art = next((a for a in (console_art(sy) for sy in syss) if a), None)
        if "members" in s:
            members = [t for t in (_emu_tile(_EMUS[m]) for m in s["members"]) if t]
            if not members:
                continue
            tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                          "art": [art] if art else [], "members": members})
            continue
        sections = _sections_for(s)
        if not sections:
            continue
        # Tiles show ONLY the system name (no sublabel) per user request; the
        # section breakdown is shown on the tile's chooser page after opening.
        tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                      "art": [art] if art else [], "sections": sections})
    return {"tiles": tiles}
