"""Tests for RPCS3 per-game input (P3): the rpcs3pgin editor (per-serial button/stick/trigger
map, buffered), rpcs3_games.path_to_serial (ROM->serial), and the switch_bind launch rail that
layers per-game binds over the global map (per-game wins) + reverts on exit.

Hermetic: the editor points at a temp store; the launch merge uses a fixture games.yml. Proves a
per-game remap never touches the global map and is applied only for the matching title."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._fakes import patch_sdl, sd

from lib import rpcs3_cfg, switch_bind
from lib.madsrv import rpc, rpcs3_games
from lib.madsrv import rpcs3_pergame_input_cmds as PGI
from lib.madsrv.rpc import RpcError

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


class Editor(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        PGI._STORE = self.d / "pergame-input.json"
        PGI._buf.reset()
        self._lo = rpcs3_cfg.load_overrides
        rpcs3_cfg.load_overrides = lambda: {}          # deterministic global source
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda name: None

    def tearDown(self):
        rpcs3_cfg.load_overrides = self._lo
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _store(self):
        return json.loads(PGI._STORE.read_text()) if PGI._STORE.exists() else {}

    def test_fresh_all_inherit(self):
        pay = PGI._input_get({"titleid": _S, "player": "1"})
        self.assertTrue(pay["buffered"])
        self.assertEqual([p["id"] for p in pay["players"]], ["1", "2", "3", "4"])
        # nothing stored yet -> a row shows the inherited default (not "—" unless truly unbound)
        self.assertFalse(pay["dirty"])
        self.assertTrue(any(b["id"] == "Cross" for g in pay["groups"] for b in g["binds"]))

    def test_capture_saves_token(self):
        r = PGI._input_set({"titleid": _S, "player": "1", "id": "Circle", "kind": "btn",
                            "value": "305"})       # BTN_EAST
        self.assertTrue(r["dirty"])
        PGI._input_set({"titleid": _S, "player": "2", "id": "Cross", "kind": "btn", "value": "304"})
        self.assertEqual(PGI._input_save({"titleid": _S})["saved"], True)
        self.assertEqual(self._store(), {_S: {"1": {"Circle": "East"}, "2": {"Cross": "South"}}})
        self.assertEqual(PGI.binds_for(_S), {"1": {"Circle": "East"}, "2": {"Cross": "South"}})

    def test_invalid_capture_raises(self):
        with self.assertRaises(RpcError):
            PGI._input_set({"titleid": _S, "player": "1", "id": "Circle", "kind": "btn",
                            "value": "999999"})    # not a mappable evdev button

    def test_clear_removes_only_that_bind(self):
        PGI._STORE.write_text(json.dumps({_S: {"1": {"Circle": "East", "Cross": "South"}}}))
        PGI._buf.reset()
        PGI._input_clear({"titleid": _S, "player": "1", "id": "Circle"})
        PGI._input_save({"titleid": _S})
        self.assertEqual(self._store(), {_S: {"1": {"Cross": "South"}}})

    def test_emptied_entry_pruned_and_no_running_guard(self):
        PGI._STORE.write_text(json.dumps({_S: {"1": {"Circle": "East"}}}))
        PGI._buf.reset()
        PGI._input_clear({"titleid": _S, "player": "1", "id": "Circle"})
        PGI._input_save({"titleid": _S})
        self.assertEqual(self._store(), {})            # whole serial dropped -> inherits global

    def test_cancel_discards(self):
        PGI._input_set({"titleid": _S, "player": "1", "id": "Circle", "kind": "btn", "value": "305"})
        PGI._input_cancel({"titleid": _S})
        self.assertFalse(PGI._input_save({"titleid": _S})["saved"])
        self.assertFalse(PGI._STORE.exists())

    def test_corrupt_store_backed_up(self):
        PGI._STORE.write_text("{ not json")
        self.assertEqual(PGI._load(), {})              # degrades to fresh
        self.assertTrue(list(self.d.glob("pergame-input.json.*.bad")))   # rule #5: preserved (hash-named)

    def test_bad_serial_rejected(self):
        with self.assertRaises(RpcError):
            PGI._input_get({"titleid": "../etc"})
        with self.assertRaises(RpcError):
            PGI._input_set({"titleid": "short", "id": "Circle", "kind": "btn", "value": "305"})
        self.assertEqual(PGI.binds_for("../etc"), {})

    def test_games_badge(self):
        rd, gy = rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML
        rom = self.d / "ps3"
        rom.mkdir()
        (self.d / "games.yml").write_text("BLES00590: /discs/DemonsSouls.iso\n", encoding="utf-8")
        (rom / "Demons Souls.desktop").write_text(
            '[Desktop Entry]\nExec=/apps/rpcs3.AppImage --no-gui "/discs/DemonsSouls.iso"\n', encoding="utf-8")
        rpcs3_games._ps3_rom_dir = lambda: rom
        rpcs3_games._GAMES_YML = self.d / "games.yml"
        try:
            PGI._STORE.write_text(json.dumps({_S: {"1": {"Circle": "East"}}}))
            out = PGI._games({})
            self.assertEqual(out["system"], "ps3")
            g = {x["titleid"]: x for x in out["games"]}
            self.assertTrue(g[_S]["override"])
            self.assertEqual(g[_S]["summary"], "Custom input")
        finally:
            rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML = rd, gy

    def test_garbage_token_dropped(self):
        # A hand-edited garbage token must never reach the launch path (it's dropped as invalid).
        PGI._STORE.write_text(json.dumps({_S: {"1": {"Circle": "GARBAGE", "Cross": "South"}}}))
        self.assertEqual(PGI.binds_for(_S), {"1": {"Cross": "South"}})

    def test_second_distinct_corruption_backed_up(self):
        PGI._STORE.write_text("{ corrupt one")
        PGI._load()
        PGI._STORE.write_text("{ corrupt TWO different")
        PGI._load()
        bads = list(self.d.glob("pergame-input.json.*.bad"))
        self.assertEqual(len(bads), 2)             # rule #5: each distinct corruption preserved

    def test_rpc_methods_registered(self):
        for verb in ("input_get", "input_set", "input_clear", "input_save", "input_cancel", "games"):
            self.assertIn(f"rpcs3pgin.{verb}", rpc._METHODS)


class LaunchRail(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        PGI._STORE = self.d / "pergame-input.json"
        self.y = self.d / "games.yml"
        self._gy = rpcs3_games._GAMES_YML
        rpcs3_games._GAMES_YML = self.y
        self._lo = rpcs3_cfg.load_overrides
        rpcs3_cfg.load_overrides = lambda: {1: {"Triangle": "North"}}   # a global override

    def tearDown(self):
        rpcs3_games._GAMES_YML = self._gy
        rpcs3_cfg.load_overrides = self._lo
        shutil.rmtree(self.d, ignore_errors=True)

    def test_pergame_binds_absent_store_is_empty(self):
        self.assertEqual(switch_bind._rpcs3_pergame_binds("/roms/ps3/x.iso"), {})   # no store file

    def test_launch_overrides_merges_pergame_over_global(self):
        self.y.write_text(f"{_S}: /roms/ps3/Demons Souls.iso\n", encoding="utf-8")
        PGI._STORE.write_text(json.dumps({_S: {"1": {"Cross": "West"}}}))
        merged = switch_bind._rpcs3_launch_overrides("/roms/ps3/Demons Souls.iso")
        self.assertEqual(merged[1]["Cross"], "West")          # per-game applied
        self.assertEqual(merged[1]["Triangle"], "North")      # global preserved
        self.assertTrue(all(isinstance(k, int) for k in merged))
        # a different rom -> per-game NOT applied, only global
        other = switch_bind._rpcs3_launch_overrides("/roms/ps3/Other.iso")
        self.assertNotIn("Cross", other.get(1, {}))
        self.assertEqual(other[1]["Triangle"], "North")

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


class Transient(unittest.TestCase):
    """A per-game override is applied at launch and REVERTED on exit; an ORPHANED sidecar (a prior
    game's game-end restore didn't run) is reverted before the next game binds, so a per-game remap
    never leaks across games."""

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
        self.assertEqual(self._cross(), "West")             # per-game override applied at launch
        switch_bind.restore_target(self.yml)                # game-end restore
        self.assertEqual(self._cross(), "South")            # reverted to resting
        self.assertFalse(switch_bind._sidecar(self.yml).exists())

    def test_orphaned_sidecar_reverted_no_cross_game_leak(self):
        # Game A remapped Default.yml then crashed (restore never ran): stale sidecar + dirty config.
        self._snapshot_sidecar()
        self._apply_override()
        self.assertEqual(self._cross(), "West")
        # Game B launches: bind()'s orphan guard reverts the stale sidecar BEFORE re-binding, so B
        # starts from resting (not A's West). restore_target is exactly what that guard calls.
        switch_bind.restore_target(self.yml)
        self.assertEqual(self._cross(), "South")
        # a fresh snapshot now records the clean resting state for B's own exit
        self.assertEqual(switch_bind._snapshot("rpcs3", self.yml)["Player 1 Input"]["Config"]["Cross"],
                         "South")


if __name__ == "__main__":
    unittest.main()
