"""Regression tests for mad_launch_wrap.wrap_console_launchers atomic write.

Covers review finding (2026-07-15 #7): the write to custom_systems/es_systems.xml
was a bare path.write_text with no XML validation, no temp+rename, no backup, so a
crash/power-loss/disk-full mid-write could truncate the file and make ES-DE silently
drop ALL custom systems. The write now validates, atomically swaps, and takes a
one-time .bak of the pre-wrap original.

Run:  python3 -m unittest tests.test_mad_launch_wrap_atomic -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import mad_launch_wrap as w


# A minimal es_systems.xml whose Switch <command> transform() will rewrite.
SRC = (
    '<?xml version="1.0"?>\n'
    '<systemList>\n'
    '  <system>\n'
    '    <name>switch</name>\n'
    '    <command>%EMULATOR_EDEN% -f -g %ROM%</command>\n'
    '  </system>\n'
    '</systemList>\n'
)


class WrapAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "es_systems.xml"
        self.f.write_text(SRC, encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wrap_writes_valid_xml_and_takes_one_time_bak(self):
        changed = w.wrap_console_launchers(self.f)
        self.assertTrue(changed)
        # Result parses as XML.
        import xml.etree.ElementTree as ET
        ET.fromstring(self.f.read_text(encoding="utf-8"))
        # A one-time .bak snapshot of the ORIGINAL (pre-wrap) file exists.
        bak = self.f.with_name(self.f.name + ".bak")
        self.assertTrue(bak.is_file())
        self.assertEqual(bak.read_text(encoding="utf-8"), SRC)

    def test_bak_is_not_overwritten_on_second_wrap(self):
        w.wrap_console_launchers(self.f)
        bak = self.f.with_name(self.f.name + ".bak")
        first = bak.read_text(encoding="utf-8")
        # Second call is idempotent (t2 == t) -> no write, .bak untouched.
        again = w.wrap_console_launchers(self.f)
        self.assertFalse(again)
        self.assertEqual(bak.read_text(encoding="utf-8"), first)

    def test_invalid_transform_output_never_replaces_good_file(self):
        # Force transform() to emit non-parseable XML: the good file must survive
        # untouched, no .bak, and the function reports "did not write".
        with mock.patch.object(w, "transform", return_value="<systemList><broken>"):
            changed = w.wrap_console_launchers(self.f)
        self.assertFalse(changed)
        self.assertEqual(self.f.read_text(encoding="utf-8"), SRC)
        self.assertFalse(self.f.with_name(self.f.name + ".bak").exists())

    def test_no_stray_tmp_left_behind(self):
        w.wrap_console_launchers(self.f)
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_missing_file_is_noop(self):
        self.assertFalse(w.wrap_console_launchers(self.tmp / "does-not-exist.xml"))


if __name__ == "__main__":
    unittest.main()
