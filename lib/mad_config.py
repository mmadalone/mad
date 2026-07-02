"""MAD config helpers + display constants (Tk-free).

Moved VERBATIM from router-config-gui.py module level (MAD native-panel phase 0,
R5) so the mad-backend daemon can serve them to the native ES-DE panel. The Tk
GUI re-imports these names — single source of truth, zero behavior change.
"""
from __future__ import annotations

from pathlib import Path

from . import esde_settings, localpolicy
from .policy import LOCAL

# Pad display constants + friendly-naming live in lib/pad_labels.py — the single
# home for controller labeling (see its docstring before adding a new label
# path). Re-exported here because this module was their historical address;
# new code should import lib.pad_labels directly.
from .pad_labels import KNOWN_PADS, PAD_SHORT, pad_name  # noqa: F401


def vidpid_from_sdl_guid(guid: str) -> str:
    """'vvvv:pppp' parsed from an SDL2 joystick GUID (vendor at bytes 4-5, product
    at bytes 8-9, both little-endian — independent of the bus/CRC/version bytes, so
    it works for Eden's CRC-zeroed GUIDs too). '' if the GUID is too short."""
    g = (guid or "").strip().lower()
    if len(g) < 20:
        return ""
    return f"{g[10:12]}{g[8:10]}:{g[18:20]}{g[16:18]}"

# Detected install presets per backend config path knob (AppImage / Flatpak / …).
# Marked with which exist at render time; a path not listed here stays TOML-only.
CONFIG_PRESETS = {
    ("cemu", "config_dir"): [
        "~/.config/Cemu/controllerProfiles",
        "~/.var/app/info.cemu.Cemu/config/Cemu/controllerProfiles"],
    ("pcsx2", "config_file"): [
        "~/.config/PCSX2/inis/PCSX2.ini",
        "~/.var/app/net.pcsx2.PCSX2/config/PCSX2/inis/PCSX2.ini"],
    ("xemu", "config_file"): [
        "~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml",
        "~/.config/xemu/xemu.toml"],
    ("eden", "config_file"): [
        "~/.config/eden/qt-config.ini"],
    ("rpcs3", "config_file"): [
        "~/.config/rpcs3/input_configs/global/Default.yml",
        "~/.var/app/net.rpcs3.RPCS3/config/rpcs3/input_configs/global/Default.yml"],
}

# Per-knob one-line captions shown under the control on a backend page.
KNOB_HELP = {
    "sdl_priority": "ON = expose only the top connected pad (strict Player 1). "
                    "off = expose all listed pads (multiplayer).",
    "pad_classes": "Pad families that count as players (left→right = P1 preference). "
                   "Pads not listed are hidden from this emulator.",
    "manage_players": "How many player slots the router configures.",
    "manage_pads": "How many pad slots the router configures.",
    "manage_ports_int": "How many controller ports the router configures.",
    "manage_ports_list": "Which controller slots the router manages "
                         "(Cemu Controller 1 = the Deck GamePad, left untouched).",
    "real2_min_wiimotes": "Use 2-remote mode when at least this many Wii Remotes connect.",
    "handheld_class": "Pad used when no listed player pad is connected (solo / handheld).",
    "respect_user_config_classes": "If any of these pads is connected, leave this "
                                    "emulator's input config untouched.",
    "keep_extra": "Extra pad families to always keep visible to the emulator.",
    "templates": "Emulator profile cloned for each pad family.",
    "p1_gamepad_template": "Profile forced onto the first managed slot (none = per-family).",
    "handheld_profile": "Profile written when no external pad is connected "
                        "(none = just clear the managed slots).",
    "template_profile": "Reference profile cloned for each player.",
    "config_dir": "Where this emulator keeps its controller config (AppImage vs Flatpak).",
    "config_file": "Where this emulator keeps its config file (AppImage vs Flatpak).",
}
# Knobs intentionally NOT exposed (shown as an Advanced note).
ADVANCED_KNOBS = ("quit_cmd", "wii_mode_tool", "name_overrides",
                  "backend", "category", "inherits")


def gui_flags() -> dict:
    """GUI-only prefs from the [gui] table of local.toml (router ignores it)."""
    g = localpolicy.load(LOCAL).get("gui", {})
    return {"sound_muted": bool(g.get("sound_muted", False)),
            "theme_colors": bool(g.get("theme_colors", True)),
            "theme_font": bool(g.get("theme_font", True)),
            "font_scale": str(g.get("font_scale", "auto"))}


def set_gui_flag(key, value):
    data = localpolicy.load(LOCAL)
    data.setdefault("gui", {})[key] = value
    localpolicy.dump(LOCAL, data)


