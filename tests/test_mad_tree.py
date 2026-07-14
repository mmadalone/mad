"""Unit tests for lib/madsrv/mad_tree.py helpers.

mad_tree holds the shared vocabulary + pure helpers the per-emu menu builders in
standalones_cmds.py route through. These tests pin the behaviour the golden-parity suite
can only imply: a golden diff shows THAT bytes moved, this proves WHY they moved (and,
for section_order, that the helper never wraps a row in a group -- which _collapse_singletons
would silently rename).

Run:  python3 -m unittest tests.test_mad_tree -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import mad_tree


class Title(unittest.TestCase):
    def test_single_ascii_separator(self):
        self.assertEqual(mad_tree.title("PlayStation 2", "Graphics"),
                         "PlayStation 2 - Graphics")
        # the separator is welded once so it can never drift back to an em-dash
        self.assertNotIn("—", mad_tree.title("x", "y"))
        self.assertNotIn("–", mad_tree.title("x", "y"))


class PergameMenu(unittest.TestCase):
    def test_wrapper_shape_and_verbatim_leaves(self):
        leaves = [{"label": "Audio", "kind": "pergame_settings", "arg": "x_audio"}]
        row = mad_tree.pergame_menu("Citron", "citron", leaves)
        self.assertEqual(row["kind"], "settings_pergame_menu")
        self.assertEqual(row["arg"], "citron")
        self.assertEqual(row["label"], mad_tree.L.PERGAME)
        self.assertEqual(row["title"], "Citron - Per-game settings")
        # leaves pass through by identity -- per-game trees are frozen, never rewritten here
        self.assertIs(row["sections"], leaves)


class SectionOrder(unittest.TestCase):
    """The canonical Switch-emu reorderer: System, Video, Audio, Input, <extras>, Per-game."""

    @staticmethod
    def _rows(*names):
        # distinct row objects, so identity + order are both checkable
        return {n: {"label": n, "kind": "settings", "arg": n} for n in names}

    def test_canonical_order(self):
        r = self._rows("sys", "vid", "aud", "inp", "pg")
        out = mad_tree.section_order(system=r["sys"], video=r["vid"], audio=r["aud"],
                                     inp=r["inp"], pergame=r["pg"])
        self.assertEqual([s["label"] for s in out], ["sys", "vid", "aud", "inp", "pg"])

    def test_audio_precedes_input(self):
        # the actual P3 invariant: Audio comes BEFORE Input regardless of kwarg call order
        r = self._rows("aud", "inp")
        out = mad_tree.section_order(inp=r["inp"], audio=r["aud"])
        self.assertEqual([s["label"] for s in out], ["aud", "inp"])

    def test_none_slots_vanish(self):
        r = self._rows("sys", "aud", "pg")
        out = mad_tree.section_order(system=r["sys"], audio=r["aud"], pergame=r["pg"])
        self.assertEqual([s["label"] for s in out], ["sys", "aud", "pg"])

    def test_extras_sit_between_input_and_pergame(self):
        r = self._rows("sys", "inp", "x1", "x2", "pg")
        out = mad_tree.section_order(system=r["sys"], inp=r["inp"],
                                     extras=[r["x1"], r["x2"]], pergame=r["pg"])
        self.assertEqual([s["label"] for s in out], ["sys", "inp", "x1", "x2", "pg"])

    def test_empty_returns_empty(self):
        self.assertEqual(mad_tree.section_order(), [])

    def test_rows_pass_through_by_identity_never_wrapped(self):
        # The critical guard: section_order must NEVER construct a group around a row (that
        # would trip _collapse_singletons' parent-label adoption and rename it). It returns
        # the SAME objects, only reordered -- never a copy, never a wrapper.
        r = self._rows("sys", "pg")
        out = mad_tree.section_order(system=r["sys"], pergame=r["pg"])
        self.assertIs(out[0], r["sys"])
        self.assertIs(out[-1], r["pg"])


if __name__ == "__main__":
    unittest.main()
