"""IdentCache -- a generic "remember an expensive per-file answer, keyed by the file
itself" cache. Generalised from the PROVEN pattern in lib/dolphin_gameids.py (path+
mtime+size keyed JSON cache, atomic best-effort save, threaded warm-up), so a new
per-file identity lookup (a disc's serial, a ROM's checksum, ...) can reuse this
instead of growing its own bespoke copy.

CACHE KEYS ARE THE REALPATH, not the path as given. This is load bearing: the same
rom folder is reachable on this machine as `~/ROMs/ps2`, `/run/media/1tbDeck/ROMs/ps2`
and `/run/media/deck/1tbDeck/ROMs/ps2` (a symlink plus two mount-point spellings of the
same SD card), and different callers use different ones. Without realpath the same disc
gets read three times and looked up under three different cache entries.

Only FINAL results are persisted. A resolver failure can be permanent (the file is
genuinely not a readable disc -- `final=True`) or transient (a timeout, a missing tool,
a one-off read error -- `final=False`). Persisting a transient failure would remember a
disc as permanently unreadable forever after one bad read, so only `final` outcomes
(successes are always final) are written to the on-disk cache; a non-final failure is
handed back to the caller but retried on the next call.

Stdlib only (no pip).
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Callable

_STORAGE_DIR = Path.home() / ".local/share/mad"


class IdentCache:
    """One named cache, e.g. IdentCache("ps2_serial", resolve_ps2_serial). `resolver(path)`
    is called on a cache miss (path = the realpath cache key) and must return
    `(id, why, final)`: `id` is the answer or None, `why` is a short plain-English reason
    (empty string on success), `final` is True iff the failure is permanent."""

    def __init__(self, name: str, resolver: Callable[[str], tuple]) -> None:
        self._path = _STORAGE_DIR / f"{name}.json"
        self._resolver = resolver
        self._cache: dict | None = None            # lazy: loaded on first use, not at construction
        self._lock = threading.Lock()               # guards self._cache ONLY -- never held during I/O

    # ---- lazy load / atomic best-effort save (copy of dolphin_gameids._load_cache/_save_cache) ----
    def _ensure_loaded(self) -> None:
        if self._cache is None:
            self._cache = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, snapshot: dict) -> None:
        """Persist a SNAPSHOT (the caller copies self._cache under the lock first, so
        json.dumps never iterates a mutating dict while another thread writes to it).
        Best-effort: any error is swallowed so an IO hiccup can never surface as an
        error in a MAD page. Atomic: write a sibling temp file, then os.replace it in,
        so a reader never observes a half-written cache file.

        THE TEMP NAME IS UNIQUE PER WRITER (pid + thread id). Saves deliberately run
        OUTSIDE the lock, so two threads can be here at once; a single shared ".tmp"
        path would let them interleave writes into the same file and then replace a
        corrupted cache in. Each writer gets its own temp file, and os.replace makes
        the last one to land win cleanly."""
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(snapshot))
            tmp.replace(self._path)
        except Exception:
            try:
                tmp.unlink()          # never leave our own debris behind on a failed save
            except OSError:
                pass

    # ---- key + validity ----
    @staticmethod
    def _key(path) -> str:
        # realpath is purely lexical for path components that don't exist, so this stays
        # correct (and identical for the same file reached two different ways) even when
        # the SD card is unmounted -- see the module docstring.
        try:
            return os.path.realpath(str(path))
        except OSError:
            return str(path)

    @staticmethod
    def _stat(path: str) -> tuple[int, int] | None:
        try:
            st = os.stat(path)
            return int(st.st_mtime), st.st_size
        except OSError:
            return None

    _FIELDS = ("mtime", "size", "id", "why", "final")

    @classmethod
    def _valid_hit(cls, entry, mtime: int, size: int) -> bool:
        # BOTH mtime and size must match. mtime alone is not enough: a file replaced
        # in place (same second, different content) can keep its old mtime.
        #
        # The SHAPE is checked too, not just the freshness. The cache file is plain JSON
        # on disk: a truncated write from an older version, or a hand edit, can leave an
        # entry with mtime+size but no "id". Callers read entry["id"] directly, so an
        # under-specified hit would raise KeyError inside a MAD page instead of simply
        # re-resolving. An entry that fails this test is treated as a miss and rebuilt.
        return (isinstance(entry, dict) and all(f in entry for f in cls._FIELDS)
                and entry.get("mtime") == mtime and entry.get("size") == size)

    def _resolve(self, key: str, mtime: int, size: int) -> dict:
        """Call the resolver for one file and build its cache entry. This is the module's
        single most important safety rail: a resolver that raises must never propagate to
        a MAD page, and a raise is always treated as a NON-final failure (never permanently
        remembered as unreadable just because it threw once)."""
        try:
            ident, why, final = self._resolver(key)
        except Exception as exc:
            ident, why, final = None, type(exc).__name__, False
        return {"mtime": mtime, "size": size, "id": ident, "why": why, "final": bool(final)}

    # ---- public API ----
    def get(self, path) -> dict | None:
        """The cache entry for one file, or None if the file doesn't exist right now.
        Resolves on a miss (or a stale mtime/size), synchronously, on the calling thread."""
        key = self._key(path)
        st = self._stat(key)
        if st is None:
            return None
        mtime, size = st
        with self._lock:
            self._ensure_loaded()
            hit = self._cache.get(key)
            if self._valid_hit(hit, mtime, size):
                return dict(hit)
        entry = self._resolve(key, mtime, size)      # slow (resolver) -- OUTSIDE the lock
        if entry["final"]:                            # only a FINAL result gets remembered
            with self._lock:
                self._ensure_loaded()
                self._cache[key] = entry
                snapshot = dict(self._cache)
            self._save(snapshot)
        # A COPY, never the dict now living in self._cache: callers must not be able to
        # corrupt the cache by mutating what they were handed (the hit path above already
        # copies, so both paths behave the same way).
        return dict(entry)

    def _bulk(self, paths: list, workers: int, budget: float | None) -> tuple[dict, int]:
        """Shared implementation for get_many/warm: resolves every cache miss in `paths`
        concurrently, writes the cache ONCE at the end, and returns
        ({realpath: entry-or-None}, count of paths that got a fresh non-None id this call)."""
        deadline = None if budget is None else monotonic() + budget
        out: dict[str, dict | None] = {}
        todo: list[tuple[str, int, int]] = []          # (key, mtime, size) needing resolution
        with self._lock:
            self._ensure_loaded()
            seen: set[str] = set()
            for p in paths:
                key = self._key(p)
                if key in seen:
                    continue
                seen.add(key)
                st = self._stat(key)
                if st is None:
                    out[key] = None                    # gone right now -- distinguish from a real answer
                    continue
                mtime, size = st
                hit = self._cache.get(key)
                if self._valid_hit(hit, mtime, size):
                    out[key] = dict(hit)
                else:
                    todo.append((key, mtime, size))
        new_count = 0
        if not todo:
            return out, new_count
        # `budget` only gates which NEW resolutions get STARTED -- a task already handed to
        # the pool is allowed to finish rather than being killed mid-read (a plain thread
        # can't be safely cancelled, and a half-run resolver must never write a wrong answer).
        started: list[tuple[str, int, int]] = []
        for item in todo:
            if deadline is not None and monotonic() >= deadline:
                out[item[0]] = None                    # budget exhausted -- never started
            else:
                started.append(item)
        resolved_final: list[tuple[str, dict]] = []
        if started:
            def _run(item: tuple[str, int, int]) -> tuple[str, dict]:
                key, mtime, size = item
                return key, self._resolve(key, mtime, size)
            with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
                for key, entry in ex.map(_run, started):
                    out[key] = dict(entry)             # a copy: see the note in get()
                    if entry["id"] is not None:
                        new_count += 1
                    if entry["final"]:
                        resolved_final.append((key, entry))
        if resolved_final:
            with self._lock:
                self._ensure_loaded()
                for key, entry in resolved_final:
                    self._cache[key] = entry
                snapshot = dict(self._cache)
            self._save(snapshot)                       # ONE write for the whole batch
        return out, new_count

    def get_many(self, paths: list, workers: int = 4, budget: float | None = None) -> dict:
        """{realpath: entry-or-None} for every path in `paths`. Misses resolve concurrently
        (default 4 workers). If `budget` (seconds) is given, stop STARTING new resolutions
        once it's exceeded; paths left unstarted map to None rather than blocking."""
        out, _ = self._bulk(paths, workers, budget)
        return out

    def warm(self, paths: list, workers: int = 4, budget: float | None = None) -> int:
        """get_many() for its side effect (populate the on-disk cache). Returns the count
        of paths that were freshly resolved to a non-None id by this call (cache hits from
        before this call don't count)."""
        _, new_count = self._bulk(paths, workers, budget)
        return new_count

    def prune_missing(self) -> int:
        """Drop cache entries whose file no longer exists, return how many were dropped.
        SAFE WHEN THE SD CARD IS UNMOUNTED: if EVERY entry is missing, prune NOTHING and
        return 0, because that pattern means the whole library is unplugged, not that every
        file was individually deleted -- the exact same reasoning as the all-missing guard in
        lib/madsrv/pcsx2_games.py:games() (an all-stale gamelist.cache keeps the full list
        rather than blanking the pickers)."""
        with self._lock:
            self._ensure_loaded()
            keys = list(self._cache.keys())
            if not keys:
                return 0
            present = {k: os.path.exists(k) for k in keys}
            if not any(present.values()):
                return 0                                # everything missing -- library unplugged, not emptied
            missing = [k for k, ok in present.items() if not ok]
            for k in missing:
                del self._cache[k]
            snapshot = dict(self._cache)
        if missing:
            self._save(snapshot)
        return len(missing)
