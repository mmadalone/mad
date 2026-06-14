"""retroarch.* methods — global RetroArch defaults editor (retroarch.cfg).

The MAD "RetroArch" page lets the user configure RetroArch's GLOBAL settings from
inside ES-DE, so they never have to drop to desktop mode and open RA's own menu.
We edit the single global retroarch.cfg; per-system overrides live on the Systems
page. RetroArch reads this file at startup and REWRITES THE WHOLE FILE on exit, so
a set is refused while RA is running.

Reuses the byte-preserving in-place writer in lib.retroarch_cfg
(get_global_option / set_global_option — keeps every other line, makes a one-time
.mad-bak, atomic os.replace). Only the curated keys in GROUPS are writable.

GROUPS shape + the get/set contract mirror model2_cmds so the SAME C++ GROUPS
renderer drives both identically:
  bool -> chip   (C++ sends "1"/"0")        enum -> stepper (C++ sends option INDEX)
  int  -> stepper [min,max,step]            (resolution/float unused here)
RA stores booleans as the strings "true"/"false"; enum values as the option STRING.
"""
from __future__ import annotations

from .. import proc_guard
from .. import retroarch_cfg
from .rpc import RpcError, method

GROUPS = [
    {"title": "Video", "note": "", "items": [
        {"key": "video_driver", "label": "Video driver", "type": "enum",
         "options": ["vulkan", "glcore", "gl"], "default": "vulkan"},
        {"key": "video_fullscreen", "label": "Fullscreen", "type": "bool", "default": True},
        {"key": "video_vsync", "label": "V-Sync", "type": "bool", "default": True},
        {"key": "video_threaded", "label": "Threaded video", "type": "bool", "default": False},
        {"key": "video_smooth", "label": "Bilinear smoothing", "type": "bool", "default": False},
        {"key": "video_scale_integer", "label": "Integer scale (sharp, pillarboxed)",
         "type": "bool", "default": False},
        {"key": "video_aspect_ratio_auto", "label": "Auto aspect ratio",
         "type": "bool", "default": False},
        {"key": "video_shader_enable", "label": "Shaders", "type": "bool", "default": False},
        {"key": "video_windowed_fullscreen", "label": "Windowed fullscreen",
         "type": "bool", "default": True},
        {"key": "video_hard_sync", "label": "Hard GPU sync (lower latency)",
         "type": "bool", "default": False},
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "audio_enable", "label": "Audio on", "type": "bool", "default": True},
        {"key": "audio_mute_enable", "label": "Mute", "type": "bool", "default": False},
        {"key": "audio_volume", "label": "Volume (dB)", "type": "float",
         "min": -80.0, "max": 12.0, "step": 1.0, "default": 0.0},
        {"key": "audio_driver", "label": "Audio driver", "type": "enum",
         "options": ["pipewire", "pulse", "alsathread", "alsa"], "default": "pipewire"},
        {"key": "audio_latency", "label": "Audio latency (ms)", "type": "int",
         "min": 8, "max": 256, "step": 8, "default": 64},
    ]},
    {"title": "Saves & latency",
     "note": "Run-ahead cuts input latency but costs CPU; fast-forward 0 = unlimited.",
     "items": [
        {"key": "run_ahead_enabled", "label": "Run-ahead", "type": "bool", "default": False},
        {"key": "run_ahead_frames", "label": "Run-ahead frames", "type": "int",
         "min": 1, "max": 6, "step": 1, "default": 1},
        {"key": "rewind_enable", "label": "Rewind", "type": "bool", "default": False},
        {"key": "fastforward_ratio", "label": "Fast-forward speed (0 = ∞)", "type": "float",
         "min": 0.0, "max": 10.0, "step": 1.0, "default": 0.0},
        {"key": "savestate_auto_save", "label": "Auto-save state on exit",
         "type": "bool", "default": False},
        {"key": "savestate_auto_load", "label": "Auto-load state on start",
         "type": "bool", "default": False},
        {"key": "pause_nonactive", "label": "Pause when in background",
         "type": "bool", "default": True},
    ]},
    {"title": "Interface & extras", "note": "", "items": [
        {"key": "menu_driver", "label": "Menu driver", "type": "enum",
         "options": ["ozone", "xmb", "rgui", "glui"], "default": "ozone"},
        {"key": "menu_show_advanced_settings", "label": "Show advanced menu",
         "type": "bool", "default": False},
        {"key": "video_font_enable", "label": "On-screen notifications",
         "type": "bool", "default": True},
        {"key": "input_overlay_enable", "label": "On-screen overlays",
         "type": "bool", "default": False},
        {"key": "video_gpu_screenshot", "label": "Screenshot with shaders",
         "type": "bool", "default": True},
        {"key": "cheevos_enable", "label": "RetroAchievements", "type": "bool",
         "default": False},
    ]},
]

