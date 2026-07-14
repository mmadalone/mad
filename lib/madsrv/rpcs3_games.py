"""rpcs3_games — headless PS3 game list + serial resolver for RPCS3.

RPCS3 writes its own games.yml (``<SERIAL>: <ROM path>``) after scanning the library, and
names per-game override files ``custom_configs/config_<SERIAL>.yml`` by that SAME serial —
so a key built here maps 1:1 onto the file RPCS3 reads. Pure helpers (no RPC). A friendly
title is derived from the ROM path basename (region/lang/serial tags stripped). Any failure
degrades to [] rather than raising.
"""
from __future__ import annotations

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
