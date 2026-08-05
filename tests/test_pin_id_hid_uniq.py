"""devices.pin_id: fall back to the parent HID device's HID_UNIQ when evdev has no per-unit id.

Some drivers never copy a Bluetooth pad's address onto the input device, so evdev's `uniq` is empty
and the pad resolves model-only -- two of the same model then collide on one pin key and cannot be
told apart. hid-wiimote is one: two Wii U Pro Controllers both read `vidpid:057e:0330` while the
kernel one level up knows their addresses. Measured on this Deck 2026-08-05:

    /sys/devices/virtual/misc/uhid/0005:057E:0330.000F/uevent
        DRIVER=wiimote
        HID_UNIQ=18:2a:7b:46:43:fd      <- this pad
        HID_PHYS=dc:2e:97:2f:0f:30      <- the DECK's adapter, identical for both

These tests build a fake sysfs tree, so they prove the ladder without needing a pad connected.

Run: python3 -m unittest tests.test_pin_id_hid_uniq -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import devices


def _UE(uniq: str, vid: str = "0000057E", pid: str = "00000330") -> str:
    return f"HID_ID=0005:{vid}:{pid}\nHID_UNIQ={uniq}\n"


def _dev(path="/dev/input/event21", uniq="", phys="", vid=0x057E, pid=0x0330):
    return devices.Device(name="Nintendo Wii Remote Pro Controller", path=path, is_joypad=True,
                          is_mouse=False, is_keyboard=False, js_index=0, mouse_index=None,
                          vid=vid, pid=pid, uniq=uniq, phys=phys)


class HidUniqFallback(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._save = devices._SYS_INPUT
        devices._SYS_INPUT = str(self.d / "class-input")
        devices._HID_UNIQ_CACHE.clear()

    def tearDown(self):
        devices._SYS_INPUT = self._save
        devices._HID_UNIQ_CACHE.clear()
        shutil.rmtree(self.d, ignore_errors=True)

    def _node(self, event: str, instance: str, uevent: str) -> None:
        """Lay out one pad the way the kernel does: the input node's `device` symlink points at
        .../uhid/<instance>/input/<inputN>, and the uevent with HID_UNIQ sits TWO levels up."""
        hid = self.d / "devices" / "uhid" / instance
        inp = hid / "input" / f"input{event[-3:]}"
        inp.mkdir(parents=True)
        (hid / "uevent").write_text(uevent, encoding="utf-8")
        link = Path(devices._SYS_INPUT) / event
        link.mkdir(parents=True)
        (link / "device").symlink_to(inp)

    def test_hid_uniq_is_used_when_evdev_has_none(self):
        self._node("event210", "0005:057E:0330.000F",
                   "DRIVER=wiimote\nHID_ID=0005:0000057E:00000330\n"
                   "HID_UNIQ=18:2a:7b:46:43:fd\nHID_PHYS=dc:2e:97:2f:0f:30\n")
        got = devices.pin_id(_dev(path="/dev/input/event210"))
        self.assertEqual(got, "uniq:057e:0330:18:2a:7b:46:43:fd")
        self.assertEqual(devices.pin_kind(got), "uniq")

    def test_two_pads_of_one_model_no_longer_collide(self):
        self._node("event210", "0005:057E:0330.000F", _UE("18:2a:7b:46:43:fd"))
        self._node("event211", "0005:057E:0330.0012", _UE("18:2a:7b:46:43:e9"))
        a = devices.pin_id(_dev(path="/dev/input/event210"))
        b = devices.pin_id(_dev(path="/dev/input/event211"))
        self.assertNotEqual(a, b)          # this is the whole point: bindable per player

    def test_evdev_uniq_still_wins(self):
        # A pad that reports its own address must not be re-resolved through sysfs.
        self._node("event210", "0005:057E:0330.000F", _UE("aa:aa:aa:aa:aa:aa"))
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210", uniq="50:EE:32:52:2B:C6")),
                         "uniq:057e:0330:50:ee:32:52:2b:c6")

    def test_hid_phys_is_never_used_as_an_identity(self):
        # Over Bluetooth HID_PHYS is the Deck's own adapter, so it is the SAME for every pad.
        # Taking it would collide every pad onto one key, which is worse than model-only.
        self._node("event210", "0005:057E:0330.000F", "HID_ID=0005:0000057E:00000330\nHID_PHYS=dc:2e:97:2f:0f:30\n")
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210")), "vidpid:057e:0330")

    def test_junk_hid_uniq_is_rejected(self):
        for junk in ("", "0", "0000000000000000", "HIDDO"):
            devices._HID_UNIQ_CACHE.clear()
            shutil.rmtree(self.d / "class-input", ignore_errors=True)
            shutil.rmtree(self.d / "devices", ignore_errors=True)
            self._node("event210", "0005:057E:0330.000F", _UE(junk))
            self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210")),
                             "vidpid:057e:0330", junk)

    def test_phys_still_beaten_but_still_used_when_there_is_no_hid_uniq(self):
        self._node("event210", "0005:057E:0330.000F", "DRIVER=usbhid\n")
        got = devices.pin_id(_dev(path="/dev/input/event210", phys="usb-0000:04:00.3-2.1/input0"))
        self.assertEqual(devices.pin_kind(got), "port")

    def test_a_serial_is_refused_when_the_node_holds_a_DIFFERENT_model(self):
        # The node a Device names may not be the device sitting there now: a stale Device survives a
        # replug that handed the number to something else, and a synthetic one never owned it. Both
        # are real -- on this Deck /dev/input/event5 carries HID_UNIQ=MFCB50200812, and a test pad
        # using that path silently inherited it. Taking a stranger's serial would pin the wrong
        # hardware to a player, so the model has to match first.
        self._node("event210", "0005:045E:02A1.0003", _UE("MFCB50200812", "0000045E", "000002A1"))
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210")), "vidpid:057e:0330")
        # the pad it really belongs to still gets it
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210", vid=0x045E, pid=0x02A1)),
                         "uniq:045e:02a1:mfcb50200812")

    def test_a_missing_sysfs_tree_degrades_to_the_old_answer(self):
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event999")), "vidpid:057e:0330")

    def test_the_cache_is_keyed_on_the_per_connection_instance(self):
        # Cached on the resolved sysfs path, which embeds the HID instance, so a replug (new
        # instance) re-reads instead of serving the previous pad's address to this long-lived
        # process.
        self._node("event210", "0005:057E:0330.000F", _UE("18:2a:7b:46:43:fd"))
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210")),
                         "uniq:057e:0330:18:2a:7b:46:43:fd")
        shutil.rmtree(self.d / "class-input")
        self._node("event210", "0005:057E:0330.0099", _UE("18:2a:7b:46:43:e9"))
        self.assertEqual(devices.pin_id(_dev(path="/dev/input/event210")),
                         "uniq:057e:0330:18:2a:7b:46:43:e9")


if __name__ == "__main__":
    unittest.main()