_TRUE = {"1", "true", "yes", "on"}


def _item_by_key(key: str) -> dict | None:
    for g in GROUPS:
        for it in g["items"]:
            if it["key"] == key:
                return it
    return None


def _enum_options(item: dict, cur) -> list[str]:
    """The option list as the C++ stepper sees it: the curated list, with the
    CURRENT cfg value `cur` prepended if it isn't one of ours. get() and set() MUST
    build this identically so the index the C++ sends maps back to the right string."""
    options = list(item["options"])
    if cur is not None and cur not in options:
        options.insert(0, cur)
    return options


@method("retroarch.get")
def _retroarch_get(params):
    """Curated global settings, grouped, with current values read from
    retroarch.cfg. `running` true → RA is live and writes will be refused."""
    vals = retroarch_cfg.get_global_options([it["key"] for g in GROUPS for it in g["items"]])
    out_groups = []
    for g in GROUPS:
        settings = []
        for it in g["items"]:
            raw = vals.get(it["key"])
            t = it["type"]
            if t == "bool":
                value = ((raw.strip().lower() in _TRUE) if raw is not None
                         else bool(it.get("default", False)))
                settings.append({"key": it["key"], "label": it["label"],
                                 "type": "bool", "value": value})
            elif t == "enum":
                options = _enum_options(it, raw)
                cur = raw if raw is not None else it.get("default", options[0])
                value = options.index(cur) if cur in options else 0
                settings.append({"key": it["key"], "label": it["label"], "type": "enum",
                                 "options": options, "value": value})
            elif t == "int":
                try:
                    value = int(float(raw))
                except (TypeError, ValueError):
                    value = int(it.get("default", it.get("min", 0)))
                settings.append({"key": it["key"], "label": it["label"], "type": "int",
                                 "min": it["min"], "max": it["max"], "step": it["step"],
                                 "value": value})
            elif t == "float":
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = float(it.get("default", it.get("min", 0.0)))
                settings.append({"key": it["key"], "label": it["label"], "type": "float",
                                 "min": it["min"], "max": it["max"], "step": it["step"],
                                 "value": value})
        if settings:
            out_groups.append({"title": g["title"], "note": g["note"], "settings": settings})
    return {"exists": True, "path": str(retroarch_cfg.RA_GLOBAL_CFG),
            "running": proc_guard.retroarch_running(), "groups": out_groups}


