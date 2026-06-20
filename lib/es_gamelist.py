"""es_gamelist — read human titles out of an ES-DE gamelist (rom stem -> <name>).

Used by the Bezel page so game rows show "X-Men: Children of the Atom" instead of
the rom basename "xmcota".

WHY REGEX, NOT ElementTree: ES-DE gamelists are effectively MULTI-ROOT — an
``<alternativeEmulator><label>…</label></alternativeEmulator>`` block is written as a
SIBLING root before ``<gameList>``. ``ET.parse`` raises
``ParseError: junk after document element`` on those (verified live on nes / fba /
wii), so an ET-based reader would silently return ``{}`` for the biggest systems. A
tolerant ``<game>…</game>`` sweep is robust to that and to any stray markup.

Gamelists are static within a MAD session (House Rule #3), so the per-system map is
lru-cached. Stdlib only.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

from . import es_systems

# A real <game> ELEMENT body. `<game\b` matches "<game>" / "<game source=…>" but NOT
# "<gameList>" ("game"+"L" is not a word boundary). Non-greedy body up to the first </game>.
_GAME_BLOCK_RE = re.compile(r"<game\b[^>]*>(.*?)</game>", re.DOTALL | re.IGNORECASE)
_PATH_RE = re.compile(r"<path>(.*?)</path>", re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r"<name>(.*?)</name>", re.DOTALL | re.IGNORECASE)


@lru_cache(maxsize=None)
def titles(system: str) -> dict:
    """{rom-stem.lower(): <name>} for one ES-DE system's gamelist. Missing/unreadable
    gamelist -> {} (callers fall back to the rom stem). HTML entities are unescaped in
    BOTH the name and the stem key (ES-DE writes e.g. ``&amp;`` in paths/names)."""
    gl = es_systems.GAMELISTS / system / "gamelist.xml"
    if not gl.is_file():
        return {}
    try:
        text = gl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for block in _GAME_BLOCK_RE.finditer(text):
        body = block.group(1)
        pm = _PATH_RE.search(body)
        nm = _NAME_RE.search(body)
        if not pm or not nm:
            continue
        stem = Path(html.unescape(pm.group(1).strip())).stem
        name = html.unescape(nm.group(1).strip())
        if stem and name:
            out[stem.lower()] = name
    return out


def titles_for(systems) -> dict:
    """Union of titles() over several ES-DE systems (a bezel system spans member rom
    dirs, e.g. megadrive = genesis + megadrive). Later members win on a stem clash."""
    out: dict[str, str] = {}
    for s in systems:
        out.update(titles(s))
    return out


@lru_cache(maxsize=None)
def path_stems(system: str) -> frozenset:
    """{rom-stem.lower()} for every <game> with a <path> in the system's gamelist —
    i.e. the games ES-DE LISTS for this system, scraped (<name>) or not. Missing /
    unreadable gamelist -> empty set. Used to hide Bezel per-game rows for games the
    user doesn't have: a bulk Bezel-Project install wires overlay .cfgs for thousands
    of romsets (incl. ones you don't own), and those carry no gamelist entry."""
    gl = es_systems.GAMELISTS / system / "gamelist.xml"
    if not gl.is_file():
        return frozenset()
    try:
        text = gl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    out: set[str] = set()
    for block in _GAME_BLOCK_RE.finditer(text):
        pm = _PATH_RE.search(block.group(1))
        if pm:
            stem = Path(html.unescape(pm.group(1).strip())).stem
            if stem:
                out.add(stem.lower())
    return frozenset(out)


def listed_stems(systems) -> frozenset:
    """Union of path_stems() over a bezel system's member rom dirs — every game ES-DE
    lists across those systems. Empty only when NO member gamelist is readable (callers
    must then fall back to NOT filtering, to avoid hiding everything)."""
    out: set[str] = set()
    for s in systems:
        out |= path_stems(s)
    return frozenset(out)
