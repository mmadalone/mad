"""granular.selection_sizes - the per-game + total footprint the backup picker shows under the art.

What it must guarantee, and why:
  * the per-game number is the REAL backup footprint - the fixed _ALL_ASSET_KEYS allowlist (rom, media,
    saves, states, lutriscfg), the same set an "All" backup expands to - so the total matches what an
    upload actually moves;
  * emucfg + heavy Steam prefix/gamedir groups are SKIPPED AT THE SOURCE, not walked and discarded: a
    single Proton prefix is tens of GB, so walking one per game would blow the panel's 12 s call timeout;
  * ONE deadline is shared by the WHOLE selection. A per-game budget would let 50 games run 50x over
    that timeout. Games not reached come back size 0 + size_partial (honest "not counted yet"), and
    total_partial marks the total as a floor - never a silent 0 presented as fact;
  * a single unreadable game never sinks the call;
  * the same game ticked twice is counted once.

Run:  python3 -m unittest tests.test_selection_sizes -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import granular_cmds as g   # noqa: E402


def _groups(rom=0, media=0, saves=0, states=0, lutriscfg=0, prefix=0, partial_keys=()):
    """Canned resolve_game_assets output: the allowlisted keys plus a heavy 'prefix' group that must
    NEVER be counted (it is not in _ALL_ASSET_KEYS)."""
    out = []
    for key, size in (("rom", rom), ("media", media), ("saves", saves),
                      ("states", states), ("lutriscfg", lutriscfg), ("prefix", prefix)):
        grp = {"key": key, "label": key, "category": key, "present": size > 0,
               "size": size, "files": []}
        if key in partial_keys:
            grp["size_partial"] = True
        out.append(grp)
    return out


class SelectionSizes(unittest.TestCase):
    def _call(self, items, resolver, budget=9.0):
        with mock.patch.object(g.game_files, "resolve_game_assets", resolver), \
             mock.patch.object(g, "_display_name", lambda s, st: st.upper()), \
             mock.patch.object(g, "_SELECTION_SIZING_BUDGET_S", budget):
            return g._granular_selection_sizes({"items": items})

    def test_sums_the_allowlist_and_ignores_heavy_groups(self):
        out = self._call(
            [{"system": "nes", "stem": "smb"}],
            lambda *a, **k: _groups(rom=100, media=10, saves=5, states=2, lutriscfg=1, prefix=10_000))
        self.assertEqual(out["games"][0]["size"], 118, "rom+media+saves+states+lutriscfg only")
        self.assertEqual(out["total"], 118, "the 10 KB 'prefix' group must not be counted")
        self.assertFalse(out["total_partial"])
        self.assertEqual((out["sized"], out["skipped"]), (1, 0))

    def test_skips_the_expensive_walks_at_the_source(self):
        """emucfg + steam_heavy must be turned OFF in the call, not filtered after the fact - the cost
        is in the walking, and those groups can never contribute to the allowlisted total anyway."""
        seen = {}

        def resolver(system, stem, **kw):
            seen.update(kw)
            return _groups(rom=1)

        self._call([{"system": "steam", "stem": "g"}], resolver)
        self.assertIs(seen.get("emucfg"), False)
        self.assertIs(seen.get("steam_heavy"), False)
        self.assertIsNotNone(seen.get("deadline"), "the shared deadline must reach the resolver")

    def test_totals_and_per_game_rows(self):
        sizes = {"a": 10, "b": 20, "c": 30}
        out = self._call(
            [{"system": "nes", "stem": s} for s in ("a", "b", "c")],
            lambda system, stem, **k: _groups(rom=sizes[stem]))
        self.assertEqual([r["stem"] for r in out["games"]], ["a", "b", "c"], "input order preserved")
        self.assertEqual([r["size"] for r in out["games"]], [10, 20, 30])
        self.assertEqual([r["name"] for r in out["games"]], ["A", "B", "C"])
        self.assertEqual(out["total"], 60)

    def test_a_duplicate_tick_counts_once(self):
        out = self._call(
            [{"system": "nes", "stem": "smb"}, {"system": "nes", "stem": "smb"}],
            lambda *a, **k: _groups(rom=42))
        self.assertEqual(len(out["games"]), 1)
        self.assertEqual(out["total"], 42)

    def test_partial_group_marks_the_game_and_the_total(self):
        out = self._call([{"system": "nes", "stem": "smb"}],
                         lambda *a, **k: _groups(rom=7, partial_keys=("rom",)))
        self.assertTrue(out["games"][0]["size_partial"])
        self.assertTrue(out["total_partial"], "a partial game makes the TOTAL a floor")
        self.assertEqual(out["total"], 7, "what was counted still counts")

    def test_expired_budget_reports_unmeasured_games_honestly(self):
        """The whole point of the shared deadline: with none left, every game is still LISTED, each
        flagged partial with size 0 - not silently dropped, and not claimed to be empty."""
        calls = []

        def resolver(*a, **k):
            calls.append(a)
            return _groups(rom=999)

        out = self._call([{"system": "nes", "stem": s} for s in ("a", "b")], resolver, budget=-1.0)
        self.assertEqual(calls, [], "an expired budget must not walk anything")
        self.assertEqual(len(out["games"]), 2)
        self.assertTrue(all(r["size_partial"] and r["size"] == 0 for r in out["games"]))
        self.assertTrue(out["total_partial"])
        self.assertEqual((out["sized"], out["skipped"]), (0, 2))

    def test_one_unreadable_game_does_not_sink_the_selection(self):
        def resolver(system, stem, **k):
            if stem == "bad":
                raise OSError("boom")
            return _groups(rom=5)

        out = self._call([{"system": "nes", "stem": s} for s in ("ok", "bad", "ok2")], resolver)
        by = {r["stem"]: r for r in out["games"]}
        self.assertEqual(by["ok"]["size"], 5)
        self.assertEqual(by["ok2"]["size"], 5)
        self.assertTrue(by["bad"]["size_partial"], "unmeasurable is partial, not a confident 0")
        self.assertEqual(by["bad"]["size"], 0)
        self.assertEqual(out["total"], 10)
        self.assertTrue(out["total_partial"])

    def test_malformed_items_are_ignored(self):
        out = self._call([{}, {"system": "nes"}, {"stem": "orphan"}, None,
                          {"system": "nes", "stem": "good"}],
                         lambda *a, **k: _groups(rom=3))
        self.assertEqual([r["stem"] for r in out["games"]], ["good"])
        self.assertEqual(out["total"], 3)

    def test_empty_selection_is_a_clean_zero(self):
        out = self._call([], lambda *a, **k: _groups(rom=1))
        self.assertEqual(out, {"games": [], "total": 0, "total_partial": False,
                               "sized": 0, "skipped": 0})


class SelectionSizesLive(unittest.TestCase):
    """Against the REAL resolver (no mocks): the RPC must never raise, whatever the library looks like."""

    def test_never_raises_on_a_nonexistent_game(self):
        out = g._granular_selection_sizes({"items": [{"system": "nes", "stem": "no-such-game"}]})
        self.assertEqual(len(out["games"]), 1)
        self.assertIsInstance(out["total"], int)

    def test_missing_params_are_a_clean_empty(self):
        self.assertEqual(g._granular_selection_sizes({})["total"], 0)
        self.assertEqual(g._granular_selection_sizes(None)["games"], [])


if __name__ == "__main__":
    unittest.main()
