"""rpcs3_settings — full RPCS3 (PS3) GLOBAL settings tree for MAD, split into 5 category
namespaces (CPU / GPU / Audio / Advanced / Emulator), each rendered by the shipped
GuiMadPageEmuSettings with BUFFERED Save/Cancel — PCSX2 parity for the PlayStation 3 tile.

Buffered model (via pcsx2_engine.BufferedEngine + the YAML codec in rpcs3_engine):
`<ns>.get` returns {buffered:true} and the C++ shows SAVE/CANCEL; `<ns>.set` STAGES the
edit into an in-memory copy of config.yml; `<ns>.save` REPLAYS the staged edits onto a
FRESH read (so an external write to other keys is never clobbered), one-time .bak + atomic,
and bumps staterev; `<ns>.cancel` reloads. All 5 categories edit ONE file
(~/.config/rpcs3/config.yml); pages are modal so a single shared buffer is safe. Writes are
refused while rpcs3 runs (it rewrites config.yml on exit).

REALITY-CHECKED: every key here is present in the live config.yml (verified 2026-07-14), so
the installed build honors it. Enum tokens are the EXACT strings RPCS3 serializes (from each
`fmt_class_string<T>::format` switch in RPCS3 master `Emu/system_config_types.cpp` +
`system_config.h`; System-block enums from cellSysutil / KeyboardHandler); cached to
deck-docs/rpcs3-config-encodings.md. Enums use write_mode "option": the display label pairs
with its stored token by index, so display ORDER is free and an on-disk value outside the
curated list is preserved (prepended) by cfgutil._enum_get. RPCS3 bools are lowercase
true/false (cfgutil defaults). Deck defaults follow deck-docs/rpcs3.md (WCB off, Async
Texture Streaming off, Multithreaded RSX off, Shader Mode = Async multi-threaded, RSX FIFO =
Fast, Wake-Up Delay 0). Deeply-nested sub-maps (Performance Overlay/*, Custom Anaglyph) are
out of scope; the two Vulkan-subblock keys we DO expose (Asynchronous Texture Streaming,
Asynchronous Queue Scheduler) have names unique within the Video block, so cfgutil.yaml_*
scopes to them correctly.
"""
from __future__ import annotations

from pathlib import Path

from . import rpcs3_engine
from .rpc import method

_FILE = Path.home() / ".config/rpcs3/config.yml"
_PROC = "rpcs3"
_F = _FILE.name


# ── item builders ─────────────────────────────────────────────────────────────
def _bool(section, key, label):
    return {"key": key, "label": label, "file": _F, "section": section, "type": "bool"}


def _enum(section, key, label, disp, stored=None):
    return {"key": key, "label": label, "file": _F, "section": section, "type": "enum",
            "write_mode": "option", "options_display": disp,
            "options_stored": stored if stored is not None else disp}


def _int(section, key, label, **bounds):
    it = {"key": key, "label": label, "file": _F, "section": section, "type": "int"}
    it.update(bounds)
    return it


# ── CPU (ns rpcs3cpu) — [Core] ────────────────────────────────────────────────
_CPU_GROUPS = [
    {"title": "Decoders", "note": "", "items": [
        _enum("Core", "PPU Decoder", "PPU Decoder",
              ["Interpreter (static)", "Recompiler (LLVM)"]),
        _enum("Core", "SPU Decoder", "SPU Decoder",
              ["Interpreter (static)", "Interpreter (dynamic)",
               "Recompiler (ASMJIT)", "Recompiler (LLVM)"]),
    ]},
    {"title": "SPU", "note": "", "items": [
        _enum("Core", "SPU Block Size", "SPU Block Size", ["Safe", "Mega", "Giga"]),
        _enum("Core", "SPU XFloat Accuracy", "SPU XFloat Accuracy",
              ["Accurate", "Approximate", "Relaxed", "Inaccurate"]),
        _int("Core", "Preferred SPU Threads", "Preferred SPU Threads (0 = auto)",
             min=0, max=6, step=1),
        _int("Core", "Max SPURS Threads", "Max SPURS Threads", min=1, max=6, step=1),
        _bool("Core", "SPU loop detection", "SPU Loop Detection"),
    ]},
    {"title": "Threading", "note": "", "items": [
        _enum("Core", "Thread Scheduler Mode", "Thread Scheduler Mode",
              ["Operating System", "RPCS3 Scheduler", "RPCS3 Alternative Scheduler"]),
        _int("Core", "Clocks scale", "Clocks Scale (%)", min=10, max=3000, step=5),
        _int("Core", "Max LLVM Compile Threads", "Max LLVM Compile Threads (0 = all)",
             min=0, max=16, step=1),
    ]},
    {"title": "RSX", "note": "", "items": [
        _enum("Core", "RSX FIFO Fetch Accuracy", "RSX FIFO Fetch Accuracy",
              ["Fast", "Atomic", "Ordered & Atomic", "PS3"]),
    ]},
]

