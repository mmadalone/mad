r"""citron_pg_input.* - Citron (Switch) PER-GAME Input Profiles.

Thin shim: the baking model docs + all logic live in the shared yuzu_pg_input engine
(profile picks BAKE resolved bindings into custom/<TITLEID>.ini - see the engine
docstring and memory switch-per-game-profile-routing). _INPUT_DIR is a module global read
per call through the engine's getter (tests redirect it); the per-game path comes from
citron_games.pergame_path (which reads citron_games._CUSTOM at call time).
"""
from __future__ import annotations

from pathlib import Path

from . import citron_games, yuzu_pg_input

_INPUT_DIR = Path.home() / ".config/citron/input"
_PROC = "citron"

_impl = yuzu_pg_input.PgInputEngine(
    ns="citron_pg_input", display="Citron",
    input_dir_getter=lambda: _INPUT_DIR, input_hint="~/.config/citron/input/",
    pergame_path_fn=citron_games.pergame_path, proc=_PROC)
