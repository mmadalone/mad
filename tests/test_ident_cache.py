"""IdentCache -- generic per-file identity cache (lib/ident_cache.py), generalised from
the proven lib/dolphin_gameids.py path+mtime+size cache pattern.

Covers: miss-then-hit (resolver called exactly once); a changed mtime, and separately a
changed size with mtime held constant, both forcing a re-resolve; a FINAL failure is
remembered (resolver not called again) while a NON-final failure is not (resolver IS
called again); a raising resolver never propagates and is never remembered; prune_missing
drops a gone file but prunes nothing when every file is gone (SD-card-unplugged guard);
two paths that realpath to the same file (a symlink) share one cache entry; get_many
honors a budget without writing a wrong answer for a path it never started; and
concurrent get_many calls never leave the on-disk cache file corrupted.

Run:  python3 -m unittest tests.test_ident_cache -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from lib import ident_cache


class _CountingResolver:
    """A resolver stub that records every call and returns a scripted (id, why, final),
    or raises, per a queue of outcomes (repeats the last outcome once exhausted)."""

    def __init__(self, outcomes):
        self.calls = []
        self._outcomes = list(outcomes)

    def __call__(self, path):
        self.calls.append(path)
        i = min(len(self.calls) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[i]
        if outcome == "raise":
            raise RuntimeError("boom")
        return outcome


class IdentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._save_storage = ident_cache._STORAGE_DIR
        ident_cache._STORAGE_DIR = self.tmp / "mad-store"     # hermetic: never touch the real cache dir

    def tearDown(self):
        ident_cache._STORAGE_DIR = self._save_storage
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, name, resolver):
        return ident_cache.IdentCache(name, resolver)

    def _write(self, name: str, content: str = "x") -> Path:
        p = self.tmp / name
        p.write_text(content)
        return p

    # ---- miss then hit ----
    def test_miss_then_hit_resolves_once(self):
        r = _CountingResolver([("ABC123", "", True)])
        c = self._cache("t1", r)
        rom = self._write("a.iso")
        first = c.get(rom)
        second = c.get(rom)
        self.assertEqual(first["id"], "ABC123")
        self.assertEqual(second["id"], "ABC123")
        self.assertEqual(len(r.calls), 1)

    def test_lazy_load_no_read_at_construction(self):
        # the cache file doesn't exist yet at all -- constructing must not touch disk / raise.
        c = self._cache("t-lazy", _CountingResolver([("X", "", True)]))
        self.assertIsNone(c._cache)                            # not loaded until first use

    # ---- review hardening: a hit must be well SHAPED, not merely fresh ----
    def test_underspecified_cache_entry_is_a_miss_not_a_keyerror(self):
        """A truncated write from an older version, or a hand edit, can leave an entry with
        a matching mtime+size but no "id". Callers read entry["id"] directly, so treating
        that as a hit would raise KeyError inside a MAD page. It must re-resolve instead."""
        rom = self._write("a.iso")
        st = os.stat(rom)
        store = self.tmp / "mad-store"
        store.mkdir(parents=True, exist_ok=True)
        (store / "t-shape.json").write_text(json.dumps({
            str(Path(os.path.realpath(rom))): {"mtime": int(st.st_mtime), "size": st.st_size}}))
        r = _CountingResolver([("REBUILT", "", True)])
        got = self._cache("t-shape", r).get(rom)
        self.assertEqual(got["id"], "REBUILT")
        self.assertEqual(len(r.calls), 1)                      # it re-resolved rather than raising

    def test_returned_entry_is_a_copy_not_the_live_cache_dict(self):
        """Callers must not be able to corrupt the cache by mutating what they were handed.
        Both the fresh-resolve path and the later hit path must hand back copies."""
        r = _CountingResolver([("REAL", "", True)])
        c = self._cache("t-copy", r)
        rom = self._write("a.iso")
        fresh = c.get(rom)
        fresh["id"] = "TAMPERED"
        self.assertEqual(c.get(rom)["id"], "REAL")             # cache survived the mutation
        hit = c.get(rom)
        hit["id"] = "TAMPERED-AGAIN"
        self.assertEqual(c.get(rom)["id"], "REAL")

    def test_save_leaves_no_temp_debris(self):
        """The temp file is named per pid+thread so concurrent savers cannot interleave into
        one shared ".tmp" and replace a corrupted cache in. Nothing may be left behind."""
        c = self._cache("t-tmp", _CountingResolver([("A", "", True)]))
        c.get(self._write("a.iso"))
        leftovers = sorted(p.name for p in (self.tmp / "mad-store").iterdir() if ".tmp" in p.name)
        self.assertEqual(leftovers, [])
        self.assertEqual(sorted(p.name for p in (self.tmp / "mad-store").iterdir()),
                         ["t-tmp.json"])

    # ---- validity: mtime and size independently ----
    def test_mtime_change_reresolves(self):
        r = _CountingResolver([("ID1", "", True), ("ID2", "", True)])
        c = self._cache("t2", r)
        rom = self._write("a.iso")
        c.get(rom)
        os.utime(rom, (os.stat(rom).st_mtime + 5, os.stat(rom).st_mtime + 5))
        second = c.get(rom)
        self.assertEqual(second["id"], "ID2")
        self.assertEqual(len(r.calls), 2)

    def test_size_change_same_mtime_reresolves(self):
        # the one that catches a lazy implementation: a file replaced IN PLACE can keep its
        # old mtime, so mtime alone must never be treated as a valid cache key.
        r = _CountingResolver([("ID1", "", True), ("ID2", "", True)])
        c = self._cache("t3", r)
        rom = self._write("a.iso", "short")
        c.get(rom)
        original_mtime = os.stat(rom).st_mtime
        rom.write_text("a much longer replacement payload")   # different size
        os.utime(rom, (original_mtime, original_mtime))       # ...but restore the EXACT old mtime
        self.assertEqual(int(os.stat(rom).st_mtime), int(original_mtime))
        second = c.get(rom)
        self.assertEqual(second["id"], "ID2")
        self.assertEqual(len(r.calls), 2)

    # ---- final vs non-final failures ----
    def test_final_failure_remembered_not_recalled(self):
        r = _CountingResolver([(None, "not a readable disc", True)])
        c = self._cache("t4", r)
        rom = self._write("bad.iso")
        first = c.get(rom)
        second = c.get(rom)
        self.assertIsNone(first["id"])
        self.assertEqual(first["why"], "not a readable disc")
        self.assertIsNone(second["id"])
        self.assertEqual(len(r.calls), 1)                     # NOT called again

    def test_nonfinal_failure_not_remembered_recalled(self):
        r = _CountingResolver([(None, "timeout", False)])
        c = self._cache("t5", r)
        rom = self._write("flaky.iso")
        first = c.get(rom)
        second = c.get(rom)
        self.assertIsNone(first["id"])
        self.assertFalse(first["final"])
        self.assertIsNone(second["id"])
        self.assertEqual(len(r.calls), 2)                     # retried both times

    def test_raising_resolver_does_not_propagate_or_persist(self):
        r = _CountingResolver(["raise"])
        c = self._cache("t6", r)
        rom = self._write("evil.iso")
        entry = c.get(rom)                                    # must not raise
        self.assertIsNone(entry["id"])
        self.assertEqual(entry["why"], "RuntimeError")
        self.assertFalse(entry["final"])
        c.get(rom)
        self.assertEqual(len(r.calls), 2)                     # not remembered -> retried

    def test_missing_file_returns_none_without_resolving(self):
        r = _CountingResolver([("X", "", True)])
        c = self._cache("t7", r)
        self.assertIsNone(c.get(self.tmp / "does-not-exist.iso"))
        self.assertEqual(r.calls, [])

    # ---- realpath keying ----
    def test_symlink_paths_share_one_cache_entry(self):
        r = _CountingResolver([("REAL1", "", True)])
        c = self._cache("t8", r)
        real = self._write("real.iso")
        link = self.tmp / "alias.iso"
        os.symlink(real, link)
        first = c.get(real)
        second = c.get(link)
        self.assertEqual(first["id"], "REAL1")
        self.assertEqual(second["id"], "REAL1")
        self.assertEqual(len(r.calls), 1)                     # resolved once, shared by both paths

    # ---- prune_missing ----
    def test_prune_missing_drops_gone_file(self):
        r = _CountingResolver([("A", "", True), ("B", "", True)])
        c = self._cache("t9", r)
        keep = self._write("keep.iso")
        gone = self._write("gone.iso")
        c.get(keep)
        c.get(gone)
        gone.unlink()
        dropped = c.prune_missing()
        self.assertEqual(dropped, 1)
        with c._lock:
            self.assertEqual(len(c._cache), 1)
            self.assertIn(os.path.realpath(str(keep)), c._cache)

    def test_prune_missing_prunes_nothing_when_all_gone(self):
        r = _CountingResolver([("A", "", True), ("B", "", True)])
        c = self._cache("t10", r)
        f1 = self._write("f1.iso")
        f2 = self._write("f2.iso")
        c.get(f1)
        c.get(f2)
        f1.unlink()
        f2.unlink()
        dropped = c.prune_missing()
        self.assertEqual(dropped, 0)                          # library unplugged, not emptied
        with c._lock:
            self.assertEqual(len(c._cache), 2)

    def test_prune_missing_empty_cache_is_noop(self):
        c = self._cache("t11", _CountingResolver([("A", "", True)]))
        self.assertEqual(c.prune_missing(), 0)

    # ---- get_many / budget ----
    def test_get_many_budget_partial_no_wrong_write(self):
        r = _CountingResolver([("CACHED", "", True)])
        c = self._cache("t12", r)
        cached_file = self._write("cached.iso")
        c.get(cached_file)                                    # pre-warm one entry (a real cache hit)
        r.calls.clear()
        new_file = self._write("new.iso")
        # budget=0: any elapsed time at all exhausts it, so the NEW file's resolution must
        # never be started; the pre-cached file is still served from cache (not budget-gated).
        out = c.get_many([cached_file, new_file], budget=0.0)
        cached_key = os.path.realpath(str(cached_file))
        new_key = os.path.realpath(str(new_file))
        self.assertEqual(out[cached_key]["id"], "CACHED")
        self.assertIsNone(out[new_key])                       # unstarted -> None, not a guessed answer
        self.assertEqual(r.calls, [])                         # resolver never invoked for the new file
        with c._lock:
            self.assertNotIn(new_key, c._cache)                # and nothing wrong was ever persisted for it

    def test_get_many_resolves_all_without_a_budget(self):
        r = _CountingResolver([("ID", "", True)])
        c = self._cache("t13", r)
        files = [self._write(f"g{i}.iso") for i in range(3)]
        out = c.get_many(files)
        for f in files:
            self.assertEqual(out[os.path.realpath(str(f))]["id"], "ID")
        self.assertEqual(len(r.calls), 3)

    def test_warm_returns_count_of_freshly_resolved(self):
        r = _CountingResolver([("ID", "", True)])
        c = self._cache("t14", r)
        files = [self._write(f"w{i}.iso") for i in range(3)]
        n = c.warm(files)
        self.assertEqual(n, 3)
        n2 = c.warm(files)                                    # all cached now -- nothing fresh
        self.assertEqual(n2, 0)

    # ---- concurrency ----
    def test_concurrent_get_many_does_not_corrupt_cache_file(self):
        def resolver(path):
            return (Path(path).name, "", True)
        c = self._cache("t15", resolver)
        files = [self._write(f"c{i}.iso") for i in range(10)]

        errors = []

        def worker():
            try:
                c.get_many(files, workers=4)
            except Exception as exc:                          # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        data = json.loads(c._path.read_text())                # must always be valid, complete JSON
        self.assertEqual(len(data), 10)
        for f in files:
            key = os.path.realpath(str(f))
            self.assertEqual(data[key]["id"], f.name)


if __name__ == "__main__":
    unittest.main()