# ── GPU (ns rpcs3gpu) — [Video] (incl. two unique nested Vulkan keys) ──────────
_GPU_GROUPS = [
    {"title": "Renderer & display", "note": "", "items": [
        _enum("Video", "Renderer", "Renderer", ["Vulkan", "OpenGL", "Null"]),
        _enum("Video", "Resolution", "Resolution",
              ["1280x720", "1920x1080", "720x480", "720x576",
               "1600x1080", "1440x1080", "1280x1080", "960x1080"]),
        _int("Video", "Resolution Scale", "Resolution Scale (%)", min=25, max=800, step=25),
        _enum("Video", "Aspect ratio", "Aspect Ratio", ["16:9", "4:3"]),
        _bool("Video", "Stretch To Display Area", "Stretch to Display Area"),
    ]},
    {"title": "Frame pacing", "note": "", "items": [
        _enum("Video", "Frame limit", "Frame Limit",
              ["Off", "30", "50", "60", "120", "Display", "Auto", "PS3 Native", "Infinite"]),
        _enum("Video", "VSync Mode", "VSync", ["Disabled", "Adaptive", "Full"]),
        _bool("Video", "Multithreaded RSX", "Multithreaded RSX"),
    ]},
    {"title": "Shaders", "note": "", "items": [
        _enum("Video", "Shader Mode", "Shader Mode",
              ["Async Recompiler (multi-threaded)", "Async Recompiler with Shader Interpreter",
               "Legacy Recompiler (single-threaded)", "Shader Interpreter only"]),
        _enum("Video", "Shader Precision", "Shader Precision",
              ["Auto", "Low", "High", "Ultra"]),
        _bool("Video", "Asynchronous Texture Streaming", "Async Texture Streaming"),
        _bool("Video", "Write Color Buffers", "Write Color Buffers"),
    ]},
    {"title": "Quality", "note": "", "items": [
        _enum("Video", "Anisotropic Filter Override", "Anisotropic Filtering",
              ["Automatic", "2x", "4x", "8x", "16x"], ["0", "2", "4", "8", "16"]),
        _enum("Video", "MSAA", "Anti-Aliasing (MSAA)", ["Disabled", "Auto"]),
        _enum("Video", "Output Scaling Mode", "Output Scaling",
              ["Bilinear", "Nearest", "FidelityFX Super Resolution"]),
        _int("Video", "FidelityFX CAS Sharpening Intensity", "FidelityFX CAS Sharpening",
             min=0, max=100, step=1),
        _enum("Video", "Asynchronous Queue Scheduler", "Vulkan Async Scheduler",
              ["Safe", "Fast"]),
    ]},
]

