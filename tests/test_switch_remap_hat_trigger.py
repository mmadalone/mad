"""Yuzu-fork (Citron/Eden) per-button remap: hat d-pad + analog-trigger awareness.

Before the fix the page assumed every binding was a plain `button:N`, so it mis-fired the
"no controller" guard on a DualSense's hat d-pad / axis triggers and could only re-point
buttons. These lock in:
  * the guard keys on a DEVICE (`guid:`), not `button:` (so a hat/axis binding is editable);
  * a d-pad remap on a HAT pad re-points `hat:N,direction:D`; on a Wii-U button pad it still
    writes `button:idx`;
  * ZL/ZR expose capture kind `"trigger"` when bound as an axis (DS/DS4/Deck) and `"btn"` when
    bound as a button (Wii U Pro);
  * a `"trigger"` remap re-points `axis:N`, and rejects a stick token.

Run:  python3 -m unittest tests.test_switch_remap_hat_trigger -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import citron_input_cmds, eden_input_cmds
from lib.madsrv import cfgutil, rpc

G_WIIU = "050000007e0500003003000001000000"
G_DS = "050000004c050000e60c000000006800"
G_DS_USB = "030000004c050000e60c000000006800"   # DS template on USB bus; the pad connects as G_DS (BT)


def _template(guid: str, base: int) -> str:
    keys = ("button_dup", "button_ddown", "button_dleft", "button_dright")
    body = "".join(f'{k}\\default=false\n{k}="engine:sdl,port:0,guid:{guid},button:{base + i}"\n'
                   for i, k in enumerate(keys))
    return "[Controls]\n" + body


def _fix() -> str:
    # P1 = DualSense (hat d-pad, axis triggers); P2 = Wii U Pro (button d-pad + triggers).
    def line(pl, key, val):
        return f'{pl}_{key}\\default=false\n{pl}_{key}="{val}"\n'
    return (
        "[Controls]\n"
        + line("player_0", "button_a", f"engine:sdl,port:0,guid:{G_DS},button:0")
        + line("player_0", "button_zl", f"engine:sdl,invert:+,port:0,guid:{G_DS},axis:4,threshold:0.500000")
        + line("player_0", "button_zr", f"engine:sdl,invert:+,port:0,guid:{G_DS},axis:5,threshold:0.500000")
        + line("player_0", "button_dup", f"engine:sdl,port:0,guid:{G_DS},direction:up,hat:0")
        + line("player_0", "lstick", f"engine:sdl,port:0,guid:{G_DS},axis_x:0,axis_y:1,invert_x:+,invert_y:+")
        + line("player_1", "button_dup", f"button:13,guid:{G_WIIU},port:0,engine:sdl")
        + line("player_1", "button_zl", f"button:6,guid:{G_WIIU},port:0,engine:sdl")
        # P3: a guid-ful d-pad stored as an axis (neither hat nor button) -- must not silently no-op.
        + line("player_2", "button_dup", f"engine:sdl,port:0,guid:{G_DS},axis:6")
        # P4: a button-style DS d-pad POISONED with the Wii U base (button:13); it connects on BT
        # (G_DS bus 05) while the DS template is USB (bus 03) -> exercises the cross-bus vid:pid match.
        + line("player_3", "button_dup", f"engine:sdl,port:0,guid:{G_DS},button:13")
        + "\n[System]\nuse_docked_mode\\default=true\nuse_docked_mode=1\n"
    )


class _Base:
    MOD = None
    EMU = ""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.ini.write_text(_fix(), newline="")
        # per-device templates (the remap reads _FILE.parent/"input"): DS base 11 on the USB-bus guid
        # (so a BT-bus DS must match by vid:pid), Wii U base 13.
        inp = self.d / "input"; inp.mkdir()
        (inp / "DS 1.ini").write_text(_template(G_DS_USB, 11), newline="")
        (inp / "WiiU Pro 1.ini").write_text(_template(G_WIIU, 13), newline="")
        self._orig = self.MOD._FILE
        self.MOD._FILE = self.ini
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

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", key) or ""

    def _row_kind(self, payload, key):
        for g in payload["groups"]:
            for b in g["binds"]:
                if b["id"] == key:
                    return b["kind"]
        return None

    # ── d-pad ────────────────────────────────────────────────────────────────
    def test_dpad_hat_remap_repoints_hat(self):
        r = self._call("input_set", id="button_dup", kind="hat", value="h0left", player="player_0")
        v = self._disk("player_0_button_dup")
        self.assertIn("direction:left", v)
        self.assertIn("hat:0", v)
        self.assertNotIn("button:", v)
        self.assertIn(f"guid:{G_DS}", v)
        self.assertIn("→", r["message"])

    def test_dpad_button_remap_on_wiiu_writes_button(self):
        self._call("input_set", id="button_dup", kind="hat", value="h0left", player="player_1")
        v = self._disk("player_1_button_dup")
        self.assertIn("button:15", v)          # Wii U template base 13: left -> 15
        self.assertNotIn("hat:", v)

    def test_dpad_button_remap_on_ds_uses_template_base(self):
        # A button-style DS d-pad poisoned to the Wii U base (button:13): remapping it must write the
        # DS's OWN base (button:11) read from the DS template, matched by vid:pid across BT/USB bus.
        self._call("input_set", id="button_dup", kind="hat", value="h0up", player="player_3")
        v = self._disk("player_3_button_dup")
        self.assertIn("button:11", v)          # DS template base, NOT the Wii U 13
        self.assertNotIn("button:13", v)
        self.assertIn(f"guid:{G_DS}", v)       # device preserved

    # ── guard ────────────────────────────────────────────────────────────────
    def test_guard_no_pad_here(self):
        # player_5 has no bindings at all -> no guid -> the "no pad here" guard.
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="button_a", kind="btn", value=307, player="player_5")

    def test_guard_allows_hat_binding(self):
        # a hat d-pad binding must NOT trip the guard (the old `button:` guard did).
        try:
            self._call("input_set", id="button_dup", kind="hat", value="h0down", player="player_0")
        except rpc.RpcError as e:
            self.fail(f"hat d-pad wrongly rejected: {e}")

    # ── ZL/ZR trigger ────────────────────────────────────────────────────────
    def test_zlzr_kind_is_trigger_for_axis(self):
        p = self._call("input_get", player="player_0")
        self.assertEqual(self._row_kind(p, "button_zl"), "trigger")
        self.assertEqual(self._row_kind(p, "button_zr"), "trigger")

    def test_zlzr_kind_is_btn_for_button_pad(self):
        p = self._call("input_get", player="player_1")
        self.assertEqual(self._row_kind(p, "button_zl"), "btn")

    def test_trigger_remap_repoints_axis(self):
        self._call("input_set", id="button_zl", kind="trigger",
                   value="+trigger_right@5", player="player_0")
        v = self._disk("player_0_button_zl")
        self.assertIn("axis:5", v)
        self.assertIn("threshold:0.500000", v)
        self.assertIn(f"guid:{G_DS}", v)

    def test_trigger_remap_rejects_stick_token(self):
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="button_zl", kind="trigger",
                       value="+left_x@0", player="player_0")

    def test_dpad_axis_binding_errors_not_silent(self):
        # a guid-ful d-pad that is neither hat nor button (an axis) must ERROR, not report a
        # phantom success while writing nothing (review finding #4).
        with self.assertRaises(rpc.RpcError):
            self._call("input_set", id="button_dup", kind="hat", value="h0up", player="player_2")


class Citron(_Base, unittest.TestCase):
    MOD = citron_input_cmds
    EMU = "citron"


class Eden(_Base, unittest.TestCase):
    MOD = eden_input_cmds
    EMU = "eden"


if __name__ == "__main__":
    unittest.main()
