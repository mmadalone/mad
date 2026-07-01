"""
Tests for the retail GunCon2 launch key (ps2guncon) - the pcsx2x6 fork launched with
-datapath ~/Applications/pcsx2x6-retail so retail PS2 lightgun co-op coexists with the
Namco arcade config. Locks the routing/binding invariants that keep the retail bind
pointed at the RETAIL ini and never the arcade portable config.

Run:  python3 -m unittest tests.test_ps2guncon -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from lib import pcsx2_cfg, switch_bind
from lib.madsrv import pads_cmds
from lib.policy import load_merged

RETAIL_INI = "Applications/pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini"


class Ps2GunconRouting(unittest.TestCase):
    def test_target_is_retail_ini(self):
        # the binder must write to the retail datapath ini, NOT the arcade portable one
        t = str(switch_bind._target("ps2guncon", "Crisis Zone.iso"))
        self.assertTrue(t.endswith(RETAIL_INI), t)
        self.assertNotIn("/pcsx2x6/PCSX2x6/", t)  # not the arcade portable root

    def test_two_players_no_multitap(self):
        self.assertEqual(switch_bind._PLAYERS["ps2guncon"], 2)
        self.assertEqual(pads_cmds.managed_players("ps2guncon"), 2)

    def test_non_transient(self):
        # ES-DE-only (like pcsx2x6); no Steam-UI context to revert, so not transient
        self.assertNotIn("ps2guncon", switch_bind._TRANSIENT)

    def test_policy_backend_points_at_retail_ini(self):
        m = load_merged()
        be = m.get("backends", {}).get("ps2guncon", {})
        self.assertTrue(be.get("config_file", "").endswith(RETAIL_INI), be)
        self.assertEqual(be.get("manage_pads"), 2)
        sysc = m.get("systems", {}).get("ps2guncon", {})
        self.assertTrue(sysc.get("router_skip"))

    def test_distinct_from_arcade_key(self):
        # ps2guncon and pcsx2x6 must resolve to DIFFERENT inis (split, no cross-wiring)
        self.assertNotEqual(str(switch_bind._target("ps2guncon", "x")),
                            str(switch_bind._target("pcsx2x6", "x")))


class Ps2GunconRelativeStrip(unittest.TestCase):
    def test_strips_retail_relative_keys(self):
        ini = ("[USB1]\nType = guncon2-retail\n"
               "guncon2-retail_Trigger = Pointer-0/LeftButton\n"
               "guncon2-retail_RelativeUp = Keyboard/I\n"
               "guncon2-retail_RelativeRight = Keyboard/L\n")
        f = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
        f.write(ini)
        f.close()
        try:
            changed = pcsx2_cfg.strip_guncon2_relative_binds(f.name)
            out = open(f.name).read()
        finally:
            os.unlink(f.name)
        self.assertTrue(changed)
        self.assertNotIn("Relative", out)
        self.assertIn("guncon2-retail_Trigger", out)  # non-relative binds preserved


if __name__ == "__main__":
    unittest.main()
