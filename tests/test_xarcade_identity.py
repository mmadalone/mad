"""Telling an X-Arcade cabinet apart from a real Xbox 360 receiver.

WHY THIS EXISTS. On 2026-08-13 an Xbox 360 Wireless Receiver was plugged into the Deck and
promptly took Player 1 for Daphne and OpenBOR, although only "X-Arcade" was ticked for both.
In Xbox mode the cabinet impersonates that exact receiver: same evdev name, same 045e:02a1,
same empty uniq, identical phys on both halves. Until now the ONLY discriminator was the USB
port the user had pressed "Identify X-Arcade" on, which meant two failures at once --

  * a plain Xbox pad at any other port was still treated as the cabinet by every code path
    that expanded the "x-arcade" token to a bare vid:pid, and
  * re-cabling the real cabinet made MAD forget it was a cabinet at all (that staleness once
    broke OpenBOR seating outright; see lib/openbor_seating._listed).

The two do differ at the USB DEVICE level: the cabinet's product string is 'X-Arcade 2' and a
genuine Microsoft receiver's is 'Xbox 360 Wireless Receiver for Windows' (cab measured
2026-06-10, receiver measured live 2026-08-13 on this Deck; deck-docs/xarcade-usb-identity.md).

WHERE THAT EVIDENCE IS AND IS NOT USED. Teaching routing.is_xarcade to believe the string was
tried on 2026-08-13 and reverted the same day: identifying a cabinet nobody Identified silently
swaps the RetroArch input profile from Gamepad to Arcade, splits pad_labels' two entry points so
one screen says "X-Arcade P1" while the row beside it says "Xbox 360", and makes ten tests depend
on what is plugged into the machine running them (simulated: the suite goes red on a Deck with
the cabinet attached and stays green on CI). So is_xarcade keeps the port rule alone.

The string is used ONLY as NEGATIVE evidence, in sdl_filter._scan, where a wrong guess hands a
game to a pad the user never listed. Certainty runs one way: a 045e that positively names itself
something else is ruled out; anything unreadable is NO EVIDENCE and must never be read as "the
cabinet is away" -- reading it that way made a connected, listed cabinet block itself and left
Daphne with no controller at all.

These tests pin the sysfs walk against a faithful fake tree (sysfs reaches the USB device dir
through three symlink hops, so a fake built from plain directories would prove nothing), and pin
the evidence rule in both directions.

Run:  python3 -m unittest tests.test_xarcade_identity -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import devices as dv
from lib import sdl_filter as sf
from lib.devices import Device

CAB = "X-Arcade 2"
MS = "Xbox 360 Wireless Receiver for Windows"


def _fake_sysfs(root: Path, node: str, product: str | None, iface: str | None = "0b") -> Path:
    """Build the sysfs shape a real input node has, and return the /sys/class/input dir.

        <usb>/product                      the string we are after
        <usb>/iface/bInterfaceNumber
        <cls>/<node>  -> <evd>             (symlink, as /sys/class/input/eventN is)
        <evd>/device  -> <inp>             (symlink, as eventN/device is)
        <inp>/device  -> <usb>/iface       (symlink, as inputX/device is)

    THREE symlink hops before the '..', and the '..' only lands on <usb> because the kernel
    resolves it AFTER following them -- which is exactly why the fake has to use symlinks. A
    fake built from plain directories would land somewhere else entirely and prove nothing.
    """
    usb = root / "usb"
    ifd = usb / "iface"
    ifd.mkdir(parents=True)
    if product is not None:
        (usb / "product").write_text(product + "\n")
    if iface is not None:
        (ifd / "bInterfaceNumber").write_text(iface + "\n")
    inp = root / "input"
    inp.mkdir()
    (inp / "device").symlink_to(ifd)
    evd = root / "evnode"
    evd.mkdir()
    (evd / "device").symlink_to(inp)
    cls = root / "class"
    cls.mkdir()
    (cls / node).symlink_to(evd)
    return cls


def _dev(path="/dev/input/event9", vid=0x045e, phys="usb-xhci-hcd.2.auto-1.1/input0"):
    return Device(name="Xbox 360 Wireless Receiver", path=path, is_joypad=True,
                  is_mouse=False, is_keyboard=False, js_index=0, mouse_index=None,
                  vid=vid, pid=0x02a1, uniq="", phys=phys)


class TheSysfsWalk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._real = dv.SYSFS_INPUT
        self.addCleanup(lambda: setattr(dv, "SYSFS_INPUT", self._real))

    def _point_at(self, **kw):
        dv.SYSFS_INPUT = _fake_sysfs(self.root, "event9", **kw)

    def test_it_reads_the_product_string_of_the_usb_device(self):
        self._point_at(product=CAB)
        self.assertEqual(dv.usb_product("/dev/input/event9"), CAB)

    def test_it_strips_the_trailing_newline_sysfs_adds(self):
        self._point_at(product=MS)
        self.assertEqual(dv.usb_product("/dev/input/event9"), MS)
        self.assertNotIn("\n", dv.usb_product("/dev/input/event9"))

    def test_a_missing_product_file_is_empty_not_an_error(self):
        self._point_at(product=None)
        self.assertEqual(dv.usb_product("/dev/input/event9"), "")

    def test_a_node_with_no_usb_ancestry_is_empty(self):
        # Bluetooth pads and virtual devices: /sys/class/input/eventN exists but the walk
        # runs off the end. The live DualSense proves this on the real machine too.
        self._point_at(product=CAB)
        self.assertEqual(dv.usb_product("/dev/input/event404"), "")

    def test_it_takes_the_basename_so_a_bare_node_name_works_too(self):
        self._point_at(product=CAB)
        self.assertEqual(dv.usb_product("event9"), CAB)

    def test_the_interface_lookup_still_works_off_the_same_root(self):
        # usb_iface_num was moved onto the same SYSFS_INPUT attribute; prove it did not
        # change meaning while gaining a test it never had.
        # 0x0b, not "01": the old fixture read the same in base 16 and base 10, so mutating
        # int(..., 16) to int(..., 10) survived and the radix was never pinned.
        self._point_at(product=CAB, iface="0b")
        self.assertEqual(dv.usb_iface_num("/dev/input/event9"), 11)


class TheEvidenceRule(unittest.TestCase):
    """sdl_filter._scan's second return value: "every connected 045e is provably NOT the cab"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._real = dv.SYSFS_INPUT
        self.addCleanup(lambda: setattr(dv, "SYSFS_INPUT", self._real))

    def _scan(self, pads, xport="1.1"):
        """pads = [(product|None, phys)]. Returns sdl_filter._scan()."""
        devs = []
        for n, (product, phys) in enumerate(pads):
            node = f"event{n}"
            root = self.root / node
            root.mkdir()
            dv.SYSFS_INPUT = _fake_sysfs(root, node, product=product)
            devs.append(_dev(path=f"/dev/input/{node}", phys=phys))
        # one root for all of them: point SYSFS_INPUT at a dir holding every node's symlink
        cls = self.root / "class"
        cls.mkdir(exist_ok=True)
        for n in range(len(pads)):
            link = cls / f"event{n}"
            if not link.exists():
                link.symlink_to(self.root / f"event{n}" / "class" / f"event{n}")
        dv.SYSFS_INPUT = cls
        with mock.patch.object(sf, "joypads", return_value=devs), \
             mock.patch.object(sf, "enumerate_devices", return_value=devs), \
             mock.patch("lib.routing.xarcade_port", return_value=xport):
            return sf._scan()

    def test_a_receiver_that_names_itself_is_ruled_out(self):
        present, ruled = self._scan([(MS, "usb-x-1.2.3/input0")])
        self.assertTrue(ruled)
        self.assertNotIn("x-arcade", present)

    def test_a_cabinet_that_names_itself_is_NOT_ruled_out(self):
        _present, ruled = self._scan([(CAB, "usb-x-9.9/input0")])
        self.assertFalse(ruled, "an unidentified cabinet must never be ruled out")

    def test_an_unreadable_pad_is_no_evidence_at_all(self):
        # THE REGRESSION THIS GUARDS. Silence is not a ruling: read it as one and a connected,
        # listed cabinet blocks itself, then the docked rule hides everything else too.
        _present, ruled = self._scan([(None, "usb-x-9.9/input0")])
        self.assertFalse(ruled)

    def test_one_unreadable_pad_spoils_the_ruling_for_all_of_them(self):
        _present, ruled = self._scan([(MS, "usb-x-1.2.3/input0"), (None, "usb-x-4.4/input0")])
        self.assertFalse(ruled, "every 045e must name itself before we act on it")

    def test_the_identified_cabinet_still_resolves_by_PORT_alone(self):
        present, ruled = self._scan([(None, "usb-x-1.1/input0")], xport="1.1")
        self.assertIn("x-arcade", present)
        self.assertFalse(ruled)

    def test_no_045e_connected_is_not_a_ruling_either(self):
        devs = [_dev(path="/dev/input/event9", vid=0x054c, phys="")]
        with mock.patch.object(sf, "joypads", return_value=devs), \
             mock.patch.object(sf, "enumerate_devices", return_value=devs):
            _present, ruled = sf._scan()
        self.assertFalse(ruled)


if __name__ == "__main__":
    unittest.main()
