r"""eden_cheats.* - Eden (Switch) per-game cheat enable/disable.

Thin shim: format docs + all logic live in the shared yuzu_cheats engine (Eden shares the
Yuzu-fork qt-config.ini format AND the load/<TitleID-HEX>/<mod>/cheats/ layout). _FILE/
_LOAD are module globals read per call through the engine's getters (tests redirect them).
"""
from __future__ import annotations

from pathlib import Path

from . import yuzu_cheats

_FILE = Path.home() / ".config/eden/qt-config.ini"
_LOAD = Path.home() / ".local/share/eden/load"
_PROC = "eden"

_impl = yuzu_cheats.CheatsEngine(
    ns="eden_cheats", display="Eden",
    file_getter=lambda: _FILE, load_getter=lambda: _LOAD,
    load_hint="~/.local/share/eden/load", proc=_PROC)

# Pure helpers re-exported for the tests (same names + signatures as pre-fold).
_parse = yuzu_cheats._parse
_serialize = yuzu_cheats._serialize


def has_content(hex_tid: str) -> bool:
    """True if this title has any cheat (used to hide the empty per-game Cheats tile)."""
    return _impl.has_content(hex_tid)
