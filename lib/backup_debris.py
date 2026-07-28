"""Canonical OS / VCS debris predicate for backups (the ONE source of truth).

A backup should not carry macOS / Windows / VCS junk that is not part of the game, emulator, or config
data (the user saw .gitattributes / .DS_Store / ._* / Thumbs.db riding into backups). These names are NEVER
a legitimate emulator or game file, so dropping them on the BACKUP write path is safe and lossless.

DELIBERATELY does NOT include *.bak* / *.tmp / *.swp / *.orig: those CAN be legitimate game or config
filenames (a ROM, a save, an emulator config), so a blanket temp-glob filter would risk dropping real data.
Per-emulator temp excludes stay in emu_map's own `exclude` specs; this module is only the always-safe set.

Applied on the BACKUP path only - RESTORE stays byte-faithful (a debris file already inside an OLD backup is
reproduced exactly, never silently dropped; new backups are clean because the debris never gets in).

Used by:
  - granular_backup._copy_path / _path_size (folder copy + size)   -> skip_debris=True on the backup callers;
  - bios_map.list_buckets + emu_map._enum_spec                     -> debris never becomes a manifest row;
  - deck-cloud.sh (rclone --exclude) + deck-backup.sh (tar --exclude) build their patterns from exclude_globs()
    via `python3 -c "from lib import backup_debris ..."` so there is a single source (a drift test guards it).
"""
from __future__ import annotations

# Exact basenames, compared case-insensitively. AppleDouble "._<name>" files are matched by the "._" prefix.
_JUNK_FILES = frozenset({
    ".ds_store", "thumbs.db", "ehthumbs.db", "desktop.ini",
    ".gitattributes", ".gitignore", ".volumeicon.icns", ".apdisk",
})
# Whole directories to prune (their contents have arbitrary names, so a basename check would miss them).
_JUNK_DIRS = frozenset({
    ".git", "__pycache__", ".spotlight-v100", ".trashes", ".fseventsd",
    ".temporaryitems", ".documentrevisions-v100",
})


def is_debris_file(name: str) -> bool:
    """True if a file's basename is OS/VCS junk (drop it from a backup)."""
    low = name.lower()
    return low in _JUNK_FILES or low.startswith("._")


def is_debris_dir(name: str) -> bool:
    """True if a directory basename is OS/VCS junk (prune it whole from a backup walk)."""
    return name.lower() in _JUNK_DIRS


def exclude_globs() -> list:
    """rclone / tar --exclude glob patterns for the same debris set, anchored at the root AND at any depth.
    rclone/tar --exclude are case-sensitive, so the mixed-case names present in the wild are listed in both
    common cases. This is what the shell transports consume so they never drift from the Python predicate."""
    file_names = [".DS_Store", "Thumbs.db", "thumbs.db", "ehthumbs.db", "Desktop.ini", "desktop.ini",
                  ".gitattributes", ".gitignore", ".VolumeIcon.icns", ".apdisk", "._*"]
    dir_names = [".git", "__pycache__", ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
                 ".DocumentRevisions-V100"]
    pats: list = []
    for f in file_names:
        pats.append(f)               # at the root of the copied tree
        pats.append("**/" + f)       # at any depth
    for d in dir_names:
        pats.append(d + "/**")
        pats.append("**/" + d + "/**")
    return pats


if __name__ == "__main__":  # `python3 -m lib.backup_debris` -> one --exclude glob per line (for the shell)
    print("\n".join(exclude_globs()))
