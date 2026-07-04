r"""eden_* - Eden (Nintendo Switch, a Yuzu fork) GLOBAL settings.

Seven instant-save pages that mirror Eden's own Configure dialog tabs:
  eden_general / eden_system / eden_cpu / eden_gfx / eden_gfxadv / eden_gfxext / eden_audio

Eden shares Citron's qt-config.ini FORMAT (both Yuzu forks) but its enum indices DIFFER
from Citron's (a different Yuzu snapshot), so the descriptors are NOT shared. Every enum
here is Eden's own, verified against Eden source src/common/settings_enums.h
(github.com/eden-emulator/mirror, master, 2026-07-04) AND cross-checked against the live
~/.config/eden/qt-config.ini. Notable Eden values (vs Citron / stock yuzu):
  resolution_setup 13 values from 0.25x so 1x native = index 3; scaling_filter inserts
  Lanczos at 4 (ScaleForce=5, Fsr=6); gpu_accuracy = Low/Medium/High; output_engine is an
  INTEGER index Auto/Cubeb/Sdl3/Null/Oboe (the live file stores `output_engine=0`, not a
  string); anisotropy adds 32x/64x/None; cpu_accuracy adds Debugging(4). backend keeps the
  OpenGL variants Citron dropped. shader_backend has no enum in settings_enums.h -> not
  offered (dead/orphaned, like Citron). See deck-docs/eden-config.md.

CRITICAL (the `\default` twin): Eden's config reader (frontend_common/config.cpp
ReadSettingGeneric) IGNORES a `key=value` line unless the twin `key\default=false` is also
present; an absent/`true` marker resets the key to its compiled default and discards the
value (it calls LoadString("")). So - exactly like Citron, and UNLIKE the old flat eden.get/
set writer (plain cfgutil.ini_replace, which never touched `\default` and thus silently
discarded every non-default write) - our writer (`_yuzu_write`) sets BOTH `key=value` AND
`key\default=false` on every change. We only OFFER keys already present (get_groups is
version-safe), so writes replace-in-place (with the `\default` flip); no key is invented.
"""
from __future__ import annotations

from pathlib import Path

from . import cfgutil
from .rpc import method

_FILE = Path.home() / ".config/eden/qt-config.ini"   # module global: tests redirect it
_PROC = "eden"
_LABEL = "Eden (Switch)"
_F = _FILE.name


# -- descriptor helpers --------------------------------------------------------
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


# -- enum option lists (Eden source order; index == stored value) --------------
_BACKEND = ["OpenGL (GLSL)", "Vulkan", "Null (no graphics)", "OpenGL (GLASM)",
            "OpenGL (SPIR-V)"]
_RESOLUTION = ["0.25x (180p)", "0.5x (360p)", "0.75x (540p)", "1x (720p, native)",
               "1.25x (900p)", "1.5x (1080p)", "2x (1440p)", "3x (2160p)", "4x", "5x",
               "6x", "7x", "8x"]
_SCALING = ["Nearest Neighbor", "Bilinear", "Bicubic", "Gaussian", "Lanczos",
            "ScaleForce", "AMD FSR", "Area", "Zero Tangent", "B-Spline",
            "Mitchell-Netravali", "Spline", "MMPX", "SGSR", "SGSR Edge"]
