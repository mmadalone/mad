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

import re

from .. import device_binds
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
        # L2/R2 exist as BOTH a digital button (l2_btn) and an analog axis
        # (l2_axis). Most pads (DualSense, Xbox, X-Arcade in Xbox mode) report
        # the triggers as ABS axes, which only the axis-capture path catches —
        # so offer both: "(button)" for digital-click pads, "(analog)" for axes.
        ("l2_btn", "L2 (button)", "btn"), ("r2_btn", "R2 (button)", "btn"),
        ("l2_axis", "L2 (analog)", "axis"), ("r2_axis", "R2 (analog)", "axis"),
        ("l3_btn", "L3 (stick click)", "btn"), ("r3_btn", "R3 (stick click)", "btn")]},
    {"title": "Start / Select", "player": True, "binds": [
        ("start_btn", "Start", "btn"), ("select_btn", "Select", "btn")]},
    {"title": "Analog sticks", "player": True, "binds": [
        ("l_x_plus_axis", "Left stick →", "axis"), ("l_x_minus_axis", "Left stick ←", "axis"),
        ("l_y_plus_axis", "Left stick ↓", "axis"), ("l_y_minus_axis", "Left stick ↑", "axis"),
        ("r_x_plus_axis", "Right stick →", "axis"), ("r_x_minus_axis", "Right stick ←", "axis"),
        ("r_y_plus_axis", "Right stick ↓", "axis"), ("r_y_minus_axis", "Right stick ↑", "axis")]},
    # BASE gun actions (no _mbtn/_btn suffix): each has 4 cfg variants — the bare
    # key (keyboard), _btn (joypad), _axis, _mbtn (mouse). input_set_gun writes the
    # one matching the captured kind and nuls the others. input_get shows whichever
    # is live.
    {"title": "Lightgun", "player": True, "binds": [
        ("gun_trigger", "Trigger", "gun"), ("gun_reload", "Reload", "gun"),
        ("gun_aux_a", "Aux A", "gun"), ("gun_aux_b", "Aux B", "gun"),
        ("gun_start", "Start", "gun"), ("gun_select", "Select", "gun"),
        ("gun_dpad_up", "D-pad up", "gun"), ("gun_dpad_down", "D-pad down", "gun"),
        ("gun_dpad_left", "D-pad left", "gun"), ("gun_dpad_right", "D-pad right", "gun")]},
    # Hotkeys are GLOBAL (no player prefix); the base key is listed and each can bind
    # to a JOYPAD button (_btn) OR a MOUSE button (_mbtn). The "hotkey" kind captures
    # either (the X-Arcade red button = mouse, for an arcade-cabinet quit/menu);
    # input_set_hotkey writes the _btn or _mbtn variant for the captured code and nuls
    # the sibling button-types (the keyboard variant is left alone so e.g. F1 still
    # opens the menu). A mouse hotkey also needs input_player1_mouse_index pinned at
    # that mouse — RA polls hotkeys on player-1's mouse only (controller-router does it).
    {"title": "System hotkeys", "player": False, "binds": [
        ("input_enable_hotkey", "Hotkey modifier", "hotkey"),
        ("input_menu_toggle", "Menu toggle", "hotkey"),
        ("input_exit_emulator", "Exit", "hotkey"),
        ("input_save_state", "Save state", "hotkey"),
        ("input_load_state", "Load state", "hotkey"),
        ("input_toggle_fast_forward", "Fast-forward", "hotkey"),
        ("input_rewind", "Rewind", "hotkey"),
        ("input_screenshot", "Screenshot", "hotkey"),
        ("input_pause_toggle", "Pause", "hotkey"),
        ("input_state_slot_increase", "Next save slot", "hotkey"),
        ("input_state_slot_decrease", "Prev save slot", "hotkey")]},
]