@method("retroarch.set")
def _retroarch_set(params):
    """Write one curated global RetroArch setting and return the re-read value.
    Refused while RetroArch is running. The C++ sends bool as "1"/"0" and enum as
    the option index (into the list get() returned)."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running — close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    raw_value = params["value"]
    item = _item_by_key(key)
    if item is None:
        raise RpcError("EINVAL", f"{key!r} is not an editable RetroArch setting")
    t = item["type"]

    if t == "bool":
        write = "true" if str(raw_value).strip().lower() in _TRUE else "false"
    elif t == "enum":
        # mirror get() (prepend the live value) so the index maps to the right token
        options = _enum_options(item, retroarch_cfg.get_global_option(key))
        try:
            idx = int(float(raw_value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad enum index {raw_value!r} for {key}")
        if not (0 <= idx < len(options)):
            raise RpcError("EINVAL", f"enum index {idx} out of range for {key}")
        write = options[idx]
    elif t == "int":
        try:
            n = int(float(raw_value))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad integer {raw_value!r} for {key}")
        write = str(max(item["min"], min(item["max"], n)))
    elif t == "float":
        try:
            v = float(raw_value)
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"bad number {raw_value!r} for {key}")
        v = max(item["min"], min(item["max"], v))
        write = f"{v:.6f}"   # RetroArch's float format, e.g. 0.000000
    else:
        raise RpcError("EINVAL", f"unsupported type {t!r} for {key}")

    retroarch_cfg.set_global_option(key, write)

    back = retroarch_cfg.get_global_option(key)
    if t == "bool":
        return {"key": key, "value": (back or "").strip().lower() in _TRUE}
    if t == "enum":
        options = _enum_options(item, back)
        return {"key": key, "value": options.index(back) if back in options else 0}
    if t == "int":
        try:
            return {"key": key, "value": int(float(back))}
        except (TypeError, ValueError):
            return {"key": key, "value": int(write)}
    if t == "float":
        try:
            return {"key": key, "value": float(back)}
        except (TypeError, ValueError):
            return {"key": key, "value": float(write)}
    return {"key": key, "value": back}


# ── RetroArch input bindings (Keybindings page) ───────────────────────────────
# Binds live in retroarch.cfg. kind: "btn" = a joypad button INDEX (capturable via
# the existing button-capture: index = evdev_code - 0x130); "axis" = "±N" (needs
# axis capture — deferred); "gun" = a mouse button / keyboard key (needs the gun
# capture path — deferred, B4). Player binds are input_player<N>_<suffix>; hotkeys
# are global (the suffix IS the full key).
RA_INPUT_GROUPS = [
    {"title": "Face buttons", "player": True, "binds": [
        ("a_btn", "A (east)", "btn"), ("b_btn", "B (south)", "btn"),
        ("x_btn", "X (north)", "btn"), ("y_btn", "Y (west)", "btn")]},
    {"title": "D-pad", "player": True, "binds": [
        ("up_btn", "Up", "btn"), ("down_btn", "Down", "btn"),
        ("left_btn", "Left", "btn"), ("right_btn", "Right", "btn")]},
    {"title": "Shoulders & triggers", "player": True, "binds": [
        ("l_btn", "L", "btn"), ("r_btn", "R", "btn"),
        ("l2_btn", "L2", "btn"), ("r2_btn", "R2", "btn"),
        ("l3_btn", "L3 (stick click)", "btn"), ("r3_btn", "R3 (stick click)", "btn")]},
    {"title": "Start / Select", "player": True, "binds": [
        ("start_btn", "Start", "btn"), ("select_btn", "Select", "btn")]},
    {"title": "Analog sticks", "player": True, "binds": [
        ("l_x_plus_axis", "Left stick →", "axis"), ("l_x_minus_axis", "Left stick ←", "axis"),
        ("l_y_plus_axis", "Left stick ↓", "axis"), ("l_y_minus_axis", "Left stick ↑", "axis"),
        ("r_x_plus_axis", "Right stick →", "axis"), ("r_x_minus_axis", "Right stick ←", "axis"),
        ("r_y_plus_axis", "Right stick ↓", "axis"), ("r_y_minus_axis", "Right stick ↑", "axis")]},
    {"title": "Lightgun", "player": True, "binds": [
        ("gun_trigger_mbtn", "Trigger", "gun"), ("gun_reload_mbtn", "Reload", "gun"),
        ("gun_aux_a_mbtn", "Aux A", "gun"), ("gun_aux_b_mbtn", "Aux B", "gun"),
        ("gun_start_mbtn", "Start", "gun"), ("gun_select_mbtn", "Select", "gun"),
        ("gun_dpad_up", "D-pad up", "gun"), ("gun_dpad_down", "D-pad down", "gun"),
        ("gun_dpad_left", "D-pad left", "gun"), ("gun_dpad_right", "D-pad right", "gun")]},
    {"title": "System hotkeys", "player": False, "binds": [
        ("input_enable_hotkey_btn", "Hotkey modifier", "btn"),
        ("input_menu_toggle_btn", "Menu toggle", "btn"),
        ("input_exit_emulator_btn", "Exit", "btn"),
        ("input_save_state_btn", "Save state", "btn"),
        ("input_load_state_btn", "Load state", "btn"),
        ("input_toggle_fast_forward_btn", "Fast-forward", "btn"),
        ("input_rewind_btn", "Rewind", "btn"),
        ("input_screenshot_btn", "Screenshot", "btn"),
        ("input_pause_toggle_btn", "Pause", "btn"),
        ("input_state_slot_increase_btn", "Next save slot", "btn"),
        ("input_state_slot_decrease_btn", "Prev save slot", "btn")]},
]


@method("retroarch.input_get")  # fast: one cfg read (not ~40 via get_global_option)
def _input_get(params):
    """Grouped RetroArch input bindings with current values, for one player."""
    try:
        player = max(1, min(8, int(params.get("player", 1) or 1)))
    except (TypeError, ValueError):
        player = 1

    def keyfor(g, suffix):
        return f"input_player{player}_{suffix}" if g["player"] else suffix

    allkeys = [keyfor(g, suffix) for g in RA_INPUT_GROUPS for suffix, _, _ in g["binds"]]
    vals = retroarch_cfg.get_global_options(allkeys)  # single file read

    groups = []
    for g in RA_INPUT_GROUPS:
        binds = []
        for suffix, label, kind in g["binds"]:
            val = vals.get(keyfor(g, suffix))
            binds.append({"key": keyfor(g, suffix), "label": label, "kind": kind,
                          "capturable": kind == "btn",   # axis/gun need other capture (B4)
                          "value": val if val is not None else ""})
        groups.append({"title": g["title"], "player_scoped": g["player"], "binds": binds})
    return {"player": player, "running": proc_guard.retroarch_running(), "groups": groups}


@method("retroarch.input_set", slow=True)
def _input_set(params):
    """Write one RetroArch input binding (value already in RA's token form: a
    button index, an axis '±N', a keyboard key, or a mouse button)."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running — close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    if not str(key).startswith("input_"):
        raise RpcError("EINVAL", f"{key!r} is not a RetroArch input binding")
    retroarch_cfg.set_global_option(key, str(params["value"]))
    return {"key": key, "value": retroarch_cfg.get_global_option(key) or ""}
