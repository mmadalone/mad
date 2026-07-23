#!/usr/bin/env python3
"""
Generate the ES-DE `steam` system: one `.sh` launcher per game (run via Steam so
each game's configured Proton version / prefix / launch options apply) + a
gamelist.xml. Curated to the user's pixel-art Steam titles + a set of non-Steam
PC games. Re-runnable (idempotent).

Mechanism: ES-DE's bundled `steam` system runs `%ROMPATH%/steam/<rom>.sh` in the
background. Each .sh execs `steam steam://rungameid/<id>`:
  - Steam game     -> id = appid
  - non-Steam game -> id = (shortcut_appid & 0xffffffff) << 32 | 0x02000000
Launching via steam:// means we never hand-encode Proton (the user's concern).
"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.proc_guard import abort_if_esde_running  # noqa: E402
from lib import fsutil  # noqa: E402

HOME = Path.home()
STEAM = HOME / ".steam" / "steam"
ROMS = (HOME / "ROMs" / "steam")               # ~/ROMs is a symlink to the SD card
GAMELIST = HOME / "ES-DE" / "gamelists" / "steam" / "gamelist.xml"
SHORTCUTS = sorted(STEAM.glob("userdata/*/config/shortcuts.vdf"))

# ── curated Steam pixel-art titles (exact appmanifest names) ───────────────
STEAM_PIXEL = [
    "Terror of Hemasaurus", "Castlevania Anniversary Collection",
    "Castlevania Advance Collection", "Castlevania Dominus Collection",
    "Astral Ascent", "Risk of Rain Returns",
    "Teenage Mutant Ninja Turtles: Shredder's Revenge", "80's OVERDRIVE",
    "Lovecraft's Untold Stories", "Lovecraft's Untold Stories 2", "Seablip",
    "Fading Afternoon", "Terminator 2D: NO FATE", "Only Lead Can Stop Them",
    "FUMES", "Double Dragon Gaiden: Rise of the Dragons", "SONOKUNI",
    "Blasphemous 2", "Volgarr the Viking II", "Abathor", "Forestrike",
    "Kill The Crows", "Huntdown", "Huntdown: Overtime",
    "NINJA GAIDEN: Ragebound", "Shadow of the Ninja - Reborn",
    "The Karate Kid: Street Rumble", "Intravenous 2", "BLADECHIMERA",
    "Brigador: Up-Armored Edition", "Westerado: Double Barreled",
    "MARVEL Cosmic Invasion", "Vindefiant", "Katanaut",
    "Death's Gambit: Afterlife", "Lucius Demake", "Police Stories",
    "Valfaris", "Door Kickers: Action Squad", "Slipstream",
    "The friends of Ringo Ishikawa", "Project Warlock", "Colt Canyon",
    "Death Trash", "CARRION",
    # Added 2026-06-11 (user request, reinstalled):
    "River City Girls", "River City Girls 2", "River City Girls Zero",
    "River City Ransom: Underground", "Retro City Rampage™ DX",
    "Shakedown: Hawaii",
    # 3D TMNT games (NOT pixel-art) — included only so they join the tmnt collection.
    "Teenage Mutant Ninja Turtles: Mutants Unleashed",
    "Teenage Mutant Ninja Turtles: Splintered Fate",
    # 3D, user-requested 2026-06-11 (NOT pixel-art).
    "SAMURAI SHODOWN",
    # Added 2026-07-01 (user request):
    "Space Adventure Cobra - The Awakening", "Double Dragon Neon",
]

# ── curated non-Steam games (exact shortcut appnames) ──────────────────────
NONSTEAM = [
    "OutRun 2006 Coast 2 Coast", "The Punisher", "Deadpool",
    "Spider-Man: Friend or Foe", "Spider-Man: Web of Shadows",
    "Transformers: Fall of Cybertron", "Transformers: Devastation",
    "Transformers: War for Cybertron", "Ultimate Spider-Man",
    "Manhunt", "Manhunt 2", "Spider-Man: Shattered Dimensions",
    "TMNT - Mutants in Manhattan",
]


def library_dirs():
    dirs = [STEAM]
    lf = STEAM / "steamapps" / "libraryfolders.vdf"
    if lf.is_file():
        for m in re.finditer(r'"path"\s*"([^"]+)"', lf.read_text(errors="replace")):
            dirs.append(Path(m.group(1)))
    return dirs


def installed_steam_appids():
    """name -> appid for every installed Steam app, across all libraries."""
    out = {}
    for base in library_dirs():
        d = base / "steamapps"
        if not d.is_dir():
            continue
        for acf in d.glob("appmanifest_*.acf"):
            t = acf.read_text(errors="replace")
            a = re.search(r'"appid"\s*"(\d+)"', t)
            n = re.search(r'"name"\s*"([^"]*)"', t)
            if a and n:
                out[n.group(1)] = int(a.group(1))
    return out


def _vdf_cstr(data, pos):
    """Read a NUL-terminated field from a binary VDF blob; return (bytes, next_pos)."""
    end = data.index(b'\x00', pos)
    return data[pos:end], end + 1


def _vdf_parse_map(data, pos):
    """Parse a binary-VDF map body starting at pos (just past the map's own key).

    Returns (entries, pos_after_terminator); entries is a list of
    (key_lowercased_bytes, type_byte, value). Type-aware: int32/int64 are read as
    fixed-width values, NOT scanned for a delimiter -- a shortcut appid legitimately
    contains \x00 and \x08 bytes that a byte-scan would misread as field/map ends.
    """
    entries = []
    n = len(data)
    while pos < n:
        t = data[pos]
        pos += 1
        if t == 0x08:                                  # end of this map
            return entries, pos
        key, pos = _vdf_cstr(data, pos)
        kl = key.lower()
        if t == 0x00:                                  # nested map (recurse)
            sub, pos = _vdf_parse_map(data, pos)
            entries.append((kl, t, sub))
        elif t == 0x01:                                # string (NUL-terminated)
            val, pos = _vdf_cstr(data, pos)
            entries.append((kl, t, val))
        elif t == 0x02:                                # int32
            val = int.from_bytes(data[pos:pos + 4], "little", signed=True)
            pos += 4
            entries.append((kl, t, val))
        elif t == 0x07:                                # uint64
            val = int.from_bytes(data[pos:pos + 8], "little", signed=False)
            pos += 8
            entries.append((kl, t, val))
        else:
            raise ValueError("unknown binary-VDF type %#x at offset %d" % (t, pos - 1))
    return entries, pos


def nonsteam_rungameids():
    """appname -> steam rungameid for every non-Steam shortcut.

    Parses shortcuts.vdf STRUCTURALLY so each shortcut's appid and appname come from the
    SAME entry-block. The old code scanned appids and appnames as two independent passes
    and paired them by position with zip(); if any block had an appid but no lowercase
    'appname' match (Steam's key casing has varied -- 'appname' vs 'AppName' -- and a
    nameless block is possible), zip truncated and every later pair shifted, so a launcher
    .sh got a DIFFERENT game's rungameid and Steam booted the wrong game. Per-block pairing
    plus a case-insensitive key match cannot shift.
    """
    out = {}
    if not SHORTCUTS:
        return out
    data = SHORTCUTS[0].read_bytes()
    try:
        root, _ = _vdf_parse_map(data, 0)
    except (ValueError, IndexError):
        return out                                     # malformed vdf: emit nothing, never a wrong game
    shortcuts = next((v for k, t, v in root if k == b'shortcuts' and t == 0x00), [])
    for _k, t, block in shortcuts:
        if t != 0x00:
            continue
        appid = name = None
        for bk, bt, bv in block:
            if bk == b'appid' and bt == 0x02:
                appid = bv
            elif bk == b'appname' and bt == 0x01:
                name = bv.decode("utf-8", "replace")
        if appid is None or name is None:
            continue
        out[name] = ((appid & 0xFFFFFFFF) << 32) | 0x02000000
    return out


def safe_filename(name: str) -> str:
    """Filesystem-safe stem (works on exFAT too); real name comes from gamelist."""
    s = re.sub(r"[^A-Za-z0-9 ._-]+", "", name).strip()
    return re.sub(r"\s+", " ", s) or "game"


SH_TEMPLATE = ("#!/bin/sh\n"
               "# {name}\n"
               "# Launched via Steam so its configured Proton / prefix / launch "
               "options apply.\n"
               "exec steam steam://rungameid/{rgid}\n")


def main():
    if abort_if_esde_running("regenerate the Steam collection gamelist"):
        return
    steam_ids = installed_steam_appids()
    ns_ids = nonsteam_rungameids()
    ROMS.mkdir(parents=True, exist_ok=True)
    GAMELIST.parent.mkdir(parents=True, exist_ok=True)

    entries = []        # (display_name, stem, source)
    missing = []
    used = set()

    def add(name, rgid, source):
        stem = safe_filename(name)
        # de-dup stems
        base = stem
        n = 2
        while stem in used:
            stem = f"{base} ({n})"; n += 1
        used.add(stem)
        sh = ROMS / f"{stem}.sh"
        sh.write_text(SH_TEMPLATE.format(name=name, rgid=rgid))
        sh.chmod(0o755)
        entries.append((name, stem, source))

    for nm in STEAM_PIXEL:
        if nm in steam_ids:
            add(nm, steam_ids[nm], "steam")
        else:
            missing.append(("steam", nm))
    for nm in NONSTEAM:
        if nm in ns_ids:
            add(nm, ns_ids[nm], "nonsteam")
        else:
            missing.append(("nonsteam", nm))

    # gamelist.xml
    lines = ['<?xml version="1.0"?>', "<gameList>"]
    for name, stem, source in sorted(entries, key=lambda e: e[0].lower()):
        lines.append("\t<game>")
        lines.append(f"\t\t<path>./{escape(stem)}.sh</path>")
        # Display "TMNT" rather than the long Steam name (matches the tmnt collection).
        disp = name.replace("Teenage Mutant Ninja Turtles", "TMNT")
        lines.append(f"\t\t<name>{escape(disp)}</name>")
        lines.append("\t</game>")
    lines.append("</gameList>")
    fsutil.atomic_write(GAMELIST, "\n".join(lines) + "\n")

    print(f"ROM dir : {ROMS}  ({'symlink → '+str(ROMS.resolve()) if ROMS.is_symlink() or ROMS.resolve()!=ROMS else ROMS})")
    print(f"gamelist: {GAMELIST}")
    print(f"wrote {len(entries)} launchers "
          f"({sum(1 for e in entries if e[2]=='steam')} Steam, "
          f"{sum(1 for e in entries if e[2]=='nonsteam')} non-Steam)")
    if missing:
        print("\nNOT FOUND (skipped — name mismatch or not installed):")
        for src, nm in missing:
            print(f"  [{src}] {nm}")


if __name__ == "__main__":
    main()
