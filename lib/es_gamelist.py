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

import glob as _glob
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
_DESC_RE = re.compile(r"<desc>(.*?)</desc>", re.DOTALL | re.IGNORECASE)
_ALTEMU_RE = re.compile(r"<altemulator>(.*?)</altemulator>", re.DOTALL | re.IGNORECASE)
_HIDDEN_RE = re.compile(r"<hidden>(.*?)</hidden>", re.DOTALL | re.IGNORECASE)


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


# ── per-game records (name + description + altemulator) ─────────────────────
# The gameview per-game page needs each game's <name>, <desc>, AND per-game
# <altemulator> (the es_systems command LABEL the user picked for just this
# game, e.g. "FinalBurn Neo" — RetroArch's per-game core resolver reads this to
# find the LAUNCHED core instead of assuming the system's default). `titles()`
# above stays a name-only map (its callers only want the name); `records()` is
# the richer read used by the gameview page. Both parse the same tolerant
# <game> sweep.

@lru_cache(maxsize=None)
def records(system: str) -> dict:
    """{rom-stem.lower(): {"name": str, "desc": str, "altemulator": str, "hidden": bool}} for one
    ES-DE system's gamelist. `desc`/`altemulator` are "" when the tag is absent.
    Requires both <path> and <name> (same as titles()). Missing/unreadable
    gamelist -> {}. HTML entities are unescaped in the stem key, name, desc and
    altemulator."""
    gl = es_systems.GAMELISTS / system / "gamelist.xml"
    if not gl.is_file():
        return {}
    try:
        text = gl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, dict] = {}
    for block in _GAME_BLOCK_RE.finditer(text):
        body = block.group(1)
        pm = _PATH_RE.search(body)
        nm = _NAME_RE.search(body)
        if not pm or not nm:
            continue
        stem = Path(html.unescape(pm.group(1).strip())).stem
        name = html.unescape(nm.group(1).strip())
        if not (stem and name):
            continue
        dm = _DESC_RE.search(body)
        desc = html.unescape(dm.group(1).strip()) if dm else ""
        am = _ALTEMU_RE.search(body)
        altemulator = html.unescape(am.group(1).strip()) if am else ""
        hm = _HIDDEN_RE.search(body)
        hidden = bool(hm) and hm.group(1).strip().lower() == "true"
        # "stem" keeps the ORIGINAL case (the dict key is lowercased for
        # case-insensitive lookup, same as titles()) — callers that need the
        # real rom_basename for a per-game identity ("<system>:<stem>", per
        # lib/classify.py's Path(rom).stem) must read rec["stem"], never the key.
        out[stem.lower()] = {"stem": stem, "name": name, "desc": desc,
                             "altemulator": altemulator, "hidden": hidden}
    return out


@lru_cache(maxsize=None)
def rom_paths(system: str) -> dict:
    """{rom-stem.lower(): raw <path> string from the gamelist} for one system. Unlike records() (which
    keeps only the stem), this preserves the FULL gamelist path - the extension and, for folder-per-game
    or .desktop-launched games (e.g. ps3), the launcher target - so the backup resolver can find the
    real ROM file/folder. The path is as written in the gamelist (usually ./-relative to the ROM dir)."""
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
        raw = html.unescape(pm.group(1).strip())
        stem = Path(raw).stem
        if stem:
            out[stem.lower()] = raw
    return out


def visible_records(system: str) -> dict:
    """records() minus games flagged <hidden>true</hidden> in the gamelist,
    UNLESS ES-DE's 'Show hidden games' setting is on -- so the RA per-game hub
    mirrors exactly what ES-DE itself shows. Not lru-cached: a cheap filter over
    the already-cached records(), and the ES-DE setting can change between runs."""
    from . import esde_settings
    if esde_settings.show_hidden_games():
        return records(system)
    return {k: r for k, r in records(system).items() if not r.get("hidden")}


def record(system: str, stem: str) -> dict:
    """The {"name","desc","altemulator"} record for one rom stem (case-insensitive),
    or {} when the game is not in the system's gamelist."""
    return records(system).get((stem or "").lower(), {})


