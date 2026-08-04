"""Tests for RPCS3 per-game input: rpcs3_games.path_to_serial (ROM->serial), the per-game
store read the launch rail does (load_entry -> profile picks; legacy binds inert), and the
transient apply/revert + orphan-sidecar guard on the launch target.

Hermetic: temp store + fixture games.yml. The per-button editor died with the input-profile
migration — its store-shape coverage lives in tests/test_rpcs3_handheld_input.py (StoreShape)
and the picker pages in tests/test_rpcs3_profile_cmds.py."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._fakes import patch_sdl, sd

from lib import rpcs3_cfg, rpcs3_profiles, switch_bind
from lib.madsrv import rpcs3_games
from lib.madsrv import rpcs3_pergame_input_cmds as PGI

_S = "BLES00590"
DS5 = "054c:0ce6"


class ReverseMap(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.y = self.d / "games.yml"
        self._orig = rpcs3_games._GAMES_YML
        rpcs3_games._GAMES_YML = self.y

    def tearDown(self):
        rpcs3_games._GAMES_YML = self._orig
        shutil.rmtree(self.d, ignore_errors=True)

    def test_exact_and_basename_and_unmatched(self):
        self.y.write_text(
            "BCES00002: /roms/ps3/Genji (Europe).iso\n"
            "BLES01291: /roms/ps3/Spider [BLES01291]/\n"
            "notaserial: /roms/ps3/x.iso\n", encoding="utf-8")
        self.assertEqual(rpcs3_games.path_to_serial("/roms/ps3/Genji (Europe).iso"), "BCES00002")
        # basename fallback (different dir, same file name)
        self.assertEqual(rpcs3_games.path_to_serial("/other/Genji (Europe).iso"), "BCES00002")
        self.assertEqual(rpcs3_games.path_to_serial("/roms/ps3/Spider [BLES01291]/"), "BLES01291")
        self.assertIsNone(rpcs3_games.path_to_serial("/roms/ps3/Unknown.iso"))
        self.assertIsNone(rpcs3_games.path_to_serial(""))

    def test_desktop_shortcut_iso(self):
        # ES-DE ps3 passes a .desktop; its Exec= holds the disc path (iso -> exact match).
        self.y.write_text("BCES00002: /roms/ps3/Genji (Europe).iso\n", encoding="utf-8")
        dt = self.d / "Genji.desktop"
        dt.write_text('[Desktop Entry]\nType=Application\n'
                      'Exec=/apps/rpcs3.AppImage --no-gui "/roms/ps3/Genji (Europe).iso"\n',
                      encoding="utf-8")
        self.assertEqual(rpcs3_games.path_to_serial(str(dt)), "BCES00002")

    def test_desktop_shortcut_eboot_dir_prefix(self):
        # A dir game: Exec points at .../[SERIAL]/PS3_GAME/USRDIR/EBOOT.BIN; games.yml has the dir.
        self.y.write_text("BLES01291: /roms/ps3/Spider [BLES01291]/\n", encoding="utf-8")
        dt = self.d / "Spider.desktop"
        dt.write_text('Exec=/apps/rpcs3.AppImage --no-gui '
                      '"/roms/ps3/Spider [BLES01291]/PS3_GAME/USRDIR/EBOOT.BIN"\n', encoding="utf-8")
        self.assertEqual(rpcs3_games.path_to_serial(str(dt)), "BLES01291")

    def test_ambiguous_basename_returns_none(self):
        # Two serials share a basename; a path that only basename-matches must NOT guess.
        self.y.write_text("BCES00002: /a/Game.iso\nBLES00590: /b/Game.iso\n", encoding="utf-8")
        self.assertIsNone(rpcs3_games.path_to_serial("/c/Game.iso"))


class LaunchRail(unittest.TestCase):
    """switch_bind._rpcs3_pergame + rpcs3_profiles.resolve — the per-game read the rpcs3
    bind branch does. Legacy binds are inert; only profile picks steer the launch."""

    ROM = "/roms/ps3/Demons Souls.iso"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st, PGI._STORE = PGI._STORE, self.d / "pergame-input.json"
        self.y = self.d / "games.yml"
        self._gy, rpcs3_games._GAMES_YML = rpcs3_games._GAMES_YML, self.y
        self.y.write_text(f"{_S}: {self.ROM}\n", encoding="utf-8")

    def tearDown(self):
        PGI._STORE = self._st
        rpcs3_games._GAMES_YML = self._gy
        shutil.rmtree(self.d, ignore_errors=True)

    def test_absent_store_is_none(self):
        self.assertIsNone(switch_bind._rpcs3_pergame(self.ROM))     # no store file, no parse

    def test_profile_pick_read_for_the_matching_title_only(self):
        PGI._STORE.write_text(json.dumps({_S: {"profiles": {"docked": "RaceWheel"}}}))
        entry = switch_bind._rpcs3_pergame(self.ROM)
        self.assertEqual(rpcs3_profiles.resolve(entry, {}, "docked"), "RaceWheel")
        self.assertIsNone(switch_bind._rpcs3_pergame("/roms/ps3/Other.iso"))

    def test_legacy_binds_only_entry_is_inert(self):
        # Old per-button shape: preserved by the store, ignored by the rail (no profile,
        # no override reaches assign_devices from it).
        PGI._STORE.write_text(json.dumps({_S: {"docked": {"1": {"Cross": "West"}}}}))
        entry = switch_bind._rpcs3_pergame(self.ROM)
        self.assertEqual(entry, {"binds": {"docked": {"1": {"Cross": "West"}}}})
        self.assertIsNone(rpcs3_profiles.resolve(entry, {}, "docked"))

    def test_assign_devices_honors_overrides(self):
        yml = self.d / "Default.yml"
        yml.write_text("Player 1 Input:\n  Handler: SDL\n  Device: 'old'\n"
                       "  Config:\n    Cross: South\n  Buddy Device: 'Null'\n", encoding="utf-8")
        pad = sd(0, DS5, "g", "DualSense")
        with patch_sdl([pad]):
            rpcs3_cfg.assign_devices([pad], config_path=str(yml), manage=2,
                                     overrides={1: {"Cross": "West"}})
        data = rpcs3_cfg.yaml.safe_load(yml.read_text(encoding="utf-8"))
        self.assertEqual(data["Player 1 Input"]["Config"]["Cross"], "West")   # override layered in


class Store(unittest.TestCase):
    """The corrupt-store backup path (rule #5) — load-bearing for the picker pages, which
    RE-SAVE the store: a corruption that failed to back up would be atomically overwritten.
    (Relocated from the retired Editor suite.)"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self._st, PGI._STORE = PGI._STORE, self.d / "pergame-input.json"
        self.addCleanup(setattr, PGI, "_STORE", self._st)

    def _bads(self):
        return sorted(self.d.glob("pergame-input.json.*.bad"))

    def test_corrupt_store_backed_up(self):
        PGI._STORE.write_text("{ not json", encoding="utf-8")
        self.assertEqual(PGI._load(), {})
        self.assertEqual(len(self._bads()), 1)

    def test_second_distinct_corruption_backed_up(self):
        PGI._STORE.write_text("{ not json", encoding="utf-8")
        PGI._load()
        PGI._load()                                    # same corruption -> no second copy
        self.assertEqual(len(self._bads()), 1)
        PGI._STORE.write_text("[ different garbage", encoding="utf-8")
        PGI._load()
        self.assertEqual(len(self._bads()), 2)         # each DISTINCT corruption preserved


class Transient(unittest.TestCase):
    """A launch-time write is applied then REVERTED on exit; an ORPHANED sidecar (a prior
    game's game-end restore didn't run) is reverted before the next game binds, so nothing
    leaks across games."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.yml = self.d / "Default.yml"
        self.yml.write_text(
            "Player 1 Input:\n  Handler: SDL\n  Device: 'DualSense 1'\n"
            "  Config:\n    Cross: South\n  Buddy Device: 'Null'\n"
            "Miscellaneous:\n  Pad handling sleep: 1000\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _cross(self):
        return rpcs3_cfg.yaml.safe_load(self.yml.read_text())["Player 1 Input"]["Config"]["Cross"]

    def _snapshot_sidecar(self):
        snap = switch_bind._snapshot("rpcs3", self.yml)     # resting: Cross=South
        switch_bind._sidecar(self.yml).write_text(
            json.dumps({"emu": "rpcs3", "input": snap}), encoding="utf-8")

    def _apply_override(self):
        pad = sd(0, DS5, "g", "DualSense")
        with patch_sdl([pad]):
            rpcs3_cfg.assign_devices([pad], config_path=str(self.yml), manage=2,
                                     overrides={1: {"Cross": "West"}})

    def test_override_applied_then_reverted(self):
        self._snapshot_sidecar()
        self._apply_override()
        self.assertEqual(self._cross(), "West")             # applied at launch
        switch_bind.restore_target(self.yml)                # game-end restore
        self.assertEqual(self._cross(), "South")            # reverted to resting
        self.assertFalse(switch_bind._sidecar(self.yml).exists())

    def test_orphaned_sidecar_reverted_no_cross_game_leak(self):
        # Game A remapped the target then crashed (restore never ran): stale sidecar + dirty
        # config. Game B's bind() orphan sweep reverts it BEFORE re-binding.
        self._snapshot_sidecar()
        self._apply_override()
        self.assertEqual(self._cross(), "West")
        switch_bind.restore_target(self.yml)
        self.assertEqual(self._cross(), "South")
        # a fresh snapshot now records the clean resting state for B's own exit
        self.assertEqual(switch_bind._snapshot("rpcs3", self.yml)["Player 1 Input"]["Config"]["Cross"],
                         "South")


if __name__ == "__main__":
    unittest.main()
