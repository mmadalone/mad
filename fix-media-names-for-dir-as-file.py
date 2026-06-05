#!/usr/bin/env python3
"""
ES-DE 3.4 USERGUIDE.md (line 3614) — for "directories interpreted as files"
the directory's extension is INCLUDED in the expected media filenames.

After reorganizing CD games into folders named like "Game.cue/" the existing
media files are named "Game.png" — ES-DE looks for "Game.cue.png".

This script renames media files to add the dir-as-file extension.

For each game folder in a system, if it has a recognized dir-as-file extension
(.cue, .m3u, .iso, .chd, etc), and corresponding media files exist with the
plain stem name (e.g., "Game.png"), rename them to include the extension
(e.g., "Game.cue.png").
"""
import argparse
import os
import sys

ROMS_ROOT = "/home/deck/ROMs"
MEDIA_ROOT = "/run/media/deck/1tbDeck/downloaded_media"
MEDIA_SUBDIRS = [
    "3dboxes", "backcovers", "covers", "custom", "fanart", "manuals",
    "marquees", "marquee", "miximages", "physicalmedia", "screenmarquee",
    "screenshots", "screenshottitle", "titlescreens", "videos", "folders",
    "images",
]
DIR_AS_FILE_EXTS = {".cue", ".m3u", ".iso", ".chd", ".bin", ".img", ".gdi", ".ccd"}


def fix_system(system, apply):
    rom_dir = os.path.join(ROMS_ROOT, system)
    media_dir = os.path.join(MEDIA_ROOT, system)
    if not os.path.isdir(rom_dir) or not os.path.isdir(media_dir):
        return 0

    renamed = 0
    for entry in os.listdir(rom_dir):
        full = os.path.join(rom_dir, entry)
        if not os.path.isdir(full):
            continue
        # Is this folder a "dir-as-file"? Must end with a recognized ext.
        stem, ext = os.path.splitext(entry)
        if ext.lower() not in DIR_AS_FILE_EXTS:
            continue

        # For each media subdir, find files with the stem and rename
        for sub in MEDIA_SUBDIRS:
            sub_dir = os.path.join(media_dir, sub)
            if not os.path.isdir(sub_dir):
                continue
            for f in os.listdir(sub_dir):
                f_stem, f_ext = os.path.splitext(f)
                if f_stem == stem:
                    src = os.path.join(sub_dir, f)
                    dst = os.path.join(sub_dir, f"{entry}{f_ext}")
                    if os.path.exists(dst):
                        continue
                    if apply:
                        os.rename(src, dst)
                    print(f"  [{system}/{sub}] {f} → {entry}{f_ext}")
                    renamed += 1
    return renamed


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    p.add_argument("systems", nargs="*", help="systems (or empty for default list)")
    args = p.parse_args()
    systems = args.systems or [
        "3do", "amigacd32", "dreamcast", "mega-cd", "pcenginecd", "pcfx",
        "ps2", "psx", "saturn", "segacd", "x68000",
    ]
    total = 0
    for s in systems:
        n = fix_system(s, args.apply)
        if n:
            print(f"=== {s}: {n} media files {'renamed' if args.apply else 'would be renamed'} ===\n")
            total += n
    print(f"\nTotal: {total}")


if __name__ == "__main__":
    main()
