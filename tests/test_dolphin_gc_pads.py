"""Tests for the GameCube "Pads -> players" profile-priority backend (dolphin_gc_pads_cmds) and the
launch-time assigner (lib/dolphin_gc_pads).

Run:  python3 -m unittest tests.test_dolphin_gc_pads -v
"""
from __future__ import annotations

import unittest
from collections import Counter

from lib import dolphin_gc_pads as launch
from lib import dolphin_profiles
from lib.madsrv import cfgutil
from lib.madsrv import dolphin_gc_pads_cmds as pc


class Backend(unittest.TestCase):
    def setUp(self):
        self.store: dict = {}
        self._orig = (pc._be, pc._set_pref, pc._connected_names,
                      dolphin_profiles.list_profiles, dolphin_profiles.profile_device,
                      pc.proc_guard.emulator_running)
        pc._be = lambda: dict(self.store)
        pc._set_pref = lambda k, v: (self.store.__setitem__(k, v) if v not in (None, [], False)
                                     else self.store.pop(k, None))
        pc._connected_names = lambda: {"DualSense Wireless Controller"}
        dolphin_profiles.list_profiles = lambda: ["GC WiiU 1", "GC Dualsense 1", "GC Dualsense 2"]
        dolphin_profiles.profile_device = lambda n: {
            "GC WiiU 1": "Nintendo Wii Remote Pro Controller",
            "GC Dualsense 1": "DualSense Wireless Controller",
            "GC Dualsense 2": "DualSense Wireless Controller"}.get(n)
        pc.proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        (pc._be, pc._set_pref, pc._connected_names,
         dolphin_profiles.list_profiles, dolphin_profiles.profile_device,
         pc.proc_guard.emulator_running) = self._orig

    def test_get_profiles_as_ids_with_connected_flag(self):
        r = pc._pads_get({"emu": "dolphin_gc"})
        self.assertEqual((r["emu"], r["players"]), ("dolphin_gc", 4))
        self.assertEqual({p["id"] for p in r["pads"]},
                         {"GC WiiU 1", "GC Dualsense 1", "GC Dualsense 2"})
        conn = {p["id"]: p["connected"] for p in r["pads"]}
        self.assertTrue(conn["GC Dualsense 1"])          # DualSense present
        self.assertFalse(conn["GC WiiU 1"])              # Wii U Pro absent
        lbl = {p["id"]: p["label"] for p in r["pads"]}
        self.assertIn("●", lbl["GC Dualsense 1"])
        self.assertNotIn("●", lbl["GC WiiU 1"])

    def test_get_stored_order_first_then_appended(self):
        self.store["pads_priority"] = ["GC Dualsense 2", "GC WiiU 1"]
        ids = [p["id"] for p in pc._pads_get({"emu": "dolphin_gc"})["pads"]]
        self.assertEqual(ids[:2], ["GC Dualsense 2", "GC WiiU 1"])   # stored first
        self.assertIn("GC Dualsense 1", ids[2:])                     # new appended

    def test_set_roundtrips_valid_only(self):
        pc._pads_set({"emu": "dolphin_gc", "order": ["GC Dualsense 1", "bogus", "GC WiiU 1"]})
        self.assertEqual(self.store["pads_priority"], ["GC Dualsense 1", "GC WiiU 1"])  # bogus dropped

    def test_hands_off_toggle(self):
        pc._pads_hands_off({"emu": "dolphin_gc", "value": True})
        self.assertTrue(self.store["pads_hands_off"])
        pc._pads_hands_off({"emu": "dolphin_gc", "value": False})
        self.assertNotIn("pads_hands_off", self.store)               # cleared

    def test_running_note(self):
        pc.proc_guard.emulator_running = lambda *a, **k: True
        r = pc._pads_get({"emu": "dolphin_gc"})
        self.assertTrue(r["running"])
        self.assertIn("Close Dolphin", r["note"])

    def test_pad_connected_exact_not_substring(self):
        # regression: exact match only — a bare 'Wireless Controller' (PS4) profile must NOT match a
        # 'DualSense Wireless Controller' pad, and vice versa; only an exact name counts.
        self.assertFalse(pc._pad_connected("Wireless Controller", {"DualSense Wireless Controller"}))
        self.assertFalse(pc._pad_connected("DualSense Wireless Controller", {"Wireless Controller"}))
        self.assertTrue(pc._pad_connected("DualSense Wireless Controller",
                                          {"DualSense Wireless Controller"}))

    def test_connected_names_includes_sdl(self):
        # the ● dot must see BOTH evdev and SDL names, so a DS4 profile ('PS4 Controller') is marked
        # connected even though its evdev name is 'Wireless Controller'.
        import types
        from lib import devices as dv
        real = self._orig[2]                               # the un-mocked pc._connected_names
        saved = (dv.enumerate_devices, dv.joypads, dv.sdl_devices)
        self.addCleanup(lambda: setattr(dv, "enumerate_devices", saved[0]))
        self.addCleanup(lambda: setattr(dv, "joypads", saved[1]))
        self.addCleanup(lambda: setattr(dv, "sdl_devices", saved[2]))
        dv.enumerate_devices = lambda: ["_"]
        dv.joypads = lambda devs: [types.SimpleNamespace(name="Wireless Controller")]
        dv.sdl_devices = lambda: [types.SimpleNamespace(name="PS4 Controller"),
                                  types.SimpleNamespace(name="")]
        self.assertEqual(real(), {"Wireless Controller", "PS4 Controller"})

    def test_no_profiles_note(self):
        dolphin_profiles.list_profiles = lambda: []
        r = pc._pads_get({"emu": "dolphin_gc"})
        self.assertEqual(r["pads"], [])
        self.assertIn("No GameCube profiles", r["note"])


