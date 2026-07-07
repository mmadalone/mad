"""retroarch_settings — full categorized RetroArch GLOBAL settings tree for MAD,
split into 8 category namespaces (Video / Audio / Latency / Saves / On-Screen /
Menu / Input / Netplay). Each category is rendered by the shipped
GuiMadPageEmuSettings via the standard settings-descriptor renderer contract
(bool / enum / int / float), so no C++ change is needed to add them.

LIVE-SAVE (not buffered): retroarch.cfg holds RetroArch's global defaults and RA
REWRITES THE WHOLE FILE on exit, so every write is refused while RA is running
(proc_guard.retroarch_running) and otherwise applied immediately, byte-preserving
a single key via retroarch_cfg.set_global_option (one-time .mad-bak, atomic
replace). No Save/Cancel: the payload omits `buffered`, so the C++ page defaults
to false and each set flashes "Saved".

Setting-descriptor shape (the GuiMadPageEmuSettings renderer sends every value back
as a STRING):
  bool   {key,label,type:"bool"}                      C++ sends "1"/"0"
  enum   {key,label,type:"enum",options:[...]}        C++ sends the option INDEX
  int    {key,label,type:"int",min,max,step}          C++ sends the integer
  float  {key,label,type:"float",min,max,step}        C++ sends "%.1f"
RetroArch stores bools as "true"/"false" and ints/floats as their token (floats as
"%.6f"). Two enum flavours (mirroring cfgutil's write_mode):
  _eopt  the stored value IS the option string (e.g. video_driver="vulkan"); the
         live on-disk value is prepended if it isn't in the curated list so a
         custom value is never lost.
  _eidx  the stored value IS the integer option INDEX (e.g. aspect_ratio_index,
         video_rotation); an out-of-range on-disk index extends the display list.

A setting is offered ONLY when its key is present in the live retroarch.cfg
(version-safe: a renamed/removed key just disappears rather than writing garbage).

REALITY-CHECKED against
~/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg (2026-07-02):
every declared key was verified present with a matching type/value. Deliberately
excluded: input BIND keys (input_playerN_*, *_btn/_axis/_mbtn and the keyboard
hotkey binds) belong to the input-mapping page, not settings; RetroAchievements
(user cut it); free-text keys with no renderer control (audio_device,
audio_dsp_plugin, netplay_nickname / ip_address / passwords) are set from
RetroArch's own menu; long version-specific theme enums (menu color themes,
timedate style) are omitted rather than mislabelled.
"""
from __future__ import annotations

from .. import proc_guard
from .. import retroarch_cfg
from ..ra_options import ra_options_for
from .rpc import RpcError, method

_TRUE = {"1", "true", "yes", "on"}


# ── descriptor helpers ────────────────────────────────────────────────────────
def _b(key, label):
    return {"key": key, "label": label, "type": "bool"}


def _eopt(key, label, options):
    """Enum whose stored value is the option STRING (e.g. video_driver). Emitted as
    type "resolution" so the C++ sends the STRING back on set — immune to option-list
    length shifts across sequential edits (Phase 1 review issue 1)."""
    return {"key": key, "label": label, "type": "resolution", "options": list(options)}


def _eidx(key, label, options):
    """Enum whose stored value is the integer option INDEX (e.g. aspect_ratio_index)."""
    return {"key": key, "label": label, "type": "enum", "stored": "index",
            "options": list(options)}


def _i(key, label, lo, hi, step=1):
    return {"key": key, "label": label, "type": "int", "min": lo, "max": hi, "step": step}


def _f(key, label, lo, hi, step=0.1):
    return {"key": key, "label": label, "type": "float", "min": lo, "max": hi, "step": step}


# ── reusable option tables ────────────────────────────────────────────────────
# RetroArch aspectratio_lut order (gfx/video_driver.c) — stable across recent RA.
_ASPECT = ["4:3", "16:9", "16:10", "16:15", "21:9", "1:1", "2:1", "3:2", "3:4",
           "4:1", "9:16", "5:4", "6:5", "7:9", "8:3", "8:7", "19:12", "19:14",
           "30:17", "32:9", "Config", "Square pixel", "Core provided", "Custom",
           "Full"]
