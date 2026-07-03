"""ryujinx.* PER-GAME inherit layer: Ryujinx has no per-key inherit (its games/<tid>/Config.json is
a COMPLETE file; an absent key resets to a default, not global -- source-verified), so MAD keeps a
sidecar pin-map and REGENERATES the complete file = existing/global base with the pinned keys
overridden and every other managed key refreshed from LIVE global. Verifies: complete file, pin
tracking, global-refresh of un-pinned keys, input_config preservation, inherit-clear, and one-time
migration of a legacy full-clone. Run: python3 -m unittest tests.test_ryujinx_pergame -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import ryujinx_cmds as R
from lib.madsrv import ryujinx_json

_TID = "0100abcd0000f000"
_GLOBAL = {
    "version": 70, "graphics_backend": "Vulkan", "res_scale": 1, "aspect_ratio": "Fixed16x9",
    "anti_aliasing": "None", "scaling_filter": "Bilinear", "max_anisotropy": -1,
    "enable_texture_recompression": False, "enable_vsync": True, "backend_threading": "Auto",
    "docked_mode": True, "enable_ptc": True, "enable_shader_cache": True,
    "memory_manager_mode": "HostMappedUnsafe", "enable_macro_hle": True,
    "input_config": [{"player": 1}], "some_global_only": "x",
}


class RyujinxPerGame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._cfg, self._games = ryujinx_json.CONFIG, R._GAMES_DIR
        ryujinx_json.CONFIG = self.d / "Config.json"
        R._GAMES_DIR = self.d / "games"
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        self._write_global(_GLOBAL)

    def tearDown(self):
        ryujinx_json.CONFIG, R._GAMES_DIR = self._cfg, self._games
        proc_guard.emulator_running = self._run
        shutil.rmtree(self.d, ignore_errors=True)

    # helpers
    def _write_global(self, data):
        ryujinx_json.CONFIG.write_text(json.dumps(data, indent=2))

    def _set(self, key, value, tid=_TID):
        return R._pergame_set(cfgutil.item_by_key(R.GROUPS, key), {"titleid": tid, "key": key, "value": value})

    def _file(self, tid=_TID):
        return json.loads(R._pergame_path(tid).read_text())

    def _pins(self, tid=_TID):
        p = R._pins_path(tid)
        return json.loads(p.read_text()) if p.is_file() else {}

    def _row(self, key, tid=_TID):
        g = R._pergame_get(tid)
        return [r for grp in g["groups"] for r in grp["settings"] if r["key"] == key][0]

    # tests
    def test_no_override_all_inherit_no_file(self):
        g = R._pergame_get(_TID)
        self.assertTrue(g["exists"])
        self.assertTrue(self._row("res_scale")["inherited"])
        self.assertEqual(self._row("aspect_ratio")["value"], 0)         # 0 == Inherit global
        self.assertFalse(R._pergame_path(_TID).is_file())               # GET never creates a file

    def test_set_writes_complete_file_and_pin(self):
        self._set("res_scale", 3)
        f = self._file()
        self.assertEqual(self._pins(), {"res_scale": 3})
        self.assertEqual(f["res_scale"], 3)
        self.assertEqual(len(f), len(_GLOBAL))                          # COMPLETE (every key present)
        self.assertEqual(f["version"], 70)                             # version carried -> no migration
        self.assertEqual(f["input_config"], [{"player": 1}])           # non-managed preserved
        self.assertEqual(f["some_global_only"], "x")

    def test_enum_uses_one_based_index(self):
        self._set("aspect_ratio", 1)                                   # option[0] was Inherit -> idx 0 -> Fixed4x3
        self.assertEqual(self._file()["aspect_ratio"], "Fixed4x3")
        self.assertEqual(self._row("aspect_ratio")["value"], 1)

    def test_unpinned_keys_track_live_global_on_regen(self):
        self._set("res_scale", 3)
        self._write_global({**_GLOBAL, "enable_vsync": False})         # a global change to an un-pinned key
        self._set("res_scale", 3)                                      # any set triggers regen
        f = self._file()
        self.assertFalse(f["enable_vsync"])                            # refreshed from global
        self.assertEqual(f["res_scale"], 3)                           # pinned key stays

    def test_pergame_input_config_preserved(self):
        self._set("res_scale", 3)
        f = self._file(); f["input_config"] = [{"player": 2}]
        R._pergame_path(_TID).write_text(json.dumps(f))
        self._set("docked_mode", 1)                                    # Off
        self.assertEqual(self._file()["input_config"], [{"player": 2}])

    def test_inherit_clear_with_remaining_pins_refreshes(self):
        self._set("res_scale", 3)
        self._set("docked_mode", 1)                                    # a 2nd pin so the file survives
        self._write_global({**_GLOBAL, "res_scale": 2})
        self._set("res_scale", "inherit")                             # clear one of two
        self.assertNotIn("res_scale", self._pins())
        self.assertIn("docked_mode", self._pins())                   # other pin remains
        self.assertEqual(self._file()["res_scale"], 2)               # cleared key back to global

    def test_legacy_clone_migrated_once(self):
        tid = "0100abcd0000f001"
        pg = R._pergame_path(tid); pg.parent.mkdir(parents=True, exist_ok=True)
        pg.write_text(json.dumps({**_GLOBAL, "res_scale": 4}))         # a clone, no sidecar
        self.assertEqual(R._ensure_pins(tid), {"res_scale": 4})        # inferred from the diff vs global
        self.assertTrue(R._pins_path(tid).is_file())

    def test_games_badge_and_summary_from_pins(self):
        self._set("res_scale", 3)
        self.assertTrue(bool(R._ensure_pins(_TID)))
        self.assertEqual(R._summary(_TID), "Custom: 1 setting")
        self._set("docked_mode", 1)
        self.assertEqual(R._summary(_TID), "Custom: 2 settings")

    def test_missing_global_refuses_set_never_writes_partial(self):
        from lib.madsrv.rpc import RpcError
        ryujinx_json.CONFIG.unlink()                                  # global gone
        with self.assertRaises(RpcError):
            self._set("res_scale", 3)                                 # cannot build a complete file
        self.assertFalse(R._pergame_path(_TID).is_file())            # no partial/corrupt file written
        self.assertFalse(R._pins_path(_TID).is_file())

    def test_clear_all_overrides_deletes_file_for_clean_inherit(self):
        self._set("res_scale", 3)
        self.assertTrue(R._pergame_path(_TID).is_file())
        self._set("res_scale", "inherit")                            # last override cleared
        self.assertFalse(R._pergame_path(_TID).is_file())           # file removed -> switch_bind uses global
        self.assertFalse(R._pins_path(_TID).is_file())              # sidecar removed

    def test_no_over_migration_when_global_missing(self):
        tid = "0100abcd0000f002"
        pg = R._pergame_path(tid); pg.parent.mkdir(parents=True, exist_ok=True)
        pg.write_text(json.dumps({**_GLOBAL, "res_scale": 4}))       # a legacy clone, no sidecar
        ryujinx_json.CONFIG.unlink()                                 # global gone
        self.assertEqual(R._ensure_pins(tid), {})                   # do NOT pin every key vs empty global
        self.assertFalse(R._pins_path(tid).is_file())

    def test_global_bool_apply_key_parses_string(self):
        from lib.madsrv.cfgutil import item_by_key
        data = dict(_GLOBAL)
        item = item_by_key(R.GROUPS, "enable_vsync")
        R._apply_key(data, item, "0")                               # the C++ sends bool as the string "0"
        self.assertFalse(data["enable_vsync"])                      # can actually be turned Off now
        R._apply_key(data, item, "1")
        self.assertTrue(data["enable_vsync"])

    def test_launch_refresh_tracks_global(self):
        self._set("res_scale", 3)                                   # pin res_scale
        self._write_global({**_GLOBAL, "enable_vsync": False})      # global change, game NOT re-edited
        R.refresh_pergame(_TID)                                     # switch_bind launch hook
        f = self._file()
        self.assertFalse(f["enable_vsync"])                         # un-pinned refreshed from live global
        self.assertEqual(f["res_scale"], 3)                        # pinned override kept


if __name__ == "__main__":
    unittest.main()
