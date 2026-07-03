r"""citron_* — Citron (Nintendo Switch, a Yuzu fork) GLOBAL settings.

Six instant-save pages that mirror Citron's own Configure dialog tabs:
  citron_general / citron_system / citron_cpu / citron_gfx / citron_gfxadv / citron_audio

Citron shares Eden's qt-config.ini FORMAT, but its enum indices DIFFER from Eden's
(Citron descends from a different Yuzu snapshot). Every enum here is Citron's own,
verified against github.com/citron-neo/emulator src/common/settings_enums.h + the live
~/.config/citron/qt-config.ini (2026-07-03). Notable Citron divergences from Eden:
  backend 0=Vulkan/1=Null (OpenGL removed; shader_backend is a dead/orphaned key -> not
  offered); resolution_setup shifted +1 (0=0.25x .. 3=1x); scaling_filter FSR=7 (5=ScaleFx);
  gpu_accuracy 0=Low/1=Normal; aspect_ratio Stretch=5 (4=32:9); cpu_backend NCE is ARM-only
  (Dynarmic-locked on the x86 Deck) -> not offered.

CRITICAL (the `\default` twin): Citron's config reader (frontend_common/config.cpp
ReadSettingGeneric) IGNORES a `key=value` line unless the twin `key\default=false` is also
present; an absent/`true` marker resets the key to its compiled default and discards the
value. Every emulation-section key in a pristine Citron ini is `\default=true`. So — unlike
Eden's global writer (cfgutil.ini_replace, which never touches `\default` and thus has a
latent silent-discard bug) — our writer (`_yuzu_write`) sets BOTH `key=value` AND
`key\default=false` on every change. We only OFFER keys already present (get_groups is
version-safe), so writes replace-in-place (with the `\default` flip); no key is invented.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/citron/qt-config.ini"   # module global: tests redirect it
_PROC = "citron"
_LABEL = "Citron (Switch)"
_F = _FILE.name


# ── descriptor helpers ────────────────────────────────────────────────────────
def _bool(key, label, section, *, true="true", false="false"):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "bool", "bool_true": true, "bool_false": false}


def _enum(key, label, section, options, *, mode="index", stored=None):
    it = {"key": key, "label": label, "file": _F, "section": section,
          "type": "enum", "write_mode": mode, "options_display": options}
    if stored is not None:
        it["options_stored"] = stored
    return it


def _int(key, label, section, lo, hi, step=1):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "int", "min": lo, "max": hi, "step": step}


def _float(key, label, section, lo, hi, step):
    return {"key": key, "label": label, "file": _F, "section": section,
            "type": "float", "min": lo, "max": hi, "step": step}


# ── enum option lists (Citron source order; index == stored value) ────────────
_RESOLUTION = ["0.25x (180p)", "0.5x (360p)", "0.75x (540p)", "1x (720p, native)",
               "1.5x (1080p)", "2x (1440p)", "3x", "4x", "5x", "6x", "7x", "8x",
               "1.25x", "1.75x"]
_SCALING = ["Nearest Neighbor", "Bilinear", "Bicubic", "Gaussian", "ScaleForce",
            "ScaleFx", "Lanczos", "AMD FSR", "FSR 2.0", "CRT (EasyMode)",
            "CRT (Royale)", "CAS"]
_ANTIALIAS = ["None", "FXAA", "SMAA", "TAA"]
_GPU_ACCURACY = ["Low", "Normal", "High", "Extreme"]
_ANISOTROPY = ["Automatic", "Default", "2x", "4x", "8x", "16x"]
_ASTC_RECOMP = ["Uncompressed", "BC1 (low)", "BC3 (medium)"]
_VRAM_USAGE = ["Conservative", "Aggressive", "High-End", "Insane"]
_NVDEC = ["No Video Output", "CPU Decoding", "GPU Decoding"]
_ASPECT = ["16:9 (Default)", "4:3", "21:9", "16:10", "32:9", "Stretch to Window"]
_FULLSCREEN = ["Borderless Windowed", "Exclusive Fullscreen"]
_MEMORY = ["4 GB (Default)", "6 GB", "8 GB", "10 GB", "12 GB", "14 GB", "16 GB"]
_VSYNC = ["Immediate (Off)", "Mailbox", "FIFO (On)", "FIFO Relaxed"]
_CPU_ACCURACY = ["Auto", "Accurate", "Unsafe", "Paranoid", "Ultra (Low)"]
_BACKEND = ["Vulkan", "Null (no graphics)"]
_ACCEL_ASTC = ["CPU", "GPU", "CPU Asynchronous"]
_FRAME_SKIP = ["Disabled", "Enabled"]
_FRAME_SKIP_MODE = ["Adaptive", "Fixed"]
_FSR2 = ["Quality", "Balanced", "Performance", "Ultra Performance"]
_CRT_MASK = ["None", "Aperture Grille", "Shadow Mask"]
_EDS = ["Disabled", "EDS1", "EDS2", "EDS3"]
_GC_AGGRO = ["Off", "Light"]
_REGION = ["Japan", "USA", "Europe", "Australia", "China", "Korea", "Taiwan"]
_SOUND = ["Mono", "Stereo", "Surround"]
_LANGUAGE = ["Japanese", "American English", "French", "German", "Italian", "Spanish",
             "Chinese", "Korean", "Dutch", "Portuguese", "Russian", "Taiwanese",
             "British English", "Canadian French", "Latin American Spanish",
             "Simplified Chinese", "Traditional Chinese", "Brazilian Portuguese"]
_AUDIO_ENGINE_DISP = ["Auto", "cubeb", "SDL2", "Null (no audio)", "oboe"]
_AUDIO_ENGINE_STORED = ["auto", "cubeb", "sdl2", "null", "oboe"]


# ── the six pages ─────────────────────────────────────────────────────────────
GENERAL_GROUPS = [
    {"title": "General", "note": "", "items": [
        _bool("use_multi_core", "Multicore CPU emulation", "Core"),
        _int("speed_limit", "Speed limit (%)", "Core", 1, 1000, 5),
        _enum("memory_layout_mode", "Emulated console memory", "Core", _MEMORY),
    ]},
    {"title": "Linux", "note": "", "items": [
        _bool("enable_gamemode", "Enable Feral GameMode", "Linux"),
    ]},
]

SYSTEM_GROUPS = [
    {"title": "Console", "note": "", "items": [
        _bool("use_docked_mode", "Docked mode", "System", true="1", false="0"),
        _enum("region_index", "Console region", "System", _REGION),
        _enum("language_index", "Console language", "System", _LANGUAGE),
        _enum("sound_index", "Sound output mode", "System", _SOUND),
    ]},
    {"title": "Clock & RNG", "note": "", "items": [
        _bool("custom_rtc_enabled", "Use custom RTC", "System"),
        _bool("rng_seed_enabled", "Use a fixed RNG seed", "System"),
    ]},
]

CPU_GROUPS = [
    {"title": "CPU", "note": "cpu_backend is fixed to Dynarmic on the Steam Deck (NCE is "
                            "ARM-only), so it is not shown.", "items": [
        _enum("cpu_accuracy", "CPU accuracy", "Cpu", _CPU_ACCURACY),
        _bool("cpu_debug_mode", "CPU debugging", "Cpu"),
    ]},
    {"title": "Unsafe optimizations", "note": "Only take effect when CPU accuracy is "
                                              "'Unsafe'.", "items": [
        _bool("cpuopt_unsafe_unfuse_fma", "Unfuse FMA (improve performance)", "Cpu"),
        _bool("cpuopt_unsafe_reduce_fp_error", "Faster FRSQRTE and FRECPE", "Cpu"),
        _bool("cpuopt_unsafe_ignore_standard_fpcr", "Faster ASIMD (ignore FPCR)", "Cpu"),
        _bool("cpuopt_unsafe_inaccurate_nan", "Inaccurate NaN handling", "Cpu"),
        _bool("cpuopt_unsafe_fastmem_check", "Disable address-space checks", "Cpu"),
        _bool("cpuopt_unsafe_ignore_global_monitor", "Ignore global monitor", "Cpu"),
    ]},
]

GFX_GROUPS = [
    {"title": "Renderer", "note": "", "items": [
        _enum("backend", "Graphics API", "Renderer", _BACKEND),
        _enum("resolution_setup", "Internal resolution", "Renderer", _RESOLUTION),
        _enum("scaling_filter", "Window adapting filter", "Renderer", _SCALING),
        _int("fsr_sharpening_slider", "FSR sharpness (%)", "Renderer", 0, 100, 5),
        _enum("fsr2_quality_mode", "FSR 2.0 quality", "Renderer", _FSR2),
        _enum("anti_aliasing", "Anti-aliasing", "Renderer", _ANTIALIAS),
        _enum("aspect_ratio", "Aspect ratio", "Renderer", _ASPECT),
        _enum("fullscreen_mode", "Fullscreen mode", "Renderer", _FULLSCREEN),
        _bool("use_disk_shader_cache", "Use disk pipeline cache", "Renderer"),
        _enum("nvdec_emulation", "NVDEC video decoding", "Renderer", _NVDEC),
        _enum("accelerate_astc", "ASTC decoding", "Renderer", _ACCEL_ASTC),
    ]},
    {"title": "Frame skipping", "note": "", "items": [
        _enum("frame_skipping", "Frame skipping", "Renderer", _FRAME_SKIP),
        _enum("frame_skipping_mode", "Frame skipping mode", "Renderer", _FRAME_SKIP_MODE),
    ]},
    {"title": "Filter tuning (CRT / Lanczos / CAS)",
     "note": "Only take effect when the matching window adapting filter is selected.",
     "items": [
        _enum("crt_mask_type", "CRT mask type", "Renderer", _CRT_MASK),
        _float("crt_scanline_strength", "CRT scanline strength", "Renderer", 0.0, 2.0, 0.05),
        _float("crt_curvature", "CRT curvature", "Renderer", 0.0, 1.0, 0.05),
        _float("crt_gamma", "CRT gamma", "Renderer", 1.0, 3.0, 0.1),
        _float("crt_bloom", "CRT bloom", "Renderer", 0.0, 1.0, 0.05),
        _float("crt_brightness", "CRT brightness", "Renderer", 0.0, 2.0, 0.05),
        _float("crt_alpha", "CRT alpha", "Renderer", 0.0, 1.0, 0.05),
        _int("lanczos_quality", "Lanczos quality", "Renderer", 2, 4),
        _int("cas_sharpening_slider", "CAS sharpness (%)", "Renderer", 0, 100, 5),
    ]},
]

GFXADV_GROUPS = [
    {"title": "Accuracy & sync", "note": "", "items": [
        _enum("gpu_accuracy", "Accuracy level", "Renderer", _GPU_ACCURACY),
        _enum("use_vsync", "VSync mode", "Renderer", _VSYNC),
        _enum("max_anisotropy", "Anisotropic filtering", "Renderer", _ANISOTROPY),
        _enum("astc_recompression", "ASTC recompression", "Renderer", _ASTC_RECOMP),
    ]},
    {"title": "Async & performance", "note": "", "items": [
        _bool("use_asynchronous_shaders", "Asynchronous shaders", "Renderer"),
        _bool("use_asynchronous_gpu_emulation", "Asynchronous GPU emulation", "Renderer"),
        _bool("use_reactive_flushing", "Reactive flushing", "Renderer"),
        _bool("use_fast_gpu_time", "Fast GPU time (hack)", "Renderer"),
        _bool("use_vulkan_driver_pipeline_cache", "Vulkan driver pipeline cache", "Renderer"),
        _bool("barrier_feedback_loops", "Barrier feedback loops", "Renderer"),
        _bool("async_presentation", "Asynchronous presentation", "Renderer"),
        _bool("force_max_clock", "Force maximum clocks (Vulkan)", "Renderer"),
        _bool("use_video_framerate", "Match video framerate", "Renderer"),
    ]},
    {"title": "Compatibility", "note": "", "items": [
        _enum("extended_dynamic_state", "Extended dynamic state", "Renderer", _EDS),
        _bool("use_conditional_rendering", "Conditional rendering", "Renderer"),
        _bool("wider_reciprocals", "Wider reciprocals (precision)", "Renderer"),
        _bool("enable_buffer_history", "Buffer history (experimental)", "Renderer"),
    ]},
    {"title": "VRAM management", "note": "", "items": [
        _enum("vram_usage_mode", "VRAM usage mode", "Renderer", _VRAM_USAGE),
        _int("vram_limit_mb", "VRAM limit (MB, 0 = auto)", "Renderer", 0, 32768, 256),
        _enum("gc_aggressiveness", "Garbage-collection aggressiveness", "Renderer", _GC_AGGRO),
        _int("texture_eviction_frames", "Texture eviction frames (GC=Light)", "Renderer", 0, 60),
        _int("buffer_eviction_frames", "Buffer eviction frames (GC=Light)", "Renderer", 0, 120),
        _bool("sparse_texture_priority_eviction", "Sparse texture priority eviction", "Renderer"),
    ]},
]

AUDIO_GROUPS = [
    {"title": "Audio", "note": "", "items": [
        _enum("output_engine", "Audio output engine", "Audio", _AUDIO_ENGINE_DISP,
              mode="option", stored=_AUDIO_ENGINE_STORED),
        _int("volume", "Output volume (%)", "Audio", 0, 200, 5),
        _bool("audio_muted", "Mute audio", "Audio"),
        _bool("muteWhenInBackground", "Mute when in background", "Audio"),
    ]},
]

# ns -> (page title, groups). Order = Citron's Configure dialog order.
PAGES = {
    "citron_general": ("General", GENERAL_GROUPS),
    "citron_system":  ("System", SYSTEM_GROUPS),
    "citron_cpu":     ("CPU", CPU_GROUPS),
    "citron_gfx":     ("Graphics", GFX_GROUPS),
    "citron_gfxadv":  ("Adv. Graphics", GFXADV_GROUPS),
    "citron_audio":   ("Audio", AUDIO_GROUPS),
}


# ── the Yuzu-aware write: value + the mandatory `key\default=false` twin ───────
def _yuzu_write(text: str, section: str, key: str, value: str) -> str | None:
    """Replace-fn for cfgutil.apply_set: write `key=value` AND flip `key\\default=false`
    so Citron honours the value (a `\\default=true`/absent twin makes it discard the
    line). ini_set_or_insert replaces in place if present, else appends to the section."""
    t = cfgutil.ini_set_or_insert(text, section, key, value)
    if t is None:
        return None
    t2 = cfgutil.ini_set_or_insert(t, section, key + "\\default", "false")
    return t2 if t2 is not None else t


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return cfgutil.do_get(groups, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        res = cfgutil.do_set(groups, params, _FILE, cfgutil.ini_read, _yuzu_write,
                             proc=_PROC, label=_LABEL)
        from .. import staterev
        staterev.bump("config")
        return res


for _ns, (_title, _groups) in PAGES.items():
    _register(_ns, _groups)
