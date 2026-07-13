r"""citron_pg_*.* — Citron (Switch) PER-GAME settings (System / CPU / Graphics / Adv. Graphics /
Audio / Linux), inherit-aware over ~/.config/citron/custom/<TITLEID>.ini.

The override model + inherit-aware rendering + create-on-demand write path all live in the shared
Yuzu-fork engine (lib/madsrv/yuzu_pergame.py, shared with Eden). This module only supplies Citron's
descriptor GROUPS (reused from citron_settings -- Citron's enums, NOT shareable with Eden), the
per-game ini path, the running check, and the six page namespaces. Instant save; refuses while
Citron runs (it rewrites config on exit).
"""
from __future__ import annotations

from .. import proc_guard
from . import cfgutil, citron_games
from . import citron_settings as cs
from . import yuzu_pergame as yp
from .rpc import method

_PROC = "citron"

# Per-game pages: reuse the global descriptor groups; the per-game dialog has no "General" tab, so
# [Core] rides on System and [Linux] is its own Linux page (both come from GENERAL_GROUPS).
_CORE_GROUP = {**cs.GENERAL_GROUPS[0], "title": "Core / performance"}   # use_multi_core, speed_limit, memory_layout_mode
_LINUX_GROUP = cs.GENERAL_GROUPS[1]                                     # enable_gamemode

PG_PAGES = {
    "citron_pg_system": ("System", cs.SYSTEM_GROUPS + [_CORE_GROUP]),
    "citron_pg_cpu":    ("CPU", cs.CPU_GROUPS),
    "citron_pg_gfx":    ("Graphics", cs.GFX_GROUPS),
    "citron_pg_gfxadv": ("Adv. Graphics", cs.GFXADV_GROUPS),
    "citron_pg_audio":  ("Audio", cs.AUDIO_GROUPS),
    "citron_pg_linux":  ("Linux", [_LINUX_GROUP]),
}

_NOTE = ("Per-game overrides. Pick 'Inherit global' to clear one; changes save instantly.")


def _running() -> bool:
    return proc_guard.emulator_running(_PROC)


def _register(ns: str, groups: list) -> None:
    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        pg_text = cfgutil.read_text(citron_games.pergame_path(yp.tid(params)))
        return yp.pergame_get(groups, pg_text, _NOTE, _running())

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        return yp.pergame_set(groups, params, citron_games.pergame_path, _running, "Citron")


for _ns, (_title, _groups) in PG_PAGES.items():
    _register(_ns, _groups)
