r"""eden_hk.* - Eden (Switch) Hotkeys remapper.

Thin shim: format docs + all logic live in the shared yuzu_hotkeys engine (Eden shares
the Yuzu-fork nested [UI] Shortcuts store + the flat-array read-only fallback). _FILE is
a module global read per call through the engine's getter (tests redirect it); _buf is
THIS fork's own staged-edit buffer (tests reset it per case).
"""
from __future__ import annotations

from pathlib import Path

from . import yuzu_hotkeys

_FILE = Path.home() / ".config/eden/qt-config.ini"
_PROC = "eden"

_impl = yuzu_hotkeys.HotkeysEngine(
    ns="eden_hk", display="Eden", an="an Eden",
    file_getter=lambda: _FILE, proc=_PROC)
_buf = _impl.buf