_ROTATION = ["Normal", "90 deg", "180 deg", "270 deg"]
_THUMBS = ["OFF", "Screenshot", "Title Screen", "Boxart", "Logo"]
# input_combo_type (RetroArch input_defines.h), shared by menu-toggle + quit combo.
_COMBO = ["None", "Down + Y + L1 + R1", "L3 + R3", "L1 + R1 + Start + Select",
          "Start + Select", "L3 + R1", "L1 + R1", "Hold Start (2s)",
          "Hold Select (2s)", "Down + Select", "L2 + R2"]
_POLL = ["Early", "Normal", "Late"]
_GAME_FOCUS = ["Off", "On", "Detect (per core)"]


# ── VIDEO (ns raset_video) ────────────────────────────────────────────────────
VIDEO_GROUPS = [
    {"title": "Driver & Display", "note": "", "items": [
        _eopt("video_driver", "Video driver", ["vulkan", "glcore", "gl"]),
        _b("video_vsync", "V-Sync"),
        _b("video_adaptive_vsync", "Adaptive V-Sync"),
        _b("video_fullscreen", "Fullscreen"),
        _b("video_windowed_fullscreen", "Windowed fullscreen"),
        _i("video_fullscreen_x", "Fullscreen width (0 = desktop)", 0, 7680, 8),
        _i("video_fullscreen_y", "Fullscreen height (0 = desktop)", 0, 4320, 8),
        _i("video_monitor_index", "Monitor index (0 = auto)", 0, 8, 1),
        _f("video_refresh_rate", "Refresh rate (Hz)", 30.0, 240.0, 1.0),
    ]},
    {"title": "Scaling & Aspect", "note": "", "items": [
        _i("video_scale", "Windowed scale", 1, 10, 1),
        _b("video_scale_integer", "Integer scale (sharp, pillarboxed)"),
        _eidx("aspect_ratio_index", "Aspect ratio", _ASPECT),
        _b("video_aspect_ratio_auto", "Auto aspect ratio"),
        _b("video_force_aspect", "Force aspect ratio"),
        _b("video_allow_rotate", "Allow rotation"),
        _eidx("video_rotation", "Rotation", _ROTATION),
        _b("video_crop_overscan", "Crop overscan"),
    ]},
    {"title": "Filtering & Sync", "note": "", "items": [
        _b("video_smooth", "Bilinear smoothing"),
        _b("video_threaded", "Threaded video"),
        _b("video_shader_enable", "Shaders"),
        _b("video_hard_sync", "Hard GPU sync (lower latency)"),
        _i("video_black_frame_insertion", "Black frame insertion", 0, 5, 1),
        _i("video_swap_interval", "Swap interval (V-Sync)", 0, 4, 1),
        _i("video_max_swapchain_images", "Max swapchain images", 1, 4, 1),
        _b("video_gpu_screenshot", "Screenshot with shaders (GPU)"),
        _b("video_hdr_enable", "HDR output"),
    ]},
]

# ── AUDIO (ns raset_audio) ────────────────────────────────────────────────────
AUDIO_GROUPS = [
    {"title": "Output",
     "note": "Audio device and DSP plugin are text fields, set in RetroArch's own menu.",
     "items": [
        _b("audio_enable", "Audio on"),
        _eopt("audio_driver", "Audio driver", ["pipewire", "pulse", "alsathread", "alsa"]),
        _b("audio_sync", "Sync audio"),
        _i("audio_latency", "Audio latency (ms)", 8, 512, 8),
        _eopt("audio_resampler", "Resampler", ["sinc", "CC", "nearest"]),
        _i("audio_resampler_quality", "Resampler quality (0 = fastest, 5 = best)", 0, 5, 1),
        _b("audio_rate_control", "Dynamic rate control"),
    ]},
    {"title": "Volume", "note": "", "items": [
        _f("audio_volume", "Volume (dB)", -80.0, 12.0, 1.0),
        _f("audio_mixer_volume", "Mixer volume (dB)", -80.0, 12.0, 1.0),
        _b("audio_mute_enable", "Mute"),
        _b("audio_mixer_mute_enable", "Mute mixer"),
    ]},
    {"title": "Muting", "note": "", "items": [
        _b("audio_fastforward_mute", "Mute on fast-forward"),
        _b("audio_fastforward_speedup", "Speed up audio on fast-forward"),
        _b("audio_rewind_mute", "Mute on rewind"),
    ]},
]

