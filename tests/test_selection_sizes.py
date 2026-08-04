"""granular.selection_sizes - the per-game + total footprint the backup picker shows under the art.

What it must guarantee, and why:
  * the number predicts the ACTION it sits next to. `keys` defaults to ("rom",) because the game cart's
    one consumer - granular.backup(category='roms') -> plan_selection - copies exactly one path per game,
    the ROM. Summing the wider asset allowlist promised a backup several GB larger than the button runs
    (review 2026-07-31). An unknown key is refused, never silently ignored;
  * emucfg + heavy Steam prefix/gamedir groups are SKIPPED AT THE SOURCE, not walked and discarded: a
    single Proton prefix is tens of GB, so walking one per game would blow the panel's 12 s call timeout;
  * ONE deadline is shared by the WHOLE selection. A per-game budget would let 50 games run 50x over
    that timeout. Games not reached come back size 0 + size_partial (honest "not counted yet"), and
    total_partial marks the total as a floor - never a silent 0 presented as fact;
  * a single unreadable game never sinks the call;
  * the same game ticked twice is counted once;
  * the deadline is SHARED - one instant for the call, passed to every game, not recomputed per game.

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
from lib.madsrv.rpc import RpcError        # noqa: E402


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
    def _call(self, items, resolver, budget=9.0, keys=None):
        params = {"items": items}
        if keys is not None:
            params["keys"] = keys
        with mock.patch.object(g.game_files, "resolve_game_assets", resolver), \
             mock.patch.object(g, "_display_name", lambda s, st: st.upper()), \
             mock.patch.object(g, "_SELECTION_SIZING_BUDGET_S", budget):
            return g._granular_selection_sizes(params)

    def test_default_sizes_the_rom_only_matching_what_the_cart_backs_up(self):
        """The cart's ONE consumer is granular.backup(category='roms') -> plan_selection, which copies a
        single path per game: the ROM. Summing media/saves/states here promised a backup several GB
        bigger than the button performs (review 2026-07-31), so 'rom' is the default."""
        out = self._call(
            [{"system": "nes", "stem": "smb"}],
            lambda *a, **k: _groups(rom=100, media=10, saves=5, states=2, lutriscfg=1, prefix=10_000))
        self.assertEqual(out["games"][0]["size"], 100, "ROM only - not media/saves/states")
        self.assertEqual(out["total"], 100)
        self.assertEqual(out["keys"], ["rom"], "the answer says what it measured")
        self.assertFalse(out["total_partial"])
        self.assertEqual((out["sized"], out["skipped"]), (1, 0))

    def test_explicit_keys_widen_it_for_the_asset_path(self):
        out = self._call(
            [{"system": "nes", "stem": "smb"}],
            lambda *a, **k: _groups(rom=100, media=10, saves=5, states=2, lutriscfg=1, prefix=10_000),
            keys=["rom", "media", "saves", "states", "lutriscfg"])
        self.assertEqual(out["total"], 118, "the heavy 'prefix' group is still never counted")

    def test_unknown_keys_are_refused_not_ignored(self):
        """A typo'd key must fail loudly - silently returning 0 for it would understate a total.
        ('prefix' is a LEGAL key since 2026-08-04: every game owns its per-appid Proton prefix now,
        so the Lutris-era shared-prefix double-count that once kept it out is gone.)"""
        with self.assertRaises(RpcError) as cm:
            self._call([{"system": "nes", "stem": "smb"}], lambda *a, **k: _groups(rom=1),
                       keys=["rom", "bogus"])
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_steam_heavy_keys_are_sizable_and_walked_only_when_named(self):
        """The steam picker's default selection includes prefix/gamedir (user 2026-08-04: the list
        showed 44 MB for a 23 GB game), so those keys must size - with the walk turned on ONLY for
        an item whose keys name them, mirroring granular_backup.plan_game_assets."""
        seen = {}

        def resolver(system, stem, **kw):
            seen.update(kw)
            return _groups(rom=100, prefix=10_000)

        out = self._call([{"system": "steam", "stem": "g"}], resolver,
                         keys=["rom", "prefix"])
        self.assertIs(seen.get("steam_heavy"), True)
        self.assertEqual(out["total"], 10_100)

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

    def test_the_deadline_is_shared_by_the_whole_selection(self):
        """A PER-GAME budget would pass every other test in this file, so pin the real contract: the
        deadline is one wall-clock instant for the call, and a game that eats it stops the ones after
        it. Here game 'a' burns the budget; 'b' and 'c' must come back unmeasured, not walked."""
        walked = []
        clock = {"t": 1000.0}

        def resolver(system, stem, **kw):
            walked.append(stem)
            clock["t"] += 100.0   # this one game consumed far more than the whole budget
            return _groups(rom=5)

        with mock.patch.object(g.time, "monotonic", lambda: clock["t"]), \
             mock.patch.object(g.game_files, "resolve_game_assets", resolver), \
             mock.patch.object(g, "_display_name", lambda s, st: st.upper()), \
             mock.patch.object(g, "_SELECTION_SIZING_BUDGET_S", 9.0):
            out = g._granular_selection_sizes(
                {"items": [{"system": "nes", "stem": s} for s in ("a", "b", "c")]})
        self.assertEqual(walked, ["a"], "the budget is spent once, not once per game")
        self.assertEqual(out["sized"], 1)
        self.assertEqual(out["skipped"], 2)
        self.assertEqual(out["total"], 5)
        self.assertTrue(out["total_partial"])
        self.assertEqual([r["stem"] for r in out["games"]], ["a", "b", "c"], "all still listed")

    def test_the_deadline_instant_is_passed_down_not_recomputed(self):
        """Every game must be handed the SAME deadline value - a per-game 'now + budget' would let each
        game start its own fresh 9 s."""
        seen = []
        clock = {"t": 500.0}

        def resolver(system, stem, **kw):
            seen.append(kw.get("deadline"))
            clock["t"] += 1.0     # time passes, but the deadline must not move with it
            return _groups(rom=1)

        with mock.patch.object(g.time, "monotonic", lambda: clock["t"]), \
             mock.patch.object(g.game_files, "resolve_game_assets", resolver), \
             mock.patch.object(g, "_display_name", lambda s, st: st.upper()), \
             mock.patch.object(g, "_SELECTION_SIZING_BUDGET_S", 9.0):
            g._granular_selection_sizes(
                {"items": [{"system": "nes", "stem": s} for s in ("a", "b", "c")]})
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 1, f"one shared deadline instant, got {seen}")
        self.assertEqual(seen[0], 509.0, "start + budget, fixed at entry")

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
                               "sized": 0, "skipped": 0, "keys": ["rom"]})


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
