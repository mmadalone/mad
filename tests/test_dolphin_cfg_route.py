"""Tests for the warn-only Dolphin route() (lib/dolphin_cfg) after the Wii source decider moved out.

Run:  python3 -m unittest tests.test_dolphin_cfg_route -v
"""
from __future__ import annotations

import logging
import unittest

from lib import dolphin_cfg
from lib import dolphin_wii_tdb

_LOG = logging.getLogger("test")


class Route(unittest.TestCase):
    def setUp(self):
        self._save = (dolphin_cfg.dolphinbar_present, dolphin_cfg.dolphinbar_wiimotes,
                      dolphin_wii_tdb.is_cc_capable)

    def tearDown(self):
        (dolphin_cfg.dolphinbar_present, dolphin_cfg.dolphinbar_wiimotes,
         dolphin_wii_tdb.is_cc_capable) = self._save

    def _bar(self, present, n=0):
        dolphin_cfg.dolphinbar_present = lambda: present
        dolphin_cfg.dolphinbar_wiimotes = lambda: n

    def test_bar_present_no_warn(self):
        self._bar(True, 1)
        self.assertFalse(dolphin_cfg.route({}, True, _LOG, "/ROMs/wii/x.rvz")["warn"])

    def test_no_bar_non_cc_warns(self):
        self._bar(False, 0)
        dolphin_wii_tdb.is_cc_capable = lambda rom: False
        self.assertTrue(dolphin_cfg.route({}, True, _LOG, "/ROMs/wii/x.rvz")["warn"])

    def test_no_bar_cc_capable_suppresses_warn(self):
        self._bar(False, 0)
        dolphin_wii_tdb.is_cc_capable = lambda rom: True
        self.assertFalse(dolphin_cfg.route({}, True, _LOG, "/ROMs/wii/cc.rvz")["warn"])

    def test_rom_none_is_back_compatible(self):
        self._bar(False, 0)
        # no ROM -> cannot check CC -> behaves like the old warn (require + no bar)
        self.assertTrue(dolphin_cfg.route({}, True, _LOG)["warn"])

    def test_require_false_never_warns(self):
        self._bar(False, 0)
        self.assertFalse(dolphin_cfg.route({}, False, _LOG, "/ROMs/wii/x.rvz")["warn"])

    def test_real2_threshold_reported(self):
        self._bar(True, 2)
        self.assertEqual(dolphin_cfg.route({"real2_min_wiimotes": 2}, False, _LOG)["mode"], "real2")


if __name__ == "__main__":
    unittest.main()