# ── LATENCY (ns raset_latency) ────────────────────────────────────────────────
LATENCY_GROUPS = [
    {"title": "Run-Ahead",
     "note": "Run-ahead cuts input latency but costs CPU; preemptive frames is the lighter alternative.",
     "items": [
        _b("run_ahead_enabled", "Run-ahead"),
        _i("run_ahead_frames", "Run-ahead frames", 1, 6, 1),
        _b("run_ahead_secondary_instance", "Second instance (safer, more CPU)"),
        _b("run_ahead_hide_warnings", "Hide run-ahead warnings"),
        _b("preemptive_frames_enable", "Preemptive frames"),
    ]},
    {"title": "Frame Timing", "note": "", "items": [
        _i("video_frame_delay", "Frame delay (ms)", 0, 15, 1),
        _b("video_frame_delay_auto", "Automatic frame delay"),
        _i("video_hard_sync_frames", "Hard GPU sync frames", 0, 3, 1),
        _i("video_max_frame_latency", "Max frame latency", 0, 4, 1),
        _b("vrr_runloop_enable", "Sync to exact content framerate (VRR)"),
    ]},
    {"title": "Rewind & Fast-Forward", "note": "Fast-forward speed 0 = unlimited.", "items": [
        _b("rewind_enable", "Rewind"),
        _i("rewind_granularity", "Rewind granularity (frames)", 1, 120, 1),
        _f("fastforward_ratio", "Fast-forward speed", 0.0, 10.0, 1.0),
        _b("fastforward_frameskip", "Fast-forward frameskip"),
    ]},
]

# ── SAVES (ns raset_saves) ────────────────────────────────────────────────────
SAVES_GROUPS = [
    {"title": "Save States", "note": "", "items": [
        _b("savestate_auto_save", "Auto-save state on exit"),
        _b("savestate_auto_load", "Auto-load state on start"),
        _b("savestate_auto_index", "Increment save-state index"),
        _b("savestate_thumbnail_enable", "Save-state thumbnails"),
        _b("savestate_file_compression", "Compress save states"),
        _i("savestate_max_keep", "Keep N save states (0 = unlimited)", 0, 999, 1),
    ]},
    {"title": "Save Files (SRAM)", "note": "", "items": [
        _i("autosave_interval", "SRAM autosave interval (s, 0 = off)", 0, 600, 5),
        _b("block_sram_overwrite", "Block SRAM overwrite"),
        _b("save_file_compression", "Compress save files"),
    ]},
    {"title": "Overrides & Config", "note": "", "items": [
        _b("config_save_on_exit", "Save config on exit"),
        _b("auto_overrides_enable", "Load override files automatically"),
        _b("auto_remaps_enable", "Load remap files automatically"),
        _b("auto_shaders_enable", "Load shader presets automatically"),
        _b("remap_save_on_exit", "Save remap files on exit"),
        _b("game_specific_options", "Load per-game core options"),
    ]},
    {"title": "Content Directory", "note": "", "items": [
        _b("savefiles_in_content_dir", "Save files in content dir"),
        _b("savestates_in_content_dir", "Save states in content dir"),
        _b("systemfiles_in_content_dir", "System files in content dir"),
        _b("screenshots_in_content_dir", "Screenshots in content dir"),
    ]},
    {"title": "Sorting", "note": "", "items": [
        _b("sort_savefiles_enable", "Sort save files into folders"),
        _b("sort_savestates_enable", "Sort save states into folders"),
        _b("sort_savefiles_by_content_enable", "Sort save files by content dir"),
        _b("sort_savestates_by_content_enable", "Sort save states by content dir"),
        _b("sort_screenshots_by_content_enable", "Sort screenshots by content dir"),
    ]},
]

