"""RetroArch on-the-go _setup helper (controller-router._ra_on_the_go).

A genuine RA launch flips the joypad driver + applies the PER-GAME handheld remap when the feature
is enabled. When the feature is DISABLED it still HEALS a crash orphan: a hard crash bypasses the
game-end restore, so if the global joypad driver is still on sdl2 it is put back to udev (a docked
RA game must not start blind to the raw X-Arcade). The handheld INPUT binds are owned by the RA
input PROFILES now (written as a per-game override, reverted by clear_override), so this helper only
heals the driver -- the old ra_handheld_input rail is gone. A standalone launch (no RA core) is a
no-op.
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
        import lib.retroarch_cfg as rc
        import lib.ra_handheld_pergame as rhp
        self.rc, self.rhp = rc, rhp
        self._orig = (rc.launched_core, rc.set_global_option, rc.get_global_options,
                      rhp.apply, rhp.restore, cr._ra_handheld_driver)
        rc.launched_core = mock.MagicMock(return_value="snes9x")   # a real RA launch by default
        rc.set_global_option = mock.MagicMock()
        rc.get_global_options = mock.MagicMock(return_value={"input_joypad_driver": "udev"})
        rhp.apply = mock.MagicMock()
        rhp.restore = mock.MagicMock()
        cr._ra_handheld_driver = mock.MagicMock(return_value="sdl2")

    def tearDown(self):
        (self.rc.launched_core, self.rc.set_global_option, self.rc.get_global_options,
         self.rhp.apply, self.rhp.restore, cr._ra_handheld_driver) = self._orig

    def _run(self, enabled):
        return cr._ra_on_the_go(_CTX, {"handheld": {"enabled": enabled}}, mock.MagicMock())

    def test_standalone_launch_is_noop(self):
        self.rc.launched_core.return_value = None      # not RetroArch
        self.assertIsNone(self._run(True))
        self.rhp.apply.assert_not_called()
        self.rhp.restore.assert_not_called()
        self.rc.set_global_option.assert_not_called()
        cr._ra_handheld_driver.assert_not_called()

    def test_enabled_flips_and_applies_pergame(self):
        driver = self._run(True)
        cr._ra_handheld_driver.assert_called_once()                 # the joypad-driver flip
        self.rhp.apply.assert_called_once_with(_CTX.system, _CTX.rom_basename)
        self.assertEqual(driver, "sdl2")                            # returns the driver the flip chose
        self.rc.set_global_option.assert_not_called()               # the flip owns the write, not here

    def test_disabled_heals_sdl2_crash_orphan(self):
        self.rc.get_global_options.return_value = {"input_joypad_driver": "sdl2"}   # a crash orphan
        driver = self._run(False)
        self.rhp.restore.assert_called_once()                       # per-game orphan healed too
        self.rc.set_global_option.assert_called_once_with("input_joypad_driver", "udev")
        self.assertEqual(driver, "udev")
        cr._ra_handheld_driver.assert_not_called()

    def test_disabled_no_orphan_leaves_driver(self):
        self.rc.get_global_options.return_value = {"input_joypad_driver": "udev"}   # nothing orphaned
        driver = self._run(False)
        self.rhp.restore.assert_called_once()
        self.rc.set_global_option.assert_not_called()               # already udev -> untouched
        self.assertEqual(driver, "udev")


if __name__ == "__main__":
    unittest.main()
