"""Item-count progress for a per-set cloud push, and its precedence over rclone byte stats.

WHY THIS EXISTS. On 2026-08-13 a fully successful games backup (203 files, 0 failed, rc 0) showed
a progress bar pinned at 0 percent for a minute and read as a failure. Replaying the job's real
output through the old parser produced the sequence 0, 0, ..., 57, ..., 0, ..., 100, ..., 0: it
could never climb. Two causes compounded, and both are permanent properties of that code path:

  * a set whose files are already on MEGA transfers ZERO bytes (rclone copy is a per-file CHECK
    sweep), so totalBytes stays 0 and there is nothing to derive a percentage from;
  * _push_set runs ONE rclone per plan entry, so each stats blob describes only its own sub-run
    (6 of 6 reads as 100) and resets to 0 of 0 between entries.

So deck-cloud.sh now announces "MAD_SET_PROGRESS done=N total=M name=X" per entry, and that WINS
over byte stats for the rest of the job. These tests pin the parse, the arithmetic, the precedence
in all three consumers, and the hostile inputs a ROM name can produce.

Run:  python3 -m unittest tests.test_cloud_item_progress -v
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cloud_cmds as cc

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "deck-cloud.sh"

# A real rclone stats blob from the 2026-08-13 games push: everything zero, because the set was
# already in sync. This is the line that used to drive the bar back to 0.
ZERO_STATS = json.dumps({
    "time": "2026-08-13T21:56:57Z", "level": "notice", "msg": "stats",
    "stats": {"bytes": 0, "totalBytes": 0, "checks": 0, "totalChecks": 0, "transfers": 0,
              "speed": 0, "eta": None, "transferring": []},
})
SUBRUN_STATS = json.dumps({          # a single entry's own sub-run: reads as 100 percent
    "time": "2026-08-13T21:55:58Z", "level": "notice", "msg": "stats",
    "stats": {"bytes": 0, "totalBytes": 0, "checks": 6, "totalChecks": 6, "transfers": 0,
              "speed": 0, "eta": None, "transferring": []},
})


class ParseTheMarker(unittest.TestCase):
    def test_percentage_is_items_done_over_total(self):
        prog, summary = cc._parse_progress("MAD_SET_PROGRESS done=50 total=200 name=disc.daphne")
        self.assertEqual(prog["overall_pct"], 25)
        self.assertEqual((prog["items_done"], prog["items_total"]), (50, 200))
        self.assertEqual(prog["item"], "disc.daphne")
        self.assertIn("50/200 files", summary)

    def test_it_starts_at_zero_and_reaches_exactly_100(self):
        first, _ = cc._parse_progress("MAD_SET_PROGRESS done=0 total=203 name=a")
        last, _ = cc._parse_progress("MAD_SET_PROGRESS done=203 total=203 name=")
        self.assertEqual(first["overall_pct"], 0)
        self.assertEqual(last["overall_pct"], 100)

    def test_the_raw_marker_is_never_shown_as_a_log_line(self):
        _prog, summary = cc._parse_progress("MAD_SET_PROGRESS done=1 total=2 name=x")
        self.assertNotIn("MAD_SET_PROGRESS", summary)

    def test_a_name_containing_spaces_survives(self):
        # name= is LAST and parsed with a bounded split precisely so a spaced ROM name is intact.
        prog, _ = cc._parse_progress("MAD_SET_PROGRESS done=1 total=2 name=Dragon's Lair (US).daphne")
        self.assertEqual(prog["item"], "Dragon's Lair (US).daphne")

    def test_an_empty_name_is_fine(self):
        prog, summary = cc._parse_progress("MAD_SET_PROGRESS done=2 total=2 name=")
        self.assertEqual(prog["item"], "")
        self.assertTrue(summary.startswith("100%"))

    def test_malformed_or_zero_total_never_raises_and_never_leaks(self):
        for bad in ("MAD_SET_PROGRESS done=x total=y name=z",
                    "MAD_SET_PROGRESS done=1 total=0 name=z",
                    "MAD_SET_PROGRESS "):
            prog, summary = cc._parse_progress(bad)
            if prog is not None:
                self.assertEqual(prog["overall_pct"], 0)
            if summary:
                self.assertNotIn("MAD_SET_PROGRESS", summary)

    def test_byte_stats_still_work_when_there_is_no_item_marker(self):
        # Tier A pushes (push-precious) do not use _push_set, so the byte path must be untouched.
        blob = json.dumps({"stats": {"bytes": 50, "totalBytes": 100, "checks": 0,
                                     "totalChecks": 0, "transfers": 1, "speed": 10,
                                     "eta": 5, "transferring": []}})
        prog, summary = cc._parse_progress(blob)
        self.assertEqual(prog["overall_pct"], 50)
        self.assertNotIn("items_total", prog)
        self.assertIn("50%", summary)


class _Sink(cc._JobTailStream):
    """A stream that records events instead of sending them."""
    def __init__(self):
        super().__init__("job-test")
        self.events = []

    def emit(self, d):
        self.events.append(d)

    def pcts(self):
        return [e["progress"]["overall_pct"] for e in self.events if "progress" in e]


class PrecedenceInTheLiveStream(unittest.TestCase):
    def test_byte_stats_cannot_drag_the_bar_back_to_zero(self):
        s = _Sink()
        s._emit_line("MAD_SET_PROGRESS done=100 total=200 name=a")   # 50 percent, truthful
        s._emit_line(ZERO_STATS)                                     # would have been 0
        s._emit_line(SUBRUN_STATS)                                   # would have been 100
        s._emit_line("MAD_SET_PROGRESS done=150 total=200 name=b")   # 75 percent
        self.assertEqual(s.pcts(), [50, 75], "byte stats must not move the bar once items lead")

    def test_the_bar_only_ever_climbs_over_a_whole_run(self):
        s = _Sink()
        total = 203
        for i in range(total):
            s._emit_line(f"MAD_SET_PROGRESS done={i} total={total} name=f{i}")
            s._emit_line(ZERO_STATS)          # the noise that used to reset it
        s._emit_line(f"MAD_SET_PROGRESS done={total} total={total} name=")
        pcts = s.pcts()
        self.assertEqual(pcts, sorted(pcts), "progress must be monotonic")
        self.assertEqual((pcts[0], pcts[-1]), (0, 100))

    def test_a_push_with_no_item_markers_still_shows_byte_progress(self):
        s = _Sink()
        s._emit_line(json.dumps({"stats": {"bytes": 25, "totalBytes": 100, "checks": 0,
                                           "totalChecks": 0, "transfers": 1, "speed": 1,
                                           "eta": None, "transferring": []}}))
        self.assertEqual(s.pcts(), [25])


class PrecedenceInTheTransfersTile(unittest.TestCase):
    """_tail_progress feeds the Transfers tile and the re-attach replay."""

    def _tail(self, lines):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "j.out"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")

        class _Reg:
            @staticmethod
            def out_path(_job):
                return out
        real = cc._registry
        cc._registry = lambda: _Reg
        self.addCleanup(lambda: setattr(cc, "_registry", real))
        return cc._tail_progress("j")

    def test_the_newest_item_marker_wins_over_a_later_byte_blob(self):
        prog, summary = self._tail(["MAD_SET_PROGRESS done=40 total=200 name=a", ZERO_STATS])
        self.assertEqual(prog["overall_pct"], 20)
        self.assertIn("40/200 files", summary)

    def test_it_falls_back_to_byte_stats_when_there_is_no_marker(self):
        prog, _ = self._tail([json.dumps({"stats": {"bytes": 30, "totalBytes": 60, "checks": 0,
                                                    "totalChecks": 0, "transfers": 0, "speed": 0,
                                                    "eta": None, "transferring": []}})])
        self.assertEqual(prog["overall_pct"], 50)

    def test_no_progress_at_all_is_still_none(self):
        self.assertEqual(self._tail(["[cloud 21:00:00] starting"]), (None, None))


class TheEngineActuallyEmitsIt(unittest.TestCase):
    """Pins the shell contract: deck-cloud.sh must count the plan and announce every entry.

    The plan file is NUL-delimited src/rel PAIRS, so the total is NULs/2. Getting that wrong by a
    factor of two is the obvious way to ship a bar that stops at 50 percent, and it would not show
    up in any Python test.
    """

    def test_the_total_is_pairs_not_records(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plan = Path(tmp.name) / "plan"
        # three src/rel PAIRS = six NUL-terminated records
        plan.write_bytes(b"".join(f"/src/{i}\0games/{i}\0".encode() for i in range(3)))
        script = f'total_n=$(( $(tr -cd "\\0" < "{plan}" | wc -c) / 2 )); echo "$total_n"'
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "3", out.stderr)

    def test_the_engine_announces_before_each_entry_and_a_final_100(self):
        src = ENGINE.read_text(encoding="utf-8")
        self.assertIn("MAD_SET_PROGRESS", src)
        # One inside the loop (before the copy) and one after it, or the bar stops an entry short.
        self.assertEqual(src.count("printf 'MAD_SET_PROGRESS"), 2,
                         "expected exactly two emitters: per-entry and the final 100 percent")

    def test_the_name_cannot_break_the_line_protocol(self):
        # A ROM name may legally contain a newline (the plan format exists to survive that), which
        # would split the record in half and desynchronise the reader.
        script = ('rel=$(printf "roms/a\\nb.zip"); '
                  'iname="$(basename -- "$rel" | tr -d "\\n\\r" | cut -c1-80)"; '
                  'printf "MAD_SET_PROGRESS done=1 total=2 name=%s\\n" "$iname"')
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(len(out.stdout.strip().splitlines()), 1, "must stay one line")
        prog, _ = cc._parse_progress(out.stdout.strip())
        self.assertEqual(prog["items_total"], 2)


if __name__ == "__main__":
    unittest.main()