# ── ES-DE startup-splash config ([esde_splash] in local.toml; read by
#    esde-splash-gen.sh at launch). Images only — ES-DE's splash is a static SVG,
#    so no video/animations. The ES-DE binary is patched (deck-patches fork)
#    to render the splash full-screen, and the generator cover-fills it.
ESDE_SPLASH_DIR = esde_settings.APPDATA / "splashscreens"   # honors $ESDE_APPDATA_DIR
SPLASH_MODES = [("off", "Off (stock ES-DE splash)"),
                ("fixed_image", "Fixed image"),
                ("random_image", "Random image")]
SPLASH_FITS = [("contain", "Contain — whole image, letterboxed"),
               ("cover", "Cover — zoom + crop to fill"),
               ("tile", "Tile — repeat a pattern to fill")]
# Cap on-screen rows — a gamepad list of thousands is unusable + slow to build.
SPLASH_PICKER_CAP = 200


def splash_cfg() -> dict:
    return localpolicy.load(LOCAL).get("esde_splash", {})


def set_splash(key, value):
    data = localpolicy.load(LOCAL)
    data.setdefault("esde_splash", {})[key] = value
    localpolicy.dump(LOCAL, data)


def list_splash_images() -> list:
    if not ESDE_SPLASH_DIR.is_dir():
        return []
    exts = {".png", ".jpg", ".jpeg", ".svg"}
    return sorted((p.name for p in ESDE_SPLASH_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in exts
                   and not p.name.startswith(".")), key=str.lower)


def toggle_splash_image(name, on):
    """Add/remove an image from the random pool ([esde_splash].images; empty=all)."""
    data = localpolicy.load(LOCAL)
    sp = data.setdefault("esde_splash", {})
    cur = list(sp.get("images") or [])
    if on and name not in cur:
        cur.append(name)
    elif not on and name in cur:
        cur.remove(name)
    sp["images"] = cur
    localpolicy.dump(LOCAL, data)


def backend_systems(merged: dict) -> list:
    from . import routing
    sysd = merged.get("systems", {})
    out = []
    for s, ent in sysd.items():
        if not isinstance(ent, dict):
            continue
        if (routing.resolve_system(merged, s) or {}).get("backend"):
            out.append(s)
    return sorted(out)


# Canonical controller families always offered in the priority reorder UI, even
# when no rule uses one yet. DualShock 4 is a SEPARATE family from DualSense so a
# DS4 and a DualSense can be ordered as distinct players (the router tells them
# apart by product id; see routing.family_of). Rules-derived tokens come first
# (preserving their order); any missing known family is appended.
KNOWN_FAMILIES = ["8BitDo", "DualSense", "DualShock 4", "Xbox", "X-Arcade",
                  "Steam Deck", "Wii Remote Pro"]


def controller_families(merged: dict) -> list:
    fams: list = []
    sysd = merged.get("systems", {})
    for s in sorted(sysd):
        ent = sysd[s]
        if not isinstance(ent, dict):
            continue
        for port in (ent.get("ports") or []):
            for tok in port:
                if tok not in fams:
                    fams.append(tok)
    for fam in KNOWN_FAMILIES:
        if fam not in fams:
            fams.append(fam)
    return fams


def pad_class_candidates(merged: dict, *extra) -> list:
    """Union of every backend's pad_classes (+ any extra current values), so a
    class-toggle row offers all known player families and shows current picks."""
    out: list = []
    for c in merged.get("backends", {}).values():
        if isinstance(c, dict):
            for cls in c.get("pad_classes", []):
                if cls not in out:
                    out.append(cls)
    for cls in extra:
        if cls and cls not in out:
            out.append(cls)
    return out


def backup_targets(merged: dict) -> dict:
    out: dict = {}
    for bname, c in merged.get("backends", {}).items():
        if not isinstance(c, dict):
            continue
        p = c.get("config_dir") or c.get("config_file")
        if p:
            out[bname] = Path(p).expanduser()
    for name, p in (merged.get("backups", {}).get("extra_configs", {}) or {}).items():
        out[name] = Path(p).expanduser()
    return out


def list_profiles(config_dir_or_file: str, pattern: str) -> list:
    """Profile names found next to an emulator config (stems for *.xml, full
    paths for eden .ini). Returns [] if the dir is missing."""
    if not config_dir_or_file:
        return []
    p = Path(config_dir_or_file).expanduser()
    base = p if p.is_dir() else p.parent
    if not base.is_dir():
        return []
    return sorted(base.glob(pattern))
