"""Per-device binding STRUCTURE at Switch pad assignment (eden_cfg, shared by Citron + Eden).

Regression guard for the bug where a hat-d-pad pad (DualSense/DS4/Deck/Xbox) landed on a slot
last held by a button-d-pad pad (the Wii U Pro adapter) and inherited its dead `button:13` d-pad
(+ `button:6/7` ZL/ZR) via `_retarget` (which only swaps guid/port). The fix gives each pad the
block that MATCHES its guid: hat d-pad + axis triggers for modern pads, plain buttons for the
Wii U Pro; an untemplated pad gets the hat+axis default, never the Wii-U button layout.

Run:  python3 -m unittest tests.test_eden_cfg_perdevice -v
"""
from __future__ import annotations

import re
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

G_DS_USB = "030000004c050000e60c000000006800"   # DS template on USB bus; the pad connects as G_DS (BT)


def _dpad_block(pl: str, guid: str, base: int) -> str:
    keys = ("button_dup", "button_ddown", "button_dleft", "button_dright")
    return "".join(f"{pl}_{k}=engine:sdl,port:0,guid:{guid},button:{base + i}\n"
                   for i, k in enumerate(keys))


def _ds_dpad_template(base: int) -> str:
    keys = ("button_dup", "button_ddown", "button_dleft", "button_dright")
    return "[Controls]\n" + "".join(f"{k}=engine:sdl,port:0,guid:{G_DS_USB},button:{base + i}\n"
                                    for i, k in enumerate(keys))


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


