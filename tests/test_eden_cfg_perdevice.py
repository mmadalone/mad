"""Per-device binding STRUCTURE at Switch pad assignment (eden_cfg, shared by Citron + Eden).

Regression guard for the bug where a hat-d-pad pad (DualSense/DS4/Deck/Xbox) landed on a slot
last held by a button-d-pad pad (the Wii U Pro adapter) and inherited its dead `button:13` d-pad
(+ `button:6/7` ZL/ZR) via `_retarget` (which only swaps guid/port). The fix gives each pad the
block that MATCHES its guid: hat d-pad + axis triggers for modern pads, plain buttons for the
Wii U Pro; an untemplated pad gets the hat+axis default, never the Wii-U button layout.

Run:  python3 -m unittest tests.test_eden_cfg_perdevice -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from lib import eden_cfg
from lib.madsrv import cfgutil

Dev = namedtuple("Dev", "vidpid guid name index player_index")

# no-CRC on-disk guids (idempotent through _eden_guid_sdl, so a test can pass them directly)
G_DS = "050000004c050000e60c000000006800"    # DualSense  054c:0ce6  (hat d-pad, axis triggers)
G_WIIU = "050000007e0500003003000001000000"  # Wii U Pro  057e:0330  (button d-pad + triggers)
G_XBOX = "030000005e0400008e02000010010000"  # Xbox 360   045e:02a1  (NO template / resting block)

DS = Dev("054c:0ce6", G_DS, "DualSense", 0, 0)
WIIU = Dev("057e:0330", G_WIIU, "WiiU Pro", 1, 1)
XBOX = Dev("045e:02a1", G_XBOX, "Xbox360", 2, 2)


def _wiiu_block(pl: str) -> str:
    # button d-pad + button triggers (the Wii U Pro adapter's structure)
    return (
        f"{pl}_button_a=button:1,guid:{G_WIIU},port:0,engine:sdl\n"
        f"{pl}_button_zl=button:6,guid:{G_WIIU},port:0,engine:sdl\n"
        f"{pl}_button_zr=button:7,guid:{G_WIIU},port:0,engine:sdl\n"
        f"{pl}_button_dup=button:13,guid:{G_WIIU},port:0,engine:sdl\n"
        f"{pl}_lstick=axis_x:0,axis_y:1,guid:{G_WIIU},port:0,engine:sdl\n"
    )


def _ds_block(pl: str) -> str:
    # hat d-pad + axis triggers (DualSense structure)
    return (
        f"{pl}_button_a=engine:sdl,port:0,guid:{G_DS},button:0\n"
        f"{pl}_button_zl=engine:sdl,invert:+,port:0,guid:{G_DS},axis:4,threshold:0.500000\n"
        f"{pl}_button_zr=engine:sdl,invert:+,port:0,guid:{G_DS},axis:5,threshold:0.500000\n"
        f"{pl}_button_dup=engine:sdl,port:0,guid:{G_DS},direction:up,hat:0\n"
        f"{pl}_lstick=engine:sdl,port:0,guid:{G_DS},axis_x:0,axis_y:1\n"
    )


class PerDeviceStructure(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        # Resting config: Wii U Pro on P1 (button d-pad), DualSense on P3 (hat d-pad).
        self.ini.write_text(
            "[Controls]\n" + _wiiu_block("player_0") + _ds_block("player_2") + "\n",
            newline="")
        self.inputdir = self.d / "input"
        self.inputdir.mkdir()
        # A nonexistent template file whose PARENT (the input dir) is what harvest scans.
        self.tmpl = str(self.inputdir / "none.ini")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _assign(self, pads, manage=2):
        eden_cfg.assign_devices(pads, ini_path=str(self.ini),
                                template_path=self.tmpl, manage=manage)
        return self.ini.read_text(newline="")

    def _val(self, text, pl, key):
        return cfgutil.ini_read(text, "Controls", f"{pl}_{key}") or ""

    def test_ds_on_wiiu_slot_gets_hat_dpad(self):
        # The bug: DS lands on slot 0 (which held Wii-U button tokens). It must get its OWN
        # hat d-pad + axis triggers, NOT button:13 / button:6.
        text = self._assign([DS])
        self.assertIn("hat:0", self._val(text, "player_0", "button_dup"))
        self.assertNotIn("button:13", self._val(text, "player_0", "button_dup"))
        self.assertIn("axis:4", self._val(text, "player_0", "button_zl"))
        self.assertIn("axis:5", self._val(text, "player_0", "button_zr"))
        self.assertIn(f"guid:{G_DS}", self._val(text, "player_0", "button_dup"))

    def test_wiiu_keeps_buttons(self):
        # The Wii U Pro must NOT be regressed onto a hat: it genuinely reports buttons.
        text = self._assign([WIIU])
        self.assertIn("button:13", self._val(text, "player_0", "button_dup"))
        self.assertIn("button:6", self._val(text, "player_0", "button_zl"))
        self.assertNotIn("hat:", self._val(text, "player_0", "button_dup"))
        self.assertIn(f"guid:{G_WIIU}", self._val(text, "player_0", "button_dup"))

    def test_untemplated_pad_gets_hat_default_never_wiiu(self):
        # Xbox has no template and no resting block -> the modern hat+axis default, retargeted
        # to the Xbox guid. Correct for an Xbox (hat d-pad + axis triggers); never button:13.
        text = self._assign([XBOX])
        dup = self._val(text, "player_0", "button_dup")
        self.assertIn("hat:0", dup)
        self.assertNotIn("button:13", dup)
        self.assertIn(f"guid:{G_XBOX}", dup)
        self.assertIn("axis:4", self._val(text, "player_0", "button_zl"))

    def test_two_ds_get_distinct_ports(self):
        text = self._assign([DS, DS], manage=2)
        self.assertIn("port:0", self._val(text, "player_0", "button_dup"))
        self.assertIn("port:1", self._val(text, "player_1", "button_dup"))
        self.assertIn("hat:0", self._val(text, "player_1", "button_dup"))

    def test_second_identical_pad_keeps_its_own_remap(self):
        # Two DualSense pads share one no-CRC guid. P1 rests default; P2 has a CUSTOM remap
        # (button_a -> button:2). The bind must PRESERVE each slot's own binds, not stamp P1's
        # block onto P2 (review finding #2). Without the fix, P2's button:2 would become button:0.
        self.ini.write_text(
            "[Controls]\n"
            f'player_0_button_a="engine:sdl,port:0,guid:{G_DS},button:0"\n'
            f'player_0_button_dup="engine:sdl,port:0,guid:{G_DS},direction:up,hat:0"\n'
            f'player_1_button_a="engine:sdl,port:1,guid:{G_DS},button:2"\n'
            f'player_1_button_dup="engine:sdl,port:1,guid:{G_DS},direction:up,hat:0"\n\n',
            newline="")
        text = self._assign([DS, DS], manage=2)
        self.assertIn("button:2", self._val(text, "player_1", "button_a"))   # P2 custom kept
        self.assertIn("button:0", self._val(text, "player_0", "button_a"))   # P1 default kept


class Helpers(unittest.TestCase):
    def test_guid_to_vidpid(self):
        self.assertEqual(eden_cfg._guid_to_vidpid(G_DS), "054c:0ce6")
        self.assertEqual(eden_cfg._guid_to_vidpid(G_WIIU), "057e:0330")
        self.assertEqual(eden_cfg._guid_to_vidpid("short"), "")

    def test_block_guid_and_modern_default(self):
        by = {
            G_WIIU: {"button_dup": f"button:13,guid:{G_WIIU},port:0,engine:sdl"},
            G_DS: {"button_dup": f"engine:sdl,guid:{G_DS},direction:up,hat:0"},
        }
        # modern default is the hat block, never the Wii-U button block
        self.assertIn("hat:0", eden_cfg._modern_default(by)["button_dup"])
        self.assertEqual(eden_cfg._block_guid(by[G_DS]).lower(), G_DS)
        self.assertEqual(eden_cfg._block_guid({"x": "engine:keyboard,code:67"}), "")
        # no hat-style block anywhere -> None (caller falls back to live/template)
        self.assertIsNone(eden_cfg._modern_default({G_WIIU: by[G_WIIU]}))

    def test_resolve_block_prefers_exact_then_vidpid_then_default(self):
        by = {G_DS: {"button_dup": f"guid:{G_DS},hat:0,direction:up"}}
        # exact guid
        self.assertIn("hat:0", eden_cfg._resolve_block(by, G_DS, "054c:0ce6", {})["button_dup"])
        # a USB-vs-BT guid variant (bus byte differs) resolves by vid:pid
        usb_ds = "030000004c050000e60c000000006800"
        self.assertIn("hat:0", eden_cfg._resolve_block(by, usb_ds, "054c:0ce6", {})["button_dup"])
        # unknown guid + unknown vidpid -> modern default (the only hat block)
        self.assertIn("hat:0", eden_cfg._resolve_block(by, G_XBOX, "045e:02a1", {})["button_dup"])


if __name__ == "__main__":
    unittest.main()
