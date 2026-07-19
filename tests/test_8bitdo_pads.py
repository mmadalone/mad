"""8BitDo FC30 + NES30 Pro merger translation (lib/openbor_maps + mad-openbor-pads feed).

Ground truth captured live 2026-07-19 (Miquel pressing each control):
  FC30 / FC30 II (2dc8:2810): no sticks/triggers; its D-PAD rides ABS_X/ABS_Y (0..255, centre
     127) -> a DIGITAL hat on an analog axis (roles dhatx/dhaty), twin gets a clean d-pad + NO
     phantom stick. Buttons match xpad.
  NES30 Pro (2dc8:3820): d-pad on ABS_HAT0 + left stick on ABS_X/Y (both default), RIGHT stick on
     ABS_Z/ABS_RZ, analog triggers on ABS_GAS/ABS_BRAKE. Buttons match xpad.

Also pins that the SHARED xpad/ps translation is untouched (no override entry for them).
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from evdev import ecodes as e

from lib import openbor_maps as M

_spec = importlib.util.spec_from_file_location(
    "mad_openbor_pads", Path(__file__).resolve().parent.parent / "mad-openbor-pads.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


class Ev:
    def __init__(self, typ, code, value):
        self.type, self.code, self.value = typ, code, value


def _twin(cls, rng):
    """A Twin wired for feed() with a recording fake uinput (no real device)."""
    t = P.Twin.__new__(P.Twin)
    t.cls = cls
    t.dpad, t.stick, t.hat = [0, 0], [0, 0], [0, 0]
    t._sector, t._fx, t._fy = -1, 0.0, 0.0
    t.gate, t._on, t._off = "box", 0.35, 0.25
    t._rng = dict(rng)
    t.writes = []
    t.ui = mock.Mock()
    t.ui.write.side_effect = lambda *a: t.writes.append(a)
    return t


def _wrote_axis(t, role):
    code = P.AX_CODE[role]
    return any(w[0] == e.EV_ABS and w[1] == code for w in t.writes)


class Tables(unittest.TestCase):
    def test_class_of_vidpid(self):
        self.assertEqual(M.CLASS_OF_VIDPID["2dc8:2810"], "fc30")
        self.assertEqual(M.CLASS_OF_VIDPID["2dc8:3820"], "8bitpro")

    def test_buttons_reuse_xpad_table(self):
        self.assertIs(M.EVDEV_BTN["fc30"], M.EVDEV_BTN["xpad"])
        self.assertIs(M.EVDEV_BTN["8bitpro"], M.EVDEV_BTN["xpad"])

    def test_override_shapes_and_shared_untouched(self):
        self.assertEqual(M.ABS_ROLE_OVERRIDE["fc30"], {0x00: "dhatx", 0x01: "dhaty"})
        self.assertEqual(M.ABS_ROLE_OVERRIDE["8bitpro"],
                         {0x02: "rx", 0x05: "ry", 0x09: "rt", 0x0a: "lt"})
        self.assertNotIn("xpad", M.ABS_ROLE_OVERRIDE)   # shared default kept for the real pads
        self.assertNotIn("ps", M.ABS_ROLE_OVERRIDE)


class FC30(unittest.TestCase):
    def setUp(self):
        self.t = _twin("fc30", {0x00: (0, 255), 0x01: (0, 255)})

    def test_dpad_on_axis_is_clean_hat_with_no_phantom_stick(self):
        self.t.feed(Ev(e.EV_ABS, 0x00, 255))            # d-pad RIGHT (ABS_X full)
        self.assertEqual(self.t.dpad[0], 1)
        self.assertIn((e.EV_ABS, e.ABS_HAT0X, 1), self.t.writes)
        self.assertFalse(_wrote_axis(self.t, "lx"), "FC30 d-pad must not emit a left-stick axis")

    def test_dpad_all_directions_and_release(self):
        self.t.feed(Ev(e.EV_ABS, 0x00, 0))              # LEFT
        self.assertEqual(self.t.dpad[0], -1)
        self.t.feed(Ev(e.EV_ABS, 0x01, 0))              # UP
        self.assertEqual(self.t.dpad[1], -1)
        self.t.feed(Ev(e.EV_ABS, 0x01, 255))            # DOWN
        self.assertEqual(self.t.dpad[1], 1)
        self.t.feed(Ev(e.EV_ABS, 0x00, 127))            # centre -> release X
        self.assertEqual(self.t.dpad[0], 0)

    def test_face_and_shoulder_buttons_via_xpad(self):
        self.t.feed(Ev(e.EV_KEY, 0x130, 1))             # A
        self.t.feed(Ev(e.EV_KEY, 0x134, 1))             # Y (BTN_WEST)
        self.t.feed(Ev(e.EV_KEY, 0x137, 1))             # R -> rb
        self.assertIn((e.EV_KEY, P.BTN_CODE["a"], 1), self.t.writes)
        self.assertIn((e.EV_KEY, P.BTN_CODE["y"], 1), self.t.writes)
        self.assertIn((e.EV_KEY, P.BTN_CODE["rb"], 1), self.t.writes)


class NES30Pro(unittest.TestCase):
    def setUp(self):
        self.t = _twin("8bitpro", {0x00: (0, 255), 0x01: (0, 255), 0x02: (0, 255),
                                   0x05: (0, 255), 0x09: (0, 255), 0x0a: (0, 255)})

    def test_right_stick_on_z_rz_not_triggers(self):
        self.t.feed(Ev(e.EV_ABS, 0x02, 255))            # right stick X (ABS_Z)
        self.t.feed(Ev(e.EV_ABS, 0x05, 0))              # right stick Y (ABS_RZ)
        self.assertTrue(_wrote_axis(self.t, "rx"))
        self.assertTrue(_wrote_axis(self.t, "ry"))
        self.assertFalse(_wrote_axis(self.t, "lt"), "Z/RZ must NOT read as triggers here")

    def test_analog_triggers_on_gas_brake(self):
        self.t.feed(Ev(e.EV_ABS, 0x09, 255))            # R2 -> rt (ABS_GAS)
        self.t.feed(Ev(e.EV_ABS, 0x0a, 255))            # L2 -> lt (ABS_BRAKE)
        self.assertTrue(_wrote_axis(self.t, "rt"))
        self.assertTrue(_wrote_axis(self.t, "lt"))

    def test_dpad_hat_and_left_stick_use_defaults(self):
        self.t.feed(Ev(e.EV_ABS, 0x10, 1))              # ABS_HAT0X -> d-pad right
        self.assertIn((e.EV_ABS, e.ABS_HAT0X, 1), self.t.writes)
        self.assertEqual(self.t.dpad[0], 1)
        self.t.feed(Ev(e.EV_ABS, 0x00, 255))            # ABS_X -> LEFT stick (default), not d-pad-only
        self.assertTrue(_wrote_axis(self.t, "lx"))

    def test_l3_r3_and_select_start(self):
        self.t.feed(Ev(e.EV_KEY, 0x13d, 1))             # L3
        self.t.feed(Ev(e.EV_KEY, 0x13a, 1))             # Select -> back
        self.assertIn((e.EV_KEY, P.BTN_CODE["thumbl"], 1), self.t.writes)
        self.assertIn((e.EV_KEY, P.BTN_CODE["back"], 1), self.t.writes)


class SharedPathsUnaffected(unittest.TestCase):
    def test_xpad_abs_z_is_still_a_trigger(self):
        # A real xpad/X-Arcade has NO override -> ABS_Z stays lt (default). The 8BitDo feature
        # must not have changed the shared path.
        t = _twin("xpad", {0x02: (0, 255)})
        t.feed(Ev(e.EV_ABS, 0x02, 255))
        self.assertTrue(_wrote_axis(t, "lt"))
        self.assertFalse(_wrote_axis(t, "rx"))

    def test_ps_class_axes_also_use_the_default(self):
        # ps (DualSense/DS4) likewise has no override: ABS_X -> lx, ABS_Z -> lt, unchanged.
        t = _twin("ps", {0x00: (0, 255), 0x02: (0, 255)})
        t.feed(Ev(e.EV_ABS, 0x00, 255))
        t.feed(Ev(e.EV_ABS, 0x02, 255))
        self.assertTrue(_wrote_axis(t, "lx"))
        self.assertTrue(_wrote_axis(t, "lt"))


class DroppedCodes(unittest.TestCase):
    """Codes we deliberately DO NOT translate must stay silent -- a future EVDEV_BTN change that
    accidentally mapped them (e.g. 0x138 -> a button) would double-fire the Pro's triggers."""

    def test_fc30_phantom_buttons_produce_no_output(self):
        t = _twin("fc30", {})
        for code in (0x132, 0x135):                     # phantom BTN_C / BTN_Z
            t.feed(Ev(e.EV_KEY, code, 1))
        self.assertEqual(t.writes, [])

    def test_pro_digital_trigger_clicks_are_dropped(self):
        t = _twin("8bitpro", {})
        for code in (0x138, 0x139):                     # BTN_TL2 / BTN_TR2 (analog GAS/BRAKE carry lt/rt)
            t.feed(Ev(e.EV_KEY, code, 1))
        self.assertEqual(t.writes, [])


if __name__ == "__main__":
    unittest.main()
