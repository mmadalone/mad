"""Tests for the device-scoped RA Input writer (item ④): lib/device_binds
set_device_bind / get_device_binds / the binds_for FC30 sentinel overlay. No
hardware — a temp autoconfig dir stands in for RetroArch's udev autoconfig.

Run:  python3 -m unittest tests.test_device_binds -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import device_binds as db
from tests._fakes import FakeDevice

DS = FakeDevice(vid=0x054c, pid=0x0ce6, path="/dev/input/event3",
                name="DualSense Wireless Controller")
FC30 = FakeDevice(vid=0x2dc8, pid=0x2810, path="/dev/input/event4",
                  name="8BitDo FC30 GamePad")

STOCK_DS = (
    'input_driver = "udev"\n'
    'input_device = "DualSense Wireless Controller"\n'
    'input_vendor_id = "1356"\n'
    'input_product_id = "3302"\n'
    'input_a_btn = "1"\n'
    'input_b_btn = "0"\n'
    'input_x_btn = "3"\n'
    'input_l2_axis = "+4"\n'
)


class FileSelection(unittest.TestCase):
    """Which autoconfig a device resolves to. Read and write MUST agree on ONE
    file: the MAD sentinel lives in it, so picking another strands the user's
    binds and silently serves that file's stock ones instead.

    Modelled on the REAL rig (2026-07-17). The X-Arcade's node is 045e:02a1 and
    THREE profiles claim its name; only readdir order was picking the right one.
    """
    XA_NAME = "Xbox 360 Wireless Receiver"
    XA = FakeDevice(vid=0x045e, pid=0x02a1, path="/dev/input/event22", name=XA_NAME)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig = db._AUTOCONF_DIR
        db._AUTOCONF_DIR = self.dir

    def tearDown(self):
        db._AUTOCONF_DIR = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _profile(self, fname, name, vid, pid, dpad, sentinel=False):
        body = (f'input_driver = "udev"\ninput_device = "{name}"\n'
                f'input_vendor_id = "{vid}"\ninput_product_id = "{pid}"\n'
                f'input_up_btn = "{dpad}"\n')
        if sentinel:
            body += (f'\n{db.DEV_BEGIN}\ninput_up_btn = "h0up"\n{db.DEV_END}\n')
        (self.dir / fname).write_text(body)

    def _real_shape(self):
        # Exactly the rig: the file with OUR binds declares the X-Arcade's true USB
        # pid 0x719 (xpad rewrites id.product to 02a1 on the node), while the stock
        # file that DOES match 02a1 carries the pre-6.16 numeric d-pad.
        self._profile("Xbox_360_Wireless_RF_Module_RF01.cfg", self.XA_NAME,
                      0x045e, 0x02a1, "13")                       # id match, STOCK
        self._profile("Xbox_360_Wireless_Receiver.cfg", self.XA_NAME,
                      0x045e, 0x0719, "13", sentinel=True)        # name only, OURS
        self._profile("Xbox_360_Wireless_Receiver_Chinese01.cfg", self.XA_NAME,
                      0x045e, 0x0291, "13")                       # name only, stock

    def test_our_sentinel_beats_a_vid_pid_match(self):
        # THE regression this guards: scoring vid:pid highest (what RetroArch's own
        # try_from_conf does) picks the stock RF01 profile and hands the router the
        # pre-6.16 numeric d-pad -> the stick rotates in every RA game.
        self._real_shape()
        self.assertEqual(db._autoconfig_file(self.XA).name,
                         "Xbox_360_Wireless_Receiver.cfg",
                         "abandoned the profile carrying our own binds")
        self.assertEqual(db.binds_for(self.XA)["up_btn"], "h0up",
                         "the router would write a stale numeric d-pad")

    def test_selection_does_not_depend_on_directory_order(self):
        # The actual bug: first-name-wins over an UNSORTED glob. Three files claim
        # the name, so readdir order alone decided. Sorting is NOT the fix either --
        # RF_Module_RF01.cfg sorts FIRST, so sorted()+first-name-wins picks the
        # stale one. Create the files in BOTH orders and demand one answer.
        for order in (0, 1):
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir.mkdir(parents=True)
            names = ["Xbox_360_Wireless_RF_Module_RF01.cfg",
                     "Xbox_360_Wireless_Receiver.cfg"]
            for fname in (names if order == 0 else list(reversed(names))):
                self._profile(fname, self.XA_NAME, 0x045e,
                              0x02a1 if "RF01" in fname else 0x0719, "13",
                              sentinel="RF01" not in fname)
            self.assertEqual(db._autoconfig_file(self.XA).name,
                             "Xbox_360_Wireless_Receiver.cfg",
                             f"creation order {order} changed the answer")

    def test_vid_pid_beats_name_only_when_neither_is_ours(self):
        # With no sentinel to honour, fall back to RetroArch's own preference.
        self._profile("by_name.cfg", self.XA_NAME, 0x045e, 0x9999, "13")
        self._profile("by_id.cfg", "Some Other Pad", 0x045e, 0x02a1, "13")
        self.assertEqual(db._autoconfig_file(self.XA).name, "by_id.cfg")

    def test_a_true_tie_resolves_the_same_way_whatever_readdir_says(self):
        # The scores above are distinct, so they never exercise the tie-break --
        # and an untested tie-break is exactly how this bug arrived. Two profiles
        # scoring IDENTICALLY (name-only, neither ours): the answer must not depend
        # on the order the directory happens to hand us. Feed BOTH orders through a
        # stub dir, because real readdir order is not ours to choose.
        class _FakeDir:
            def __init__(self, files): self._files = files
            def is_dir(self): return True
            def glob(self, _pat): return iter(self._files)

        self._profile("aaa_tie.cfg", self.XA_NAME, 0x045e, 0x9998, "13")
        self._profile("zzz_tie.cfg", self.XA_NAME, 0x045e, 0x9999, "13")
        a, z = self.dir / "aaa_tie.cfg", self.dir / "zzz_tie.cfg"
        got = []
        for order in ([a, z], [z, a]):
            db._AUTOCONF_DIR = _FakeDir(order)
            got.append(db._autoconfig_file(self.XA).name)
        db._AUTOCONF_DIR = self.dir
        self.assertEqual(got[0], got[1],
                         "a tie resolves by directory order: same files, different "
                         "answer, so which binds the router writes is filesystem luck")

    def test_a_file_matching_neither_never_wins(self):
        self._profile("unrelated.cfg", "Nothing Like It", 0x1234, 0x5678, "13")
        self.assertIsNone(db._autoconfig_file(self.XA))

    def test_read_and_write_resolve_the_same_file(self):
        # The contract the docstring names: if these diverge, set_device_bind writes
        # the user's remap into a file binds_for never reads.
        self._real_shape()
        self.assertEqual(db.autoconfig_path_for(self.XA), db._autoconfig_file(self.XA))
        written = db.set_device_bind(self.XA, "a_btn", "7")
        self.assertEqual(written, db._autoconfig_file(self.XA))
        self.assertEqual(db.binds_for(self.XA)["a_btn"], "7",
                         "a user edit landed in a file the router does not read")


class DeviceBinds(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig = db._AUTOCONF_DIR
        db._AUTOCONF_DIR = self.dir

    def tearDown(self):
        db._AUTOCONF_DIR = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _stock(self, name, text):
        (self.dir / f"{name}.cfg").write_text(text)

    def test_set_creates_minimal_profile_when_absent(self):
        path = db.set_device_bind(DS, "a_btn", "2")
        self.assertTrue(path.exists())
        txt = path.read_text()
        self.assertIn('input_device = "DualSense Wireless Controller"', txt)
        self.assertIn(db.DEV_BEGIN, txt)
        self.assertIn('input_a_btn = "2"', txt)
        self.assertEqual(db.get_device_binds(DS).get("a_btn"), "2")

    def test_sentinel_overrides_stock_last_wins(self):
        self._stock("DualSense Wireless Controller", STOCK_DS)
        self.assertEqual(db._autoconfig_binds(DS)["a_btn"], "1")     # stock
        db.set_device_bind(DS, "a_btn", "5")
        self.assertEqual(db._autoconfig_binds(DS)["a_btn"], "5")     # sentinel wins
        self.assertEqual(db._autoconfig_binds(DS)["b_btn"], "0")     # untouched stock
        txt = (self.dir / "DualSense Wireless Controller.cfg").read_text()
        self.assertIn('input_b_btn = "0"', txt)                      # stock preserved
        self.assertIn(db.DEV_BEGIN, txt)

    def test_idempotent_and_one_time_bak(self):
        self._stock("DualSense Wireless Controller", STOCK_DS)
        p = self.dir / "DualSense Wireless Controller.cfg"
        db.set_device_bind(DS, "a_btn", "5")
        once = p.read_text()
        db.set_device_bind(DS, "a_btn", "5")
        self.assertEqual(p.read_text(), once)                        # idempotent
        self.assertTrue((self.dir / "DualSense Wireless Controller.cfg.mad-bak").exists())

    def test_axis_suffix_accepted(self):
        db.set_device_bind(DS, "l2_axis", "+2")
        self.assertEqual(db.get_device_binds(DS).get("l2_axis"), "+2")

    def test_fc30_profile_overlay(self):
        base = db.binds_for(FC30)                                    # no autoconfig yet
        self.assertEqual(base["a_btn"], "1")
        self.assertEqual(base["start_btn"], "11")
        db.set_device_bind(FC30, "start_btn", "6")                   # user remap
        over = db.binds_for(FC30)
        self.assertEqual(over["start_btn"], "6")                     # user edit wins
        self.assertEqual(over["a_btn"], "1")                         # FC30 profile intact

    def test_non_fc30_without_autoconfig_returns_none(self):
        # A device with neither a profile nor an autoconfig → no bind lines.
        unknown = FakeDevice(vid=0x1234, pid=0x5678, path="/dev/input/event9", name="Mystery Pad")
        self.assertIsNone(db.binds_for(unknown))


if __name__ == "__main__":
    unittest.main()
