"""dolphin_* -- Dolphin (GameCube / Wii) GLOBAL settings, split into the emulator's
own Config + Graphics dialog tabs and grouped the Citron way (System / Video / Audio).

Nine instant-save pages, byte-preserving, MULTI-FILE:
  dolphin_general / dolphin_gc / dolphin_wii / dolphin_advanced   -> Dolphin.ini
  dolphin_gfx_general / _enh / _hacks / _adv                      -> GFX.ini (+ a few Dolphin.ini)
  dolphin_audio                                                   -> Dolphin.ini

Supersedes the old single-page dolphin_cmds.py. Each item carries its own `file`
(Dolphin.ini or GFX.ini) so one page can mix both. Value encodings verified against
Dolphin source + the live config (deck-docs/dolphin-ini-encodings.md):
  bool  -> capitalized True / False
  enum  -> "index" (stored int == option index) or "option" (stored string)
  MSAA (u32 hex sample-count) + SSAA (bool) are merged into ONE "Anti-aliasing" composite.

SAFETY: we EDIT ONLY keys that already exist (version-safe -- a key Dolphin didn't
write is simply not offered), EXCEPT a small vetted allowlist Dolphin doesn't persist
until changed (Overclock enable+value, DSP Volume) which we CREATE in their
source-verified [Core]/[DSP] section via ini_set_or_insert. Refused while Dolphin runs
(it rewrites its config on exit). Reuses the shared cfgutil byte-preserving primitives.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import RpcError, method

_DIR = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
DOLPHIN = "Dolphin.ini"
GFX = "GFX.ini"
_FILES = {DOLPHIN: _DIR / DOLPHIN, GFX: _DIR / GFX}
_PROC = "dolphin"
_NOTE = ("Dolphin (GameCube / Wii). Only settings this Dolphin build actually writes are shown.")


# -- descriptor helpers (Dolphin bools are capitalized True/False) --------------
def _bool(key, label, file, section, **kw):
    return {"key": key, "label": label, "file": file, "section": section,
            "type": "bool", "bool_true": "True", "bool_false": "False", **kw}


def _enum_idx(key, label, file, section, options, **kw):
    # stored integer == the option index
    return {"key": key, "label": label, "file": file, "section": section,
            "type": "enum", "write_mode": "index", "options_display": options, **kw}


def _enum_opt(key, label, file, section, display, stored, **kw):
    # stored value == an option token string (e.g. GFXBackend "Vulkan"; SIDevice "6")
    return {"key": key, "label": label, "file": file, "section": section,
            "type": "enum", "write_mode": "option",
            "options_display": display, "options_stored": stored, **kw}


def _int(key, label, file, section, lo, hi, step=1, **kw):
    return {"key": key, "label": label, "file": file, "section": section,
            "type": "int", "min": lo, "max": hi, "step": step, **kw}


def _float(key, label, file, section, lo, hi, step, **kw):
    return {"key": key, "label": label, "file": file, "section": section,
            "type": "float", "min": lo, "max": hi, "step": step, **kw}


# -- enum option lists (Dolphin source order) ----------------------------------
_GFX_BACKEND = ["Vulkan", "OGL", "Software Renderer"]           # Dolphin.ini [Core] GFXBackend
_DSP_BACKEND = ["Cubeb", "ALSA", "Pulse", "OpenAL", "No Audio Output"]
_ASPECT = ["Auto", "Force 16:9", "Force 4:3", "Stretch",
           "Custom", "Custom (Stretch)", "Raw (square pixels)"]   # AspectMode 0..6
_RESOLUTION = ["Auto (multiple of 640x528)", "Native (1x)", "2x", "3x", "4x",
               "5x", "6x", "7x", "8x"]                            # InternalResolution: value == scale
_ANISO = ["1x", "2x", "4x", "8x", "16x"]                          # MaxAnisotropy 0..4
_SHADER_COMPILE = ["Synchronous", "Synchronous (Ubershaders)",
                   "Asynchronous (Ubershaders)", "Asynchronous (skip drawing)"]  # 0..3
# GameCube SI port device (SerialInterface::SIDevices -- NON-contiguous enum ints).
_SIDEV_DISP = ["Nothing", "Standard Controller", "GameCube Adapter for Wii U",
               "GBA (real link)", "GBA (emulated)", "Steering Wheel",
               "Dance Mat", "DK Bongos", "Keyboard"]
_SIDEV_STORED = ["0", "6", "12", "5", "13", "8", "9", "10", "7"]


# -- the nine pages ------------------------------------------------------------
GENERAL_GROUPS = [
    {"title": "Core", "note": "Dual core is faster; a few games need it off.", "items": [
        _bool("CPUThread", "Dual core (speed-up)", DOLPHIN, "Core"),
        _bool("EnableCheats", "Enable cheats (AR/Gecko)", DOLPHIN, "Core"),
        _float("EmulationSpeed", "Emulation speed (0 = unlimited)", DOLPHIN, "Core", 0.0, 4.0, 0.1),
        _bool("OverrideRegionSettings", "Override region settings", DOLPHIN, "Core"),
        _bool("AutoDiscChange", "Auto disc change (multi-disc)", DOLPHIN, "Core"),
    ]},
    {"title": "Interface", "note": "", "items": [
        _bool("ConfirmStop", "Confirm before stopping", DOLPHIN, "Interface"),
        _bool("OnScreenDisplayMessages", "On-screen messages", DOLPHIN, "Interface"),
        _bool("PauseOnFocusLost", "Pause when window loses focus", DOLPHIN, "Interface"),
        _bool("UsePanicHandlers", "Show panic handler pop-ups", DOLPHIN, "Interface"),
    ]},
]

GC_GROUPS = [
    {"title": "GameCube ports", "note": "Which device is plugged into each GameCube "
                                        "controller port.", "items": [
        _enum_opt("SIDevice0", "Port 1 device", DOLPHIN, "Core", _SIDEV_DISP, _SIDEV_STORED),
        _enum_opt("SIDevice1", "Port 2 device", DOLPHIN, "Core", _SIDEV_DISP, _SIDEV_STORED),
        _enum_opt("SIDevice2", "Port 3 device", DOLPHIN, "Core", _SIDEV_DISP, _SIDEV_STORED),
        _enum_opt("SIDevice3", "Port 4 device", DOLPHIN, "Core", _SIDEV_DISP, _SIDEV_STORED),
    ]},
]

WII_GROUPS = [
    {"title": "Wii Remotes", "note": "Sensor bar, Wii language and 4:3/16:9 are set in Dolphin "
                                     "itself, not here.", "items": [
        _bool("WiimoteContinuousScanning", "Continuous Wii Remote scanning", DOLPHIN, "Core"),
        _bool("WiimoteEnableSpeaker", "Wii Remote speaker", DOLPHIN, "Core"),
        _bool("WiimoteControllerInterface", "Connect real Wii Remotes (controller interface)",
              DOLPHIN, "Core"),
    ]},
]

ADVANCED_GROUPS = [
    {"title": "CPU", "note": "MMU fixes a few games but is slower.", "items": [
        _bool("MMU", "Enable MMU (accuracy)", DOLPHIN, "Core"),
        _bool("LoadGameIntoMemory", "Load entire disc into memory", DOLPHIN, "Core"),
    ]},
    {"title": "Clock override", "note": "Overclocking can break games or cause crashes.", "items": [
        _bool("OverclockEnable", "Override emulated CPU clock", DOLPHIN, "Core",
              create=True, default="False"),
        _float("Overclock", "CPU clock factor (1.0 = 100%)", DOLPHIN, "Core", 0.0, 4.0, 0.05,
               create=True, default="1.0"),
    ]},
]

GFX_GENERAL_GROUPS = [
    {"title": "General", "note": "", "items": [
        _enum_opt("GFXBackend", "Graphics backend", DOLPHIN, "Core", _GFX_BACKEND, _GFX_BACKEND),
        _enum_idx("AspectRatio", "Aspect ratio", GFX, "Settings", _ASPECT),
        _bool("VSync", "V-Sync", GFX, "Hardware"),
        _bool("Fullscreen", "Fullscreen", DOLPHIN, "Display"),
        _bool("ShowFPS", "Show FPS", GFX, "Settings"),
    ]},
]

GFX_ENH_GROUPS = [
    {"title": "Enhancements", "note": "", "items": [
        _enum_idx("InternalResolution", "Internal resolution", GFX, "Settings", _RESOLUTION),
        {"key": "_aa", "label": "Anti-aliasing", "type": "aa", "file": GFX},
        # MaxAnisotropy MOVED sections across Dolphin versions: newer master (this Deck's
        # build 2606) reads [Enhancements] (enum AnisotropicFilteringMode: -1=Default,
        # 0=1x..4=16x); the older 2603a wrote [Hardware] (plain 0=1x..4=16x). Same 0..4
        # meaning; -1 only in [Enhancements]. Detect the live section (Enhancements first).
        _enum_opt("MaxAnisotropy", "Anisotropic filtering", GFX, "Enhancements",
                  ["Default", "1x", "2x", "4x", "8x", "16x"], ["-1", "0", "1", "2", "3", "4"],
                  sections=["Enhancements", "Hardware"]),
        _bool("ArbitraryMipmapDetection", "Arbitrary mipmap detection", GFX, "Enhancements"),
        _bool("DisableCopyFilter", "Disable copy filter", GFX, "Enhancements"),
        _bool("ForceTrueColor", "Force 24-bit color", GFX, "Enhancements"),
        _bool("wideScreenHack", "Widescreen hack (4:3 games -> 16:9)", GFX, "Settings"),
    ]},
]

GFX_HACKS_GROUPS = [
    {"title": "EFB", "note": "Speed hacks; a few games need some of these OFF for correctness.",
     "items": [
        _bool("EFBToTextureEnable", "Store EFB copies to texture only", GFX, "Hacks"),
        _bool("EFBScaledCopy", "Scaled EFB copy", GFX, "Hacks"),
        _bool("EFBAccessEnable", "EFB CPU access", GFX, "Hacks"),
        _bool("EFBEmulateFormatChanges", "Emulate EFB format changes", GFX, "Hacks"),
        _bool("DeferEFBCopies", "Defer EFB copies", GFX, "Hacks"),
    ]},
    {"title": "XFB / other", "note": "", "items": [
        _bool("XFBToTextureEnable", "Store XFB copies to texture only", GFX, "Hacks"),
        _bool("SkipDuplicateXFBs", "Skip duplicate XFB frames", GFX, "Hacks"),
        _bool("ImmediateXFBEnable", "Immediately present XFB", GFX, "Hacks"),
        _bool("VISkip", "VI skip (speed hack)", GFX, "Hacks"),
        _bool("BBoxEnable", "Bounding box emulation", GFX, "Hacks"),
    ]},
]

GFX_ADV_GROUPS = [
    {"title": "Performance", "note": "", "items": [
        _bool("BackendMultithreading", "Backend multithreading", GFX, "Settings"),
        _enum_idx("ShaderCompilationMode", "Shader compilation", GFX, "Settings", _SHADER_COMPILE),
        _bool("WaitForShadersBeforeStarting", "Compile shaders before starting", GFX, "Settings"),
        _bool("EnableGPUTextureDecoding", "GPU texture decoding", GFX, "Settings"),
        _bool("FastDepthCalc", "Fast depth calculation", GFX, "Settings"),
    ]},
    {"title": "Textures & mods", "note": "", "items": [
        _bool("HiresTextures", "Load custom textures", GFX, "Settings"),
        _bool("CacheHiresTextures", "Prefetch custom textures", GFX, "Settings"),
        _bool("SaveTextureCacheToState", "Save texture cache to save states", GFX, "Settings"),
        _bool("EnableMods", "Enable graphics mods", GFX, "Settings"),
    ]},
]

AUDIO_GROUPS = [
    {"title": "Audio", "note": "", "items": [
        _enum_opt("Backend", "Audio backend", DOLPHIN, "DSP", _DSP_BACKEND, _DSP_BACKEND),
        _int("Volume", "Volume (%)", DOLPHIN, "DSP", 0, 100, 5, create=True, default="100"),
        _bool("DSPHLE", "DSP HLE (fast)", DOLPHIN, "Core"),
        _bool("DSPThread", "DSP on a dedicated thread", DOLPHIN, "DSP"),
        _bool("AudioStretch", "Audio stretching", DOLPHIN, "Core"),
        _bool("DPL2Decoder", "Dolby Pro Logic II decoder", DOLPHIN, "Core"),
    ]},
]

# ns -> (page title, groups). Order mirrors Dolphin's Config + Graphics dialogs.
PAGES = {
    "dolphin_general":     ("General", GENERAL_GROUPS),
    "dolphin_gc":          ("GameCube", GC_GROUPS),
    "dolphin_wii":         ("Wii", WII_GROUPS),
    "dolphin_advanced":    ("Advanced", ADVANCED_GROUPS),
    "dolphin_gfx_general": ("Graphics: General", GFX_GENERAL_GROUPS),
    "dolphin_gfx_enh":     ("Graphics: Enhancements", GFX_ENH_GROUPS),
    "dolphin_gfx_hacks":   ("Graphics: Hacks", GFX_HACKS_GROUPS),
    "dolphin_gfx_adv":     ("Graphics: Advanced", GFX_ADV_GROUPS),
    "dolphin_audio":       ("Audio", AUDIO_GROUPS),
}


# -- Anti-aliasing composite (MSAA hex sample-count + SSAA bool -> one enum) ----
_AA_OPTIONS = ["None", "2x MSAA", "4x MSAA", "8x MSAA", "2x SSAA", "4x SSAA", "8x SSAA"]
_AA_MAP = [(1, False), (2, False), (4, False), (8, False), (2, True), (4, True), (8, True)]


def _aa_get(texts):
    text = texts.get(GFX)
    if text is None:
        return None
    msaa_raw = cfgutil.ini_read(text, "Settings", "MSAA")
    ssaa_raw = cfgutil.ini_read(text, "Settings", "SSAA")
    if msaa_raw is None or ssaa_raw is None:
        return None
    try:
        count = int(msaa_raw.strip(), 0)          # accepts 0x.. hex and decimal
    except (TypeError, ValueError):
        count = 1
    ssaa = ssaa_raw.strip().lower() in cfgutil._TRUE
    try:
        idx = _AA_MAP.index((count, ssaa))
        options = list(_AA_OPTIONS)
    except ValueError:                            # an unknown on-disk combo -- never lose it
        options = list(_AA_OPTIONS) + [f"{count}x {'SSAA' if ssaa else 'MSAA'} (current)"]
        idx = len(options) - 1
    return {"key": "_aa", "label": "Anti-aliasing", "type": "enum",
            "options": options, "value": idx}


def _aa_set(value):
    path = _FILES[GFX]
    text = cfgutil.read_text(path)
    if text is None:
        raise RpcError("ENOENT", f"{GFX} not found -- launch a game once to create it.")
    try:
        idx = int(float(value))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad anti-aliasing index {value!r}")
    if idx < 0:
        raise RpcError("EINVAL", f"anti-aliasing index {idx} out of range")
    if idx >= len(_AA_MAP):
        # the synthetic "Nx … (current)" option _aa_get appends for an unknown on-disk
        # combo -- re-affirming it is a no-op, not an error (matches the enum framework).
        row = _aa_get({GFX: text})
        return row["value"] if row else idx
    count, ssaa = _AA_MAP[idx]
    t = cfgutil.ini_replace(text, "Settings", "MSAA", f"0x{count:08x}")
    if t is None:
        raise RpcError("ENOKEY", "MSAA not present in GFX.ini [Settings]")
    t = cfgutil.ini_replace(t, "Settings", "SSAA", "True" if ssaa else "False")
    if t is None:
        raise RpcError("ENOKEY", "SSAA not present in GFX.ini [Settings]")
    if t != text:
        cfgutil.ensure_bak(path)
        cfgutil.atomic_write(path, t)
    row = _aa_get({GFX: cfgutil.read_text(path)})
    return row["value"] if row else idx


# -- multi-file get/set engine -------------------------------------------------
def _item_by_key(groups, key):
    for g in groups:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _candidate_sections(it):
    """The section(s) that may hold an item's key, in preference order. Most items
    have one; a version-drifted key (e.g. MaxAnisotropy) lists several."""
    return it.get("sections") or [it["section"]]


def _find_section(it, text):
    """The section that ACTUALLY holds it['key'] in `text` (first present of the
    candidates), or None if the key is present in none of them."""
    for sec in _candidate_sections(it):
        if cfgutil.ini_read(text, sec, it["key"]) is not None:
            return sec
    return None


def _get_item(it, texts):
    if it["type"] == "aa":
        return _aa_get(texts)
    text = texts.get(it["file"])
    if text is None:
        return None
    sec = _find_section(it, text)
    if sec is not None:
        raw = cfgutil.ini_read(text, sec, it["key"])
    else:
        if not it.get("create"):
            return None                            # not in THIS build -- don't offer it
        sec = _candidate_sections(it)[0]
        if cfgutil._ini_span(text, sec) is None:
            return None                            # create-item whose target section is absent
        raw = it.get("default", "")
    if it["type"] == "bool":
        return {"key": it["key"], "label": it["label"], "type": "bool",
                "value": cfgutil.bool_get(it, raw)}
    if it["type"] == "enum":
        disp, val = cfgutil._enum_get(it, raw)
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": disp, "value": val}
    if it["type"] in ("int", "float"):
        conv = int if it["type"] == "int" else float
        try:
            v = conv(float(raw))
        except (TypeError, ValueError):
            v = conv(it.get("min", 0))
        row = {"key": it["key"], "label": it["label"], "type": it["type"], "value": v}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def _do_get(groups):
    from .. import proc_guard
    texts = {name: cfgutil.read_text(p) for name, p in _FILES.items()}
    out = []
    for g in groups:
        settings = [row for it in g["items"] if (row := _get_item(it, texts)) is not None]
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    return {"exists": any(t is not None for t in texts.values()), "path": str(_DIR),
            "running": proc_guard.emulator_running(_PROC), "note": _NOTE, "groups": out}


def _set_item(item, value):
    path = _FILES[item["file"]]
    text = cfgutil.read_text(path)
    if text is None:
        raise RpcError("ENOENT", f"{item['file']} not found -- launch a game once to create it.")
    sec = _find_section(item, text)
    if sec is None:
        if not item.get("create"):
            raise RpcError("ENOKEY", f"{item['key']!r} not present in {item['file']}")
        sec = _candidate_sections(item)[0]
    cur = cfgutil.ini_read(text, sec, item["key"])
    write = cfgutil.compute_write(item, value, cur if cur is not None else item.get("default", ""))
    if cur is None and item.get("create"):
        new_text = cfgutil.ini_set_or_insert(text, sec, item["key"], write)
        if new_text is None:
            raise RpcError("ENOKEY", f"[{sec}] section missing in {item['file']}")
    else:
        new_text = cfgutil.ini_replace(text, sec, item["key"], write)
        if new_text is None:
            raise RpcError("ENOKEY", f"{item['key']!r} not present in {item['file']} [{sec}]")
    if new_text != text:
        cfgutil.ensure_bak(path)
        cfgutil.atomic_write(path, new_text)
    back = cfgutil.ini_read(cfgutil.read_text(path), sec, item["key"])
    if item["type"] == "bool":
        return cfgutil.bool_get(item, back or "")
    if item["type"] == "enum":
        _, v = cfgutil._enum_get(item, back if back is not None else "")
        return v
    if item["type"] == "float":
        try:
            return float(back)
        except (TypeError, ValueError):
            return back
    try:
        return int(float(back))
    except (TypeError, ValueError):
        return back


def _do_set(groups, params):
    from .. import proc_guard, staterev
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", "Dolphin is running -- close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    item = _item_by_key(groups, key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable Dolphin setting")
    value = _aa_set(params["value"]) if item["type"] == "aa" else _set_item(item, params["value"])
    staterev.bump("config")
    return {"key": key, "value": value}


def _register(ns, groups):
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return _do_get(groups)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        return _do_set(groups, params)


for _ns, (_title, _groups) in PAGES.items():
    _register(_ns, _groups)
