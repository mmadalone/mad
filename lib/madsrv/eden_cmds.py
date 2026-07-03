"""eden.* — Switch (Eden) settings editor: global AND per-game.

GLOBAL: byte-preserving single-value edits to ~/.config/eden/qt-config.ini via
cfgutil. Eden-specific encodings verified against source + the live file: bools are
lowercase `true`/`false`, EXCEPT use_docked_mode (ConsoleMode stored `1`/`0`); the
Qt `key\\default=` twin line is left untouched (the anchored regex only matches
`key=`). Enum index meanings verified against eden settings_enums.h.

PER-GAME (`titleid` param): Eden stores per-game overrides in
~/.config/eden/custom/<TITLEID>.ini, byte-format-identical to Citron -- so the shared
Yuzu-fork engine (yuzu_pergame) drives it: inherit-aware ("Inherit global" at index 0) and
create-on-demand. A key inherits global when `key\\use_global` is true/absent; an override is
the triple `key\\use_global=false` + `key\\default=false` + `key=<value>`. Eden keeps its OWN
GROUPS below (its enum indices differ from Citron's -- descriptors are NOT shared). No need to
open the game's Properties in Eden first; the ini is created writing only the overrides.
"""
from __future__ import annotations

from pathlib import Path

from .. import proc_guard
from . import cfgutil
from . import yuzu_pergame as yp
from .rpc import method

_FILE = Path.home() / ".config/eden/qt-config.ini"
_CUSTOM = Path.home() / ".config/eden/custom"
_PROC = "eden"
_LABEL = "Eden (Switch)"
_F = _FILE.name

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
    if params.get("titleid"):
        return _pergame_get(yp.tid(params))
    return cfgutil.do_get(GROUPS, _FILE, cfgutil.ini_read, proc=_PROC, label=_LABEL)


@method("eden.set", slow=True)
def _set(params):
    if params.get("titleid"):
        return _pergame_set(params)
    return cfgutil.do_set(GROUPS, params, _FILE, cfgutil.ini_read, cfgutil.ini_replace,
                          proc=_PROC, label=_LABEL)


# ── per-game (shared Yuzu-fork engine: create-on-demand, inherit-aware) ───────
_PG_NOTE = ("Per-game overrides for Eden. Pick 'Inherit global' to clear an override so this game "
            "uses your global Eden setting. Each change saves instantly and only affects this game.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _pergame_get(titleid: str) -> dict:
    pg_text = cfgutil.read_text(_pergame_path(titleid))
    return yp.pergame_get(GROUPS, pg_text, _PG_NOTE, _running())


def _pergame_set(params: dict) -> dict:
    return yp.pergame_set(GROUPS, params, _pergame_path, _running, _LABEL)


def _has_override(tid: str) -> bool:
    # spaces-tolerant: MAD-created inis use `key = value` (see yuzu_pergame.has_override).
    return yp.has_override(cfgutil.read_text(_pergame_path(tid)))


def _summary(tid: str) -> str:
    return "Custom: settings" if _has_override(tid) else ""


@method("eden.games", slow=True)
def _games(params):
    """Switch games for the per-game media browser: [{titleid,name,stem,override,summary}].
    `override` = the game has an Eden per-game ini with at least one overridden key."""
    from . import switch_games

    return {"games": switch_games.listing(_has_override, _summary), "system": "switch"}
