"""retroarch_cfg's launched-core resolver (Phase 5a: core-awareness base) —
which RetroArch core dir a per-game READ should prefer, given a per-game
<altemulator> override or the system's active default es_systems command.

Mirrors test_retroarch_pergame_cfg.py's temp-dir + monkeypatch style
(RA_CONFIG_BASE / SYSTEM_CORE_MAP); es_systems.load_systems/default_command
and es_gamelist.record are mocked so no real ES-DE config is read, and
_corename_cache is seeded directly instead of writing fake *_libretro.info
files (mirrors _corename's own cache-hit fast path).

Run:  python3 -m unittest tests.test_launched_core -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import es_gamelist, es_systems
from lib import retroarch_cfg as rcfg

SYS = "testsys"


class CommandForLabel(unittest.TestCase):
    def test_finds_matching_label(self):
        systems = {SYS: [("Label A", "cmd a"), ("Label B", "cmd b")]}
        self.assertEqual(rcfg._command_for_label(SYS, "Label B", systems), "cmd b")

    def test_returns_none_when_no_command_carries_that_label(self):
        systems = {SYS: [("Label A", "cmd a")]}
        self.assertIsNone(rcfg._command_for_label(SYS, "Nope", systems))

    def test_returns_none_for_a_system_with_no_commands(self):
        self.assertIsNone(rcfg._command_for_label(SYS, "Label A", {}))


class CoreNameFromCommand(unittest.TestCase):
    def setUp(self):
        self._saved_cache = dict(rcfg._corename_cache)

    def tearDown(self):
        rcfg._corename_cache.clear()
        rcfg._corename_cache.update(self._saved_cache)

    def test_standalone_command_returns_none(self):
        self.assertIsNone(rcfg._core_name_from_command("/path/to/SomeEmu.AppImage %ROM%"))

    def test_empty_or_missing_command_returns_none(self):
        self.assertIsNone(rcfg._core_name_from_command(""))
        self.assertIsNone(rcfg._core_name_from_command(None))

    def test_extracts_corename_via_the_libretro_so_token(self):
        rcfg._corename_cache["mesen"] = "Mesen"
        cmd = "%EMULATOR_RETROARCH% -L %COREPATH%/mesen_libretro.so %ROM%"
        self.assertEqual(rcfg._core_name_from_command(cmd), "Mesen")

    def test_skips_a_token_with_no_resolvable_corename(self):
        rcfg._corename_cache["missing"] = None
        rcfg._corename_cache["fallback"] = "Fallback Core"
        cmd = ("%EMULATOR_RETROARCH% -L %COREPATH%/missing_libretro.so "
              "-L %COREPATH%/fallback_libretro.so %ROM%")
        self.assertEqual(rcfg._core_name_from_command(cmd), "Fallback Core")


class LaunchedCore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="launched-core-test-"))
        self._saved = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        rcfg.SYSTEM_CORE_MAP = {}
        self._saved_cache = dict(rcfg._corename_cache)

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved
        rcfg._corename_cache.clear()
        rcfg._corename_cache.update(self._saved_cache)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkcore(self, name: str) -> None:
        (self.tmp / name).mkdir(parents=True, exist_ok=True)

    def test_altemulator_picks_that_labels_command_core(self):
        self._mkcore("Nestopia")
        self._mkcore("FCEUmm")
        rcfg._corename_cache["nestopia"] = "Nestopia"
        systems = {SYS: [
            ("Nestopia", "%EMULATOR_RETROARCH% -L %COREPATH%/nestopia_libretro.so %ROM%"),
            ("FCEUmm", "%EMULATOR_RETROARCH% -L %COREPATH%/fceumm_libretro.so %ROM%"),
        ]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": "Nestopia"}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command",
                              return_value=systems[SYS][1][1]):   # would pick FCEUmm if used
            self.assertEqual(rcfg.launched_core(SYS, "Some Rom"), "Nestopia")

    def test_no_altemulator_falls_back_to_the_system_default_command(self):
        self._mkcore("FCEUmm")
        rcfg._corename_cache["fceumm"] = "FCEUmm"
        systems = {SYS: [("FCEUmm", "%EMULATOR_RETROARCH% -L %COREPATH%/fceumm_libretro.so %ROM%")]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command", return_value=systems[SYS][0][1]):
            self.assertEqual(rcfg.launched_core(SYS, "Some Rom"), "FCEUmm")

    def test_standalone_system_returns_none(self):
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value={SYS: []}), \
             mock.patch.object(es_systems, "default_command",
                              return_value="/path/to/Dolphin.AppImage %ROM%"):
            self.assertIsNone(rcfg.launched_core(SYS, "Some Rom"))

    def test_no_resolvable_core_in_the_command_returns_none(self):
        systems = {SYS: [("Weird", "%EMULATOR_RETROARCH% %ROM%")]}   # no *_libretro.so token
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command", return_value=systems[SYS][0][1]):
            self.assertIsNone(rcfg.launched_core(SYS, "Some Rom"))

    # ── corename ≠ config-dir reconciliation ──
    def test_reconciles_dolphin_corename_to_its_config_dir(self):
        # The Dolphin libretro core's .info corename ("Dolphin") does not match
        # the actual per-game config dir RetroArch writes into ("dolphin_emu");
        # the curated SYSTEM_CORE_MAP entry is the reconciliation fallback.
        self._mkcore("dolphin_emu")
        rcfg.SYSTEM_CORE_MAP = {"gc": ["dolphin_emu"]}
        rcfg._corename_cache["dolphin"] = "Dolphin"
        systems = {"gc": [("Dolphin",
                          "%EMULATOR_RETROARCH% -L %COREPATH%/dolphin_libretro.so %ROM%")]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command", return_value=systems["gc"][0][1]):
            self.assertEqual(rcfg.launched_core("gc", "Some Game"), "dolphin_emu")

    def test_reconciles_mame2010_corename_to_its_config_dir(self):
        self._mkcore("MAME 2010")
        rcfg.SYSTEM_CORE_MAP = {"mame": ["MAME 2010"]}
        rcfg._corename_cache["mame2010"] = "MAME2010"   # corename != the "MAME 2010" dir
        systems = {"mame": [("MAME 2010",
                            "%EMULATOR_RETROARCH% -L %COREPATH%/mame2010_libretro.so %ROM%")]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command", return_value=systems["mame"][0][1]):
            self.assertEqual(rcfg.launched_core("mame", "Some Game"), "MAME 2010")

    def test_versioned_corename_via_altemulator_beats_map_index0(self):
        # Regression (review-caught, live: area51mx): <altemulator>MAME 2010</...>
        # -> corename "MAME 2010 (0.139)" whose dir is absent, but "MAME 2010"
        # exists. The fallback must strip the version and pick "MAME 2010", NOT
        # the arcade map's index-0 core "FinalBurn Neo" just because it exists.
        self._mkcore("FinalBurn Neo")
        self._mkcore("MAME 2010")
        rcfg.SYSTEM_CORE_MAP = {"arcade": ["FinalBurn Neo", "MAME", "MAME 2010", "FB Alpha 2012"]}
        rcfg._corename_cache["mame2010"] = "MAME 2010 (0.139)"
        systems = {"arcade": [
            ("FinalBurn Neo", "%EMULATOR_RETROARCH% -L %COREPATH%/fbneo_libretro.so %ROM%"),
            ("MAME 2010", "%EMULATOR_RETROARCH% -L %COREPATH%/mame2010_libretro.so %ROM%"),
        ]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": "MAME 2010"}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command",
                              return_value=systems["arcade"][0][1]):   # default would be FinalBurn Neo
            self.assertEqual(rcfg.launched_core("arcade", "area51mx"), "MAME 2010")

    def test_best_effort_returns_corename_when_no_map_dir_exists_either(self):
        rcfg.SYSTEM_CORE_MAP = {SYS: ["SomeOtherCore"]}   # not on disk either
        rcfg._corename_cache["oddcore"] = "OddCore"
        systems = {SYS: [("OddCore", "%EMULATOR_RETROARCH% -L %COREPATH%/oddcore_libretro.so %ROM%")]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems", return_value=systems), \
             mock.patch.object(es_systems, "default_command", return_value=systems[SYS][0][1]):
            self.assertEqual(rcfg.launched_core(SYS, "Some Game"), "OddCore")

    def test_accepts_a_preloaded_systems_dict_without_reloading(self):
        self._mkcore("FCEUmm")
        rcfg._corename_cache["fceumm"] = "FCEUmm"
        systems = {SYS: [("FCEUmm", "%EMULATOR_RETROARCH% -L %COREPATH%/fceumm_libretro.so %ROM%")]}
        with mock.patch.object(es_gamelist, "record", return_value={"altemulator": ""}), \
             mock.patch.object(es_systems, "load_systems",
                              side_effect=AssertionError("must not reload es_systems.xml")), \
             mock.patch.object(es_systems, "default_command", return_value=systems[SYS][0][1]):
            self.assertEqual(rcfg.launched_core(SYS, "Some Rom", systems), "FCEUmm")


class CoreDirsPreferCore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-dirs-prefer-test-"))
        self._saved = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        for name in ("Alpha", "Bravo", "Charlie"):
            (self.tmp / name).mkdir()
        rcfg.SYSTEM_CORE_MAP = {SYS: ["Alpha", "Bravo", "Charlie"]}

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_prefer_core_stays_alphabetical(self):
        self.assertEqual([d.name for d in rcfg.core_dirs_for_system(SYS)],
                         ["Alpha", "Bravo", "Charlie"])

    def test_prefer_core_moves_it_to_the_front_stable(self):
        self.assertEqual(
            [d.name for d in rcfg.core_dirs_for_system(SYS, prefer_core="Charlie")],
            ["Charlie", "Alpha", "Bravo"])

    def test_prefer_core_not_present_leaves_order_unchanged(self):
        self.assertEqual(
            [d.name for d in rcfg.core_dirs_for_system(SYS, prefer_core="Nope")],
            ["Alpha", "Bravo", "Charlie"])


if __name__ == "__main__":
    unittest.main()
