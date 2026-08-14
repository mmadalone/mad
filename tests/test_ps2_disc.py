"""lib/ps2_disc.identify() -- derive PCSX2's per-game override key straight from a PS2
disc image. Hermetic: every test disc is a tiny synthetic ISO9660 image built by
tests/ps2_fixtures.py, never a real ROM.

Covers the four load-bearing corrections a design review found against PCSX2's upstream
source (see lib/ps2_disc.py's module docstring):
  1. the ELF CRC fold DROPS a trailing 1-3 byte remainder, never pads it;
  2/3. the serial is derived, then VALIDATED against PCSX2's wildcard test, and CLEARED
       (not kept) on failure -- producing a bare-CRC key shape;
  4. the ';1' version suffix + case fold applies to EVERY path component while walking
     to the boot file (not just the last), and a directory-flagged record is rejected
     when a FILE is wanted.

Plus: container dispatch (.iso/.bin/.cue/.chd-absent/unsupported), and the never-raises
/ clean-why-message safety rails identify() exists to guarantee.

Run:  python3 -m unittest tests.test_ps2_disc -v
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import ps2_disc
from tests import ps2_fixtures as fx


def _ref_crc(data: bytes) -> int:
    """Independent reference for the fold: XOR complete little-endian 4-byte words only,
    dropping any trailing remainder. Written separately from lib.ps2_disc._elf_crc (not
    calling it) so a shared bug between "implementation" and "check" can't hide."""
    crc = 0
    for i in range(len(data) // 4):
        crc ^= struct.unpack_from("<I", data, i * 4)[0]
    return crc


class ElfCrc(unittest.TestCase):
    def test_parity_against_independent_reference(self):
        pattern = bytes((i * 41 + 7) % 256 for i in range(200000))
        for length in (0, 1, 3, 4, 5, 7, 4096, 4097, 123457):
            data = pattern[:length]
            self.assertEqual(ps2_disc._elf_crc(data), _ref_crc(data), f"length={length}")

    def test_trailing_remainder_dropped_not_padded_explicit_values(self):
        # Crafted with NONZERO trailing bytes so a (wrong) zero-padding fold would give a
        # DIFFERENT, nonzero answer here -- the hardcoded 0s are the whole point.
        self.assertEqual(ps2_disc._elf_crc(b""), 0)
        self.assertEqual(ps2_disc._elf_crc(b"\xAB"), 0)                 # 1 byte, dropped
        self.assertEqual(ps2_disc._elf_crc(b"\xAB\xCD\xEF"), 0)         # 3 bytes, dropped
        four = b"\x01\x02\x03\x04"
        self.assertEqual(ps2_disc._elf_crc(four), 0x04030201)
        # a dropped trailing remainder must not change the result AT ALL, whether it's
        # 1 or 3 extra bytes -- same expected value as the bare 4-byte case above.
        self.assertEqual(ps2_disc._elf_crc(four + b"\xFF"), 0x04030201)
        self.assertEqual(ps2_disc._elf_crc(four + b"\x05\x06\x07"), 0x04030201)


class SerialFromBoot2(unittest.TestCase):
    def test_normal_backslash_form(self):
        self.assertEqual(ps2_disc._serial_from_boot2("cdrom0:\\SLES_529.50;1"), "SLES-52950")

    def test_no_backslash_colon_only(self):
        self.assertEqual(ps2_disc._serial_from_boot2("cdrom0:SLES_529.50;1"), "SLES-52950")

    def test_bare_no_prefix(self):
        self.assertEqual(ps2_disc._serial_from_boot2("SLES_529.50"), "SLES-52950")

    def test_lowercase_input(self):
        self.assertEqual(ps2_disc._serial_from_boot2("cdrom0:\\sles_529.50;1"), "SLES-52950")

    def test_dash_form(self):
        self.assertEqual(ps2_disc._serial_from_boot2("SLES-529.50"), "SLES-52950")

    def test_wildcard_failure_clears_serial(self):
        # "BOOT.ELF": the 5th character is '.', not '_'/'-' -- fails PCSX2's own
        # '????_???.??*' / '????-???.??*' wildcard test, so the serial must be CLEARED
        # (empty), never kept as a best guess.
        self.assertEqual(ps2_disc._serial_from_boot2("cdrom0:\\BOOT.ELF;1"), "")


class KeyShape(unittest.TestCase):
    def test_serial_shape(self):
        self.assertEqual(ps2_disc._key("SLES-52950", 0x1B02C1DC), "SLES-52950_1B02C1DC")

    def test_bare_crc_shape_when_serial_cleared(self):
        self.assertEqual(ps2_disc._key("", 0x83C9749E), "83C9749E")


class IdentifyIso(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_level_boot_end_to_end(self):
        data = bytes((i * 13 + 1) % 256 for i in range(837)) + b"\xAB\xCD\xEF"
        p = fx.make_iso(self.tmp / "game.iso", boot2="cdrom0:\\SLES_529.50;1",
                         boot_name="SLES_529.50", boot_data=data)
        key, why, final = ps2_disc.identify(str(p))
        self.assertEqual(why, "")
        self.assertTrue(final)
        self.assertEqual(key, f"SLES-52950_{ps2_disc._elf_crc(data):08X}")

    def test_boot_in_subdirectory_resolves(self):
        data = b"\xAA\xBB\xCC\xDD" * 50
        p = fx.make_iso(self.tmp / "sub.iso", boot2="cdrom0:\\SUBDIR\\SLES_500.00;1",
                         boot_name="SLES_500.00", boot_data=data, boot_subdir="SUBDIR")
        key, why, final = ps2_disc.identify(str(p))
        self.assertEqual(why, "")
        self.assertTrue(final)
        self.assertEqual(key, f"SLES-50000_{ps2_disc._elf_crc(data):08X}")

    def test_intermediate_component_version_suffix_resolves(self):
        # Correction 4: the ';1' shows up on the SUBDIR path component in the BOOT2
        # STRING even though the on-disc directory record itself is plain 'SUBDIR' (no
        # semicolon) -- it must still resolve, to the SAME key as the unsuffixed form.
        data = b"\xAA\xBB\xCC\xDD" * 50
        plain = fx.make_iso(self.tmp / "plain.iso", boot2="cdrom0:\\SUBDIR\\SLES_500.00;1",
                             boot_name="SLES_500.00", boot_data=data, boot_subdir="SUBDIR")
        suffixed = fx.make_iso(self.tmp / "suffixed.iso", boot2="cdrom0:\\SUBDIR;1\\SLES_500.00;1",
                                boot_name="SLES_500.00", boot_data=data, boot_subdir="SUBDIR")
        key_plain, _, _ = ps2_disc.identify(str(plain))
        key_suffixed, why, final = ps2_disc.identify(str(suffixed))
        self.assertEqual(why, "")
        self.assertTrue(final)
        self.assertIsNotNone(key_plain)
        self.assertEqual(key_suffixed, key_plain)

    def test_directory_record_rejected_when_file_wanted(self):
        # Root holds a DIRECTORY named exactly like the boot target ("GAME.ELF"), and NO
        # file of that name anywhere -- correction 4 says a directory-flagged record must
        # never satisfy a file lookup, so this must fail as "missing", not match the dir.
        b = fx.IsoBuilder()
        empty_dir = b.add_dir("GAME.ELF", [])
        cnf = b.add_file("SYSTEM.CNF;1", b"BOOT2 = cdrom0:\\GAME.ELF;1\r\n")
        (self.tmp / "collide.iso").write_bytes(b.build([empty_dir, cnf]))
        key, why, final = ps2_disc.identify(str(self.tmp / "collide.iso"))
        self.assertIsNone(key)
        self.assertTrue(final)
        self.assertEqual(why, "The boot program named on this disc is missing")

    def test_missing_system_cnf(self):
        p = fx.make_iso(self.tmp / "nocnf.iso", boot2="cdrom0:\\SLES_529.50;1",
                         boot_name="SLES_529.50", boot_data=b"\x00" * 40,
                         include_system_cnf=False)
        key, why, final = ps2_disc.identify(str(p))
        self.assertIsNone(key)
        self.assertTrue(final)
        self.assertEqual(why, "No SYSTEM.CNF on this disc")

    def test_random_bytes_is_not_a_disc(self):
        # Large enough that the LBA-16 read itself succeeds (so this is genuinely "read
        # fine, but it's not ISO9660" -- a PERMANENT conclusion -- not a truncated-read
        # transient case, which is covered separately under NeverRaises).
        p = self.tmp / "junk.iso"
        p.write_bytes(os.urandom(200 * 1024))
        key, why, final = ps2_disc.identify(str(p))
        self.assertIsNone(key)
        self.assertTrue(final)
        self.assertTrue(why)

    def test_unsupported_extension(self):
        p = self.tmp / "game.cso"
        p.write_bytes(b"whatever bytes, never even opened for this")
        key, why, final = ps2_disc.identify(str(p))
        self.assertIsNone(key)
        self.assertTrue(final)
        self.assertEqual(why, "This disc format is not supported yet")


class IdentifyBinCue(unittest.TestCase):
    """The .bin (raw 2352/24 sectors) and .cue (resolves to a .bin) containers -- same
    ISO9660 logical structure as IdentifyIso, repacked into a real disc's physical
    layout, proving the layout probe in ps2_disc._open_bin actually works."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        data = bytes((i * 13 + 1) % 256 for i in range(500))
        self.data = data
        self.expected_key = f"SLES-52950_{ps2_disc._elf_crc(data):08X}"
        image = fx.iso_bytes(boot2="cdrom0:\\SLES_529.50;1", boot_name="SLES_529.50",
                              boot_data=data)
        (self.tmp / "Game.bin").write_bytes(fx.to_raw_bin(image))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bin_layout_probe_resolves(self):
        key, why, final = ps2_disc.identify(str(self.tmp / "Game.bin"))
        self.assertEqual(why, "")
        self.assertTrue(final)
        self.assertEqual(key, self.expected_key)

    def test_cue_resolves_case_insensitively_to_first_data_track(self):
        # the .cue text spells the filename in a DIFFERENT case than the file on disk.
        cue = self.tmp / "game.cue"
        cue.write_text('FILE "GAME.BIN" BINARY\n  TRACK 01 MODE2/2352\n')
        key, why, final = ps2_disc.identify(str(cue))
        self.assertEqual(why, "")
        self.assertTrue(final)
        self.assertEqual(key, self.expected_key)


class ChdChdmanAbsent(unittest.TestCase):
    """.chd support only exists via EmuDeck's bundled chdman5 subprocess -- a bare CI
    runner has neither the tool nor real .chd data, so this is the one .chd test that
    must run everywhere: it never even needs a real .chd file."""

    def test_missing_binary_is_transient_never_final(self):
        with mock.patch.object(ps2_disc, "_CHDMAN", Path("/nonexistent/chdman5")):
            key, why, final = ps2_disc.identify("/does/not/matter.chd")
        self.assertIsNone(key)
        self.assertFalse(final)                 # NEVER True -- a missing tool is not a fact about the disc
        self.assertTrue(why)


class NeverRaises(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_directory_does_not_raise(self):
        d = self.tmp / "adir.iso"
        d.mkdir()
        key, why, final = ps2_disc.identify(str(d))
        self.assertIsNone(key)
        self.assertTrue(why)

    def test_an_empty_file_does_not_raise(self):
        p = self.tmp / "empty.iso"
        p.write_bytes(b"")
        key, why, final = ps2_disc.identify(str(p))
        self.assertIsNone(key)
        self.assertTrue(why)

    def test_a_missing_file_does_not_raise(self):
        key, why, final = ps2_disc.identify(str(self.tmp / "does-not-exist.iso"))
        self.assertIsNone(key)
        self.assertTrue(why)

    def test_a_file_that_disappears_mid_call_does_not_raise(self):
        p = self.tmp / "vanish.iso"
        p.write_bytes(b"\x00" * 4096)
        with mock.patch("builtins.open", side_effect=FileNotFoundError("gone")):
            key, why, final = ps2_disc.identify(str(p))
        self.assertIsNone(key)
        self.assertTrue(why)


class WhyMessagesAreClean(unittest.TestCase):
    """Every 'why' shown to the user must be non-empty, and free of file paths and
    exception class names -- it's read by a non-technical user in the MAD panel."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_clean(self, why: str, path_str: str) -> None:
        self.assertTrue(why)
        self.assertNotIn(path_str, why)
        for token in ("Error", "Exception", "Traceback", "errno", "Errno"):
            self.assertNotIn(token, why)

    def test_every_failure_reason_is_clean(self):
        scenarios = [self.tmp / "g.cso"]
        scenarios[0].write_bytes(b"x")
        scenarios.append(fx.make_iso(self.tmp / "nocnf.iso", boot2="cdrom0:\\SLES_529.50;1",
                                      boot_name="SLES_529.50", boot_data=b"\x00" * 20,
                                      include_system_cnf=False))
        junk = self.tmp / "junk.iso"
        junk.write_bytes(os.urandom(200 * 1024))
        scenarios.append(junk)
        scenarios.append(self.tmp / "gone.iso")                # never created
        adir = self.tmp / "d.iso"
        adir.mkdir()
        scenarios.append(adir)
        for p in scenarios:
            _, why, _ = ps2_disc.identify(str(p))
            self._assert_clean(why, str(p))

    def test_chd_missing_binary_reason_is_clean(self):
        target = self.tmp / "g.chd"
        with mock.patch.object(ps2_disc, "_CHDMAN", Path("/nonexistent/chdman5")):
            _, why, final = ps2_disc.identify(str(target))
        self._assert_clean(why, str(target))
        self.assertFalse(final)


if __name__ == "__main__":
    unittest.main()
