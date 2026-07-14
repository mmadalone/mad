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
_EXT_RE = re.compile(r"\.(iso|ISO|pkg|PKG|bin|BIN)$")
_TAG_RE = re.compile(r"\s*[\[(][^\])]*[\])]")       # " [BLES01291]", " (Europe)", " (En,Fr,..)"


def is_serial(s: str) -> bool:
    return bool(_SERIAL_RE.match(s or ""))


def _clean_name(path: str) -> str:
    p = Path(path.rstrip("/"))
    raw = p.name or path
    raw = _EXT_RE.sub("", raw)
    raw = _TAG_RE.sub("", raw)          # drop bracketed serial + parenthetical region/lang tags
    raw = raw.replace("_", " ")         # "Spider-Man_ Edge" (a stand-in for ':') -> "Spider-Man  Edge"
    raw = re.sub(r"\s{2,}", " ", raw).strip()
    return raw or (p.name or path)


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
    """[{key: SERIAL, name: friendly title, path: ROM path}], sorted by name.
    Empty if games.yml is missing/unreadable or PyYAML is unavailable."""
    if yaml is None or not _GAMES_YML.is_file():
        return []
    try:
        data = yaml.safe_load(_GAMES_YML.read_text(encoding="utf-8", errors="replace")) or {}
    except (OSError, yaml.YAMLError):      # errors="replace" -> a non-UTF-8 path byte can't crash us
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for serial, path in data.items():
        serial = str(serial)
        if not is_serial(serial) or not isinstance(path, str):
            continue
        out.append({"key": serial, "name": _clean_name(path), "path": path})
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
