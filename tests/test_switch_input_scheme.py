"""Per-device numbering scheme on the Switch input page (eden + citron, byte-clones).

The page used to capture + label every pad in SDL *joystick* rank (L3=button:11), but Eden/Citron
open a DualSense/DS4 as an SDL *GameController* (L3=button:7, ZL=axis:4, d-pad=button:11), so a
DualSense d-pad read "L3" and a remap wrote raw indices onto a GameController pad (corrupting it).
These lock in: the scheme is taken from the device's CLEAN input template (not the corruptible live
block); a GameController pad captures/labels in GameController numbering, a raw pad (Wii U) in
joystick rank; and the page advertises `clearable` so Start-to-clear fires.

Run:  python3 -m unittest tests.test_switch_input_scheme -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil, citron_input_cmds, eden_input_cmds, rpc

G_DS = "050000004c050000e60c000000006800"       # DualSense, connects over Bluetooth (bus 05)
G_DS_USB = "030000004c050000e60c000000006800"   # its template's bus-03 guid (same vid:pid)
G_WIIU = "050000007e0500003003000001000000"     # Wii U Pro adapter (raw joystick)


def _gc_template() -> str:      # DualSense: L3=button:7, d-pad up=button:11 => GameController scheme
    return ("[Controls]\n"
            f"button_lstick=engine:sdl,port:0,guid:{G_DS_USB},button:7\n"
            f"button_dup=engine:sdl,port:0,guid:{G_DS_USB},button:11\n")


def _raw_template() -> str:     # Wii U Pro: L3=button:11, d-pad up=button:13 => raw joystick scheme
    return ("[Controls]\n"
            f"button_lstick=button:11,guid:{G_WIIU},port:0,engine:sdl\n"
            f"button_dup=button:13,guid:{G_WIIU},port:0,engine:sdl\n")


def _config() -> str:
    def line(pl, key, val):
        return f'{pl}_{key}\\default=false\n{pl}_{key}="{val}"\n'
    return (
        "[Controls]\n"
        # P1 = DualSense (GameController numbering as Eden/Citron record it)
        + line("player_0", "button_l", f"engine:sdl,port:0,guid:{G_DS},button:9")
        + line("player_0", "button_zl", f"engine:sdl,invert:+,port:0,guid:{G_DS},axis:4,threshold:0.500000")
        + line("player_0", "button_dup", f"engine:sdl,port:0,guid:{G_DS},button:11")
        + line("player_0", "lstick", f"engine:sdl,port:0,guid:{G_DS},axis_x:0,axis_y:1")
        # P2 = Wii U Pro (raw joystick rank)
        + line("player_1", "button_l", f"button:4,guid:{G_WIIU},port:0,engine:sdl")
        + line("player_1", "button_dup", f"button:13,guid:{G_WIIU},port:0,engine:sdl")
        + "\n[System]\nuse_docked_mode\\default=true\nuse_docked_mode=1\n"
    )


class _Base:
    MOD = None
    EMU = ""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(_config(), newline="")
        inp = self.d / "input"; inp.mkdir()
        (inp / "DS 1.ini").write_text(_gc_template(), newline="")
        (inp / "WiiU Pro 1.ini").write_text(_raw_template(), newline="")
        self._orig = self.MOD._FILE
        self.MOD._FILE = self.ini
        self.MOD._buf.reset()          # fresh buffer per case (the buffer is a module-level singleton)
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        self.MOD._FILE = self._orig
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, verb, **params):
        return rpc._METHODS[f"{self.EMU}.{verb}"][0](params)

    def _set(self, **params):
        """Stage a capture then Save. The buffered editor only writes disk on save, so a
        test that asserts on file content must commit first."""
        r = self._call("input_set", **params)
        self._call("input_save")
        return r

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", key) or ""

    def _shown_row(self, payload, key):
        for g in payload["groups"]:
            for b in g["binds"]:
                if b["id"] == key:
                    return b["value"]
        return None

    # ── scheme detection (from the clean template, by vid:pid) ───────────────
    def test_scheme_from_template(self):
        self.assertEqual(self.MOD._scheme(G_DS), "gc")      # DS template L3=button:7
        self.assertEqual(self.MOD._scheme(G_WIIU), "raw")   # Wii U template L3=button:11

    # ── capture writes the right per-scheme index ────────────────────────────
    def test_gc_button_capture_uses_gamecontroller_index(self):
        # physical L1 (BTN_TL 0x136) on the DualSense -> GameController LeftShoulder = button:9
        self._set(id="button_l", kind="btn", value=0x136, player="player_0")
        self.assertIn("button:9", self._disk("player_0_button_l"))

    def test_raw_button_capture_uses_joystick_rank(self):
        # physical L1 on the Wii U Pro -> raw joystick rank = button:4
        self._set(id="button_l", kind="btn", value=0x136, player="player_1")
        self.assertIn("button:4", self._disk("player_1_button_l"))

    def test_gc_trigger_capture_uses_gamecontroller_axis(self):
        # pull L2 on the DualSense -> GameController TriggerLeft = axis:4 (not raw ABS_Z rank 2)
        self._set(id="button_zl", kind="trigger", value="+trigger_left@2", player="player_0")
        self.assertIn("axis:4", self._disk("player_0_button_zl"))

    # ── display labels in the pad's own scheme ───────────────────────────────
    def test_gc_dpad_displays_as_dpad_not_l3(self):
        p = self._call("input_get", player="player_0")
        self.assertEqual(self._shown_row(p, "button_dup"), "D-Up")   # never "L3"

    def test_gc_l3_label(self):
        # button_lstick stored as button:11 on a GC pad? no -- the DS L3 is button:7; label it "L3".
        # Prove the GC label map is used: player_0 button_l is button:9 -> "L (LB)", not raw "L1".
        p = self._call("input_get", player="player_0")
        self.assertEqual(self._shown_row(p, "button_l"), "L (LB)")

    def test_raw_label_uses_joystick_map(self):
        p = self._call("input_get", player="player_1")
        self.assertEqual(self._shown_row(p, "button_l"), "L1")       # raw button:4 -> "L1"

    # ── Start-to-clear flag ──────────────────────────────────────────────────
    def test_input_get_advertises_clearable(self):
        self.assertTrue(self._call("input_get", player="player_0").get("clearable"))

    # ── the guard keys on the PLAYER, not the single binding (Start-clear interaction) ──
    def test_rebind_after_clear(self):
        # button_rstick was CLEARED to [empty] but the player still has a pad (button_l). Re-binding
        # must succeed -- the guard must not read the cleared binding as 'no pad'. Guards the bug where
        # Start-clear made a binding un-rebindable.
        self.ini.write_text(
            "[Controls]\n"
            f'player_0_button_l\\default=false\nplayer_0_button_l="engine:sdl,port:0,guid:{G_DS},button:9"\n'
            'player_0_button_rstick\\default=false\nplayer_0_button_rstick=[empty]\n', newline="")
        self._set(id="button_rstick", kind="btn", value=0x13e, player="player_0")  # R3
        v = self._disk("player_0_button_rstick")
        self.assertIn("button:", v)
        self.assertIn(f"guid:{G_DS}", v)
        # C4/C5 regression: the re-created binding must be a VALID QUOTED value (Eden/Citron quote
        # theirs), not the unquoted/malformed string the first skeleton attempt produced.
        self.assertTrue(v.startswith('"') and v.endswith('"'), f"re-bound value must be quoted, got {v!r}")

    def test_genuinely_empty_player_still_errors(self):
        from lib.madsrv import rpc as _rpc
        with self.assertRaises(_rpc.RpcError):
            self._call("input_set", id="button_a", kind="btn", value=0x130, player="player_7")

    # ── buffered editor: stage in memory, commit on Save, revert on Cancel ────
    def test_stage_then_save_commits(self):
        self.assertIn("button:9", self._disk("player_0_button_l"))       # initial on-disk value
        p = self._call("input_set", id="button_l", kind="btn", value=0x130, player="player_0")  # A -> GC button:0
        self.assertTrue(p["dirty"])                                      # response reports it is staged
        self.assertIn("button:9", self._disk("player_0_button_l"))       # NOT written to disk yet
        self.assertTrue(self._call("input_get", player="player_0")["dirty"])
        self._call("input_save")
        self.assertIn("button:0", self._disk("player_0_button_l"))       # committed on save
        self.assertFalse(self._call("input_get", player="player_0")["dirty"])

    def test_stage_then_cancel_reverts(self):
        self._call("input_set", id="button_l", kind="btn", value=0x130, player="player_0")
        self.assertIn("button:9", self._disk("player_0_button_l"))       # unchanged while staged
        self._call("input_cancel")
        self.assertIn("button:9", self._disk("player_0_button_l"))       # discard leaves disk untouched
        self.assertFalse(self._call("input_get", player="player_0")["dirty"])

    def test_buffered_flag_advertised(self):
        self.assertTrue(self._call("input_get", player="player_0").get("buffered"))


class Citron(_Base, unittest.TestCase):
    MOD = citron_input_cmds
    EMU = "citron"


class Eden(_Base, unittest.TestCase):
    MOD = eden_input_cmds
    EMU = "eden"


if __name__ == "__main__":
    unittest.main()
