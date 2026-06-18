"""Ryujinx analog-stick selectors (Phase 2b): the Switch sticks are a physical-
stick CHOICE (Left/Right) + invert flags, not a capture. Verifies the selector
read (input_get) + write (selector_set). ryujinx_json load/write are stubbed; no
hardware, no real config touched.

Run:  python3 -m unittest tests.test_ryujinx_sticks -v
"""
from __future__ import annotations

import copy
import unittest

from lib.madsrv import ryujinx_input_cmds as rc
from lib.madsrv import ryujinx_json
from lib.madsrv.rpc import RpcError


def _data():
    return {"input_config": [{
        "player_index": "Player1", "controller_type": "ProController", "id": "x",
        "left_joycon": {}, "right_joycon": {},
        "left_joycon_stick": {"joystick": "Left", "invert_stick_x": False, "invert_stick_y": False},
        "right_joycon_stick": {"joystick": "Right", "invert_stick_x": False, "invert_stick_y": False},
    }]}


class RyujinxStickSelectors(unittest.TestCase):
    def setUp(self):
        self.data = _data()
        self.written = []
        self._load, self._write = ryujinx_json.load, ryujinx_json.write
        self._run = rc.proc_guard.emulator_running
        ryujinx_json.load = lambda: copy.deepcopy(self.data)
        ryujinx_json.write = lambda d: self.written.append(d)
        rc.proc_guard.emulator_running = lambda n: False

    def tearDown(self):
        ryujinx_json.load, ryujinx_json.write = self._load, self._write
        rc.proc_guard.emulator_running = self._run

    def _w(self):
        return self.written[-1]["input_config"][0]

    def test_set_left_source_to_right(self):
        res = rc._selector_set({"key": "left_stick_source", "value": "Right", "player": "Player1"})
        self.assertEqual(res["value"], "Right")
        self.assertEqual(self._w()["left_joycon_stick"]["joystick"], "Right")
        self.assertEqual(self._w()["right_joycon_stick"]["joystick"], "Right")  # untouched

    def test_toggle_invert(self):
        rc._selector_set({"key": "right_invert_y", "value": "true", "player": "Player1"})
        self.assertIs(self._w()["right_joycon_stick"]["invert_stick_y"], True)

    def test_input_get_exposes_stick_selectors(self):
        res = rc._input_get({"player": "Player1"})
        sel = {s["key"]: s for s in res["selectors"]}
        self.assertEqual(sel["left_stick_source"]["value"], "Left")
        self.assertIn("right_invert_x", sel)
        # invert toggle is an Off/On option selector (C++ renders options generically)
        self.assertEqual([o["value"] for o in sel["left_invert_x"]["options"]], ["false", "true"])

    def test_reject_bad_source(self):
        with self.assertRaises(RpcError):
            rc._selector_set({"key": "left_stick_source", "value": "Up", "player": "Player1"})

    def test_controller_type_still_works(self):
        res = rc._selector_set({"key": "controller_type", "value": "Handheld", "player": "Player1"})
        self.assertEqual(self._w()["controller_type"], "Handheld")
        self.assertEqual(res["value"], "Handheld")


if __name__ == "__main__":
    unittest.main()