_ANTIALIAS = ["None", "FXAA", "SMAA"]
_ASPECT = ["16:9 (Default)", "4:3", "21:9", "16:10", "Stretch to Window"]
_VSYNC = ["Immediate (Off)", "Mailbox", "FIFO (On)", "FIFO Relaxed"]
_FULLSCREEN = ["Borderless Windowed", "Exclusive Fullscreen"]
_GPU_ACCURACY = ["Low", "Medium", "High"]
_ANISOTROPY = ["Automatic", "Default", "2x", "4x", "8x", "16x", "32x", "64x", "None"]
_ASTC_RECOMP = ["Uncompressed", "BC1 (low)", "BC3 (medium)"]
_ACCEL_ASTC = ["CPU", "GPU", "CPU Asynchronous"]
_NVDEC = ["Off (No Video)", "CPU Decoding", "GPU Decoding"]
_VRAM_USAGE = ["Conservative", "Aggressive"]
_DMA_ACCURACY = ["Default", "Unsafe", "Safe"]
_FRAME_PACING = ["Auto", "30 FPS", "60 FPS", "90 FPS", "120 FPS"]
_EDS = ["Disabled", "EDS1", "EDS2", "EDS3"]
_SPIRV_OPT = ["Never", "On Load", "Always"]
_CPU_ACCURACY = ["Auto", "Accurate", "Unsafe", "Paranoid", "Debugging"]
_MEMORY = ["4 GB (Default)", "6 GB", "8 GB", "10 GB", "12 GB"]
_REGION = ["Japan", "USA", "Europe", "Australia", "China", "Korea", "Taiwan"]
_SOUND = ["Mono", "Stereo", "Surround"]
_LANGUAGE = ["Japanese", "American English", "French", "German", "Italian", "Spanish",
             "Chinese", "Korean", "Dutch", "Portuguese", "Russian", "Taiwanese",
             "British English", "Canadian French", "Latin American Spanish",
             "Simplified Chinese", "Traditional Chinese", "Brazilian Portuguese",
             "Polish", "Thai"]
_AUDIO_ENGINE = ["Auto", "cubeb", "SDL3", "Null (no audio)", "oboe"]
_UNSW_SIZE = ["Very Small", "Small", "Normal", "Large", "Very Large"]
_UNSW = ["Very Low", "Low", "Normal", "Medium", "High"]


# -- the seven pages -----------------------------------------------------------
GENERAL_GROUPS = [
    {"title": "General", "note": "", "items": [
        _bool("use_multi_core", "Multicore CPU emulation", "Core"),
        _bool("use_speed_limit", "Limit emulation speed", "Core"),
        _int("speed_limit", "Speed limit (%)", "Core", 1, 1000, 5),
        _bool("sync_core_speed", "Sync core speed", "Core"),
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
        _bool("disable_nca_verification", "Skip NCA integrity checks", "System"),
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
        _enum("anti_aliasing", "Anti-aliasing", "Renderer", _ANTIALIAS),
        _enum("aspect_ratio", "Aspect ratio", "Renderer", _ASPECT),
        _enum("fullscreen_mode", "Fullscreen mode", "Renderer", _FULLSCREEN),
        _bool("use_disk_shader_cache", "Use disk pipeline cache", "Renderer"),
        _bool("use_asynchronous_gpu_emulation", "Asynchronous GPU emulation", "Renderer"),
        _enum("nvdec_emulation", "NVDEC video decoding", "Renderer", _NVDEC),
    ]},
]

GFXADV_GROUPS = [
    {"title": "Accuracy & sync", "note": "", "items": [
        _enum("gpu_accuracy", "Accuracy level", "Renderer", _GPU_ACCURACY),
        _enum("use_vsync", "VSync mode", "Renderer", _VSYNC),
        _enum("max_anisotropy", "Anisotropic filtering", "Renderer", _ANISOTROPY),
        _enum("astc_recompression", "ASTC recompression", "Renderer", _ASTC_RECOMP),
        _enum("accelerate_astc", "ASTC decoding", "Renderer", _ACCEL_ASTC),
    ]},
    {"title": "Async & performance", "note": "", "items": [
        _bool("use_reactive_flushing", "Reactive flushing", "Renderer"),
        _bool("use_fast_gpu_time", "Fast GPU time (hack)", "Renderer"),
        _bool("use_vulkan_driver_pipeline_cache", "Vulkan driver pipeline cache", "Renderer"),
        _bool("enable_compute_pipelines", "Enable compute pipelines", "Renderer"),
        _bool("use_video_framerate", "Match video framerate", "Renderer"),
        _bool("barrier_feedback_loops", "Barrier feedback loops", "Renderer"),
        _bool("async_presentation", "Asynchronous presentation", "Renderer"),
        _bool("force_max_clock", "Force maximum clocks (Vulkan)", "Renderer"),
    ]},
    {"title": "Pacing & precision", "note": "", "items": [
        _enum("frame_pacing_mode", "Frame pacing", "Renderer", _FRAME_PACING),
        _enum("dma_accuracy", "DMA accuracy", "Renderer", _DMA_ACCURACY),
        _enum("optimize_spirv_output", "Optimize SPIR-V output", "Renderer", _SPIRV_OPT),
    ]},
    {"title": "VRAM management", "note": "", "items": [
        _enum("vram_usage_mode", "VRAM usage mode", "Renderer", _VRAM_USAGE),
    ]},
]