def _resolve_device(device):
    """Resolve a {vidpid, name} identity to a connected evdev joypad, or None."""
    if not device:
        return None
    vidpid = str(device.get("vidpid", "")).lower()
    name = device.get("name") or ""
    try:
        from .. import devices as _dv
        for d in _dv.enumerate_devices():
            if d.is_joypad and f"{d.vid:04x}:{d.pid:04x}" == vidpid and (not name or d.name == name):
                return d
    except Exception:
        return None
    return None


def _device_id(d):
    return {"vidpid": f"{d.vid:04x}:{d.pid:04x}", "name": d.name}


def _connected_pads():
    """Connected real joypads for the device picker. Excludes Sinden guns, MAD's virtual
    nav pad, and Steam virtual PHANTOMS (28de:11ff — vendor Valve, deceptively named
    "Microsoft X-Box 360 pad N"). Labels are port-aware via pad_label ("X-Arcade"); the
    X-Arcade's two halves are emitted SEPARATELY as "X-Arcade P1"/"P2" (by USB interface),
    though both carry the raw `name` so RetroArch's vid:pid+name profile matching resolves
    either to the same autoconfig (the halves can't be bound differently — same profile)."""
    try:
        from .. import devices as _dv
        from .device_cmds import pad_label
        from ..routing import xarcade_port
        from ..policy import load_merged
        xport = xarcade_port(load_merged())
        out, seen = [], set()
        for d in _dv.enumerate_devices():
            if (not d.is_joypad or d.is_sinden or d.is_mad_virtual or d.is_steam_virtual):
                continue
            vidpid = f"{d.vid:04x}:{d.pid:04x}"
            label = pad_label(d.vid, vidpid, d.name, _dv.port_of(d.phys), xport)
            if label == "X-Arcade":            # split the two halves into P1/P2 (don't dedup)
                iface = _dv.usb_iface_num(d.path)
                label = f"X-Arcade P{iface + 1}" if iface in (0, 1) else "X-Arcade"
                key = (vidpid, d.name, iface)
            else:
                key = (vidpid, d.name)
            if key in seen:
                continue
            seen.add(key)
            out.append({"vidpid": vidpid, "name": d.name, "label": label,
                        "reservable": not d.is_steam_virtual})
        return out
    except Exception:
        return []


def _device_keys(dd) -> set:
    """The device's EV_KEY capability set — for hiding rows a pad physically can't produce
    (e.g. the digital L2/R2 buttons on a pad whose triggers are analog-axis only)."""
    try:
        import evdev
        d = evdev.InputDevice(dd.path)
        try:
            return set(d.capabilities().get(evdev.ecodes.EV_KEY, []))
        finally:
            d.close()
    except Exception:
        return set()


