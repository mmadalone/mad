"""Eden analog-stick remap (Phase 2b): rewrites axis_x/y + invert_x/y in the
player_N_lstick/rstick line, PRESERVING the offset_* calibration. Temp config; no
hardware.

Run:  python3 -m unittest tests.test_eden_sticks -v
"""
from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import eden_input_cmds as ec
from lib.madsrv.rpc import RpcError

GUID = "050000007e0500003003000001000000"
CFG = f"""[Controls]
player_0_button_a="engine:sdl,port:0,guid:{GUID},button:0"
player_0_lstick="engine:sdl,port:0,guid:{GUID},axis_x:0,offset_x:0.048830,axis_y:1,offset_y:-0.000977,invert_x:+,invert_y:+"
player_0_rstick="engine:sdl,port:0,guid:{GUID},axis_x:2,offset_x:-0.000000,axis_y:3,offset_y:0.007782,invert_x:+,invert_y:+"
"""


class EdenSticks(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.f = self.dir / "qt-config.ini"
        self.f.write_text(CFG)
        self._file, ec._FILE = ec._FILE, self.f
        self._run, ec.proc_guard.emulator_running = ec.proc_guard.emulator_running, lambda n: False

    def tearDown(self):
        ec._FILE = self._file
        ec.proc_guard.emulator_running = self._run
        shutil.rmtree(self.dir, ignore_errors=True)

    def _stick(self, which):
        m = re.search(rf'player_0_{which}="([^"]*)"', self.f.read_text())
        return m.group(1)

    def test_remap_rstick_x_inverted_preserves_offset(self):
        res = ec._input_set({"id": "rstick_x", "kind": "axis", "value": "-right_x@3", "player": "player_0"})
        self.assertEqual(res["value"], "axis 3")
        line = self._stick("rstick")
        self.assertIn("axis_x:3", line)                      # bound to raw axis 3 (DualSense right-X)
        self.assertIn("invert_x:-", line)                    # pushed opposite the prompt → inverted
        self.assertIn("offset_x:-0.000000", line)            # calibration preserved
        self.assertIn("axis_y:3,offset_y:0.007782", line)    # other axis + offset untouched
        self.assertIn("invert_y:+", line)
        self.assertEqual(self._stick("lstick"),              # the OTHER stick line untouched
                         f"engine:sdl,port:0,guid:{GUID},axis_x:0,offset_x:0.048830,axis_y:1,"
                         "offset_y:-0.000977,invert_x:+,invert_y:+")

    def test_normal_push_not_inverted(self):
        ec._input_set({"id": "lstick_y", "kind": "axis", "value": "+left_y@1", "player": "player_0"})
        line = self._stick("lstick")
        self.assertIn("axis_y:1", line)
        self.assertIn("invert_y:+", line)
        self.assertIn("offset_y:-0.000977", line)

    def test_rejects_trigger_and_garbage(self):
        for v in ("+trigger_left@4", "+0", "garbage"):
            with self.assertRaises(RpcError):
                ec._input_set({"id": "lstick_x", "kind": "axis", "value": v, "player": "player_0"})

    def test_stick_row_capturable(self):
        res = ec._input_get({"player": "player_0"})
        binds = {b["id"]: b for g in res["groups"] for b in g["binds"]}
        self.assertEqual(binds["lstick_x"]["kind"], "axis")
        self.assertTrue(binds["lstick_x"]["capturable"])
        self.assertEqual(binds["rstick_x"]["value"], "axis 2")   # shows the stored index


if __name__ == "__main__":
    unittest.main()