# vid:pid classes used by the launch tests
VP_WIIU, VP_DS, VP_DS4 = "057e:0330", "054c:0ce6", "054c:09cc"
# every connected pad's evdev AND SDL name -> its vid:pid (what _connected_index builds); note the
# DS4 has TWO names for one vid:pid: evdev 'Wireless Controller' + SDL 'PS4 Controller'.
_NAME_TO_VP = {"Nintendo Wii Remote Pro Controller": VP_WIIU,
               "DualSense Wireless Controller": VP_DS,
               "Wireless Controller": VP_DS4, "PS4 Controller": VP_DS4}


class LaunchAssign(unittest.TestCase):
    def setUp(self):
        self._orig = (launch.prefs.priority, launch.prefs.hands_off, launch._connected_index,
                      dolphin_profiles.profile_device, dolphin_profiles.profile_body,
                      dolphin_profiles.apply_profile_body)
        launch.prefs.hands_off = lambda: False
        dolphin_profiles.profile_device = lambda n: {
            "WiiU": "Nintendo Wii Remote Pro Controller",
            "DS-A": "DualSense Wireless Controller",
            "DS-B": "DualSense Wireless Controller",
            "DS4": "PS4 Controller"}.get(n)               # profile targets the SDL name

    def tearDown(self):
        (launch.prefs.priority, launch.prefs.hands_off, launch._connected_index,
         dolphin_profiles.profile_device, dolphin_profiles.profile_body,
         dolphin_profiles.apply_profile_body) = self._orig

    def _connected(self, **vp_counts):
        launch._connected_index = lambda: (Counter(vp_counts), dict(_NAME_TO_VP))

    def test_priority_walk_skips_absent(self):
        launch.prefs.priority = lambda: ["WiiU", "DS-A"]
        self._connected(**{VP_DS: 1})                     # only a DualSense
        self.assertEqual(launch.plan_assignment(), [(1, "DS-A")])   # WiiU absent -> skipped

    def test_ds4_sdl_name_matches_via_vidpid(self):
        # the reported bug: a DS4 profile targets SDL 'PS4 Controller' but the pad's evdev name is
        # 'Wireless Controller'; matching by vid:pid resolves both -> the DS4 profile IS assigned.
        launch.prefs.priority = lambda: ["DS4"]
        self._connected(**{VP_DS4: 1})
        self.assertEqual(launch.plan_assignment(), [(1, "DS4")])

    def test_same_type_pads_consume_distinct_instances(self):
        launch.prefs.priority = lambda: ["DS-A", "DS-B", "WiiU"]
        self._connected(**{VP_DS: 2})
        self.assertEqual(launch.plan_assignment(), [(1, "DS-A"), (2, "DS-B")])  # two DS -> P1,P2
        self._connected(**{VP_DS: 1})
        self.assertEqual(launch.plan_assignment(), [(1, "DS-A")])   # one DS -> only first

    def test_ds4_does_not_steal_dualsense(self):
        # a DS4 profile ('PS4 Controller' -> 054c:09cc) must NOT consume a connected DualSense
        # (054c:0ce6); the genuine DualSense profile still gets the port.
        launch.prefs.priority = lambda: ["DS4", "DS-A"]
        self._connected(**{VP_DS: 1})                     # only a DualSense (no DS4)
        self.assertEqual(launch.plan_assignment(), [(1, "DS-A")])

    def test_hands_off_and_empty_noop(self):
        launch.prefs.priority = lambda: ["DS-A"]
        self._connected(**{VP_DS: 1})
        launch.prefs.hands_off = lambda: True
        self.assertEqual(launch.plan_assignment(), [])
        launch.prefs.hands_off = lambda: False
        launch.prefs.priority = lambda: []
        self.assertEqual(launch.plan_assignment(), [])

    def test_cap_at_four_ports(self):
        launch.prefs.priority = lambda: ["DS-A", "DS-B", "DS-A", "DS-B", "DS-A"]
        self._connected(**{VP_DS: 9})
        self.assertEqual([p for p, _ in launch.plan_assignment()], [1, 2, 3, 4])

    def test_assign_text_block_copies_into_ports(self):
        launch.prefs.priority = lambda: ["DS-A"]
        self._connected(**{VP_DS: 1})
        dolphin_profiles.profile_body = lambda n: (
            "Device = SDL/0/DualSense Wireless Controller\nButtons/A = `Button S`\n")
        text = "[GCPad1]\nDevice = old\nButtons/A = EAST\n[GCPad2]\nDevice = other\n"
        nt, applied = launch.assign_text(text)                       # real apply_profile_body
        self.assertEqual(applied, [(1, "DS-A")])
        self.assertEqual(cfgutil.ini_read(nt, "GCPad1", "Device"), "SDL/0/DualSense Wireless Controller")
        self.assertEqual(cfgutil.ini_read(nt, "GCPad2", "Device"), "other")   # other ports untouched


if __name__ == "__main__":
    unittest.main()
