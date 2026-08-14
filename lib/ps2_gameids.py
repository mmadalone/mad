"""ps2_gameids -- the lib/ident_cache.py wiring for lib/ps2_disc.identify: a thin,
path+mtime+size cache so a PS2 disc's <SERIAL>_<CRC> key is only ever derived once per
file. Deliberately tiny: all the actual derivation logic lives in lib/ps2_disc.py, all
the caching/threading/persistence logic lives in lib/ident_cache.py; this module just
wires them together and adds a CLI for warming the cache / auditing the ROM library.

    python3 -m lib.ps2_gameids --warm     derive every ps2 disc, report new resolutions
    python3 -m lib.ps2_gameids --audit    print the derived key for every ps2 disc
"""
from __future__ import annotations

import sys
import time

from lib import ident_cache, ps2_disc, rom_folder

_cache = ident_cache.IdentCache("ps2_gameids", ps2_disc.identify)


def ident(path) -> dict | None:
    """One disc's cache entry ({mtime, size, id, why, final}), or None if the file
    doesn't exist right now. Delegates entirely to the shared IdentCache."""
    return _cache.get(path)


def idents(paths: list, workers: int = 4, budget: float | None = None) -> dict:
    """{realpath: entry-or-None} for many discs at once. See IdentCache.get_many."""
    return _cache.get_many(paths, workers=workers, budget=budget)


def warm(paths: list, workers: int = 4, budget: float | None = None) -> int:
    """Resolve every cache miss in `paths` and persist it. Returns the count of discs
    freshly resolved to a non-None key by this call. See IdentCache.warm."""
    return _cache.warm(paths, workers=workers, budget=budget)


def _rom_paths() -> list[str]:
    return [e["path"] for e in rom_folder.entries("ps2").values()]


def _main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    paths = _rom_paths()
    if mode == "--warm":
        t0 = time.monotonic()
        n = warm(paths)
        dt = time.monotonic() - t0
        print(f"ps2_gameids: {n} newly resolved of {len(paths)} discs in {dt:.2f}s")
        return 0
    if mode == "--audit":
        # NOTE: this only prints what ps2_disc derives -- comparing it against PCSX2's own
        # gamelist.cache key is the integration's job, not this module's.
        for path in paths:
            entry = ident(path)
            key = entry["id"] if entry else None
            why = entry["why"] if entry else "file not found"
            print(f"{key or '-':22s} {path}" + ("" if key else f"  ({why})"))
        return 0
    print("usage: python3 -m lib.ps2_gameids {--warm|--audit}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
