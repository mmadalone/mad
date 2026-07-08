"""Tests for the mappable Dolphin hotkeys remapper (lib/madsrv/dolphin_hotkeys_cmds.py).

Verifies the source-verified Dolphin token format `evdev/0/<name>:<control>` (control NAME,
whitespace-stripped device, chord = @(a+b)); pad-only scope (keyboard rejected); byte-preserving
single-line edits; buffered save/cancel + clear; and the running-guard.

Run:  python3 -m unittest tests.test_dolphin_hk -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import lib.proc_guard as proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import dolphin_hotkeys_cmds as hk
from lib.madsrv.rpc import RpcError

_FIXTURE = """\
[Hotkeys]
Device = evdev/0/Some Device
General/Open = @(Ctrl+O)
General/Toggle Pause = @(Back+`Button S`)
General/Take Screenshot = F9
Load State/Load State Slot 1 = F1
"""

SOUTH, EAST, NORTH = 0x130, 0x131, 0x133
KEY_A = 30   # a keyboard code


class DolphinHotkeys(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "Hotkeys.ini"
        self.tmp.write_text(_FIXTURE)
        self._orig_file = hk._FILE
        hk._FILE = self.tmp
        hk._buf.reset()
        self._orig_run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        hk._FILE = self._orig_file
        hk._buf.reset()
        proc_guard.emulator_running = self._orig_run
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def _val(self, name):
        return (cfgutil.ini_read(self.tmp.read_text(), "Hotkeys", name) or "").strip()

    def _set(self, name, codes, device="My Pad"):
        hk._input_set({"id": name, "codes": codes, "device": device})
        hk._input_save({})

    # -- enumerate -------------------------------------------------------------
    def test_get_enumerates_actions_skips_device(self):
        r = hk._input_get({})
        labels = [b["label"] for g in r["groups"] for b in g["binds"]]
        self.assertIn("Open", labels)
        self.assertIn("Toggle Pause", labels)
        self.assertNotIn("Device", labels)                 # the Device header is not an action
        self.assertTrue(r["clearable"])
        self.assertTrue(all(b["kind"] == "chord" for g in r["groups"] for b in g["binds"]))

    # -- token format ----------------------------------------------------------
    def test_single_button_device_qualified(self):
        self._set("General/Toggle Pause", [EAST], device="  My Pad  ")   # whitespace stripped
        self.assertEqual(self._val("General/Toggle Pause"), "`evdev/0/My Pad:EAST`")

    def test_chord_expression(self):
        self._set("General/Toggle Pause", [SOUTH, EAST])
        self.assertEqual(self._val("General/Toggle Pause"),
                         "@(`evdev/0/My Pad:SOUTH`+`evdev/0/My Pad:EAST`)")

    # -- byte preservation -----------------------------------------------------
    def test_only_target_line_changes(self):
        before = self.tmp.read_text()
        self._set("General/Toggle Pause", [NORTH])
        after = self.tmp.read_text()
        diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0][0], "General/Toggle Pause = @(Back+`Button S`)")
        # Device line + other actions untouched
        self.assertIn("Device = evdev/0/Some Device", after)
        self.assertIn("General/Open = @(Ctrl+O)", after)
        self.assertIn("General/Take Screenshot = F9", after)

    # -- clear -----------------------------------------------------------------
    def test_clear_blanks_the_line(self):
        hk._input_clear({"id": "General/Open"})
        hk._input_save({})
        self.assertEqual(self._val("General/Open"), "")
        self.assertIn("General/Open = \n", self.tmp.read_text() + "\n")   # key kept, value blank

    # -- pad-only scope + guards ----------------------------------------------
    def test_keyboard_code_rejected(self):
        with self.assertRaises(RpcError) as cm:
            hk._input_set({"id": "General/Open", "codes": [KEY_A], "device": "kb"})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_missing_device_rejected(self):
        with self.assertRaises(RpcError):
            hk._input_set({"id": "General/Open", "codes": [EAST], "device": ""})

    def test_empty_capture_rejected(self):
        with self.assertRaises(RpcError):
            hk._input_set({"id": "General/Open", "codes": [], "device": "My Pad"})

    def test_running_refuses_save(self):
        hk._input_set({"id": "General/Open", "codes": [EAST], "device": "My Pad"})   # stage ok
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            hk._input_save({})
        self.assertEqual(cm.exception.code, "EBUSY")

    # -- buffered save/cancel --------------------------------------------------
    def test_cancel_discards_stage(self):
        before = self.tmp.read_text()
        hk._input_set({"id": "General/Open", "codes": [EAST], "device": "My Pad"})
        self.assertTrue(hk._buf.dirty)
        hk._input_cancel({})
        self.assertFalse(hk._buf.dirty)
        self.assertEqual(self.tmp.read_text(), before)     # nothing written


if __name__ == "__main__":
    unittest.main()
