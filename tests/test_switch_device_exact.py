"""Device-exact Switch input numbering (C1/C2) on the shared Yuzu-fork page (eden + citron).

The page reads each pad's OWN SDL index straight from its clean device template
(~/.config/<emu>/input/*.ini) instead of guessing one of two static "gc"/"raw" tables:

  * C2 -- the Steam Deck built-in pad's real layout (L3=button:9, Minus=6, X=button:3) matches
    NEITHER static table; a capture must write the template's exact index, and a stored index must
    label back from the template (L3, not the raw-table "Start").
  * C1 -- a GameController pad with NO matching template is still recognised "gc" from its LIVE
    [Controls] block (button_lstick=button:7), so a remap writes a GameController index (button:9
    for L1), not the corrupting raw-joystick rank (button:4).
  * A fully-templated DualSense is UNCHANGED for capture (device-exact == the old gc table) but its
    face labels are now correct (button:1 -> "A", not the old swapped "B").

Both emulators share one module, so every case runs against Citron AND Eden.

Run:  python3 -m unittest tests.test_switch_device_exact -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib.madsrv import cfgutil, citron_input_cmds, eden_input_cmds, rpc

# guid -> vid:pid : Deck 28de:11ff, DualSense 054c:0ce6, "variant DS4" 054c:05c4 (no template here)
G_DECK = "03000000de280000ff11000001000000"
G_DS = "030000004c050000e60c000000006800"
G_VDS4 = "030000004c050000c405000000006800"


def _tmpl(guid: str, vals: dict) -> str:
    lines = ["[Controls]"]
    for func, tok in vals.items():
        lines.append(f"{func}\\default=false")
        lines.append(f'{func}="engine:sdl,port:0,guid:{guid},{tok}"')
    return "\n".join(lines) + "\n"


# Real Handheld.ini layout: L3=9 (raw table says 11), Minus=6 (raw says 8), X=button:3 (raw North
# says 2), rstick axis_x:3 (unusual), ZL=axis:2.
_DECK_TMPL = _tmpl(G_DECK, {
    "button_a": "button:1", "button_b": "button:0", "button_x": "button:3", "button_y": "button:2",
    "button_l": "button:4", "button_r": "button:5", "button_zl": "axis:2", "button_zr": "axis:5",
    "button_minus": "button:6", "button_plus": "button:7",
    "button_lstick": "button:9", "button_rstick": "button:10", "button_home": "button:8",
    "lstick": "axis_x:0,axis_y:1", "rstick": "axis_x:3,axis_y:4",
})
# DualSense: GameController numbering (L3=7); face A=button:1.
_DS_TMPL = _tmpl(G_DS, {
    "button_a": "button:1", "button_l": "button:9", "button_lstick": "button:7",
    "button_zl": "axis:4", "lstick": "axis_x:0,axis_y:1", "rstick": "axis_x:2,axis_y:3",
})


def _config() -> str:
    def line(pl, key, val):
        return f'{pl}_{key}\\default=false\n{pl}_{key}="engine:sdl,port:0,guid:{val}"\n'
    return (
        "[Controls]\n"
        # P1 = Steam Deck built-in (C2): a placeholder button_x + the real L3/Minus values.
        + line("player_0", "button_x", f"{G_DECK},button:0")
        + line("player_0", "button_lstick", f"{G_DECK},button:9")
        + line("player_0", "button_minus", f"{G_DECK},button:6")
        # P2 = untemplated GameController pad (C1): live L3 = button:7 marks it "gc".
        + line("player_1", "button_l", f"{G_VDS4},button:0")
        + line("player_1", "button_lstick", f"{G_VDS4},button:7")
        # P3 = fully-templated DualSense: A = button:1.
        + line("player_2", "button_a", f"{G_DS},button:1")
        + line("player_2", "button_lstick", f"{G_DS},button:7")
        # P4 = untemplated GC pad, label-stability: L-stick-click + a sibling shoulder, both gc.
        + line("player_3", "button_lstick", f"{G_VDS4},button:7")
        + line("player_3", "button_r", f"{G_VDS4},button:10")
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
        (inp / "Handheld.ini").write_text(_DECK_TMPL, newline="")
        (inp / "DS 1.ini").write_text(_DS_TMPL, newline="")   # NO template for G_VDS4 (054c:05c4)
        self._orig = self.MOD._FILE
        self.MOD._FILE = self.ini
        self.MOD._buf.reset()
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
        r = self._call("input_set", **params)
        self._call("input_save")
        return r

    def _disk(self, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", key) or ""

    def _shown(self, player, key):
        p = self._call("input_get", player=player)
        for g in p["groups"]:
            for b in g["binds"]:
                if b["id"] == key:
                    return b["value"]
        return None

    # ── C2: Steam Deck built-in, template-exact (matches neither static table) ──
    def test_deck_button_x_uses_template_index_not_raw(self):
        # press North (BTN_NORTH 0x133 = Switch X) -> Deck template button_x = button:3
        # (the static raw table would wrongly give North = button:2).
        self._set(id="button_x", kind="btn", value=0x133, player="player_0")
        v = self._disk("player_0_button_x")
        self.assertIn("button:3", v)
        self.assertNotIn("button:2", v)
        self.assertNotIn("button:0", v)          # actively recomputed, not left at the placeholder

    def test_deck_l3_uses_template_index_not_raw(self):
        # press L3 (BTN_THUMBL 0x13d) -> Deck template button_lstick = button:9 (raw table says 11).
        self._set(id="button_lstick", kind="btn", value=0x13D, player="player_0")
        v = self._disk("player_0_button_lstick")
        self.assertIn("button:9", v)
        self.assertNotIn("button:11", v)

    def test_deck_l3_labels_from_template_not_start(self):
        # stored button:9 on the Deck must read "L3", not the raw-table label "Start".
        self.assertEqual(self._shown("player_0", "button_lstick"), "L3")

    def test_deck_minus_labels_from_template(self):
        self.assertEqual(self._shown("player_0", "button_minus"), "Minus")

    # ── C1: untemplated GameController pad recognised from its live block ──
    def test_untemplated_gc_pad_uses_gc_index_not_raw(self):
        # variant DS4 has no template; its live L3 = button:7 marks it "gc". Press L1 (BTN_TL 0x136)
        # -> GameController LeftShoulder = button:9, NOT the raw-joystick rank button:4 (the C1
        # corruption: raw indices written into a GameController block).
        self._set(id="button_l", kind="btn", value=0x136, player="player_1")
        v = self._disk("player_1_button_l")
        self.assertIn("button:9", v)
        self.assertNotIn("button:4", v)

    # ── fully-templated DualSense: capture unchanged, labels corrected ──
    def test_ds_button_a_capture_unchanged(self):
        # press East (BTN_EAST 0x131 = Switch A) -> DS template button_a = button:1 (== the old gc
        # table result, so no regression for the pads actually in use).
        self._set(id="button_a", kind="btn", value=0x131, player="player_2")
        self.assertIn("button:1", self._disk("player_2_button_a"))

    def test_ds_button_a_label_is_a_not_swapped_b(self):
        # the face-label swap wart is gone: button:1 on the A row reads "A", not the old xemu "B".
        self.assertEqual(self._shown("player_2", "button_a"), "A")

    # ── review finding: editing the scheme-discriminator row must not flip labels ──
    def test_untemplated_lstick_remap_keeps_labels_consistent(self):
        # On an untemplated GC pad, remap the L-stick-click row by pressing Start (0x13B). Under the
        # pad's gc scheme index 6 = "Start"; the echo + every subsequent label must agree with the
        # written byte, and a sibling row must NOT flip gc->raw. Regression for the circular
        # single-row scheme discriminator (echoed "L2" / relabelled siblings to raw).
        r = self._call("input_set", id="button_lstick", kind="btn", value=0x13B, player="player_3")
        self.assertEqual(r["value"], "Start")                     # not the raw-table "L2"
        self.assertEqual(self._shown("player_3", "button_lstick"), "Start")
        self.assertEqual(self._shown("player_3", "button_r"), "R (RB)")   # sibling did not flip
        self._call("input_save")
        self.assertIn("button:6", self._disk("player_3_button_lstick"))   # byte is the gc index
        self.assertEqual(self._shown("player_3", "button_lstick"), "Start")
        self.assertEqual(self._shown("player_3", "button_r"), "R (RB)")


class Citron(_Base, unittest.TestCase):
    MOD = citron_input_cmds
    EMU = "citron"


class Eden(_Base, unittest.TestCase):
    MOD = eden_input_cmds
    EMU = "eden"


if __name__ == "__main__":
    unittest.main()
