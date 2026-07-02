"""pad_labels — the single home for controller display labels.

Locks the port-identity labeling (a 045e pad at the identified USB port is the
X-Arcade, split "P1"/"P2" by USB interface; any other 045e stays "Xbox 360"),
the KNOWN_PADS fallbacks, the historical re-exports (mad_config, device_cmds),
and the lindbergh connected_pads() consumer: friendly labels, port/#N
disambiguation for same-labeled pads, and the tags-stay-cosmetic guarantee.
Run with the rest: python3 -m unittest discover -s tests -t .
"""
import unittest
from unittest import mock

from lib import pad_labels
from tests._fakes import FakeDevice

XPORT = "1.1"
XA_PHYS = f"usb-0000:04:00.3-{XPORT}/input0"          # both halves share this phys


def xa_half(path: str) -> FakeDevice:
    return FakeDevice(vid=0x045E, pid=0x02A1, path=path,
                      name="Xbox 360 Wireless Receiver", phys=XA_PHYS)


class PadLabel(unittest.TestCase):
    def test_identified_xarcade(self):
        self.assertEqual(
            pad_labels.pad_label(0x045E, "045e:02a1", "Xbox 360 Wireless Receiver",
                                 XPORT, XPORT), "X-Arcade")

    def test_real_xbox_pad_on_other_port_stays_xbox(self):
        self.assertEqual(
            pad_labels.pad_label(0x045E, "045e:02a1", "Xbox 360 Wireless Receiver",
                                 "1.4", XPORT), "Xbox 360")

    def test_unidentified_045e_stays_xbox(self):
        self.assertEqual(
            pad_labels.pad_label(0x045E, "045e:02a1", "Xbox 360 Wireless Receiver",
                                 XPORT, ""), "Xbox 360")

    def test_known_pad(self):
        self.assertEqual(
            pad_labels.pad_label(0x054C, "054c:0ce6", "Sony Wireless Controller",
                                 "1.2", XPORT), "DualSense")

    def test_unknown_pad_keeps_raw_name(self):
        self.assertEqual(
            pad_labels.pad_label(0x1234, "1234:5678", "Generic USB Joystick",
                                 "1.2", XPORT), "Generic USB Joystick")


class DeviceLabel(unittest.TestCase):
    def _label(self, d, xport=XPORT, iface=None):
        with mock.patch("lib.pad_labels.usb_iface_num", return_value=iface):
            return pad_labels.device_label(d, xport)

    def test_xarcade_halves_split_by_interface(self):
        self.assertEqual(self._label(xa_half("/dev/input/event6"), iface=0), "X-Arcade P1")
        self.assertEqual(self._label(xa_half("/dev/input/event10"), iface=1), "X-Arcade P2")

    def test_unreadable_interface_stays_plain(self):
        self.assertEqual(self._label(xa_half("/dev/input/event6"), iface=None), "X-Arcade")

    def test_unidentified_xarcade_is_xbox(self):
        self.assertEqual(self._label(xa_half("/dev/input/event6"), xport="", iface=0),
                         "Xbox 360")

    def test_known_pad(self):
        d = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event3",
                       name="Sony Wireless Controller",
                       phys="usb-0000:04:00.3-1.2/input0")
        self.assertEqual(self._label(d), "DualSense")

    def test_unknown_pad_keeps_raw_name(self):
        d = FakeDevice(vid=0x1234, pid=0x5678, path="/dev/input/event4",
                       name="Generic USB Joystick",
                       phys="usb-0000:04:00.3-1.3/input0")
        self.assertEqual(self._label(d), "Generic USB Joystick")


class Reexports(unittest.TestCase):
    """The historical addresses keep working (mad_config data, device_cmds labeler)."""

    def test_mad_config(self):
        from lib import mad_config
        self.assertIs(mad_config.KNOWN_PADS, pad_labels.KNOWN_PADS)
        self.assertIs(mad_config.PAD_SHORT, pad_labels.PAD_SHORT)
        self.assertEqual(mad_config.pad_name("054c:0ce6"), "DualSense")

    def test_device_cmds(self):
        from lib.madsrv import device_cmds
        self.assertIs(device_cmds.pad_label, pad_labels.pad_label)


