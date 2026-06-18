"""Tests for the device-scoped RA Input writer (item ④): lib/device_binds
set_device_bind / get_device_binds / the binds_for FC30 sentinel overlay. No
hardware — a temp autoconfig dir stands in for RetroArch's udev autoconfig.

Run:  python3 -m unittest tests.test_device_binds -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import device_binds as db
from tests._fakes import FakeDevice

DS = FakeDevice(vid=0x054c, pid=0x0ce6, path="/dev/input/event3",
                name="DualSense Wireless Controller")
FC30 = FakeDevice(vid=0x2dc8, pid=0x2810, path="/dev/input/event4",
                  name="8BitDo FC30 GamePad")

STOCK_DS = (
    'input_driver = "udev"\n'
    'input_device = "DualSense Wireless Controller"\n'
    'input_vendor_id = "1356"\n'
    'input_product_id = "3302"\n'
    'input_a_btn = "1"\n'
    'input_b_btn = "0"\n'
    'input_x_btn = "3"\n'
    'input_l2_axis = "+4"\n'
)


class DeviceBinds(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig = db._AUTOCONF_DIR
        db._AUTOCONF_DIR = self.dir

    def tearDown(self):
        db._AUTOCONF_DIR = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _stock(self, name, text):
        (self.dir / f"{name}.cfg").write_text(text)

    def test_set_creates_minimal_profile_when_absent(self):
        path = db.set_device_bind(DS, "a_btn", "2")
        self.assertTrue(path.exists())
        txt = path.read_text()
        self.assertIn('input_device = "DualSense Wireless Controller"', txt)
        self.assertIn(db.DEV_BEGIN, txt)
        self.assertIn('input_a_btn = "2"', txt)
        self.assertEqual(db.get_device_binds(DS).get("a_btn"), "2")

    def test_sentinel_overrides_stock_last_wins(self):
        self._stock("DualSense Wireless Controller", STOCK_DS)
        self.assertEqual(db._autoconfig_binds(DS)["a_btn"], "1")     # stock
        db.set_device_bind(DS, "a_btn", "5")
        self.assertEqual(db._autoconfig_binds(DS)["a_btn"], "5")     # sentinel wins
        self.assertEqual(db._autoconfig_binds(DS)["b_btn"], "0")     # untouched stock
        txt = (self.dir / "DualSense Wireless Controller.cfg").read_text()
        self.assertIn('input_b_btn = "0"', txt)                      # stock preserved
        self.assertIn(db.DEV_BEGIN, txt)

    def test_idempotent_and_one_time_bak(self):
        self._stock("DualSense Wireless Controller", STOCK_DS)
        p = self.dir / "DualSense Wireless Controller.cfg"
        db.set_device_bind(DS, "a_btn", "5")
        once = p.read_text()
        db.set_device_bind(DS, "a_btn", "5")
        self.assertEqual(p.read_text(), once)                        # idempotent
        self.assertTrue((self.dir / "DualSense Wireless Controller.cfg.mad-bak").exists())

    def test_axis_suffix_accepted(self):
        db.set_device_bind(DS, "l2_axis", "+2")
        self.assertEqual(db.get_device_binds(DS).get("l2_axis"), "+2")

    def test_fc30_profile_overlay(self):
        base = db.binds_for(FC30)                                    # no autoconfig yet
        self.assertEqual(base["a_btn"], "1")
        self.assertEqual(base["start_btn"], "11")
        db.set_device_bind(FC30, "start_btn", "6")                   # user remap
        over = db.binds_for(FC30)
        self.assertEqual(over["start_btn"], "6")                     # user edit wins
        self.assertEqual(over["a_btn"], "1")                         # FC30 profile intact

    def test_non_fc30_without_autoconfig_returns_none(self):
        # A device with neither a profile nor an autoconfig → no bind lines.
        unknown = FakeDevice(vid=0x1234, pid=0x5678, path="/dev/input/event9", name="Mystery Pad")
        self.assertIsNone(db.binds_for(unknown))


if __name__ == "__main__":
    unittest.main()
