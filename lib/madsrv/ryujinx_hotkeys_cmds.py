"""ryujinx_hk.* — the Hotkeys page on the Ryujinx tile (Input group).

Ryujinx hotkeys are single KEYBOARD keys (not chords / controller combos), stored in the nested
`hotkeys` object of ~/.config/Ryujinx/Config.json:
  {"toggle_vsync_mode":"F1","screenshot":"F8",...,"turbo_mode_while_held":false}
Each keyed action is an enum over a curated Key list (Ryujinx silently reverts a bad token to member
0, so we only ever write exact Key-enum names); turbo_mode_while_held is the one bool. Rendered by
the generic GuiMadPageEmuSettings, so the payload mirrors cfgutil.get_groups
(exists/running/note/groups). Only actions PRESENT in the live config are offered (version-safe).
ryujinx_json.write bumps staterev('config') at its atomic chokepoint."""
from __future__ import annotations

from .. import proc_guard
from . import cfgutil, ryujinx_json
from .rpc import RpcError, method

_PROC = "ryujinx"
_LABEL = "Ryujinx (Hotkeys)"

# Curated keyboard Key names (exact Ryujinx Key-enum tokens). A current value outside this list is
# appended to that row's options on the fly so it stays representable.
_KEYS = ["Unbound", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
         "Space", "Enter", "Tab", "Escape", "Pause", "Insert", "Delete",
         "Home", "End", "PageUp", "PageDown", "Up", "Down", "Left", "Right"]

# action key -> friendly label (row order). turbo_mode_while_held is the one bool (below).
_ACTIONS = [
    ("toggle_vsync_mode", "Toggle VSync"),
    ("screenshot", "Screenshot"),
    ("show_ui", "Toggle UI"),
    ("pause", "Pause"),
    ("toggle_mute", "Mute"),
    ("res_scale_up", "Resolution scale up"),
    ("res_scale_down", "Resolution scale down"),
    ("volume_up", "Volume up"),
    ("volume_down", "Volume down"),
    ("custom_vsync_interval_increment", "Custom VSync interval up"),
    ("custom_vsync_interval_decrement", "Custom VSync interval down"),
    ("turbo_mode", "Turbo mode (toggle)"),
]
_BOOL_ACTION = "turbo_mode_while_held"


def _options_for(current: str) -> list:
    opts = list(_KEYS)
    if current and current not in opts:
        opts.append(current)
    return opts


def _hotkeys() -> dict:
    try:
        hk = ryujinx_json.load().get("hotkeys")
        return hk if isinstance(hk, dict) else {}
    except (OSError, ValueError):
        return {}


@method("ryujinx_hk.get", slow=True, cache=("config",))
def _get(params):
    hk = _hotkeys()
    rows = []
    for key, label in _ACTIONS:
        if key not in hk:
            continue                       # version-safe: only offer present actions
        current = str(hk.get(key) or "Unbound")
        opts = _options_for(current)
        rows.append({"key": key, "label": label, "type": "enum",
                     "options": opts, "value": opts.index(current)})
    if _BOOL_ACTION in hk:
        rows.append({"key": _BOOL_ACTION, "label": "Turbo mode while held",
                     "type": "bool", "value": bool(hk.get(_BOOL_ACTION))})
    return {"exists": bool(hk), "running": proc_guard.emulator_running(_PROC),
            "note": "Each hotkey is a single keyboard key (Ryujinx has no controller hotkeys).",
            "groups": [{"title": "Hotkeys", "note": "", "settings": rows}]}


@method("ryujinx_hk.set", slow=True)
def _set(params):
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close Ryujinx first "
                                "(it rewrites its config on exit).")
    key = params["key"]
    try:
        data = ryujinx_json.load()
    except (OSError, ValueError):
        raise RpcError("ENOENT", "Ryujinx config not found/readable")
    hk = data.get("hotkeys")
    if not isinstance(hk, dict) or key not in hk:
        raise RpcError("EINVAL", f"{key!r} is not a Ryujinx hotkey")
    if key == _BOOL_ACTION:
        out = str(params["value"]).strip().lower() in ("1", "true", "yes", "on")
        hk[key] = out
    else:
        current = str(hk.get(key) or "Unbound")
        opts = _options_for(current)
        try:
            hk[key] = opts[int(params["value"])]
        except (ValueError, IndexError, TypeError):
            raise RpcError("EINVAL", f"bad hotkey index {params.get('value')!r}")
        out = int(params["value"])
    cfgutil.ensure_bak(ryujinx_json.CONFIG)
    ryujinx_json.write(data, ryujinx_json.CONFIG)
    return {"key": key, "value": out}
