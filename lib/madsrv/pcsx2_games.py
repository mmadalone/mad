"""pcsx2_games — headless PS2 game list + widescreen-patch lookup for standard PCSX2.

Pure helpers (no RPC). Two jobs:

1. ROM -> serial + CRC + friendly title, by parsing PCSX2's OWN game-list cache
   (a binary "GLCE" file it writes after scanning its library) rather than parsing
   discs ourselves. This is the same serial+CRC PCSX2 uses to name per-game override
   files (~/.config/PCSX2/gamesettings/<SERIAL>_<CRC>.ini), so a key built here maps
   1:1 onto the file PCSX2 reads.

2. "Does a working widescreen patch exist for this game?" by indexing PCSX2's bundled
   patch database (patches.zip inside the pcsx2-Qt AppImage). The index (serial_crc /
   crc stems that carry a `[Widescreen 16:9]` block) is built once and cached on disk,
   rebuilt only when the AppImage changes. Any failure degrades gracefully to None
   (no widescreen affordance) rather than raising.

Scope = STANDARD PCSX2 only (the pcsx2-Qt AppImage, ~/.config/PCSX2). The pcsx2x6
lightgun forks use their own -datapath/-portable data roots and are out of scope.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .. import mad_paths
from . import cfgutil

_CFG = Path.home() / ".config/PCSX2"
_INI = _CFG / "inis/PCSX2.ini"
_GAMESETTINGS = _CFG / "gamesettings"
_PATCHES_DIR = _CFG / "patches"                 # on-disk pnach override dir (usually empty)
_WS_MARK = b"[Widescreen 16:9]"
_MAGIC = b"GLCE"
_VERSION = 34


# ── ROM -> serial via gamelist.cache ─────────────────────────────────────────
def cache_path() -> Path:
    """Locate PCSX2's gamelist.cache. Its directory is `[Folders] Cache` in
    PCSX2.ini (absolute on this box, else relative to the config dir); the file is
    always `gamelist.cache` inside it."""
    text = cfgutil.read_text(_INI) or ""
    folder = cfgutil.ini_read(text, "Folders", "Cache")
    if folder:
        d = Path(folder).expanduser()
        if not d.is_absolute():
            d = _CFG / folder
    else:
        d = _CFG / "cache"
    return d / "gamelist.cache"


def _read_str(data: bytes, off: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", data, off)
    off += 4
    s = data[off:off + n].decode("utf-8", "replace")
    return s, off + n


def parse_cache(path: Path) -> list[dict]:
    """Parse the GLCE-format gamelist.cache into entries. Tolerant: a bad magic or
    version returns []; a truncated/misaligned tail returns the leading good entries
    (PCSX2 may be mid-rescan). Each entry -> {serial, crc:int, title, title_en,
    region, path, key:'<SERIAL>_<CRC:08X>'}."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    if data[:4] != _MAGIC:
        return []
    try:
        (ver,) = struct.unpack_from("<I", data, 4)
    except struct.error:
        return []
    if ver != _VERSION:
        return []
    out: list[dict] = []
    off, n = 8, len(data)
    while off < n:
        try:
            path_s, off = _read_str(data, off)
            serial, off = _read_str(data, off)
            title, off = _read_str(data, off)
            _title_sort, off = _read_str(data, off)
            title_en, off = _read_str(data, off)
            typ, region = struct.unpack_from("<BB", data, off)
            off += 2
            off += 16                                    # total_size u64 + last_modified u64
            (crc,) = struct.unpack_from("<I", data, off)
            off += 4
            struct.unpack_from("<B", data, off)          # compatibility_rating (validate presence)
            off += 1
        except (struct.error, IndexError):
            break                                        # truncated -> keep what parsed
        if off > n:
            break
        if serial:
            out.append({"serial": serial, "crc": crc, "title": title, "title_en": title_en,
                        "region": region, "path": path_s, "key": f"{serial}_{crc:08X}"})
    return out


def games() -> list[dict]:
    """Deduplicated, name-sorted game list: [{key, serial, crc, name, path}]."""
    seen: set[str] = set()
    out: list[dict] = []
    for e in parse_cache(cache_path()):
        if e["key"] in seen:
            continue
        seen.add(e["key"])
        out.append({"key": e["key"], "serial": e["serial"], "crc": e["crc"],
                    "name": e["title_en"] or e["title"] or e["serial"], "path": e["path"]})
    out.sort(key=lambda g: g["name"].lower())
    return out


