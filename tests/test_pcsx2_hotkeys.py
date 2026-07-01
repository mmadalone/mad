"""Tests for the PCSX2 Hotkeys remapper (pcsx2hk.*), the generic Start-to-unbind (input_clear)
verbs, and the launch-time [Hotkeys] SDL-index rewrite.

Covers, headlessly (temp inis — no hardware, no running emulator):
  * pcsx2hk.input_get/input_set/input_clear over the flat [Hotkeys] section
  * chord rendering (pad + keyboard + Guide + trigger), byte-preservation, multi-line hotkeys
  * capture_cmds / input_translate: the F-key keymap + Guide token keyboard hotkeys rely on
  * switch_bind: _rewrite_pcsx2_hotkeys + lazy-record/restore round-trip
  * standalones_cmds: the Hotkeys row inside the PS2 Input group
  * input_clear registered + clearable flag on the PS2-env input backends

Run:  python3 -m unittest tests.test_pcsx2_hotkeys -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import evdev.ecodes as e

from lib import inifile, switch_bind as sb
from lib.madsrv import capture_cmds as cc
from lib.madsrv import input_translate as it
from lib.madsrv import pcsx2_hotkeys_cmds as hk
from lib.madsrv import rpc, standalones_cmds
# Import the PS2-env input backends so their @method registrations run regardless of test
# order (this file asserts their input_clear verbs exist).
from lib.madsrv import (guncon2_retail_input_cmds,  # noqa: F401
                        pcsx2_input_cmds, pcsx2_pergame_input_cmds)


def _flat(secs):
    """Flatten the PS2 nested menu (recurse into group rows) to (kind, arg, label) leaves."""
    out = []
    for s in secs:
        if s.get("kind") == "group":
            out.extend(_flat(s.get("sections", [])))
        else:
            out.append((s["kind"], s.get("arg"), s.get("label")))
    return out


ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2")


class Registration(unittest.TestCase):
    def test_hotkey_rpcs_registered(self):
        for m in ("pcsx2hk.input_get", "pcsx2hk.input_set", "pcsx2hk.input_clear"):
            self.assertIn(m, rpc._METHODS, m)

    def test_input_clear_on_ps2_env_backends(self):
        for m in ("pcsx2.input_clear", "pcsx2pgin.input_clear", "guncon2_retail.input_clear"):
            self.assertIn(m, rpc._METHODS, m)

    def test_hotkeys_row_inside_ps2_input_group(self):
        leaves = _flat(standalones_cmds._sections_for(ENTRY))
        self.assertIn(("input_map", "pcsx2hk", "Hotkeys"), leaves)
        # it sits inside the Input group (a group row), not at the tile's top level
        top = [s for s in standalones_cmds._sections_for(ENTRY)]
        inp = next(s for s in top if s.get("label") == "Input" and s.get("kind") == "group")
        inner = [(s.get("kind"), s.get("arg")) for s in inp["sections"]]
        self.assertIn(("input_map", "pcsx2hk"), inner)
        # ordered right after "Pads -> players"
        labels = [s.get("label") for s in inp["sections"]]
        self.assertLess(labels.index("Pads → players"), labels.index("Hotkeys"))


class InputTranslate(unittest.TestCase):
    def test_guide_and_trigger_unchanged(self):
        self.assertEqual(it.sdl_button_source(0x13C), "Guide")
        self.assertEqual(it.sdl_button_source(0x139), "+RightTrigger")

    def test_keyboard_fkeys_and_edit_keys(self):
        self.assertEqual(it.usb_keyboard_source("f1"), "Keyboard/F1")
        self.assertEqual(it.usb_keyboard_source("f12"), "Keyboard/F12")
        self.assertEqual(it.usb_keyboard_source("ctrl"), "Keyboard/Control")
        self.assertEqual(it.usb_keyboard_source("insert"), "Keyboard/Insert")
        self.assertEqual(it.usb_keyboard_source("minus"), "Keyboard/Minus")


class Keymap(unittest.TestCase):
    def test_guide_and_fkeys_available_for_keyboard_hotkeys(self):
        # Guide is already an accepted pad button; the F-keys + edit keys were added to the keymap
        # so keyboard hotkeys (PCSX2 save-state defaults are F1..F10) can be captured/rendered.
        self.assertEqual(cc.ra_keyname(e.KEY_F1), "f1")
        self.assertEqual(cc.ra_keyname(e.KEY_F10), "f10")
        self.assertEqual(cc.ra_keyname(e.KEY_INSERT), "insert")
        self.assertEqual(cc.ra_keyname(e.KEY_A), "a")
        self.assertEqual(it.sdl_button_source(0x13C), "Guide")


class HotkeyBackend(unittest.TestCase):
    HK = ("[UI]\nx = 1\n\n"
          "[Hotkeys]\nSaveStateToSlot = Keyboard/F1\n"
          "ZoomIn = Keyboard/Control & Keyboard/Plus\n\n"
          "[Pad1]\nType = DualShock2\n")

    def _ini(self, body=None):
        p = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        p.write_text(self.HK if body is None else body, encoding="utf-8", newline="")
        return p

    def _with(self, p, running=False):
        hk._INI = p
        hk._running = lambda: running

    def tearDown(self):
        # restore module state (other tests import the module too)
        hk._INI = Path("~/.config/PCSX2/inis/PCSX2.ini").expanduser()

    def test_input_get_shape(self):
        self._with(self._ini())
        g = hk._input_get({})
        titles = [grp["title"] for grp in g["groups"]]
        for t in ("Navigation", "Frame control", "System", "Save states", "Audio", "Graphics"):
            self.assertIn(t, titles)
        self.assertTrue(g["clearable"])
        self.assertFalse(g["running"])
        rows = {b["id"]: b for grp in g["groups"] for b in grp["binds"]}
        self.assertEqual(rows["SaveStateToSlot"]["value"], "F1")
        self.assertEqual(rows["SaveStateToSlot"]["kind"], "chord")
        self.assertTrue(rows["ToggleFullscreen"]["capturable"])
        # unknown live key surfaced + preserved (ZoomIn is not hardcoded)
        self.assertEqual(titles[-1], "Other (set in PCSX2)")
        self.assertEqual(rows["ZoomIn"]["value"], "Control + Plus")

    def test_chord_and_single_binds_written(self):
        p = self._ini()
        self._with(p)
        hk._input_set({"id": "OpenPauseMenu", "codes": [0x13A, 0x130]})        # pad chord
        hk._input_set({"id": "ToggleFullscreen", "codes": [e.KEY_LEFTCTRL, e.KEY_F5]})  # kb chord
        hk._input_set({"id": "Mute", "codes": [0x13C]})                        # Guide
        hk._input_set({"id": "HoldTurbo", "codes": [0x139]})                   # +RightTrigger
        txt = p.read_text(encoding="utf-8", newline="")
        for want in ("OpenPauseMenu = SDL-0/Back & SDL-0/FaceSouth",
                     "ToggleFullscreen = Keyboard/Control & Keyboard/F5",
                     "Mute = SDL-0/Guide", "HoldTurbo = SDL-0/+RightTrigger"):
            self.assertIn(want + "\n", txt)
        # byte-preservation: pre-existing keys + other sections untouched
        self.assertIn("SaveStateToSlot = Keyboard/F1\n", txt)
        self.assertIn("ZoomIn = Keyboard/Control & Keyboard/Plus\n", txt)
        self.assertTrue(txt.startswith("[UI]\nx = 1\n"))
        self.assertIn("[Pad1]\nType = DualShock2\n", txt)

    def test_replace_keeps_single_line(self):
        p = self._ini()
        self._with(p)
        hk._input_set({"id": "SaveStateToSlot", "codes": [e.KEY_F2]})
        txt = p.read_text(encoding="utf-8", newline="")
        self.assertEqual(txt.count("SaveStateToSlot ="), 1)
        self.assertIn("SaveStateToSlot = Keyboard/F2\n", txt)

    def test_input_clear_removes_only_target(self):
        p = self._ini()
        self._with(p)
        hk._input_clear({"id": "SaveStateToSlot"})
        txt = p.read_text(encoding="utf-8", newline="")
        self.assertNotIn("SaveStateToSlot", txt)
        self.assertIn("ZoomIn = Keyboard/Control & Keyboard/Plus\n", txt)  # untouched

    def test_guards(self):
        p = self._ini()
        self._with(p)
        with self.assertRaises(rpc.RpcError):
            hk._input_set({"id": "NotARealAction", "codes": [0x130]})
        with self.assertRaises(rpc.RpcError):
            hk._input_set({"id": "Mute", "codes": [0x999]})               # unmappable code
        # EBUSY while pcsx2-qt runs
        self._with(p, running=True)
        with self.assertRaises(rpc.RpcError):
            hk._input_set({"id": "Mute", "codes": [0x13C]})

    def test_creates_hotkeys_section_when_absent(self):
        p = self._ini("[UI]\nx = 1\n")     # no [Hotkeys] at all
        self._with(p)
        hk._input_set({"id": "ToggleFullscreen", "codes": [e.KEY_F11]})
        txt = p.read_text(encoding="utf-8", newline="")
        self.assertIn("[Hotkeys]\n", txt)
        self.assertIn("ToggleFullscreen = Keyboard/F11\n", txt)

    def test_multiline_hotkey_shown_and_collapsed_on_rebind(self):
        # PCSX2 allows a hotkey to have several alternative binding lines (review fix B).
        p = self._ini("[Hotkeys]\nToggleFullscreen = Keyboard/F11\n"
                      "ToggleFullscreen = SDL-0/Guide\n")
        self._with(p)
        rows = {b["id"]: b for grp in hk._input_get({})["groups"] for b in grp["binds"]}
        self.assertEqual(rows["ToggleFullscreen"]["value"], "F11 / Guide")   # BOTH alternatives shown
        hk._input_set({"id": "ToggleFullscreen", "codes": [e.KEY_F10]})       # rebind
        txt = p.read_text(encoding="utf-8", newline="")
        self.assertEqual(txt.count("ToggleFullscreen ="), 1)                  # collapsed to ONE line
        self.assertIn("ToggleFullscreen = Keyboard/F10\n", txt)

    def test_multiline_hotkey_clear_removes_all(self):
        p = self._ini("[Hotkeys]\nScreenshot = Keyboard/F8\nScreenshot = SDL-0/Guide\n")
        self._with(p)
        hk._input_clear({"id": "Screenshot"})
        self.assertNotIn("Screenshot", p.read_text(encoding="utf-8", newline=""))  # every line gone


class LaunchRewrite(unittest.TestCase):
    def _fixture(self):
        p = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        orig = ("[UI]\nx = 1\n\n"
                "[Hotkeys]\nOpenPauseMenu = SDL-0/Back & SDL-0/RightStick\n"
                "ToggleFullscreen = Keyboard/Alt & Keyboard/Return\n\n"
                "[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\n\n"
                "[Folders]\nCheats = cheats\n")
        p.write_text(orig, encoding="utf-8", newline="")
        return p, orig

    def _base_snapshot(self, p):
        side = sb._sidecar(p)
        if side.exists():
            side.unlink()
        side.write_text(json.dumps({"emu": "pcsx2", "input": sb._snapshot("pcsx2", p)}),
                        encoding="utf-8")
        return side

    def test_rewrite_to_player1_index_and_restore(self):
        p, orig = self._fixture()
        side = self._base_snapshot(p)
        sb._rewrite_pcsx2_hotkeys(p, 4, side)
        txt = p.read_text(encoding="utf-8", newline="")
        self.assertIn("OpenPauseMenu = SDL-4/Back & SDL-4/RightStick\n", txt)
        self.assertNotIn("SDL-0/Back", txt)
        self.assertIn("ToggleFullscreen = Keyboard/Alt & Keyboard/Return\n", txt)  # kb untouched
        self.assertTrue(json.loads(side.read_text())["input"].get("Hotkeys"))       # recorded lazily
        sb.restore_target(p)
        self.assertEqual(p.read_text(encoding="utf-8", newline=""), orig)           # byte-exact

    def test_keyboard_only_hotkeys_untouched(self):
        p = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        body = ("[Hotkeys]\nTogglePause = Keyboard/Space\n\n[Pad1]\nType = DualShock2\n")
        p.write_text(body, encoding="utf-8", newline="")
        side = self._base_snapshot(p)
        before = side.read_text()
        sb._rewrite_pcsx2_hotkeys(p, 4, side)
        self.assertEqual(p.read_text(encoding="utf-8", newline=""), body)   # file untouched
        self.assertNotIn("Hotkeys", json.loads(side.read_text())["input"])  # not recorded


class Pcsx2ClearResetsToBaked(unittest.TestCase):
    def test_clear_forces_baked_default_not_delete(self):
        # review fix C: a resting non-baked [PadN] source is preserved at launch when no override
        # exists, so "reset to default" must WRITE the baked default (not delete the entry).
        from lib import pcsx2_cfg
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text("[Pad1]\nType = DualShock2\n", encoding="utf-8")
        pcsx2_cfg.update_input_override(ini, 1, "Cross", "FaceEast")   # a non-baked binding
        pcsx2_cfg.clear_input_override(ini, 1, "Cross")
        ov = pcsx2_cfg.load_input_overrides(ini)
        self.assertEqual(ov.get(1, {}).get("Cross"),
                         pcsx2_cfg.baked_default_sources()["Cross"])   # forced baked, not deleted


if __name__ == "__main__":
    unittest.main()
