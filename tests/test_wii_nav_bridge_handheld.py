"""Handheld gating of the MAD Wii Nav bridge (wii-nav-bridge.py).

The bridge's virtual "MAD Wii Nav" pad (4d41:0001) is useless handheld (no DolphinBar) and can
grab a controller slot, so the bridge tears the pad down when the Deck is physically handheld
and rebuilds it when docked -- tracked live in run()'s loop. These tests drive the state machine
with UInput + _handheld() mocked (no /dev/uinput, no DolphinBar, no log writes).
Run: python3 -m unittest tests.test_wii_nav_bridge_handheld -v
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load():
    os.environ.setdefault("MAD_WII_DEBUG", "0")
    spec = importlib.util.spec_from_file_location("wii_nav_bridge", ROOT / "wii-nav-bridge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # top level is import-safe (main() is __main__-guarded)
    return m


wnb = _load()


class _Base(unittest.TestCase):
    def setUp(self):
        self.created = []               # every faked UInput instance, in creation order
        self.ui_should_fail = False     # flip to simulate /dev/uinput unavailable
        self._ui = wnb.UInput
        wnb.UInput = lambda *a, **k: self._mk()
        self._hh = wnb._handheld
        self._dbg = wnb.dbg
        wnb.dbg = lambda *a, **k: None  # no stderr / log-file writes in tests

    def tearDown(self):
        wnb.UInput = self._ui
        wnb._handheld = self._hh
        wnb.dbg = self._dbg

    def _mk(self):
        if self.ui_should_fail:
            raise OSError("no /dev/uinput")
        u = mock.MagicMock(name="UInput")
        self.created.append(u)
        return u

    def _bridge(self, handheld):
        wnb._handheld = lambda: handheld
        b = wnb.Bridge()
        b.rescan = lambda: None         # never touch the real DolphinBar
        return b


class StartupState(_Base):
    def test_start_docked_creates_pad(self):
        b = self._bridge(handheld=False)
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertEqual(len(self.created), 1)      # pad created

    def test_start_handheld_is_padless(self):
        b = self._bridge(handheld=True)
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        self.assertEqual(self.created, [])          # no pad ever created


class DockTransitions(_Base):
    def test_docked_to_handheld_closes_pad(self):
        b = self._bridge(handheld=False)
        pad = b.ui
        wnb._handheld = lambda: True
        b._apply_dock_state()
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        pad.close.assert_called_once()              # the pad was destroyed

    def test_handheld_to_docked_reopens_pad(self):
        b = self._bridge(handheld=True)
        rescanned = []
        b.rescan = lambda: rescanned.append(True)
        wnb._handheld = lambda: False
        b._apply_dock_state()
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertTrue(rescanned)                  # slots re-scanned on redock

    def test_no_op_when_state_unchanged(self):
        b = self._bridge(handheld=False)
        wnb._handheld = lambda: False               # still docked
        b._apply_dock_state()
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertEqual(len(self.created), 1)      # no extra pad churn


class UInputFailure(_Base):
    """A failed /dev/uinput open must NOT crash the bridge; it stays disabled to retry."""

    def test_start_docked_open_failure_stays_disabled(self):
        self.ui_should_fail = True
        b = self._bridge(handheld=False)            # __init__ tries _open_ui, which raises inside
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)                 # not crashed, stays disabled to retry

    def test_redock_open_failure_stays_disabled(self):
        b = self._bridge(handheld=True)             # padless
        self.ui_should_fail = True
        wnb._handheld = lambda: False               # redock, but the pad won't open
        b._apply_dock_state()
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)                 # did NOT flip to enabled with a dead pad


class PadlessSafety(_Base):
    def test_apply_is_noop_when_padless(self):
        b = self._bridge(handheld=True)             # ui is None
        b.apply(wnb.blank_state())                  # must not raise
        b.apply({"buttons": {"a"}, "hat": (0, 0), "lt": False, "rt": False})

    def test_resume_stays_padless_when_disabled(self):
        b = self._bridge(handheld=True)
        rescanned = []
        b.rescan = lambda: rescanned.append(True)
        b.paused = True
        b.handle_command("resume")
        self.assertFalse(rescanned)                 # handheld: resume does NOT reopen slots


class HandheldGate(unittest.TestCase):
    """The real _handheld() gate: on-the-go enabled + physically handheld only; fail-safe."""

    def setUp(self):
        import lib.policy as policy
        self._lm = policy.load_merged

    def tearDown(self):
        import lib.policy as policy
        policy.load_merged = self._lm
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def _set_policy(self, enabled):
        import lib.policy as policy
        policy.load_merged = lambda: {"handheld": {"enabled": enabled}}

    def test_enabled_and_handheld_true(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self._set_policy(True)
        self.assertTrue(wnb._handheld())

    def test_docked_false(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._set_policy(True)
        self.assertFalse(wnb._handheld())

    def test_feature_off_false(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        self._set_policy(False)
        self.assertFalse(wnb._handheld())


if __name__ == "__main__":
    unittest.main()
