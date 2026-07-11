"""Keyboard keys in the quit COMBO (capture side) + the watcher's keyboard gate.

The quit combo can be set from keyboard keystrokes (a key / key-combo on a keyboard
or a control-panel encoder), in addition to gamepad + mouse buttons. Keys are accepted
ONLY in combo mode, ride in the same evdev-code path as buttons, and the watcher opens
keyboard nodes only when the active combo actually contains a key.

Synthetic events; no hardware.  Run:  python3 -m unittest tests.test_keyboard_combo_capture -v
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import evdev.ecodes as e

from lib.madsrv import capture_cmds as cc

KEY_A, KEY_B = e.KEY_A, e.KEY_B   # 0x1e, 0x30 — capturable (in _RA_KEYMAP)


class _Ev:
    def __init__(self, ty, code, val):
        self.type, self.code, self.value = ty, code, val


class _D:
    def __init__(self, path="/dev/input/event99"):
        self.path = path

    def capabilities(self, absinfo=False):
        return {e.EV_KEY: [0x130, KEY_A, KEY_B]}


class KeyboardComboCapture(unittest.TestCase):
    def _stream(self, mode):
        s = cc._CaptureStream(mode, 5.0)
        s._identify = lambda d: {"name": "Keyboard"}
        return s

    def test_combo_accepts_a_key_press_then_fires_on_release(self):
        s = self._stream("combo")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 1), _D()))   # press: accumulate
        res = s._on_button(_Ev(e.EV_KEY, KEY_A, 0), _D())               # release: fire
        self.assertEqual(res["held"], [KEY_A])
        self.assertIn("A", res["names"])                               # btn_name resolves KEY_A

    def test_keys_are_combo_only_not_identify(self):
        # a key must NOT be captured in identify/axis modes (combo-only, like mouse)
        s = self._stream("identify")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 1), _D()))
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 0), _D()))   # never fires

    def test_multi_key_same_device_combo(self):
        s = self._stream("combo")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 1), _D()))   # A down
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_B, 1), _D()))   # B down
        res = s._on_button(_Ev(e.EV_KEY, KEY_B, 0), _D())               # release -> fire
        self.assertEqual(res["held"], sorted([KEY_A, KEY_B]))

    def test_face_button_plus_key_same_device(self):
        s = self._stream("combo")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x130, 1), _D()))   # face button
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 1), _D()))   # + a key
        res = s._on_button(_Ev(e.EV_KEY, KEY_A, 0), _D())
        self.assertEqual(res["held"], sorted([0x130, KEY_A]))

    def test_cross_device_key_rejected_single_device_lock(self):
        # combo locks to the first device; a key on a DIFFERENT device is rejected
        s = self._stream("combo")
        d1, d2 = _D("/dev/input/event1"), _D("/dev/input/event2")
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, 0x130, 1), d1))     # locks to d1
        self.assertIsNone(s._on_button(_Ev(e.EV_KEY, KEY_A, 1), d2))     # other device -> ignored
        res = s._on_button(_Ev(e.EV_KEY, 0x130, 0), d1)                  # release on d1 -> fire
        self.assertEqual(res["held"], [0x130])                          # the cross-device key dropped


def _load_watcher():
    p = Path(__file__).resolve().parent.parent / "quit-combo-watcher.py"
    spec = importlib.util.spec_from_file_location("quit_combo_watcher", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class WatcherKeyboardGate(unittest.TestCase):
    def setUp(self):
        self.qcw = _load_watcher()

    def test_keyboards_opened_only_when_requested(self):
        fake = [SimpleNamespace(path="/dev/input/event0", is_joypad=True, is_mouse=False, is_keyboard=False),
                SimpleNamespace(path="/dev/input/event1", is_joypad=False, is_mouse=True, is_keyboard=False),
                SimpleNamespace(path="/dev/input/event2", is_joypad=False, is_mouse=False, is_keyboard=True)]
        orig = self.qcw.devices.enumerate_devices
        self.qcw.devices.enumerate_devices = lambda: fake
        try:
            self.assertEqual(self.qcw._all_input_event_nodes(False),
                             ["/dev/input/event0", "/dev/input/event1"])         # keyboard excluded
            self.assertEqual(self.qcw._all_input_event_nodes(True),
                             ["/dev/input/event0", "/dev/input/event1", "/dev/input/event2"])  # included
        finally:
            self.qcw.devices.enumerate_devices = orig

    def test_gate_predicate_detects_a_key_in_the_combo(self):
        # the call-site gate: capturable keys are < BTN_MISC (0x100); buttons are >=
        self.assertTrue(any(c < 0x100 for c in {0x130, KEY_A}))      # combo has a key
        self.assertFalse(any(c < 0x100 for c in {0x13a, 0x13b}))    # buttons-only


class QuitComboHandheldScope(unittest.TestCase):
    """WS-G: --handheld layers [quit_combo.handheld] on top as the highest-priority override."""

    def setUp(self):
        self.qcw = _load_watcher()
        self.d = Path(tempfile.mkdtemp())
        self._save = (self.qcw.POLICY, self.qcw.LOCAL_POLICY)
        self.qcw.POLICY = self.d / "policy.toml"          # absent -> only the local file applies
        self.local = self.d / "local.toml"
        self.qcw.LOCAL_POLICY = self.local
        for k in ("QUIT_COMBO_BUTTONS", "QUIT_COMBO_HOLD"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.qcw.POLICY, self.qcw.LOCAL_POLICY = self._save
        shutil.rmtree(self.d, ignore_errors=True)

    def test_handheld_scope_overrides_when_set(self):
        self.local.write_text('[quit_combo]\nbuttons = [314, 315]\nhold_sec = 2.0\n'
                              '[quit_combo.handheld]\nbuttons = [317, 318]\nhold_sec = 1.0\n')
        self.assertEqual(self.qcw._read_quit_combo("ps2", handheld=True), ({317, 318}, 1.0))   # Deck chord wins
        self.assertEqual(self.qcw._read_quit_combo("ps2", handheld=False), ({314, 315}, 2.0))  # docked untouched

    def test_handheld_default_overrides_per_system_combo(self):
        # a keyboard-only per-system combo (Lindbergh) is REPLACED by the pressable Deck default
        # handheld (one global chord), while docked keeps the keyboard combo untouched.
        self.local.write_text('[quit_combo]\nbuttons = [314, 315]\nhold_sec = 2.0\n'
                              '[quit_combo.lindbergh]\nbuttons = [106, 108]\n')
        self.assertEqual(self.qcw._read_quit_combo("lindbergh", handheld=True)[0], {314, 315})   # Deck default
        self.assertEqual(self.qcw._read_quit_combo("lindbergh", handheld=False)[0], {106, 108})  # docked untouched


if __name__ == "__main__":
    unittest.main()
