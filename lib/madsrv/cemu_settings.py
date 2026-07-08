"""cemu_* - Cemu (Wii U) GLOBAL settings: byte-preserving single-element XML edits over
~/.config/Cemu/settings.xml (pugixml). Five instant-save pages mirroring Cemu's Options dialog:
  cemu_general / cemu_gfx / cemu_overlay / cemu_notif / cemu_audio

`section` = the XML PARENT tag, which isolates non-unique tags: <api> exists under BOTH <Graphic>
and <Audio>; <Position>/<TextScale> under BOTH <Overlay> and <Notification>. All of these sub-blocks
live inside the single <content> root, so the General-page keys use section "content" (cfgutil's
xml_read scopes to the <content>...</content> block, which is the whole file). Enum values are
Cemu's INTEGER ENUM CODES (source-verified against CemuConfig.h, cemu-project/Cemu, 2026-07-08), NOT
0-based combo indices except where they coincide. Audio `api` is PINNED to Cubeb=3 (the only Linux
backend; writing 0 = DirectSound breaks audio). GPU-device UUID strings, mlc_path, the wx language
id, and audio-device id strings are deliberately NOT exposed (no safe generic control). The engine
is version-safe (offers a key only if already present in the file), so it never invents a key -
Cemu writes a complete settings.xml on clean exit, so all keys below exist after one real session.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/Cemu/settings.xml"   # module global: tests redirect it
_PROC = "cemu"
_LABEL = "Cemu (Wii U)"
_F = _FILE.name


# ── descriptor helpers ────────────────────────────────────────────────────────
def _bool(key, label, section):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "bool", "bool_true": "true", "bool_false": "false"}


def _enum(key, label, section, options, *, mode="index", stored=None, name=None):
    it = {"key": key, "label": label, "file": _F, "section": section,
          "type": "enum", "write_mode": mode, "options_display": options}
    if stored is not None:
        it["options_stored"] = stored
    if name is not None:
        it["name"] = name
    return it


def _int(key, label, section, lo, hi, step=1):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "int", "min": lo, "max": hi, "step": step}


# ── enum option lists (source order; index == the stored ENUM CODE) ────────────
_CONSOLE_LANG = ["Japanese", "English", "French", "German", "Italian", "Spanish",
                 "Chinese", "Korean", "Dutch", "Portuguese", "Russian", "Taiwanese"]
_FILTER = ["Bilinear", "Bicubic", "Hermite", "Nearest Neighbor"]   # UpscalingFilter 0..3
_SCREEN_POS = ["Disabled", "Top left", "Top center", "Top right",
               "Bottom left", "Bottom center", "Bottom right"]      # ScreenPosition 0..6


# ── the five pages ─────────────────────────────────────────────────────────────
GENERAL_GROUPS = [
    {"title": "Startup & updates", "note": "", "items": [
        _bool("fullscreen", "Start in fullscreen", "content"),
        _bool("fullscreen_menubar", "Show menu bar in fullscreen", "content"),
        _bool("check_update", "Check for updates on startup", "content"),
        _bool("receive_untested_updates", "Include untested update builds", "content"),
        _bool("save_screenshot", "Save screenshots to a folder", "content"),
    ]},
    {"title": "System", "note": "", "items": [
        _enum("console_language", "Console language", "content", _CONSOLE_LANG),
        _bool("play_boot_sound", "Play the Wii U boot sound", "content"),
        _bool("disable_screensaver", "Disable the screensaver while running", "content"),
    ]},
    {"title": "Integrations", "note": "", "items": [
        _bool("feral_gamemode", "Feral GameMode (Linux)", "content"),
        _bool("use_discord_presence", "Discord Rich Presence", "content"),
    ]},
]

GFX_GROUPS = [
    {"title": "Renderer", "note": "VSync labels assume the current graphics API (Vulkan on the Deck).",
     "items": [
        _enum("graphic_api", "Graphics API", "Graphic", ["OpenGL", "Vulkan"], name="api"),
        _enum("VSync", "VSync", "Graphic",
              ["Off", "Double buffering", "Triple buffering", "Match emulated display"]),
        _bool("AsyncCompile", "Async shader compile", "Graphic"),
        _bool("GX2DrawdoneSync", "Full sync at GX2DrawDone", "Graphic"),
    ]},
    {"title": "Scaling", "note": "", "items": [
        _enum("UpscaleFilter", "Upscale filter", "Graphic", _FILTER),
        _enum("DownscaleFilter", "Downscale filter", "Graphic", _FILTER),
        _enum("FullscreenScaling", "Fullscreen scaling", "Graphic", ["Keep aspect ratio", "Stretch"]),
    ]},
]

OVERLAY_GROUPS = [
    {"title": "Performance overlay", "note": "", "items": [
        _enum("Position", "Overlay position", "Overlay", _SCREEN_POS),
        _int("TextScale", "Text scale (%)", "Overlay", 50, 300, 10),
        _bool("FPS", "Show FPS", "Overlay"),
        _bool("DrawCalls", "Show draw calls", "Overlay"),
        _bool("CPUUsage", "Show CPU usage", "Overlay"),
        _bool("CPUPerCoreUsage", "Show per-core CPU usage", "Overlay"),
        _bool("RAMUsage", "Show RAM usage", "Overlay"),
        _bool("VRAMUsage", "Show VRAM usage", "Overlay"),
        _bool("Debug", "Show debug info", "Overlay"),
    ]},
]

NOTIF_GROUPS = [
    {"title": "Notifications", "note": "", "items": [
        _enum("Position", "Notification position", "Notification", _SCREEN_POS),
        _int("TextScale", "Text scale (%)", "Notification", 50, 300, 10),
        _bool("ControllerProfiles", "Notify on controller profile load", "Notification"),
        _bool("ControllerBattery", "Notify on low controller battery", "Notification"),
        _bool("ShaderCompiling", "Notify while compiling shaders", "Notification"),
        _bool("FriendService", "Notify on friend list / service events", "Notification"),
    ]},
]

AUDIO_GROUPS = [
    {"title": "Audio", "note": "Only Cubeb is available on Linux.", "items": [
        # AudioAPI is stored as the enum CODE (Cubeb=3), not a 0-based index -> option mode.
        _enum("audio_api", "Audio API", "Audio", ["Cubeb"], mode="option", stored=["3"], name="api"),
        _int("TVVolume", "TV volume (%)", "Audio", 0, 100, 5),
        _int("PadVolume", "GamePad volume (%)", "Audio", 0, 100, 5),
        _enum("TVChannels", "TV channels", "Audio", ["Mono", "Stereo", "Surround"]),
        _int("delay", "Audio delay (blocks)", "Audio", 0, 30, 1),
    ]},
]

# ns -> (page title, groups). Order = the tile's Graphics group + top-level rows.
PAGES = {
    "cemu_general": ("General", GENERAL_GROUPS),
    "cemu_gfx":     ("Graphics", GFX_GROUPS),
    "cemu_overlay": ("Overlay", OVERLAY_GROUPS),
    "cemu_notif":   ("Notifications", NOTIF_GROUPS),
    "cemu_audio":   ("Audio", AUDIO_GROUPS),
}


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return cfgutil.do_get(groups, _FILE, cfgutil.xml_read, proc=_PROC, label=_LABEL)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        res = cfgutil.do_set(groups, params, _FILE, cfgutil.xml_read, cfgutil.xml_replace,
                             proc=_PROC, label=_LABEL)
        from .. import staterev
        staterev.bump("config")     # cfgutil.atomic_write doesn't bump; a config write must
        return res


for _ns, (_title, _groups) in PAGES.items():
    _register(_ns, _groups)
