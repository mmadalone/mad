"""Tests for suspend-mode-setup.sh: the QUIRK-AWARE decision (--check) across
s2idle-supported x INSTALL_SUSPEND x pin-present, plus the apply path (s2idle-blocked writes
the deep pin; s2idle-supported moves a stale pin to a recoverable _TMP). Drives the decision
via MAD_S2IDLE_OK (the kernel-quirk override) + MAD_SUSPEND_PIN + stubbed sudo so nothing
touches the real /etc or /sys. Run: python3 -m unittest tests.test_suspend_mode -v
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
    def _check(self, s2idle_ok, pref, pin_exists) -> int:
        tmp = Path(tempfile.mkdtemp())
        pin = tmp / "99-mem_sleep.conf"
        if pin_exists:
            pin.write_text("w /sys/power/mem_sleep - - - - deep\n")
        env = {**os.environ, "MAD_S2IDLE_OK": s2idle_ok, "INSTALL_SUSPEND": pref,
               "MAD_SUSPEND_PIN": str(pin), "MAD_INSTALL_CONF": str(tmp / "none.conf")}
        return subprocess.run(["bash", str(SCRIPT), "--check"], env=env,
                              capture_output=True, text=True).returncode

    def test_s2idle_blocked_wants_pin(self):
        # The quirk case (LCD AND this OLED): s2idle blocked -> deep -> pin must exist.
        self.assertEqual(self._check("0", "auto", True), 0)    # pin present = correct
        self.assertEqual(self._check("0", "auto", False), 1)   # pin missing = needs apply

    def test_s2idle_supported_wants_no_pin(self):
        # A kernel that truly allows s2idle: no deep pin wanted.
        self.assertEqual(self._check("1", "auto", False), 0)   # no pin = correct (s2idle)
        self.assertEqual(self._check("1", "auto", True), 1)    # stale pin = needs fix

    def test_off_always_ok(self):
        self.assertEqual(self._check("0", "off", False), 0)
        self.assertEqual(self._check("1", "off", True), 0)

    def test_force_on_wants_pin(self):
        # INSTALL_SUSPEND=on forces deep regardless of s2idle support.
        self.assertEqual(self._check("1", "on", True), 0)
        self.assertEqual(self._check("1", "on", False), 1)


class SuspendApply(unittest.TestCase):
    def setUp(self):
        self.bin = _stub_bin()
        self.home = Path(tempfile.mkdtemp())
        self.tmp = Path(tempfile.mkdtemp())
        self.pin = self.tmp / "99-mem_sleep.conf"

    def _apply(self, s2idle_ok, pref="auto"):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
               "MAD_S2IDLE_OK": s2idle_ok, "INSTALL_SUSPEND": pref,
               "MAD_SUSPEND_PIN": str(self.pin), "HOME": str(self.home),
               "MAD_INSTALL_CONF": str(self.tmp / "none.conf")}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def test_blocked_writes_pin(self):
        r = self._apply("0")
        self.assertTrue(self.pin.exists(), r.stdout + r.stderr)
        self.assertIn("deep", self.pin.read_text())

    def test_supported_removes_stale_pin_to_tmp(self):
        self.pin.write_text("w /sys/power/mem_sleep - - - - deep\n")
        r = self._apply("1")
        self.assertFalse(self.pin.exists(), r.stdout + r.stderr)
        tmps = list((self.home / "Downloads").glob("_TMP-suspend-fix-*"))
        self.assertTrue(tmps, "no _TMP backup created")
        self.assertTrue((tmps[0] / "99-mem_sleep.conf").exists(), "pin not moved into _TMP")

    def test_off_leaves_pin_untouched(self):
        self.pin.write_text("deep\n")
        self._apply("1", "off")
        self.assertTrue(self.pin.exists())


class SuspendDetect(unittest.TestCase):
    """Exercises the REAL detection (NO MAD_S2IDLE_OK override) via a stubbed journalctl + fake
    mem_sleep + a faked DMI string (MAD_DMI_PRODUCT). On a STEAM DECK the decision is DMI-driven
    (always deep — the quirk string is too transient to trust); the journal/mem_sleep path is only
    for NON-Decks, so those cases pass dmi='Generic Laptop'."""

    def _check(self, mem_sleep, klog, dmi="Generic Laptop") -> int:
        d = Path(tempfile.mkdtemp())
        (d / "journalctl").write_text(f'#!/bin/bash\ncat <<"__EOF__"\n{klog}\n__EOF__\n')
        (d / "journalctl").chmod(0o755)
        (d / "mem_sleep").write_text(mem_sleep)
        env = {k: v for k, v in os.environ.items()
               if k not in ("MAD_S2IDLE_OK", "MAD_DMI_PRODUCT")}
        env.update({"PATH": f"{d}:{os.environ['PATH']}",
                    "MAD_MEM_SLEEP_FILE": str(d / "mem_sleep"),
                    "MAD_DMI_PRODUCT": dmi,
                    "MAD_SUSPEND_PIN": str(d / "99-mem_sleep.conf"),  # no pin present
                    "INSTALL_SUSPEND": "auto", "MAD_INSTALL_CONF": str(d / "none.conf")})
        return subprocess.run(["bash", str(SCRIPT), "--check"], env=env,
                              capture_output=True, text=True).returncode

    def test_steamdeck_dmi_forces_deep(self):
        # THE regression: a Steam Deck wants deep even when the boot log has NO quirk string
        # (it ages out of `journalctl -kb`) and mem_sleep lists s2idle. DMI is the reliable signal.
        self.assertEqual(self._check("s2idle [deep]\n", "boot log, no quirk", dmi="Galileo"), 1)
        self.assertEqual(self._check("s2idle [deep]\n", "no quirk here", dmi="Valve Jupiter"), 1)

    def test_nondeck_quirk_present_decides_deep(self):
        self.assertEqual(self._check("s2idle [deep]\n",
                                     "PM: Steam Deck quirk - no s2idle allowed!"), 1)

    def test_nondeck_no_quirk_and_listed_decides_s2idle(self):
        # Non-Deck: s2idle listed AND no quirk -> supported -> no pin = correct (exit 0).
        self.assertEqual(self._check("[s2idle] deep\n", "boot log without the quirk"), 0)

    def test_nondeck_empty_journal_falls_back_to_deep(self):
        # Non-Deck, can't verify (empty journal) -> safe default deep; no pin => needs apply (1).
        self.assertEqual(self._check("s2idle [deep]\n", ""), 1)

    def test_nondeck_mem_sleep_without_s2idle_decides_deep(self):
        self.assertEqual(self._check("[deep]\n", "boot log without the quirk"), 1)


if __name__ == "__main__":
    unittest.main()
