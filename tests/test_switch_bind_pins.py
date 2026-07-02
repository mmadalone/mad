"""switch_bind device-pin honoring (RetroArch-hub plan, Phase 0 item 5).

The standalone launch binder now honors the SAME [pins] model the RA router uses
(global [pins] + per-system [systems.<sys>.pins]) so the shared "Device pins" page
applies to standalones too. HARD INVARIANT: with NO pins set, the resolved order is
byte-identical to before (proved below). Pure logic — no hardware: FakeDevice stands
in for the evdev Device (where pin_id lives), SdlDevice for the SDL pad list; the
evdev->SDL bridge (routing.resolve_pins + devices.sdl_index_of) runs for real.

Run:  python3 -m unittest tests.test_switch_bind_pins -v
"""
from __future__ import annotations

import unittest

import lib.devices as devices
import lib.policy as policy
from lib import switch_bind
from tests._fakes import FakeDevice, sd

DS5 = "054c:0ce6"
XBOX = "045e:02a1"
GONE = "aaaa:bbbb"


def _fake(vidpid: str, path: str, name: str) -> FakeDevice:
    vid, pid = (int(x, 16) for x in vidpid.split(":"))
    return FakeDevice(vid=vid, pid=pid, path=path, name=name)


class PlacePins(unittest.TestCase):
    """_place_pins: pure positional reorder — pinned device to its slot, the rest
    fill the other slots in original order."""

    def _ord(self):
        return [sd(0, DS5, "gA", "DualSense"), sd(1, XBOX, "gB", "Xbox"),
                sd(2, "2dc8:6001", "gC", "8BitDo")]

    def test_empty_pins_is_identity(self):
        o = self._ord()
        self.assertIs(switch_bind._place_pins(o, {}), o)

    def test_pin_to_slot1_promotes(self):
        o = self._ord()
        out = switch_bind._place_pins(o, {1: o[1]})       # Xbox -> P1
        self.assertEqual([d.vidpid for d in out], [XBOX, DS5, "2dc8:6001"])

    def test_pin_to_middle_slot(self):
        o = self._ord()
        out = switch_bind._place_pins(o, {2: o[2]})       # 8BitDo -> P2
        self.assertEqual([d.vidpid for d in out], [DS5, "2dc8:6001", XBOX])

    def test_two_pins(self):
        o = self._ord()
        out = switch_bind._place_pins(o, {1: o[2], 2: o[0]})   # 8BitDo=P1, DS5=P2
        self.assertEqual([d.vidpid for d in out], ["2dc8:6001", DS5, XBOX])

    def test_pin_beyond_count_does_not_crash(self):
        o = [sd(0, DS5, "gA", "DualSense"), sd(1, XBOX, "gB", "Xbox")]
        out = switch_bind._place_pins(o, {5: o[1]})       # slot beyond device count
        # No crash, no gap/None; every device still present.
        self.assertEqual({d.vidpid for d in out}, {DS5, XBOX})


class EffPins(unittest.TestCase):
    """_eff_pins: global [pins] overlaid with the emulator's ES-DE system pins,
    derived emu->system from the Standalones catalog (pcsx2->ps2, xemu->xbox…)."""

    def setUp(self):
        self._saved = policy.load_merged

    def tearDown(self):
        policy.load_merged = self._saved

    def _merged(self, d):
        policy.load_merged = lambda: d

    def test_no_pins_returns_empty(self):
        self._merged({"systems": {"ps2": {"category": "console"}},
                      "backends": {"pcsx2": {"manage_pads": 2}}})
        self.assertEqual(switch_bind._eff_pins("pcsx2"), {})

    def test_global_pins(self):
        self._merged({"pins": {"1": "vidpid:" + XBOX}})
        self.assertEqual(switch_bind._eff_pins("pcsx2"), {"1": "vidpid:" + XBOX})

    def test_system_pins_via_emu_mapping(self):
        # pcsx2 -> ps2 (from standalones_cmds.STANDALONES); its per-system pin applies.
        self._merged({"systems": {"ps2": {"pins": {"1": "vidpid:" + XBOX}}}})
        self.assertEqual(switch_bind._eff_pins("pcsx2"), {"1": "vidpid:" + XBOX})

    def test_system_overrides_global(self):
        self._merged({"pins": {"1": "vidpid:" + DS5},
                      "systems": {"xbox": {"pins": {"1": "vidpid:" + XBOX}}}})
        # xemu -> xbox: the per-system pin wins over the global for that player.
        self.assertEqual(switch_bind._eff_pins("xemu"), {"1": "vidpid:" + XBOX})

    def test_emu_mapping_covers_expected(self):
        self.assertIn("ps2", switch_bind._emu_systems("pcsx2"))
        self.assertIn("xbox", switch_bind._emu_systems("xemu"))
        self.assertIn("ps3", switch_bind._emu_systems("rpcs3"))
        self.assertIn("switch", switch_bind._emu_systems("eden"))
        self.assertIn("switch", switch_bind._emu_systems("ryujinx"))
        self.assertIn("pcsx2x6", switch_bind._emu_systems("pcsx2x6"))


