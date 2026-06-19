"""Tests for retroarch_cmds._input_set_hotkey — binding a global system hotkey to a
captured joypad button, mouse button, or d-pad hat token.

Guards the X-Arcade fix: a joypad button must be written by its udev RANK (`index`,
e.g. the stick = 11-14) when given, the MOUSE branch must stay keyed on `code` so a
small index never mis-routes the X-Arcade red button to _btn, and the legacy
code-0x130 fallback must still hold for an older daemon / contiguous pad.

The global-cfg writer is stubbed so the test never touches the real retroarch.cfg.

Run:  python3 -m unittest tests.test_ra_hotkey -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import retroarch_cmds as rc

BASE = "input_exit_emulator"
BTN, MBTN, AXIS = BASE + "_btn", BASE + "_mbtn", BASE + "_axis"


class RaHotkey(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "run": rc.proc_guard.retroarch_running,
            "setg": rc.retroarch_cfg.set_global_option,
        }
        self.writes = {}                       # last write per key
        rc.proc_guard.retroarch_running = lambda: False
        rc.retroarch_cfg.set_global_option = lambda k, v: self.writes.__setitem__(k, v)

    def tearDown(self):
        rc.proc_guard.retroarch_running = self._saved["run"]
        rc.retroarch_cfg.set_global_option = self._saved["setg"]

    def test_joypad_button_uses_index_rank_not_code(self):
        # The X-Arcade stick "up" = evdev BTN_TRIGGER_HAPPY3 (0x2c2), udev rank 13. The rank,
        # not code-0x130 (which would be 0x2c2-0x130 = 400), must land in _btn.
        res = rc._input_set_hotkey({"base": BASE, "code": 0x2c2, "index": 13})
        self.assertEqual(res, {"base": BASE, "kind": "btn", "value": "13"})
        self.assertEqual(self.writes[BTN], "13")
        self.assertEqual(self.writes[MBTN], "nul")
        self.assertEqual(self.writes[AXIS], "nul")

    def test_mouse_branch_keyed_on_code_not_bypassed_by_index(self):
        # The X-Arcade red button (BTN_MIDDLE 0x112) must route to _mbtn even though an index
        # is also forwarded — the mouse branch is tested FIRST and keyed on code.
        res = rc._input_set_hotkey({"base": BASE, "code": 0x112, "index": 1})
        self.assertEqual(res, {"base": BASE, "kind": "mouse", "value": "3"})
        self.assertEqual(self.writes[MBTN], "3")
        self.assertEqual(self.writes[BTN], "nul")

    def test_legacy_code_only_falls_back_to_code_minus_0x130(self):
        # No index (older daemon): fall back to code-0x130. Documents that a HAPPY code with
        # no index yields the WRONG 400 — i.e. the index path is what makes the stick correct.
        res = rc._input_set_hotkey({"base": BASE, "code": 0x2c0})
        self.assertEqual(res["value"], str(0x2c0 - 0x130))   # 400
        self.assertEqual(self.writes[BTN], "400")
        # a contiguous pad's face button is still correct via the fallback.
        rc._input_set_hotkey({"base": BASE, "code": 0x130})
        self.assertEqual(self.writes[BTN], "0")

    def test_negative_index_falls_back_to_code(self):
        # C++ sends index = -1 when heldIndices is empty; treat as absent.
        rc._input_set_hotkey({"base": BASE, "code": 0x131, "index": -1})
        self.assertEqual(self.writes[BTN], "1")

    def test_hat_token_written_verbatim(self):
        # A genuine-hat pad (held empty) still drives a hotkey via the h<dir> token.
        res = rc._input_set_hotkey({"base": BASE, "token": "h0up"})
        self.assertEqual(res, {"base": BASE, "kind": "dpad", "value": "h0up"})
        self.assertEqual(self.writes[BTN], "h0up")
        self.assertEqual(self.writes[MBTN], "nul")


if __name__ == "__main__":
    unittest.main()
