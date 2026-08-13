"""Citron per-game game list + per-game ini helpers.

Thin shim over the shared yuzu_games engine (Citron/Eden are code-identical here).
`citron.games` reuses the shared Switch library resolver (switch_games) so Citron's
per-game picker shows the same CURRENT library as Eden/Ryujinx. `_CUSTOM` stays a module
global read at call time (tests redirect it; citron_pergame + citron_pg_input_cmds call
pergame_path through this module).
"""
from __future__ import annotations

from pathlib import Path

from . import switch_games, yuzu_games
from .rpc import method

_CUSTOM = Path.home() / ".config/citron/custom"
_PROFILE_RE = yuzu_games._PROFILE_RE            # kept for greppability/back-compat


def pergame_path(tid: str) -> Path:
    return yuzu_games.pergame_path(_CUSTOM, tid)


def has_override(tid: str) -> bool:
    return yuzu_games.has_override(pergame_path(tid))


def _summary(tid: str) -> str:
    return yuzu_games.summary(pergame_path(tid))


@method("citron.games", slow=True)
def _games(params):
    # system = the ES-DE system whose media the browser resolves (art -> preview video).
    def _hide(tid):
        # Drop the per-game Add-Ons / Cheats tile for a game that has none (nothing to configure).
        from . import citron_addons_cmds as _ad, citron_cheats_cmds as _ch
        hide = []
        if not _ad.has_content(tid):
            hide.append("addons")
        if not _ch.has_content(tid):
            hide.append("cheats")
        return hide
    return {"games": switch_games.listing(has_override, _summary, _hide), "system": "switch"}
