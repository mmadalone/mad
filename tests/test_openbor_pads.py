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

    def test_a_stale_xarcade_identify_does_not_delete_the_cabinet(self):
        # REGRESSION (this batch). The LIVE policy on this rig is
        #   pad_classes  = ["x-arcade", "054c:0ce6", "054c:09cc"]   (the TOKEN)
        #   xarcade_port = "1.1"
        # _listed() only accepts the "x-arcade" token for a device that passes
        # is_xarcade(d, xport) — i.e. a 045e at the IDENTIFIED port. Re-cable the
        # cab (routing.is_xarcade's own docstring: "re-cabling the stick ->
        # re-identify") and the token no longer answers for it, so _listed falls
        # through to `vidpid(d) in pad_classes` — and "045e:02a1" is NOT in the
        # token-spelled list. The whole cabinet drops out of the plan.
        #
        # Before this batch build_plan filtered on translatability alone, so both
        # halves were planned, the merger ran, and the cab played (correctly
        # seated, via rank()'s node fallback). Now: no pads -> no merger -> and
        # `sdl-ignore openbor` returns "" for the same reason, so openbor.sh's
        # `WL_RC==0 && -z WL` arm declares a DOCKED launch HANDHELD and sets
        # CANON=1 — the one gate that is supposed to stop us writing a cfg on a
        # launch we do not understand.
        halves = [dev(XA, "/dev/input/event10", phys="usb-xhci-hcd.2.auto-1.1/input0"),
                  dev(XA, "/dev/input/event11", phys="usb-xhci-hcd.2.auto-1.1/input1")]
        ifaces = {"/dev/input/event10": 0, "/dev/input/event11": 1}
        live = ["x-arcade", DS, DS4]
        with mock.patch.object(P, "usb_iface_num", side_effect=lambda p: ifaces.get(p)):
            # sanity: identified, it works
            self.assertEqual(len(P.build_plan(halves, live, "1.1")), 2)
            # stale identify (cab moved to another hub port)
            self.assertEqual(len(P.build_plan(halves, live, "1.2")), 2,
                             "a stale identify must not hide the cabinet")
            # identify never set / cleared
            self.assertEqual(len(P.build_plan(halves, live, "")), 2,
                             "an unidentified cabinet must not hide the cabinet")

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


class Pump(unittest.TestCase):
    """The event loop's failure behaviour: a lost pad must cost a pause, never the
    game, and a pad coming back must resume play."""

    class _Src:
        def __init__(self, exc=None, events=(), path="/dev/input/eventX"):
            self.exc, self.events, self.path = exc, list(events), path
            self.fd = abs(hash(path)) % 10000

        def read(self):
            if self.exc:
                raise self.exc
            return list(self.events)

    class _Twin:
        def __init__(self, slot, exc=None, path=None):
            self.slot = slot
            self.src = Pump._Src(exc, path=path or f"/dev/input/event{slot}")
            self.cls, self.fed, self.neutralized = "xpad", [], 0

        def feed(self, ev):
            self.fed.append(ev)

        def neutralize(self):
            self.neutralized += 1

    def _run(self, twins, rounds, reattach=None):
        by_fd = {t.src.fd: t for t in twins}
        seq, seen = list(rounds), []

        class _Stop(Exception):
            pass

        def fake_select(fds, _w, _x, _t):
            if not seq:
                raise _Stop()
            return ([fd for fd in seq.pop(0) if fd in by_fd], [], [])

        def _slept(*_a, **_k):
            raise AssertionError("pump() slept: the idle-forever hang is back")

        def _noop(_vacant, _busy):
            seen.append(True)
            return []

        with mock.patch.object(P, "log", lambda *_a, **_k: None), \
                mock.patch.object(P.time, "sleep", _slept):
            try:
                P.pump(by_fd, twins, reattach or _noop, _select=fake_select)
            except _Stop:
                pass
        return by_fd, seen

    def test_a_spurious_readable_never_drops_a_live_pad(self):
        # select() says readable but read() raises EAGAIN (verified on-device:
        # errno 11), and BlockingIOError is an OSError subclass -- the broad
        # handler would drop a LIVE pad and cost that player their controls.
        t = self._Twin(0, exc=BlockingIOError(11, "EAGAIN"))
        by_fd, _ = self._run([t], [[t.src.fd]] * 3)
        self.assertIn(t.src.fd, by_fd, "a live pad was dropped on a spurious readable")
        self.assertEqual(t.neutralized, 0)

    def test_losing_one_pad_of_two_keeps_the_other_playing(self):
        dead, alive = self._Twin(0, exc=OSError("gone")), self._Twin(1)
        by_fd, _ = self._run([dead, alive], [[dead.src.fd], [alive.src.fd]])
        self.assertNotIn(dead.src.fd, by_fd, "the dead source is still selected")
        self.assertIn(alive.src.fd, by_fd, "a live pad was dropped with its neighbour")
        self.assertEqual(dead.neutralized, 1, "the dead twin was left holding input")

    def test_losing_the_LAST_pad_does_NOT_end_the_game(self):
        # Miquel, 2026-07-17: "if a ds loses connection cause the battery runs out
        # ... the game should not get killed. what if i reconnect the pad or if i
        # connect another charged pad?" A dead battery costs a pause, not progress.
        # (An earlier fix made this EXIT so openbor.sh would kill the game. Wrong.)
        t = self._Twin(0, exc=OSError("gone"))
        by_fd, _ = self._run([t], [[t.src.fd], [], []])
        self.assertEqual(by_fd, {}, "the dead source is still selected")
        self.assertEqual(t.neutralized, 1)

    def test_it_keeps_asking_for_a_pad_while_a_slot_is_vacant(self):
        # The old code idled with nothing to wake up FOR. The idle was never the
        # bug; having no re-attach was.
        t = self._Twin(0, exc=OSError("gone"))
        _, seen = self._run([t], [[t.src.fd], [], [], []])
        self.assertGreaterEqual(len(seen), 2,
                                "nothing polls for a pad to fill the vacant slot")

    def test_a_returning_pad_resumes_play(self):
        dead = self._Twin(0, exc=OSError("gone"))
        fresh = Pump._Src(events=["ev"], path="/dev/input/event99")

        def reattach(vacant, _busy):
            out = []
            for t in vacant:
                t.src = fresh          # the pad is back (or a different one)
                out.append((fresh.fd, t))
            return out

        by_fd, _ = self._run([dead], [[dead.src.fd], [], [fresh.fd]], reattach)
        self.assertIn(fresh.fd, by_fd, "the returning pad never took the slot")
        self.assertEqual(dead.fed, ["ev"], "the twin is not being fed again")

    def test_a_failing_rescan_never_stops_the_pump(self):
        t = self._Twin(0, exc=OSError("gone"))

        def boom(_vacant, _busy):
            raise RuntimeError("udev exploded")

        by_fd, _ = self._run([t], [[t.src.fd], [], []], boom)
        self.assertEqual(by_fd, {})      # survived; no exception escaped

    def test_events_reach_the_twin(self):
        # Guard the guard: if pump stopped forwarding, the rest would pass for the
        # wrong reason.
        t = self._Twin(0)
        t.src.events = ["ev1", "ev2"]
        self._run([t], [[t.src.fd]])
        self.assertEqual(t.fed, ["ev1", "ev2"])


