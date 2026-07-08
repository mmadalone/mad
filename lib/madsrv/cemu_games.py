"""cemu_games - installed Wii U game resolver + Cemu path helpers for the Cemu MAD tile.

The user's CURRENT Wii U library, resolved from Cemu's OWN scanned cache
(``<data>/title_list_cache.xml`` - well-formed single-root XML, so ElementTree-safe, exactly
analogous to how pcsx2_games parses PCSX2's gamelist.cache), keyed by the 16-hex title id. Only
BASE application titles are listed (title-id high word ``00050000``); update (``0005000e``) and DLC
(``0005000c``) entries share a game and are dropped. Ghost entries whose rom ``<path>`` no longer
exists are hidden UNLESS the whole library is missing (SD card unmounted), mirroring
``pcsx2_games.games()``. Friendly names prefer the ES-DE ``wiiu`` gamelist (cleaner, scraped) over
the cache ``<name>``, keyed by rom-file stem.

Path model (this Deck runs the NATIVE Cemu AppImage, XDG layout - same as lib/cemu_cfg.py and the
old cemu_cmds.py which both hardcode ~/.config/Cemu):
  * CONFIG dir  ~/.config/Cemu      -> settings.xml, controllerProfiles/, gameProfiles/
  * DATA   dir  ~/.local/share/Cemu -> graphicPacks/, title_list_cache.xml
Per-game overrides live in ``gameProfiles/<titleid:016x>.ini`` (lowercase hex) - the SAME file Cemu
reads (right-click game -> Edit game profile). cemu_pergame edits it; the picker's ``override`` badge
= that ini exists. Tests redirect ``_CONFIG_DIR`` / ``_DATA_DIR``.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .. import es_gamelist
from . import cfgutil
from .rpc import method

_CONFIG_DIR = Path.home() / ".config/Cemu"
_DATA_DIR = Path.home() / ".local/share/Cemu"
_ESDE_SYSTEM = "wiiu"
_BASE_PREFIX = "00050000"        # base application title-id high word (update=0005000e, dlc=0005000c)


# ── path helpers (read the module globals at call time, so tests can redirect them) ──
def config_dir() -> Path:
    return _CONFIG_DIR


def data_dir() -> Path:
    return _DATA_DIR


def settings_xml() -> Path:
    return _CONFIG_DIR / "settings.xml"


def controllerprofiles_dir() -> Path:
    return _CONFIG_DIR / "controllerProfiles"


def graphicpacks_dir() -> Path:
    return _DATA_DIR / "graphicPacks"


def gameprofiles_dir() -> Path:
    return _CONFIG_DIR / "gameProfiles"


def pergame_path(tid: str) -> Path:
    """gameProfiles/<titleid>.ini - Cemu names the file with the LOWERCASE 16-hex title id."""
    return gameprofiles_dir() / f"{tid.lower()}.ini"


def _title_cache() -> Path:
    return _DATA_DIR / "title_list_cache.xml"


# ── CRLF-aware game-profile IO (Cemu writes gameProfiles/*.ini with CRLF; settings.xml is LF) ──
def read_ini(path: Path) -> tuple[str | None, bool]:
    """Read a Cemu ini NORMALISED to LF, plus whether the file used CRLF. Cemu writes gameProfiles
    with CRLF, so raw cfgutil.ini_read values carry a trailing \\r that breaks exact enum matching;
    editing in LF then restoring the original ending keeps every untouched line byte-identical.
    Returns (lf_text | None, crlf); a NEW/absent file defaults to CRLF (Cemu's gameProfile style)."""
    text = cfgutil.read_text(path)
    if text is None:
        return None, True
    crlf = "\r\n" in text
    return (text.replace("\r\n", "\n") if crlf else text), crlf


def write_ini(path: Path, lf_text: str, crlf: bool) -> None:
    """Write an LF-internal ini back in the file's original ending (one-time .bak + atomic)."""
    out = lf_text.replace("\n", "\r\n") if crlf else lf_text
    path.parent.mkdir(parents=True, exist_ok=True)
    cfgutil.ensure_bak(path)                          # no-op when the file is new
    cfgutil.atomic_write(path, out)


def _exists(path: str) -> bool:
    try:
        return bool(path) and Path(path).exists()
    except OSError:
        return False


def _library() -> dict:
    """titleid (lowercase 16-hex) -> {"name","stem","path"} for the user's current Wii U BASE games.
    Empty when the cache is absent/unreadable (no title-id source -> no per-game/pack features)."""
    try:
        root = ET.fromstring(_title_cache().read_text(encoding="utf-8", errors="replace"))
    except (OSError, ET.ParseError):
        return {}
    esde_names = es_gamelist.titles(_ESDE_SYSTEM)      # {rom-stem.lower(): <name>}, {} if no gamelist
    out: dict[str, dict] = {}
    present: dict[str, bool] = {}
    for t in root.iter("title"):
        tid = (t.get("titleId") or "").strip().lower()
        if len(tid) != 16 or not tid.startswith(_BASE_PREFIX) or tid in out:
            continue
        path = (t.findtext("path") or "").strip()
        stem = Path(path).stem if path else ""
        name = ((esde_names.get(stem.lower()) if stem else None)
                or (t.findtext("name") or "").strip() or tid.upper())
        out[tid] = {"name": name, "stem": stem, "path": path}
        present[tid] = _exists(path)
    # Ghost guard: hide titles whose rom path is gone, UNLESS every one is gone (library unmounted).
    if out and any(present.values()):
        out = {tid: info for tid, info in out.items() if present.get(tid)}
    return out


def has_override(tid: str) -> bool:
    """The game has a Cemu game profile ini (right-click -> Edit game profile, or MAD per-game edits).
    A cheap profile-only check; the ``cemu.games`` picker badge ALSO counts enabled graphic packs
    (see ``_games``) since a game can be customised by packs alone with no profile."""
    try:
        return pergame_path(tid).is_file()
    except OSError:
        return False


def _profile_titleids() -> set:
    """Lowercase 16-hex title ids that have a gameProfiles/<tid>.ini."""
    out: set = set()
    try:
        for p in gameprofiles_dir().glob("*.ini"):
            s = p.stem.lower()
            if len(s) == 16:
                out.add(s)
    except OSError:
        pass
    return out


def listing(override_fn=has_override, summary_fn=None, hide_fn=None) -> list:
    """[{titleid,name,stem,override[,summary][,hide]}] sorted by name, the per-game picker payload.
    hide_fn(tid) -> [section keys the browser should omit for this game] (empty -> field omitted)."""
    items = []
    for tid, info in _library().items():
        row = {"titleid": tid, "name": info["name"], "stem": info["stem"],
               "override": bool(override_fn(tid))}
        if summary_fn is not None:
            row["summary"] = summary_fn(tid) or ""
        if hide_fn is not None:
            hide = hide_fn(tid)
            if hide:
                row["hide"] = hide
        items.append(row)
    items.sort(key=lambda g: g["name"].lower())
    return items


@method("cemu.games", slow=True)
def _games(params):
    # A game counts as customised (the picker's `* custom` badge) if it has EITHER a game profile OR
    # enabled game-specific graphic packs -- Twilight Princess etc. are customised by packs alone.
    # `hide` tells the (rebuilt) per-game browser which Graphic-packs entries to omit for this game:
    # "packs" when it has no packs at all, else "packs_<category>" for each empty category sub-page.
    # Late import of cemu_packs_cmds avoids an import cycle; older AppImages ignore the `hide` field.
    profiles = _profile_titleids()
    try:
        from . import cemu_packs_cmds as cp
        packs = cp.enabled_titleids()
        cats = cp.applicable_categories()          # {tid: {category present}}
        all_cats, catkey = cp.CATEGORIES, cp.catkey
    except Exception:
        packs, cats, all_cats, catkey = set(), {}, [], (lambda c: c)

    def _override(tid):
        return tid in profiles or tid in packs

    def _summary(tid):
        bits = []
        if tid in profiles:
            bits.append("game profile")
        if tid in packs:
            bits.append("graphic packs")
        return "Custom: " + ", ".join(bits) if bits else ""

    def _hide(tid):
        present = cats.get(tid, set())
        if not present:
            return ["packs"]                       # no packs -> hide the whole Graphic packs group
        return ["packs_" + catkey(c) for c in all_cats if c not in present]

    # system = the ES-DE system whose media the per-game browser resolves (art -> preview video).
    return {"games": listing(_override, _summary, _hide), "system": _ESDE_SYSTEM}
