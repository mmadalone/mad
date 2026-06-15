"""eden.* — Switch (Eden) settings editor: global AND per-game.

GLOBAL: byte-preserving single-value edits to ~/.config/eden/qt-config.ini via
cfgutil. Eden-specific encodings verified against source + the live file: bools are
lowercase `true`/`false`, EXCEPT use_docked_mode (ConsoleMode stored `1`/`0`); the
Qt `key\\default=` twin line is left untouched (the anchored regex only matches
`key=`). Enum index meanings verified against eden settings_enums.h.

PER-GAME (`titleid` param): Eden stores per-game overrides in
~/.config/eden/custom/<TITLEID>.ini. A key INHERITS global when `key\\use_global`
is true/absent; it is OVERRIDDEN by THREE lines — `key\\use_global=false`,
`key\\default=false`, `key=<value>`. So GET resolves inherited→global vs the
per-game value, and SET flips use_global + writes the value (creating the default/
value lines when missing). We never SYNTHESIZE a full per-game ini — if the game
has none, the user must open its Properties in Eden once (version-fragile to fake).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import cfgutil
from .rpc import RpcError, method

_FILE = Path.home() / ".config/eden/qt-config.ini"
_CUSTOM = Path.home() / ".config/eden/custom"
_PROC = "eden"
_LABEL = "Eden (Switch)"
_F = _FILE.name
_TRUEISH = {"true", "1", "yes", "on"}

GROUPS = [
    {"title": "Graphics", "note": "", "items": [
        {"key": "resolution_setup", "label": "Internal resolution", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["0.5x (360p)", "0.75x (540p)", "1x (720p, native)",
                             "1.5x (1080p)", "2x (1440p)", "3x", "4x", "5x", "6x", "7x", "8x"]},
        {"key": "use_vsync", "label": "VSync mode", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Immediate (Off)", "Mailbox", "FIFO (On)", "FIFO Relaxed"]},
        {"key": "scaling_filter", "label": "Scaling filter", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Nearest Neighbor", "Bilinear", "Bicubic", "Gaussian",
                             "ScaleForce", "AMD FSR"]},
        {"key": "anti_aliasing", "label": "Anti-aliasing", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["None", "FXAA", "SMAA"]},
        {"key": "max_anisotropy", "label": "Anisotropic filtering", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Automatic", "Default", "2x", "4x", "8x", "16x"]},
        {"key": "gpu_accuracy", "label": "GPU accuracy", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Normal", "High", "Extreme"]},
        {"key": "astc_recompression", "label": "ASTC recompression", "file": _F,
         "section": "Renderer", "type": "enum", "write_mode": "index",
         "options_display": ["Uncompressed", "BC1 (low)", "BC3 (medium)"]},
    ]},
    {"title": "System / performance", "note": "", "items": [
        {"key": "use_asynchronous_shaders", "label": "Asynchronous shaders", "file": _F,
         "section": "Renderer", "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "use_asynchronous_gpu_emulation", "label": "Asynchronous GPU", "file": _F,
         "section": "Renderer", "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "use_reactive_flushing", "label": "Reactive flushing", "file": _F,
         "section": "Renderer", "type": "bool", "bool_true": "true", "bool_false": "false"},
        {"key": "cpu_accuracy", "label": "CPU accuracy", "file": _F,
         "section": "Cpu", "type": "enum", "write_mode": "index",
         "options_display": ["Auto", "Accurate", "Unsafe", "Paranoid"]},
    ]},
    {"title": "Display", "note": "", "items": [
        {"key": "use_docked_mode", "label": "Docked mode", "file": _F,
         "section": "System", "type": "bool", "bool_true": "1", "bool_false": "0"},
        {"key": "fullscreen", "label": "Start in fullscreen", "file": _F,
         "section": "UI", "type": "bool", "bool_true": "true", "bool_false": "false"},
    ]},
    {"title": "Audio", "note": "", "items": [
        {"key": "output_engine", "label": "Audio output engine", "file": _F,
         "section": "Audio", "type": "enum", "write_mode": "option",
         "options_display": ["Auto", "cubeb", "SDL2", "Null (no audio)", "oboe (Android)"],
         "options_stored": ["auto", "cubeb", "sdl2", "null", "oboe"]},
        {"key": "volume", "label": "Audio volume (%)", "file": _F,
         "section": "Audio", "type": "int", "min": 0, "max": 200, "step": 5},
    ]},
]


def _pergame_path(titleid: str) -> Path:
    return _CUSTOM / f"{titleid.upper()}.ini"


# ── global ──────────────────────────────────────────────────────────────────
@method("eden.get", slow=True)
def _get(params):
    tid = params.get("titleid")
    if tid:
        return _pergame_get(tid)
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("eden.set", slow=True)
def _set(params):
    if params.get("titleid"):
        return _pergame_set(params)
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)


# ── per-game ────────────────────────────────────────────────────────────────
def _inherits(pg_text: str, section: str, name: str) -> bool:
    ug = cfgutil.ini_read(pg_text, section, name + "\\use_global")
    return ug is None or ug.strip().lower() in _TRUEISH


def _pergame_get(titleid: str) -> dict:
    pg = _pergame_path(titleid)
    pg_text = cfgutil.read_text(pg)
    if pg_text is None:
        return {"exists": False, "running": proc_guard.emulator_running(_PROC),
                "note": "This game has no Eden per-game config yet — open its "
                        "Properties in Eden once to create it, then edit here.",
                "groups": []}
    global_text = cfgutil.read_text(_FILE) or ""

    def read(_text, section, name):           # resolve inherited → global, else per-game
        if _inherits(pg_text, section, name):
            return cfgutil.ini_read(global_text, section, name)
        return cfgutil.ini_read(pg_text, section, name)

    res = cfgutil.get_groups(GROUPS, {_F: pg_text}, read,
                             running=proc_guard.emulator_running(_PROC),
                             note="Per-game overrides. A value shown in grey inherits "
                                  "the global Eden setting until you change it here.")
    return res


def _set_or_insert(text: str, section: str, key: str, value: str, after_key: str) -> str:
    r = cfgutil.ini_replace(text, section, key, value)
    if r is not None:
        return r
    out = cfgutil.ini_insert_after(text, section, after_key, f"{key}={value}")
    if out is None:
        raise RpcError("ENOKEY", f"couldn't place '{key}' in [{section}]")
    return out


def _pergame_set(params: dict) -> dict:
    if proc_guard.emulator_running(_PROC):
        raise RpcError("EBUSY", f"{_LABEL} is running — close it first "
                                "(it rewrites its config on exit).")
    tid = params["titleid"]
    pg = _pergame_path(tid)
    text = cfgutil.read_text(pg)
    if text is None:
        raise RpcError("ENOENT", "This game has no Eden per-game config — open its "
                                 "Properties in Eden once to create it.")
    item = cfgutil.item_by_key(GROUPS, params["key"])
    if item is None:
        raise RpcError("EINVAL", f"{params['key']!r} is not an editable setting")
    sec, name = item["section"], item["key"]
    # raw_cur for enum "option" mirroring: the resolved current value.
    if _inherits(text, sec, name):
        raw_cur = cfgutil.ini_read(cfgutil.read_text(_FILE) or "", sec, name) or ""
    else:
        raw_cur = cfgutil.ini_read(text, sec, name) or ""
    stored = cfgutil.compute_write(item, params["value"], raw_cur)
    # Flip use_global → false (must already exist for this key), then set/create the
    # \default=false twin and the value line.
    t = cfgutil.ini_replace(text, sec, name + "\\use_global", "false")
    if t is None:
        raise RpcError("ENOKEY", f"'{name}' isn't in this game's Eden config — open "
                                 "its Properties in Eden once.")
    t = _set_or_insert(t, sec, name + "\\default", "false", name + "\\use_global")
    t = _set_or_insert(t, sec, name, stored, name + "\\default")
    cfgutil.ensure_bak(pg)
    cfgutil.atomic_write(pg, t)
    from .. import staterev
    staterev.bump("config")
    # echo back the C++-shaped value
    back = stored
    if item["type"] == "bool":
        return {"key": name, "value": cfgutil.bool_get(item, back)}
    if item["type"] == "enum":
        _, v = cfgutil._enum_get(item, back)
        return {"key": name, "value": v}
    try:
        return {"key": name, "value": int(float(back))}
    except (TypeError, ValueError):
        return {"key": name, "value": back}


@method("eden.games", slow=True)
def _games(params):
    """Switch games for the per-game picker: [{titleid,name,override}]. `override`
    = the game has an Eden per-game ini with at least one overridden key."""
    from . import switch_games

    def has_override(tid: str) -> bool:
        text = cfgutil.read_text(_pergame_path(tid))
        return bool(text) and "\\use_global=false" in text

    return {"games": switch_games.listing(has_override)}
