"""Citron launch routing + dock auto-detect (switch_bind):
config-path dispatch, transient membership, the dock snapshot (a dict), the docked/handheld
write with the mandatory \\default flip, the auto-detect-off no-op, and the snapshot->write->
restore round-trip that reverts use_docked_mode to the resting value (so a later Steam-UI
handheld launch stays clean)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import switch_bind as sb
from lib.madsrv import cfgutil

FIX = (
    "[Controls]\n"
    "player_0_button_a=engine:sdl,port:0,guid:AAAA,button:1\n\n"
    "[System]\n"
    "use_docked_mode\\default=true\nuse_docked_mode=1\n"
    "region_index\\default=true\nregion_index=1\n"
)


class CitronRouting(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(FIX, newline="")
        self._orig_auto = sb._citron_dock_autodetect
        sb._citron_dock_autodetect = lambda: True     # default ON; tests override via _mock
        import lib.gui_theme as gt
        self._orig_ext = gt.external_display_connected

    def tearDown(self):
        sb._citron_dock_autodetect = self._orig_auto
        import lib.gui_theme as gt
        gt.external_display_connected = self._orig_ext
        shutil.rmtree(self.d, ignore_errors=True)

    def _mock(self, *, autodetect=True, docked=True):
        sb._citron_dock_autodetect = lambda: autodetect
        import lib.gui_theme as gt
        gt.external_display_connected = lambda: docked

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "System", key)

    # ── dispatch ─────────────────────────────────────────────────────────────
    def test_target_is_citron_ini(self):
        self.assertEqual(sb._target("citron", "x.nsp"), sb._CITRON_INI)

    def test_membership(self):
        self.assertIn("citron", sb._TRANSIENT)
        self.assertEqual(sb._PLAYERS["citron"], 8)
        self.assertIn(sb._CITRON_INI, list(sb._known_configs()))

    # ── snapshot is a dict (controls + docked value + docked \default) ────────
    def test_snapshot_records_docked(self):
        snap = sb._snapshot("citron", self.ini)
        self.assertIsInstance(snap, dict)
        self.assertEqual(snap["docked"], "1")
        self.assertEqual(snap["docked_default"], "true")
        self.assertIn("player_0_button_a", snap["controls"])

    # ── the dock write flips \default (else Citron discards it) ───────────────
    def test_dock_write_docked(self):
        self._mock(autodetect=True, docked=True)
        sb._apply_citron_dock(self.ini)
        self.assertEqual(self._disk("use_docked_mode"), "1")
        self.assertEqual(self._disk("use_docked_mode\\default"), "false")

    def test_dock_write_handheld(self):
        self._mock(autodetect=True, docked=False)
        sb._apply_citron_dock(self.ini)
        self.assertEqual(self._disk("use_docked_mode"), "0")
        self.assertEqual(self._disk("use_docked_mode\\default"), "false")

    def test_autodetect_off_is_noop(self):
        self._mock(autodetect=False, docked=False)
        before = self.ini.read_text(newline="")
        sb._apply_citron_dock(self.ini)
        self.assertEqual(self.ini.read_text(newline=""), before)   # untouched

    # ── the round-trip: snapshot -> handheld write -> restore reverts to resting ──
    def test_restore_reverts_docked_to_resting(self):
        # resting = docked=1/\default=true. Snapshot it, write handheld (0/false), then restore.
        snap = sb._snapshot("citron", self.ini)
        side = sb._sidecar(self.ini)
        side.write_text(json.dumps({"emu": "citron", "input": snap}))
        self._mock(autodetect=True, docked=False)
        sb._apply_citron_dock(self.ini)
        self.assertEqual(self._disk("use_docked_mode"), "0")        # handheld written
        sb.restore_target(self.ini)
        self.assertEqual(self._disk("use_docked_mode"), "1")        # reverted to resting
        self.assertEqual(self._disk("use_docked_mode\\default"), "true")
        self.assertFalse(side.exists())                            # sidecar dropped
        self.assertIn("player_0_button_a", self.ini.read_text(newline=""))  # controls kept

    def test_autodetect_off_skips_docked_snapshot(self):
        # with the toggle OFF we must NOT snapshot/revert use_docked_mode (else an in-Citron docked
        # change during play would be clobbered on exit).
        self._mock(autodetect=False)
        snap = sb._snapshot("citron", self.ini)
        self.assertNotIn("dock_managed", snap)
        self.assertIn("controls", snap)

    def test_restore_removes_key_absent_at_rest(self):
        # resting [System] has NO use_docked_mode; the dock write inserts it -> restore must REMOVE it.
        self.ini.write_text("[Controls]\nplayer_0_button_a=x\n\n[System]\nregion_index=1\n", newline="")
        self._mock(autodetect=True, docked=True)
        snap = sb._snapshot("citron", self.ini)
        self.assertTrue(snap.get("dock_managed"))
        self.assertIsNone(snap.get("docked"))                      # absent at rest
        side = sb._sidecar(self.ini)
        side.write_text(json.dumps({"emu": "citron", "input": snap}))
        sb._apply_citron_dock(self.ini)
        self.assertEqual(self._disk("use_docked_mode"), "1")       # inserted at launch
        sb.restore_target(self.ini)
        self.assertIsNone(self._disk("use_docked_mode"))           # removed on exit (transient contract)


if __name__ == "__main__":
    unittest.main()
