"""On-the-go internal-resolution rail for RetroArch heavy cores (lib/ra_res.py).

.opt apply/revert byte-stable, _metric contract, per-content vs folder resolution, Mupen two-key,
only-ever-LOWER, revert-if-user-edited guard, docked/inherit no-op. Temp core tree + MAD_FORCE_CONTEXT.
Run: python3 -m unittest tests.test_ra_res -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import ra_res, retroarch_cfg


def _pol(sys, res="native"):
    return {"handheld": {"enabled": True},
            "systems": {sys: {"handheld": {"enabled": True, "res": res}}}}


class RaRes(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "config"
        self.cfg.mkdir()
        self.res_dir = self.d / "ra-res"
        self._p1 = mock.patch.object(ra_res, "_RES_DIR", self.res_dir)
        self._p2 = mock.patch.object(retroarch_cfg, "RA_CONFIG_BASE", self.cfg)
        self._p1.start()
        self._p2.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _opt(self, core, name):
        return self.cfg / core / (name + ".opt")

    def _mk(self, core, name, body):
        (self.cfg / core).mkdir(parents=True, exist_ok=True)
        self._opt(core, name).write_text(body)

    def _val(self, core, name, key):
        return retroarch_cfg.read_opt(self._opt(core, name), key)

    def _markers(self):
        return list(self.res_dir.glob("*.json"))

    def _apply(self, sys, name, core, policy):
        with mock.patch.object(ra_res, "load_merged", lambda: policy):
            ra_res.apply(sys, name, core)

    def test_metric(self):
        self.assertEqual(ra_res._metric("2x"), 2.0)
        self.assertEqual(ra_res._metric("1X"), 1.0)
        self.assertEqual(ra_res._metric("960x720"), 691200.0)
        self.assertIsNone(ra_res._metric("junk"))

    def test_opt_byte_stable(self):
        self._mk("Beetle PSX HW", "Crash",
                 'beetle_psx_hw_renderer = "hardware"\nbeetle_psx_hw_internal_resolution = "4x"\n')
        before = self._opt("Beetle PSX HW", "Crash").read_bytes()
        self._apply("psx", "Crash", "Beetle PSX HW", _pol("psx"))
        self.assertEqual(self._val("Beetle PSX HW", "Crash", "beetle_psx_hw_internal_resolution"), "1x")
        ra_res.sweep_all()
        self.assertEqual(self._opt("Beetle PSX HW", "Crash").read_bytes(), before)
        self.assertFalse(self._markers())

    def test_per_content_vs_folder(self):
        self._mk("Beetle PSX HW", "Beetle PSX HW", 'beetle_psx_hw_internal_resolution = "2x"\n')   # folder default
        self._mk("Beetle PSX HW", "Tekken", 'beetle_psx_hw_internal_resolution = "4x"\n')           # per-content
        self._apply("psx", "Tekken", "Beetle PSX HW", _pol("psx"))
        self.assertEqual(self._val("Beetle PSX HW", "Tekken", "beetle_psx_hw_internal_resolution"), "1x")
        self.assertEqual(self._val("Beetle PSX HW", "Beetle PSX HW", "beetle_psx_hw_internal_resolution"), "2x")  # folder untouched
        ra_res.sweep_all()

    def test_mupen_two_keys(self):
        self._mk("Mupen64Plus-Next", "Mupen64Plus-Next",
                 'mupen64plus-43screensize = "1280x960"\nmupen64plus-169screensize = "1920x1080"\n')
        self._apply("n64", "Mario", "Mupen64Plus-Next", _pol("n64"))
        self.assertEqual(self._val("Mupen64Plus-Next", "Mupen64Plus-Next", "mupen64plus-43screensize"), "640x480")
        self.assertEqual(self._val("Mupen64Plus-Next", "Mupen64Plus-Next", "mupen64plus-169screensize"), "960x540")
        ra_res.sweep_all()
        self.assertEqual(self._val("Mupen64Plus-Next", "Mupen64Plus-Next", "mupen64plus-43screensize"), "1280x960")

    def test_only_lower(self):
        self._mk("Beetle PSX HW", "Beetle PSX HW", 'beetle_psx_hw_internal_resolution = "1x"\n')
        self._apply("psx", "Any", "Beetle PSX HW", _pol("psx"))
        self.assertFalse(self._markers())

    def test_revert_if_user_edited(self):
        self._mk("Beetle PSX HW", "Crash", 'beetle_psx_hw_internal_resolution = "4x"\n')
        self._apply("psx", "Crash", "Beetle PSX HW", _pol("psx"))
        retroarch_cfg.write_opt(self._opt("Beetle PSX HW", "Crash"),
                                "beetle_psx_hw_internal_resolution", "8x")   # user edit
        ra_res.sweep_all()
        self.assertEqual(self._val("Beetle PSX HW", "Crash", "beetle_psx_hw_internal_resolution"), "8x")

    def test_docked_and_inherit(self):
        self._mk("Beetle PSX HW", "Crash", 'beetle_psx_hw_internal_resolution = "4x"\n')
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._apply("psx", "Crash", "Beetle PSX HW", _pol("psx"))
        self.assertEqual(self._val("Beetle PSX HW", "Crash", "beetle_psx_hw_internal_resolution"), "4x")
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self._apply("psx", "Crash", "Beetle PSX HW", _pol("psx", "inherit"))
        self.assertEqual(self._val("Beetle PSX HW", "Crash", "beetle_psx_hw_internal_resolution"), "4x")
        self.assertFalse(self._markers())


if __name__ == "__main__":
    unittest.main()
