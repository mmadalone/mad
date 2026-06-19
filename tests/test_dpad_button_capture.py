"""Capture of d-pad / arcade-stick directions reported as buttons. Two scenarios:

  • BTN_DPAD_* (0x220..0x223) — e.g. the Wii U Pro Controller, which exposes NO ABS
    hat; routed through the hat machinery so it identifies / combos like a direction.
  • BTN_TRIGGER_HAPPY1..4 (0x2c0..0x2c3) — the X-Arcade arcade STICK. RetroArch reads
    these as buttons 11-14; the device's separate ABS_HAT is DEAD and must be suppressed.
    Capture DUAL-EMITS a lone press: a button index (RA) + a d-pad hat token (SDL
    standalones). Covers _has_happy detection, the dead-hat suppression, and 11-14 ranking.

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

    def __init__(self, keys=None, path=None):
        self._keys = list(keys) if keys is not None else [0x130, 0x131, 0x133, 0x134]
        if path is not None:
            self.path = path

    def capabilities(self, absinfo=False):
        return {e.EV_KEY: self._keys}


# The X-Arcade Tankstick gamepad's EV_KEY set in Xbox mode: 11 face/system buttons (0x130-0x13e,
# skipping 0x132/0x135/0x138/0x139) + the 4 arcade-stick BTN_TRIGGER_HAPPY buttons (0x2c0-0x2c3).
XARCADE_KEYS = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e,
                0x2c0, 0x2c1, 0x2c2, 0x2c3]


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
        # RA udev ranks buttons across the WHOLE EV_KEY space in 4 ordered ranges, not just
        # 0x130-0x13f. X-Arcade (no sub-0x130 buttons) skips 0x132/0x135 → BTN_NORTH idx 2.
        m = cc._btn_index_map(_D([0x130, 0x131, 0x133, 0x134, 0x136, 0x137]))
        self.assertEqual(m[0x133], 2)
        self.assertEqual(m[0x134], 3)
        # a contiguous pad keeps rank == code-0x130 (no regression)
        mc = cc._btn_index_map(_D(list(range(0x130, 0x138))))
        self.assertTrue(all(mc[c] == c - 0x130 for c in range(0x130, 0x138)))
        # a pad with sub-0x130 buttons (Steam Deck pad: BTN_THUMB 0x121/0x122, BTN_BASE
        # 0x126) shifts the face-button indices — RA loop 2 ranks them BEFORE 0x130.
        md = cc._btn_index_map(_D([0x121, 0x122, 0x126, 0x130, 0x131, 0x133, 0x134]))
        self.assertEqual(md[0x130], 3)
        self.assertEqual(md[0x133], 5)
        # X-Arcade arcade stick: BTN_TRIGGER_HAPPY1-4 (0x2c0-0x2c3) rank 11-14 via RA's
        # BTN_MISC..KEY_MAX loop — matching the loaded Xbox_360_Wireless_Receiver.cfg
        # (left=11 right=12 up=13 down=14). This is what makes the stick bind in-game.
        mx = cc._btn_index_map(_D(XARCADE_KEYS))
        self.assertEqual([mx[c] for c in (0x2c0, 0x2c1, 0x2c2, 0x2c3)], [11, 12, 13, 14])

    def test_happy_identify_dual_emits_button_and_hat(self):
        # The X-Arcade stick is a BTN_TRIGGER_HAPPY *button*, so (like a face button) it
        # accumulates on press and FIRES ON RELEASE. The result dual-emits: a button index
        # (btn_indices=[13], what RetroArch reads) AND a d-pad hat token (bind_token='h0up',
        # what the SDL standalones read on a kind=='hat' row). 0x2c2 = up = idx 13.
        s = self._stream("identify")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x2c2, 1), _D(XARCADE_KEYS)))  # press
        res = s._on_button(_Ev(e.EV_KEY, 0x2c2, 0), _D(XARCADE_KEYS))               # release
        self.assertEqual(res["held"], [0x2c2])
        self.assertEqual(res["btn_indices"], [13])
        self.assertEqual(res["bind_token"], "h0up")

    def test_happy_dir_token_each_direction(self):
        # bind_token direction must match the gamecontrollerdb/autoconfig order, asserted by CODE.
        for code, tok in ((0x2c0, "h0left"), (0x2c1, "h0right"),
                          (0x2c2, "h0up"), (0x2c3, "h0down")):
            s = self._stream("identify")
            self.assertIsNone(s._on_button(_Ev(e.EV_KEY, code, 1), _D(XARCADE_KEYS)))
            res = s._on_button(_Ev(e.EV_KEY, code, 0), _D(XARCADE_KEYS))
            self.assertEqual(res["bind_token"], tok)

    def test_dead_hat_suppressed_for_happy_device(self):
        # With the device flagged as a HAPPY (X-Arcade) node, its dead phantom ABS_HAT is
        # IGNORED — no bogus h0up — so the real stick buttons are what bind.
        s = self._stream("identify")
        s._has_happy = {_D.path}
        self.assertIsNone(s._on_button(_Ev(e.EV_ABS, e.ABS_HAT0Y, -1), _D(XARCADE_KEYS)))

    def test_genuine_hat_still_fires_when_not_happy(self):
        # Fresh stream (no run()): _has_happy exists from __init__ (no AttributeError) and is
        # empty, so a GENUINE-hat pad's ABS_HAT still identifies as h0up (no regression).
        s = self._stream("identify")
        res = s._on_button(_Ev(e.EV_ABS, e.ABS_HAT0Y, -1), _D())
        self.assertEqual(res["bind_token"], "h0up")
        # ...and BTN_DPAD (Wii U Pro) is likewise untouched.
        s2 = self._stream("identify")
        self.assertEqual(s2._on_button(_Ev(e.EV_KEY, 0x220, 1), _D())["bind_token"], "h0up")

    def test_happy_paths_detection(self):
        # The load-bearing run()-time detection: a node exposing ANY of 0x2c0-0x2c3 is flagged;
        # a plain face-button node is not; BOTH byte-identical X-Arcade halves (distinct paths)
        # are flagged. This pins absinfo=False and the 0x2c0-0x2c3 bound that _on_button gates on.
        p1 = _D(XARCADE_KEYS, path="/dev/input/event23")
        p2 = _D(XARCADE_KEYS, path="/dev/input/event25")
        plain = _D([0x130, 0x131, 0x133, 0x134], path="/dev/input/event7")
        self.assertEqual(cc._happy_paths([p1, p2, plain]),
                         {"/dev/input/event23", "/dev/input/event25"})
        self.assertEqual(cc._happy_paths([plain]), set())
        # an unreadable node is skipped, not flagged, and doesn't abort the scan
        class _Boom:
            path = "/dev/input/event98"
            def capabilities(self, absinfo=False):
                raise OSError("gone")
        self.assertEqual(cc._happy_paths([_Boom(), p1]), {"/dev/input/event23"})

    def test_happy_combo_binds_via_button_not_token(self):
        # In COMBO mode (RA hotkey / quit-combo) the consumers read held/btn_indices, never
        # bind_token — so the dual-emit token is identify-gated OFF here (no stray token).
        s = self._stream("combo")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x2c2, 1), _D(XARCADE_KEYS)))  # press
        res = s._on_button(_Ev(e.EV_KEY, 0x2c2, 0), _D(XARCADE_KEYS))               # release
        self.assertEqual(res["held"], [0x2c2])
        self.assertEqual(res["btn_indices"], [13])
        self.assertNotIn("bind_token", res)

    def test_happy_diagonal_roll_still_emits_a_token(self):
        # An 8-way diagonal briefly holds two HAPPY directions; on release the all-HAPPY hold
        # still yields a hat token (from the first, by code) so a standalone d-pad row binds it
        # instead of falling through to a raw-btn write the hat-row writer would reject.
        s = self._stream("identify")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x2c0, 1), _D(XARCADE_KEYS)))  # left press
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x2c2, 1), _D(XARCADE_KEYS)))  # up press
        res = s._on_button(_Ev(e.EV_KEY, 0x2c2, 0), _D(XARCADE_KEYS))               # release
        self.assertEqual(res["held"], [0x2c0, 0x2c2])
        self.assertEqual(res["bind_token"], "h0left")   # first by code (0x2c0 = left)


if __name__ == "__main__":
    unittest.main()