# ── ON-SCREEN DISPLAY (ns raset_osd) ──────────────────────────────────────────
OSD_GROUPS = [
    {"title": "Appearance", "note": "", "items": [
        _b("video_font_enable", "On-screen notifications"),
        _f("video_font_size", "Notification font size", 8.0, 64.0, 1.0),
        _b("menu_enable_widgets", "Graphics widgets (fancy OSD)"),
        _b("video_msg_bgcolor_enable", "Notification background box"),
    ]},
    {"title": "Performance Stats", "note": "", "items": [
        _b("fps_show", "Show framerate"),
        _i("fps_update_interval", "Framerate update interval (frames)", 1, 512, 1),
        _b("framecount_show", "Show frame count"),
        _b("memory_show", "Show memory usage"),
        _b("statistics_show", "Show statistics"),
    ]},
    {"title": "Notifications shown", "note": "", "items": [
        _b("notification_show_autoconfig", "Controller connected"),
        _b("notification_show_autoconfig_fails", "Controller config failed"),
        _b("notification_show_cheats_applied", "Cheats applied"),
        _b("notification_show_config_override_load", "Config override loaded"),
        _b("notification_show_disk_control", "Disk control"),
        _b("notification_show_fast_forward", "Fast-forward active"),
        _b("notification_show_netplay_extra", "Extra netplay messages"),
        _b("notification_show_patch_applied", "Patch applied"),
        _b("notification_show_refresh_rate", "Refresh rate"),
        _b("notification_show_remap_load", "Remap loaded"),
        _b("notification_show_save_state", "Save state"),
        _b("notification_show_screenshot", "Screenshot taken"),
        _b("notification_show_set_initial_disk", "Initial disk set"),
        _b("notification_show_when_menu_is_alive", "Also while menu is open"),
    ]},
]

# ── MENU (ns raset_menu) ──────────────────────────────────────────────────────
MENU_GROUPS = [
    {"title": "General", "note": "", "items": [
        _eopt("menu_driver", "Menu driver", ["ozone", "xmb", "rgui", "glui"]),
        _f("menu_scale_factor", "Menu scale", 0.2, 5.0, 0.1),
        _b("menu_show_advanced_settings", "Show advanced settings"),
        _b("menu_show_sublabels", "Show setting descriptions"),
        _b("menu_use_preferred_system_color_theme", "Use system color theme"),
        _b("menu_horizontal_animation", "Horizontal animation"),
        _b("menu_scroll_fast", "Fast scrolling"),
    ]},
    {"title": "Thumbnails & Resume", "note": "", "items": [
        _eidx("menu_thumbnails", "Primary thumbnail", _THUMBS),
        _b("menu_timedate_enable", "Show date and time"),
        _b("menu_savestate_resume", "Resume after loading a save state"),
        _b("menu_insert_disk_resume", "Resume after changing disk"),
    ]},
    {"title": "RGUI", "note": "", "items": [
        _b("menu_rgui_full_width_layout", "Full-width layout"),
        _b("menu_rgui_shadows", "Shadow effects"),
        _b("menu_rgui_transparency", "Transparency"),
    ]},
    {"title": "Ozone", "note": "", "items": [
        _b("ozone_collapse_sidebar", "Collapse the sidebar"),
        _b("ozone_truncate_playlist_name", "Truncate playlist names"),
        _b("ozone_scroll_content_metadata", "Scroll long metadata"),
    ]},
    {"title": "Main Menu Items", "note": "", "items": [
        _b("menu_show_load_core", "Load Core"),
        _b("menu_show_load_content", "Load Content"),
        _b("menu_show_load_disc", "Load Disc"),
        _b("menu_show_dump_disc", "Dump Disc"),
        _b("menu_show_online_updater", "Online Updater"),
        _b("menu_show_core_updater", "Core Downloader"),
        _b("menu_show_information", "Information"),
        _b("menu_show_configurations", "Configuration File"),
        _b("menu_show_help", "Help"),
        _b("menu_show_restart_retroarch", "Restart RetroArch"),
        _b("menu_show_quit_retroarch", "Quit RetroArch"),
        _b("menu_show_reboot", "Reboot"),
        _b("menu_show_shutdown", "Shutdown"),
        _b("menu_show_rewind", "Rewind settings"),
        _b("menu_show_latency", "Latency settings"),
        _b("menu_show_overlays", "Overlay settings"),
    ]},
    {"title": "Content Tabs", "note": "", "items": [
        _b("content_show_favorites", "Favorites tab"),
        _b("content_show_history", "History tab"),
        _b("content_show_images", "Images tab"),
        _b("content_show_music", "Music tab"),
        _b("content_show_video", "Video tab"),
        _b("content_show_netplay", "Netplay tab"),
        _b("content_show_explore", "Explore tab"),
        _b("content_show_playlists", "Playlists tab"),
        _b("content_show_playlist_tabs", "Per-playlist tabs"),
        _b("content_show_settings", "Settings tab"),
        _b("content_show_favorites_first", "Favorites before history"),
    ]},
    {"title": "Quick Menu Items", "note": "", "items": [
        _b("quick_menu_show_options", "Core Options"),
        _b("quick_menu_show_controls", "Controls"),
        _b("quick_menu_show_cheats", "Cheats"),
        _b("quick_menu_show_shaders", "Shaders"),
        _b("quick_menu_show_information", "Information"),
        _b("quick_menu_show_download_thumbnails", "Download Thumbnails"),
        _b("quick_menu_show_add_to_favorites", "Add to Favorites"),
        _b("quick_menu_show_restart_content", "Restart Content"),
        _b("quick_menu_show_close_content", "Close Content"),
        _b("quick_menu_show_undo_save_load_state", "Undo Save/Load State"),
        _b("quick_menu_show_reset_core_association", "Reset Core Association"),
        _b("quick_menu_show_set_core_association", "Set Core Association"),
        _b("quick_menu_show_save_core_overrides", "Save Core Overrides"),
        _b("quick_menu_show_save_game_overrides", "Save Game Overrides"),
    ]},
]