@method("retroarch.input_get")  # fast: one cfg read (not ~40 via get_global_option)
def _input_get(params):
    """Grouped RetroArch input bindings with current values, for one player (global
    cfg) or one controller (device mode — per-player binds from its autoconfig)."""
    try:
        player = max(1, min(8, int(params.get("player", 1) or 1)))
    except (TypeError, ValueError):
        player = 1
    # Device mode: when the page targets a specific controller, per-PLAYER gamepad
    # binds are read from (and written to) THAT device's autoconfig — so they
    # survive the controller-router's reserved-port override at launch. Lightgun
    # and hotkeys stay global. No device → the legacy global-cfg view.
    dd = _resolve_device(params.get("device"))
    dev_binds = device_binds.get_device_binds(dd) if dd else {}
    dd_keys = _device_keys(dd) if dd else None   # device mode: hide trigger rows it lacks

    def keyfor(g, suffix):
        return f"input_player{player}_{suffix}" if g["player"] else suffix

    # gun binds need all 4 cfg variants read so we can show whichever is live.
    allkeys = []
    for g in RA_INPUT_GROUPS:
        for suffix, _, kind in g["binds"]:
            k = keyfor(g, suffix)
            allkeys += _gun_variant_keys(k) if kind in ("gun", "hotkey") else [k]
    vals = retroarch_cfg.get_global_options(allkeys)  # single file read

    groups = []
    for g in RA_INPUT_GROUPS:
        binds = []
        for suffix, label, kind in g["binds"]:
            # Device mode: hide the digital-trigger rows ("L2/R2 (button)") for a pad whose
            # triggers are ANALOG only (no BTN_TL2/TR2 — e.g. the X-Arcade: ABS_Z/RZ). The
            # "(analog)" rows capture those instead, so the dead button rows just confuse.
            if dd_keys is not None and suffix in ("l2_btn", "r2_btn"):
                if (0x138 if suffix == "l2_btn" else 0x139) not in dd_keys:
                    continue
            k = keyfor(g, suffix)
            if kind in ("gun", "hotkey"):
                value = _gun_display(vals, k)
            elif dd and g["player"]:
                raw = dev_binds.get(suffix)            # device-scoped per-player bind
                value = "" if raw in (None, "nul") else raw
            else:
                raw = vals.get(k)
                value = "" if raw in (None, "nul") else raw
            binds.append({"key": k, "label": label, "kind": kind,
                          # btn/axis/gun/hotkey are all capturable; axis uses axis
                          # capture, gun uses pointer, hotkey uses combo (joypad OR mouse).
                          "capturable": kind in ("btn", "axis", "gun", "hotkey"),
                          "value": value})
        groups.append({"title": g["title"], "player_scoped": g["player"], "binds": binds})
    return {"player": player, "running": proc_guard.retroarch_running(), "groups": groups,
            "mode": "device" if dd else "global",
            "device": _device_id(dd) if dd else None,
            "device_label": dd.name if dd else "",
            "devices": _connected_pads()}


def _gun_variant_keys(basekey: str) -> list:
    """The 4 retroarch.cfg keys a gun action can live in (keyboard / joypad / axis /
    mouse)."""
    return [basekey, basekey + "_btn", basekey + "_axis", basekey + "_mbtn"]


def _gun_display(vals: dict, basekey: str) -> str:
    """Human value of whichever gun variant is set (mouse > key > btn > axis)."""
    def ok(v):
        return v not in (None, "", "nul")
    mbtn, kbd = vals.get(basekey + "_mbtn"), vals.get(basekey)
    btn, axis = vals.get(basekey + "_btn"), vals.get(basekey + "_axis")
    if ok(mbtn):
        return f"mouse {mbtn}"
    if ok(kbd):
        return f"key {kbd}"
    if ok(btn):
        return f"btn {btn}"
    if ok(axis):
        return f"axis {axis}"
    return ""


@method("retroarch.input_set", slow=True)
def _input_set(params):
    """Write one RetroArch input binding (value already in RA's token form: a
    button index, an axis '±N', a keyboard key, or a mouse button).

    Device mode: a per-PLAYER bind on a RESERVABLE controller is written to THAT
    device's autoconfig sentinel (device_binds.set_device_bind), so the router
    carries it onto the reserved port and it SURVIVES launch. Hotkeys (no player
    prefix) and the non-reservable Steam Deck pad fall back to the global cfg —
    which is what actually applies in those cases."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running — close it first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    if not str(key).startswith("input_"):
        raise RpcError("EINVAL", f"{key!r} is not a RetroArch input binding")
    value = str(params["value"])
    dd = _resolve_device(params.get("device"))
    m = re.match(r"^input_player\d+_(.+)$", key)
    if dd is not None and m and not dd.is_steam_virtual:
        device_binds.set_device_bind(dd, m.group(1), value)
        from .. import staterev
        staterev.bump("config")
        return {"key": key, "value": value, "scope": "device", "device": dd.name}
    retroarch_cfg.set_global_option(key, value)
    return {"key": key, "value": retroarch_cfg.get_global_option(key) or "",
            "scope": "global"}


@method("retroarch.input_set_gun", slow=True)
def _input_set_gun(params):
    """Bind one lightgun action to a captured mouse button or keyboard key. Writes
    the matching variant (mouse → ..._mbtn, key → bare key) and nuls the siblings so
    only one input source is live."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running — close it first "
                                "(it rewrites its config on exit).")
    try:
        player = max(1, min(16, int(params.get("player", 1) or 1)))
    except (TypeError, ValueError):
        player = 1
    base = str(params.get("base", ""))
    if not base.startswith("gun_"):
        raise RpcError("EINVAL", f"{base!r} is not a lightgun binding")
    kind = params.get("kind")
    if kind not in ("mouse", "key"):
        raise RpcError("EINVAL", f"kind must be mouse|key, got {kind!r}")
    value = str(params["value"])
    pfx = f"input_player{player}_{base}"
    variants = {"mbtn": pfx + "_mbtn", "kbd": pfx, "btn": pfx + "_btn", "axis": pfx + "_axis"}
    target = "mbtn" if kind == "mouse" else "kbd"
    for vk, key in variants.items():
        retroarch_cfg.set_global_option(key, value if vk == target else "nul")
    return {"base": base, "player": player, "kind": kind, "value": value}