# ── per-game downloaded media ────────────────────────────────────────────────
# ES-DE stores scraped art/video under downloaded_media/<system>/<subdir>/, each
# file named after the rom basename (stem). Subdir names verified live under
# ~/ES-DE/downloaded_media/*/ (2026-07-02): 3dboxes, backcovers, covers, fanart,
# manuals, marquees, miximages, physicalmedia, screenshots, titlescreens, videos.
# `kind` = the stable API name; `box3d` maps to ES-DE's "3dboxes" dir, the rest 1:1.
_MEDIA_SUBDIRS = {
    "covers": "covers",
    "backcovers": "backcovers",
    "box3d": "3dboxes",
    "physicalmedia": "physicalmedia",
    "marquees": "marquees",
    "screenshots": "screenshots",
    "titlescreens": "titlescreens",
    "miximages": "miximages",
    "fanart": "fanart",
    "manuals": "manuals",
    "videos": "videos",
}


def media_kinds() -> tuple:
    """The media `kind` names media_for() reports, in a stable order."""
    return tuple(_MEDIA_SUBDIRS)


# Human labels for the per-kind media DRILL (P4). Kept next to _MEDIA_SUBDIRS so a new kind is labelled here.
_MEDIA_KIND_LABELS = {
    "covers": "Box art", "backcovers": "Back cover", "box3d": "3D box",
    "physicalmedia": "Physical media", "marquees": "Marquee", "screenshots": "Screenshot",
    "titlescreens": "Title screen", "miximages": "Mix image", "fanart": "Fan art",
    "manuals": "Manual", "videos": "Video",
}
# subdir -> kind (the reverse of _MEDIA_SUBDIRS; 1:1 except "3dboxes" -> "box3d").
_SUBDIR_TO_KIND = {sub: kind for kind, sub in _MEDIA_SUBDIRS.items()}


def media_kind_label(kind: str) -> str:
    """A human label for a media `kind` (falls back to the kind name)."""
    return _MEDIA_KIND_LABELS.get(kind, kind)


def media_kind_from_rel(rel: str):
    """The media `kind` a backup-relative media path belongs to, or None. A media rel is
    'media/<system>/<subdir>/<stem>.<ext>' (resolve_game_assets), so the 3rd segment is the ES-DE media
    subdir; reverse-map it to the stable kind name. This lets a per-kind backup/restore filter derive the
    kind straight from the manifest rel - no schema change, so pre-P4 backups stay per-kind restorable."""
    if not isinstance(rel, str):
        return None
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "media":
        return _SUBDIR_TO_KIND.get(parts[2])
    return None


def media_for(system: str, stem: str, kinds=None) -> dict:
    """{kind: absolute-path-str | None} for one game's ES-DE downloaded media,
    globbed as media_root()/<system>/<subdir>/<stem>.<ext> (ES-DE names each media
    file after the rom basename). None for a kind with no file; every kind None if
    `stem` is empty or the media dir is absent (never raises). First match wins when
    several extensions exist for one kind. `kinds` limits which media kinds are
    globbed (e.g. ("covers",) for a fast browse over a huge system); default = all."""
    from . import esde_settings
    out: dict = {k: None for k in _MEDIA_SUBDIRS}
    if not stem:
        return out
    try:
        base = esde_settings.media_root() / system
    except Exception:
        return out
    want = _MEDIA_SUBDIRS if kinds is None else {k: _MEDIA_SUBDIRS[k] for k in kinds if k in _MEDIA_SUBDIRS}
    pattern = _glob.escape(stem) + ".*"
    # the game's ACTUAL rom extension (e.g. ".lindbergh", ".openbor"), for the fake-extension media fallback
    raw = rom_paths(system).get((stem or "").lower()) or ""
    romext = Path(raw).suffix
    for kind, sub in want.items():
        d = base / sub
        try:
            cands = [p for p in sorted(d.glob(pattern)) if p.is_file()]
        except OSError:
            continue
        # EXACT <stem>.<imgext> always wins, so a real cover can never be shadowed by a longer-stemmed
        # sibling. Only if there is no exact file do we accept the FAKE-EXTENSION form <stem>.<romext>.<imgext>
        # (ES-DE names lindbergh/openbor/... media after the FULL rom filename, e.g. rambo.lindbergh.png). We
        # require the middle segment to be the game's OWN rom extension (p.stem == stem+romext), so a real
        # sibling game 'Doom.2' can't have its 'Doom.2.png' misattributed to a coverless 'Doom'.
        hit = next((p for p in cands if p.name == stem + p.suffix), None)
        if hit is None and romext:
            hit = next((p for p in cands if p.stem == stem + romext), None)
        if hit is not None:
            out[kind] = str(hit)
    return out
