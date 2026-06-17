"""Resolve MAD's mutable DATA root — the EmuDeck "Emulation" subtree where MAD
keeps its own state (storage/, and the emulator roms/saves/bios trees it reads).

This is what lets MAD run on a non-EmuDeck folder layout: every tool calls these
accessors instead of hardcoding ~/Emulation/... . With nothing overridden the
result is byte-identical to the historical hardcoded paths, so existing EmuDeck
installs are unaffected.

Resolution order (first that applies wins):
  1. $MAD_DATA_ROOT                  — explicit override
  2. $storagePath's parent           — follow a relocated EmuDeck storage, if
                                       EmuDeck exported storagePath into the env
  3. ~/Emulation                     — the standard default (unchanged)

NOT the MAD *install* dir (~/Emulation/tools/launchers) — Python self-locates via
lib/policy.py's Path(__file__) pattern; shell uses $MAD_HOME. NOT ES-DE's
~/ROMs / ~/ES-DE — those decouple via es_collections.rom_root() /
esde_settings.APPDATA and must NOT be routed through here.

Stdlib only (no pip). Twin of lib/mad-paths.sh for shell callers.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path


@functools.lru_cache(maxsize=1)
def data_root() -> Path:
    """The mutable data root (default ~/Emulation). Cached — tests that vary the
    environment must call data_root.cache_clear() between permutations."""
    env = os.environ.get("MAD_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    sp = os.environ.get("storagePath")        # EmuDeck exports this (<root>/storage)
    if sp:
        return Path(sp).expanduser().parent
    return Path.home() / "Emulation"


def storage_root() -> Path:
    """<root>/storage — controller-router, sinden, control-panel, openbor, …"""
    return data_root() / "storage"


def storage(*parts: str) -> Path:
    """storage('controller-router') -> <root>/storage/controller-router"""
    return storage_root().joinpath(*parts)


def roms_root() -> Path:
    """<root>/roms — the EmuDeck rom tree (NOT ES-DE's ~/ROMs)."""
    return data_root() / "roms"


def tools_root() -> Path:
    """<root>/tools"""
    return data_root() / "tools"


def saves_root() -> Path:
    """<root>/saves"""
    return data_root() / "saves"


def bios_root() -> Path:
    """<root>/bios"""
    return data_root() / "bios"


if __name__ == "__main__":   # quick manual check
    for fn in (data_root, storage_root, roms_root, tools_root, saves_root, bios_root):
        print(f"{fn.__name__:14s} {fn()}")
