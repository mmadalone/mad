"""granular.system_sizes - the EXACT per-system sizing that drives the backup picker's totals.

Miquel chose exact over approximate, which rules out the bounded 9 s call: the largest system on this
Deck (fba, 1828 games) needs 51 s to measure honestly. So this streams progress while it walks, and
caches per game so the cost is paid once:

  * it is NOT a _GranularStream - no transfer-job registration and no _GRAN_ACTIVE. A measurement must
    not block a backup, must not be blocked by one, and has no business in the Transfers tile;
  * progress arrives in batches as it goes, and the final total equals the sum of the parts it
    reported - a total that disagreed with its own rows would be worse than no total;
  * the cache makes a re-walk free (2 ms for fba against 51 s cold), which is what lets the panel do
    arithmetic on every tick instead of re-measuring;
  * it is cancellable - leaving the page must not leave a thread walking 1828 games;
  * one unreadable game costs that game, not the run.

Run:  python3 -m unittest tests.test_system_sizes -v
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import granular_cmds as g   # noqa: E402
from lib.madsrv.rpc import RpcError         # noqa: E402


def _groups(rom=0, media=0, saves=0):
    out = []
    for key, size in (("rom", rom), ("media", media), ("saves", saves)):
        out.append({"key": key, "label": key.title(), "present": size > 0, "size": size,
                    "files": [{"src": f"/x/{key}", "rel": key, "kind": "file"}] * (1 if size else 0)})
    return out


class _Collect(g._SizingStream):
    """Capture emitted events instead of pushing them at a panel."""

    def __init__(self, system, games):
        super().__init__(system, games)
        self.events = []
        self.finished = threading.Event()

    def emit(self, ev):
        self.events.append(ev)
        if ev.get("done"):
            self.finished.set()

    def drain(self, timeout=30):
        self.start()
        self.assertDone = self.finished.wait(timeout)
        return self.events


class SystemSizes(unittest.TestCase):
    def setUp(self):
        g._SIZE_CACHE.clear()
        self._sizes = {"a": 100, "b": 200, "c": 300}
        self._resolver = mock.patch.object(
            g.game_files, "resolve_game_assets",
            lambda system, stem, **k: _groups(rom=self._sizes.get(stem, 0), media=1))
        self._resolver.start()
        self._name = mock.patch.object(g, "_display_name", lambda s, st: st.upper())
        self._name.start()

    def tearDown(self):
        self._resolver.stop()
        self._name.stop()
        g._SIZE_CACHE.clear()

    def test_streams_every_game_and_a_matching_total(self):
        s = _Collect("nes", ["a", "b", "c"])
        events = s.drain()
        self.assertTrue(s.finished.is_set(), "the stream must always terminate")
        games = [gm for ev in events if "games" in ev for gm in ev["games"]]
        self.assertEqual([gm["stem"] for gm in games], ["a", "b", "c"])
        self.assertEqual([gm["name"] for gm in games], ["A", "B", "C"])
        final = events[-1]
        self.assertTrue(final["done"])
        self.assertEqual(final["rc"], 0)
        self.assertEqual(final["count"], 3)
        self.assertEqual(final["total"], sum(gm["total"] for gm in games),
                         "the headline total must equal the rows it reported")
        self.assertEqual(final["total"], 100 + 200 + 300 + 3)   # +1 media each

    def test_progress_counts_up_to_the_total(self):
        with mock.patch.object(g, "_SIZE_BATCH", 2):
            s = _Collect("nes", ["a", "b", "c"])
            events = s.drain()
        prog = [(ev["done_n"], ev["total_n"]) for ev in events if "done_n" in ev]
        self.assertEqual(prog, [(2, 3), (3, 3)], "batched progress, last batch flushed")
        running = [ev["running_total"] for ev in events if "running_total" in ev]
        self.assertEqual(running, sorted(running), "the running total only grows")

    def test_per_asset_rows_come_through(self):
        s = _Collect("nes", ["a"])
        events = s.drain()
        game = [gm for ev in events if "games" in ev for gm in ev["games"]][0]
        by = {a["key"]: a for a in game["assets"]}
        self.assertEqual(by["rom"]["size"], 100)
        self.assertEqual(by["rom"]["label"], "Rom", "the label the panel prints")
        self.assertEqual(by["media"]["count"], 1)
        self.assertNotIn("saves", by, "an absent asset is not listed")

    def test_the_cache_makes_a_second_walk_free(self):
        calls = []

        def counting(system, stem, **k):
            calls.append(stem)
            return _groups(rom=self._sizes.get(stem, 0))

        with mock.patch.object(g.game_files, "resolve_game_assets", counting):
            _Collect("nes", ["a", "b"]).drain()
            self.assertEqual(sorted(calls), ["a", "b"])
            second = _Collect("nes", ["a", "b"]).drain()
        self.assertEqual(sorted(calls), ["a", "b"], "the second walk measured nothing again")
        self.assertEqual(second[-1]["total"], 300, "and still reports the same total")

    def test_cancelling_stops_the_walk(self):
        seen = []

        def slow(system, stem, **k):
            seen.append(stem)
            return _groups(rom=1)

        with mock.patch.object(g.game_files, "resolve_game_assets", slow):
            s = _Collect("nes", [f"g{i}" for i in range(50)])
            s.stopped.set()          # cancelled before it starts walking
            s.drain()
        self.assertLessEqual(len(seen), 1, "a cancelled walk must not grind through the system")
        self.assertTrue(s.events[-1].get("stopped"), "and it says so")

    def test_one_unreadable_game_costs_only_that_game(self):
        def flaky(system, stem, **k):
            if stem == "b":
                raise OSError("boom")
            return _groups(rom=10)

        with mock.patch.object(g.game_files, "resolve_game_assets", flaky):
            events = _Collect("nes", ["a", "b", "c"]).drain()
        self.assertEqual(events[-1]["rc"], 0, "the run still completes")
        games = {gm["stem"]: gm for ev in events if "games" in ev for gm in ev["games"]}
        self.assertEqual(games["b"]["assets"], [], "the unreadable one is empty, not missing")
        self.assertEqual(events[-1]["total"], 20)

    def test_rpc_requires_a_system(self):
        with self.assertRaises(RpcError) as cm:
            g._granular_system_sizes({})
        self.assertEqual(cm.exception.code, "EINVAL")


class PersistedTicks(unittest.TestCase):
    """The backup picker's exclusions live on DISK: curating a big library is only worth doing if it
    survives quitting ES-DE. Only exclusions are stored, so an untouched system has no entry at all
    and the "everything is ticked" default cannot drift from the games actually present."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="ticks-"))
        self._patch = mock.patch.object(g, "_ticks_path", lambda: self.tmp / "backup-ticks.json")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self):
        g._granular_set_ticks({"system": "snes", "games": ["Aladdin"],
                               "assets": {"ActRaiser": ["media", "states"]}})
        out = g._granular_ticks({"system": "snes"})
        self.assertEqual(out["games"], ["Aladdin"])
        self.assertEqual(out["assets"], {"ActRaiser": ["media", "states"]})

    def test_an_untouched_system_has_nothing_saved(self):
        self.assertEqual(g._granular_ticks({"system": "nes"}), {"system": "nes", "games": [],
                                                                "assets": {}})

    def test_systems_do_not_bleed_into_each_other(self):
        g._granular_set_ticks({"system": "snes", "games": ["A"], "assets": {}})
        g._granular_set_ticks({"system": "nes", "games": ["B"], "assets": {}})
        self.assertEqual(g._granular_ticks({"system": "snes"})["games"], ["A"])
        self.assertEqual(g._granular_ticks({"system": "nes"})["games"], ["B"])

    def test_clearing_removes_the_entry_rather_than_storing_an_empty_one(self):
        g._granular_set_ticks({"system": "snes", "games": ["A"], "assets": {}})
        g._granular_set_ticks({"system": "snes", "games": [], "assets": {}})
        import json as _json
        self.assertEqual(_json.loads((self.tmp / "backup-ticks.json").read_text()), {},
                         "'untouched' must stay distinguishable from 'everything ticked'")

    def test_a_corrupt_file_reads_as_no_exclusions(self):
        (self.tmp / "backup-ticks.json").write_text("{not json")
        self.assertEqual(g._granular_ticks({"system": "snes"})["games"], [],
                         "a damaged file must not make a backup silently skip games")

    def test_garbage_entries_are_filtered(self):
        (self.tmp / "backup-ticks.json").write_text(
            '{"snes": {"games": ["ok", 7, null], "assets": {"g": ["media", 3], "bad": "nope"}}}')
        out = g._granular_ticks({"system": "snes"})
        self.assertEqual(out["games"], ["ok"])
        self.assertEqual(out["assets"], {"g": ["media"]})

    def test_system_is_required(self):
        for fn in (g._granular_ticks, g._granular_set_ticks):
            with self.assertRaises(RpcError):
                fn({})


