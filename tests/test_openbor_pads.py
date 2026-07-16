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

    def test_an_unlisted_family_is_kept_out(self):
        # MAD's "Player pad families" row promises "Pads not listed are hidden
        # from this emulator". It used to filter on translatability ALONE, so an
        # unchecked pad still took a seat and the row was lying (audited
        # 2026-07-17). Unchecking DS4 must actually keep it out.
        devs = [dev(DS, "/dev/input/event20"), dev(DS4, "/dev/input/event30")]
        plan = P.build_plan(devs, ["x-arcade", DS], "")     # DS4 not listed
        self.assertEqual([d.path for d, _ in plan], ["/dev/input/event20"])

    def test_unlisting_the_xarcade_keeps_it_out_by_either_spelling(self):
        # The base policy lists the cab by vid:pid, MAD's picker writes the
        # "x-arcade" token — both must count as listed, and neither as listed
        # when it is absent.
        devs = [dev(XA, "/dev/input/event10")]
        with mock.patch.object(P, "usb_iface_num", side_effect=lambda p: 0), \
             mock.patch.object(P, "is_xarcade",
                               side_effect=lambda d, xp: d.vid == 0x045E):
            self.assertEqual(len(P.build_plan(devs, ["x-arcade"], "1.0")), 1)
            self.assertEqual(len(P.build_plan(devs, [XA], "1.0")), 1)
            self.assertEqual(P.build_plan(devs, [DS], "1.0"), [])

    def test_unknown_family_dropped_never_guessed(self):
        # No translation table -> we cannot map its buttons, so it must not
        # silently occupy a player slot.
        devs = [dev(DS, "/dev/input/event20"), dev("1234:5678", "/dev/input/event40")]
        plan = self.plan(devs, xport="")
        self.assertEqual([d.path for d, _ in plan], ["/dev/input/event20"])

    def test_no_pads_is_the_handheld_signal(self):
        self.assertEqual(self.plan([], xport=""), [])


class TwinProductIds(unittest.TestCase):
    """One product id per player is what pins the OpenBOR seats.

    Wine keys each pad as `##?#HID#VID_4D41&PID_000X&IG_00#1&<GUID>...` and
    enumerates those keys ALPHABETICALLY, so the string order is the port order.
    The GUID carries crc16(NAME), so on one shared pid the seats were decided by
    a name hash: crc16("MAD OpenBOR P2") = 0x8002 sorted ahead of
    crc16("MAD OpenBOR P1") = 0x8142, and P2 took port 0. The pid sits earlier in
    the key than the GUID, so a pid per player decides it first. Measured
    2026-07-16: reversing the creation order moved every node and left twin P1
    on port 1 regardless — the sort key never depended on us."""

    def test_each_player_gets_its_own_product_id(self):
        ids = [P.product_for(i) for i in range(P.MAX_PADS)]
        self.assertEqual(ids, [0x0002, 0x0003, 0x0004, 0x0005])
        self.assertEqual(len(set(ids)), P.MAX_PADS, "a collision = the bug")

    def test_product_ids_ascend_with_player_order(self):
        # The registry key is ...&PID_000X&IG_00#<suffix>, so the pid decides
        # any alphabetical enumeration before the suffix can. Ascending pid ==
        # ascending seat.
        ids = [P.product_for(i) for i in range(P.MAX_PADS)]
        self.assertEqual(ids, sorted(ids))

    def test_never_collides_with_the_wii_nav_bridge(self):
        # 4d41:0001 is the Wii Nav pad; is_mad_virtual is vid-wide, so the pids
        # have to stay distinct or the two features alias each other.
        self.assertNotIn(0x0001, [P.product_for(i) for i in range(P.MAX_PADS)])

    def test_whitelist_lists_every_twin_pid(self):
        # A pid the whitelist forgets is a player the game cannot see at all.
        wl = P.sdl_whitelist()
        for i in range(P.MAX_PADS):
            self.assertIn(f"0x4d41/0x{P.product_for(i):04x}", wl)
        self.assertEqual(len(wl.split(",")), P.MAX_PADS)


class Teardown(unittest.TestCase):
    """A twin that blows up on teardown must never cost the pads their ungrab.

    Reproduces DD_FINAL.log, 2026-07-16: shutdown() called close() on a twin
    whose fd was already gone, evdev raised ValueError (NOT OSError, so the
    handler sailed past it), and the exception escaped the signal handler
    BEFORE the ungrab loop — which would leave every real pad grabbed and the
    rig mute, ES-DE included, with no working controller left to kill us."""

    def _twin_with_dead_fd(self):
        t = P.Twin.__new__(P.Twin)
        t.dpad, t.stick, t.hat = [0, 0], [0, 0], [0, 0]
        t.ui = mock.Mock()
        # exactly what evdev raises once the fd is -1
        err = ValueError("file descriptor cannot be a negative integer (-1)")
        t.ui.close.side_effect = err
        t.ui.write.side_effect = err
        t.ui.syn.side_effect = err
        return t

    def test_close_swallows_the_dead_fd_valueerror(self):
        self._twin_with_dead_fd().close()          # must not raise

    def test_neutralize_swallows_the_dead_fd_valueerror(self):
        self._twin_with_dead_fd().neutralize()     # must not raise


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
