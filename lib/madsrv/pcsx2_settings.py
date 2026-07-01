"""pcsx2_settings — full PCSX2 GLOBAL settings tree for MAD, split into 5 category
namespaces (Emulation / Graphics / On-Screen Display / Audio / Advanced), each rendered
by the shipped GuiMadPageEmuSettings with BUFFERED Save/Cancel.

Buffered model (mirrors lindbergh_cmds._buf): `<ns>.get` returns {buffered:true} and the
C++ shows SAVE/CANCEL; `<ns>.set` STAGES the edit into an in-memory copy of PCSX2.ini;
`<ns>.save` writes it (one-time .bak + atomic) and bumps staterev; `<ns>.cancel` reloads.
All 5 categories edit ONE file (~/.config/PCSX2/inis/PCSX2.ini); pages are modal so a
single shared buffer is safe. Switching category (or a clean re-fetch) reloads fresh; a
dirty same-category re-fetch preserves staged edits.

REALITY-CHECKED: a setting is only offered when its key is present in the live PCSX2.ini
(verified 2026-07-01), so the installed AppImage actually honors it — master-only keys
(OsdMargin, HWAccurateAlphaTest, ...) and Windows-only widgets are omitted. Encodings +
sources: deck-docs/pcsx2-ini-encodings.md. Byte-preserving single-key edits via cfgutil;
refuses while pcsx2-qt is running.

Item dict = cfgutil's schema (key, label, section, type bool/enum/int/float, write_mode,
options_display/_stored, bool_true/false, min/max/step) PLUS one composite type:
  type "clamp"  clamp_keys=[k0,k1,k2]  options_display=[...]  — a 4-way enum stored as a
                triple of bools (idx>=1, idx>=2, idx>=3), mirroring PCSX2's setClampingMode.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method, RpcError

_FILE = Path.home() / ".config/PCSX2/inis/PCSX2.ini"
_PROC = "pcsx2"
_F = _FILE.name

# ── reusable option tables ────────────────────────────────────────────────────
# Speed scalars ([Framerate]) — FLOAT token presets (bare-int for whole values).
_SPEED_DISP = ["Unlimited", "10%", "25%", "50%", "75%", "90%", "100%", "110%",
               "125%", "150%", "175%", "200%", "300%", "400%", "500%", "1000%"]
_SPEED_STORED = ["0", "0.1", "0.25", "0.5", "0.75", "0.9", "1", "1.1",
                 "1.25", "1.5", "1.75", "2", "3", "4", "5", "10"]
_ROUND_DISP = ["Nearest", "Negative", "Positive", "Chop / Zero"]        # FPRoundMode 0..3
_OSD_POS = ["None", "Top Left", "Top Center", "Top Right", "Center Left", "Center",
            "Center Right", "Bottom Left", "Bottom Center", "Bottom Right"]  # OsdOverlayPos 0..9


def _bool(key, label, section="EmuCore/GS"):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "bool", "bool_true": "true", "bool_false": "false"}


def _enum_idx(key, label, options, section="EmuCore/GS"):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "enum", "write_mode": "index", "options_display": options}


def _enum_opt(key, label, disp, stored, section="EmuCore/GS"):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "enum", "write_mode": "option",
            "options_display": disp, "options_stored": stored}


def _int(key, label, section="EmuCore/GS", **bounds):
    it = {"key": key, "label": label, "file": _F, "section": section, "type": "int"}
    it.update(bounds)
    return it


def _float(key, label, section="EmuCore/GS", **bounds):
    it = {"key": key, "label": label, "file": _F, "section": section, "type": "float"}
    it.update(bounds)
    return it


def _clamp(key, label, keys, disp):
    return {"key": key, "label": label, "file": _F, "section": "EmuCore/CPU/Recompiler",
            "type": "clamp", "clamp_keys": keys, "options_display": disp}


def _float_scaled(key, label, section, scale, **bounds):
    """A float stored as value/scale, presented to the C++ as an INT stepper in the
    scaled units (mirrors PCSX2's own x10/x100 normalized sliders). Needed because the
    C++ float stepper only shows one decimal, so fine floats (0.01 granularity) would
    be unreachable and pre-tuned values would be coarsened. min/max/step are in the
    SCALED int units (e.g. scale=100, min=-100, max=100 -> stored float -1.00..1.00)."""
    it = {"key": key, "label": label, "file": _F, "section": section,
          "type": "float_scaled", "scale": scale}
    it.update(bounds)
    return it


def _speed(key, label):
    return _enum_opt(key, label, _SPEED_DISP, _SPEED_STORED, section="Framerate")


# ── EMULATION (ns pcsx2emu) ───────────────────────────────────────────────────
EMU_GROUPS = [
    {"title": "Speed Control", "note": "", "items": [
        _speed("NominalScalar", "Normal Speed"),
        _speed("TurboScalar", "Fast-Forward Speed"),
        _speed("SlomoScalar", "Slow-Motion Speed"),
    ]},
    {"title": "System", "note": "", "items": [
        _enum_opt("EECycleRate", "EE Cycle Rate",
                  ["50% (Underclock)", "60% (Underclock)", "75% (Underclock)",
                   "100% (Normal)", "130% (Overclock)", "180% (Overclock)", "300% (Overclock)"],
                  ["-3", "-2", "-1", "0", "1", "2", "3"], section="EmuCore/Speedhacks"),
        _enum_idx("EECycleSkip", "EE Cycle Skip",
                  ["Disabled", "Mild Underclock", "Moderate Underclock", "Maximum Underclock"],
                  section="EmuCore/Speedhacks"),
        _bool("vuThread", "Enable Multi-Threaded VU1 (MTVU)", section="EmuCore/Speedhacks"),
        _bool("EnableThreadPinning", "Enable Thread Pinning", section="EmuCore"),
        _bool("EnableCheats", "Enable Cheats", section="EmuCore"),
        _bool("HostFs", "Enable Host Filesystem", section="EmuCore"),
        _bool("CdvdPrecache", "Enable CDVD Precaching", section="EmuCore"),
    ]},
    {"title": "Frame Pacing", "note": "", "items": [
        _enum_opt("VsyncQueueSize", "Frame Pacing / Max Latency",
                  ["Optimal Frame Pacing", "1 frame", "2 frames", "3 frames", "4 frames", "5 frames"],
                  ["0", "1", "2", "3", "4", "5"]),
        _bool("SyncToHostRefreshRate", "Sync to Host Refresh Rate"),
        _bool("UseVSyncForTiming", "Use Host VSync Timing"),
        _bool("VsyncEnable", "Vertical Sync (VSync)"),
        _bool("SkipDuplicateFrames", "Skip Presenting Duplicate Frames"),
    ]},
    {"title": "Save States", "note": "", "items": [
        _enum_idx("SavestateCompressionType", "Compression Method",
                  ["Uncompressed", "Deflate", "Zstandard"], section="EmuCore"),
        _enum_idx("SavestateCompressionRatio", "Compression Level",
                  ["Low", "Medium", "High", "Very High"], section="EmuCore"),
        _bool("BackupSavestate", "Create Save State Backups", section="EmuCore"),
        _bool("SaveStateOnShutdown", "Save State On Shutdown", section="EmuCore"),
    ]},
    {"title": "Boot & Patches", "note": "", "items": [
        _bool("EnableFastBoot", "Fast Boot (skip BIOS logo)", section="EmuCore"),
        _bool("EnableGameFixes", "Enable Game Fixes", section="EmuCore"),
        _bool("EnablePatches", "Enable Compatibility Patches", section="EmuCore"),
    ]},
    {"title": "PINE", "note": "", "items": [
        _bool("EnablePINE", "Enable PINE", section="EmuCore"),
        _int("PINESlot", "PINE Slot", section="EmuCore", min=0, max=65535, step=1),
    ]},
]

# ── ADVANCED (ns pcsx2adv) ────────────────────────────────────────────────────
_CLAMP_EE = ["None", "Normal", "Extra + Preserve Sign", "Full"]
_CLAMP_VU = ["None", "Normal", "Extra", "Extra + Preserve Sign"]
ADV_GROUPS = [
    {"title": "EmotionEngine (EE)", "note": "", "items": [
        _enum_idx("FPU.Roundmode", "FPU Round Mode", _ROUND_DISP, section="EmuCore/CPU"),
        _enum_idx("FPUDiv.Roundmode", "FPU Division Round Mode", _ROUND_DISP, section="EmuCore/CPU"),
        _clamp("EEClampMode", "EE Clamping Mode",
               ["fpuOverflow", "fpuExtraOverflow", "fpuFullMode"], _CLAMP_EE),
        _bool("EnableEE", "Enable EE Recompiler", section="EmuCore/CPU/Recompiler"),
        _bool("EnableEECache", "Enable EE Cache (Slow)", section="EmuCore/CPU/Recompiler"),
        _bool("EnableFastmem", "Enable Fast Memory Access", section="EmuCore/CPU/Recompiler"),
        _bool("PauseOnTLBMiss", "Pause On TLB Miss", section="EmuCore/CPU/Recompiler"),
        _bool("WaitLoop", "EE Wait Loop Detection", section="EmuCore/Speedhacks"),
        _bool("IntcStat", "INTC Spin Detection", section="EmuCore/Speedhacks"),
        _bool("ExtraMemory", "Enable 128MB RAM (Dev)", section="EmuCore/CPU"),
    ]},
    {"title": "Vector Units (VU)", "note": "", "items": [
        _enum_idx("VU0.Roundmode", "VU0 Round Mode", _ROUND_DISP, section="EmuCore/CPU"),
        _enum_idx("VU1.Roundmode", "VU1 Round Mode", _ROUND_DISP, section="EmuCore/CPU"),
        _clamp("VU0ClampMode", "VU0 Clamping Mode",
               ["vu0Overflow", "vu0ExtraOverflow", "vu0SignOverflow"], _CLAMP_VU),
        _clamp("VU1ClampMode", "VU1 Clamping Mode",
               ["vu1Overflow", "vu1ExtraOverflow", "vu1SignOverflow"], _CLAMP_VU),
        _bool("EnableVU0", "Enable VU0 Recompiler", section="EmuCore/CPU/Recompiler"),
        _bool("EnableVU1", "Enable VU1 Recompiler", section="EmuCore/CPU/Recompiler"),
        _bool("vuFlagHack", "VU Flag Optimization Hack", section="EmuCore/Speedhacks"),
        _bool("vu1Instant", "Enable Instant VU1", section="EmuCore/Speedhacks"),
    ]},
    {"title": "I/O Processor (IOP)", "note": "", "items": [
        _bool("EnableIOP", "Enable IOP Recompiler", section="EmuCore/CPU/Recompiler"),
    ]},
]

# ── GRAPHICS (ns pcsx2gfx) — one group == one emulator tab ────────────────────
GFX_GROUPS = [
    {"title": "Renderer", "note": "", "items": [
        _enum_opt("Renderer", "Renderer", ["Automatic", "Vulkan", "OpenGL", "Software"],
                  ["-1", "14", "12", "13"]),
    ]},
    {"title": "Display", "note": "", "items": [
        _enum_opt("AspectRatio", "Aspect Ratio",
                  ["Fit to Window / Fullscreen", "Auto Standard (4:3 / 3:2)", "Standard (4:3)",
                   "Widescreen (16:9)", "Native (10:7)"],
                  ["Stretch", "Auto 4:3/3:2", "4:3", "16:9", "10:7"]),
        _enum_opt("FMVAspectRatioSwitch", "FMV Aspect Ratio",
                  ["Off", "Auto Standard (4:3 / 3:2)", "Standard (4:3)", "Widescreen (16:9)", "Native (10:7)"],
                  ["Off", "Auto 4:3/3:2", "4:3", "16:9", "10:7"]),
        _enum_idx("deinterlace_mode", "Deinterlacing",
                  ["Automatic", "No Deinterlacing", "Weave TFF", "Weave BFF", "Bob TFF", "Bob BFF",
                   "Blend TFF", "Blend BFF", "Adaptive TFF", "Adaptive BFF"]),
        _enum_idx("linear_present_mode", "Bilinear Filtering (Display)",
                  ["None", "Bilinear (Smooth)", "Bilinear (Sharp)"]),
        _bool("IntegerScaling", "Integer Scaling"),
        _bool("pcrtc_antiblur", "Anti-Blur"),
        _bool("pcrtc_offsets", "Screen Offsets"),
        _bool("pcrtc_overscan", "Show Overscan"),
        _bool("disable_interlace_offset", "Disable Interlace Offset"),
        _int("StretchY", "Vertical Stretch (%)", min=1, max=300, step=1),
        _int("CropLeft", "Crop Left (px)", min=0, max=1000, step=1),
        _int("CropTop", "Crop Top (px)", min=0, max=1000, step=1),
        _int("CropRight", "Crop Right (px)", min=0, max=1000, step=1),
        _int("CropBottom", "Crop Bottom (px)", min=0, max=1000, step=1),
        _bool("EnableWideScreenPatches", "Enable Widescreen Patches (global)", section="EmuCore"),
        _bool("EnableNoInterlacingPatches", "Enable No-Interlacing Patches (global)", section="EmuCore"),
    ]},
    {"title": "Rendering (Hardware)", "note": "", "items": [
        _enum_opt("upscale_multiplier", "Internal Resolution",
                  ["Native (PS2)", "2x", "3x", "4x", "5x", "6x", "7x", "8x", "10x", "12x"],
                  ["1", "2", "3", "4", "5", "6", "7", "8", "10", "12"]),
        _enum_idx("filter", "Texture Filtering",
                  ["Nearest", "Bilinear (Forced)", "Bilinear (PS2)", "Bilinear (Forced excl. sprite)"]),
        _enum_opt("TriFilter", "Trilinear Filtering",
                  ["Automatic", "Off", "Trilinear (PS2)", "Trilinear (Forced)"],
                  ["-1", "0", "1", "2"]),
        _enum_opt("MaxAnisotropy", "Anisotropic Filtering",
                  ["Off", "2x", "4x", "8x", "16x"], ["0", "2", "4", "8", "16"]),
        _enum_idx("dithering_ps2", "Dithering", ["Off", "Scaled", "Unscaled", "Force 32-bit"]),
        _bool("hw_mipmap", "Mipmapping"),
        _enum_idx("accurate_blending_unit", "Blending Accuracy",
                  ["Minimum", "Basic", "Medium", "High", "Full", "Maximum"]),
    ]},
    {"title": "Rendering (Software)", "note": "", "items": [
        _int("extrathreads", "Software Rendering Threads", min=0, max=32, step=1),
        _bool("autoflush_sw", "Auto Flush (Software)"),
        _bool("mipmap", "Mipmapping (Software)"),
    ]},
    {"title": "Post-Processing", "note": "", "items": [
        _enum_idx("CASMode", "Contrast Adaptive Sharpening",
                  ["None", "Sharpen Only", "Sharpen and Resize"]),
        _int("CASSharpness", "CAS Sharpness (%)", min=0, max=100, step=1),
        _bool("fxaa", "FXAA"),
        _bool("ShadeBoost", "Shade Boost"),
        _int("ShadeBoost_Brightness", "Shade Boost Brightness", min=1, max=100, step=1),
        _int("ShadeBoost_Contrast", "Shade Boost Contrast", min=1, max=100, step=1),
        _int("ShadeBoost_Gamma", "Shade Boost Gamma", min=1, max=100, step=1),
        _int("ShadeBoost_Saturation", "Shade Boost Saturation", min=1, max=100, step=1),
        _enum_idx("TVShader", "TV Shader",
                  ["None", "Scanline Filter", "Diagonal Filter", "Triangular Filter", "Wave Filter",
                   "Lottes CRT", "4xRGSS Downsampling", "NxAGSS Downsampling"]),
    ]},
    {"title": "Media Capture", "note": "", "items": [
        _enum_idx("ScreenshotSize", "Screenshot Resolution",
                  ["Display Resolution", "Internal Resolution", "Internal (No Aspect Correction)"]),
        _enum_idx("ScreenshotFormat", "Screenshot Format", ["PNG", "JPEG", "WebP"]),
        _int("ScreenshotQuality", "Screenshot Quality", min=1, max=100, step=1),
        _bool("EnableVideoCapture", "Enable Video Capture"),
        _int("VideoCaptureBitrate", "Video Bitrate (kbps)", min=100, max=200000, step=100),
        _bool("VideoCaptureAutoResolution", "Auto Video Resolution"),
        _int("VideoCaptureWidth", "Video Width", min=320, max=32768, step=16),
        _int("VideoCaptureHeight", "Video Height", min=240, max=32768, step=16),
        _bool("EnableAudioCapture", "Enable Audio Capture"),
        _int("AudioCaptureBitrate", "Audio Bitrate (kbps)", min=16, max=2048, step=1),
    ]},
    {"title": "Advanced (GS)", "note": "", "items": [
        _enum_idx("texture_preloading", "Texture Preloading", ["None", "Partial", "Full (Hash Cache)"]),
        _enum_idx("GSDumpCompression", "GS Dump Compression", ["Uncompressed", "LZMA (xz)", "Zstandard"]),
        _enum_opt("OverrideTextureBarriers", "Override Texture Barriers",
                  ["Automatic", "Force Disabled", "Force Enabled"], ["-1", "0", "1"]),
        _bool("ExtendedUpscalingMultipliers", "Extended Upscaling Multipliers"),
        _bool("DisableFramebufferFetch", "Disable Framebuffer Fetch"),
        _bool("DisableShaderCache", "Disable Shader Cache"),
        _bool("DisableVertexShaderExpand", "Disable Vertex Shader Expand"),
        _bool("DisableMailboxPresentation", "Disable Mailbox Presentation"),
        _bool("HWSpinCPUForReadbacks", "Spin CPU for Readbacks"),
        _bool("HWSpinGPUForReadbacks", "Spin GPU for Readbacks"),
        _bool("UseDebugDevice", "Use Debug Device"),
        # Curated rate presets (not a raw float stepper): the C++ float stepper only shows
        # one decimal, so the PS2-correct 59.94 could never be selected/restored. An
        # out-of-list on-disk value is preserved (prepended) by the enum reader.
        _enum_opt("FramerateNTSC", "NTSC Frame Rate (Hz)",
                  ["59.94 (NTSC)", "60", "50", "30"], ["59.94", "60", "50", "30"]),
        _enum_opt("FrameratePAL", "PAL Frame Rate (Hz)",
                  ["50 (PAL)", "60", "59.94", "25"], ["50", "60", "59.94", "25"]),
    ]},
]

# ── ON-SCREEN DISPLAY (ns pcsx2osd) — keys in [EmuCore/GS] ─────────────────────
OSD_GROUPS = [
    {"title": "Appearance", "note": "", "items": [
        _float("OsdScale", "OSD Scale (%)", min=50, max=500, step=1),
        _enum_idx("OsdMessagesPos", "Messages Position", _OSD_POS),
        _enum_idx("OsdPerformancePos", "Performance Position", _OSD_POS),
    ]},
    {"title": "Show", "note": "", "items": [
        _bool("OsdShowSpeed", "Show Emulation Speed"),
        _bool("OsdShowFPS", "Show FPS"),
        _bool("OsdShowVPS", "Show VPS"),
        _bool("OsdShowResolution", "Show Resolution"),
        _bool("OsdShowGSStats", "Show GS Statistics"),
        _bool("OsdShowCPU", "Show CPU Usage"),
        _bool("OsdShowGPU", "Show GPU Usage"),
        _bool("OsdShowIndicators", "Show Indicators"),
        _bool("OsdShowSettings", "Show Settings Overlay"),
        _bool("OsdShowInputs", "Show Inputs"),
        _bool("OsdShowFrameTimes", "Show Frame Times Graph"),
        _bool("OsdShowVersion", "Show Version"),
        _bool("OsdShowHardwareInfo", "Show Hardware Info"),
        _bool("OsdShowVideoCapture", "Show Video Capture Status"),
        _bool("OsdShowInputRec", "Show Input Recording Status"),
    ]},
]

# ── AUDIO (ns pcsx2aud) — all keys in [SPU2/Output] ───────────────────────────
_SPU = "SPU2/Output"
AUD_GROUPS = [
    {"title": "Volume", "note": "", "items": [
        _int("OutputVolume", "Volume (%)", section=_SPU, min=0, max=200, step=1),
        _int("FastForwardVolume", "Fast-Forward Volume (%)", section=_SPU, min=0, max=200, step=1),
        _bool("OutputMuted", "Mute All Sound", section=_SPU),
    ]},
    {"title": "Output", "note": "", "items": [
        _enum_opt("Backend", "Audio Backend", ["Null", "Cubeb", "SDL"], ["Null", "Cubeb", "SDL"], section=_SPU),
        _enum_opt("SyncMode", "Synchronization", ["Disabled (Noisy)", "TimeStretch (Recommended)"],
                  ["Disabled", "TimeStretch"], section=_SPU),
        _int("BufferMS", "Buffer Size (ms)", section=_SPU, min=15, max=500, step=1),
        _int("OutputLatencyMS", "Output Latency (ms)", section=_SPU, min=15, max=200, step=1),
        _bool("OutputLatencyMinimal", "Minimal Output Latency", section=_SPU),
        _enum_opt("ExpansionMode", "Expansion Mode",
                  ["Disabled (Stereo)", "Stereo with LFE", "Quadraphonic", "Quadraphonic with LFE",
                   "5.1 Surround", "7.1 Surround"],
                  ["Disabled", "StereoLFE", "Quadraphonic", "QuadraphonicLFE", "Surround51", "Surround71"],
                  section=_SPU),
    ]},
    {"title": "Expansion Tuning", "note": "Only affects output when Expansion Mode is not Disabled.", "items": [
        # PCSX2 forces block size to a power of two on load (AudioStream.cpp bit_ceil), so offer pow2 only.
        _enum_opt("ExpandBlockSize", "Block Size",
                  ["128", "256", "512", "1024", "2048", "4096", "8192"],
                  ["128", "256", "512", "1024", "2048", "4096", "8192"], section=_SPU),
        _float("ExpandCircularWrap", "Circular Wrap", section=_SPU, min=0, max=360, step=1),
        # shift/focus/center are x100 normalized sliders (clamps -1..1 / 0..1); scaled-int -> 0.01 steps.
        _float_scaled("ExpandShift", "Shift (x0.01)", _SPU, 100, min=-100, max=100, step=1),
        _float("ExpandDepth", "Depth", section=_SPU, min=0, max=5, step=0.1),
        _float_scaled("ExpandFocus", "Focus (x0.01)", _SPU, 100, min=-100, max=100, step=1),
        _float_scaled("ExpandCenterImage", "Center Image (x0.01)", _SPU, 100, min=0, max=100, step=1),
        _float("ExpandFrontSeparation", "Front Separation", section=_SPU, min=0, max=10, step=0.1),
        _float("ExpandRearSeparation", "Rear Separation", section=_SPU, min=0, max=10, step=0.1),
        _int("ExpandLowCutoff", "Low Cutoff", section=_SPU, min=0, max=100, step=1),
        _int("ExpandHighCutoff", "High Cutoff", section=_SPU, min=0, max=100, step=1),
    ]},
    {"title": "Time Stretch Tuning", "note": "Only affects output when Synchronization is TimeStretch.", "items": [
        _int("StretchSequenceLengthMS", "Sequence Length (ms)", section=_SPU, min=20, max=100, step=1),
        _int("StretchSeekWindowMS", "Seek Window (ms)", section=_SPU, min=10, max=30, step=1),
        _int("StretchOverlapMS", "Overlap (ms)", section=_SPU, min=5, max=15, step=1),
        _bool("StretchUseQuickSeek", "Use Quick Seek", section=_SPU),
        _bool("StretchUseAAFilter", "Use Anti-Aliasing Filter", section=_SPU),
    ]},
]

# ── category registry ─────────────────────────────────────────────────────────
CATEGORIES = {
    "pcsx2emu": ("Emulation", EMU_GROUPS),
    "pcsx2gfx": ("Graphics", GFX_GROUPS),
    "pcsx2osd": ("On-Screen Display", OSD_GROUPS),
    "pcsx2aud": ("Audio", AUD_GROUPS),
    "pcsx2adv": ("Advanced", ADV_GROUPS),
}


# ── shared buffered engine ────────────────────────────────────────────────────
# One buffer for the shared PCSX2.ini. ns tracks which category page owns the
# current staged edits so switching category discards the previous page's unsaved
# changes (a fresh reload), while a dirty same-category re-fetch preserves them.
# `edits` = the ordered (key, value) pairs staged since the last reload; _save REPLAYS
# them onto a FRESH read of the file (not the possibly-stale whole-text buffer), so an
# external write to other keys between load and save is never clobbered.
_buf: dict = {"ns": None, "text": None, "disk": None, "dirty": False, "edits": []}


def _reload() -> None:
    text = cfgutil.read_text(_FILE)
    _buf["text"] = text
    _buf["disk"] = text
    _buf["dirty"] = False
    _buf["edits"] = []


def _running() -> bool:
    from .. import proc_guard
    return proc_guard.emulator_running(_PROC)


def _clamp_index(bits: list[bool]) -> int:
    """Number of leading True bits (F,F,F)->0 .. (T,T,T)->3; stops at the first
    False so an inconsistent on-disk triple degrades gracefully."""
    idx = 0
    for b in bits:
        if not b:
            break
        idx += 1
    return idx


def _read_item(text: str, it: dict):
    if it["type"] == "clamp":
        sec = it["section"]
        raws = [cfgutil.ini_read(text, sec, k) for k in it["clamp_keys"]]
        if raws[0] is None:
            return None
        bits = [cfgutil.bool_get(it, r or "") for r in raws]
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": list(it["options_display"]), "value": _clamp_index(bits)}
    raw = cfgutil.ini_read(text, it["section"], it.get("name", it["key"]))
    if raw is None:
        return None
    t = it["type"]
    if t == "bool":
        return {"key": it["key"], "label": it["label"], "type": "bool",
                "value": cfgutil.bool_get(it, raw)}
    if t == "enum":
        disp, val = cfgutil._enum_get(it, raw)
        return {"key": it["key"], "label": it["label"], "type": "enum",
                "options": disp, "value": val}
    if t == "float_scaled":
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 0.0
        row = {"key": it["key"], "label": it["label"], "type": "int",
               "value": int(round(v * it["scale"]))}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    if t in ("int", "float"):
        try:
            v = float(raw) if t == "float" else int(float(raw))
        except (TypeError, ValueError):
            v = float(it.get("min", 0)) if t == "float" else int(it.get("min", 0))
        row = {"key": it["key"], "label": it["label"], "type": t, "value": v}
        for k in ("min", "max", "step"):
            if k in it:
                row[k] = it[k]
        return row
    return None


def _shape(it: dict, token: str):
    t = it["type"]
    if t == "bool":
        return cfgutil.bool_get(it, token)
    if t in ("enum", "clamp"):
        return None  # handled by callers directly
    if t == "float":
        try:
            return float(token)
        except (TypeError, ValueError):
            return token
    try:
        return int(float(token))
    except (TypeError, ValueError):
        return token


def _write_item(text: str, it: dict, value):
    """Stage one edit into `text`; return (new_text, cpp_shaped_value)."""
    if it["type"] == "clamp":
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad clamp index {value!r} for {it['key']}")
        idx = max(0, min(len(it["options_display"]) - 1, idx))
        sec = it["section"]
        for i, k in enumerate(it["clamp_keys"]):
            tok = "true" if idx >= (i + 1) else "false"
            nt = cfgutil.ini_set_or_insert(text, sec, k, tok)
            if nt is None:
                raise RpcError("ENOKEY", f"{k!r} not present in [{sec}]")
            text = nt
        return text, idx
    if it["type"] == "float_scaled":
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad scaled value {value!r} for {it['key']}")
        if "min" in it and "max" in it:
            n = max(it["min"], min(it["max"], n))
        tok = cfgutil.fmt_float(n / it["scale"])
        nt = cfgutil.ini_set_or_insert(text, it["section"], it["key"], tok)
        if nt is None:
            raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
        return nt, n
    name = it.get("name", it["key"])
    cur = cfgutil.ini_read(text, it["section"], name)
    if cur is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    write = cfgutil.compute_write(it, value, cur)
    nt = cfgutil.ini_set_or_insert(text, it["section"], name, write)
    if nt is None:
        raise RpcError("ENOKEY", f"{it['key']!r} not present in [{it['section']}]")
    if it["type"] == "enum":
        _, shaped = cfgutil._enum_get(it, write)
    else:
        shaped = _shape(it, write)
    return nt, shaped


def _item_by_key(ns: str, key: str):
    for g in CATEGORIES[ns][1]:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _get(ns: str) -> dict:
    title, groups = CATEGORIES[ns]
    # Reload fresh unless re-fetching the SAME category with staged (dirty) edits.
    if not (_buf["ns"] == ns and _buf["dirty"]):
        _reload()
    _buf["ns"] = ns
    text = _buf["text"] or ""
    out = []
    for g in groups:
        settings = []
        for it in g["items"]:
            row = _read_item(text, it)
            if row is not None:
                settings.append(row)
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    note = (f"PCSX2 {title} settings. Changes are staged; press Save to apply "
            "(a one-time backup is made before the first change).")
    return {"exists": _buf["text"] is not None, "running": _running(),
            "buffered": True, "dirty": _buf["dirty"], "note": note, "groups": out}


def _set(ns: str, params: dict) -> dict:
    if _running():
        raise RpcError("EBUSY", "PCSX2 is running — close it first (it rewrites its config on exit).")
    if _buf["ns"] != ns or _buf["text"] is None:
        _reload()
        _buf["ns"] = ns
    key = params["key"]
    it = _item_by_key(ns, key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable setting")
    new_text, shaped = _write_item(_buf["text"], it, params["value"])
    _buf["text"] = new_text
    _buf["edits"].append((key, params["value"]))
    _buf["dirty"] = (new_text != _buf["disk"])
    return {"key": key, "value": shaped}


def _save(ns: str) -> dict:
    if _running():
        raise RpcError("EBUSY", "PCSX2 is running — close it first (it rewrites its config on exit).")
    from .. import staterev
    if not _buf["edits"]:
        _buf["dirty"] = False
        return {"saved": False}
    # Re-read the file FRESH and replay only the staged edits onto it, so an external
    # write to OTHER keys since the buffer loaded is preserved (no stale-buffer clobber).
    fresh = cfgutil.read_text(_FILE)
    if fresh is None:
        raise RpcError("ENOENT", f"{_FILE.name} not found — launch a game once to create it.")
    text = fresh
    for key, value in _buf["edits"]:
        it = _item_by_key(_buf["ns"], key)
        if it is not None:
            text, _ = _write_item(text, it, value)
    saved = text != fresh
    if saved:
        cfgutil.ensure_bak(_FILE)
        cfgutil.atomic_write(_FILE, text)
        staterev.bump("config")
    _buf["text"] = text
    _buf["disk"] = text
    _buf["edits"] = []
    _buf["dirty"] = False
    return {"saved": saved}


def _cancel(ns: str) -> dict:
    _reload()
    _buf["ns"] = ns
    return {"cancelled": True}


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