# ── AUDIO (ns rpcs3aud) — [Audio] ─────────────────────────────────────────────
_AUD_GROUPS = [
    {"title": "Output", "note": "", "items": [
        _enum("Audio", "Renderer", "Audio Backend", ["Cubeb", "FAudio", "Null"]),
        _enum("Audio", "Audio Format", "Audio Format",
              ["Stereo", "Surround 5.1", "Surround 7.1", "Automatic", "Manual"]),
        _enum("Audio", "Audio Channel Layout", "Channel Layout",
              ["Automatic", "Mono", "Stereo", "Stereo LFE", "Quadraphonic",
               "Quadraphonic LFE", "Surround 5.1", "Surround 7.1"]),
        _int("Audio", "Master Volume", "Master Volume (%)", min=0, max=200, step=1),
    ]},
    {"title": "Buffering", "note": "", "items": [
        _int("Audio", "Desired Audio Buffer Duration", "Buffer Duration (ms)",
             min=4, max=250, step=1),
        _bool("Audio", "Enable Buffering", "Enable Buffering"),
        _bool("Audio", "Enable Time Stretching", "Enable Time Stretching"),
        _int("Audio", "Time Stretching Threshold", "Time Stretching Threshold (%)",
             min=0, max=100, step=1),
    ]},
    {"title": "Misc", "note": "", "items": [
        _bool("Audio", "Convert to 16 bit", "Convert to 16-bit"),
        _enum("Audio", "Music Handler", "Music Handler", ["Qt", "Null"]),
    ]},
]

# ── ADVANCED (ns rpcs3adv) — niche [Core] + [Savestate] + [Net] + [VFS] + [I/O] ─
_ADV_GROUPS = [
    {"title": "CPU accuracy", "note": "Leave at defaults unless a game needs it.", "items": [
        _bool("Core", "Accurate RSX reservation access", "Accurate RSX Reservation Access"),
        _bool("Core", "Accurate SPU Reservations", "Accurate SPU Reservations"),
        _bool("Core", "Accurate SPU DMA", "Accurate SPU DMA"),
        _int("Core", "PPU Threads", "PPU Threads", min=1, max=8, step=1),
    ]},
    {"title": "Save states", "note": "", "items": [
        _bool("Savestate", "Suspend Emulation Savestate Mode", "Suspend-Emulation Savestates"),
        _bool("Savestate", "Compatible Savestate Mode", "Compatible Savestate Mode"),
        _bool("Savestate", "Start Paused", "Start Paused"),
        _bool("Savestate", "Save Disc Game Data", "Save Disc Game Data"),
        _int("Savestate", "Maximum SaveState Files", "Max Savestate Files", min=0, max=64, step=1),
    ]},
    {"title": "Network", "note": "", "items": [
        _enum("Net", "Internet enabled", "Internet", ["Disconnected", "Connected"]),
        _enum("Net", "PSN status", "PSN", ["Disconnected", "Simulated", "RPCN"]),
        _bool("Net", "UPNP Enabled", "Enable UPnP"),
    ]},
    {"title": "Storage", "note": "", "items": [
        _bool("VFS", "Empty /dev_hdd0/tmp/", "Empty /dev_hdd0/tmp/ on boot"),
        _bool("VFS", "Enable /host_root/", "Enable /host_root/ (unsafe)"),
        _bool("VFS", "Limit disk cache size", "Limit Disk Cache Size"),
    ]},
    {"title": "Input", "note": "", "items": [
        _bool("Input/Output", "Background input enabled", "Background Input"),
        _bool("Input/Output", "Keep pads connected", "Keep Pads Connected"),
        _bool("Input/Output", "Show move cursor", "Show Move Cursor"),
    ]},
]

# ── EMULATOR (ns rpcs3emu) — [System] + [Miscellaneous] ───────────────────────
_LANGS = ["Japanese", "English (US)", "French", "Spanish", "German", "Italian", "Dutch",
          "Portuguese (Portugal)", "Russian", "Korean", "Chinese (Traditional)",
          "Chinese (Simplified)", "Finnish", "Swedish", "Danish", "Norwegian", "Polish",
          "Portuguese (Brazil)", "English (UK)", "Turkish"]
