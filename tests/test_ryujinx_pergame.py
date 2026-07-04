"""ryujinx.* PER-GAME settings, DIRECT read/write model. Ryujinx has no per-key inherit (a
games/<tid>/Config.json is a COMPLETE file that replaces global), so MAD edits the game's real
Config.json IN PLACE -- exactly like Ryujinx's own per-game config. A per-game file is an
independent FROZEN snapshot (it does NOT track later global changes), which gives full interop:
values set in Ryujinx are read + preserved. Override-vs-inherit is a live diff of the file against
global; there is no pin-map / regen. Verifies: direct write, complete file, diff-based override view,
frozen snapshot, inherit copies current global, pure-clone delete, completeness top-up, and the
house-rule-#5 protections (never delete/clobber Ryujinx-authored content, a per-game input_config, or
an unparseable file). Run: python3 -m unittest tests.test_ryujinx_pergame -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard, staterev
from lib.madsrv import cfgutil
from lib.madsrv import ryujinx_cmds as R
from lib.madsrv import ryujinx_json
from lib.madsrv.rpc import RpcError

_TID = "0100abcd0000f000"
_GLOBAL = {
    "version": 70, "graphics_backend": "Vulkan", "res_scale": 1, "aspect_ratio": "Fixed16x9",
    "anti_aliasing": "None", "scaling_filter": "Bilinear", "max_anisotropy": -1,
    "enable_texture_recompression": False, "enable_vsync": True, "backend_threading": "Auto",
    "docked_mode": True, "enable_ptc": True, "enable_shader_cache": True,
    "memory_manager_mode": "HostMappedUnsafe", "enable_macro_hle": True, "audio_backend": "SDL3",
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
        self._bump = staterev.bump
        staterev.bump = lambda n: None
        self._write_global(_GLOBAL)

    def tearDown(self):
        ryujinx_json.CONFIG, R._GAMES_DIR = self._cfg, self._games
        proc_guard.emulator_running = self._run
        staterev.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    # helpers
    def _write_global(self, data):
        ryujinx_json.CONFIG.write_text(json.dumps(data, indent=2))

    def _write_pergame(self, data, tid=_TID):
        pg = R._pergame_path(tid)
        pg.parent.mkdir(parents=True, exist_ok=True)
        pg.write_text(json.dumps(data, indent=2))

    def _set(self, key, value, tid=_TID):
        return R._pergame_set(cfgutil.item_by_key(R.GROUPS, key), {"titleid": tid, "key": key, "value": value})

    def _file(self, tid=_TID):
        return json.loads(R._pergame_path(tid).read_text())

    def _row(self, key, tid=_TID):
        g = R._pergame_get(tid)
        return [r for grp in g["groups"] for r in grp["settings"] if r["key"] == key][0]

    # ── view (get) ───────────────────────────────────────────────────────────
    def test_no_override_all_inherit_no_file(self):
        g = R._pergame_get(_TID)
        self.assertTrue(g["exists"])
        self.assertTrue(self._row("res_scale")["inherited"])
        self.assertEqual(self._row("aspect_ratio")["value"], 0)         # 0 == Inherit global
        self.assertFalse(R._pergame_path(_TID).is_file())               # GET never creates a file

    def test_ryujinx_authored_file_shows_overrides(self):
        # A file authored in Ryujinx's OWN GUI (no MAD sidecar) is read DIRECTLY: keys that differ
        # from global show as overrides immediately -- this is the interop the model exists for.
        self._write_pergame({**_GLOBAL, "res_scale": 3, "audio_backend": "OpenAl"})
        self.assertFalse(self._row("res_scale")["inherited"])           # override, not inherit
        self.assertEqual(self._row("res_scale")["value"], 3)
        self.assertGreater(self._row("audio_backend")["value"], 0)      # OpenAl is an override, not inherit
        self.assertEqual(R._override_count(_TID), 2)

    # ── write (set) ──────────────────────────────────────────────────────────
    def test_set_writes_complete_file_directly(self):
        self._set("res_scale", 3)
        f = self._file()
        self.assertEqual(f["res_scale"], 3)
        self.assertEqual(len(f), len(_GLOBAL))                          # COMPLETE (a full global clone)
        self.assertEqual(f["version"], 70)
        self.assertEqual(f["input_config"], [{"player": 1}])           # non-managed carried from global
        self.assertEqual(f["some_global_only"], "x")

    def test_enum_uses_one_based_index(self):
        self._set("aspect_ratio", 1)                                   # option[0] was Inherit -> idx 0 -> Fixed4x3
        self.assertEqual(self._file()["aspect_ratio"], "Fixed4x3")
        self.assertEqual(self._row("aspect_ratio")["value"], 1)

    def test_pergame_stored_int_enum_round_trips_integer(self):
        self._write_global({**_GLOBAL, "vsync_mode": 0})
        self._set("vsync_mode", 2)                                     # Inherit=0, On=1, Off=2 -> stored_int 1
        self.assertEqual(self._file()["vsync_mode"], 1)
        self.assertIsInstance(self._file()["vsync_mode"], int)

    def test_incomplete_old_file_topped_up_on_set(self):
        # An old-schema per-game file missing keys must be completed from global on a MAD write, else
        # Ryujinx would reset the absent keys to compiled defaults.
        self._write_pergame({"res_scale": 2})                          # only ONE key present
        self._set("docked_mode", 1)                                    # Off
        f = self._file()
        self.assertIn("version", f)                                    # topped up -> complete
        self.assertEqual(f["graphics_backend"], "Vulkan")             # from global
        self.assertEqual(f["res_scale"], 2)                          # user value preserved (setdefault)
        self.assertFalse(f["docked_mode"])                           # the edit applied

    # ── the interop bug this rewrite fixes ─────────────────────────────────────
    def test_direct_write_preserves_ryujinx_edits(self):
        # THE regression: editing one setting in MAD must NOT reset OTHER values the user set in
        # Ryujinx -- neither an unpinned managed key (audio_backend) nor a non-managed one.
        self._write_pergame({**_GLOBAL, "res_scale": 3, "audio_backend": "OpenAl",
                             "system_time_offset": 999})
        self._set("docked_mode", 1)                                    # edit a DIFFERENT key
        f = self._file()
        self.assertEqual(f["audio_backend"], "OpenAl")                # unpinned managed override kept
        self.assertEqual(f["system_time_offset"], 999)               # non-managed Ryujinx key kept
        self.assertEqual(f["res_scale"], 3)
        self.assertFalse(f["docked_mode"])                           # the edit applied

    def test_frozen_snapshot_does_not_track_global(self):
        # A per-game file does NOT track later global changes (Ryujinx's own native behavior).
        self._set("res_scale", 3)                                     # clones global (enable_vsync True)
        self._write_global({**_GLOBAL, "enable_vsync": False})        # global changes an un-edited key
        self._set("docked_mode", 1)                                   # edit a different key
        self.assertTrue(self._file()["enable_vsync"])                # FROZEN: still True, not global's False

    # ── inherit (copy current global) ──────────────────────────────────────────
    def test_inherit_copies_current_global_frozen(self):
        self._set("res_scale", 3)                                     # keep the file alive
        self._set("aspect_ratio", 1)                                  # Fixed4x3 override
        self._write_global({**_GLOBAL, "aspect_ratio": "Fixed21x9"})  # global moves on
        self._set("aspect_ratio", "inherit")
        self.assertEqual(self._file()["aspect_ratio"], "Fixed21x9")  # copied the CURRENT global value
        self.assertEqual(self._file()["res_scale"], 3)

    def test_inherit_one_of_several_keeps_file(self):
        self._set("res_scale", 3)
        self._set("docked_mode", 1)                                   # a 2nd override
        self._set("res_scale", "inherit")                            # clear ONE of two
        self.assertEqual(self._file()["res_scale"], _GLOBAL["res_scale"])   # back to global
        self.assertFalse(self._file()["docked_mode"])               # other override remains
        self.assertEqual(R._override_count(_TID), 1)

    def test_inherit_on_last_override_deletes_pure_clone(self):
        self._set("res_scale", 3)
        self.assertTrue(R._pergame_path(_TID).is_file())
        self._set("res_scale", "inherit")                            # last override cleared
        self.assertFalse(R._pergame_path(_TID).is_file())           # pure clone removed -> clean inherit

    def test_inherit_no_file_is_noop(self):
        self._set("res_scale", "inherit")                           # no file present
        self.assertFalse(R._pergame_path(_TID).is_file())          # never creates a file

    def test_missing_global_refuses_set_never_writes_partial(self):
        ryujinx_json.CONFIG.unlink()                               # global gone
        with self.assertRaises(RpcError):
            self._set("res_scale", 3)                              # cannot build a complete file
        self.assertFalse(R._pergame_path(_TID).is_file())         # no partial/corrupt file written

    # ── house rule #5: never delete/clobber non-MAD content ────────────────────
    def test_pergame_input_config_preserved(self):
        self._set("res_scale", 3)
        f = self._file(); f["input_config"] = [{"player": 2}]
        R._pergame_path(_TID).write_text(json.dumps(f))
        self._set("docked_mode", 1)
        self.assertEqual(self._file()["input_config"], [{"player": 2}])

    def test_clear_keeps_file_with_divergent_input(self):
        # Clearing the last MAD override must NOT delete a file that holds a genuine per-game
        # input_config -- it is kept (and the cleared setting reverts to global).
        self._set("res_scale", 3)
        f = self._file(); f["input_config"] = [{"player": 9}]        # divergent from global [{player:1}]
        R._pergame_path(_TID).write_text(json.dumps(f))
        self._set("res_scale", "inherit")                           # clear the ONLY managed override
        self.assertTrue(R._pergame_path(_TID).is_file())           # file KEPT
        self.assertEqual(self._file()["input_config"], [{"player": 9}])   # input preserved

    def test_clear_keeps_user_file_with_nonmanaged_override(self):
        # A file authored in Ryujinx that overrides only a key MAD does NOT manage must never be
        # deleted (or needlessly rewritten) by clearing a MAD row.
        tid = "0100abcd0000f004"
        self._write_pergame({**_GLOBAL, "system_time_offset": 12345}, tid)
        self._set("res_scale", "inherit", tid=tid)                 # an inherit no-op must not delete it
        self.assertTrue(R._pergame_path(tid).is_file())            # file KEPT
        self.assertEqual(self._file(tid)["system_time_offset"], 12345)   # user override preserved

    def test_clear_refuses_on_unparseable_user_file(self):
        # An unparseable (hand-edited/corrupt) per-game file must never be deleted or overwritten: an
        # inherit-clear is REFUSED and the file is left byte-for-byte untouched.
        tid = "0100abcd0000f005"
        pg = R._pergame_path(tid)
        pg.parent.mkdir(parents=True, exist_ok=True)
        pg.write_text("{ not valid json")
        with self.assertRaises(RpcError):
            self._set("res_scale", "inherit", tid=tid)
        self.assertEqual(pg.read_text(), "{ not valid json")       # left byte-for-byte untouched

    def test_set_refuses_on_unparseable_file_no_clobber(self):
        # HOUSE RULE #5 (review-caught): a CONCRETE set on an unparseable per-game file must NOT
        # overwrite it with a bland global clone (ensure_bak may skip a .bak when a .router-backup
        # exists, so the clobber would be unrecoverable). It is refused; authored content survives.
        tid = "0100abcd0000f006"
        pg = R._pergame_path(tid)
        pg.parent.mkdir(parents=True, exist_ok=True)
        corrupt = '{ "res_scale": 4, "user_secret": 123, }'        # trailing comma -> invalid JSON
        pg.write_text(corrupt)
        with self.assertRaises(RpcError):
            self._set("docked_mode", 1, tid=tid)                   # a concrete override
        self.assertEqual(pg.read_text(), corrupt)                  # NOT clobbered -> content preserved

    # ── badge / summary / slicing / global-bool ────────────────────────────────
    def test_games_badge_and_summary_from_diff(self):
        self._set("res_scale", 3)
        self.assertEqual(R._override_count(_TID), 1)
        self.assertEqual(R._summary(_TID), "Custom: 1 setting")
        self._set("docked_mode", 1)
        self.assertEqual(R._summary(_TID), "Custom: 2 settings")

    def test_granular_pergame_page_renders_only_its_slice(self):
        from lib.madsrv import ryujinx_pergame as rp
        got = R._pergame_get(_TID, rp._page_groups("ryujinx_audio"))   # the Audio page slice
        keys = [s["key"] for grp in got["groups"] for s in grp["settings"]]
        self.assertIn("audio_backend", keys)
        self.assertNotIn("res_scale", keys)                        # a gfx key is NOT on the audio page

    def test_global_bool_apply_key_parses_string(self):
        item = cfgutil.item_by_key(R.GROUPS, "docked_mode")        # a bool still in GROUPS
        data = dict(_GLOBAL)
        R._apply_key(data, item, "0")                              # the C++ sends bool as the string "0"
        self.assertFalse(data["docked_mode"])
        R._apply_key(data, item, "1")
        self.assertTrue(data["docked_mode"])


if __name__ == "__main__":
    unittest.main()
