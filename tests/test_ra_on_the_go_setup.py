"""RetroArch on-the-go _setup helper (controller-router._ra_on_the_go).

A genuine RA launch flips the joypad driver + applies the handheld profile when the feature is
enabled, and -- crucially -- still HEALS a crash-orphaned handheld profile when the feature is
DISABLED (a hard crash bypasses the game-end restore, so a docked RA game must not start on the
leftover sdl2 driver + Deck P1 binds). A standalone launch (no RA core) is a no-op.
Run: python3 -m unittest tests.test_ra_on_the_go_setup -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cr = _load("controller_router", "controller-router.py")
_CTX = SimpleNamespace(system="snes", rom_basename="Game")


class RaOnTheGo(unittest.TestCase):
    def setUp(self):
        import lib.ra_handheld_input as rhi, lib.retroarch_cfg as rc
        self.rhi, self.rc = rhi, rc
        self._orig = (rc.launched_core, rc.set_global_option, rhi.apply, rhi.restore,
                      cr._ra_handheld_driver)
        rc.launched_core = mock.MagicMock(return_value="snes9x")   # a real RA launch by default
        rc.set_global_option = mock.MagicMock()
        rhi.apply = mock.MagicMock()
        rhi.restore = mock.MagicMock(return_value=False)
        cr._ra_handheld_driver = mock.MagicMock()

    def tearDown(self):
        (self.rc.launched_core, self.rc.set_global_option, self.rhi.apply, self.rhi.restore,
         cr._ra_handheld_driver) = self._orig

    def _run(self, enabled):
        cr._ra_on_the_go(_CTX, {"handheld": {"enabled": enabled}}, mock.MagicMock())

    def test_standalone_launch_is_noop(self):
        self.rc.launched_core.return_value = None      # not RetroArch
        self._run(True)
        self.rhi.apply.assert_not_called()
        self.rhi.restore.assert_not_called()
        self.rc.set_global_option.assert_not_called()

    def test_enabled_flips_and_applies(self):
        self._run(True)
        cr._ra_handheld_driver.assert_called_once()
        self.rhi.apply.assert_called_once()
        self.rhi.restore.assert_not_called()           # apply() does the orphan sweep on this path

    def test_disabled_heals_crash_orphan(self):
        self.rhi.restore.return_value = True            # a crash orphan existed
        self._run(False)
        self.rhi.restore.assert_called_once()
        self.rc.set_global_option.assert_called_once_with("input_joypad_driver", "udev")
        cr._ra_handheld_driver.assert_not_called()
        self.rhi.apply.assert_not_called()

    def test_disabled_no_orphan_leaves_driver(self):
        self.rhi.restore.return_value = False           # nothing orphaned
        self._run(False)
        self.rhi.restore.assert_called_once()
        self.rc.set_global_option.assert_not_called()   # driver untouched (legacy behaviour)


if __name__ == "__main__":
    unittest.main()
