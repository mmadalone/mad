"""input_translate d-pad hat→token maps (d-pad remapping, Phase 1). The capture
engine returns a single d-pad direction as "h<N><dir>"; each emulator stores the
d-pad differently. No hardware.

Run:  python3 -m unittest tests.test_dpad_remap -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import input_translate as t

DIRS = ("h0up", "h0down", "h0left", "h0right")


class HatDirection(unittest.TestCase):
    def test_directions_any_hat_number(self):
        self.assertEqual(t._hat_direction("h0up"), "up")
        self.assertEqual(t._hat_direction("h3right"), "right")   # hat number ignored

    def test_rejects_non_dpad(self):
        for tok in ("", "h0x", "h0", "0x130", "up", "habcup", "hup"):
            self.assertIsNone(t._hat_direction(tok), tok)


class DpadMaps(unittest.TestCase):
    def test_xemu(self):
        self.assertEqual([t.xemu_hat_dpad_index(x) for x in DIRS], [11, 12, 13, 14])

    def test_pcsx2(self):
        self.assertEqual([t.pcsx2_dpad_source(x) for x in DIRS],
                         ["DPadUp", "DPadDown", "DPadLeft", "DPadRight"])

    def test_ryujinx(self):
        self.assertEqual([t.ryujinx_hat_dpad(x) for x in DIRS],
                         ["DpadUp", "DpadDown", "DpadLeft", "DpadRight"])

    def test_eden(self):
        self.assertEqual([t.eden_hat_button_index(x) for x in DIRS], [13, 14, 15, 16])

    def test_button_code_is_not_a_dpad(self):
        # a real evdev button code must never be mistaken for a d-pad direction
        for fn in (t.xemu_hat_dpad_index, t.pcsx2_dpad_source,
                   t.ryujinx_hat_dpad, t.eden_hat_button_index):
            self.assertIsNone(fn("0x130"))
            self.assertIsNone(fn("h0x"))

    def test_pcsx2_dpad_label(self):
        self.assertEqual(t.sdl_source_label("DPadUp"), "D-pad Up")


if __name__ == "__main__":
    unittest.main()
