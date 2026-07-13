"""Handheld suppresses the two X-Arcade presence warnings (controller-router._xarcade_warn).

When the Deck is handheld (undocked, on-the-go) the X-Arcade Tankstick is definitionally
absent, so both presence prompts -- "No X-Arcade detected" (arcade) and "Plug in a gamepad?"
(console, only-X-Arcade) -- are noise and must be skipped. Docked, they fire exactly as before.
The gate is fail-safe: any detection error leaves the warning ON.

Loads the hyphenated top-level script via spec_from_file_location (main() stays unrun).
Run: python3 -m unittest tests.test_xarcade_warn_handheld -v
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    modu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modu)
    return modu


cr = _load("controller_router", "controller-router.py")


class XarcadeWarnHandheld(unittest.TestCase):
    """_xarcade_warn's handheld gate: handheld => return 0 + NO dialog; docked => dialog fires."""

    def setUp(self):
        self.log = mock.MagicMock()
        # devs=[] + a set xport => xarcade_present([], xport) is False => the arcade "no X-Arcade"
        # branch is armed; only_xarcade_present is patched per-test for the console branch.
        self.xport = "1-1"

    def _warn(self, sys_entry, handheld):
        with mock.patch.object(cr, "_show_warning_blocking", return_value=1) as dlg:
            rc = cr._xarcade_warn(sys_entry, [], self.log, self.xport, {}, handheld=handheld)
        return rc, dlg

    # -- arcade: "No X-Arcade detected" --------------------------------------
    def test_handheld_suppresses_no_xarcade_warn(self):
        rc, dlg = self._warn({"category": "arcade"}, handheld=True)
        self.assertEqual(rc, 0)                 # Proceed, no cancel
        dlg.assert_not_called()                 # no blocking dialog shown

    def test_docked_still_fires_no_xarcade_warn(self):
        rc, dlg = self._warn({"category": "arcade"}, handheld=False)
        dlg.assert_called_once()                # docked: the dialog still appears
        self.assertEqual(rc, 1)                 # and its exit code passes through

    # -- console: "Plug in a gamepad?" (only the X-Arcade present) ------------
    def test_handheld_suppresses_only_xarcade_warn(self):
        with mock.patch.object(cr, "only_xarcade_present", return_value=True):
            rc, dlg = self._warn({"category": "console"}, handheld=True)
        self.assertEqual(rc, 0)
        dlg.assert_not_called()

    def test_docked_still_fires_only_xarcade_warn(self):
        with mock.patch.object(cr, "only_xarcade_present", return_value=True):
            rc, dlg = self._warn({"category": "console"}, handheld=False)
        dlg.assert_called_once()
        self.assertEqual(rc, 1)


class WiiRemoteWarnHandheld(unittest.TestCase):
    """_wii_remote_warn: the "No Wii Remote detected" dialog fires docked when dolphin_route flagged
    a missing DolphinBar, but is skipped handheld (a DolphinBar is definitionally absent undocked)."""

    def setUp(self):
        self.log = mock.MagicMock()

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def _warn(self, summary, policy):
        with mock.patch.object(cr, "_show_warning_blocking", return_value=0) as dlg:
            cr._wii_remote_warn(summary, policy, self.log)
        return dlg

    def test_handheld_suppresses_wii_warn(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self._warn({"warn": True}, {"handheld": {"enabled": True}}).assert_not_called()

    def test_docked_shows_wii_warn(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._warn({"warn": True}, {"handheld": {"enabled": True}}).assert_called_once()

    def test_no_warn_flag_no_dialog(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._warn({"warn": False}, {"handheld": {"enabled": True}}).assert_not_called()

    def test_feature_disabled_shows_even_if_physically_handheld(self):
        # on-the-go OFF => _handheld_active False => the warn fires (docked behaviour), fail-safe.
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self._warn({"warn": True}, {"handheld": {"enabled": False}}).assert_called_once()


class HandheldActiveGate(unittest.TestCase):
    """_handheld_active: on-the-go enabled + physically handheld => True; else False (fail-safe)."""

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def test_enabled_and_handheld_is_true(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertTrue(cr._handheld_active({"handheld": {"enabled": True}}))

    def test_enabled_but_docked_is_false(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.assertFalse(cr._handheld_active({"handheld": {"enabled": True}}))

    def test_feature_disabled_is_false(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"      # handheld, but master switch off
        self.assertFalse(cr._handheld_active({"handheld": {"enabled": False}}))

    def test_missing_or_malformed_policy_is_false(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self.assertFalse(cr._handheld_active({}))
        self.assertFalse(cr._handheld_active({"handheld": "not-a-dict"}))
        self.assertFalse(cr._handheld_active(None))


if __name__ == "__main__":
    unittest.main()