GFXEXT_GROUPS = [
    {"title": "Vulkan extensions", "note": "Advanced Vulkan feature toggles. Leave at "
                                           "defaults unless a specific game needs them.", "items": [
        _bool("provoking_vertex", "Provoking vertex", "Renderer"),
        _bool("descriptor_indexing", "Descriptor indexing", "Renderer"),
        _bool("sample_shading", "Sample shading", "Renderer"),
        _bool("vertex_input_dynamic_state", "Vertex input dynamic state", "Renderer"),
        _enum("dyna_state", "Extended dynamic state", "Renderer", _EDS),
        _bool("sync_memory_operations", "Sync memory operations", "Renderer"),
        _bool("skip_cpu_inner_invalidation", "Skip CPU inner invalidation", "Renderer"),
        _bool("disable_shader_loop_safety_checks", "Disable shader loop safety checks", "Renderer"),
        _bool("enable_raii", "Enable RAII", "Renderer"),
        _bool("disable_buffer_reorder", "Disable buffer reorder", "Renderer"),
        _bool("enable_buffer_history", "Buffer history (experimental)", "Renderer"),
    ]},
    {"title": "Hacks", "note": "", "items": [
        _bool("use_asynchronous_shaders", "Asynchronous shaders", "Renderer"),
        _bool("rescale_hack", "Rescale hack", "Renderer"),
        _bool("fix_bloom_effects", "Fix bloom effects", "Renderer"),
        _bool("emulate_bgr565", "Emulate BGR565", "Renderer"),
    ]},
    {"title": "GPU unswizzle", "note": "", "items": [
        _bool("gpu_unswizzle_enabled", "Enable GPU unswizzle", "Renderer"),
        _enum("gpu_unswizzle_texture_size", "Unswizzle texture size", "Renderer", _UNSW_SIZE),
        _enum("gpu_unswizzle_stream_size", "Unswizzle stream size", "Renderer", _UNSW),
        _enum("gpu_unswizzle_chunk_size", "Unswizzle chunk size", "Renderer", _UNSW),
    ]},
]

AUDIO_GROUPS = [
    {"title": "Audio", "note": "", "items": [
        _enum("output_engine", "Audio output engine", "Audio", _AUDIO_ENGINE),
        _int("volume", "Output volume (%)", "Audio", 0, 200, 5),
        _bool("audio_muted", "Mute audio", "Audio"),
        _bool("muteWhenInBackground", "Mute when in background", "Audio"),
    ]},
]

# ns -> (page title, groups). Order = Eden's Configure dialog order.
PAGES = {
    "eden_general": ("General", GENERAL_GROUPS),
    "eden_system":  ("System", SYSTEM_GROUPS),
    "eden_cpu":     ("CPU", CPU_GROUPS),
    "eden_gfx":     ("Graphics", GFX_GROUPS),
    "eden_gfxadv":  ("Adv. Graphics", GFXADV_GROUPS),
    "eden_gfxext":  ("GPU extensions", GFXEXT_GROUPS),
    "eden_audio":   ("Audio", AUDIO_GROUPS),
}


# -- the Yuzu-aware write: value + the mandatory `key\default=false` twin -------
def _yuzu_write(text: str, section: str, key: str, value: str) -> str | None:
    """Replace-fn for cfgutil.apply_set: write `key=value` AND flip `key\\default=false`
    so Eden honours the value (a `\\default=true`/absent twin makes it discard the line).
    ini_set_or_insert replaces in place if present, else appends to the section."""
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