# ── INPUT (ns raset_input) ────────────────────────────────────────────────────
# SETTINGS only — never the per-button binds (those are the input-mapping page).
INPUT_GROUPS = [
    {"title": "Menu Control", "note": "", "items": [
        _b("menu_swap_ok_cancel_buttons", "Swap menu OK / Cancel buttons"),
        _b("all_users_control_menu", "All controllers control the menu"),
        _eidx("input_menu_toggle_gamepad_combo", "Menu toggle combo", _COMBO),
        _eidx("input_quit_gamepad_combo", "Quit combo", _COMBO),
    ]},
    {"title": "Analog & Turbo", "note": "", "items": [
        _f("input_analog_deadzone", "Analog deadzone", 0.0, 1.0, 0.1),
        _f("input_analog_sensitivity", "Analog sensitivity", 0.0, 2.0, 0.1),
        _f("input_axis_threshold", "Analog-to-digital threshold", 0.0, 1.0, 0.1),
        _b("input_turbo_enable", "Turbo enable"),
        _i("input_turbo_period", "Turbo period (frames)", 1, 120, 1),
        _i("input_turbo_duty_cycle", "Turbo duty cycle (frames)", 0, 100, 1),
        _b("input_turbo_allow_dpad", "Allow turbo on D-pad"),
    ]},
    {"title": "Behavior", "note": "", "items": [
        _b("input_autodetect_enable", "Auto-configure controllers"),
        _b("input_remap_binds_enable", "Apply per-core remaps"),
        _b("input_auto_mouse_grab", "Auto-grab mouse"),
        _eidx("input_poll_type_behavior", "Polling behavior", _POLL),
        _eidx("input_auto_game_focus", "Game Focus mode", _GAME_FOCUS),
        _i("input_max_users", "Max users (ports)", 1, 16, 1),
        _i("input_hotkey_block_delay", "Hotkey enable delay (frames)", 0, 600, 1),
        _i("input_bind_timeout", "Bind timeout (s)", 1, 10, 1),
        _i("input_bind_hold", "Bind hold (s)", 0, 10, 1),
        _b("input_descriptor_label_show", "Show core input labels"),
        _b("input_descriptor_hide_unbound", "Hide unbound core inputs"),
    ]},
]

