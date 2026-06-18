"""input_translate canonical-axis maps (Phase 2a — analog sticks + triggers). The
"axisname" capture mode emits "{sign}{canonical}" (e.g. "+left_x"); each emulator
maps the canonical name to its storage. No hardware.

Run:  python3 -m unittest tests.test_axis_remap -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import input_translate as t

CANON = ("left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right")


class ParseAxisToken(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(t.parse_axis_token("+left_x"), ("+", "left_x"))
        self.assertEqual(t.parse_axis_token("-trigger_right"), ("-", "trigger_right"))

    def test_rejects_rank_and_garbage(self):
        # crucially "+0"/"-3" are the OLD rank tokens (RetroArch's "axis" mode) — they
        # must NOT be accepted here, so the two capture modes can't cross-contaminate.
        for tok in ("", "+0", "-3", "left_x", "x", "+nope", "++left_x"):
            self.assertIsNone(t.parse_axis_token(tok), tok)

    def test_rank_suffix(self):
        # the @rank suffix (for Eden's raw joystick axis index) is stripped by
        # parse_axis_token and read by axis_token_rank; both forms accepted.
        self.assertEqual(t.parse_axis_token("+left_x@2"), ("+", "left_x"))
        self.assertEqual(t.axis_token_rank("+left_x@2"), 2)
        self.assertIsNone(t.axis_token_rank("+left_x"))
        self.assertIsNone(t.axis_token_rank("+left_x@x"))


class XemuAxis(unittest.TestCase):
    def test_index(self):
        self.assertEqual([t.xemu_axis_index(a) for a in CANON], [0, 1, 2, 3, 4, 5])

    def test_invert_from_directed_push(self):
        self.assertFalse(t.axis_invert("+", "left_y"))   # pushed the prompted way
        self.assertTrue(t.axis_invert("-", "left_y"))    # pushed opposite → invert

    def test_label(self):
        self.assertEqual(t.xemu_axis_label(0), "L-stick X")
        self.assertEqual(t.xemu_axis_label(4), "L trigger")


class Pcsx2Axis(unittest.TestCase):
    def test_source(self):
        self.assertEqual(t.pcsx2_axis_source("-", "left_y"), "-LeftY")
        self.assertEqual(t.pcsx2_axis_source("+", "right_x"), "+RightX")

    def test_trigger_always_positive(self):
        self.assertEqual(t.pcsx2_axis_source("-", "trigger_left"), "+LeftTrigger")

    def test_source_label(self):
        self.assertEqual(t.sdl_source_label("-LeftY"), "L-stick ↑")
        self.assertEqual(t.sdl_source_label("+RightX"), "R-stick →")


if __name__ == "__main__":
    unittest.main()
