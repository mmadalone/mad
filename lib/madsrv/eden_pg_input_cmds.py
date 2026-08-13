r"""eden_pg_input.* - Eden (Switch) PER-GAME Input Profiles.

Thin shim: the baking model docs + all logic live in the shared yuzu_pg_input engine
(profile picks BAKE resolved bindings into custom/<TITLEID>.ini - see the engine
docstring and memory switch-per-game-profile-routing). _INPUT_DIR is a module global read
per call through the engine's getter (tests redirect it); the per-game path comes from
eden_cmds.pergame_path (which reads eden_cmds._CUSTOM at call time).
"""
from __future__ import annotations

from pathlib import Path

from . import eden_cmds, yuzu_pg_input

_INPUT_DIR = Path.home() / ".config/eden/input"
_PROC = "eden"

_impl = yuzu_pg_input.PgInputEngine(
    ns="eden_pg_input", display="Eden",
    input_dir_getter=lambda: _INPUT_DIR, input_hint="~/.config/eden/input/",
    pergame_path_fn=eden_cmds.pergame_path, proc=_PROC)
