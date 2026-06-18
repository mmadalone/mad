"""Capture of a d-pad reported as discrete buttons (BTN_DPAD_*, 0x220..0x223) —
e.g. the Wii U Pro Controller, which exposes NO ABS hat. The button is routed
through the hat machinery so it identifies / combos like a hat direction.
Synthetic events; no hardware.

Run:  python3 -m unittest tests.test_dpad_button_capture -v
"""
from __future__ import annotations

import unittest

import evdev.ecodes as e

from lib.madsrv import capture_cmds as cc


class _Ev:
    def __init__(self, ty, code, val):
        self.type, self.code, self.value = ty, code, val


class _D:
    path = "/dev/input/event99"

    def __init__(self, keys=None):
        self._keys = list(keys) if keys is not None else [0x130, 0x131, 0x133, 0x134]

    def capabilities(self, absinfo=False):
        return {e.EV_KEY: self._keys}


class DpadButtonCapture(unittest.TestCase):
    def _stream(self, mode):
        s = cc._CaptureStream(mode, 5.0)
        s._identify = lambda d: {"name": "Wii U Pro"}
        return s

    def test_identify_fires_bind_token_each_direction(self):
        for code, tok in ((0x220, "h0up"), (0x221, "h0down"),
                          (0x222, "h0left"), (0x223, "h0right")):
            s = self._stream("identify")
            res = s._on_button(_Ev(e.EV_KEY, code, 1), _D())   # via _on_button dispatch
            self.assertEqual(res["bind_token"], tok)

    def test_combo_accumulates_then_fires_on_release(self):
        s = self._stream("combo")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x220, 1), _D()))   # press: accumulate
        res = s._on_button(_Ev(e.EV_KEY, 0x220, 0), _D())               # release: fire
        self.assertEqual(res["hats"], ["h0up"])

    def test_face_button_unaffected(self):
        # a real face button still accumulates + fires on release (no regression)
        s = self._stream("identify")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x130, 1), _D()))   # press
        res = s._on_button(_Ev(e.EV_KEY, 0x130, 0), _D())                # release
        self.assertEqual(res["held"], [0x130])

    def test_btn_index_map_ranks(self):
        # RA udev numbers buttons by RANK among present face buttons, not code-0x130.
        # X-Arcade skips 0x132/0x135 → BTN_NORTH(0x133) is index 2, not 3.
        m = cc._btn_index_map(_D([0x130, 0x131, 0x133, 0x134, 0x136, 0x137]))
        self.assertEqual(m[0x133], 2)
        self.assertEqual(m[0x134], 3)
        # a contiguous pad keeps rank == code-0x130 (no regression)
        mc = cc._btn_index_map(_D(list(range(0x130, 0x138))))
        self.assertTrue(all(mc[c] == c - 0x130 for c in range(0x130, 0x138)))


if __name__ == "__main__":
    unittest.main()
