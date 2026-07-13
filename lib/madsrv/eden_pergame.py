r"""eden_pg_*.* - Eden (Switch) PER-GAME settings (System / CPU / Graphics / Adv. Graphics /
GPU extensions / Audio / Linux), inherit-aware over ~/.config/eden/custom/<TITLEID>.ini.

The override model + inherit-aware rendering + create-on-demand write path all live in the shared
Yuzu-fork engine (lib/madsrv/yuzu_pergame.py, shared with Citron). This module only supplies Eden's
descriptor GROUPS (reused from eden_settings -- Eden's enums, NOT shareable with Citron), the
per-game ini path (eden_cmds.pergame_path), the running check, and the seven page namespaces.
Instant save; refuses while Eden runs (it rewrites config on exit).
"""
from __future__ import annotations

from .. import proc_guard
from . import cfgutil, eden_cmds
from . import eden_settings as es
from . import yuzu_pergame as yp
from .rpc import method

_PROC = "eden"

# Per-game pages: reuse the global descriptor groups; the per-game dialog has no "General" tab, so
# [Core] rides on System and [Linux] is its own Linux page (both come from GENERAL_GROUPS).
_CORE_GROUP = {**es.GENERAL_GROUPS[0], "title": "Core / performance"}   # use_multi_core, speed_limit, memory_layout_mode
_LINUX_GROUP = es.GENERAL_GROUPS[1]                                     # enable_gamemode

PG_PAGES = {
    "eden_pg_system": ("System", es.SYSTEM_GROUPS + [_CORE_GROUP]),
    "eden_pg_cpu":    ("CPU", es.CPU_GROUPS),
    "eden_pg_gfx":    ("Graphics", es.GFX_GROUPS),
    "eden_pg_gfxadv": ("Adv. Graphics", es.GFXADV_GROUPS),
    "eden_pg_gfxext": ("GPU extensions", es.GFXEXT_GROUPS),
    "eden_pg_audio":  ("Audio", es.AUDIO_GROUPS),
    "eden_pg_linux":  ("Linux", [_LINUX_GROUP]),
}

_NOTE = ("Per-game overrides. Pick 'Inherit global' to clear one; changes save instantly.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        pg_text = cfgutil.read_text(eden_cmds.pergame_path(yp.tid(params)))
        return yp.pergame_get(groups, pg_text, _NOTE, _running())

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        return yp.pergame_set(groups, params, eden_cmds.pergame_path, _running, "Eden")


for _ns, (_title, _groups) in PG_PAGES.items():
    _register(_ns, _groups)