# ── category registry: ns -> (page title, GROUPS) ─────────────────────────────
# Netplay (raset_netplay) was retired from the Settings tree (RetroArch hub
# Controllers batch) — Netplay was never a controllers/pads concern and had no
# on-device use here.
CATEGORIES = {
    "raset_video": ("Video", VIDEO_GROUPS),
    "raset_audio": ("Audio", AUDIO_GROUPS),
    "raset_latency": ("Latency", LATENCY_GROUPS),
    "raset_saves": ("Saves", SAVES_GROUPS),
    "raset_osd": ("On-Screen Display", OSD_GROUPS),
    "raset_menu": ("Menu", MENU_GROUPS),
    "raset_input": ("Input", INPUT_GROUPS),
}


# ── read/shape helpers ────────────────────────────────────────────────────────
def _item_by_key(ns: str, key: str) -> dict | None:
    for g in CATEGORIES[ns][1]:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _opt_options(it: dict) -> list[str]:
    """Option-enum list as the stepper sees it: the curated list with the CURRENT
    on-disk value prepended if it isn't one of ours (get + set build it identically
    so the index the C++ sends maps back to the right string)."""
    options = list(it["options"])
    cur = retroarch_cfg.get_global_option(it["key"])
    if cur is not None and cur not in options:
        options.insert(0, cur)
    return options


def _read_row(it: dict, raw: str) -> dict:
    key, label, t = it["key"], it["label"], it["type"]
    if t == "bool":
        return {"key": key, "label": label, "type": "bool",
                "value": raw.strip().lower() in _TRUE}
    if t == "resolution":                       # _eopt: value is the STRING (C++ matches it)
        options = list(it["options"])
        if raw not in options:                  # keep a custom on-disk value selectable
            options.insert(0, raw)
        return {"key": key, "label": label, "type": "resolution",
                "options": options, "value": raw}
    if t == "enum":
        if it.get("stored") == "index":
            try:
                idx = int(float(raw))
            except (TypeError, ValueError):
                idx = 0
            options = list(it["options"])
            if idx >= len(options):            # represent an out-of-range on-disk code
                options += [str(i) for i in range(len(options), idx + 1)]
            return {"key": key, "label": label, "type": "enum",
                    "options": options, "value": max(0, idx)}
        options = list(it["options"])
        if raw not in options:                 # keep a custom value nothing else knows
            options.insert(0, raw)
        return {"key": key, "label": label, "type": "enum",
                "options": options, "value": options.index(raw)}
    if t == "int":
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            v = it["min"]
        return {"key": key, "label": label, "type": "int",
                "value": v, "min": it["min"], "max": it["max"], "step": it["step"]}
    # float
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(it["min"])
    return {"key": key, "label": label, "type": "float",
            "value": v, "min": it["min"], "max": it["max"], "step": it["step"]}


def _compute_write(it: dict, value) -> str:
    """The exact token to store, from the C++-sent (stringy) value."""
    t = it["type"]
    if t == "bool":
        return "true" if str(value).strip().lower() in _TRUE else "false"
    if t == "resolution":                       # _eopt: the C++ sends the STRING back
        s = str(value)                          # (immune to option-list index shifts)
        if s not in _opt_options(it):
            raise RpcError("EINVAL", f"unknown option {value!r} for {it['key']}")
        return s
    if t == "enum":
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad enum index {value!r} for {it['key']}")
        if it.get("stored") == "index":
            if idx < 0:
                raise RpcError("EINVAL", f"enum index {idx} out of range for {it['key']}")
            return str(idx)
        options = _opt_options(it)
        if not (0 <= idx < len(options)):
            raise RpcError("EINVAL", f"enum index {idx} out of range for {it['key']}")
        return options[idx]
    if t == "int":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad integer {value!r} for {it['key']}")
        return str(max(it["min"], min(it["max"], n)))
    # float — RetroArch's on-disk format is %.6f, so unchanged values round-trip byte-for-byte
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"bad number {value!r} for {it['key']}")
    v = max(float(it["min"]), min(float(it["max"]), v))
    return f"{v:.6f}"


