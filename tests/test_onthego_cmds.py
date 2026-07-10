"""MAD 'On-the-go' page backend (lib/madsrv/onthego_cmds.py).

Chooser tree shape, global mode enum round-trip + watt clamp, per-system enable/watt-cap-inherit,
the res-enum divergence (PS2/PS3 offer 2x, others don't), switch/wiiu no-res + note, and the policy
round-trip. Temp local.toml + stubbed staterev. Run: python3 -m unittest tests.test_onthego_cmds -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import onthego_cmds, rpc  # noqa: F401 (import registers the methods)


def call(name, **p):
    return rpc._METHODS[name][0](p)


class OnTheGo(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        import lib.policy as policy
        self._orig = policy.LOCAL
        policy.LOCAL = self.d / "controller-policy.local.toml"
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        import lib.policy as policy
        policy.LOCAL = self._orig
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _merged(self):
        import lib.policy as policy
        return policy.load_merged()

    def _row(self, ns, key):
        rows = [s for s in call(ns + ".get")["groups"][0]["settings"] if s["key"] == key]
        return rows[0] if rows else None

    def test_tree(self):
        secs = call("onthego.list")["tiles"][0]["sections"]
        self.assertEqual(secs[0]["arg"], "onthego_global")
        self.assertEqual(secs[1]["kind"], "group")
        self.assertEqual(len(secs[1]["sections"]), 12)

    def test_global_mode_roundtrip(self):
        for idx, (detect, force) in ((1, ("manual", "handheld")),
                                     (2, ("manual", "docked")),
                                     (0, ("display", ""))):
            call("onthego_global.set", key="mode", value=str(idx))
            hh = self._merged()["handheld"]
            self.assertEqual((hh["detect"], hh["force"]), (detect, force))
            self.assertEqual(self._row("onthego_global", "mode")["value"], idx)

    def test_watt_clamp(self):
        call("onthego_global.set", key="default_watt_cap", value="99")
        self.assertEqual(self._merged()["handheld"]["default_watt_cap"], 15)

    def test_per_system_inherit(self):
        call("onthego_ps2.set", key="watt_cap", value="13")
        row = self._row("onthego_ps2", "watt_cap")
        self.assertEqual((row["value"], row["inherited"]), (13, False))
        call("onthego_ps2.set", key="watt_cap", value="inherit")
        self.assertNotIn("watt_cap", self._merged()["systems"]["ps2"]["handheld"])
        self.assertTrue(self._row("onthego_ps2", "watt_cap")["inherited"])

    def test_res_enum_divergence(self):
        self.assertEqual(len(self._row("onthego_ps2", "res")["options"]), 3)   # Native/2x/Inherit
        self.assertEqual(len(self._row("onthego_psx", "res")["options"]), 2)   # Native/Inherit
        call("onthego_ps2.set", key="res", value="1")
        call("onthego_psx.set", key="res", value="1")
        m = self._merged()["systems"]
        self.assertEqual(m["ps2"]["handheld"]["res"], "2x")        # ps2 idx1 -> 2x
        self.assertEqual(m["psx"]["handheld"]["res"], "inherit")   # psx idx1 -> inherit (no 2x)

    def test_switch_wiiu_no_res_with_note(self):
        for ns in ("onthego_switch", "onthego_wiiu"):
            payload = call(ns + ".get")
            self.assertIsNone(self._row(ns, "res"))
            self.assertTrue(payload["note"])

    def test_enable_roundtrip(self):
        call("onthego_ps2.set", key="enable", value="1")
        self.assertTrue(self._merged()["systems"]["ps2"]["handheld"]["enabled"])


if __name__ == "__main__":
    unittest.main()
