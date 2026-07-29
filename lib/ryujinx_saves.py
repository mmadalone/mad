"""Ryujinx per-game save resolver - map a Switch title-id to its opaque SaveDataId folder.

Ryujinx stores each game's save under bis/user/save/<save-index>/ where <save-index> is a sequential
SaveDataId (0000000000000001, 0000000000000002, ...), NOT the title-id; a separate SaveDataIndexer imkvdb
(bis/system/save/8000000000000000/) owns the mapping. Rather than parse that binary db, we read the
title-id straight out of each save: ExtraData0 (in the save dir) begins with the u64 ProgramId (title-id)
LITTLE-ENDIAN at offset 0 (Horizon SaveDataExtraData layout). So {title-id -> save-index} is reconstructable
by reading 8 bytes per index.

Read-only, best-effort. Note (documented limitation): the save dir + its saveMeta sibling round-trip on the
SAME device (the indexer already maps them); a CROSS-device restore of an opaque Ryujinx save also needs the
global SaveDataIndexer db, which is shared across all games and so is not a per-game asset - the same limit
the wholesale emu_map "saves" group has.
"""
from __future__ import annotations

import struct
from pathlib import Path

# Resolved at CALL time (not import) so a patched Path.home() / a relocated $HOME is honored.
_SAVE_REL = ".config/Ryujinx/bis/user/save"
_META_REL = ".config/Ryujinx/bis/user/saveMeta"


def _save_base() -> Path:
    return Path.home() / _SAVE_REL


def _meta_base() -> Path:
    return Path.home() / _META_REL


def _title_of(index_dir: Path) -> str | None:
    """The 16-hex title-id stored in <index_dir>/ExtraData0 (u64 LE ProgramId at offset 0), or None when the
    file is missing/short or the ProgramId is zero (a system/temporary save with no title)."""
    try:
        with (index_dir / "ExtraData0").open("rb") as fh:
            raw = fh.read(8)
    except OSError:
        return None
    if len(raw) != 8:
        return None
    (pid,) = struct.unpack("<Q", raw)
    if pid == 0:
        return None
    return f"{pid:016x}"


def title_to_index(base: Path | None = None) -> dict:
    """{title-id (lower 16-hex) -> save-index dir name} for every Ryujinx save on disk. Best-effort: an index
    whose ExtraData0 is missing/zero is skipped. A title with two save-indices (rare) keeps the first seen."""
    root = base if base is not None else _save_base()
    out: dict = {}
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in entries:
        tid = _title_of(d)
        if tid and tid not in out:
            out[tid] = d.name
    return out


def save_paths(titleid: str, base: Path | None = None, meta: Path | None = None) -> list:
    """The live save dir + its saveMeta sibling (both FOLDERS) for one title-id, or [] if not found. The
    caller backs these up as folder-kind rows under the emucfg category."""
    root = base if base is not None else _save_base()
    metaroot = meta if meta is not None else _meta_base()
    idx = title_to_index(root).get((titleid or "").lower())
    if not idx:
        return []
    out: list = []
    sd = root / idx
    if sd.is_dir():
        out.append(sd)
    md = metaroot / idx
    if md.is_dir():
        out.append(md)
    return out