def _shape_back(it: dict, key: str):
    """Re-read the written value and shape it the way the C++ page expects."""
    back = retroarch_cfg.get_global_option(key) or ""
    t = it["type"]
    if t == "bool":
        return back.strip().lower() in _TRUE
    if t == "resolution":
        return back
    if t == "enum":
        if it.get("stored") == "index":
            try:
                return max(0, int(float(back)))
            except (TypeError, ValueError):
                return 0
        options = list(it["options"])
        if back not in options:
            options.insert(0, back)
        return options.index(back)
    if t == "int":
        try:
            return int(float(back))
        except (TypeError, ValueError):
            return it["min"]
    try:
        return float(back)
    except (TypeError, ValueError):
        return float(it["min"])


# ── get / set (LIVE-SAVE) ─────────────────────────────────────────────────────
def _get(ns: str) -> dict:
    title, groups = CATEGORIES[ns]
    keys = [it["key"] for g in groups for it in g["items"]]
    vals = retroarch_cfg.get_global_options(keys)   # one file read
    out = []
    for g in groups:
        settings = []
        for it in g["items"]:
            raw = vals.get(it["key"])
            if raw is None:                          # key absent -> not offered (version-safe)
                continue
            settings.append(_read_row(it, raw))
        if settings:
            out.append({"title": g["title"], "note": g.get("note", ""), "settings": settings})
    note = (f"RetroArch {title} settings. Changes save instantly to retroarch.cfg "
            "(a one-time backup is made before the first change). Close RetroArch "
            "first, it rewrites this file on exit.")
    return {"exists": retroarch_cfg.RA_GLOBAL_CFG.exists(),
            "running": proc_guard.retroarch_running(),
            "note": note, "groups": out}


def _set(ns: str, params: dict) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    it = _item_by_key(ns, key)
    if it is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable RetroArch setting")
    retroarch_cfg.set_global_option(key, _compute_write(it, params["value"]))
    return {"key": key, "value": _shape_back(it, key)}


# ── RPC registration: <ns>.get / <ns>.set for each category ───────────────────
def _register(ns: str) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, ns=ns):
        return _get(ns)

    @method(f"{ns}.set", slow=True)
    def _s(params, ns=ns):
        return _set(ns, params)


for _ns in CATEGORIES:
    _register(_ns)


# ── per-system RetroArch options (RA-hub "Per-system settings" section) ────────
# The curated per-system toggles (lib/ra_options.py) that used to live on the
# retired Systems page render here, one settings namespace per RetroArch system.
# Reuses the generic GuiMadPageEmuSettings via kind:"settings" (no C++), exactly
# like the global category namespaces above; writes go to config/<Core>/<system>.cfg
# via retroarch_cfg.set_system_option (all cores, one-time backup, atomic).
def _rasys_get(system: str) -> dict:
    settings = [
        {"key": o["id"], "label": o["label"], "type": "bool",
         "value": retroarch_cfg.get_system_option(system, o["cfg_key"]) == o["on"]}
        for o in ra_options_for(system)
    ]
    return {"exists": bool(retroarch_cfg.core_dirs_for_system(system)),
            "running": proc_guard.retroarch_running(),
            "note": ("Options applied to every " + system + " game (written to this "
                     "system's RetroArch config). Close RetroArch first; it rewrites "
                     "its config on exit."),
            "groups": ([{"title": "Per-system options", "note": "", "settings": settings}]
                       if settings else [])}


def _rasys_set(system: str, params: dict) -> dict:
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running, close it first "
                                "(it rewrites its config on exit).")
    oid = params["key"]
    value = str(params.get("value")).strip().lower() in _TRUE
    opt = next((o for o in ra_options_for(system) if o["id"] == oid), None)
    if opt is None:
        raise RpcError("EINVAL", f"{oid!r} is not a per-system option for {system!r}")
    retroarch_cfg.set_system_option(system, opt["cfg_key"], opt["on"] if value else None)
    return {"key": oid,
            "value": retroarch_cfg.get_system_option(system, opt["cfg_key"]) == opt["on"]}


def _register_rasys(system: str) -> None:
    ns = f"rasys_{system}"

    @method(f"{ns}.get", slow=True)
    def _g(params, system=system):
        return _rasys_get(system)

    @method(f"{ns}.set", slow=True)
    def _s(params, system=system):
        return _rasys_set(system, params)


