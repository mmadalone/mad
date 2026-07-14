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
_SERIAL_RE = re.compile(r"^[A-Z]{4}[0-9]{5}\Z")    # BLES00590 / NPEA00362 (\Z: no trailing newline)
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


def path_to_serial(rom: str) -> str | None:
    """Reverse-map a launched path to its RPCS3 serial via games.yml. ES-DE's ps3 system uses
    .desktop shortcuts, so a launched `rom` is usually a .desktop whose Exec= points at the disc:
    an .iso path that exact-matches a games.yml value, or a dir game's
    `.../[SERIAL]/PS3_GAME/USRDIR/EBOOT.BIN` whose games.yml entry is the parent `.../[SERIAL]/`
    dir. Match order: exact | dir-prefix | realpath | UNambiguous basename. None if unresolved or
    an ambiguous basename collision (never guess the wrong game -> wrong overrides)."""
    if yaml is None or not rom or not _GAMES_YML.is_file():
        return None
    disc = _desktop_disc_path(rom) if str(rom).endswith(".desktop") else str(rom)
    if not disc:
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
