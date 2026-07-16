"""The OpenBOR game list, read from the .openbor manifests ES-DE launches.

One place to answer "which OpenBOR games exist, and what is each one's key?".
Three callers used to re-derive this: openbor.sh parses `DIR=` inline, and
openbor-gen-{manifests,gamelist}.py each hardcode
`/run/media/deck/1tbDeck/ROMs/openbor` — which lands on the right files only by
symlink luck (`~/ROMs` -> the SD card, then `ROMs/openbor` -> `/home/deck/OpenBor`,
all one inode) and breaks the day ROMDirectory is set or the card is relabelled.
Resolve it the way the rest of MAD does instead: es_collections.rom_root().

Manifests, not folders: there are 35 game folders and 33 manifests. MIWv100.old
and Maximun_Carnage_Returns are deliberately not in ES-DE, and a folder scan
would offer two games that cannot be launched.
"""
from __future__ import annotations

import functools
from pathlib import Path

from . import es_collections, es_gamelist

SYSTEM = "openbor"


def rom_dir() -> Path:
    """The directory ES-DE scans for .openbor manifests."""
    return es_collections.rom_root() / SYSTEM


def _dir_key(manifest: Path) -> str:
    """The manifest's DIR= value — the key openbor.sh passes to openbor_cfg, and
    therefore the key the input-map store is keyed by. Falls back to the stem,
    which matches DIR for all 33 today, but the contract is the DIR field."""
    try:
        for line in manifest.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("DIR="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return manifest.stem


@functools.lru_cache(maxsize=1)
def _scan() -> tuple[tuple[str, str], ...]:
    d = rom_dir()
    if not d.is_dir():
        return ()
    return tuple(sorted((_dir_key(m), m.stem) for m in d.glob("*.openbor")))


def dir_keys() -> list[str]:
    """Every launchable game's DIR key, sorted."""
    return [k for k, _stem in _scan()]


def names() -> dict:
    """{dir_key: display name} — the ES-DE gamelist name, else the manifest stem."""
    titles = es_gamelist.titles(SYSTEM)
    return {k: titles.get(stem.lower(), stem) for k, stem in _scan()}
