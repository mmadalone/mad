"""mad-openbor-pads RADIAL stick gate (MUGEN opt-in) - the _radial_sector helper.

CI-safe: tests the PURE vector->8-way function (no uinput). Guards the fix for the
box-gate "dead diagonal" band - cardinals AND diagonals must engage at the same push -
plus the magnitude + angular hysteresis. The default box gate stays covered by
test_openbor_pads (unchanged).
"""
import importlib.util
import math
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "mad_openbor_pads", Path(__file__).resolve().parent.parent / "mad-openbor-pads.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def vec(deg, mag):
    th = math.radians(deg)
    return mag * math.cos(th), mag * math.sin(th)


class RadialGate(unittest.TestCase):
    def test_cardinal_and_diagonal_engage_at_same_push(self):
        # The whole point: NO dead-diagonal band. Every 8-way angle engages just above
        # RADIAL_ON and is neutral just below it - identically for cardinals and diagonals.
        for deg in range(0, 360, 45):
            fx, fy = vec(deg, P.RADIAL_ON + 0.005)
            self.assertGreaterEqual(P._radial_sector(fx, fy, -1), 0, f"{deg}deg should engage")
            fx, fy = vec(deg, P.RADIAL_ON - 0.02)
            self.assertEqual(P._radial_sector(fx, fy, -1), -1, f"{deg}deg below radius = neutral")

    def test_old_box_dead_band_now_lives(self):
        # A 45deg diagonal at magnitude 0.50 was DEAD in the box gate (needed >=0.566);
        # the radial gate engages it as down-right.
        s = P._radial_sector(*vec(45, 0.50), -1)
        self.assertEqual(P._SECTOR_XY[s], (1, 1))

    def test_all_eight_sectors_map_correctly(self):
        want = {0: (1, 0), 45: (1, 1), 90: (0, 1), 135: (-1, 1),
                180: (-1, 0), 225: (-1, -1), 270: (0, -1), 315: (1, -1)}
        for deg, xy in want.items():
            s = P._radial_sector(*vec(deg, 1.0), -1)
            self.assertEqual(P._SECTOR_XY[s], xy, f"{deg}deg -> {xy}")

    def test_magnitude_hysteresis(self):
        self.assertEqual(P._radial_sector(*vec(0, P.RADIAL_ON - 0.01), -1), -1)   # below ON, neutral
        s = P._radial_sector(*vec(0, P.RADIAL_ON + 0.01), -1)
        self.assertEqual(P._SECTOR_XY[s], (1, 0))                                 # engage Right
        self.assertEqual(P._radial_sector(*vec(0, P.RADIAL_OFF + 0.02), s), s)    # hold above OFF
        self.assertEqual(P._radial_sector(*vec(0, P.RADIAL_OFF - 0.02), s), -1)   # release below OFF

    def test_angular_hysteresis_holds_sector_near_boundary(self):
        # Holding Right (sector 0): stay until the angle is well past 22.5deg + margin.
        self.assertEqual(P._radial_sector(*vec(22.5 + P.SECTOR_MARGIN - 3, 1.0), 0), 0)
        self.assertNotEqual(P._radial_sector(*vec(22.5 + P.SECTOR_MARGIN + 5, 1.0), 0), 0)

    def test_fast_flick_crosses_cleanly(self):
        # Right fully engaged, flick straight to Left: lands on Left (no stuck diagonal).
        s = P._radial_sector(*vec(0, 1.0), -1)
        s = P._radial_sector(*vec(180, 1.0), s)
        self.assertEqual(P._SECTOR_XY[s], (-1, 0))

    def test_gate_routing(self):
        # A backend that sets stick_gate="radial" (mugen, and now openbor) -> radial;
        # a backend with no stick_gate -> the default box. Use a name that has no
        # policy entry for the box case so the test does not depend on which backends
        # happen to opt in.
        P._configure("mugen")
        self.assertEqual(P._gate_now(), "radial")
        P._configure("no_such_backend_zzz")
        self.assertEqual(P._gate_now(), "box")


if __name__ == "__main__":
    unittest.main()
