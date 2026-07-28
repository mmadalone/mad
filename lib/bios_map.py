"""Bucket the files under the BIOS root into per-system groups for the granular BIOS backup/restore UI.

FILE-FIRST and never lossy: a BIOS file's backup key is its TRUE biosRoot-relative path, so it restores to
its exact origin regardless of which bucket the browser showed it under. Bucketing is DISPLAY-ONLY:
  - a file inside a SUBDIR -> that subdir is the system (authoritative; the emulator placed it there);
  - a loose .zip at the root -> "Arcade / MAME" (the ~1959 MAME/FBNeo device ROMs live loose at the root);
  - a loose named-BIOS file -> a curated PATTERN map (PS1/Saturn/Sega CD/Neo Geo/PC Engine/3DO/...);
  - anything unmatched -> "Other" (a catch-all, so NO file is ever hidden or unbackable).

Read-only. `rel` is "bios/<path relative to bios_root>", the exact key the granular engine restores to under
the "bios" category (lexical containment, same as saves). Sizes are best-effort.
"""
from __future__ import annotations

import os
import re as _re
from pathlib import Path

# The loose .zip files at the BIOS root are the SHARED arcade device-ROM set (what MAME/FBNeo load).
# Labelled just "Arcade" so it does not collide with the per-core "MAME" / "MAME 2003" subdir tiles
# (those hold each libretro core's OWN bios/cheats/hiscores - distinct from this shared set).
ARCADE_KEY, ARCADE_LABEL = "arcade", "Arcade"
OTHER_KEY, OTHER_LABEL = "other", "Other"

# A bucket's canonical key -> the ES-DE theme system whose console.png art the tile should reuse, WHEN the
# canonical key itself has no console art. The UI falls back to the generic BIOS icon for a bucket left here
# without a match (pokemini/cdi/fm7/pc98/x1/palettes/other). See art_key().
_ART_KEYS = {
    "ps1": "psx", "pce": "tg16", "pcecd": "tg16", "neogeocd": "neogeo", "lynx": "atarilynx",
    "sgb": "gb", "fbneo": "arcade", "atari": "atari2600", "laserdisc": "daphne",
    "mame2003": "arcade", "mame2003plus": "arcade", "mame2010": "arcade", "mame2016": "arcade",
    "mame2000": "arcade", "ume": "arcade", "stv": "arcade", "msx-db": "msx", "msx-machines": "msx",
    "kronos": "saturn", "switch-ryujinx": "switch", "switch-yuzu": "switch", "3ds": "n3ds",
    "nes-hdpacks": "nes",
    # buckets whose console art the theme provides under a different system dir name
    "fm7": "fmtowns", "cdi": "cdimono1", "pokemini": "pokemonmini",
}


def art_key(bucket_key: str) -> str:
    """The theme system key whose console.png a bucket's tile should show: the bucket key itself, or a
    remap for the ones whose canonical key has no console art. The caller resolves it via console_art and
    falls back to the generic BIOS icon when it yields nothing."""
    return _ART_KEYS.get(bucket_key, bucket_key)

# BIOS SUBDIR -> (canonical bucket key, friendly label). Aliases (3do/3dobios, amiga/Amiga, fm7/xm7,
# neocd, Mupen64plus, np2/np2kai/pc98) share a canonical key so a system is ONE tile, not two. A subdir not
# listed here derives its label from es_systems.short_name (key = the subdir name).
_SUBDIR_LABELS = {
    "dc": ("dreamcast", "Dreamcast"), "flycast": ("dreamcast", "Dreamcast"),
    "pce": ("pce", "PC Engine"), "tg16": ("tg16", "TurboGrafx-16"), "PPSSPP": ("psp", "PSP"),
    "ps2": ("ps2", "PlayStation 2"), "ps3": ("ps3", "PlayStation 3"),
    "3do": ("3do", "3DO"), "3dobios": ("3do", "3DO"),
    "ryujinx": ("switch-ryujinx", "Switch (Ryujinx)"), "yuzu": ("switch-yuzu", "Switch (Yuzu)"),
    "azahar": ("3ds", "3DS (Azahar)"), "shadps4": ("ps4", "PS4 (shadPS4)"),
    "scummvm": ("scummvm", "ScummVM"), "vice": ("c64", "Commodore"),
    "amiga": ("amiga", "Amiga"), "Amiga": ("amiga", "Amiga"),
    "neocd": ("neogeocd", "Neo Geo CD"), "kronos": ("kronos", "Saturn (Kronos)"),
    "same_cdi": ("cdi", "Philips CD-i"), "keropi": ("x68000", "Sharp X68000"),
    "np2": ("pc98", "PC-98"), "np2kai": ("pc98", "PC-98"), "pc98": ("pc98", "PC-98"),
    "pc88": ("pc88", "PC-88"), "fm7": ("fm7", "FM-7"), "xm7": ("fm7", "FM-7"),
    "xmil": ("x1", "Sharp X1"), "openmsx": ("msx", "MSX"),
    "Mupen64plus": ("n64", "Nintendo 64"), "DirkSimple": ("laserdisc", "LaserDisc"),
    "mame": ("mame", "MAME"), "ume": ("ume", "MAME (UME)"), "fba": ("fba", "FinalBurn"),
    "fbneo": ("fbneo", "FinalBurn Neo"), "mame2000": ("mame2000", "MAME 2000"),
    "mame2003": ("mame2003", "MAME 2003"), "mame2003-plus": ("mame2003plus", "MAME 2003-Plus"),
    "mame2010": ("mame2010", "MAME 2010"), "mame2016": ("mame2016", "MAME 2016"),
    "palettes": ("palettes", "Palettes"), "Databases": ("msx-db", "MSX (databases)"),
    "Machines": ("msx-machines", "MSX (machines)"), "HdPacks": ("nes-hdpacks", "NES HD packs"),
    "STV BIOS": ("stv", "Sega ST-V"),
}

