r"""citron_hk.* - Citron (Switch) Hotkeys remapper.

Thin shim: format docs + all logic live in the shared yuzu_hotkeys engine (the nested
[UI] Shortcuts store + flat-array read-only fallback were verified against
citron-neo/emulator's QtConfig::ReadShortcutValues path). _FILE is a module global read
per call through the engine's getter (tests redirect it); _buf is THIS fork's own staged-
edit buffer (tests reset it per case).
"""
from __future__ import annotations

from pathlib import Path

from . import yuzu_hotkeys

_FILE = Path.home() / ".config/citron/qt-config.ini"
_PROC = "citron"    # read ONCE at engine construction below - repointing it later is inert

_impl = yuzu_hotkeys.HotkeysEngine(
    ns="citron_hk", display="Citron", an="a Citron",
    file_getter=lambda: _FILE, proc=_PROC)
_buf = _impl.buf
