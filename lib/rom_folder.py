"""rom_folder -- "which games are actually in this system's ROM folder right now",
independent of ES-DE's gamelist (a game can be scraped-but-deleted, or present-but-
unscraped) and independent of any emulator's own cache (which can go stale after a
library move, see lib/madsrv/pcsx2_games.py:games()'s doc comment). This is a live
filesystem read, not a database -- it exists so callers can cross-check "ES-DE
thinks I have this" against "the folder actually has this" (e.g. a backup manifest
sanity check).

TOP LEVEL ONLY: ES-DE's rom dirs are full of per-core subfolders and .srm/.state
save litter that must never be mistaken for a game, so this never recurses.

Stdlib only (no pip).
"""
from __future__ import annotations

from pathlib import Path

from . import es_collections
from . import es_systems


def rom_dir(system: str) -> Path:
    """The on-disk ROM folder for one ES-DE system (<ES-DE ROM root>/<system>).
    Reuses es_collections.rom_root() -- do not re-derive ES-DE's ROMDirectory here."""
    return es_collections.rom_root() / system


def _scan(system: str) -> tuple[dict[str, dict], list[str]]:
    """One live top-level scan of rom_dir(system), returning (entries, skipped_stems).
    Shared by entries()/skipped() so both read the same walk instead of drifting."""
    exts = es_systems.extensions(system)
    if not exts:
        return {}, []                      # folder truth disabled for this system -- never guess
    d = rom_dir(system)
    try:
        # sorted() forces the whole iterator now, inside the try, so a permission error
        # raised partway through iteration is caught here too, not just on the opendir() call.
        children = sorted(d.iterdir(), key=lambda p: p.name)
    except OSError:
        return {}, []                      # missing/unreadable dir -- never raise, never guess
    out: dict[str, dict] = {}
    skipped: list[str] = []
    for p in children:
        stem: str | None = None
        kind: str | None = None
        try:
            if p.is_file():
                suf = p.suffix.lower()
                if suf in exts:
                    stem = p.stem
                    kind = "file"
            elif p.is_dir():
                # "dir as file" layout (ES-DE supports a game AS a directory, e.g. a PS3
                # folder named "Game.ps3"): match the extension against the DIRECTORY NAME,
                # not Path.stem, and strip exactly the matched extension's length -- so a
                # folder whose name has its own unrelated dots is not mistakenly truncated
                # any further than the one trailing system extension.
                low = p.name.lower()
                for ext in exts:               # exts is first-seen order -- deterministic pick
                    if low.endswith(ext):
                        stem = p.name[:-len(ext)]
                        kind = "folder"
                        break
        except OSError:
            continue                           # a broken entry (e.g. dangling symlink) -- skip it
        if stem is None:
            continue                           # not a recognised rom (e.g. metadata.txt, .srm litter)
        stem_lower = stem.lower()
        # A ':' in the stem would corrupt the "system:stem" keying the backup manifest uses,
        # so route it to `skipped` instead of `out` -- discoverable, never silently dropped.
        if ":" in stem_lower:
            skipped.append(stem)
            continue
        if stem_lower in out:
            continue                           # FIRST WINS (children is sorted -> deterministic)
        out[stem_lower] = {"stem": stem, "path": str(p), "kind": kind}
    return out, skipped


def entries(system: str) -> dict[str, dict]:
    """{stem_lower: {"stem": str, "path": str, "kind": "file"|"folder"}} for every rom-like
    entry directly inside rom_dir(system), filtered by es_systems.extensions(system) (case
    insensitively). `stem_lower` matches how es_gamelist.py keys its maps (Path(path).stem,
    lowercased), so the two can be merged by the caller. Not cached: this is the live truth,
    so re-scans the directory on every call; callers cache if they want to."""
    return _scan(system)[0]


def skipped(system: str) -> list[str]:
    """Stems that were found but excluded because they contain ':' (would corrupt the
    "system:stem" backup-manifest key). Same live, uncached scan as entries()."""
    return _scan(system)[1]