def _ra_option_systems() -> list[str]:
    """Every ES-DE system, enumerated once at import to register a per-system
    rasys_<system> settings namespace for each. Registration is a cheap superset:
    whether a system actually shows the RETROARCH OPTIONS button is gated live by
    priority.get's ra_options_available (core_dirs_for_system) plus its membership
    in the two-grid Systems list (present_ra_systems). Registering every system
    (not just the non-standalone set) guarantees the button never opens an
    unregistered namespace even if a system's launch backend changes."""
    try:
        from .. import es_systems
        return sorted(es_systems.load_systems())
    except Exception:
        return []


_RA_OPTION_SYSTEMS = _ra_option_systems()
for _sys in _RA_OPTION_SYSTEMS:
    _register_rasys(_sys)


# ── RetroArch hub tile (Phase 2 + Phase 3) ────────────────────────────────────
# Mirrors the Standalones tile/section contract (standalones_cmds) so the C++
# GuiMadPageStandalones, once parametrized with a listMethod, renders it exactly
# like the Standalones hub. Wires the sections whose C++ pages ALREADY exist
# (Settings via the generic GuiMadPageEmuSettings per raset_* namespace, Input
# via GuiMadPageRetroArchInput, Bezels via GuiMadPageBezelProject, Controllers
# via racontrollers.get) plus Per-game (Phase 3: ragame.systems/.games backs a
# new thin GuiMadPageRetroArchSystems -> GuiMadPageRetroArchGame pair; the
# per-game Settings/Input editors reuse GuiMadPageEmuSettings again, under the
# ns="ragameset"/"ragamein" namespaces). `retroarch_input`/`bezels`/
# `racontrollers`/`ra_systems` are section-kind strings the C++ dispatcher
# gains across these slices; they are plain data here.
def _ra_hub_tiles() -> list[dict]:
    if not retroarch_cfg.RA_GLOBAL_CFG.exists():
        # RA absent: the sidebar row is already probe-gated off (sidebar_cmds), so this
        # empty list is belt-and-suspenders -> if the row is somehow reached, the grid
        # shows its empty-state copy ("RetroArch isn't set up...") instead of stale tiles.
        return []
    from .systems_cmds import resolve_art
    icon = resolve_art(["icons/retroarch.png"])
    settings_subs = [
        {"label": title, "sublabel": "", "kind": "settings", "arg": ns,
         "title": f"RetroArch — {title}"}
        for ns, (title, _groups) in CATEGORIES.items()
    ]
    sections = [
        {"label": "Settings", "sublabel": "video, audio, latency, saves, menu…",
         "kind": "group", "arg": "", "title": "RetroArch — Settings",
         "sections": settings_subs},
        {"label": "Input mapping", "sublabel": "buttons, sticks, hotkeys",
         "kind": "retroarch_input", "arg": "", "title": "RetroArch — Input mapping"},
        # The former "Controllers" section, slimmed to the GLOBAL default order editor
        # (its per-system/collection rules moved to "Per-system settings" below).
        {"label": "Global default", "sublabel": "base controller order for all systems",
         "kind": "racontrollers", "arg": "", "title": "Global default order"},
        # Per-system + collection controller rules AND per-system RA options, as a
        # two-grid page (Systems on top, Collections below); a system tile opens the
        # per-system editor. Opens GuiMadPagePriority (kind "priority_scopes").
        {"label": "Per-system settings",
         "sublabel": "per-system + collection rules and options",
         "kind": "priority_scopes", "arg": "", "title": "Per-system settings"},
        {"label": "Per-game", "sublabel": "settings, input & controllers per game",
         "kind": "ra_systems", "arg": "", "title": "RetroArch — Per-game"},
        {"label": "Bezels", "sublabel": "overlays and borders",
         "kind": "bezels", "arg": "", "title": "RetroArch — Bezels"},
    ]
    tile = {
        "key": "retroarch", "label": "RetroArch", "sublabel": "",
        "art": [icon] if icon else [],
        "sections": sections,
    }
    return [tile]


@method("retroarch.list", slow=True)
def _retroarch_list(params):
    return {"tiles": _ra_hub_tiles()}