class Reattach(unittest.TestCase):
    """Which pad fills a vacant slot."""

    # usb_iface_num reads sysfs; map our fake paths the way the Plan tests do.
    IFACES = {"/dev/input/event22": 0, "/dev/input/event23": 1}

    def setUp(self):
        self._p = [
            mock.patch.object(P, "usb_iface_num",
                              side_effect=lambda p: self.IFACES.get(p)),
            mock.patch.object(P, "is_xarcade",
                              side_effect=lambda d, xp: d.vid == 0x045E and d.pid == 0x02A1),
        ]
        for m in self._p:
            m.start()

    def tearDown(self):
        for m in self._p:
            m.stop()

    def _mk(self, pad_classes=("x-arcade", DS), xport="1.0", want=None, scan=()):
        opened = []

        class _Fake:
            def __init__(self, path):
                self.path, self.fd = path, abs(hash(path)) % 10000
                opened.append(path)

            def grab(self):
                pass

        return P.make_reattach(list(pad_classes), xport, want or {},
                               _open=_Fake, _scan=lambda: list(scan)), opened

    class _T:
        def __init__(self, slot):
            self.slot, self.src, self.cls = slot, None, None

    def test_the_same_cabinet_goes_back_to_its_own_halves(self):
        # usb_iface_num is replug-stable, which is exactly why identity uses it:
        # a one-pass scan could drop :1.1 into P1 and swap the halves the merger
        # exists to pin.
        p1 = dev(XA, "/dev/input/event22")      # iface 0 per IFACES
        p2 = dev(XA, "/dev/input/event23")      # iface 1
        want = {0: P.slot_identity(p1, "1.0"), 1: P.slot_identity(p2, "1.0")}
        # Offer them in the WRONG order: identity must win, not scan order.
        r, _ = self._mk(want=want, scan=[p2, p1])
        t0, t1 = self._T(0), self._T(1)
        got = dict((t.slot, t.src.path) for _fd, t in r([t0, t1], set()))
        self.assertEqual(got, {0: "/dev/input/event22", 1: "/dev/input/event23"},
                         "the X-Arcade halves came back swapped")

    def test_a_different_charged_pad_can_take_the_slot(self):
        # The explicit ask: "what if i connect another charged pad?"
        gone = dev(DS, "/dev/input/event11")
        other = dev(DS, "/dev/input/event77")           # same model, different unit
        r, _ = self._mk(want={0: P.slot_identity(gone, "1.0")}, scan=[other])
        t = self._T(0)
        out = r([t], set())
        self.assertEqual(len(out), 1, "a charged replacement was refused")
        self.assertEqual(t.src.path, "/dev/input/event77")

    def test_it_updates_the_class_so_translation_follows_the_pad(self):
        # cls picks the evdev->canonical table. A DS4 standing in for an X-Arcade
        # must be read with the PS table or every button is wrong.
        r, _ = self._mk(pad_classes=("x-arcade", DS4), want={0: ("045e:02a1", None)},
                        scan=[dev(DS4, "/dev/input/event55")])
        t = self._T(0)
        r([t], set())
        self.assertEqual(t.cls, "ps", "the twin kept the old family's table")

    def test_an_unlisted_pad_is_never_taken(self):
        r, _ = self._mk(pad_classes=(DS,), scan=[dev(XA, "/dev/input/event22")])
        self.assertEqual(r([self._T(0)], set()), [],
                         "an unlisted family took a seat")

    def test_a_pad_already_driving_a_slot_is_not_stolen(self):
        busy = dev(DS, "/dev/input/event11")
        r, _ = self._mk(scan=[busy])
        self.assertEqual(r([self._T(1)], {"/dev/input/event11"}), [],
                         "it stole a live player's pad for another slot")

    def test_nothing_connected_is_simply_nothing(self):
        r, _ = self._mk(scan=[])
        self.assertEqual(r([self._T(0)], set()), [])
