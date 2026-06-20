"""RetroArch-page additions: the fast-forward / slow-motion hotkey rows on the Input
page, and the menu OK/Cancel swap toggle on the settings page. The global-cfg
readers/writers are stubbed so the test never touches the real retroarch.cfg —
these guard against a key-name typo (which would silently fail to bind/apply).

Run:  python3 -m unittest tests.test_ra_input_additions -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import retroarch_cmds as rc


class RaInputAdditions(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "run": rc.proc_guard.retroarch_running,
            "setg": rc.retroarch_cfg.set_global_option,
            "getg": rc.retroarch_cfg.get_global_option,
            "getgs": rc.retroarch_cfg.get_global_options,
            "pads": rc._connected_pads,
        }
        self.cfg = {}
        rc.proc_guard.retroarch_running = lambda: False
        rc.retroarch_cfg.set_global_option = lambda k, v: self.cfg.__setitem__(k, v)
        rc.retroarch_cfg.get_global_option = lambda k: self.cfg.get(k)
        rc.retroarch_cfg.get_global_options = lambda keys: {k: self.cfg.get(k) for k in keys}
        rc._connected_pads = lambda: []

    def tearDown(self):
        rc.proc_guard.retroarch_running = self._saved["run"]
        rc.retroarch_cfg.set_global_option = self._saved["setg"]
        rc.retroarch_cfg.get_global_option = self._saved["getg"]
        rc.retroarch_cfg.get_global_options = self._saved["getgs"]
        rc._connected_pads = self._saved["pads"]

    def _hotkey_binds(self):
        res = rc._input_get({"player": 1})
        for g in res["groups"]:
            if g["title"] == "System hotkeys":
                return {b["key"]: b for b in g["binds"]}
        return {}

    def test_speed_control_hotkeys_present(self):
        binds = self._hotkey_binds()
        for key in ("input_toggle_fast_forward", "input_hold_fast_forward",
                    "input_toggle_slowmotion", "input_hold_slowmotion"):
            self.assertIn(key, binds, f"{key} missing from System hotkeys")
            self.assertEqual(binds[key]["kind"], "hotkey")
            self.assertTrue(binds[key]["capturable"])

    def test_menu_swap_setting_present_and_round_trips(self):
        grp = next((g for g in rc._retroarch_get({})["groups"] if g["title"] == "Input"), None)
        self.assertIsNotNone(grp, "Input settings group missing")
        item = next((s for s in grp["settings"]
                     if s["key"] == "menu_swap_ok_cancel_buttons"), None)
        self.assertIsNotNone(item, "menu_swap_ok_cancel_buttons missing from Input group")
        self.assertEqual(item["type"], "bool")
        # set True -> writes "true" and the re-read reflects it
        out = rc._retroarch_set({"key": "menu_swap_ok_cancel_buttons", "value": True})
        self.assertEqual(self.cfg["menu_swap_ok_cancel_buttons"], "true")
        self.assertTrue(out["value"])
        # set False -> "false"
        rc._retroarch_set({"key": "menu_swap_ok_cancel_buttons", "value": False})
        self.assertEqual(self.cfg["menu_swap_ok_cancel_buttons"], "false")


if __name__ == "__main__":
    unittest.main()
