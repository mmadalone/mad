"""Eden (Switch) per-game game list + per-game ini helpers (Eden's citron_games.py role).

`eden.games` reuses the shared Switch library resolver (switch_games) so Eden's per-game picker
shows the same CURRENT library as Citron/Ryujinx (the user's ROMs, incl. those whose filename
lacks a [TITLEID] tag). Per-game override model = Citron's: `custom/<TITLEID uppercased>.ini`, a
key inherits global when `key\\use_global` is true/absent, else the triple
`\\use_global=false`/`\\default=false`/value; a per-game INPUT profile bakes `player_N_profile_name`.

The GLOBAL settings pages moved to eden_settings.py (7 pages, Eden-verified enums + the mandatory
`\\default` twin flip) and the per-game settings pages to eden_pergame.py; both are grouped into the
Eden tile's 5-row tree by standalones_cmds._eden_sections. This module keeps only the game listing +
the per-game path/override helpers those pages share.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import cfgutil, switch_games
from . import yuzu_pergame as yp
from .rpc import method

_CUSTOM = Path.home() / ".config/eden/custom"
_PROFILE_RE = re.compile(r"(?m)^player_\d+_profile_name=\s*\S")


def pergame_path(tid: str) -> Path:
    return _CUSTOM / f"{tid.upper()}.ini"


def has_override(tid: str) -> bool:
    """The game has an Eden per-game ini with an actual override: a settings override
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


@method("eden.games", slow=True)
def _games(params):
    """Switch games for the per-game media browser: [{titleid,name,stem,override,summary}].
    system = the ES-DE system whose media the browser resolves (art -> preview video)."""
    return {"games": switch_games.listing(has_override, _summary), "system": "switch"}
