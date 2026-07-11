"""Per-game HANDHELD RetroArch input remap rail (lib/ra_handheld_pergame.py, WS-I).

Store round-trip + the transient snapshot/apply/restore rail (write the handheld remap into the
launching core's .rmp when handheld, revert on exit, crash-safe). In-memory .rmp + monkeypatched gate;
no hardware. Run: python3 -m unittest tests.test_ra_handheld_pergame -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import ra_handheld_pergame as rhp

_TID = "snes:GameX"


class Store(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._save = rhp.STORE
        rhp.STORE = self.d / "store.json"

    def tearDown(self):
        rhp.STORE = self._save
        shutil.rmtree(self.d, ignore_errors=True)

    def test_set_get_roundtrip_and_clear(self):
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        self.assertEqual(rhp.get_pergame(_TID), {"input_player1_btn_a": "1"})
        rhp.set_pergame(_TID, {})                 # empty -> clears the entry
        self.assertEqual(rhp.get_pergame(_TID), {})
        self.assertEqual(rhp.get_pergame("other:Game"), {})   # unknown -> {}


class Rail(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._save = (rhp.STORE, rhp._SIDECAR, rhp.rmp.get_game_remap, rhp.rmp.set_game_remap,
                      rhp.retroarch_cfg.launched_core, rhp.retroarch_cfg.ensure_pergame_enabled,
                      rhp._handheld)
        rhp.STORE = self.d / "store.json"
        rhp._SIDECAR = self.d / "restore.json"
        self.disk: dict = {}                      # in-memory .rmp: {(system, stem, core): mapping}
        rhp.rmp.get_game_remap = lambda s, st, only_core=None, **k: dict(self.disk.get((s, st, only_core), {}))
        rhp.rmp.set_game_remap = self._set
        rhp.retroarch_cfg.launched_core = lambda s, st: "TestCore"
        rhp.retroarch_cfg.ensure_pergame_enabled = lambda flags: None
        rhp._handheld = lambda: True

    def _set(self, s, st, mapping, only_core=None):
        if mapping:
            self.disk[(s, st, only_core)] = dict(mapping)
        else:
            self.disk.pop((s, st, only_core), None)
        return []

    def tearDown(self):
        (rhp.STORE, rhp._SIDECAR, rhp.rmp.get_game_remap, rhp.rmp.set_game_remap,
         rhp.retroarch_cfg.launched_core, rhp.retroarch_cfg.ensure_pergame_enabled,
         rhp._handheld) = self._save
        shutil.rmtree(self.d, ignore_errors=True)

    def _rmp(self):
        return self.disk.get(("snes", "GameX", "TestCore"))

    def _snap(self):
        return json.loads(rhp._SIDECAR.read_text())["resting"] if rhp._SIDECAR.is_file() else None

    def test_apply_writes_handheld_and_snapshots_permanent(self):
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        self.disk[("snes", "GameX", "TestCore")] = {"input_player1_btn_b": "9"}   # a permanent remap
        rhp.apply("snes", "GameX")
        self.assertEqual(self._rmp(), {"input_player1_btn_a": "1"})   # handheld remap live
        self.assertEqual(self._snap(), {"input_player1_btn_b": "9"})  # permanent snapshotted

    def test_restore_reverts_to_permanent(self):
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        self.disk[("snes", "GameX", "TestCore")] = {"input_player1_btn_b": "9"}
        rhp.apply("snes", "GameX")
        rhp.restore()
        self.assertEqual(self._rmp(), {"input_player1_btn_b": "9"})   # permanent restored
        self.assertFalse(rhp._SIDECAR.is_file())

    def test_restore_removes_rmp_when_no_resting(self):
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})           # no permanent .rmp
        rhp.apply("snes", "GameX")
        self.assertEqual(self._rmp(), {"input_player1_btn_a": "1"})
        rhp.restore()
        self.assertIsNone(self._rmp())                               # transient .rmp removed
        self.assertFalse(rhp._SIDECAR.is_file())

    def test_no_store_entry_noop(self):
        self.disk[("snes", "GameX", "TestCore")] = {"input_player1_btn_b": "9"}
        rhp.apply("snes", "GameX")
        self.assertEqual(self._rmp(), {"input_player1_btn_b": "9"})   # untouched
        self.assertFalse(rhp._SIDECAR.is_file())

    def test_docked_noop(self):
        rhp._handheld = lambda: False
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        rhp.apply("snes", "GameX")
        self.assertIsNone(self._rmp())
        self.assertFalse(rhp._SIDECAR.is_file())

    def test_no_core_noop(self):
        rhp.retroarch_cfg.launched_core = lambda s, st: None          # standalone / not an RA remap
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        rhp.apply("snes", "GameX")
        self.assertFalse(rhp._SIDECAR.is_file())

    def test_crash_orphan_self_heals(self):
        rhp.set_pergame(_TID, {"input_player1_btn_a": "1"})
        rhp.apply("snes", "GameX")                                    # crash: sidecar left present
        self.assertTrue(rhp._SIDECAR.is_file())
        rhp.apply("snes", "GameX")                                    # sweeps the orphan, then re-applies
        self.assertEqual(self._rmp(), {"input_player1_btn_a": "1"})
        rhp.restore()
        self.assertIsNone(self._rmp())                               # back to no .rmp (resting was empty)


if __name__ == "__main__":
    unittest.main()
