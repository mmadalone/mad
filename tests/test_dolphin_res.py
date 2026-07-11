"""On-the-go internal-resolution rail for GameCube/Wii (lib/dolphin_res.py).

Global GFX.ini path byte-stable, per-game GameSettings override wins (global untouched), Auto(0)/
Native(1) untouched, inherit + non-gc/wii no-op, revert-if-changed guard. Temp configs +
MAD_FORCE_CONTEXT. Run: python3 -m unittest tests.test_dolphin_res -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import dolphin_res
from lib.madsrv import cfgutil

_GFX_BODY = "[Enhancements]\nEFBScaledCopy = True\n\n[Settings]\nInternalResolution = 3\nAspectRatio = 0\n"


def _pol(sys="gc", res="native"):
    return {"handheld": {"enabled": True},
            "systems": {sys: {"handheld": {"enabled": True, "res": res}}}}


class DolphinRes(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.gfx = self.d / "GFX.ini"
        self.gfx.write_text(_GFX_BODY)
        self.res_dir = self.d / "dolphin-res"
        self._p1 = mock.patch.object(dolphin_res, "_GFX", self.gfx)
        self._p2 = mock.patch.object(dolphin_res, "_RES_DIR", self.res_dir)
        self._p1.start()
        self._p2.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _ir(self, f, sect):
        return cfgutil.ini_read(cfgutil.read_text(f), sect, "InternalResolution")

    def _markers(self):
        return list(self.res_dir.glob("*.json"))

    def _apply(self, sys, rom, policy, gid=None, user=None):
        with mock.patch.object(dolphin_res, "load_merged", lambda: policy), \
             mock.patch("lib.dolphin_gameids.gameid", lambda r: gid), \
             mock.patch("lib.dolphin_gameids.user_ini", lambda g: user or (self.d / f"{g}.ini")):
            dolphin_res.apply(sys, rom)

    def test_global_byte_stable(self):
        before = self.gfx.read_bytes()
        self._apply("gc", "x.rvz", _pol("gc"))
        self.assertEqual(self._ir(self.gfx, "Settings"), "1")
        dolphin_res.sweep_all()
        self.assertEqual(self.gfx.read_bytes(), before)
        self.assertFalse(self._markers())

    def test_pergame_override_wins(self):
        pg = self.d / "GSWP64.ini"
        pg.write_text("[Core]\nCPUThread = False\n[Video_Settings]\nInternalResolution = 3\n")
        gfx_before = self.gfx.read_bytes()
        self._apply("gc", "rogue.rvz", _pol("gc"), gid="GSWP64", user=pg)
        self.assertEqual(self._ir(pg, "Video_Settings"), "1")     # per-game downshifted
        self.assertEqual(self.gfx.read_bytes(), gfx_before)       # GLOBAL untouched
        dolphin_res.sweep_all()
        self.assertEqual(self._ir(pg, "Video_Settings"), "3")

    def test_auto_native_untouched(self):
        for v in ("0", "1"):
            self.gfx.write_text(f"[Settings]\nInternalResolution = {v}\n")
            self._apply("wii", "x", _pol("wii"))
            self.assertEqual(self._ir(self.gfx, "Settings"), v)
            self.assertFalse(self._markers())

    def test_inherit_and_nonsystem_noop(self):
        self._apply("gc", "x", _pol("gc", "inherit"))
        self.assertEqual(self._ir(self.gfx, "Settings"), "3")
        self._apply("nes", "x", _pol("nes"))          # not gc/wii
        self.assertEqual(self._ir(self.gfx, "Settings"), "3")
        self.assertFalse(self._markers())

    def test_revert_if_changed(self):
        self._apply("gc", "x", _pol("gc"))            # -> 1
        cfgutil.atomic_write(self.gfx, cfgutil.ini_replace(
            cfgutil.read_text(self.gfx), "Settings", "InternalResolution", "5"))
        dolphin_res.sweep_all()
        self.assertEqual(self._ir(self.gfx, "Settings"), "5")     # user edit preserved

    def test_marker_kept_when_revert_write_fails(self):
        self._apply("gc", "x", _pol("gc"))            # -> 1, marker written
        self.assertTrue(self._markers())
        with mock.patch.object(cfgutil, "atomic_write", side_effect=OSError("disk full")):
            dolphin_res.sweep_all()                   # revert WRITE fails
        self.assertTrue(self._markers())              # marker KEPT for retry
        self.assertEqual(self._ir(self.gfx, "Settings"), "1")     # not yet reverted
        dolphin_res.sweep_all()                       # a later (working) sweep heals it
        self.assertEqual(self._ir(self.gfx, "Settings"), "3")
        self.assertFalse(self._markers())


if __name__ == "__main__":
    unittest.main()
