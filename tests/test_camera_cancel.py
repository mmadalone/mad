"""Tests for camera.cancel (Sinden lightgun Camera-tuning buffered Y=Cancel).

camera.cancel re-seeds the in-memory slider buffer from the SAVED config and,
only while a gun is being previewed, pushes those saved values back to the live
v4l2 controls. It must never write config and never touch v4l2 when no preview
owns the camera.

Hermetic: sinden_cfg.get / set_ctrl are patched so nothing reads or writes a
real device or config file.

Run:  python3 -m unittest tests.test_camera_cancel -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib import sinden_cfg
from lib.madsrv import sinden_cmds as sc
from lib.madsrv import rpc


def _call(name, params=None):
    return rpc._METHODS[name][0](params or {})


# The "saved config" the seed reads back (only the keys _cam_seed_vals touches).
SAVED = {
    "CameraBrightness": "100", "CameraContrast": "50",
    "CameraExposureAuto": "1", "CameraExposure": "80",
    "CameraBrightnessP2": "100", "CameraContrastP2": "50",
    "CameraExposureAutoP2": "1", "CameraExposureP2": "80",
}


class Registration(unittest.TestCase):
    def test_registered_and_slow(self):
        ent = rpc._METHODS.get("camera.cancel")
        self.assertIsNotNone(ent)
        self.assertTrue(ent[1])  # slow=True (v4l2-ctl subprocesses)


class CameraCancel(unittest.TestCase):
    def setUp(self):
        p_get = mock.patch.object(sinden_cfg, "get", side_effect=lambda k: SAVED.get(k))
        self.set_ctrl = mock.Mock()
        p_set = mock.patch.object(sinden_cfg, "set_ctrl", self.set_ctrl)
        for p in (p_get, p_set):
            p.start()
            self.addCleanup(p.stop)
        # Isolate module state between tests.
        self._saved_player = sc._cam["player"]
        self.addCleanup(lambda: sc._cam.__setitem__("player", self._saved_player))

    def test_cancel_reverts_the_buffer_from_saved_config(self):
        sc._cam["player"] = None
        # Dirty the buffer with an out-of-band edit.
        sc._cam["vals"] = {1: {"Brightness": 5, "Contrast": 5, "auto": True, "Exposure": 999},
                           2: {"Brightness": 5, "Contrast": 5, "auto": True, "Exposure": 999}}
        r = _call("camera.cancel", {})
        self.assertEqual(sc._cam["vals"][1]["Brightness"], 100)  # <- reseeded from SAVED
        self.assertEqual(sc._cam["vals"][1]["Contrast"], 50)
        self.assertEqual(sc._cam["vals"][1]["Exposure"], 80)
        self.assertFalse(sc._cam["vals"][1]["auto"])             # "1" -> not auto
        self.assertEqual(r["vals"]["1"]["Brightness"], 100)      # camera.get shape

    def test_cancel_without_preview_never_touches_v4l2(self):
        sc._cam["player"] = None
        sc._cam["vals"] = {1: {"Brightness": 5, "Contrast": 5, "auto": False, "Exposure": 999},
                           2: {"Brightness": 5, "Contrast": 5, "auto": False, "Exposure": 999}}
        _call("camera.cancel", {})
        self.set_ctrl.assert_not_called()                        # no device we own -> no writes

    def test_cancel_while_previewing_reapplies_live(self):
        sc._cam["player"] = 1
        sc._cam["vals"] = {1: {"Brightness": 5, "Contrast": 5, "auto": False, "Exposure": 999},
                           2: {"Brightness": 5, "Contrast": 5, "auto": False, "Exposure": 999}}
        _call("camera.cancel", {})
        self.assertTrue(self.set_ctrl.called)                    # live controls reverted
        devs = {c.args[0] for c in self.set_ctrl.call_args_list}
        self.assertEqual(devs, {sinden_cfg.CAM[1]})              # ONLY the previewed gun's device


if __name__ == "__main__":
    unittest.main()