@method("retroarch.input_set_hotkey", slow=True)
def _input_set_hotkey(params):
    """Bind a GLOBAL system hotkey to a captured JOYPAD button or MOUSE button.
    `base` is the bare hotkey key (e.g. input_exit_emulator); `code` is the raw evdev
    code from the combo capture (which opens gamepad + mouse nodes). Writes the
    matching variant — _btn for a joypad button (RA index = code-0x130), _mbtn for a
    mouse button (1=left … 5=extra) — and nuls the sibling button-types so exactly one
    button source drives the hotkey. The keyboard variant (the bare key) is left alone,
    so e.g. F1 can still open the menu alongside a bound button.

    A MOUSE hotkey only fires if input_player1_mouse_index points at that mouse — RA
    polls hotkeys on port 0 (player 1) only. The controller-router pins the X-Arcade
    trackball there per non-lightgun RA launch."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running — close it first "
                                "(it rewrites its config on exit).")
    base = str(params.get("base", ""))
    if not base.startswith("input_") or base.endswith(("_btn", "_mbtn", "_axis")):
        raise RpcError("EINVAL", f"{base!r} is not a hotkey base key")
    btn, mbtn, axis = base + "_btn", base + "_mbtn", base + "_axis"
    # A d-pad / hat DIRECTION ("h0up" etc.) is a valid *_btn value in RetroArch — let the
    # X-Arcade joystick (or any hat) drive a hotkey by writing the token to _btn.
    token = str(params.get("token", ""))
    if token:
        if not re.match(r"^h[0-3](up|down|left|right)$", token):
            raise RpcError("EINVAL", f"token {token!r} is not a valid hat token (e.g. 'h0up')")
        retroarch_cfg.set_global_option(btn, token)
        retroarch_cfg.set_global_option(mbtn, "nul")
        retroarch_cfg.set_global_option(axis, "nul")
        return {"base": base, "kind": "dpad", "value": token}
    try:
        code = int(params["code"])
    except (TypeError, ValueError, KeyError):
        raise RpcError("EINVAL", f"bad evdev code {params.get('code')!r}")
    if 0x110 <= code <= 0x114:            # mouse button -> _mbtn (1=left … 5=extra)
        num = code - 0x110 + 1
        retroarch_cfg.set_global_option(mbtn, str(num))
        retroarch_cfg.set_global_option(btn, "nul")
        retroarch_cfg.set_global_option(axis, "nul")
        return {"base": base, "kind": "mouse", "value": str(num)}
    if code >= 0x130:                      # joypad button -> _btn (RA index = code-0x130)
        idx = code - 0x130
        retroarch_cfg.set_global_option(btn, str(idx))
        retroarch_cfg.set_global_option(mbtn, "nul")
        retroarch_cfg.set_global_option(axis, "nul")
        return {"base": base, "kind": "btn", "value": str(idx)}
    raise RpcError("EINVAL", f"evdev code {code} is neither a joypad nor a mouse button")