# Loose named-BIOS -> (bucket key, label). Ordered: FIRST match wins. Grounded in the real device filenames;
# only the common/high-value systems are mapped - "Other" catches the tail (still fully backup/restorable).
_PATTERNS: list = [
    (r"scph|ps1_rom|psxonpsp|psone", "ps1", "PlayStation"),
    (r"sega_?101|saturn|mpr-1793|stvbios|sega1003|stv", "saturn", "Saturn"),
    (r"neocd", "neogeocd", "Neo Geo CD"),
    (r"uni-?bios|neogeo|000-lo\.lo|sfix|sm1|sp-s|sp-u|vs-bios|asia-s3|ng-lo", "neogeo", "Neo Geo"),
    (r"syscard|\.pce$|super_?cd|cd-rom_?system|gexpress", "pcecd", "PC Engine CD"),
    (r"pcfx", "pcfx", "PC-FX"),
    (r"panafz|3do|goldstar|sanyotry", "3do", "3DO"),
    (r"32x_", "sega32x", "Sega 32X"),
    (r"bios_?cd|_mcd|_scd|mcd_|scd_|segacd", "segacd", "Sega CD"),
    (r"bios_?md|genesis|megadriv", "genesis", "Genesis / Mega Drive"),
    (r"dc_boot|dc_flash|naomi|awbios|airlbios", "dreamcast", "Dreamcast / NAOMI"),
    (r"disksys|fds", "fds", "Famicom Disk System"),
    (r"sgb", "sgb", "Super Game Boy"),
    (r"gba_?bios", "gba", "Game Boy Advance"),
    (r"gbc?_?bios|cgb|dmg", "gb", "Game Boy"),
    (r"lynx", "lynx", "Atari Lynx"),
    (r"ngp", "ngp", "Neo Geo Pocket"),
    (r"bios\.min", "pokemini", "Pokemon Mini"),
    (r"gc-|ipl\.bin|dolphin", "gc", "GameCube"),
    (r"mupen|64dd|pifdata|pif\.rom", "n64", "Nintendo 64"),
    (r"msx|cbios|panasonic|fs-a1", "msx", "MSX"),
    (r"kick|amiga", "amiga", "Amiga"),
    (r"cd-?i|cdi", "cdi", "Philips CD-i"),
    (r"cpc|amstrad", "amstradcpc", "Amstrad CPC"),
    (r"48\.rom|128.*\.rom|256s|plus|spectrum|\.szx$", "zxspectrum", "ZX Spectrum"),
    (r"5200|7800|atari|xegs", "atari", "Atari"),
    (r"mcpx|xbox", "xbox", "Xbox"),
    (r"colecovision|coleco", "colecovision", "ColecoVision"),
    (r"pc88|pc98|np2", "pc98", "PC-98"),
]
_COMPILED = [(_re.compile(rx, _re.IGNORECASE), key, label) for rx, key, label in _PATTERNS]

