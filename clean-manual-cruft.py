#!/usr/bin/env python3
"""
Tidy ES-DE 'manual' PDFs. A manual is matched by the ROM filename STEM (extension
stripped), so a manual is LIVE only if its stem equals a real game's stem (a
gamelist <path> stem or a primary ROM file stem). Everything else is handled:

  RECOVER  a wrong-named "Game.<romext>.pdf" whose game IS valid but has no
           correct "Game.pdf" twin  ->  renamed to "Game.pdf" (makes it work;
           typically multi-disc .m3u manuals Skyscraper skipped).
  MOVE     redundant wrong-named dups (correct twin already exists), per-(Track NN)
           files, and orphans of deleted games  ->  _TMP/<sys>/manuals-cruft/.

Default dry run; pass --apply to act. Moves are reversible (go to _TMP).
"""
import re, sys, shutil
from pathlib import Path

DM   = Path("/run/media/deck/1tbDeck/downloaded_media")
ROMS = Path("/home/deck/ROMs")
GL   = Path("/home/deck/ES-DE/gamelists")
TMP  = Path("/home/deck/Downloads/_TMP")
APPLY = "--apply" in sys.argv

TRACK_RE = re.compile(r"\(Track\s*\d+\)", re.I)
PRIMARY_EXT = {".zip",".7z",".cue",".chd",".iso",".cdi",".gdi",".m3u",".rvz",
               ".wbfs",".nsp",".xci",".wux",".wad",".pce",".sfc",".smc",".nes",
               ".gb",".gbc",".gba",".n64",".z64",".v64",".md",".gen",".sms",
               ".gg",".col",".a78",".lnx",".ngp",".ws",".wsc",".vb",".d64",
               ".hdm",".dim",".img",".ccd"}
ROMEXT_FOR_RECOVER = PRIMARY_EXT | {".bin"}  # the leftover-extension set we know how to strip

def gl_stems(s):
    f = GL / s / "gamelist.xml"
    if not f.is_file(): return set()
    t = f.read_text(encoding="utf-8", errors="replace")
    return {Path(m.group(1)).stem for m in re.finditer(r"<path>\s*\./?([^<]+?)\s*</path>", t)}

def rom_stems(s):
    d = ROMS / s
    if not d.is_dir(): return set()
    return {p.stem for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in PRIMARY_EXT and not TRACK_RE.search(p.name)}

rec_tot = mov_tot = live_tot = 0
rows = []
for sysdir in sorted(DM.iterdir()):
    mandir = sysdir / "manuals"
    if not mandir.is_dir(): continue
    s = sysdir.name
    valid = gl_stems(s) | rom_stems(s)
    pdfs = [p for p in mandir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    recover, move = [], []
    for p in pdfs:
        stem = p.name[:-4]
        if not TRACK_RE.search(stem) and stem in valid:
            continue  # LIVE
        # cruft — is it a recoverable wrong-extension manual?
        inner = Path(stem)
        if (not TRACK_RE.search(stem) and inner.suffix.lower() in ROMEXT_FOR_RECOVER
                and inner.stem in valid and not (mandir / f"{inner.stem}.pdf").exists()):
            recover.append((p, mandir / f"{inner.stem}.pdf"))
        else:
            move.append(p)
    live = len(pdfs) - len(recover) - len(move)
    live_tot += live; rec_tot += len(recover); mov_tot += len(move)
    if recover or move:
        rows.append((s, live, len(recover), len(move)))
        if APPLY:
            cruft = TMP / s / "manuals-cruft"
            for src, dst in recover:
                if dst.exists():       # a same-stem sibling already became this
                    cruft.mkdir(parents=True, exist_ok=True)   # Game.pdf this run —
                    with open(cruft / "_MANIFEST.txt", "a") as mf:  # route to _TMP,
                        shutil.move(str(src), str(cruft / src.name))  # never overwrite
                        mf.write(src.name + " (recover-collision)\n")
                else:
                    shutil.move(str(src), str(dst))
            if move:
                cruft.mkdir(parents=True, exist_ok=True)
                with open(cruft / "_MANIFEST.txt", "a") as mf:
                    for p in move:
                        shutil.move(str(p), str(cruft / p.name)); mf.write(p.name + "\n")

print(f"{'system':<14}{'live':>6}{'recover':>9}{'move':>7}")
print("-"*36)
for s, l, r, m in rows: print(f"{s:<14}{l:>6}{r:>9}{m:>7}")
print("-"*36)
print(f"{'TOTAL':<14}{live_tot:>6}{rec_tot:>9}{mov_tot:>7}")
print(f"\n{'APPLIED' if APPLY else 'DRY RUN — pass --apply'}  (recover=rename to working name, move=_TMP/<sys>/manuals-cruft/)")
