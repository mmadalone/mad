#!/usr/bin/env python3
"""
Reorganize CD/multi-disc games into per-game folders, per ES-DE docs.

ES-DE supports "directories interpreted as files" — if a folder name matches
a known game extension (e.g., "Shenmue.m3u/"), ES-DE shows it as one entry
and launches the file inside matching the folder name. See ES-DE 3.4
USERGUIDE.md lines 1289 and 1295.

For each system folder:
  - find .m3u files; parse to get referenced disc files;
    create folder "<m3u>/" and move m3u + all referenced + related audio
    track files inside.
  - find .cue files (not already inside an m3u group); parse to get
    referenced .bin / .iso / .ogg / .wav tracks; create "<cue>/" folder
    and move into it.

Usage:
  reorganize-cd-games.py --dry-run <system>     # plan only
  reorganize-cd-games.py --apply  <system>      # actually move files
  reorganize-cd-games.py --apply  --all         # all multi-disc systems

Safe behavior:
  - Skips files already inside a subfolder
  - Skips if target folder already exists
  - Prints every move; --dry-run does not touch the disk
"""
import argparse
import os
import re
import shutil
import sys

ROMS_ROOT = "/home/deck/ROMs"

# Systems known to have multi-disc / multi-track CD content
DEFAULT_SYSTEMS = [
    "3do", "amigacd32", "dreamcast", "mega-cd", "pcenginecd", "pcfx",
    "ps2", "psx", "saturn", "segacd", "x68000",
]

# File extensions that ES-DE recognizes as launchable + companion audio tracks
GAME_EXTS = {".m3u", ".cue", ".chd", ".iso", ".bin", ".img", ".gdi", ".ccd"}
TRACK_EXTS = {".ogg", ".wav", ".sub", ".toc"}


def parse_m3u(path):
    """Return list of files referenced by the m3u (just the filenames)."""
    refs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # m3u entries can have paths, but they're usually just filenames
                refs.append(os.path.basename(line))
    except OSError:
        pass
    return refs


CUE_FILE_RE = re.compile(r'^\s*FILE\s+"([^"]+)"', re.IGNORECASE)


def parse_cue(path):
    """Return list of files referenced by the cue sheet."""
    refs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = CUE_FILE_RE.match(line)
                if m:
                    refs.append(os.path.basename(m.group(1)))
    except OSError:
        pass
    return refs


def companion_files(system_dir, basename):
    """Find files in system_dir matching basename* prefix (e.g., 'Game.cue', 'Game.bin', 'Game (Track 1).bin')."""
    matches = []
    for name in os.listdir(system_dir):
        full = os.path.join(system_dir, name)
        if not os.path.isfile(full):
            continue
        # match exactly basename.* or 'basename (Track N)' etc
        stem, ext = os.path.splitext(name)
        if stem == basename or stem.startswith(basename + " ") or stem.startswith(basename + "."):
            matches.append(name)
    return matches


def plan_for_system(system_dir):
    """Yield (folder_name, [files_to_move]) tuples."""
    if not os.path.isdir(system_dir):
        return

    # files already inside subfolders — skip
    root_entries = [e for e in os.listdir(system_dir) if os.path.isfile(os.path.join(system_dir, e))]

    used = set()

    # Trust the m3u/cue file contents exclusively — no prefix-matching of
    # companion files, since prefixes like "Shenmue" would match "Shenmue II".

    # Pass 1: m3u groups. m3u may reference .cue files, which in turn reference .bin tracks.
    for name in sorted(root_entries):
        if not name.lower().endswith(".m3u"):
            continue
        full = os.path.join(system_dir, name)
        related = {name}
        related.update(parse_m3u(full))
        # Recurse: if m3u references a .cue, also pull in that cue's tracks
        for ref in list(related):
            ref_path = os.path.join(system_dir, ref)
            if ref.lower().endswith(".cue") and os.path.isfile(ref_path):
                related.update(parse_cue(ref_path))
        files = sorted(f for f in related if os.path.isfile(os.path.join(system_dir, f)))
        if files:
            yield name, files
            used.update(files)

    # Pass 2: cue groups (cue files NOT already absorbed by an m3u)
    for name in sorted(root_entries):
        if not name.lower().endswith(".cue"):
            continue
        if name in used:
            continue
        full = os.path.join(system_dir, name)
        related = {name}
        related.update(parse_cue(full))
        files = sorted(f for f in related if os.path.isfile(os.path.join(system_dir, f)) and f not in used)
        if files:
            yield name, files
            used.update(files)


def execute_plan(system_dir, plan, apply):
    moved = 0
    skipped = 0
    for folder_name, files in plan:
        target = os.path.join(system_dir, folder_name)
        if os.path.isdir(target):
            print(f"  SKIP folder exists: {folder_name}/")
            skipped += 1
            continue
        # If the same name exists as a file (the .cue/.m3u itself), we need
        # to move it aside first, create the dir, then drop it back in.
        if os.path.isfile(target):
            tmp_target = target + ".__moving"
            if not apply:
                print(f"  WOULD rename {folder_name} → {folder_name}.__moving (temp) then MKDIR + MOVE ({len(files)} files)")
                continue
            os.rename(target, tmp_target)
            os.makedirs(target)
            os.rename(tmp_target, os.path.join(target, folder_name))
            files = [f for f in files if f != folder_name]
            moved += 1
        action = "MKDIR + MOVE" if apply else "WOULD MKDIR + MOVE"
        print(f"  {action}: {folder_name}/  ({len(files)} files)")
        if apply:
            os.makedirs(target, exist_ok=True)
            for f in files:
                src = os.path.join(system_dir, f)
                dst = os.path.join(target, f)
                if os.path.exists(dst):
                    print(f"    SKIP (exists): {f}")
                    continue
                shutil.move(src, dst)
                moved += 1
    return moved, skipped


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    p.add_argument("system", nargs="?", help="system name (or --all)")
    p.add_argument("--all", action="store_true", help="all default multi-disc systems")
    args = p.parse_args()

    if args.all:
        systems = DEFAULT_SYSTEMS
    elif args.system:
        systems = [args.system]
    else:
        sys.exit("specify a system or --all")

    total_moves = 0
    for sys_name in systems:
        sys_dir = os.path.join(ROMS_ROOT, sys_name)
        if not os.path.isdir(sys_dir):
            continue
        plan = list(plan_for_system(sys_dir))
        if not plan:
            continue
        print(f"\n=== {sys_name} ({len(plan)} groups) ===")
        moved, skipped = execute_plan(sys_dir, plan, args.apply)
        total_moves += moved
    if args.apply:
        print(f"\nTotal files moved: {total_moves}")


if __name__ == "__main__":
    main()
