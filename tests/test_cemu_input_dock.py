"""On-the-go handheld controller swap for Cemu / Wii U (lib/cemu_input_dock.py).

Transient whole-file swap of controller0.xml (the GamePad) <-> the handheld profile: handheld
swaps + snapshots once, restore is byte-identical, docked/disabled/non-participating/no-profile
are no-ops, and a crash orphan self-heals on the next launch (restore-first). Temp config_dir +
MAD_FORCE_CONTEXT. Run: python3 -m unittest tests.test_cemu_input_dock -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import cemu_input_dock

_DOCKED = "<emulated_controller><profile>WiiU Pro 1</profile></emulated_controller>\n"
_HANDHELD = "<emulated_controller><profile>Steamdeck</profile></emulated_controller>\n"


class CemuInputDock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.c0 = self.d / "controller0.xml"
        self.c0.write_text(_DOCKED)
        (self.d / "Steamdeck.xml").write_text(_HANDHELD)
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _pol(self, enabled=True, participating=True, profile="Steamdeck"):
        return {"handheld": {"enabled": enabled},
                "systems": {"wiiu": {"handheld": {"enabled": participating}}},
                "backends": {"cemu": {"config_dir": str(self.d), "handheld_profile": profile}}}

    def _apply(self, policy):
        with mock.patch.object(cemu_input_dock, "_load_policy", lambda: policy):
            return cemu_input_dock.apply()

    def _restore(self, policy):
        with mock.patch.object(cemu_input_dock, "_load_policy", lambda: policy):
            return cemu_input_dock.restore()

    def _backup(self):
        return self.d / "controller0.xml.dock-backup"

    # ── the swap ──────────────────────────────────────────────────────────────
    def test_handheld_swaps_and_snapshots(self):
        self._apply(self._pol())
        self.assertEqual(self.c0.read_text(), _HANDHELD)         # GamePad = handheld profile
        self.assertTrue(self._backup().is_file())
        self.assertEqual(self._backup().read_text(), _DOCKED)    # snapshot = resting docked profile

    def test_restore_byte_identical(self):
        before = self.c0.read_bytes()
        self._apply(self._pol())
        self.assertTrue(self._restore(self._pol()))
        self.assertEqual(self.c0.read_bytes(), before)           # exact revert
        self.assertFalse(self._backup().exists())

    # ── no-ops ────────────────────────────────────────────────────────────────
    def test_docked_noop(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        before = self.c0.read_bytes()
        self._apply(self._pol())
        self.assertEqual(self.c0.read_bytes(), before)
        self.assertFalse(self._backup().exists())

    def test_feature_disabled_noop(self):
        before = self.c0.read_bytes()
        self._apply(self._pol(enabled=False))
        self.assertEqual(self.c0.read_bytes(), before)
        self.assertFalse(self._backup().exists())

    def test_participation_off_noop(self):
        before = self.c0.read_bytes()
        self._apply(self._pol(participating=False))
        self.assertEqual(self.c0.read_bytes(), before)
        self.assertFalse(self._backup().exists())

    def test_no_profile_set_noop(self):
        before = self.c0.read_bytes()
        self._apply(self._pol(profile=""))
        self.assertEqual(self.c0.read_bytes(), before)
        self.assertFalse(self._backup().exists())

    def test_missing_profile_noop(self):
        before = self.c0.read_bytes()
        self._apply(self._pol(profile="DoesNotExist"))
        self.assertEqual(self.c0.read_bytes(), before)
        self.assertFalse(self._backup().exists())

    def test_controller0_missing_noop(self):
        self.c0.unlink()
        msg = self._apply(self._pol())
        self.assertIn("missing", msg)
        self.assertFalse(self._backup().exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod 000 does not block root")
    def test_controller0_unreadable_noop(self):
        self.c0.chmod(0o000)                          # present but unreadable -> must NOT throw
        try:
            msg = self._apply(self._pol())
        finally:
            self.c0.chmod(0o644)                      # let tearDown clean up
        self.assertIn("cannot read", msg.lower())
        self.assertFalse(self._backup().exists())

    def test_already_handheld_no_double_snapshot(self):
        self.c0.write_text(_HANDHELD)               # user already set Steamdeck as their GamePad
        self._apply(self._pol())
        self.assertEqual(self.c0.read_text(), _HANDHELD)
        self.assertFalse(self._backup().exists())   # must NOT snapshot the handheld profile as docked

    # ── crash-orphan self-heal (restore-first) ─────────────────────────────────
    def test_orphan_swept_on_docked_relaunch(self):
        self._apply(self._pol())                    # handheld swap -> c0=handheld, backup=docked
        self.assertTrue(self._backup().is_file())
        os.environ["MAD_FORCE_CONTEXT"] = "docked"  # dock + relaunch (crash skipped game-end)
        self._apply(self._pol())                    # restore-first sweeps the orphan
        self.assertEqual(self.c0.read_text(), _DOCKED)
        self.assertFalse(self._backup().exists())

    def test_orphan_reapplied_keeps_docked_snapshot(self):
        self._apply(self._pol())                    # c0=handheld, backup=docked
        self._apply(self._pol())                    # crash relaunch (still handheld): sweep + re-swap
        self.assertEqual(self.c0.read_text(), _HANDHELD)
        self.assertEqual(self._backup().read_text(), _DOCKED)   # backup is the DOCKED profile, not handheld


if __name__ == "__main__":
    unittest.main()
