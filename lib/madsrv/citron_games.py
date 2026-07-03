"""Citron per-game game list + per-game ini helpers.

`citron.games` reuses the shared Switch library resolver (switch_games) so Citron's per-game
picker shows the same CURRENT library as Eden/Ryujinx (the user's ROMs, incl. those whose
filename lacks a [TITLEID] tag, via Citron's own scan). Per-game override model = Eden's:
`custom/<TITLEID uppercased>.ini`, a key inherits global when `key\\use_global` is true/absent,
else the triple `\\use_global=false`/`\\default=false`/value.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import cfgutil, switch_games
from . import yuzu_pergame as yp
from .rpc import method

_CUSTOM = Path.home() / ".config/citron/custom"
_PROFILE_RE = re.compile(r"(?m)^player_\d+_profile_name=\s*\S")


def pergame_path(tid: str) -> Path:
    return _CUSTOM / f"{tid.upper()}.ini"


def has_override(tid: str) -> bool:
    """The game has a Citron per-game ini with an actual override: a settings override
    (`\\use_global=false`) OR a baked per-game input profile (a non-empty `player_N_profile_name`,
    which is stored WITHOUT a use_global marker)."""
    text = cfgutil.read_text(pergame_path(tid))
    # spaces-tolerant: MAD-created inis use `key = value` (see yuzu_pergame.has_override).
    return yp.has_override(text) or bool(text and _PROFILE_RE.search(text))


def _summary(tid: str) -> str:
    """The media browser's info line: which per-game aspects are overridden ("" == all default)."""
    text = cfgutil.read_text(pergame_path(tid)) or ""
    parts = []
    if yp.has_override(text):              # spaces-tolerant (MAD-created inis use `key = value`)
        parts.append("settings")
    if _PROFILE_RE.search(text):
        parts.append("input profile")
    return "Custom: " + ", ".join(parts) if parts else ""


@method("citron.games", slow=True)
def _games(params):
    # system = the ES-DE system whose media the browser resolves (art -> preview video).
    return {"games": switch_games.listing(has_override, _summary), "system": "switch"}