_KBD = ["English keyboard (US standard)", "Japanese keyboard", "Japanese keyboard (Kana state)",
        "German keyboard", "Spanish keyboard", "French keyboard", "Italian keyboard",
        "Dutch keyboard", "Portuguese keyboard (Portugal)", "Russian keyboard",
        "English keyboard (UK standard)", "Korean keyboard", "Norwegian keyboard",
        "Finnish keyboard", "Danish keyboard", "Swedish keyboard",
        "Chinese keyboard (Traditional)", "Chinese keyboard (Simplified)",
        "French keyboard (Switzerland)", "German keyboard (Switzerland)",
        "French keyboard (Canada)", "French keyboard (Belgium)", "Polish keyboard",
        "Portuguese keyboard (Brazil)", "Turkish keyboard"]
_EMU_GROUPS = [
    {"title": "Region & language", "note": "", "items": [
        _enum("System", "Language", "System Language", _LANGS),
        _enum("System", "Enter button assignment", "Enter Button",
              ["Enter with cross", "Enter with circle"]),
        _enum("System", "License Area", "Console Region",
              ["SCEA", "SCEE", "SCEJ", "SCEH", "SCEK", "SCH", "Other"]),
        _enum("System", "Keyboard Type", "Keyboard Type", _KBD),
        _enum("System", "Date Format", "Date Format", ["ddmmyyyy", "mmddyyyy", "yyyymmdd"]),
        _enum("System", "Time Format", "Time Format", ["clock24", "clock12"]),
    ]},
    {"title": "Startup & window", "note": "", "items": [
        _bool("Miscellaneous", "Start games in fullscreen mode", "Start Games in Fullscreen"),
        _bool("Miscellaneous", "Automatically start games after boot", "Auto-Start Games After Boot"),
        _bool("Miscellaneous", "Exit RPCS3 when process finishes", "Exit RPCS3 When Game Ends"),
        _bool("Miscellaneous", "Pause emulation on RPCS3 focus loss", "Pause on Focus Loss"),
        _bool("Miscellaneous", "Pause Emulation During Home Menu", "Pause During Home Menu"),
        _bool("Miscellaneous", "Use native user interface", "Use Native UI"),
        _bool("Miscellaneous", "Prevent display sleep while running games", "Prevent Display Sleep"),
    ]},
    {"title": "Notifications", "note": "", "items": [
        _bool("Miscellaneous", "Show trophy popups", "Show Trophy Popups"),
        _bool("Miscellaneous", "Show RPCN popups", "Show RPCN Popups"),
        _bool("Miscellaneous", "Play music during boot sequence", "Play Boot Music"),
    ]},
]

# ── category registry ─────────────────────────────────────────────────────────
CATEGORIES = {
    "rpcs3cpu": ("CPU", _CPU_GROUPS),
    "rpcs3gpu": ("GPU", _GPU_GROUPS),
    "rpcs3aud": ("Audio", _AUD_GROUPS),
    "rpcs3adv": ("Advanced", _ADV_GROUPS),
    "rpcs3emu": ("Emulator", _EMU_GROUPS),
}

# The buffer stays MODULE-LEVEL (not owned by the engine) so the test suite can
# monkeypatch _FILE / _running / _buf and drive the engine hermetically; each verb
# builds a lightweight engine from the CURRENT globals (mirrors pcsx2_settings).
_buf: dict = rpcs3_engine.new_buf()


def _running() -> bool:
    from .. import proc_guard
    return proc_guard.emulator_running(_PROC)


def _engine():
    return rpcs3_engine.engine(_FILE, _running, CATEGORIES, _buf)


def _get(ns: str) -> dict:
    return _engine().get(ns)


def _set(ns: str, params: dict) -> dict:
    return _engine().set(ns, params)


def _save(ns: str) -> dict:
    return _engine().save(ns)


def _cancel(ns: str) -> dict:
    return _engine().cancel(ns)


# ── RPC registration: <ns>.get/.set/.save/.cancel for each category ───────────
def _register(ns: str) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, ns=ns):
        return _get(ns)

    @method(f"{ns}.set", slow=True)
    def _s(params, ns=ns):
        return _set(ns, params)

    @method(f"{ns}.save", slow=True)
    def _sv(params, ns=ns):
        return _save(ns)

    @method(f"{ns}.cancel", slow=True)
    def _c(params, ns=ns):
        return _cancel(ns)


for _ns in CATEGORIES:
    _register(_ns)
