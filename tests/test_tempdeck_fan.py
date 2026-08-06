"""Fan-control interlocks for ~/bin/temp-deck.py.

Forcing the Deck's fan OFF is the only genuinely dangerous thing this monitor
can do: jupiter-fan-control exists to hold the APU under 90 C and the firmware
trips passive at 100 C. Every route back out of OFF is covered here.

The helper is never actually invoked. FAN_SUDO and FAN_HELPER are redirected at a
recording mock, so these run without root and without touching the real fan. The
out-of-process backstop (a root-side `systemd-run --on-active` timer that restores
the fan even after kill -9) cannot be exercised from a unit test; it was verified
by hand on the device, see the journal at 2026-08-06 14:54:35.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from tests._ci import skip_on_ci

# The REPO copy, which is what install.sh deploys to ~/bin - not ~/bin itself. Testing
# the deployed copy would exercise whatever happens to be on this machine, including a
# hand-edited or stale one, instead of the file that actually ships.
MONITOR = str(Path(__file__).resolve().parent.parent / "temp-deck.py")


def load_monitor():
    spec = importlib.util.spec_from_file_location("temp_deck", MONITOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class FanInterlocks(unittest.TestCase):

    def setUp(self):
        self.tn = load_monitor()
        self.dir = tempfile.mkdtemp(prefix="tempdeck-fan-test.")
        self.log = os.path.join(self.dir, "calls.log")
        self.mock = os.path.join(self.dir, "mock-fan-ctl")
        with open(self.mock, "w") as handle:
            handle.write(f'#!/bin/bash\necho "$1" >> {self.log}\necho "$1"\nexit 0\n')
        os.chmod(self.mock, 0o755)
        self.tn.FAN_HELPER = self.mock
        self.tn.FAN_SUDO = []          # the mock needs no privilege
        # available() shells out to probe sudo; short-circuit it so these tests
        # exercise the interlock logic rather than the local sudo configuration.
        self.tn.FanController.available = lambda ctl: (
            (True, "ready", "ready") if ctl.enabled else (False, "disabled", "disabled")
        )

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        try:
            return open(self.log).read().split()
        finally:
            os.unlink(self.log)

    def controller(self, enabled=True, max_temp=70.0, timeout=600):
        return self.tn.FanController(enabled, max_temp, timeout)

    # -- opt-in ------------------------------------------------------------

    def test_disabled_controller_is_inert(self):
        fan = self.controller(enabled=False)
        fan.cycle(40.0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(fan.mode, self.tn.FAN_AUTO)

    # -- the cycle ---------------------------------------------------------

    def test_cycle_is_auto_off_max_auto(self):
        fan = self.controller()
        fan.cycle(40.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_OFF, ["off"]))
        fan.cycle(40.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_MAX, ["max"]))
        fan.cycle(40.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_AUTO, ["auto"]))

    # -- refusing OFF ------------------------------------------------------

    def test_off_is_refused_when_already_hot(self):
        fan = self.controller()
        fan.cycle(85.0)
        self.assertEqual(fan.mode, self.tn.FAN_MAX, "should skip past OFF, not stall")
        self.assertEqual(self.calls(), ["max"])
        message = fan.active_message() or ""
        self.assertIn("refused", message)
        self.assertIn("85", message, "the refusal must name the temperature")

    # -- getting back out of OFF ------------------------------------------

    def test_guard_reverts_when_the_apu_heats_up(self):
        fan = self.controller()
        fan.cycle(40.0)
        self.calls()
        fan.guard(50.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_OFF, []))
        fan.guard(72.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_AUTO, ["auto"]))
        self.assertIn("restored", fan.active_message() or "")

    def test_deadman_reverts_even_while_cold(self):
        fan = self.controller(timeout=30)
        fan.cycle(40.0)
        self.calls()
        fan.since = time.time() - 31
        fan.guard(40.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_AUTO, ["auto"]))

    def test_max_also_carries_a_deadman(self):
        fan = self.controller(timeout=20)
        fan.set(self.tn.FAN_MAX)
        self.calls()
        fan.since = time.time() - 21
        fan.guard(40.0)
        self.assertEqual((fan.mode, self.calls()), (self.tn.FAN_AUTO, ["auto"]))

    def test_restore_is_idempotent(self):
        fan = self.controller()
        fan.cycle(40.0)
        self.calls()
        fan.restore()
        self.assertEqual(self.calls(), ["auto"])
        fan.restore()
        self.assertEqual(self.calls(), [], "second restore must be a no-op")
        self.assertEqual(fan.mode, self.tn.FAN_AUTO)

    def test_timeout_is_clamped_to_the_helper_backstop(self):
        # The in-process timer may only ever fire EARLIER than the root-side one.
        self.assertEqual(self.controller(timeout=99999).timeout,
                         self.tn.FAN_DEADMAN_MAX)
        self.assertEqual(self.controller(timeout=1).timeout, 10)

    # -- failure handling --------------------------------------------------

    def test_helper_failure_does_not_fake_a_mode_change(self):
        failing = os.path.join(self.dir, "mock-fail")
        with open(failing, "w") as handle:
            handle.write('#!/bin/bash\necho "nope" >&2\nexit 1\n')
        os.chmod(failing, 0o755)
        self.tn.FAN_HELPER = failing
        fan = self.controller()
        fan.cycle(40.0)
        self.assertEqual(fan.mode, self.tn.FAN_AUTO)
        self.assertIn("nope", fan.active_message() or "")

    def test_label_shows_the_countdown(self):
        fan = self.controller()
        fan.set(self.tn.FAN_OFF)
        self.assertTrue(fan.label().startswith("OFF"))
        self.assertIn("left", fan.label())
        fan.set(self.tn.FAN_AUTO)
        self.assertEqual(fan.label(), "AUTO")


@skip_on_ci
@unittest.skipUnless(os.path.exists(MONITOR), "temp-deck.py not installed")
class AvailabilityStates(unittest.TestCase):
    """The three failure modes must stay distinct.

    Telling someone to run --install-fan-helper when the files are already on
    disk sends them round a loop that cannot fix anything; a failed passwordless
    probe can simply mean sudo's cached credential lapsed.
    """

    def setUp(self):
        self.tn = load_monitor()

    def test_disabled(self):
        self.assertEqual(self.tn.FanController(False, 70, 600).available()[2],
                         "disabled")

    def test_missing_helper_points_at_the_installer(self):
        self.tn.FAN_HELPER = "/nonexistent/deck-fan-ctl"
        ok, why, state = self.tn.FanController(True, 70, 600).available()
        self.assertEqual(state, "not_installed")
        self.assertIn("--install-fan-helper", why)

    def test_installed_but_locked_does_not_say_reinstall(self):
        self.tn.FAN_SUDO = ["false", "--"]
        if not os.path.exists(self.tn.FAN_SUDOERS):
            self.skipTest("fan helper not installed on this Deck")
        ok, why, state = self.tn.FanController(True, 70, 600).available()
        self.assertEqual(state, "needs_auth")
        self.assertNotIn("--install-fan-helper", why)

    def test_no_sudoers_at_all_does_say_reinstall(self):
        self.tn.FAN_SUDO = ["false", "--"]
        self.tn.FAN_SUDOERS = "/nonexistent/zz-deck-fan"
        self.tn.FAN_SUDOERS_LEGACY = "/nonexistent/99-deck-fan"
        ok, why, state = self.tn.FanController(True, 70, 600).available()
        self.assertEqual(state, "not_installed")
        self.assertIn("--install-fan-helper", why)


if __name__ == "__main__":
    unittest.main()