class SelectionSizesPerItemKeys(unittest.TestCase):
    """The picker remembers a DIFFERENT tick set per game, so one key set per call cannot describe it."""

    def _call(self, items, **kw):
        with mock.patch.object(g.game_files, "resolve_game_assets",
                               lambda s, st, **k: _groups(rom=100, media=10, saves=5)), \
             mock.patch.object(g, "_display_name", lambda s, st: st):
            return g._granular_selection_sizes({"items": items, **kw})

    def test_each_item_can_carry_its_own_keys(self):
        out = self._call([{"system": "nes", "stem": "a", "keys": ["rom"]},
                          {"system": "nes", "stem": "b", "keys": ["rom", "media"]},
                          {"system": "nes", "stem": "c", "keys": ["media", "saves"]}])
        self.assertEqual([r["size"] for r in out["games"]], [100, 110, 15])
        self.assertEqual(out["total"], 225)

    def test_an_item_without_keys_falls_back_to_the_call_level_set(self):
        out = self._call([{"system": "nes", "stem": "a"}], keys=["rom", "media"])
        self.assertEqual(out["games"][0]["size"], 110)

    def test_a_bad_key_on_one_item_is_refused(self):
        # "prefix" became a legal (sizable) key on 2026-08-04; a genuinely unknown
        # key must still refuse loudly.
        with self.assertRaises(RpcError) as cm:
            self._call([{"system": "nes", "stem": "a", "keys": ["bogus"]}])
        self.assertEqual(cm.exception.code, "EINVAL")


if __name__ == "__main__":
    unittest.main()
