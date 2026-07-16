"""
ES-DE custom collections as routable units for the controller-router.

An ES-DE custom collection is a plain-text list of ROM paths at
`~/ES-DE/collections/custom-<display-name>.cfg`, one per line. Entries may be
absolute (`/home/deck/ROMs/nes/x.zip`, how ES-DE itself writes them) or
`%ROMPATH%`-relative, and the `~/ROMs` root is itself a symlink — so membership
is matched on a CANONICALISED path (see `_canon`), not by literal string compare.
ES-DE launches a collection game AS its real system (it passes e.g. `nes`/`arcade`
to the hooks, never the collection name), so collection membership is detected at
launch by matching the launched ROM path against the enabled collections' `.cfg`
files.

A matched collection lets the router apply a `[collections.<display-name>]`
policy that OVERRIDES the launched system's policy (e.g. a Duck Hunt launch from
NES routes as the lightgun collection, not as `nes`). This generalises the old
hardcoded Pew-Pew-Pew handling.

Only ENABLED collections count — the set listed in es_settings.xml's
`CollectionSystemsCustom` (so a stale .cfg never silently overrides). Reads are
cached per-process (the router runs once per launch; the GUI is short-lived).
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

from . import esde_settings

ESDE = esde_settings.APPDATA                 # honors $ESDE_APPDATA_DIR (default ~/ES-DE)
COLLECTIONS_DIR = ESDE / "collections"
SETTINGS = ESDE / "settings" / "es_settings.xml"


def _esde_setting(name: str) -> str | None:
    """Value of a `<string name="..." value="..."/>` setting, or None."""
    if not SETTINGS.is_file():
        return None
    try:
        txt = SETTINGS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(rf'<string name="{re.escape(name)}"\s+value="([^"]*)"', txt)
    return m.group(1) if m else None


@functools.lru_cache(maxsize=1)
def rom_root() -> Path:
    """ES-DE's ROM directory. Empty `ROMDirectory` => ES-DE's default ~/ROMs."""
    v = (_esde_setting("ROMDirectory") or "").replace("%HOME%", str(Path.home())).strip()
    return Path(v).expanduser() if v else (Path.home() / "ROMs")


def _canon(path: str) -> str:
    """Canonical key for comparing collection/ROM paths regardless of the form
    they were written in.

    A `.cfg` may store an entry as `%ROMPATH%/nes/x.zip`, as the absolute
    symlink path `/home/deck/ROMs/nes/x.zip`, or (in principle) as the resolved
    `/run/media/.../ROMs/nes/x.zip`; ES-DE hands the launch hooks the absolute
    form. We collapse all of these to one value: expand `%ROMPATH%`/`%HOME%`/`~`,
    then `os.path.realpath` to resolve the `~/ROMs` symlink and normalise away
    `.`/`..`/`//`. realpath is purely lexical for path components that don't
    exist, so this stays correct (and identical on both sides) even when the SD
    card is unmounted — both the stored entry and the launched path resolve the
    same way."""
    if not path:
        return ""
    p = path.strip().replace("%ROMPATH%", str(rom_root())).replace("%HOME%", str(Path.home()))
    return os.path.realpath(os.path.expanduser(p))


@functools.lru_cache(maxsize=1)
def enabled_collections() -> tuple[str, ...]:
    """Display names of enabled custom collections, in setting order (= routing
    precedence when a ROM is in more than one). Falls back to every on-disk
    custom-*.cfg if the setting is unreadable."""
    raw = _esde_setting("CollectionSystemsCustom")
    if raw and raw.strip():
        names = [n.strip() for n in re.split(r"[,;]", raw) if n.strip()]
        if names:
            return tuple(names)
    if COLLECTIONS_DIR.is_dir():
        return tuple(sorted(f.name[len("custom-"):-len(".cfg")]
                            for f in COLLECTIONS_DIR.glob("custom-*.cfg")))
    return ()


def collection_file(name: str) -> Path:
    return COLLECTIONS_DIR / f"custom-{name}.cfg"


@functools.lru_cache(maxsize=None)
def members(name: str) -> frozenset[str]:
    """The ROM paths listed in a collection's .cfg, verbatim (stripped lines).
    Entries may be absolute or `%ROMPATH%`-relative; use `_canon` to compare."""
    f = collection_file(name)
    if not f.is_file():
        return frozenset()
    try:
        return frozenset(
            ln.strip() for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip())
    except OSError:
        return frozenset()


@functools.lru_cache(maxsize=None)
def _canon_members(name: str) -> frozenset[str]:
    """`members(name)` mapped through `_canon` for path-form-independent matching."""
    return frozenset(_canon(p) for p in members(name))


def collection_for_rom(rom_path: str) -> str | None:
    """First ENABLED collection whose .cfg lists this ROM. Matches regardless of
    the path form the .cfg uses (`%ROMPATH%` / `~/ROMs` symlink / resolved real
    path) by comparing canonicalised paths; an exact verbatim match is also
    honoured as a fast path. `rom_path` must already be unescaped (see
    classify._strip_escapes)."""
    if not rom_path:
        return None
    raw = rom_path.strip()
    canon = _canon(rom_path)
    for name in enabled_collections():
        if raw in members(name) or canon in _canon_members(name):
            return name
    return None


def rom_in_collection(rom_path: str, name: str) -> bool:
    """True iff `rom_path` is a member of the ENABLED custom collection `name`.
    Unlike `collection_for_rom` (which returns the FIRST enabled owner by
    precedence), this tests one specific collection — used to honour the
    collection the user actually launched FROM (the recorded `system-select`
    view) rather than first-by-order. Path-form independent (see `_canon`)."""
    if not rom_path or not name or name not in enabled_collections():
        return False
    raw = rom_path.strip()
    return raw in members(name) or _canon(rom_path) in _canon_members(name)


def most_specific_collection(rom_path: str) -> str | None:
    """The MOST SPECIFIC enabled custom collection containing `rom_path` = the one
    with the FEWEST members (ties broken by CollectionSystemsCustom order). None if
    the ROM is in no enabled collection. Used by the launch-screen resolver so the
    splash is a deterministic function of the GAME, independent of which view you
    launched from — e.g. a Spider-Man game (in spiderman⊂superheroes) always
    resolves to `spiderman`, while Batman/X-Men (only in superheroes) resolve to
    `superheroes`. No navigation/view-state tracking, hence no 'sticky' behaviour."""
    if not rom_path:
        return None
    best: str | None = None
    best_n = 0
    for name in enabled_collections():
        if rom_in_collection(rom_path, name):
            n = len(members(name))
            if best is None or n < best_n:
                best, best_n = name, n
    return best


def narrowest_combo_collection(rom_path: str, quit_combo: dict) -> str | None:
    """The narrowest ENABLED collection containing `rom_path` that HAS a per-collection
    quit combo set — i.e. `quit_combo["collection-<name>"]` is a dict. Narrowest = fewest
    members (ties by CollectionSystemsCustom order), like `most_specific_collection` but
    filtered to collections that actually carry a combo. None if none qualifies.

    `quit_combo` is the merged `[quit_combo]` table (controller-router.load_policy()'s
    `quit_combo`). Used by the quit-combo-watcher game-start hook (via `controller-router.py
    quit-combo-collection`) to (a) re-key the combo BUTTONS on the collection so they
    override the system/per-game combo, and (b) arm a quit watcher for plain RetroArch
    games in a combo-collection. `rom_path` must already be unescaped."""
    if not rom_path or not isinstance(quit_combo, dict):
        return None
    best: str | None = None
    best_n = 0
    for name in enabled_collections():
        if not isinstance(quit_combo.get(f"collection-{name}"), dict):
            continue
        if not rom_in_collection(rom_path, name):
            continue
        n = len(members(name))
        if best is None or n < best_n:
            best, best_n = name, n
    return best


def member_systems(name: str) -> set[str]:
    """ES-DE system shortnames the collection's members belong to — the path
    component right after the ROM root (EmuDeck layout `<ROMroot>/<system>/...`).
    Used by the GUI to auto-wire the systems a collection rule needs."""
    croot = _canon(str(rom_root())).rstrip("/")
    out: set[str] = set()
    for p in members(name):
        cp = _canon(p)
        sysname = None
        if cp.startswith(croot + "/"):
            rest = cp[len(croot) + 1:]
            sysname = rest.split("/", 1)[0] if "/" in rest else None
        if not sysname:
            m = re.search(r"/ROMs/([^/]+)/", cp)   # fallback for a differing root
            sysname = m.group(1) if m else None
        if sysname:
            out.add(sysname)
    return out
