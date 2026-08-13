"""Shared Yuzu-fork (Citron/Eden) per-game path + override helpers.

citron_games.py and eden_cmds.py were code-identical glue; their logic lives here as pure
functions taking explicit paths. The shims keep `_CUSTOM` as a module global read at call
time plus module-level pergame_path/has_override/_summary wrappers (tests monkeypatch
`<shim>._CUSTOM`, and citron_pergame / citron_pg_input_cmds + the eden twins call
`<shim>.pergame_path`), and each shim registers its own `<fork>.games` method with the
fork's deferred addons/cheats imports (kept in the shim so the import graph stays acyclic
and greppable).

Override model shared by both forks: `custom/<TITLEID uppercased>.ini`, a key inherits
global when `key\\use_global` is true/absent, else the triple
`\\use_global=false`/`\\default=false`/value; a per-game INPUT profile bakes
`player_N_profile_name`. Not for Ryujinx (JSON config, own ryujinx_cmds listing).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import cfgutil
from . import yuzu_pergame as yp

_PROFILE_RE = re.compile(r"(?m)^player_\d+_profile_name=\s*\S")


def pergame_path(custom: Path, tid: str) -> Path:
    return custom / f"{tid.upper()}.ini"


def has_override(path: Path) -> bool:
    """The game's per-game ini has an actual override: a settings override
    (`\\use_global=false`) OR a baked per-game input profile (a non-empty
    `player_N_profile_name`, which is stored WITHOUT a use_global marker)."""
    text = cfgutil.read_text(path)
    # spaces-tolerant: MAD-created inis use `key = value` (see yuzu_pergame.has_override).
    return yp.has_override(text) or bool(text and _PROFILE_RE.search(text))


def summary(path: Path) -> str:
    """The media browser's info line: which per-game aspects are overridden ("" == all default)."""
    text = cfgutil.read_text(path) or ""
    parts = []
    if yp.has_override(text):              # spaces-tolerant (MAD-created inis use `key = value`)
        parts.append("settings")
    if _PROFILE_RE.search(text):
        parts.append("input profile")
    return "Custom: " + ", ".join(parts) if parts else ""