class LindberghConnectedPads(unittest.TestCase):
    """connected_pads() labels via pad_labels; tags/names stay the loader's raw
    matching keys. Tag order is /dev/input string sort (event10 < event6), so the
    BASE tag is the iface-1 half = "X-Arcade P2" — the physical side wins."""

    IFACE = {"/dev/input/event10": 1, "/dev/input/event6": 0}

    def _run(self, devs, tags, xport=XPORT):
        from lib import lindbergh_pads
        with mock.patch("lib.devices.enumerate_devices", return_value=devs), \
             mock.patch("lib.devices.joypads", side_effect=lambda ds: ds), \
             mock.patch("lib.lindbergh_pads.loader_tags", return_value=tags), \
             mock.patch("lib.policy.load_merged", return_value={}), \
             mock.patch("lib.routing.xarcade_port", return_value=xport), \
             mock.patch("lib.pad_labels.usb_iface_num",
                        side_effect=lambda p: self.IFACE.get(p)):
            return lindbergh_pads.connected_pads()

    def _xa_setup(self):
        devs = [xa_half("/dev/input/event10"), xa_half("/dev/input/event6")]
        tags = [{"path": "/dev/input/event10", "name": "Xbox 360 Wireless Receiver",
                 "tag": "xbox_360_wireless_receiver"},
                {"path": "/dev/input/event6", "name": "Xbox 360 Wireless Receiver",
                 "tag": "xbox_360_wireless_receiver_2"}]
        return devs, tags

    def test_xarcade_halves_get_p1_p2(self):
        rows = self._run(*self._xa_setup())
        self.assertEqual([r["label"] for r in rows], ["X-Arcade P2", "X-Arcade P1"])

    def test_tags_and_names_unchanged(self):
        rows = self._run(*self._xa_setup())
        self.assertEqual([r["tag"] for r in rows],
                         ["xbox_360_wireless_receiver", "xbox_360_wireless_receiver_2"])
        self.assertEqual({r["name"] for r in rows}, {"Xbox 360 Wireless Receiver"})

    def test_unidentified_xarcade_falls_back_to_numbered_xbox(self):
        rows = self._run(*self._xa_setup(), xport="")
        self.assertEqual([r["label"] for r in rows], ["Xbox 360 #1", "Xbox 360 #2"])

    def test_known_pad_gets_friendly_name(self):
        d = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event3",
                       name="Sony Wireless Controller",
                       phys="usb-0000:04:00.3-1.2/input0")
        rows = self._run([d], [{"path": d.path, "name": d.name,
                                "tag": "sony_wireless_controller"}])
        self.assertEqual([r["label"] for r in rows], ["DualSense"])

    def test_same_label_pads_disambiguated_by_port(self):
        a = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event3",
                       name="Sony Wireless Controller",
                       phys="usb-0000:04:00.3-1.2/input0")
        b = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event4",
                       name="Sony Wireless Controller",
                       phys="usb-0000:04:00.3-1.3/input0")
        rows = self._run([a, b], [
            {"path": a.path, "name": a.name, "tag": "sony_wireless_controller"},
            {"path": b.path, "name": b.name, "tag": "sony_wireless_controller_2"}])
        self.assertEqual([r["label"] for r in rows],
                         ["DualSense (1.2)", "DualSense (1.3)"])

    def test_same_label_no_ports_numbered(self):
        a = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event3",
                       name="Sony Wireless Controller", phys="")
        b = FakeDevice(vid=0x054C, pid=0x0CE6, path="/dev/input/event4",
                       name="Sony Wireless Controller", phys="")
        rows = self._run([a, b], [
            {"path": a.path, "name": a.name, "tag": "sony_wireless_controller"},
            {"path": b.path, "name": b.name, "tag": "sony_wireless_controller_2"}])
        self.assertEqual([r["label"] for r in rows],
                         ["DualSense #1", "DualSense #2"])

    def test_non_joypad_tag_skipped(self):
        devs, tags = self._xa_setup()
        tags = tags + [{"path": "/dev/input/event2", "name": "Some Keyboard",
                        "tag": "some_keyboard"}]
        rows = self._run(devs, tags)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