class ApplyPins(unittest.TestCase):
    """_apply_pins end-to-end: policy -> resolve_pins (evdev) -> sdl_index_of bridge
    -> _place_pins over the SDL pad list. The bridge (routing + devices) runs live."""

    def setUp(self):
        self._pol = policy.load_merged
        self._enum = devices.enumerate_devices
        self._sdl = devices.sdl_devices

    def tearDown(self):
        policy.load_merged = self._pol
        devices.enumerate_devices = self._enum
        devices.sdl_devices = self._sdl

    def _wire(self, merged, evdevs, sdl_all):
        policy.load_merged = lambda: merged
        devices.enumerate_devices = lambda: list(evdevs)
        devices.sdl_devices = lambda pump=True: list(sdl_all)

    def _two(self):
        # SDL: index 0 = DualSense, index 1 = Xbox.  evdev: matching two pads.
        ordered = [sd(0, DS5, "gA", "DualSense"), sd(1, XBOX, "gB", "Xbox")]
        evdevs = [_fake(DS5, "/dev/input/event3", "DualSense"),
                  _fake(XBOX, "/dev/input/event5", "Xbox")]
        return ordered, evdevs, list(ordered)

    def test_no_pins_is_identity_no_enumeration(self):
        # HARD INVARIANT: no pins -> the ordered list is returned unchanged, and NO
        # enumeration happens (enumerate/sdl left un-wired; a call would crash).
        policy.load_merged = lambda: {"backends": {"pcsx2": {"manage_pads": 2}}}
        devices.enumerate_devices = lambda: (_ for _ in ()).throw(
            AssertionError("enumerate_devices must NOT be called when unpinned"))
        devices.sdl_devices = lambda pump=True: (_ for _ in ()).throw(
            AssertionError("sdl_devices must NOT be called when unpinned"))
        ordered = [sd(0, DS5, "gA", "DualSense"), sd(1, XBOX, "gB", "Xbox")]
        self.assertIs(switch_bind._apply_pins("pcsx2", ordered), ordered)

    def test_global_pin_applies(self):
        ordered, evdevs, sdl_all = self._two()
        self._wire({"pins": {"1": "vidpid:" + XBOX}}, evdevs, sdl_all)
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)
        self.assertEqual([d.vidpid for d in out], [XBOX, DS5])   # Xbox forced to P1

    def test_per_system_pin_forces_slot(self):
        ordered, evdevs, sdl_all = self._two()
        self._wire({"systems": {"ps2": {"pins": {"1": "vidpid:" + XBOX}}}}, evdevs, sdl_all)
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)   # pcsx2 -> ps2
        self.assertEqual([d.vidpid for d in out], [XBOX, DS5])

    def test_pin_to_player2(self):
        ordered, evdevs, sdl_all = self._two()
        self._wire({"pins": {"2": "vidpid:" + XBOX}}, evdevs, sdl_all)
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)
        self.assertEqual([d.vidpid for d in out], [DS5, XBOX])   # Xbox pinned to P2

    def test_disconnected_pin_ignored(self):
        ordered, evdevs, sdl_all = self._two()
        self._wire({"pins": {"1": "vidpid:" + GONE}}, evdevs, sdl_all)   # not connected
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)
        self.assertEqual([d.vidpid for d in out], [DS5, XBOX])   # unchanged, graceful

    def test_pin_to_pad_not_in_candidate_list_ignored(self):
        # Pinned device is connected (evdev) but was filtered from `ordered`
        # (unsupported for this emu) -> its SDL index has no candidate -> skipped.
        ordered = [sd(1, XBOX, "gB", "Xbox")]                 # DS5 dropped from candidates
        evdevs = [_fake(DS5, "/dev/input/event3", "DualSense"),
                  _fake(XBOX, "/dev/input/event5", "Xbox")]
        sdl_all = [sd(0, DS5, "gA", "DualSense"), sd(1, XBOX, "gB", "Xbox")]
        self._wire({"pins": {"1": "vidpid:" + DS5}}, evdevs, sdl_all)
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)
        self.assertEqual([d.vidpid for d in out], [XBOX])     # unchanged

    def test_out_of_range_pin_is_noop(self):
        # review finding 1: a global P3 pin on a 2-player standalone (pcsx2 caps at 2)
        # must be DROPPED, never reshuffle P1. Xbox is P1 by order; pinning it to P3
        # must leave it at P1 (pre-fix it demoted Xbox to P2 and put DualSense on P1).
        ordered = [sd(1, XBOX, "gB", "Xbox"), sd(0, DS5, "gA", "DualSense")]  # Xbox=P1
        evdevs = [_fake(XBOX, "/dev/input/event5", "Xbox"),
                  _fake(DS5, "/dev/input/event3", "DualSense")]
        self._wire({"pins": {"3": "vidpid:" + XBOX}}, evdevs, list(ordered))
        out = switch_bind._apply_pins("pcsx2", ordered, quiet=True)
        self.assertEqual([d.vidpid for d in out], [XBOX, DS5])   # P3 dropped, order kept


if __name__ == "__main__":
    unittest.main()
