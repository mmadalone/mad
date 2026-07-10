"""On-the-go TDP watt-cap rail (lib/deck_power.py).

Covers the sidecar snapshot/apply/restore round-trip (byte-stable), crash-sweep-before-reapply,
only-ever-LOWER, self-floor, restore-keeps-sidecar-on-write-failure, malformed-policy tolerance,
watts parse, and the docked short-circuit. Uses a FAKE amdgpu hwmon (temp files) + MAD_FORCE_CONTEXT
- never touches real hardware. Run: python3 -m unittest tests.test_deck_power -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import deck_power

_HANDHELD = "handheld"
_DOCKED = "docked"


def _mkhwmon(d: Path, p1=15_000_000, p2=15_000_000, default=15_000_000):
    (d / "power1_cap").write_text(str(p1))
    (d / "power2_cap").write_text(str(p2))
    (d / "power1_cap_default").write_text(str(default))
    (d / "power1_cap_max").write_text("29000000")


def _pol(watt_default=None, sys_cap=None):
    hh = {"enabled": True}
    if watt_default is not None:
        hh["default_watt_cap"] = watt_default
    sysh = {"enabled": True}
    if sys_cap is not None:
        sysh["watt_cap"] = sys_cap
    return {"handheld": hh, "systems": {"switch": {"handheld": sysh}}}


class DeckPower(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.hw = self.d / "hwmon"
        self.hw.mkdir()
        _mkhwmon(self.hw)
        self.sidecar = self.d / ".mad-power-restore"
        self._p_hw = mock.patch.object(deck_power, "_amdgpu_hwmon", lambda: str(self.hw))
        self._p_sc = mock.patch.object(deck_power, "SIDECAR", self.sidecar)
        self._p_hw.start()
        self._p_sc.start()
        os.environ.pop("MAD_FORCE_CONTEXT", None)

    def tearDown(self):
        self._p_hw.stop()
        self._p_sc.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _cap(self):
        return int((self.hw / "power1_cap").read_text())

    # ── the core round-trip ──
    def test_apply_restore_byte_stable(self):
        before = (self.hw / "power1_cap").read_bytes()
        deck_power._apply(10)
        self.assertEqual(self._cap(), 10_000_000)
        self.assertTrue(self.sidecar.exists())
        deck_power.restore()
        self.assertEqual((self.hw / "power1_cap").read_bytes(), before)   # byte-identical resting value
        self.assertFalse(self.sidecar.exists())

    def test_only_ever_lower(self):
        ok, _ = deck_power._apply(20)          # 20 >= 15 default -> clamps to 15 -> no downshift
        self.assertFalse(ok)
        self.assertEqual(self._cap(), 15_000_000)
        self.assertFalse(self.sidecar.exists())

    def test_self_floor(self):
        deck_power._apply(2)                   # below the 4 W floor
        self.assertEqual(self._cap(), deck_power._FLOOR_UW)

    def test_restore_value_sanitize(self):
        self.assertEqual(deck_power._restore_value("1"), deck_power._FLOOR_UW)   # floored
        self.assertIsNone(deck_power._restore_value("garbage"))
        self.assertEqual(deck_power._restore_value("15000000"), 15_000_000)

    def test_restore_keeps_sidecar_on_failure(self):
        deck_power._apply(10)
        with mock.patch.object(deck_power, "_amdgpu_hwmon", lambda: None):
            ok, _ = deck_power.restore()
        self.assertFalse(ok)
        self.assertTrue(self.sidecar.exists())            # kept so a later sweep retries
        self.assertTrue(deck_power.restore()[0])          # hwmon back -> restores + clears
        self.assertFalse(self.sidecar.exists())

    def test_crash_sweep_before_reapply(self):
        deck_power._apply(10)                              # orphan: capped, sidecar records resting 15
        with mock.patch.object(deck_power, "_load_policy", lambda: _pol()):
            os.environ["MAD_FORCE_CONTEXT"] = _HANDHELD
            deck_power._cli_apply("switch")               # sweeps first, then re-caps
        # the sidecar must hold the TRUE resting 15, never the already-lowered 10
        self.assertEqual(self.sidecar.read_text().splitlines()[0], "power1_cap=15000000")

    # ── policy-driven entry ──
    def test_cli_per_system_cap(self):
        with mock.patch.object(deck_power, "_load_policy", lambda: _pol(watt_default=10, sys_cap=13)):
            os.environ["MAD_FORCE_CONTEXT"] = _HANDHELD
            deck_power._cli_apply("switch")
        self.assertEqual(self._cap(), 13_000_000)         # per-system 13 W wins over default 10 W

    def test_docked_no_cap(self):
        with mock.patch.object(deck_power, "_load_policy", lambda: _pol()):
            os.environ["MAD_FORCE_CONTEXT"] = _DOCKED
            r = deck_power._cli_apply("switch")
        self.assertIn("docked", r)
        self.assertEqual(self._cap(), 15_000_000)
        self.assertFalse(self.sidecar.exists())

    def test_disabled_and_nonparticipating(self):
        with mock.patch.object(deck_power, "_load_policy", lambda: {"handheld": {"enabled": False}}):
            os.environ["MAD_FORCE_CONTEXT"] = _HANDHELD
            self.assertIn("disabled", deck_power._cli_apply("switch"))
        with mock.patch.object(deck_power, "_load_policy", lambda: {"handheld": {"enabled": True}, "systems": {}}):
            os.environ["MAD_FORCE_CONTEXT"] = _HANDHELD
            self.assertIn("not participating", deck_power._cli_apply("nes"))
        self.assertEqual(self._cap(), 15_000_000)

    # ── malformed policy / parse ──
    def test_malformed_policy_no_crash_and_sweeps(self):
        deck_power._apply(10)                              # pre-existing orphan
        with mock.patch.object(deck_power, "_load_policy", lambda: {"handheld": "x", "systems": "y"}):
            os.environ["MAD_FORCE_CONTEXT"] = _HANDHELD
            deck_power._cli_apply("switch")               # must not raise
        self.assertEqual(self._cap(), 15_000_000)         # orphan swept first even though policy is junk
        self.assertFalse(self.sidecar.exists())

    def test_coerce_watts(self):
        self.assertEqual(deck_power._coerce_watts("12"), 12)
        self.assertEqual(deck_power._coerce_watts("11.5"), 11)
        self.assertEqual(deck_power._coerce_watts(None, 12), 12)
        self.assertEqual(deck_power._coerce_watts("junk", 12), 12)

    def test_dget_tolerates_nondict(self):
        self.assertIsNone(deck_power._dget("x", "k"))
        self.assertEqual(deck_power._dget({"k": 1}, "k"), 1)


if __name__ == "__main__":
    unittest.main()
