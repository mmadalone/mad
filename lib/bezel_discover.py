"""bezel_discover — DYNAMIC source for the Bezel page's system tiles.

The page used to render a FIXED list (every row of ``bezel_cfg.SYSTEMS``). This makes
the DISPLAY gamelist-driven: a bezel system gets a tile only when at least one of its
member ES-DE systems (a) launches via RetroArch and (b) has a gamelist with >=1 game.

Effect (verified live): ADDS systems whose gamelist exists (atomiswave, naomi), DROPS
systems with no games (Game Gear — no gamelist, yet was always shown), and hides empty
stubs (naomi2 ships an empty ``<gameList></gameList>``). ``bezel_cfg.SYSTEMS`` stays the
irreducible per-pack metadata lookup (TBP repo / overlay subdir / cores / art), byte-for-
byte unchanged for every existing system, so the proven install/assign/status flow is
untouched — only the *tile enumeration* moved here.

Stdlib only.
"""
from __future__ import annotations

import re

from . import bezel_cfg
from . import es_systems
from . import retroarch_cfg

# A real <game> element, NOT the <gameList> wrapper: "<game\b" matches "<game>" / "<game …>"
# (incl. a tab/newline after the tag name) but the \b excludes "<gameList>". Matches the
# es_gamelist title reader's tag pattern so the two never disagree.
_GAME_RE = re.compile(r"<game\b", re.IGNORECASE)


def has_games(system: str) -> bool:
    """True iff this ES-DE system's gamelist holds >=1 actual <game> entry. (naomi2's
    empty <gameList></gameList> -> 0; Game Gear -> no gamelist file -> 0.)"""
    gl = es_systems.GAMELISTS / system / "gamelist.xml"
    if not gl.is_file():
        return False
    try:
        return bool(_GAME_RE.search(gl.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def is_ra(system: str, systems=None) -> bool:
    """True iff this ES-DE system launches via RetroArch. Derived from es_systems: the
    active <command> contains %EMULATOR_RETROARCH%. Def-less RA "hack" systems
    (genh / snesh / snesmsu1) have no <command> -> recovered via SYSTEM_CORE_MAP
    membership (which includes those three and excludes standalones like cannonball).
    Do NOT use `not is_standalone(cmd)` — is_standalone('')==False misclassifies def-less."""
    cmd = es_systems.default_command(system, systems)
    if cmd:
        return es_systems._RA_MACRO in cmd
    # def-less: RA iff it maps to a NON-EMPTY core list. SYSTEM_CORE_MAP also holds a few
    # []-mapped standalone keys (daphne/model3/mugen/wii) — value-test, not just membership,
    # so a def-less []-mapped key is never misread as RA. (genh/snesh/snesmsu1 -> real cores.)
    return bool(retroarch_cfg.SYSTEM_CORE_MAP.get(system))


def active_members(key: str, systems=None) -> list:
    """The member ES-DE systems of a bezel system (its rom_dirs) that are RA AND have
    games — i.e. the ones that justify showing this tile."""
    s = bezel_cfg._by_key(key)
    if not s:
        return []
    rom_dirs = s[4]
    return [m for m in rom_dirs if has_games(m) and is_ra(m, systems)]


def discover_keys() -> list:
    """bezel-system keys (in SYSTEMS order) that have >=1 active member — the dynamic
    tile set. Order is cosmetic; the page sorts by label."""
    systems = es_systems.load_systems()
    return [s[0] for s in bezel_cfg.SYSTEMS if active_members(s[0], systems)]


def list_systems() -> list:
    """The dynamic replacement for bezel_cfg.list_systems(): the same per-tile dict shape
    (key / label / art_system / repo_present / widescreen_warn + status fields), but only
    for discovered systems, sorted A->Z by label. Tile art is resolved by the caller
    (bezel_cmds, via console_art) exactly as before."""
    systems = es_systems.load_systems()
    out = []
    for key, label, repo, subdir, _rom_dirs, _cores, art in bezel_cfg.SYSTEMS:
        if not active_members(key, systems):
            continue
        st = bezel_cfg.status(key)
        out.append({"key": key, "label": label, "art_system": art,
                    "repo_present": bezel_cfg._src_subdir(repo, subdir) is not None,
                    "widescreen_warn": key in bezel_cfg.WIDESCREEN_WARN, **st})
    return sorted(out, key=lambda r: r["label"].lower())
