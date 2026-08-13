"""lib/inifile.py - CRLF regression guard.

All three functions (section_body, set_section, remove_section) used to match a
section header with the literal pattern `\\[Name\\]\\n`, which never matches a
Windows line ending (`[Name]\\r\\n`). Found latent during audit phase 4a: a
CRLF-written [Controls] section was invisible to section_body, so set_section
would APPEND a second [Controls] block after the invisible one instead of
replacing it - the file would end up with two [Controls] sections, and only
the first (dead) one would be read back by the emulator.

This module locks:
  - section_body finds a CRLF section and returns a body with no trailing \\r
  - set_section REPLACES an existing CRLF (or LF) section rather than
    duplicating it
  - set_section preserves the file's line-ending style (CRLF stays CRLF, LF
    stays LF - and the LF case is byte-identical to the pre-fix behaviour)
  - remove_section works on CRLF
  - a brand-new section appended to a CRLF file is written in CRLF
  - a file with mixed line endings does not crash and stays replaceable

Run:  python3 -m unittest tests.test_inifile_crlf -v
"""
from __future__ import annotations

import unittest

from lib import inifile


LF_FILE = (
    "[General]\n"
    "foo=1\n"
    "\n"
    "[Controls]\n"
    "p1_button_a=button:0\n"
    "p1_button_b=button:1\n"
    "\n"
    "[Other]\n"
    "bar=2\n"
)

CRLF_FILE = LF_FILE.replace("\n", "\r\n")


class SectionBodyCRLF(unittest.TestCase):
    def test_lf_body_has_no_stray_cr(self):
        body = inifile.section_body(LF_FILE, "Controls")
        self.assertEqual(body, "p1_button_a=button:0\np1_button_b=button:1")
        self.assertNotIn("\r", body)

    def test_crlf_body_is_found_and_has_no_trailing_cr(self):
        body = inifile.section_body(CRLF_FILE, "Controls")
        self.assertIsNotNone(body)
        # trailing \r\n (the blank separator line) must be gone, same as the
        # LF case strips the trailing \n
        self.assertFalse(body.endswith("\r"))
        self.assertFalse(body.endswith("\n"))
        # internal line endings are preserved as CRLF (this module does not
        # rewrite content it isn't asked to touch), only the trailing blank
        # separator is stripped
        self.assertEqual(body, "p1_button_a=button:0\r\np1_button_b=button:1")

    def test_crlf_missing_section_returns_none(self):
        self.assertIsNone(inifile.section_body(CRLF_FILE, "Nope"))


class SetSectionReplacesNotAppends(unittest.TestCase):
    def test_lf_replace_leaves_exactly_one_header(self):
        out = inifile.set_section(LF_FILE, "Controls", "p1_button_a=button:9")
        self.assertEqual(out.count("[Controls]"), 1)
        self.assertIn("p1_button_a=button:9", out)
        self.assertNotIn("p1_button_a=button:0", out)

    def test_crlf_replace_leaves_exactly_one_header(self):
        out = inifile.set_section(CRLF_FILE, "Controls", "p1_button_a=button:9")
        # this is the actual regression: before the fix, the CRLF header was
        # invisible to the matcher, so this would produce TWO [Controls]
        # headers (the untouched original + a freshly appended one).
        self.assertEqual(out.count("[Controls]"), 1)
        self.assertIn("p1_button_a=button:9", out)
        self.assertNotIn("p1_button_a=button:0", out)


class SetSectionPreservesLineEndingStyle(unittest.TestCase):
    def test_crlf_file_stays_crlf_throughout(self):
        out = inifile.set_section(CRLF_FILE, "Controls", "p1_button_a=button:9")
        # every bare \n must be part of a \r\n pair - i.e. no lone \n snuck in
        for i, ch in enumerate(out):
            if ch == "\n":
                self.assertEqual(out[i - 1], "\r",
                                  f"bare LF at offset {i} in {out!r}")

    def test_crlf_body_supplied_as_lf_is_rendered_crlf(self):
        # callers (pcsx2_cfg/xemu_cfg/eden_cfg) build bodies with "\n".join(...);
        # set_section must still render them in the target file's style.
        out = inifile.set_section(CRLF_FILE, "Controls", "a=1\nb=2")
        self.assertIn("[Controls]\r\na=1\r\nb=2\r\n\r\n", out)

    def test_lf_file_is_byte_identical_to_pre_fix_behaviour(self):
        out = inifile.set_section(LF_FILE, "Controls", "p1_button_a=button:9")
        expected = (
            "[General]\n"
            "foo=1\n"
            "\n"
            "[Controls]\n"
            "p1_button_a=button:9\n"
            "\n"
            "[Other]\n"
            "bar=2\n"
        )
        self.assertEqual(out, expected)


class RemoveSectionCRLF(unittest.TestCase):
    def test_removes_crlf_section(self):
        out = inifile.remove_section(CRLF_FILE, "Controls")
        self.assertNotIn("[Controls]", out)
        self.assertIn("[General]", out)
        self.assertIn("[Other]", out)

    def test_missing_section_is_noop(self):
        out = inifile.remove_section(CRLF_FILE, "Nope")
        self.assertEqual(out, CRLF_FILE)


class SetSectionAppendsNewSectionInFileStyle(unittest.TestCase):
    def test_new_section_appended_to_crlf_file_uses_crlf(self):
        out = inifile.set_section(CRLF_FILE, "Brand New", "x=1")
        self.assertTrue(out.endswith("[Brand New]\r\nx=1\r\n\r\n"))
        # and the append itself introduced no bare \n
        tail = out[len(CRLF_FILE):]
        for i, ch in enumerate(tail):
            if ch == "\n":
                self.assertEqual(tail[i - 1], "\r")

    def test_new_section_appended_to_lf_file_uses_lf(self):
        out = inifile.set_section(LF_FILE, "Brand New", "x=1")
        self.assertTrue(out.endswith("[Brand New]\nx=1\n\n"))

    def test_new_section_on_empty_text_defaults_to_lf(self):
        out = inifile.set_section("", "Brand New", "x=1")
        self.assertEqual(out, "[Brand New]\nx=1\n\n")


class MixedLineEndings(unittest.TestCase):
    """A file that somehow ended up with both styles (e.g. hand-edited, or
    concatenated from two sources) must not crash the matcher, and the
    section we DO own must still be findable and replaceable."""

    MIXED = (
        "[General]\r\n"
        "foo=1\r\n"
        "\n"
        "[Controls]\n"
        "p1_button_a=button:0\r\n"
        "p1_button_b=button:1\n"
        "\n"
        "[Other]\r\n"
        "bar=2\n"
    )

    def test_section_body_does_not_crash_and_finds_section(self):
        body = inifile.section_body(self.MIXED, "Controls")
        self.assertIsNotNone(body)
        self.assertIn("p1_button_a=button:0", body)
        self.assertIn("p1_button_b=button:1", body)

    def test_set_section_replaces_without_duplicating(self):
        out = inifile.set_section(self.MIXED, "Controls", "p1_button_a=button:9")
        self.assertEqual(out.count("[Controls]"), 1)
        self.assertIn("p1_button_a=button:9", out)
        self.assertIn("[General]", out)
        self.assertIn("[Other]", out)


if __name__ == "__main__":
    unittest.main()
