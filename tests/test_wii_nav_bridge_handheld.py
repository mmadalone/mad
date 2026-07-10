"""Run-gating of the MAD Wii Nav bridge (wii-nav-bridge.py).

The bridge's virtual "MAD Wii Nav" pad (4d41:0001) should exist ONLY when the Deck is docked
(not handheld) AND a DolphinBar is connected -- otherwise there are no Wii Remotes to navigate
with and the pad just grabs a controller slot. The bridge tracks this predicate live in run()'s
loop, tearing the pad down / rebuilding it as the dock state or DolphinBar changes. These tests
drive the state machine with UInput + _handheld() + dolphinbar_present() mocked (no /dev/uinput,
no real DolphinBar, no log writes). Run: python3 -m unittest tests.test_wii_nav_bridge_handheld -v
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
        self._dbp = wnb.dv.dolphinbar_present
        self._dbg = wnb.dbg
        wnb.dbg = lambda *a, **k: None  # no stderr / log-file writes in tests

    def tearDown(self):
        wnb.UInput = self._ui
        wnb._handheld = self._hh
        wnb.dv.dolphinbar_present = self._dbp
        wnb.dbg = self._dbg

    def _mk(self):
        if self.ui_should_fail:
            raise OSError("no /dev/uinput")
        u = mock.MagicMock(name="UInput")
        self.created.append(u)
        return u

    def _set(self, *, handheld=False, dolphinbar=True):
        wnb._handheld = lambda: handheld
        wnb.dv.dolphinbar_present = lambda: dolphinbar

    def _bridge(self, *, handheld=False, dolphinbar=True):
        self._set(handheld=handheld, dolphinbar=dolphinbar)
        b = wnb.Bridge()
        b.rescan = lambda: None         # never touch the real DolphinBar
        return b


class StartupState(_Base):
    def test_start_docked_with_bar_creates_pad(self):
        b = self._bridge(handheld=False, dolphinbar=True)
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertEqual(len(self.created), 1)      # pad created

    def test_start_handheld_is_padless(self):
        b = self._bridge(handheld=True)
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        self.assertEqual(self.created, [])          # no pad ever created

    def test_start_docked_no_dolphinbar_is_padless(self):
        b = self._bridge(handheld=False, dolphinbar=False)
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        self.assertEqual(self.created, [])          # no bar -> no pad


class RunTransitions(_Base):
    def test_docked_to_handheld_closes_pad(self):
        b = self._bridge(handheld=False)
        pad = b.ui
        self._set(handheld=True)
        b._apply_run_state()
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        pad.close.assert_called_once()              # the pad was destroyed

    def test_handheld_to_docked_reopens_pad(self):
        b = self._bridge(handheld=True)
        rescanned = []
        b.rescan = lambda: rescanned.append(True)
        self._set(handheld=False)
        b._apply_run_state()
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertTrue(rescanned)                  # slots re-scanned on redock

    def test_dolphinbar_unplugged_closes_pad(self):
        b = self._bridge(handheld=False, dolphinbar=True)
        pad = b.ui
        self._set(handheld=False, dolphinbar=False)  # bar removed while docked
        b._apply_run_state()
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)
        pad.close.assert_called_once()

    def test_dolphinbar_plugged_opens_pad(self):
        b = self._bridge(handheld=False, dolphinbar=False)   # docked, no bar -> padless
        self.assertTrue(b.disabled)
        rescanned = []
        b.rescan = lambda: rescanned.append(True)
        self._set(handheld=False, dolphinbar=True)   # bar plugged in
        b._apply_run_state()
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertTrue(rescanned)

    def test_no_op_when_state_unchanged(self):
        b = self._bridge(handheld=False)
        self._set(handheld=False, dolphinbar=True)   # still docked + bar
        b._apply_run_state()
        self.assertIsNotNone(b.ui)
        self.assertFalse(b.disabled)
        self.assertEqual(len(self.created), 1)      # no extra pad churn


class ShouldRunPredicate(_Base):
    """_should_run == docked AND DolphinBar; fail-safe keeps nav working on a probe glitch."""

    def test_probe_glitch_while_docked_keeps_nav(self):
        b = self._bridge(handheld=False, dolphinbar=True)
        wnb.dv.dolphinbar_present = mock.Mock(side_effect=OSError("probe fail"))
        wnb._handheld = lambda: False
        self.assertTrue(b._should_run())            # docked + probe error -> keep nav (True)
        wnb._handheld = lambda: True
        self.assertFalse(b._should_run())           # handheld short-circuits before the probe


class UInputFailure(_Base):
    """A failed /dev/uinput open must NOT crash the bridge; it stays disabled to retry."""

    def test_start_docked_open_failure_stays_disabled(self):
        self.ui_should_fail = True
        b = self._bridge(handheld=False)            # __init__ tries _open_ui, which raises inside
        self.assertIsNone(b.ui)
        self.assertTrue(b.disabled)                 # not crashed, stays disabled to retry

    def test_reopen_failure_stays_disabled(self):
        b = self._bridge(handheld=True)             # padless
        self.ui_should_fail = True
        self._set(handheld=False)                   # redock, but the pad won't open
        b._apply_run_state()
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
