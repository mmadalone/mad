r"""citron_addons.* - Citron (Switch) per-game Add-Ons enable/disable.

Thin shim: format docs + all logic live in the shared yuzu_addons engine. _FILE/_LOAD are
module globals read per call through the engine's getters (tests redirect them).
"""
from __future__ import annotations

from pathlib import Path

from . import yuzu_addons

_FILE = Path.home() / ".config/citron/qt-config.ini"
_LOAD = Path.home() / ".local/share/citron/load"
_PROC = "citron"    # read ONCE at engine construction below - repointing it later is inert

_impl = yuzu_addons.AddonsEngine(
    ns="citron_addons", display="Citron",
    file_getter=lambda: _FILE, load_getter=lambda: _LOAD,
    load_hint="~/.local/share/citron/load", proc=_PROC)

# Pure helpers re-exported for the tests (same names + signatures as pre-fold).
_parse = yuzu_addons._parse
_serialize = yuzu_addons._serialize


def _available(hex_tid: str):
    return yuzu_addons._available(_LOAD, hex_tid)      # _LOAD read per call


def has_content(hex_tid: str) -> bool:
    """True if this title has any add-on to manage (used to hide the empty Add-Ons tile)."""
    return _impl.has_content(hex_tid)