class DpadSelfHeal(unittest.TestCase):
    """A poisoned resting d-pad (a foreign base a buggy remap stamped on) self-heals at launch from
    the device template; a legit in-range remap is preserved. Guards eden_cfg._heal_dpad + the
    assign_devices heal on BOTH poison paths (the `own` same-slot branch and _resolve_block)."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.inp = self.d / "input"
        self.inp.mkdir()
        (self.inp / "DS 1.ini").write_text(_ds_dpad_template(11), newline="")   # DS base 11, USB guid
        self.tmpl = str(self.inp / "none.ini")   # nonexistent default -> harvest scans self.inp

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _dup_after_assign(self, resting: str) -> str:
        self.ini.write_text("[Controls]\n" + resting + "\n", newline="")
        eden_cfg.assign_devices([DS], ini_path=str(self.ini), template_path=self.tmpl, manage=2)
        return cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", "player_0_button_dup") or ""

    def test_poison_via_resolve_heals(self):
        # Resting DS block carries the USB guid + a full Wii-U-base d-pad (13..16); the connected DS is
        # BT, so own-guid != connected -> _resolve_block vid:pid path returns the poison -> healed to 11.
        dup = self._dup_after_assign(_dpad_block("player_0", G_DS_USB, 13))
        self.assertIn("button:11", dup)
        self.assertNotIn("button:16", dup)
        self.assertIn(f"guid:{G_DS_USB}", dup)  # written as Eden's canonical bus-03 guid, not raw BT bus-05

    def test_poison_via_own_branch_heals(self):
        # Poison under the CONNECTED (BT) guid -> the `own` same-slot branch is taken; still heals.
        dup = self._dup_after_assign(_dpad_block("player_0", G_DS, 13))
        self.assertIn("button:11", dup)
        self.assertNotIn("button:16", dup)

    def test_legit_inrange_remap_preserved(self):
        # An in-range permuted d-pad (all within the DS's {11,12,13,14}) is NOT foreign -> preserved.
        keys = [("button_dup", 14), ("button_ddown", 11), ("button_dleft", 12), ("button_dright", 13)]
        resting = "".join(f"player_0_{k}=engine:sdl,port:0,guid:{G_DS},button:{b}\n" for k, b in keys)
        self.assertIn("button:14", self._dup_after_assign(resting))

    def test_legit_facebutton_crossmap_preserved(self):
        # A d-pad direction legitimately cross-mapped (in Eden's own GUI) to a face button the DS HAS
        # (button:0, OUTSIDE the 4 d-pad indices) is NOT poison -> preserved, not healed. Foreign is
        # judged against the device's FULL button range, so a button the template maps stays legal.
        (self.inp / "DS 1.ini").write_text(
            "[Controls]\n"
            f"button_a=engine:sdl,port:0,guid:{G_DS_USB},button:0\n"
            f"button_dup=engine:sdl,port:0,guid:{G_DS_USB},button:11\n"
            f"button_ddown=engine:sdl,port:0,guid:{G_DS_USB},button:12\n"
            f"button_dleft=engine:sdl,port:0,guid:{G_DS_USB},button:13\n"
            f"button_dright=engine:sdl,port:0,guid:{G_DS_USB},button:14\n", newline="")
        keys = [("button_dup", 0), ("button_ddown", 12), ("button_dleft", 13), ("button_dright", 14)]
        resting = "".join(f"player_0_{k}=engine:sdl,port:0,guid:{G_DS},button:{b}\n" for k, b in keys)
        self.assertIn("button:0", self._dup_after_assign(resting))   # cross-map kept, not healed to 11


# Bus-03 (GameController-canonical) forms Eden records; the pads connect over Bluetooth (bus 05).
G_DS4_USB = "030000004c050000cc09000000006800"                 # DualShock 4 054c:09cc, Eden's bus-03 form
DS_BT   = Dev("054c:0ce6", "050057564c050000e60c000000006800", "DualSense", 0, 0)
DS4_BT  = Dev("054c:09cc", "05008fe54c050000cc09000000006800", "PS4 Controller", 1, 0)
WIIU_BT = Dev("057e:0330", "0500a9177e0500003003000001000000", "WiiU Pro", 2, 0)


class GuidBusCanonicalization(unittest.TestCase):
    """Eden's SDL canonicalizes a DualSense/DS4 to a bus-03 GameController guid even over Bluetooth,
    so the launch binder must write THAT guid (what Eden matches) -- not the raw bus-05 connection
    guid -- or the DS/DS4 read dead in-game while the Wii U Pro (bus-05 both sides) works. This is
    the CONFIRMED real Eden cause, distinct from the earlier d-pad-structure fixes. Verified live:
    binder wrote `05..` while Eden's own config held `03..`."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ini = self.d / "qt-config.ini"
        self.inp = self.d / "input"; self.inp.mkdir()
        self.tmpl = str(self.inp / "none.ini")   # nonexistent default -> harvest scans self.inp

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _out_guid(self, resting, pads, pl="player_0", manage=2):
        self.ini.write_text("[Controls]\n" + resting + "\n", newline="")
        eden_cfg.assign_devices(pads, ini_path=str(self.ini), template_path=self.tmpl, manage=manage)
        v = cfgutil.ini_read(self.ini.read_text(newline=""), "Controls", f"{pl}_button_dup") or ""
        m = re.search(r"guid:([0-9a-fA-F]+)", v)
        return m.group(1).lower() if m else ""

    def test_ds_written_as_emulator_bus03_not_raw_bus05(self):
        # Resting DS recorded at bus 03 (Eden's form); the pad connects over Bluetooth (bus 05).
        g = self._out_guid(_dpad_block("player_0", G_DS_USB, 11), [DS_BT])
        self.assertEqual(g[:2], "03", f"DS must keep Eden's bus-03 guid, got bus {g[:2]} ({g})")
        self.assertEqual(g, G_DS_USB)

    def test_ds4_written_as_emulator_bus03(self):
        g = self._out_guid(_dpad_block("player_0", G_DS4_USB, 11), [DS4_BT])
        self.assertEqual(g[:2], "03", f"DS4 must keep Eden's bus-03 guid, got bus {g[:2]} ({g})")
        self.assertEqual(g, G_DS4_USB)

    def test_wiiu_stays_bus05(self):
        # Wii U Pro is bus 05 in Eden's records AND on the wire -> unchanged (this always worked).
        g = self._out_guid(_wiiu_block("player_0"), [WIIU_BT])
        self.assertEqual(g[:2], "05", f"Wii U must stay bus-05, got bus {g[:2]} ({g})")

    def test_new_pad_with_no_block_keeps_connected_guid(self):
        # An Xbox with no resting/template block has no emulator-recorded form -> keep the connected
        # guid (retargeted onto the modern hat default); never forced to another device's guid.
        g = self._out_guid(_dpad_block("player_0", G_DS_USB, 11), [XBOX])
        self.assertEqual(g, G_XBOX.lower(), f"a new pad must keep its own connected guid, got {g}")

    def test_two_ds_both_bus03_distinct_ports(self):
        # Two DualSense pads: both get Eden's bus-03 guid (identical -- Eden distinguishes by PORT,
        # the same mechanism that makes the two Wii U pads work), with distinct ports 0/1.
        self.ini.write_text("[Controls]\n" + _dpad_block("player_0", G_DS_USB, 11) + "\n", newline="")
        eden_cfg.assign_devices([DS_BT, DS_BT], ini_path=str(self.ini),
                                template_path=self.tmpl, manage=2)
        text = self.ini.read_text(newline="")
        for pl in ("player_0", "player_1"):
            self.assertIn(f"guid:{G_DS_USB}", cfgutil.ini_read(text, "Controls", f"{pl}_button_dup"))
        self.assertIn("port:0", cfgutil.ini_read(text, "Controls", "player_0_button_dup"))
        self.assertIn("port:1", cfgutil.ini_read(text, "Controls", "player_1_button_dup"))

    def test_stale_bus05_resting_heals_from_bus03_template(self):
        # Review finding #1: the resting config holds a STALE bus-05 DS block (what the OLD binder
        # wrote) while a bus-03 DS template exists. Connecting over Bluetooth (bus 05) must write the
        # template's canonical bus-03, NOT re-derive the stale bus-05 (which would never self-heal).
        (self.inp / "DS 1.ini").write_text(_ds_dpad_template(11), newline="")   # bus-03 template
        stale = _dpad_block("player_0", "050000004c050000e60c000000006800", 11)  # bus-05 stale resting
        g = self._out_guid(stale, [DS_BT])
        self.assertEqual(g, G_DS_USB, f"stale bus-05 resting must heal to the template's bus-03, got {g}")

    def test_raw_live_bus_pad_kept_no_spurious_override(self):
        # Review finding #2 (the Citron regression): a pad with NO bus-03 form anywhere (recorded AND
        # connected both bus-05, i.e. all of Citron, or a Wii U) must stay on its LIVE bus -- the
        # override must not force it to bus-03. Old code kept the live guid; this must too.
        resting = _dpad_block("player_0", "050000004c050000e60c000000006800", 11)  # bus-05 recorded
        g = self._out_guid(resting, [DS_BT])                                       # connects bus-05
        self.assertEqual(g[:2], "05", f"a raw-live-bus pad must stay bus-05 (no override), got {g}")

    def test_usb_connected_pad_left_alone(self):
        # A USB-connected DS is already bus-03 -> no spurious re-resolution, keep the live guid.
        ds_usb = Dev("054c:0ce6", G_DS_USB, "DualSense", 0, 0)
        g = self._out_guid(_dpad_block("player_0", G_DS_USB, 11), [ds_usb])
        self.assertEqual(g, G_DS_USB)


if __name__ == "__main__":
    unittest.main()
