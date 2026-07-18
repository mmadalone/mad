"""Byte-stable writer tests for the Wii Classic-Controller profile editor
(lib/madsrv/dolphin_wii_input_cmds.py).

Verifies: profile-first editing writes the Profiles/Wiimote/<name>.ini [Profile] section only;
Classic bindings vocabulary follows the profile's Device (evdev vs SDL); sticks/triggers ->
legacy Axis; d-pad mirrors the profile's existing token; Device/Extension are never touched;
the Player picker is hidden (profile is device-based, not port-based); switching profiles with
unsaved edits is refused; the running-guard (EBUSY); the no-profiles empty state.

Run:  python3 -m unittest tests.test_dolphin_wii_input -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import lib.proc_guard as proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import dolphin_wii_input_cmds as wi
from lib.madsrv.rpc import RpcError

# A Classic-Controller profile (SDL vocabulary).
_PROFILE = """\
[Profile]
Device = SDL/0/Test Pad
Extension = Classic
Classic/Buttons/A = `Button E`
Classic/Buttons/B = `Button S`
Classic/Buttons/ZL = `Shoulder L`
Classic/D-Pad/Up = `Pad N`
Classic/D-Pad/Down = `Pad S`
Classic/Left Stick/Up = `Axis 1-`
Classic/Right Stick/Up = `Axis 3-`
Classic/Triggers/L = `Full Axis 2+`
Rumble/Motor = Strong
"""

SOUTH, EAST, NORTH, WEST = 0x130, 0x131, 0x133, 0x134


class WiiCC(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.pdir = self.dir / "Profiles" / "Wiimote"
        self.pdir.mkdir(parents=True)
        (self.pdir / "CC One.ini").write_text(_PROFILE)
        (self.pdir / "CC Two.ini").write_text(_PROFILE.replace("SDL/0/Test Pad", "SDL/1/Two Pad"))
        self._o_dir = wi.dolphin_wii_profiles.profiles_dir
        self._o_list = wi.dolphin_wii_profiles.list_profiles
        wi.dolphin_wii_profiles.profiles_dir = lambda: self.pdir
        wi.dolphin_wii_profiles.list_profiles = lambda: ["CC One", "CC Two"]
        wi._buf.reset()
        wi._edit_target = ("none",)
        self._o_run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        wi.dolphin_wii_profiles.profiles_dir = self._o_dir
        wi.dolphin_wii_profiles.list_profiles = self._o_list
        wi._buf.reset()
        wi._edit_target = ("none",)
        proc_guard.emulator_running = self._o_run
        shutil.rmtree(self.dir, ignore_errors=True)

    def _pf(self, name="CC One"):
        return self.pdir / f"{name}.ini"

    def _val(self, name, key):
        return cfgutil.ini_read(self._pf(name).read_text(), "Profile", key)

    # -- profile-first shape ---------------------------------------------------
    def test_get_autoselects_first_profile_no_players(self):
        r = wi._input_get({})
        self.assertEqual(r["players"], [])                          # profile is device-based, no port stepper
        self.assertEqual(r["selectors"][0]["value"], "CC One")      # first profile auto-selected
        a = next(b for g in r["groups"] for b in g["binds"] if b["id"] == "Classic/Buttons/A")["value"]
        self.assertEqual(a, "Button E")                             # binds from the profile
        self.assertTrue(r["clearable"])

    def test_get_lists_only_classic_no_nunchuk(self):
        keys = {b["id"] for g in wi._input_get({})["groups"] for b in g["binds"]}
        self.assertTrue(all(k.startswith("Classic/") for k in keys))
        self.assertFalse(any("Nunchuk" in k for k in keys))
        self.assertIn("Classic/Buttons/ZL", keys)
        self.assertIn("Classic/Triggers/L", keys)

    # -- writing ---------------------------------------------------------------
    def test_edit_writes_profile_file_only(self):
        wi._input_get({})                                           # auto-select CC One
        wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": NORTH})   # SDL -> `Button N`
        wi._input_save({})
        self.assertEqual(self._val("CC One", "Classic/Buttons/A"), "`Button N`")
        self.assertEqual(self._val("CC Two", "Classic/Buttons/A"), "`Button E`")    # other profile untouched

    def test_meta_lines_never_written(self):
        wi._input_get({})
        before = self._pf().read_text()
        wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": WEST})
        wi._input_save({})
        after = self._pf().read_text()
        self.assertEqual(after.count("Device = SDL/0/Test Pad"), before.count("Device = SDL/0/Test Pad"))
        self.assertEqual(after.count("Extension = Classic"), before.count("Extension = Classic"))
        self.assertNotIn("Source = ", after)                        # editor never introduces Source

    def test_only_rebound_line_changes(self):
        wi._input_get({})
        before = self._pf().read_text()
        wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": SOUTH})
        wi._input_save({})
        diff = [(a, b) for a, b in zip(before.splitlines(), self._pf().read_text().splitlines()) if a != b]
        self.assertEqual(diff, [("Classic/Buttons/A = `Button E`", "Classic/Buttons/A = `Button S`")])

    def test_stick_legacy_axis(self):
        wi._input_get({})
        wi._input_set({"id": "Classic/Left Stick/Up", "kind": "axis", "value": "-left_x@0"})
        wi._input_save({})
        self.assertEqual(self._val("CC One", "Classic/Left Stick/Up"), "`Axis 0-`")

    def test_trigger_full_axis(self):
        wi._input_get({})
        wi._input_set({"id": "Classic/Triggers/L", "kind": "trigger", "value": "+trigger_left@4"})
        wi._input_save({})
        self.assertEqual(self._val("CC One", "Classic/Triggers/L"), "`Full Axis 4+`")

    def test_dpad_mirror(self):
        wi._input_get({})
        before_down = self._val("CC One", "Classic/D-Pad/Down")
        wi._input_set({"id": "Classic/D-Pad/Up", "kind": "hat", "value": "h0down"})
        wi._input_save({})
        self.assertEqual(self._val("CC One", "Classic/D-Pad/Up"), before_down)

    def test_clear_blanks(self):
        wi._input_get({})
        wi._input_clear({"id": "Classic/Buttons/A"})
        wi._input_save({})
        self.assertEqual(self._val("CC One", "Classic/Buttons/A"), "")

    # -- switching + guards ----------------------------------------------------
    def test_switch_profile(self):
        wi._input_get({})
        wi._selector_set({"key": "profile", "value": "CC Two"})
        self.assertEqual(wi._input_get({})["selectors"][0]["value"], "CC Two")

    def test_switch_with_unsaved_edits_refused(self):
        wi._input_get({})
        wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": SOUTH})   # dirty
        with self.assertRaises(RpcError) as cm:
            wi._selector_set({"key": "profile", "value": "CC Two"})
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_unknown_profile_rejected(self):
        wi._input_get({})
        with self.assertRaises(RpcError):
            wi._selector_set({"key": "profile", "value": "Nope"})

    def test_running_refuses(self):
        wi._input_get({})
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": SOUTH})
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_none_state_clears_phantom_dirty(self):
        # all Classic profiles vanish mid-edit -> the none-state must NOT strand an unclearable
        # "unsaved changes" indicator (dirty must be cleared, since the edit can no longer be saved).
        wi._input_get({})
        wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": SOUTH})   # dirty
        self.assertTrue(wi._buf.dirty)
        wi.dolphin_wii_profiles.list_profiles = lambda: []             # every profile gone
        r = wi._input_get({})
        self.assertFalse(r["dirty"])                                   # phantom dirty cleared
        self.assertFalse(wi._buf.dirty)
        self.assertIn("Create one in Dolphin", r["note"])

    def test_no_profiles_empty_state(self):
        wi.dolphin_wii_profiles.list_profiles = lambda: []
        wi._buf.reset()
        wi._edit_target = ("none",)
        r = wi._input_get({})
        self.assertEqual(r["players"], [])
        self.assertFalse(r["clearable"])                            # nothing to edit
        self.assertIn("Create one in Dolphin", r["note"])
        with self.assertRaises(RpcError):                           # can't edit with no profile
            wi._input_set({"id": "Classic/Buttons/A", "kind": "btn", "value": SOUTH})


if __name__ == "__main__":
    unittest.main()
