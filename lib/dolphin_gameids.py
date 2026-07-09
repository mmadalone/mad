"""Dolphin GameCube/Wii ROM -> 6-char GameID resolver + per-game / bundled-DB path helpers.

Dolphin per-game overrides live in `GameSettings/<GameID>.ini` (user-writable) layered over a bundled
read-only DB (`sys/GameSettings/`). To edit a game's overrides MAD must map an ES-DE ROM path to
Dolphin's 6-char disc GameID (e.g. `GALE01`). The GameID can't be read from the file directly for the
container formats ES-DE stores (.rvz/.wbfs/.nkit), so we ask Dolphin's OWN tool (rule 1: use the
documented interface, don't reverse-engineer the binary gamelist.cache):

    flatpak run --command=dolphin-tool org.DolphinEmu.dolphin-emu header -i <rom>   -> "Game ID: <ID>"

Each result is cached by path+mtime (a JSON file) so dolphin-tool runs at most once per ROM ever;
first-open resolution of a whole library runs concurrently to keep it quick.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Dolphin (Flatpak) splits its dirs XDG-style: the .ini CONFIG (Dolphin.ini/GFX.ini, read by the
# GLOBAL settings) is under config/, but the USER DATA dir -- GameSettings/, Load/, StateSaves/, GC/,
# Wii/ -- is under data/. Per-game GameINI overrides + cheats live in data/.../GameSettings/ (verified:
# the user's live cheat edits land there); config/.../GameSettings/ is a stale/foreign copy Dolphin
# does NOT read. So per-game MUST use the data/ dir.
_USER_GS = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GameSettings"
_SYS_GS = Path.home() / (".local/share/flatpak/app/org.DolphinEmu.dolphin-emu/current/active/"
                         "files/share/dolphin-emu/sys/GameSettings")
_CACHE = Path.home() / ".local/share/mad/dolphin_gameids.json"     # {abspath: {mtime, id}}
_ID_RE = re.compile(r"^[A-Z0-9]{6}$")
_TOOL = ["flatpak", "run", "--command=dolphin-tool", "org.DolphinEmu.dolphin-emu"]
_GAMEID_LINE = re.compile(r"^\s*Game ID:\s*([A-Z0-9]{6})\s*$", re.MULTILINE)

# Dolphin-readable GC/Wii disc extensions (DiscIO). ".nkit.iso" is caught by the ".iso" suffix; .tgc
# is the GameCube TGC container (ES-DE lists it for gc/wii and dolphin-tool reads its GameID).
EXTS = (".rvz", ".iso", ".gcm", ".gcz", ".wbfs", ".ciso", ".wia", ".tgc")

_cache: dict | None = None                                        # lazy per-process
_LOCK = threading.Lock()                                          # guards _cache (concurrent .games)


def user_ini(gameid: str) -> Path:
    return _USER_GS / f"{gameid}.ini"


def bundled_chain(gameid: str) -> list[Path]:
    """Bundled read-only DB files that apply to <gameid>, low->high priority (Dolphin's
    GetGameIniFilenames fallback: <letter>, <3-letter>, <id>, <id>r<rev>). The revision tier is
    globbed (<id>r*.ini) because the shipped DB often keys codes there -- e.g. Melee's are in
    GALE01r0/r1/r2.ini, not GALE01.ini -- and we don't know the ROM's exact revision here."""
    out: list[Path] = []
    if len(gameid) == 6:
        out += [_SYS_GS / f"{gameid[0]}.ini", _SYS_GS / f"{gameid[:3]}.ini"]
    out.append(_SYS_GS / f"{gameid}.ini")
    try:
        out += sorted(_SYS_GS.glob(f"{gameid}r*.ini"))
    except OSError:
        pass
    seen, chain = set(), []
    for p in out:
        if p not in seen and p.is_file():
            seen.add(p)
            chain.append(p)
    return chain


def _load_cache() -> dict:
    try:
        data = json.loads(_CACHE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(snapshot: dict) -> None:
    """Persist a SNAPSHOT of the cache (the caller copies _cache under _LOCK, so json.dumps never
    iterates a mutating dict). Best-effort: any error is swallowed so a serialize/IO hiccup can never
    surface as EINTERNAL to the page."""
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot))
        tmp.replace(_CACHE)
    except Exception:
        pass


def _mtime(p: Path) -> int | None:
    try:
        return int(p.stat().st_mtime)
    except OSError:
        return None


def _tool_gameid(rom: Path) -> str | None:
    try:
        r = subprocess.run(_TOOL + ["header", "-i", str(rom)],
                           capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _GAMEID_LINE.search(r.stdout or "")
    return m.group(1) if m else None


def _cached(key: str, mtime: int):
    hit = (_cache or {}).get(key)
    if isinstance(hit, dict) and hit.get("mtime") == mtime and _ID_RE.match(hit.get("id") or ""):
        return hit["id"]
    return None


def _ensure_loaded() -> None:
    global _cache
    if _cache is None:
        _cache = _load_cache()


def gameid(rom: str | Path) -> str | None:
    """The 6-char GameID for one ROM path, or None. Cached by path+mtime."""
    p = Path(rom)
    mt = _mtime(p)
    if mt is None:
        return None
    key = str(p)
    with _LOCK:
        _ensure_loaded()
        got = _cached(key, mt)
    if got:
        return got
    gid = _tool_gameid(p)                                   # slow (subprocess) -- outside the lock
    if gid and _ID_RE.match(gid):
        with _LOCK:
            _cache[key] = {"mtime": mt, "id": gid}
            _save_cache(dict(_cache))
        return gid
    return None


def gameids(roms: list) -> dict:
    """{abspath: gameid|None} for many ROMs. Cache hits are instant; uncached ROMs resolve
    concurrently via dolphin-tool (first-open warm-up), then the cache is written once. Thread-safe:
    the shared _cache is only touched under _LOCK, and the slow dolphin-tool calls run outside it."""
    with _LOCK:
        _ensure_loaded()
    out: dict[str, str | None] = {}
    todo: list[tuple[str, Path, int]] = []
    for rom in roms:
        p = Path(rom)
        mt = _mtime(p)
        if mt is None:
            out[str(p)] = None
            continue
        key = str(p)
        with _LOCK:
            got = _cached(key, mt)
        if got:
            out[key] = got
        else:
            todo.append((key, p, mt))
    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            resolved = list(ex.map(lambda t: (t[0], t[2], _tool_gameid(t[1])), todo))
        with _LOCK:
            for key, mt, gid in resolved:
                if gid and _ID_RE.match(gid):
                    _cache[key] = {"mtime": mt, "id": gid}
                    out[key] = gid
                else:
                    out[key] = None
            _save_cache(dict(_cache))
    return out
