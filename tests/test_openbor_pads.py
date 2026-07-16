"""mad-openbor-pads: the pad plan (whose index IS the OpenBOR player slot).

The plan is the fix for the X-Arcade P1/P2 half-swap: Wine used to decide port
order and got it wrong at random, so these tests pin the order we impose."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from lib.devices import Device

_spec = importlib.util.spec_from_file_location(
    "mad_openbor_pads",
    Path(__file__).resolve().parent.parent / "mad-openbor-pads.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

XA, DS, DS4 = "045e:02a1", "054c:0ce6", "054c:09cc"
CLASSES = ["x-arcade", DS, DS4]


def dev(vidpid: str, path: str, name="pad", **kw) -> Device:
    vid, pid = (int(x, 16) for x in vidpid.split(":"))
    return Device(name=name, path=path, is_joypad=True, is_mouse=False,
                  is_keyboard=False, js_index=0, mouse_index=None,
                  vid=vid, pid=pid, **kw)


class Plan(unittest.TestCase):
    def plan(self, devs, xport="1.0"):
        # usb_iface_num reads sysfs; map our fake paths deterministically.
        ifaces = {"/dev/input/event10": 0, "/dev/input/event11": 1}
        with mock.patch.object(P, "usb_iface_num",
                               side_effect=lambda p: ifaces.get(p)), \
             mock.patch.object(P, "is_xarcade",
                               side_effect=lambda d, xp: d.vid == 0x045E and d.pid == 0x02A1):
            return P.build_plan(devs, CLASSES, xport)

    def test_xarcade_halves_take_p1_p2_by_usb_interface(self):
        # The whole point of P2: :1.0 is ALWAYS P1, :1.1 ALWAYS P2 — regardless
        # of enumeration order, which is what Wine used to get wrong.
        devs = [dev(XA, "/dev/input/event11"),      # :1.1 enumerated FIRST
                dev(XA, "/dev/input/event10")]      # :1.0 second
        plan = self.plan(devs)
        self.assertEqual([d.path for d, _ in plan],
                         ["/dev/input/event10", "/dev/input/event11"])
        self.assertEqual([c for _, c in plan], ["xpad", "xpad"])

    def test_xarcade_plus_two_ds_is_p1_p2_then_p3_p4(self):
        devs = [dev(DS, "/dev/input/event20"), dev(DS, "/dev/input/event21"),
                dev(XA, "/dev/input/event10"), dev(XA, "/dev/input/event11")]
        plan = self.plan(devs)
        self.assertEqual([c for _, c in plan], ["xpad", "xpad", "ps", "ps"])
        self.assertEqual([d.path for d, _ in plan][:2],
                         ["/dev/input/event10", "/dev/input/event11"])

    def test_two_ds_and_two_ds4_fills_four_slots(self):
        devs = [dev(DS4, "/dev/input/event30"), dev(DS, "/dev/input/event20"),
                dev(DS4, "/dev/input/event31"), dev(DS, "/dev/input/event21")]
        plan = self.plan(devs, xport="")
        self.assertEqual(len(plan), 4)
        # DualSense (054c:0ce6) outranks DS4 (054c:09cc) per pad_classes order
        self.assertEqual([hex(d.pid) for d, _ in plan],
                         ["0xce6", "0xce6", "0x9cc", "0x9cc"])

    def test_seats_sort_by_NUMERIC_node_not_string(self):
        # Regression for the on-device P2 gate failure (2026-07-16): paths were
        # sorted as strings, so "event258" < "event30" and the seats depended on
        # collation. A pad's node number changes whenever it reconnects, so the
        # same two pads took DIFFERENT seats on consecutive launches.
        devs = [dev(DS, "/dev/input/event258"), dev(DS, "/dev/input/event30")]
        plan = self.plan(devs, xport="")
        self.assertEqual([d.path for d, _ in plan],
                         ["/dev/input/event30", "/dev/input/event258"])

    def test_seat_order_stable_across_node_renumbering(self):
        # The SAME physical pad set must seat identically however the kernel
        # numbered the nodes this boot.
        a = [dev(DS, "/dev/input/event30"), dev(DS, "/dev/input/event258")]
        b = [dev(DS, "/dev/input/event258"), dev(DS, "/dev/input/event30")]
        self.assertEqual([d.path for d, _ in self.plan(a, xport="")],
                         [d.path for d, _ in self.plan(b, xport="")])

    def test_capped_at_four(self):
        devs = [dev(DS, f"/dev/input/event2{i}") for i in range(6)]
        self.assertEqual(len(self.plan(devs, xport="")), P.MAX_PADS)

    def test_unknown_family_dropped_never_guessed(self):
        # No translation table -> we cannot map its buttons, so it must not
        # silently occupy a player slot.
        devs = [dev(DS, "/dev/input/event20"), dev("1234:5678", "/dev/input/event40")]
        plan = self.plan(devs, xport="")
        self.assertEqual([d.path for d, _ in plan], ["/dev/input/event20"])

    def test_no_pads_is_the_handheld_signal(self):
        self.assertEqual(self.plan([], xport=""), [])


class Digitize(unittest.TestCase):
    """Stick -> d-pad with hysteresis, so a stick resting on the line cannot
    chatter the hat, and so stick AND d-pad both drive the game's one binding."""

    def setUp(self):
        self.t = P.Twin.__new__(P.Twin)          # no uinput needed
        self.t.stick = [0, 0]
        self.t.dpad = [0, 0]
        self.t.hat = [0, 0]

    def test_engage_and_release_thresholds(self):
        self.t._digitize(0, 0.35)                # below ENGAGE
        self.assertEqual(self.t.stick[0], 0)
        self.t._digitize(0, 0.45)                # crosses ENGAGE
        self.assertEqual(self.t.stick[0], 1)
        self.t._digitize(0, 0.35)                # above RELEASE -> holds
        self.assertEqual(self.t.stick[0], 1)
        self.t._digitize(0, 0.25)                # below RELEASE -> drops
        self.assertEqual(self.t.stick[0], 0)
        self.t._digitize(0, -0.9)
        self.assertEqual(self.t.stick[0], -1)

    def test_fast_flick_maps_straight_across_no_dropped_frame(self):
        # right -> hard left in one event: must land on left immediately, not
        # pass through centre and need a second event (a dropped input).
        self.t._digitize(1, 0.9)
        self.assertEqual(self.t.stick[1], 1)
        self.t._digitize(1, -0.9)
        self.assertEqual(self.t.stick[1], -1)

    def test_holds_inside_the_hysteresis_band(self):
        self.t._digitize(0, 0.9)
        self.assertEqual(self.t.stick[0], 1)
        for f in (0.39, 0.35, 0.31):             # between RELEASE and ENGAGE
            self.t._digitize(0, f)
            self.assertEqual(self.t.stick[0], 1, f"chattered at {f}")


class HatMerge(unittest.TestCase):
    def setUp(self):
        self.t = P.Twin.__new__(P.Twin)
        self.t.stick = [0, 0]
        self.t.dpad = [0, 0]
        self.t.hat = [0, 0]
        self.writes = []
        self.t.ui = mock.Mock()
        self.t.ui.write.side_effect = lambda *a: self.writes.append(a)

    def test_dpad_or_stick_both_drive_the_hat(self):
        self.t.stick = [0, -1]                   # stick up
        self.assertTrue(self.t._push_hat())
        self.assertEqual(self.t.hat, [0, -1])
        self.t.stick = [0, 0]                    # stick centred...
        self.t.dpad = [0, -1]                    # ...d-pad still up
        self.t._push_hat()
        self.assertEqual(self.t.hat, [0, -1], "d-pad must hold the hat")

    def test_no_write_when_unchanged(self):
        self.t.dpad = [1, 0]
        self.assertTrue(self.t._push_hat())
        self.writes.clear()
        self.assertFalse(self.t._push_hat())     # idempotent
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
