"""Tests for the device-scoped RetroArch Input dual-mode (item ④):
retroarch_cmds.input_get/input_set route per-player binds to a controller's
autoconfig in device mode, but keep hotkeys (and the non-reservable Deck pad) on
the global cfg. The global-cfg writers are stubbed so the test never touches the
real retroarch.cfg; the device autoconfig lands in a temp dir.

Run:  python3 -m unittest tests.test_ra_input_device -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import device_binds as db
from lib.madsrv import retroarch_cmds as rc
from tests._fakes import FakeDevice

DEV = FakeDevice(vid=0x054c, pid=0x0ce6, path="/dev/input/event3",
                 name="DualSense Wireless Controller")
DEVICE = {"vidpid": "054c:0ce6", "name": DEV.name}


class RaInputDeviceMode(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._adir = db._AUTOCONF_DIR
        db._AUTOCONF_DIR = self.dir
        # Stub everything that would hit hardware or the real global cfg.
        self._saved = {
            "resolve": rc._resolve_device, "pads": rc._connected_pads,
            "run": rc.proc_guard.retroarch_running,
            "setg": rc.retroarch_cfg.set_global_option,
            "getg": rc.retroarch_cfg.get_global_option,
            "getgs": rc.retroarch_cfg.get_global_options,
        }
        self.global_writes = []
        rc._resolve_device = lambda device: DEV if device else None
        rc._connected_pads = lambda: [{"vidpid": "054c:0ce6", "name": DEV.name,
                                       "label": DEV.name, "reservable": True}]
        rc.proc_guard.retroarch_running = lambda: False
        rc.retroarch_cfg.set_global_option = lambda k, v: self.global_writes.append((k, v))
        rc.retroarch_cfg.get_global_option = lambda k: ""
        rc.retroarch_cfg.get_global_options = lambda keys: {}

    def tearDown(self):
        rc._resolve_device = self._saved["resolve"]
        rc._connected_pads = self._saved["pads"]
        rc.proc_guard.retroarch_running = self._saved["run"]
        rc.retroarch_cfg.set_global_option = self._saved["setg"]
        rc.retroarch_cfg.get_global_option = self._saved["getg"]
        rc.retroarch_cfg.get_global_options = self._saved["getgs"]
        db._AUTOCONF_DIR = self._adir
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_device_set_writes_autoconfig_not_global(self):
        res = rc._input_set({"key": "input_player1_a_btn", "value": "5", "device": DEVICE})
        self.assertEqual(res["scope"], "device")
        self.assertEqual(db.get_device_binds(DEV).get("a_btn"), "5")   # in autoconfig
        self.assertEqual(self.global_writes, [])                        # NOT global

    def test_device_get_reads_autoconfig(self):
        db.set_device_bind(DEV, "a_btn", "7")
        res = rc._input_get({"player": 1, "device": DEVICE})
        self.assertEqual(res["mode"], "device")
        binds = {b["key"]: b for g in res["groups"] for b in g["binds"]}
        self.assertEqual(binds["input_player1_a_btn"]["value"], "7")

    def test_hotkey_in_device_mode_stays_global(self):
        res = rc._input_set({"key": "input_menu_toggle_btn", "value": "9", "device": DEVICE})
        self.assertEqual(res["scope"], "global")
        self.assertEqual(self.global_writes, [("input_menu_toggle_btn", "9")])
        # nothing leaked into the device autoconfig
        self.assertEqual(db.get_device_binds(DEV), {})

    def test_global_mode_without_device(self):
        res = rc._input_set({"key": "input_player1_a_btn", "value": "5"})
        self.assertEqual(res["scope"], "global")
        self.assertEqual(self.global_writes, [("input_player1_a_btn", "5")])


if __name__ == "__main__":
    unittest.main()