# Canonical bucket key -> friendly label, inverted from the subdir + pattern tables. A BACKUP source's
# manifest stores only the bucket key, so this lets bios.systems show the SAME friendly label as the live
# scan (subdir labels win over pattern labels where a key appears in both).
_KEY_LABELS = {ARCADE_KEY: ARCADE_LABEL, OTHER_KEY: OTHER_LABEL}
for _k, _lbl in _SUBDIR_LABELS.values():
    _KEY_LABELS.setdefault(_k, _lbl)
for _rx, _k, _lbl in _PATTERNS:
    _KEY_LABELS.setdefault(_k, _lbl)


def label_for_key(key: str) -> str:
    """Friendly label for a bucket key (for a BACKUP source, whose manifest carries only the key). Falls
    back to es_systems.short_name, then the key itself - mirrors _label_for_subdir's derivation."""
    if key in _KEY_LABELS:
        return _KEY_LABELS[key]
    try:
        from . import es_systems
        short = es_systems.short_name(key)
    except Exception:
        short = key
    return short or key


def order_key(row: dict):
    """Tile sort key: strictly alphabetical by friendly label, but the "Other" catch-all always sorts LAST
    (it is not a real system). Used by list_buckets AND the backup/cloud bios.systems view so they agree."""
    return (1 if row.get("key") == OTHER_KEY else 0, (row.get("label") or "").lower())


def _label_for_subdir(sub: str):
    """Canonical (key, label) for a BIOS subdir. Prefer the curated supplement (which shares a key across
    aliases), then es_systems.short_name when it maps the subdir to a real system name (not the bare key),
    else the raw subdir name as both key and label."""
    if sub in _SUBDIR_LABELS:
        return _SUBDIR_LABELS[sub]
    try:
        from . import es_systems
        short = es_systems.short_name(sub)
    except Exception:
        short = sub
    return sub, (short if short and short != sub else sub)


def _classify_loose(name: str):
    """(key, label) for a loose root file: a .zip is a MAME/arcade device ROM; a named file matches the
    pattern map; everything else falls to Other (kept, never lost)."""
    if name.lower().endswith(".zip"):
        return ARCADE_KEY, ARCADE_LABEL
    for rx, key, label in _COMPILED:
        if rx.search(name):
            return key, label
    return OTHER_KEY, OTHER_LABEL


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def list_buckets(bios_root=None) -> list:
    """Every file under the BIOS root, grouped into display buckets:
      [{key, label, count, size, files:[{src, rel, kind, size}]}]  (only non-empty buckets, sorted by label)
    `rel` = 'bios/<path relative to bios_root>' - the exact restore key. A file in a subdir buckets by the
    TOP-LEVEL subdir; a loose file buckets by _classify_loose. Read-only."""
    from . import mad_paths
    root = Path(bios_root) if bios_root is not None else mad_paths.bios_root()
    root_s = os.path.normpath(str(root))
    buckets: dict = {}
    if not root.is_dir():
        return []
    # followlinks=True: several BIOS subdirs are FRONT-DOOR SYMLINKS to the emulator's own store
    # (ryujinx/keys -> ~/.config/Ryujinx/system, azahar/keys, flycast/bios, ...); their files are real
    # BIOS that must be backable. A seen-realpath guard prevents a cyclic symlink from looping forever.
    seen: set = set()
    for dirpath, dirs, files in os.walk(root, followlinks=True):
        rp = os.path.realpath(dirpath)
        if rp in seen:
            dirs[:] = []   # already visited via another path (a symlink cycle) - do not descend again
            continue
        seen.add(rp)
        for fn in files:
            src = os.path.join(dirpath, fn)
            relpath = os.path.relpath(src, root_s)
            if relpath == os.curdir or relpath.startswith(os.pardir):
                continue
            parts = relpath.split(os.sep)
            if len(parts) > 1:                     # inside a subdir -> that top-level subdir is the system
                key, label = _label_for_subdir(parts[0])
            else:                                  # loose at the root
                key, label = _classify_loose(fn)
            b = buckets.get(key)
            if b is None:
                b = {"key": key, "label": label, "count": 0, "size": 0, "files": []}
                buckets[key] = b
            sz = _size(src)
            b["files"].append({"src": src, "rel": f"bios/{relpath}", "kind": "file", "size": sz})
            b["count"] += 1
            b["size"] += sz
    # Files WITHIN a bucket read A->Z by name (os.walk order is arbitrary); tiles read A->Z by label with
    # "Other" pinned last (order_key). "Arcade / MAME" is a real label -> it sorts under A, not to the end.
    for b in buckets.values():
        b["files"].sort(key=lambda f: os.path.basename(f["rel"]).lower())
    return sorted(buckets.values(), key=order_key)
