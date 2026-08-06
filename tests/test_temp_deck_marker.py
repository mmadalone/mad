"""temp-deck.py's fan-helper OPT-IN MARKER (~/.config/temp-deck/fan-helper-installed).

Why this matters: the helper (/var/lib/deck-fan/deck-fan-ctl) and its sudoers rule
are BOTH wiped by every SteamOS update, so neither can signal "this Deck wanted fan
control". Only the $HOME marker survives, and deck-post-update.sh reinstalls the
helper solely while it exists.

Found live on 2026-08-06: the helper was installed and working, the marker was
absent, so the post-update reapply would have silently skipped and fan control
would have broken on the next OS update with nothing on screen to say why. Cause:
a helper installed by a build predating the marker, or a failed marker write --
both leave a working helper permanently unmarked. available() now self-heals.

Run:  python3 -m unittest tests.test_temp_deck_marker -v
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "temp-deck.py"


def _load():
    spec = importlib.util.spec_from_file_location("temp_deck_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FanMarker(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.m = _load()
        self.marker = self.t / "config" / "temp-deck" / "fan-helper-installed"
        self.m.FAN_MARKER = str(self.marker)
        # A helper that always succeeds, invoked with no sudo prefix.
        self.m.FAN_HELPER = "/bin/true"
        self.m.FAN_SUDO = []

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def _fc(self, enabled=True):
        return self.m.FanController(enabled=enabled, max_temp=70.0, timeout=600)

    def test_write_creates_parent_dirs(self):
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.m.write_fan_marker())
        self.assertTrue(self.marker.is_file())
        self.assertIn("deck-post-update.sh", self.marker.read_text())

    def test_write_never_raises_on_unwritable_path(self):
        # Best-effort by contract: a failed marker write must not break the install.
        self.m.FAN_MARKER = "/proc/definitely/not/writable/marker"
        self.assertFalse(self.m.write_fan_marker())

    def test_available_self_heals_a_missing_marker(self):
        # THE REGRESSION: helper works, marker absent -> the marker must appear.
        self.assertFalse(self.marker.exists())
        usable, _msg, state = self._fc().available()
        self.assertTrue(usable)
        self.assertEqual(state, "ready")
        self.assertTrue(self.marker.is_file(),
                        "a working helper with no marker must self-heal, or the "
                        "post-update reapply silently skips forever")

    def test_available_does_not_rewrite_an_existing_marker(self):
        self.m.write_fan_marker()
        self.marker.write_text("SENTINEL\n")
        before = self.marker.stat().st_mtime_ns
        self._fc().available()
        self.assertEqual(self.marker.read_text(), "SENTINEL\n",
                         "must not rewrite on every tick")
        self.assertEqual(self.marker.stat().st_mtime_ns, before)

    def test_no_marker_written_when_helper_absent(self):
        self.m.FAN_HELPER = str(self.t / "nope")
        usable, _msg, state = self._fc().available()
        self.assertFalse(usable)
        self.assertEqual(state, "not_installed")
        self.assertFalse(self.marker.exists(),
                         "never claim opt-in for a Deck with no helper")

    def test_no_marker_written_when_fan_control_disabled(self):
        usable, _msg, state = self._fc(enabled=False).available()
        self.assertFalse(usable)
        self.assertEqual(state, "disabled")
        self.assertFalse(self.marker.exists())

    def test_no_marker_written_when_probe_fails(self):
        # needs_auth / broken helper: opt-in is not proven, so do not claim it.
        self.m.FAN_HELPER = "/bin/false"
        usable, _msg, _state = self._fc().available()
        self.assertFalse(usable)
        self.assertFalse(self.marker.exists())


class ScriptShape(unittest.TestCase):
    def test_stdlib_only(self):
        # The installer deploys this with no pip step, so a third-party import
        # would break it on a clean Deck. Import it in a bare interpreter.
        import subprocess
        r = subprocess.run(["python3", "-c",
                            f"import importlib.util as u;"
                            f"s=u.spec_from_file_location('t','{SCRIPT}');"
                            f"m=u.module_from_spec(s);s.loader.exec_module(m)"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "PYTHONPATH": ""})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_executable_bit(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK),
                        "install.sh deploys this as a runnable command")


if __name__ == "__main__":
    unittest.main()