def path_to_key(rom_path: str) -> str | None:
    """Resolve a launching ROM path to its <SERIAL>_<CRC> key (realpath-normalized on
    both sides, so ES-DE's ~/ROMs symlink matches PCSX2's ~/Emulation/roms entry).
    Used by the launch-time router (Phase 2)."""
    try:
        target = os.path.realpath(rom_path)
    except OSError:
        return None
    for e in parse_cache(cache_path()):
        try:
            if os.path.realpath(e["path"]) == target:
                return e["key"]
        except OSError:
            continue
    return None


# ── widescreen-patch index (patches.zip inside the AppImage) ──────────────────
def appimage_path() -> Path:
    """Newest ~/Applications/pcsx2-Qt*.AppImage (never the pcsx2x6 forks)."""
    cands = [c for c in glob.glob(str(Path.home() / "Applications/pcsx2-Qt*.AppImage"))
             if "x6" not in Path(c).name.lower()]
    return Path(sorted(cands)[-1]) if cands else (Path.home() / "Applications/pcsx2-Qt.AppImage")


def _index_file() -> Path:
    return mad_paths.storage("pcsx2", "widescreen-index.json")


def _scan_zip(zip_path: Path) -> set[str]:
    """The pnach stems in `zip_path` that carry a `[Widescreen 16:9]` block."""
    keys: set[str] = set()
    with zipfile.ZipFile(zip_path) as z:
        for nm in z.namelist():
            if not nm.endswith(".pnach"):
                continue
            try:
                if _WS_MARK in z.read(nm):
                    keys.add(Path(nm).name[:-len(".pnach")])
            except Exception:
                continue
    return keys


def _build_ws_index(app: Path) -> set[str] | None:
    """Extract only patches.zip from the AppImage and index the pnach stems that
    contain a `[Widescreen 16:9]` block. Returns None on any failure."""
    tmp = tempfile.mkdtemp(prefix="mad-wsidx-")
    try:
        r = subprocess.run([str(app), "--appimage-extract", "usr/bin/resources/patches.zip"],
                           cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=120)
        if r.returncode != 0:
            return None
        zp = Path(tmp) / "squashfs-root/usr/bin/resources/patches.zip"
        if not zp.is_file():
            return None
        return _scan_zip(zp)
    except (OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ws_index() -> set[str] | None:
    """The set of pnach stems (`<SERIAL>_<CRC>` and bare `<CRC>`) that carry a
    widescreen patch. Cached on disk keyed by AppImage mtime+size; rebuilt when the
    AppImage changes. None if the DB can't be read (graceful: no widescreen hint)."""
    app = appimage_path()
    try:
        st = app.stat()
    except OSError:
        return None
    idxf = _index_file()
    if idxf.is_file():
        try:
            data = json.loads(idxf.read_text(encoding="utf-8"))
            if (data.get("appimage") == str(app) and data.get("mtime") == st.st_mtime
                    and data.get("size") == st.st_size):
                return set(data.get("keys") or [])
        except Exception:
            pass
    keys = _build_ws_index(app)
    if keys is None:
        return None
    try:
        idxf.parent.mkdir(parents=True, exist_ok=True)
        cfgutil.atomic_write(idxf, json.dumps({"appimage": str(app), "mtime": st.st_mtime,
                                               "size": st.st_size, "keys": sorted(keys)}))
    except OSError:
        pass
    return keys


def has_widescreen(serial: str, crc_hex: str) -> bool | None:
    """True if a widescreen patch exists for this serial+CRC. On-disk patches/ takes
    precedence over the bundled DB (PCSX2's own order). None = DB unreadable."""
    present = [p for p in (_PATCHES_DIR / f"{serial}_{crc_hex}.pnach",
                           _PATCHES_DIR / f"{crc_hex}.pnach") if p.is_file()]
    if present:
        for p in present:
            try:
                if _WS_MARK in p.read_bytes():
                    return True
            except OSError:
                pass
        return False
    idx = ws_index()
    if idx is None:
        return None
    return f"{serial}_{crc_hex}" in idx or crc_hex in idx
