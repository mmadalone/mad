"""
Golden-parity tests for the MAD menu-tree: every surface's emitted JSON must be
byte-identical to the captured golden. After the descriptor/projector refactor
(P1-P4) these must all pass with the goldens UNCHANGED (proof of no behaviour
change) — except intended label/structure changes, whose goldens are re-captured
(`python3 -m tests.capture_menu_golden`) and reviewed in the diff.

Run:  python3 -m unittest tests.test_menu_golden -v
Hermetic + CI-safe: everything is stubbed, so it passes on a bare runner too.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lib.madsrv import onthego_cmds as og
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import standalones_cmds as sc
from tests._ci import skip_on_ci
from tests._menu_capture import enumerate_cases, serialize

GOLD = Path(__file__).parent / "golden" / "menu"

# Built once at import (mirrors test_golden.py's dynamic-method pattern). Each
# tree is already normalized; serialize() is deterministic.
_CASES = list(enumerate_cases())


class MenuGolden(unittest.TestCase):
    pass


def _make(case_id, tree):
    def test(self):
        gp = GOLD / f"{case_id}.json"
        self.assertTrue(
            gp.exists(),
            f"missing golden {gp} — run `python3 -m tests.capture_menu_golden`")
        self.assertMultiLineEqual(
            serialize(tree), gp.read_text(encoding="utf-8"),
            f"menu/{case_id} drifted from golden")
    return test


for _cid, _tree in _CASES:
    setattr(MenuGolden, f"test_{_cid}", _make(_cid, _tree))


class MenuGoldenParity(unittest.TestCase):
    """Per-tile goldens silently ignore a deleted/renamed tile; assert the golden
    set matches the emitted case set so an add/remove/rename fails loudly."""

    def test_golden_set_matches_emitted_cases(self):
        want = {cid for cid, _ in _CASES}
        have = {p.stem for p in GOLD.glob("*.json")}
        self.assertEqual(
            have, want,
            "golden/menu set != emitted cases — a tile was added/removed/renamed; "
            "re-run `python3 -m tests.capture_menu_golden` and review the diff")


class MenuLiveSmoke(unittest.TestCase):
    """A stub could mask a real on-Deck crash. Call the three builders UNSTUBBED
    against live Deck state and assert they return well-formed trees without
    raising (no golden compare — live output is machine-specific). Skipped on CI,
    which has none of that state."""

    @skip_on_ci
    def test_builders_do_not_raise_unstubbed(self):
        self.assertIsInstance(rs._ra_hub_tiles(), list)
        sa = sc._standalones_list({})
        self.assertIsInstance(sa.get("tiles"), list)
        og_tile = og._list({})
        self.assertIsInstance(og_tile.get("tiles"), list)


class MenuNoEmDash(unittest.TestCase):
    """P1 welded every menu title/label to plain ASCII (no em/en-dashes or arrows), per
    the no-em-dashes standing rule. This guards against any of those characters creeping
    back into an emitted menu string in ANY builder, present or future — a stronger,
    builder-agnostic guard than routing each title through mad_tree.title()."""

    FORBIDDEN = "—–→←↑↓"   # em-dash en-dash and the four arrows

    def test_no_dashes_or_arrows_in_any_emitted_menu(self):
        for case_id, tree in _CASES:
            blob = serialize(tree)
            bad = sorted({c for c in self.FORBIDDEN if c in blob})
            self.assertEqual(
                bad, [],
                f"menu/{case_id} emits forbidden character(s) {bad!r} — use plain ASCII "
                f"(compose titles via mad_tree.title / mad_tree.SEP)")


if __name__ == "__main__":
    unittest.main()
