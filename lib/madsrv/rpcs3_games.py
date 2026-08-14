"""rpcs3_games — headless PS3 game list + serial resolver for RPCS3.

RPCS3 writes its own games.yml (``<SERIAL>: <ROM path>``) after scanning the library, and
names per-game override files ``custom_configs/config_<SERIAL>.yml`` by that SAME serial —
so a key built here maps 1:1 onto the file RPCS3 reads. Pure helpers (no RPC). A friendly
title is derived from the ROM path basename (region/lang/serial tags stripped). Any failure
degrades to [] rather than raising.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

try:
    import yaml
except ImportError:                    # PyYAML missing -> no per-game game list
    yaml = None

_GAMES_YML = Path.home() / ".config/rpcs3/games.yml"
_SERIAL_PAT = r"[A-Z]{4}[0-9]{5}"                  # BLES00590 / NPEA00362 -- ONE definition
_SERIAL_RE = re.compile(rf"^{_SERIAL_PAT}\Z")      # (\Z: no trailing newline)

# A title INSTALLED to RPCS3's virtual hard drive lives at <datadir>/dev_hdd0/game/<SERIAL>/, and
# the ES-DE shortcut launches its .../USRDIR/EBOOT.BIN directly. Anchoring on the `dev_hdd0/game/`
# parent is what makes this safe: `game/` holds real serial-shaped directories that are NOT games
# (verified live on this Deck: a leftover `TEST12345`, which matches the serial shape exactly, and
# `NPEA00362GAMEDATA`, which does not). We only ever read the component we were pointed AT, never
# scan the directory, so a decoy sitting beside the real title can never be picked.
_HDD_GAME_RE = re.compile(rf"(?:^|/)dev_hdd0/game/({_SERIAL_PAT})(?:/|$)")
_BRACKET_RE = re.compile(rf"\[({_SERIAL_PAT})\]")  # "Asura's Wrath [BLUS30721]/" dir-style games
_PS3_EXTS = {".desktop", ".ps3"}                    # ES-DE ps3 system extensions (case-insensitive)


def is_serial(s: str) -> bool:
    return bool(_SERIAL_RE.match(s or ""))


def _ps3_rom_dir() -> Path:
    from . import dolphin_games
    return dolphin_games._rom_root() / "ps3"


def _esde_ps3_roms() -> list[Path]:
    """Top-level ES-DE ps3 ROM files (the .desktop shortcuts / .ps3 files ES-DE shows), sorted."""
    try:
        return sorted(p for p in _ps3_rom_dir().iterdir()
                      if p.is_file() and p.suffix.lower() in _PS3_EXTS)
    except OSError:
        return []


def stem_of(path: str) -> str:
    """The ES-DE FileData stem (basename minus extension) so the media browser resolves
    this game's art; the folder NAME (kept whole, dots included) for a dir-style
    ('...[SERIAL]/') entry."""
    if not path:
        return ""
    if path.endswith("/"):                 # dir-style entry -> the folder name (no .stem dot-split)
        return Path(path.rstrip("/")).name
    return Path(path).stem


def games() -> list[dict]:
    """The user's ES-DE ps3 games (the .desktop shortcuts ES-DE actually shows) mapped to their
    RPCS3 serial. [{key: SERIAL, name, stem, path}], sorted by name. `stem` is the ES-DE FileData
    stem (the .desktop filename) so the per-game media browser resolves covers -- ES-DE files PS3
    media under the SHORTCUT name, not RPCS3's disc name. A shortcut with no games.yml serial
    (RPCS3 hasn't registered its disc) is dropped: no per-game config is possible for it."""
    from .. import es_gamelist
    roms = _esde_ps3_roms()
    if not roms:
        return []
    names = es_gamelist.titles("ps3")                 # {stem.lower(): name}
    out, seen = [], set()
    for p in roms:
        serial = path_to_serial(str(p))
        if not serial or serial in seen:              # unregistered disc, or a dup pointing at one disc
            continue
        seen.add(serial)
        stem = p.stem
        out.append({"key": serial, "name": names.get(stem.lower()) or stem, "stem": stem,
                    "path": str(p)})
    out.sort(key=lambda g: g["name"].lower())
    return out


_EXEC_QUOTED_RE = re.compile(r'(?m)^Exec=[^\n]*?"([^"]+)"')


def _desktop_disc_path(desktop: str) -> str | None:
    """The disc / EBOOT path an ES-DE .desktop shortcut launches (its Exec= quoted argument),
    with %%->% de-escaping (mirrors rpcs3.sh). None if unreadable or the Exec arg is unquoted."""
    try:
        text = Path(desktop).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _EXEC_QUOTED_RE.search(text)
    return m.group(1).replace("%%", "%") if m else None


def _serial_from_path(disc: str) -> str | None:
    """The RPCS3 serial carried by the disc PATH ITSELF, for titles games.yml does not register.

    WHY THIS EXISTS. games.yml lists games RPCS3 was pointed at as a disc or a folder. A title
    INSTALLED to the virtual hard drive (a PSN download, a disc installed to dev_hdd0) is never
    written there, by design, so games.yml alone can never resolve it and the game was dropped
    from every per-game picker with no way for the user to fix it. Verified live: TMNT Turtles in
    Time Re-Shelled (NPUB30107) and TMNT Out of the Shadows (NPUB31217) were both unreachable.

    Two shapes only, both anchored so a serial-SHAPED string cannot be mistaken for a serial:
      1. a `dev_hdd0/game/<SERIAL>` path component (the virtual-hard-drive install layout)
      2. a `[SERIAL]` tag in a folder name (the dir-style game layout)
    A path carrying two DIFFERENT serials of the same shape is refused rather than guessed at:
    writing per-game settings under the wrong serial fails silently, which is worse than not
    offering the game at all."""
    if not disc:
        return None
    for rx in (_HDD_GAME_RE, _BRACKET_RE):
        found = set(rx.findall(disc))
        if len(found) == 1:
            return found.pop()
        if found:
            return None                       # ambiguous -> refuse, never guess
    return None


def _serial_from_games_yml(disc: str) -> str | None:
    """The serial RPCS3's own games.yml register maps this disc path to, or None.
    Match order: exact | dir-prefix | realpath | UNambiguous basename."""
    if yaml is None or not _GAMES_YML.is_file():
        return None
    try:
        data = yaml.safe_load(_GAMES_YML.read_text(encoding="utf-8", errors="replace")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    want_real = os.path.realpath(disc) if os.path.exists(disc) else disc
    want_base = Path(disc.rstrip("/")).name
    base_hits: dict[str, str] = {}
    ambiguous: set[str] = set()
    for serial, path in data.items():
        serial = str(serial)
        if not is_serial(serial) or not isinstance(path, str):
            continue
        gp = path.rstrip("/")
        if disc == path or disc == gp or disc.startswith(gp + "/"):   # exact | dir entry is a prefix
            return serial
        if (os.path.realpath(path) if os.path.exists(path) else path) == want_real:
            return serial
        b = Path(gp).name
        if b in base_hits and base_hits[b] != serial:
            ambiguous.add(b)
        else:
            base_hits.setdefault(b, serial)
    return None if want_base in ambiguous else base_hits.get(want_base)


def path_to_serial(rom: str) -> str | None:
    """Reverse-map a launched path to its RPCS3 serial. ES-DE's ps3 system uses .desktop
    shortcuts, so a launched `rom` is usually a .desktop whose Exec= points at the disc.

    RPCS3's own games.yml register is asked FIRST and always wins: it is RPCS3's own answer,
    so where it has one there is nothing to second-guess. Only when it has nothing to say do
    we read the serial out of the disc path itself, which is the sole way to reach a title
    installed to the virtual hard drive (games.yml never lists those).

    THE ORDER OF THE GUARDS MATTERS. The games.yml lookup bails early when PyYAML is missing
    or the register file does not exist; those returns live inside _serial_from_games_yml so
    they skip only that lookup. Hoisted up here (where they used to be) they would also skip
    the path fallback, and a machine with no games.yml would resolve nothing at all."""
    if not rom:
        return None
    disc = _desktop_disc_path(rom) if str(rom).endswith(".desktop") else str(rom)
    if not disc:
        return None
    return _serial_from_games_yml(disc) or _serial_from_path(disc)
