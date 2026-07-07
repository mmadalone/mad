"""eden_hk.* — the format-adaptive Hotkeys remapper: enumerate actions from the live nested
store, render keyboard · controller, remap keyboard (single + combo) -> KeySeq and controller ->
Controller_KeySeq (each flipping \\default), clear both fields, and READ-ONLY on the flat-array
format."""
import shutil
import tempfile
import unittest
from pathlib import Path

import evdev.ecodes as e

from lib import proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import eden_hotkeys_cmds as hk
from lib.madsrv import rpc

_DOCK = "Shortcuts\\Main%20Window\\Change%20Docked%20Mode"
_SHOT = "Shortcuts\\Main%20Window\\Capture%20Screenshot"
FIX = (
    "[UI]\n"
    f"{_DOCK}\\KeySeq\\default=false\n{_DOCK}\\KeySeq=F10\n"
    f"{_DOCK}\\Controller_KeySeq\\default=false\n{_DOCK}\\Controller_KeySeq=Home+X\n"
    f"{_SHOT}\\KeySeq\\default=false\n{_SHOT}\\KeySeq=Ctrl+P\n"
    f"{_SHOT}\\Controller_KeySeq\\default=false\n{_SHOT}\\Controller_KeySeq=Screenshot\n"
)
FLAT = ("[UI]\nshortcuts\\size=1\n"
        "shortcuts\\1\\name=Fullscreen\nshortcuts\\1\\keyseq=F11\n"
        "shortcuts\\1\\controller_keyseq=\n")


class EdenHotkeys(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(FIX, newline="")
        self._orig = hk._FILE
        hk._FILE = self.ini
        hk._buf.reset()          # fresh buffer per case (the buffer is a module-level singleton)
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        hk._FILE = self._orig
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, verb, **params):
        return rpc._METHODS[f"eden_hk.{verb}"][0](params)

    def _set(self, **params):
        """Stage a capture then Save. The buffered editor only writes disk on save, so a
        test that asserts on file content must commit first."""
        r = self._call("input_set", **params)
        self._call("input_save")
        return r

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "UI", key)

    def test_registered(self):
        for v in ("input_get", "input_set", "input_clear", "input_save", "input_cancel"):
            self.assertIn(f"eden_hk.{v}", rpc._METHODS)

    def test_enumerate_and_render(self):
        p = self._call("input_get")
        self.assertEqual(len(p["groups"]), 1)
        self.assertEqual(p["groups"][0]["title"], "Main Window")
        rows = {b["label"]: b for b in p["groups"][0]["binds"]}
        self.assertEqual(rows["Change Docked Mode"]["value"], "F10  ·  Home+X")
        self.assertTrue(p["clearable"])

    def test_keyboard_single_remap(self):
        self._set(id=_DOCK, kind="chord", codes=[e.KEY_F5])
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F5")
        self.assertEqual(self._disk(_DOCK + "\\KeySeq\\default"), "false")
        self.assertEqual(self._disk(_DOCK + "\\Controller_KeySeq"), "Home+X")  # controller untouched

    def test_keyboard_combo_remap(self):
        self._set(id=_SHOT, kind="chord", codes=[e.KEY_LEFTCTRL, e.KEY_P])
        self.assertEqual(self._disk(_SHOT + "\\KeySeq"), "Ctrl+P")

    def test_controller_remap(self):
        self._set(id=_DOCK, kind="chord", codes=[e.BTN_START])
        self.assertEqual(self._disk(_DOCK + "\\Controller_KeySeq"), "Plus")
        self.assertEqual(self._disk(_DOCK + "\\Controller_KeySeq\\default"), "false")
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F10")                # keyboard untouched

    def test_trigger_maps_to_zl(self):
        # sdl_button_source sign-prefixes triggers ('+LeftTrigger'); the token map must still hit ZL.
        self._set(id=_DOCK, kind="chord", codes=[e.BTN_TL2])
        self.assertEqual(self._disk(_DOCK + "\\Controller_KeySeq"), "ZL")

    def test_unknown_action_errors(self):
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="Shortcuts\\X\\Y", kind="chord", codes=[e.KEY_F5])

    def test_clear_both_fields(self):
        self._call("input_clear", id=_DOCK)
        self._call("input_save")
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "")
        self.assertEqual(self._disk(_DOCK + "\\Controller_KeySeq"), "")

    def test_flat_format_read_only(self):
        self.ini.write_text(FLAT, newline="")
        p = self._call("input_get")
        self.assertFalse(p["clearable"])
        self.assertFalse(p["groups"][0]["binds"][0]["capturable"])
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="flat:1", kind="chord", codes=[e.KEY_F5])

    # ── buffered editor: stage in memory, commit on Save, revert on Cancel ────
    def test_buffered_flag_advertised(self):
        self.assertTrue(self._call("input_get").get("buffered"))

    def test_stage_leaves_file_unchanged(self):
        before = self.ini.read_text(newline="")
        p = self._call("input_set", id=_DOCK, kind="chord", codes=[e.KEY_F5])
        self.assertTrue(p["dirty"])                                   # response reports it is staged
        self.assertEqual(self.ini.read_text(newline=""), before)     # NOT written to disk yet
        g = self._call("input_get")
        self.assertTrue(g["buffered"])
        self.assertTrue(g["dirty"])
        self.assertEqual(self._render_row(g, _DOCK), "F5  ·  Home+X")  # buffer reflects the stage

    def test_stage_then_save_commits_once(self):
        self._call("input_set", id=_DOCK, kind="chord", codes=[e.KEY_F5])
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F10")       # still original while staged
        self.assertEqual(self._call("input_save"), {"saved": True, "dirty": False})
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F5")        # committed on save
        self.assertFalse(self._call("input_get")["dirty"])
        # nothing left to save -> a second save is a no-op (proves it committed exactly once)
        self.assertEqual(self._call("input_save"), {"saved": False, "dirty": False})

    def test_stage_then_cancel_reverts(self):
        self._call("input_set", id=_DOCK, kind="chord", codes=[e.KEY_F5])
        self._call("input_cancel")
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F10")       # discard leaves disk untouched
        self.assertFalse(self._call("input_get")["dirty"])
        self.assertEqual(self._render_row(self._call("input_get"), _DOCK), "F10  ·  Home+X")

    def test_flush_replays_onto_fresh_disk_preserving_foreign_edits(self):
        # LANDMINE: eden.* rewrites [Controls]/[System] in the SAME qt-config.ini. A hotkey save
        # must re-read fresh disk and rewrite ONLY [UI], never blind-write the whole buffered copy.
        self._call("input_set", id=_DOCK, kind="chord", codes=[e.KEY_F5])   # stage a [UI] hotkey edit
        # a concurrent writer (eden.*) commits a [Controls] change AFTER we staged
        self.ini.write_text(self.ini.read_text(newline="") +
                            '\n[Controls]\nplayer_0_button_a="engine:sdl,button:0"\n', newline="")
        self._call("input_save")
        self.assertEqual(self._disk(_DOCK + "\\KeySeq"), "F5")              # our hotkey landed
        controls = cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", "player_0_button_a")
        self.assertEqual(controls, '"engine:sdl,button:0"')                # foreign [Controls] survived

    def _render_row(self, payload, base):
        for g in payload["groups"]:
            for b in g["binds"]:
                if b["id"] == base:
                    return b["value"]
        return None


if __name__ == "__main__":
    unittest.main()
