"""P1: the router writes RA input PROFILES into the per-game override.

The bug this closes, measured on the live rig 2026-07-17: RetroArch polls hotkeys on ONE port, and
the global retroarch.cfg's six raw numbers are X-Arcade-shaped (modifier=6=Select). Launch an
arcade game with the cabinet UNPLUGGED and a DualSense takes P1, where 6 is L2, 7 is R2 and 13/14
do not exist at all (a DualSense reaches index 12). The router now resolves the seated family's
profile per launch and writes it into the same transient block it already owns.

Hotkeys ride the per-game override, which is verified in libretro source at v1.22.2 rather than
assumed: config_load_override appends the override into the SAME config_file_t as retroarch.cfg,
and config_read_keybinds_conf then parses the merged result across the FULL bind map, reading
_btn/_axis/_mbtn. The only ident blocklist is on the SAVE path. So no new rail was needed.

Two halves:
  * _build_block / write_override carrying `extra` (full RA keys, verbatim);
  * _setup choosing a profile per seated port and NOT double-writing its binds.

Run:  python3 -m unittest tests.test_ra_profiles_router -v
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import retroarch_cfg as rcfg
from tests._fakes import FakeDevice

SYS = "testsys"
ROM = "Test Game (USA)"
XA_PHYS = "usb-0000:04:00.3-1.1/input0"
XPORT = "1.1"

ARCADE = {"hotkeys": {"modifier": "select", "rewind": "left", "fast_forward": "right",
                      "slowmotion": "r", "menu": "start", "quit": "mbtn:3"}}
GAMEPAD = {"hotkeys": {"modifier": "l3", "rewind": "l2", "fast_forward": "r2",
                       "slowmotion": "r", "menu": "start", "quit": ""}}
XARCADE_BASE = {"a_btn": "0", "b_btn": "1", "select_btn": "6", "start_btn": "7",
                "r_btn": "5", "left_btn": "h0left", "right_btn": "h0right"}
DS_BASE = {"a_btn": "1", "b_btn": "0", "select_btn": "8", "start_btn": "9",
           "r_btn": "5", "l3_btn": "11", "l2_axis": "+2", "r2_axis": "+5"}


def _load_router():
    spec = importlib.util.spec_from_file_location("cr_under_test", "controller-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class BuildBlock(unittest.TestCase):
    """`extra` is written verbatim: hotkey keys have NO player prefix (meta binds are user-0 only),
    so a per-port template cannot express them."""

    def test_extra_lines_are_emitted_sorted(self):
        body = rcfg._build_block({1: "vid:pid Pad"}, None, None,
                                 {"input_enable_hotkey_btn": "6", "input_rewind_btn": "h0left"})
        self.assertIn('input_enable_hotkey_btn = "6"', body)
        self.assertIn('input_rewind_btn = "h0left"', body)
        self.assertLess(body.index("input_enable_hotkey_btn"), body.index("input_rewind_btn"))

    def test_no_extra_is_unchanged(self):
        self.assertEqual(rcfg._build_block({1: "x"}, None, None, None),
                         rcfg._build_block({1: "x"}))

    def test_extra_alone_still_builds(self):
        self.assertIn('input_enable_hotkey_btn = "6"',
                      rcfg._build_block({}, None, None, {"input_enable_hotkey_btn": "6"}))


class WriteOverride(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ra-prof-test-"))
        (self.tmp / "FakeCore").mkdir()
        self._saved = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore"]}

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _txt(self):
        return (self.tmp / "FakeCore" / f"{ROM}.cfg").read_text()

    def test_hotkeys_land_in_the_override(self):
        rcfg.write_override(SYS, ROM, {1: "054c:0ce6 DualSense"}, None, None,
                            {"input_enable_hotkey_btn": "11", "input_rewind_axis": "+2"})
        t = self._txt()
        self.assertIn('input_enable_hotkey_btn = "11"', t)
        self.assertIn('input_rewind_axis = "+2"', t)
        self.assertIn(rcfg.BEGIN, t)                       # inside the router's own sentinel

    def test_extra_alone_is_enough_to_write(self):
        # The guard used to demand port_names/mouse_indices. A profile with only hotkeys must not
        # be silently dropped. (P2 needs this for real: handheld with no external pad,
        # resolve_ports filters the Deck's Steam-virtual pad and port_names comes back EMPTY.)
        w = rcfg.write_override(SYS, ROM, {}, None, None, {"input_enable_hotkey_btn": "11"})
        self.assertTrue(w)
        self.assertIn('input_enable_hotkey_btn = "11"', self._txt())

    def test_nothing_at_all_writes_nothing(self):
        self.assertEqual(rcfg.write_override(SYS, ROM, {}, None, None, None), [])

    def test_clear_override_removes_the_hotkeys_too(self):
        # The block is TRANSIENT: whatever the profile wrote must leave with it at game-end, or a
        # DualSense's L3 modifier would outlive the launch and confuse the cabinet.
        rcfg.write_override(SYS, ROM, {1: "x"}, None, None, {"input_enable_hotkey_btn": "11"})
        rcfg.clear_override(SYS, ROM)
        f = self.tmp / "FakeCore" / f"{ROM}.cfg"
        self.assertTrue(not f.exists() or "input_enable_hotkey_btn" not in f.read_text())


class RouterSetup(unittest.TestCase):
    """_setup resolves a profile per seated port."""

    def setUp(self):
        self.cr = _load_router()
        self.tmp = Path(tempfile.mkdtemp(prefix="ra-router-test-"))
        self.policy = {
            "systems": {SYS: {"category": "arcade", "ports": [["X-Arcade", "DualSense"],
                                                              ["X-Arcade", "DualSense"]]}},
            "hardware": {"xarcade_port": XPORT},
            "ra_profiles": {"Arcade": ARCADE, "Gamepad": GAMEPAD},
            "ra_profile_map": {"X-Arcade": "Arcade", "DualSense": "Gamepad"},
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, devs, driver="udev", policy=None, binds=None):
        pol = policy if policy is not None else self.policy
        log = mock.Mock()
        ctx = self.cr.GameContext(rom_path=f"/x/{ROM}.zip", name=ROM, system=SYS,
                                  fullname=ROM, collection=None, policy_key=SYS)
        bind_fn = (lambda d: dict(binds)) if binds else (lambda d: None)
        with mock.patch.object(self.cr, "load_policy", return_value=pol), \
             mock.patch.object(self.cr, "enumerate_devices", return_value=devs), \
             mock.patch.object(self.cr, "core_dirs_for_system", return_value=[self.tmp]), \
             mock.patch.object(rcfg, "core_dirs_for_system", return_value=[self.tmp]), \
             mock.patch.object(self.cr, "_ra_on_the_go", return_value=driver), \
             mock.patch.object(self.cr, "ra_mouse_hotkey_bound", return_value=False), \
             mock.patch.object(self.cr, "binds_for", side_effect=bind_fn), \
             mock.patch.object(self.cr.ra_profiles.device_binds, "binds_for",
                               side_effect=lambda d: dict(XARCADE_BASE)
                               if d.vid == 0x045e else dict(DS_BASE)), \
             mock.patch.object(self.cr, "_show_warning_blocking", return_value=0):
            self.cr._setup(ctx, log)
        f = self.tmp / f"{ROM}.cfg"
        return (f.read_text() if f.exists() else ""), log

    def _cab(self, path="/dev/input/event22"):
        return FakeDevice(vid=0x045e, pid=0x02a1, path=path,
                          name="Xbox 360 Wireless Receiver", phys=XA_PHYS)

    def _ds(self, path="/dev/input/event27"):
        return FakeDevice(vid=0x054c, pid=0x0ce6, path=path,
                          name="DualSense Wireless Controller", phys="usb-x-1/input0")

    def test_the_xarcade_gets_the_arcade_profile(self):
        txt, _ = self._run([self._cab()])
        self.assertIn('input_enable_hotkey_btn = "6"', txt)        # Select
        self.assertIn('input_menu_toggle_btn = "7"', txt)          # Start
        self.assertIn('input_rewind_btn = "h0left"', txt)          # kernel-proof, was raw 13
        self.assertIn('input_exit_emulator_mbtn = "3"', txt)       # trackball red button

    def test_a_dualsense_on_p1_gets_the_gamepad_profile(self):
        # THE BUG: X-Arcade top of the list but unplugged -> the DualSense takes P1 and used to
        # inherit the cabinet's numbers.
        txt, _ = self._run([self._ds()])
        self.assertIn('input_enable_hotkey_btn = "11"', txt)       # L3, not 6 (=L2 on a DS)
        self.assertIn('input_rewind_axis = "+2"', txt)             # LT, not the nonexistent 13
        self.assertIn('input_hold_fast_forward_axis = "+5"', txt)  # RT, not the nonexistent 14
        self.assertIn('input_menu_toggle_btn = "9"', txt)          # Options, not 7 (=R2 on a DS)

    def test_stale_variants_are_cleared_not_left_to_fire(self):
        # The global cfg has input_rewind_btn = "13". The override must NUL it, or 13 and the new
        # axis would both be live.
        txt, _ = self._run([self._ds()])
        self.assertIn('input_rewind_btn = "nul"', txt)
        self.assertIn('input_exit_emulator_mbtn = "nul"', txt)     # no trackball on this launch

    def test_hotkeys_are_written_once_for_p1_only(self):
        txt, _ = self._run([self._cab("/dev/input/event22"), self._cab("/dev/input/event23")])
        self.assertEqual(txt.count("input_enable_hotkey_btn"), 1)
        self.assertIn('input_player2_a_btn', txt)                  # P2 still gets gameplay binds

    def test_a_profiled_port_does_not_also_get_legacy_device_binds(self):
        # Same key from two writers would land twice in the block. The profile's base map IS
        # binds_for's answer, so a profile'd port must skip the legacy copy.
        txt, _ = self._run([self._ds()], binds={"a_btn": "9"})
        self.assertEqual(txt.count("input_player1_a_btn"), 1)
        self.assertIn('input_player1_a_btn = "1"', txt)            # from the profile's base map

    def test_an_unmapped_family_falls_back_to_device_binds(self):
        pol = dict(self.policy, ra_profile_map={})                 # nothing assigned
        txt, _ = self._run([self._ds()], policy=pol, binds={"a_btn": "9"})
        self.assertIn('input_player1_a_btn = "9"', txt)            # legacy path, unchanged
        self.assertNotIn("input_enable_hotkey_btn", txt)           # and no hotkeys

    def test_a_profile_name_with_no_definition_warns_and_falls_back(self):
        pol = dict(self.policy, ra_profile_map={"DualSense": "Ghost"})
        txt, log = self._run([self._ds()], policy=pol, binds={"a_btn": "9"})
        self.assertIn('input_player1_a_btn = "9"', txt)
        self.assertNotIn("input_enable_hotkey_btn", txt)
        self.assertTrue(log.warning.called)

    def test_not_an_ra_launch_writes_no_profile(self):
        # driver None = _ra_on_the_go said "standalone". Never guess a number space.
        txt, _ = self._run([self._ds()], driver=None, binds={"a_btn": "9"})
        self.assertNotIn("input_enable_hotkey_btn", txt)
        self.assertIn('input_player1_a_btn = "9"', txt)

    def test_the_driver_is_threaded_not_read_back(self):
        # sdl2 must resolve through the SDL semantic table, NOT the pad's udev autoconfig. Reading
        # input_joypad_driver back would race the write _ra_on_the_go just made.
        txt, _ = self._run([self._ds()], driver="sdl2")
        self.assertIn('input_enable_hotkey_btn = "7"', txt)        # L3 under sdl2 (11 under udev)
        self.assertIn('input_rewind_axis = "+4"', txt)             # LT under sdl2 (+2 under udev)


if __name__ == "__main__":
    unittest.main()
