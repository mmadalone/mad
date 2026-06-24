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

from pathlib import Path

from .. import es_systems
from . import cfgutil
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
    {"key": "dolphin",    "label": "Wii",                "systems": ["wii", "gc"],
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
    # pcsx2x6 = the Namco System 246/256 PCSX2 fork (Tekken, Soul Calibur, Time Crisis,
    # Vampire Night, …; both pad/stick and Sinden-lightgun games; portable ini).
    # Full standalone tile: Settings (+ a "Start Sinden guns" action button), Input
    # mapping, and Controllers (pads -> players). It has NO router `backend` key: pads
    # are bound at launch by switch_bind (mad-standalone-launch.py), so the Controllers
    # section is the pads-to-players assigner (via _PADS_MAP_EMUS), not the router page.
    {"key": "pcsx2x6",    "label": "Namco 246/256",      "systems": ["pcsx2x6"],
     "settings_ns": "pcsx2x6"},
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
# xemu: per-pad controller_mapping in xemu.toml (xemu >= v0.8.133).
# rpcs3: per-button Config in input_configs/global/Default.yml (Player N Input).
_INPUT_MAP_EMUS = {"pcsx2", "pcsx2x6", "eden", "ryujinx", "xemu", "rpcs3"}

# Emulators whose "Controllers → pads → players" section is the per-emulator
# device-assignment page (pads.get/.set → GuiMadPagePadsPriority), NOT the router
# backend detail page. The Switch emulators are configure-once / router-skip, so
# the router-backend "gamepad" page is inert for them — this writes the emulator's
# own config directly (arg = emulator key, not a router backend name).
_PADS_MAP_EMUS = {"eden", "ryujinx", "pcsx2", "pcsx2x6", "xemu", "rpcs3"}

# Emulators with a per-game settings editor (a game picker → the settings page
# targeting that game's override). Switch only for now (Ryujinx clones global on
# create; Eden edits an existing custom/<TID>.ini). PCSX2/RPCS3/Dolphin = Phase 3.
_PERGAME_EMUS = {"eden", "ryujinx"}

# Switch-emulator install detection (drives the dynamic Switch tile). A Switch
# emulator counts as installed if MAD can actually configure it — its config file
# exists (the input/settings pages all need it) — OR a matching AppImage is present
# in ~/Applications (installed-but-not-yet-launched). `token` is matched
# case-insensitively against ~/Applications/*.appimage names. NOTE: the bundled
# `switch` <system> also lists Yuzu/Suyu commands, so command-label presence is NOT
# a usable "installed" signal — these concrete artefacts are.
_SWITCH_EMU_SIGNALS = {
    "eden":    {"config": "~/.config/eden/qt-config.ini",  "token": "eden"},
    "ryujinx": {"config": "~/.config/Ryujinx/Config.json", "token": "ryujinx"},
}


def _emu_installed(emu: str) -> bool:
    """True if a group-member emulator is present enough to configure. Unknown
    members (no signal entry) are treated as installed so they're never hidden."""
    sig = _SWITCH_EMU_SIGNALS.get(emu)
    if sig is None:
        return True
    if Path(sig["config"]).expanduser().is_file():
        return True
    apps = Path("~/Applications").expanduser()
    token = sig["token"]
    try:
        return any(token in p.name.lower() and p.name.lower().endswith(".appimage")
                   for p in apps.iterdir())
    except OSError:
        return False


def _pcsx2x6_has_guncon2() -> bool:
    """True if either pcsx2x6 USB port is the lightgun (guncon2) device — gates the
    Lightgun section on the Namco 246/256 tile (the controller-type picker sets it)."""
    ini = Path("~/Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini").expanduser()
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any((cfgutil.ini_read(text, sec, "Type") or "").strip() == "guncon2"
               for sec in ("USB1", "USB2"))


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
    # pcsx2x6: the Lightgun page (crosshair / Sinden border / Start Sinden guns) appears
    # only when a USB port is set to the Light Gun (guncon2) controller type, set on the
    # Settings page's controller-type picker. standalones.list re-runs per tile-grid open,
    # so toggling the type then re-entering shows/hides this section.
    if s.get("key") == "pcsx2x6" and _pcsx2x6_has_guncon2():
        secs.append({"label": "Lightgun", "sublabel": "crosshair, border, start guns",
                     "kind": "settings", "arg": "pcsx2x6_lightgun",
                     "title": s["label"] + " lightgun"})
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
    # "Present" = systems the user ACTUALLY has games for (a gamelist exists) — the
    # same signal ES-DE uses to show a system (and es_systems.quit_combo_systems). So
    # an emulator with no games drops off the grid. None = couldn't determine → don't
    # filter (show all, the old fallback).
    try:
        present = {s for s in es_systems.load_systems() if es_systems._has_gamelist(s)}
    except Exception:
        present = None
    tiles = []
    for s in STANDALONES:
        syss = ([sy for sy in s["systems"] if sy in present]
                if present is not None else list(s["systems"]))
        if not syss:
            continue
        art = next((a for a in (console_art(sy) for sy in syss) if a), None)
        if "members" in s:
            # Only offer the Switch emulators that are actually installed, so the
            # tile is DYNAMIC: both present → keep the Eden/Ryujinx sub-grid; exactly
            # one → collapse to a normal tile that opens straight into that emulator's
            # sections (no mid-step); neither → drop the tile entirely.
            members = [t for t in (_emu_tile(_EMUS[m]) for m in s["members"]
                                   if _emu_installed(m)) if t]
            if not members:
                continue
            if len(members) == 1:
                # Collapse: keep the group's label + console art, but carry the lone
                # member's sections (its arg=<emu> section kinds open the emu's pages).
                tiles.append({"key": s["key"], "label": s["label"], "sublabel": "",
                              "art": [art] if art else [], "sections": members[0]["sections"]})
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
    tiles.sort(key=lambda t: (t.get("label") or "").lower())   # alphabetical by label
    return {"tiles": tiles}
