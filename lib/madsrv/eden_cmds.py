"""Eden (Switch) per-game game list + per-game ini helpers (Eden's citron_games.py role).

Thin shim over the shared yuzu_games engine (Citron/Eden are code-identical here).
`eden.games` reuses the shared Switch library resolver (switch_games) so Eden's per-game
picker shows the same CURRENT library as Citron/Ryujinx. `_CUSTOM` stays a module global
read at call time (tests redirect it; eden_pergame + eden_pg_input_cmds call pergame_path
through this module).

The GLOBAL settings pages live in eden_settings.py (Eden-verified enums + the mandatory
`\\default` twin flip) and the per-game settings pages in eden_pergame.py; both are grouped
into the Eden tile's 5-row tree by standalones_cmds._eden_sections. The historical
eden_cmds.py name is kept (a rename is churn across the backend list + importers).
"""
from __future__ import annotations

from pathlib import Path

from . import switch_games, yuzu_games
from .rpc import method

_CUSTOM = Path.home() / ".config/eden/custom"
_PROFILE_RE = yuzu_games._PROFILE_RE            # kept for greppability/back-compat


def pergame_path(tid: str) -> Path:
    return yuzu_games.pergame_path(_CUSTOM, tid)


def has_override(tid: str) -> bool:
    return yuzu_games.has_override(pergame_path(tid))


def _summary(tid: str) -> str:
    return yuzu_games.summary(pergame_path(tid))


@method("eden.games", slow=True)
def _games(params):
    """Switch games for the per-game media browser: [{titleid,name,stem,override,summary}].
    system = the ES-DE system whose media the browser resolves (art -> preview video)."""
    def _hide(tid):
        # Drop the per-game Add-Ons / Cheats tile for a game that has none (nothing to configure).
        from . import eden_addons_cmds as _ad, eden_cheats_cmds as _ch
        hide = []
        if not _ad.has_content(tid):
            hide.append("addons")
        if not _ch.has_content(tid):
            hide.append("cheats")
        return hide
    return {"games": switch_games.listing(has_override, _summary, _hide), "system": "switch"}
