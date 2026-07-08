"""Byte-stable writer tests for the GameCube pad remap (lib/madsrv/dolphin_gc_input_cmds.py).

Verifies: the token vocabulary follows each slot's Device (evdev vs SDL names); bare-vs-backtick
serialization; sticks/triggers -> legacy `Axis N` / `Full Axis N+` (resolves on both backends);
d-pad mirrors the slot's existing scheme; profile load = [GCPadN] block replace; only the rebound
line changes; Start clears; and the running-guard (EBUSY).

Run:  python3 -m unittest tests.test_dolphin_gc_input -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import lib.proc_guard as proc_guard
from lib.madsrv import cfgutil
from lib.madsrv import dolphin_gc_input_cmds as gi
from lib.madsrv.rpc import RpcError

# GCPad1 = SDL vocabulary, GCPad2 = evdev vocabulary (mirrors the live layout).
_FIXTURE = """\
[GCPad1]
Device = SDL/0/Test Pad
Buttons/A = `Button E`
Buttons/B = `Button S`
Buttons/X = `Button N`
Buttons/Y = `Button W`
Buttons/Z = Back
Buttons/Start = Start
Main Stick/Up = `Axis 1-`
C-Stick/Up = `Right Y+`
Main Stick/Calibration = 100.00 141.42 100.00 141.42
Triggers/L = `Shoulder L`
Triggers/R = `Shoulder R`
D-Pad/Up = `Pad N`
Rumble/Motor = Strong
[GCPad2]
Device = evdev/1/Test Pad
Buttons/A = EAST
Buttons/B = SOUTH
Buttons/X = NORTH
Buttons/Y = WEST
Buttons/Z = SELECT
Buttons/Start = START
Main Stick/Up = `Axis 1-`
C-Stick/Up = `Axis 3-`
Main Stick/Calibration = 100.00 141.42 100.00 141.42
Triggers/L = TL
Triggers/R = TR
Triggers/L-Analog = `Full Axis 2+`
Triggers/R-Analog = `Full Axis 5+`
D-Pad/Up = `DPAD_UP`
D-Pad/Down = `DPAD_DOWN`
D-Pad/Left = `DPAD_LEFT`
D-Pad/Right = `DPAD_RIGHT`
Rumble/Motor = Strong
"""

# evdev codes
SOUTH, EAST, NORTH, WEST = 0x130, 0x131, 0x133, 0x134
TL, TR, TL2, SELECT, START = 0x136, 0x137, 0x138, 0x13A, 0x13B


class GCRemap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "GCPadNew.ini"
        self.tmp.write_text(_FIXTURE)
        self._orig_file = gi._FILE
        gi._FILE = self.tmp
        gi._buf.reset()
        gi._pending.clear()
        self._orig_run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        gi._FILE = self._orig_file
        gi._buf.reset()
        proc_guard.emulator_running = self._orig_run
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def _val(self, sec, key):
        return cfgutil.ini_read(self.tmp.read_text(), sec, key)

    def _remap(self, player, key, code):
        gi._input_set({"player": str(player), "id": key, "kind": "btn", "value": code})
        gi._input_save({})

    # -- vocabulary + quoting --------------------------------------------------
    def test_evdev_vocab_bare_and_backtick(self):
        self._remap(2, "Buttons/A", NORTH)     # evdev NORTH (bare, alpha)
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "NORTH")
        self._remap(2, "Buttons/Z", TL2)       # evdev TL2 (backtick, digit)
        self.assertEqual(self._val("GCPad2", "Buttons/Z"), "`TL2`")

    def test_sdl_vocab_bare_and_backtick(self):
        self._remap(1, "Buttons/A", WEST)      # SDL `Button W` (backtick, space)
        self.assertEqual(self._val("GCPad1", "Buttons/A"), "`Button W`")
        self._remap(1, "Buttons/Z", SELECT)    # SDL Back (bare, alpha)
        self.assertEqual(self._val("GCPad1", "Buttons/Z"), "Back")
        self._remap(1, "Triggers/L", TL)       # SDL `Shoulder L`
        self.assertEqual(self._val("GCPad1", "Triggers/L"), "`Shoulder L`")

    # -- byte-stability --------------------------------------------------------
    def test_only_rebound_line_changes(self):
        before = self.tmp.read_text()
        self._remap(2, "Buttons/A", SOUTH)     # EAST -> SOUTH
        after = self.tmp.read_text()
        diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(diff, [("Buttons/A = EAST", "Buttons/A = SOUTH")])
        # Device / Calibration / Rumble untouched
        self.assertEqual(before.count("Device = "), after.count("Device = "))
        self.assertIn("Main Stick/Calibration = 100.00 141.42 100.00 141.42", after)
        self.assertEqual(after.count("Rumble/Motor = Strong"), 2)

    def test_wrong_player_targets_right_section(self):
        self._remap(1, "Buttons/A", NORTH)     # SDL slot
        self._remap(2, "Buttons/A", NORTH)     # evdev slot
        self.assertEqual(self._val("GCPad1", "Buttons/A"), "`Button N`")
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "NORTH")

    # -- sticks / triggers / d-pad / profiles ---------------------------------
    def test_stick_remap_legacy_axis(self):
        # sticks -> `Axis <rank><sign>` (legacy form; resolves on both evdev + SDL)
        gi._input_set({"player": "2", "id": "Main Stick/Up", "kind": "axis", "value": "-left_x@0"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "Main Stick/Up"), "`Axis 0-`")
        gi._input_set({"player": "1", "id": "C-Stick/Up", "kind": "axis", "value": "+right_y@3"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad1", "C-Stick/Up"), "`Axis 3+`")

    def test_trigger_remap_full_axis(self):
        gi._input_set({"player": "2", "id": "Triggers/R-Analog", "kind": "trigger",
                       "value": "+trigger_right@5"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "Triggers/R-Analog"), "`Full Axis 5+`")

    def test_dpad_mirror_reuses_existing(self):
        # rebind D-Pad/Up to physical 'down' -> reuse the slot's existing D-Pad/Down token, verbatim
        before_down = self._val("GCPad2", "D-Pad/Down")
        gi._input_set({"player": "2", "id": "D-Pad/Up", "kind": "hat", "value": "h0down"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "D-Pad/Up"), before_down)     # `DPAD_DOWN`

    def test_dpad_unbound_source_rejected(self):
        self.tmp.write_text(_FIXTURE.replace("D-Pad/Left = `DPAD_LEFT`", "D-Pad/Left = "))
        gi._buf.reset()
        with self.assertRaises(RpcError):
            gi._input_set({"player": "2", "id": "D-Pad/Up", "kind": "hat", "value": "h0left"})

    def test_button_on_dpad_row(self):
        gi._input_set({"player": "2", "id": "D-Pad/Up", "kind": "btn", "value": SOUTH})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "D-Pad/Up"), "SOUTH")

    def test_profile_load_replaces_section(self):
        orig = gi.dolphin_profiles.profile_body
        self.addCleanup(lambda: setattr(gi.dolphin_profiles, "profile_body", orig))
        gi.dolphin_profiles.profile_body = lambda name: "Device = SDL/9/Fake\nButtons/A = `Button S`\n"
        gi._selector_set({"player": "2", "key": "profile", "value": "Fake"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "Device"), "SDL/9/Fake")
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "`Button S`")
        self.assertIsNone(self._val("GCPad2", "D-Pad/Up"))                 # replaced away
        self.assertEqual(self._val("GCPad1", "Device"), "SDL/0/Test Pad")  # GCPad1 untouched

    def test_profile_selector_value_stays_and_reverts(self):
        # regression for the on-device bug: after a pick, input_get must return it as the selector
        # value (no snap-back to "—"), and "— pick —" reverts the port to its saved mapping.
        orig = gi.dolphin_profiles.profile_body
        self.addCleanup(lambda: setattr(gi.dolphin_profiles, "profile_body", orig))
        gi.dolphin_profiles.profile_body = lambda name: "Device = SDL/9/Fake\nButtons/A = `Button S`\n"

        def a_val(r):
            return next(b for g in r["groups"] for b in g["binds"] if b["id"] == "Buttons/A")["value"]

        gi._selector_set({"player": "2", "key": "profile", "value": "Fake"})
        r = gi._input_get({"player": "2"})
        self.assertEqual(r["selectors"][0]["value"], "Fake")   # stepper stays on the pick
        self.assertEqual(a_val(r), "Button S")                 # bind rows refreshed to the profile
        gi._selector_set({"player": "2", "key": "profile", "value": ""})   # "— pick —"
        r2 = gi._input_get({"player": "2"})
        self.assertEqual(r2["selectors"][0]["value"], "")
        self.assertEqual(a_val(r2), "EAST")                    # reverted to the resting mapping
        self.assertFalse(gi._buf.dirty)                        # revert == no change to save

    def test_profile_selector_and_rows_persist_after_save(self):
        # after Save: the picker keeps showing the loaded profile AND the bind rows reflect it.
        orig = gi.dolphin_profiles.profile_body
        self.addCleanup(lambda: setattr(gi.dolphin_profiles, "profile_body", orig))
        gi.dolphin_profiles.profile_body = lambda name: (
            "Device = SDL/9/Fake\nButtons/A = `Button N`\nButtons/B = `Button S`\n")
        gi._selector_set({"player": "2", "key": "profile", "value": "Fake"})
        gi._input_save({})
        r = gi._input_get({"player": "2"})
        self.assertEqual(r["selectors"][0]["value"], "Fake")            # picker still shows it
        a = next(b for g in r["groups"] for b in g["binds"] if b["id"] == "Buttons/A")["value"]
        self.assertEqual(a, "Button N")                                 # rows reflect the loaded profile

    def test_profile_pick_sticks_even_when_noop(self):
        # picking a profile a port ALREADY matches (no-op, buffer stays non-dirty) must still show
        # the pick — not snap back to "— pick —" via the non-dirty buffer reload.
        span = cfgutil._ini_span(self.tmp.read_text(), "GCPad2")
        cur_body = self.tmp.read_text()[span[0]:span[1]]
        orig = gi.dolphin_profiles.profile_body
        self.addCleanup(lambda: setattr(gi.dolphin_profiles, "profile_body", orig))
        gi.dolphin_profiles.profile_body = lambda name: cur_body        # a profile == the current port
        gi._selector_set({"player": "2", "key": "profile", "value": "Same"})
        self.assertFalse(gi._buf.dirty)                                 # no-op load
        self.assertEqual(gi._input_get({"player": "2"})["selectors"][0]["value"], "Same")

    def test_unmappable_code_rejected(self):
        with self.assertRaises(RpcError):
            gi._input_set({"player": "2", "id": "Buttons/A", "kind": "btn", "value": 0x2FF})

    def test_running_refuses(self):
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            self._remap(2, "Buttons/A", SOUTH)
        self.assertEqual(cm.exception.code, "EBUSY")

    # -- Start-to-clear --------------------------------------------------------
    def test_clear_blanks_binding(self):
        gi._input_clear({"player": "2", "id": "Buttons/A"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "")   # unbound
        # only that line changed; Device/others intact
        self.assertIn("Device = evdev/1/Test Pad", self.tmp.read_text())
        self.assertEqual(self._val("GCPad2", "Buttons/B"), "SOUTH")

    def test_get_advertises_clearable(self):
        self.assertTrue(gi._input_get({})["clearable"])

    def test_load_consumes_orphaned_dock_backup(self):
        # A crash-orphaned undocked swap: GCPadNew.ini holds the transient profile + a _BACKUP of the
        # resting config. Opening the input page must restore the resting config first (edits land on it).
        from lib import dolphin_gc_dock as dk
        orig = (dk._FILE, dk._BACKUP)
        self.addCleanup(lambda: (setattr(dk, "_FILE", orig[0]), setattr(dk, "_BACKUP", orig[1])))
        dk._FILE = self.tmp
        dk._BACKUP = self.tmp.parent / "GCPadNew.ini.dock-backup"
        resting = self.tmp.read_text()
        dk._BACKUP.write_text(resting)                                   # snapshot of the resting config
        self.tmp.write_text(resting.replace("Buttons/A = EAST", "Buttons/A = SOUTH"))  # transient swap
        gi._buf.reset()
        gi._input_get({"player": "2"})                                   # load -> should restore resting
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "EAST")
        self.assertFalse(dk._BACKUP.is_file())

    def test_clear_works_on_stick(self):
        gi._input_clear({"player": "2", "id": "Main Stick/Up"})
        gi._input_save({})
        self.assertEqual(self._val("GCPad2", "Main Stick/Up"), "")

    # -- get -------------------------------------------------------------------
    def test_get_players_capturable_and_selector(self):
        r = gi._input_get({})
        self.assertEqual([p["id"] for p in r["players"]], ["1", "2"])
        self.assertTrue(all(b["capturable"] for g in r["groups"] for b in g["binds"]))
        self.assertIn("D-pad", [g["title"] for g in r["groups"]])    # now a normal (capturable) group
        self.assertEqual(r["selectors"][0]["key"], "profile")        # profile selector present

    def test_compound_binding_shown_verbatim(self):
        # A compound OR binding must not be mangled into a stray-backtick string.
        self.tmp.write_text(_FIXTURE.replace("Buttons/Z = Back", "Buttons/Z = `Shoulder R`|Back"))
        gi._buf.reset()
        r = gi._input_get({"player": "1"})
        z = next(b for g in r["groups"] for b in g["binds"] if b["id"] == "Buttons/Z")
        self.assertEqual(z["value"], "`Shoulder R`|Back")     # verbatim, not "Shoulder R`|Back"

    def test_crlf_not_wholesale_translated(self):
        # The review finding: reading via Path.read_text() translated EVERY CRLF->LF, so a
        # single remap rewrote all line endings. cfgutil.read_text (newline="") fixes that --
        # UNEDITED lines keep CRLF. (cfgutil.ini_replace still drops the CR on the one edited
        # line; that pre-existing 1-byte quirk affects every emulator writer and Dolphin uses
        # LF anyway, so at most one lone LF is acceptable.)
        self.tmp.write_text(_FIXTURE.replace("\n", "\r\n"), newline="")
        gi._buf.reset()
        self._remap(2, "Buttons/A", SOUTH)
        raw = self.tmp.read_bytes()
        self.assertGreaterEqual(raw.count(b"\r\n"), raw.count(b"\n") - 1)  # only the edited line may lose CR
        self.assertEqual(self._val("GCPad2", "Buttons/A"), "SOUTH")        # and the remap still applied


if __name__ == "__main__":
    unittest.main()
