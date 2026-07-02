"""retroarch_rmp — RetroArch's NATIVE per-game input remap (.rmp) writer.

Byte-golden write->read round-trip, multi-core multi-write, a pre-existing
(foreign) .rmp backed up to a recoverable _TMP (never deleted) on the FIRST
managed write, and an empty mapping removing the file. Mirrors
tests/test_retroarch_pergame_cfg.py's temp-core-dir + monkeypatch style.

Run:  python3 -m unittest tests.test_retroarch_rmp -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import fsutil, retroarch_cfg as rcfg
from lib import retroarch_rmp as rmp

SYS = "testsys"


class RetroArchRmpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ra-rmp-test-"))
        self.core = self.tmp / "FakeCore"
        self.core.mkdir()
        self._saved_cfg = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore"]}
        # Redirect recoverable_delete's tmp_base into our own temp tree instead
        # of the real ~/Downloads/_TMP (same style as test_pcsx2x6.py's
        # fake_retire patch).
        self.tmp_base = self.tmp / "tmp_base"
        self._orig_recoverable_delete = fsutil.recoverable_delete

        def _redirected(*a, **kw):
            kw["tmp_base"] = self.tmp_base
            return self._orig_recoverable_delete(*a, **kw)

        fsutil.recoverable_delete = _redirected
        rmp.fsutil.recoverable_delete = _redirected

    def tearDown(self):
        fsutil.recoverable_delete = self._orig_recoverable_delete
        rmp.fsutil.recoverable_delete = self._orig_recoverable_delete
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved_cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _target(self, core="FakeCore", rom="Test Game (USA)") -> Path:
        return self.tmp / "remaps" / core / f"{rom}.rmp"

    # ── core-dir resolution re-roots under remaps/, not the config/<Core>/ tree ──
    def test_core_remap_dirs_reroot_under_remaps(self):
        dirs = rmp.core_remap_dirs_for_system(SYS)
        self.assertEqual(dirs, [self.tmp / "remaps" / "FakeCore"])

    def test_unknown_system_yields_no_dirs(self):
        self.assertEqual(rmp.core_remap_dirs_for_system("nope"), [])

    # ── write -> read round-trip (byte-golden) ──
    def test_write_read_round_trip(self):
        mapping = {"input_player1_btn_a": "0", "input_libretro_device_p1": "1",
                   "input_player1_analog_dpad_mode": "0"}
        written = rmp.set_game_remap(SYS, "Test Game (USA)", mapping)
        self.assertEqual(written, [self._target()])
        self.assertEqual(
            self._target().read_text(encoding="utf-8"),
            'input_libretro_device_p1 = "1"\n'
            'input_player1_analog_dpad_mode = "0"\n'
            'input_player1_btn_a = "0"\n')
        self.assertEqual(rmp.get_game_remap(SYS, "Test Game (USA)"), mapping)
        self.assertTrue(rmp.has_game_remap(SYS, "Test Game (USA)"))

    def test_missing_game_reads_empty(self):
        self.assertEqual(rmp.get_game_remap(SYS, "Nope"), {})
        self.assertFalse(rmp.has_game_remap(SYS, "Nope"))

    def test_rewrite_is_idempotent(self):
        mapping = {"input_player1_btn_a": "0"}
        rmp.set_game_remap(SYS, "Test Game (USA)", mapping)
        before = self._target().read_text(encoding="utf-8")
        rmp.set_game_remap(SYS, "Test Game (USA)", mapping)
        self.assertEqual(self._target().read_text(encoding="utf-8"), before)
        # No repeat backup dir on a second write to the same managed path.
        self.assertEqual(len(list(self.tmp_base.glob("_TMP_retroarch-rmp-*"))), 0)

    # ── multi-core system -> multi-write ──
    def test_multi_core_system_writes_every_core_dir(self):
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore", "FakeCore2"]}
        (self.tmp / "FakeCore2").mkdir()
        written = rmp.set_game_remap(SYS, "Multi Game", {"input_player1_btn_a": "8"})
        self.assertEqual(sorted(written),
                         sorted([self._target("FakeCore", "Multi Game"),
                                self._target("FakeCore2", "Multi Game")]))
        for p in written:
            self.assertEqual(p.read_text(encoding="utf-8"), 'input_player1_btn_a = "8"\n')

    # ── pre-existing (foreign) file is backed up, never clobbered ──
    def test_preexisting_file_moved_to_recoverable_tmp_on_first_write(self):
        target = self._target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('input_player1_btn_a = "1"\n', encoding="utf-8")

        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "0"})

        moved = list(self.tmp_base.glob("_TMP_retroarch-rmp-*/*.rmp"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_text(encoding="utf-8"), 'input_player1_btn_a = "1"\n')
        recovery = list(self.tmp_base.glob("_TMP_retroarch-rmp-*/RECOVERY.txt"))
        self.assertEqual(len(recovery), 1)
        # the NEW content is what's live now, not clobbered-then-lost
        self.assertEqual(target.read_text(encoding="utf-8"), 'input_player1_btn_a = "0"\n')

    def test_second_write_does_not_repeat_the_backup(self):
        target = self._target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('input_player1_btn_a = "1"\n', encoding="utf-8")
        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "0"})
        self.assertEqual(len(list(self.tmp_base.glob("_TMP_retroarch-rmp-*"))), 1)
        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "9"})
        # still just the one backup dir from the FIRST managed write
        self.assertEqual(len(list(self.tmp_base.glob("_TMP_retroarch-rmp-*"))), 1)

    # ── empty mapping removes the file ──
    def test_empty_mapping_removes_the_file(self):
        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "0"})
        self.assertTrue(self._target().exists())
        touched = rmp.set_game_remap(SYS, "Test Game (USA)", {})
        self.assertEqual(touched, [self._target()])
        self.assertFalse(self._target().exists())
        self.assertEqual(rmp.get_game_remap(SYS, "Test Game (USA)"), {})

    def test_empty_mapping_with_no_existing_file_touches_nothing(self):
        touched = rmp.set_game_remap(SYS, "Never Written", {})
        self.assertEqual(touched, [])
        self.assertFalse(self._target(rom="Never Written").exists())
        # no marker/backup dance for a game that was never managed
        self.assertFalse(list(self.tmp_base.glob("_TMP_retroarch-rmp-*")))

    def test_empty_mapping_drops_the_managed_marker_so_a_later_foreign_file_is_backed_up(self):
        # Adversarial review fix, applied inline: clearing a game's remap must
        # also drop its ".mad-managed" marker, so the path returns to fully
        # UNMANAGED -- not "MAD owns this path but there's currently nothing
        # here", which would silently skip the backup on a later foreign write.
        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "0"})
        marker = self._target().with_name(self._target().name + ".mad-managed")
        self.assertTrue(marker.exists())

        rmp.set_game_remap(SYS, "Test Game (USA)", {})
        self.assertFalse(self._target().exists())
        self.assertFalse(marker.exists())        # ownership marker also dropped

        # A foreign file reappears at the same path later (hand-restored, or
        # saved again from RetroArch's own Quick Menu). Since MAD no longer
        # "owns" this path, the NEXT managed write must back it up again, not
        # silently clobber it.
        self._target().parent.mkdir(parents=True, exist_ok=True)
        self._target().write_text('input_player1_btn_a = "9"\n', encoding="utf-8")
        rmp.set_game_remap(SYS, "Test Game (USA)", {"input_player1_btn_a": "1"})

        moved = list(self.tmp_base.glob("_TMP_retroarch-rmp-*/*.rmp"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_text(encoding="utf-8"), 'input_player1_btn_a = "9"\n')
        self.assertTrue(marker.exists())         # re-managed: marker is back


# ── Phase 5a: prefer_core (read-side only; set_game_remap has no such param) ──

class GetGameRemapPreferCore(unittest.TestCase):
    def setUp(self):
        self.tmp2 = Path(tempfile.mkdtemp(prefix="ra-rmp-prefer-test-"))
        self._saved_cfg2 = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp2
        (self.tmp2 / "CoreA").mkdir()
        (self.tmp2 / "CoreB").mkdir()
        rcfg.SYSTEM_CORE_MAP = {SYS: ["CoreA", "CoreB"]}

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved_cfg2
        shutil.rmtree(self.tmp2, ignore_errors=True)

    def _seed(self, core: str, rom: str, value: str) -> None:
        target = self.tmp2 / "remaps" / core / f"{rom}.rmp"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'input_player1_btn_a = "{value}"\n', encoding="utf-8")

    def test_no_prefer_core_reads_the_alphabetically_first_core(self):
        self._seed("CoreA", "Test Game (USA)", "0")
        self._seed("CoreB", "Test Game (USA)", "9")
        self.assertEqual(rmp.get_game_remap(SYS, "Test Game (USA)"),
                         {"input_player1_btn_a": "0"})

    def test_prefer_core_reads_the_preferred_core_instead(self):
        self._seed("CoreA", "Test Game (USA)", "0")
        self._seed("CoreB", "Test Game (USA)", "9")
        self.assertEqual(rmp.get_game_remap(SYS, "Test Game (USA)", prefer_core="CoreB"),
                         {"input_player1_btn_a": "9"})


if __name__ == "__main__":
    unittest.main()
