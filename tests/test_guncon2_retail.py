"""Tests for the unified retail PS2 GunCon2 MAD page (guncon2_retail.*).

ONE input_map page carries the button binds, the crosshair + Sinden-border selectors, and
a dynamic Start/Stop Sinden button - all targeting the retail -datapath ini. A single
section attaches to the PlayStation 2 (pcsx2) tile, gated on the retail setup being installed.

Run:  python3 -m unittest tests.test_guncon2_retail -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import inifile
from lib.madsrv import rpc, standalones_cmds
from lib.madsrv import guncon2_retail_input_cmds as gin

# The single retail GunCon2 section attaches to the PlayStation 2 (pcsx2) tile.
ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2")


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("guncon2_retail.input_get", "guncon2_retail.input_set",
                  "guncon2_retail.selector_set"):
            self.assertIn(m, rpc._METHODS, m)

    def test_one_unified_section_on_ps2_tile_when_installed(self):
        # PS2 tile is a nested menu; flatten (recurse into group rows) to check leaf sections.
        def flat(secs):
            out = []
            for s in secs:
                if s.get("kind") == "group":
                    out.extend(flat(s.get("sections", [])))
                else:
                    out.append((s["kind"], s.get("arg")))
            return out
        orig = standalones_cmds._pcsx2x6_has_guncon2_retail
        try:
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: True
            on = flat(standalones_cmds._sections_for(ENTRY))
            standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: False
            off = flat(standalones_cmds._sections_for(ENTRY))
        finally:
            standalones_cmds._pcsx2x6_has_guncon2_retail = orig
        # exactly ONE unified retail section, present when installed (now inside the PS2
        # tile's "Input" group rather than appended last).
        self.assertEqual([a for _, a in on].count("guncon2_retail"), 1)
        self.assertIn(("input_map", "guncon2_retail"), on)
        # gated off -> the same sections minus the guncon2 one
        self.assertEqual(off, [x for x in on if x != ("input_map", "guncon2_retail")])
        self.assertNotIn("guncon2_retail", [a for _, a in off])
        self.assertNotIn("guncon2_retail_lightgun", [a for _, a in on])   # no separate crosshair page

    def test_targets_retail_datapath_ini(self):
        self.assertTrue(str(gin._INI).endswith("pcsx2x6-retail/PCSX2x6/inis/PCSX2.ini"))
        self.assertNotIn("/pcsx2x6/PCSX2x6/", str(gin._INI))   # not the arcade portable ini


class Page(unittest.TestCase):
    def _ini(self):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text("[USB1]\nType = guncon2-retail\n\n[USB2]\nType = guncon2-retail\n\n"
                       "[JVS]\nTestMode = false\n", encoding="utf-8")
        return ini

    def _with(self, ini, fn):
        oi, orun = gin._INI, gin._running
        try:
            gin._INI, gin._running = ini, (lambda: False)
            return fn()
        finally:
            gin._INI, gin._running = oi, orun

    def test_full_bind_set_no_relative(self):
        g = self._with(self._ini(), lambda: gin._input_get({"player": "usb1"}))
        ids = [b["id"] for grp in g["groups"] for b in grp["binds"]]
        self.assertEqual(set(ids), {
            "guncon2-retail_Up", "guncon2-retail_Down", "guncon2-retail_Left", "guncon2-retail_Right",
            "guncon2-retail_Trigger", "guncon2-retail_ShootOffscreen", "guncon2-retail_Recalibrate",
            "guncon2-retail_A", "guncon2-retail_B", "guncon2-retail_C",
            "guncon2-retail_Start", "guncon2-retail_Select"})
        self.assertFalse([i for i in ids if "Relative" in i])   # relative NEVER offered (freeze)
        self.assertTrue(all(b["kind"] == "gun" for grp in g["groups"] for b in grp["binds"]))

    def test_unified_page_has_crosshair_selectors_only(self):
        # crosshair image/size are the ONLY selectors. The Sinden-border control was removed:
        # the software border needs ACJV LIGHTGUN mode (arcade-only), so it is dead for retail.
        g = self._with(self._ini(), lambda: gin._input_get({"player": "usb1"}))
        keys = {s["key"]: s for s in g["selectors"]}
        self.assertEqual(keys["cursor_scale"]["scope"], "player")       # per-gun
        self.assertFalse([k for k in keys if k.startswith("sinden")])   # no dead border control

    def test_crosshair_image_selector_scans_dir(self):
        ini = self._ini()
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "Green.png").write_bytes(b"")
            (dd / "Red.png").write_bytes(b"")
            with mock.patch.object(gin, "_CROSSHAIR_DIR", dd):
                g = self._with(ini, lambda: gin._input_get({"player": "usb1"}))
        img = next(s for s in g["selectors"] if s["key"] == "cursor_path")
        self.assertEqual(img["scope"], "player")
        self.assertEqual([o["label"] for o in img["options"]], ["Green", "Red"])

    def test_player_picker_two_usb_ports(self):
        g = self._with(self._ini(), lambda: gin._input_get({"player": "usb1"}))
        self.assertEqual([p["id"] for p in g["players"]], ["usb1", "usb2"])

    def test_set_mouse_button_uses_port_pointer(self):
        ini = self._ini()
        self._with(ini, lambda: gin._input_set(
            {"player": "usb2", "id": "guncon2-retail_Trigger", "kind": "gun",
             "gun_kind": "mouse", "value": "1"}))
        self.assertIn("guncon2-retail_Trigger = Pointer-1/LeftButton",
                      inifile.section_body(ini.read_text(), "USB2"))

    def test_set_key_writes_keyboard_source(self):
        ini = self._ini()
        self._with(ini, lambda: gin._input_set(
            {"player": "usb1", "id": "guncon2-retail_Start", "kind": "gun",
             "gun_kind": "key", "value": "enter"}))
        self.assertIn("guncon2-retail_Start = Keyboard/Return",
                      inifile.section_body(ini.read_text(), "USB1"))

    def test_selector_set_crosshair_is_player_scoped(self):
        ini = self._ini()
        self._with(ini, lambda: gin._selector_set(
            {"key": "cursor_scale", "value": "0.12", "player": "usb2"}))
        self.assertIn("guncon2-retail_cursor_scale = 0.12",
                      inifile.section_body(ini.read_text(), "USB2"))
        self.assertNotIn("cursor_scale", inifile.section_body(ini.read_text(), "USB1"))   # gun 1 untouched

    def test_selector_set_rejects_removed_sinden_border(self):
        # the Sinden-border selector was removed (dead control for retail); it must be rejected
        ini = self._ini()
        with self.assertRaises(rpc.RpcError):
            self._with(ini, lambda: gin._selector_set(
                {"key": "sinden_border", "value": "true", "player": "usb1"}))

    def test_rejects_relative_and_unknown_binds(self):
        ini = self._ini()
        with self.assertRaises(rpc.RpcError):
            self._with(ini, lambda: gin._input_set(
                {"player": "usb1", "id": "guncon2-retail_RelativeUp",
                 "gun_kind": "key", "value": "up"}))

    def test_selector_set_rejects_unknown_key(self):
        ini = self._ini()
        with self.assertRaises(rpc.RpcError):
            self._with(ini, lambda: gin._selector_set(
                {"key": "bogus", "value": "x", "player": "usb1"}))

    def test_start_stop_sinden_action_dynamic(self):
        orig = gin.sinden_cmds._driver_running
        try:
            gin.sinden_cmds._driver_running = lambda: False
            off = self._with(self._ini(), lambda: gin._input_get({"player": "usb1"}))
            gin.sinden_cmds._driver_running = lambda: True
            on = self._with(self._ini(), lambda: gin._input_get({"player": "usb1"}))
        finally:
            gin.sinden_cmds._driver_running = orig
        a_off, a_on = off["actions"][0], on["actions"][0]
        self.assertEqual(a_off["rpc"], "sinden.driver")
        self.assertEqual(a_off["args"], {"action": "start"})
        self.assertIn("Start", a_off["label"])
        self.assertEqual(a_on["args"], {"action": "stop"})
        self.assertIn("Stop", a_on["label"])


if __name__ == "__main__":
    unittest.main()
