"""Tests for suspend-mode-setup.sh: the model-aware decision (--check) across model x
INSTALL_SUSPEND x pin-present, plus the apply path (LCD writes the pin; OLED moves a stale
pin to a recoverable _TMP). Uses MAD_DECK_MODEL / MAD_SUSPEND_PIN overrides + stubbed sudo so
nothing touches the real /etc or /sys. Run: python3 -m unittest tests.test_suspend_mode -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "suspend-mode-setup.sh"


def _stub_bin() -> Path:
    """A PATH dir whose `sudo` skips /sys/power writes (else exec) and `systemd-tmpfiles` no-ops."""
    d = Path(tempfile.mkdtemp())
    (d / "sudo").write_text('#!/bin/bash\ncase "$*" in *"/sys/power/"*) exit 0 ;; esac\nexec "$@"\n')
    (d / "systemd-tmpfiles").write_text('#!/bin/bash\nexit 0\n')
    for f in ("sudo", "systemd-tmpfiles"):
        (d / f).chmod(0o755)
    return d


class SuspendCheck(unittest.TestCase):
    def _check(self, model, pref, pin_exists) -> int:
        tmp = Path(tempfile.mkdtemp())
        pin = tmp / "99-mem_sleep.conf"
        if pin_exists:
            pin.write_text("w /sys/power/mem_sleep - - - - deep\n")
        env = {**os.environ, "MAD_DECK_MODEL": model, "INSTALL_SUSPEND": pref,
               "MAD_SUSPEND_PIN": str(pin), "MAD_INSTALL_CONF": str(tmp / "none.conf")}
        return subprocess.run(["bash", str(SCRIPT), "--check"], env=env,
                              capture_output=True, text=True).returncode

    def test_lcd_wants_pin(self):
        self.assertEqual(self._check("lcd", "auto", True), 0)    # pin present = correct
        self.assertEqual(self._check("lcd", "auto", False), 1)   # pin missing = needs apply

    def test_oled_wants_no_pin(self):
        self.assertEqual(self._check("oled", "auto", False), 0)  # no pin = correct (s2idle)
        self.assertEqual(self._check("oled", "auto", True), 1)   # stale pin = needs fix

    def test_off_always_ok(self):
        self.assertEqual(self._check("lcd", "off", False), 0)
        self.assertEqual(self._check("oled", "off", True), 0)

    def test_force_on_wants_pin(self):
        self.assertEqual(self._check("oled", "on", True), 0)
        self.assertEqual(self._check("oled", "on", False), 1)


class SuspendApply(unittest.TestCase):
    def setUp(self):
        self.bin = _stub_bin()
        self.home = Path(tempfile.mkdtemp())
        self.tmp = Path(tempfile.mkdtemp())
        self.pin = self.tmp / "99-mem_sleep.conf"

    def _apply(self, model, pref="auto"):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
               "MAD_DECK_MODEL": model, "INSTALL_SUSPEND": pref,
               "MAD_SUSPEND_PIN": str(self.pin), "HOME": str(self.home),
               "MAD_INSTALL_CONF": str(self.tmp / "none.conf")}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def test_lcd_writes_pin(self):
        r = self._apply("lcd")
        self.assertTrue(self.pin.exists(), r.stdout + r.stderr)
        self.assertIn("deep", self.pin.read_text())

    def test_oled_removes_stale_pin_to_tmp(self):
        self.pin.write_text("w /sys/power/mem_sleep - - - - deep\n")
        r = self._apply("oled")
        self.assertFalse(self.pin.exists(), r.stdout + r.stderr)
        tmps = list((self.home / "Downloads").glob("_TMP-suspend-fix-*"))
        self.assertTrue(tmps, "no _TMP backup created")
        self.assertTrue((tmps[0] / "99-mem_sleep.conf").exists(), "pin not moved into _TMP")

    def test_off_leaves_pin_untouched(self):
        self.pin.write_text("deep\n")
        self._apply("oled", "off")
        self.assertTrue(self.pin.exists())


if __name__ == "__main__":
    unittest.main()
