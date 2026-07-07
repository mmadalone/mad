"""Retail gun USB pages x6r_usb1 / x6r_usb2: single-port views of the shipped guncon2_retail
page, pinned to one USB port (no picker) and delegating to its helpers (retail -datapath ini).

Run:  python3 -m unittest tests.test_pcsx2x6_retail_input -v
"""
import tempfile
import unittest
from pathlib import Path

from lib import inifile
from lib.madsrv import guncon2_retail_input_cmds as gr
from lib.madsrv import pcsx2x6_retail_input_cmds as ri
from lib.madsrv import rpc


class Registration(unittest.TestCase):
    def test_both_ports_register_the_full_verb_set(self):
        for ns in ("x6r_usb1", "x6r_usb2"):
            for v in ("input_get", "input_set", "input_clear", "selector_set",
                      "input_save", "input_cancel"):     # buffered editor adds save/cancel
                self.assertIn(f"{ns}.{v}", rpc._METHODS, f"{ns}.{v}")


class SinglePort(unittest.TestCase):
    def _with(self, ini, fn):
        oi, orun = gr._INI, gr._running
        try:
            gr._INI, gr._running = ini, (lambda: False)
            gr._buf.reset()                # fresh buffer per case (module-level singleton)
            return fn()
        finally:
            gr._INI, gr._running = oi, orun

    def _ini(self):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text("[USB1]\nType = guncon2-retail\n\n[USB2]\nType = guncon2-retail\n\n"
                       "[JVS]\nTestMode = false\n", encoding="utf-8")
        return ini

    def test_drops_the_port_picker(self):
        # scoped to one gun -> no players/player -> the C++ renders no port picker.
        pay = self._with(self._ini(), lambda: ri._single_port("usb1"))
        self.assertNotIn("players", pay)
        self.assertNotIn("player", pay)
        # but keeps everything the unified page carries for that gun.
        self.assertIn("groups", pay)       # the gun binds
        self.assertIn("selectors", pay)    # crosshair image/size
        self.assertIn("actions", pay)      # the Sinden Start/Stop toggle
        self.assertTrue(pay["buffered"])   # buffered editor advertised

    def test_set_targets_the_pinned_port(self):
        # x6r_usb2.input_set must write [USB2] even though the caller sends no "player".
        ini = self._ini()
        set_usb2 = rpc._METHODS["x6r_usb2.input_set"][0]   # (fn, slow, cache) -> fn
        save_usb2 = rpc._METHODS["x6r_usb2.input_save"][0]
        before = ini.read_text()
        self._with(ini, lambda: (
            set_usb2({"id": "guncon2-retail_Trigger", "kind": "gun",
                      "gun_kind": "mouse", "value": "1"}),
            self.assertEqual(ini.read_text(), before),     # staging leaves the ini unchanged
            save_usb2({})))                                # X=Save commits it
        self.assertIn("guncon2-retail_Trigger = Pointer-1/LeftButton",
                      inifile.section_body(ini.read_text(), "USB2"))
        self.assertNotIn("guncon2-retail_Trigger", inifile.section_body(ini.read_text(), "USB1"))

    def test_selector_set_scoped_to_the_pinned_gun(self):
        ini = self._ini()
        sel_usb1 = rpc._METHODS["x6r_usb1.selector_set"][0]
        save_usb1 = rpc._METHODS["x6r_usb1.input_save"][0]
        self._with(ini, lambda: (
            sel_usb1({"key": "cursor_scale", "value": "0.12"}),
            save_usb1({})))
        self.assertIn("guncon2-retail_cursor_scale = 0.12",
                      inifile.section_body(ini.read_text(), "USB1"))
        self.assertNotIn("cursor_scale", inifile.section_body(ini.read_text(), "USB2"))

    def test_cancel_discards_the_pinned_port_edit(self):
        ini = self._ini()
        set_usb1 = rpc._METHODS["x6r_usb1.input_set"][0]
        cancel_usb1 = rpc._METHODS["x6r_usb1.input_cancel"][0]
        before = ini.read_text()
        self._with(ini, lambda: (
            set_usb1({"id": "guncon2-retail_Start", "kind": "gun",
                      "gun_kind": "key", "value": "enter"}),
            cancel_usb1({})))
        self.assertEqual(ini.read_text(), before)          # Y=Cancel drops the staged bind

    def test_clear_unbinds_on_the_pinned_port(self):
        ini = self._ini()
        set_usb1 = rpc._METHODS["x6r_usb1.input_set"][0]
        clr_usb1 = rpc._METHODS["x6r_usb1.input_clear"][0]
        save_usb1 = rpc._METHODS["x6r_usb1.input_save"][0]
        self._with(ini, lambda: (
            set_usb1({"id": "guncon2-retail_Start", "kind": "gun", "gun_kind": "key", "value": "enter"}),
            save_usb1({})))
        self.assertIn("guncon2-retail_Start", inifile.section_body(ini.read_text(), "USB1"))
        self._with(ini, lambda: (clr_usb1({"id": "guncon2-retail_Start"}), save_usb1({})))
        self.assertNotIn("guncon2-retail_Start", inifile.section_body(ini.read_text(), "USB1"))

    def test_write_refused_while_emulator_running(self):
        # pcsx2x6 rewrites its ini on exit, so a live-emulator stage must refuse (EBUSY) and change
        # nothing -- else the edit is silently discarded when pcsx2x6 saves on quit.
        ini = self._ini()
        set_usb1 = rpc._METHODS["x6r_usb1.input_set"][0]
        oi, orun = gr._INI, gr._running
        try:
            gr._INI, gr._running = ini, (lambda: True)     # emulator live
            gr._buf.reset()
            with self.assertRaises(rpc.RpcError):
                set_usb1({"id": "guncon2-retail_Start", "kind": "gun", "gun_kind": "key", "value": "enter"})
        finally:
            gr._INI, gr._running = oi, orun
        self.assertNotIn("guncon2-retail_Start", inifile.section_body(ini.read_text(), "USB1"))

    def test_write_bumps_config_rev(self):
        # a successful SAVE (not stage) MUST bump staterev "config" or MAD keeps serving the stale page.
        ini = self._ini()
        set_usb1 = rpc._METHODS["x6r_usb1.input_set"][0]
        save_usb1 = rpc._METHODS["x6r_usb1.input_save"][0]
        bumps, ob = [], gr.staterev.bump
        try:
            gr.staterev.bump = lambda n: bumps.append(n)
            self._with(ini, lambda: (
                set_usb1({"id": "guncon2-retail_Start", "kind": "gun", "gun_kind": "key", "value": "enter"}),
                save_usb1({})))
        finally:
            gr.staterev.bump = ob
        self.assertIn("config", bumps)


if __name__ == "__main__":
    unittest.main()
