"""lib/es_find_rules.transform/ensure — the custom es_find_rules.xml carries MAD's dynamic
emulator rules (Citron/Eden/Yuzu/Suyu/pcsx2x6). transform() is pure, additive, idempotent;
ensure_find_rules() writes only on change and creates the file if absent.
Run: python3 -m unittest tests.test_es_find_rules -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import xml.dom.minidom as X
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib import es_find_rules as fr   # noqa: E402

_NAMES = {"CITRON", "EDEN", "YUZU", "SUYU", "PCSX2X6"}


class FindRules(unittest.TestCase):
    def test_canonical_is_valid_xml_with_all_emus(self):
        c = fr._canonical()
        X.parseString(c)                                   # well-formed
        for n in _NAMES:
            self.assertIn(f'name="{n}"', c)

    def test_empty_or_blank_becomes_canonical(self):
        c = fr._canonical()
        self.assertEqual(fr.transform(""), c)
        self.assertEqual(fr.transform("   \n"), c)

    def test_transform_idempotent(self):
        c = fr._canonical()
        self.assertEqual(fr.transform(c), c)

    def test_additive_preserves_user_rules(self):
        user = ('<?xml version="1.0"?>\n<ruleList>\n'
                '    <emulator name="MYEMU"><rule type="staticpath">'
                '<entry>~/x.AppImage</entry></rule></emulator>\n</ruleList>\n')
        m = fr.transform(user)
        self.assertIn('name="MYEMU"', m)                   # user's rule kept
        for n in _NAMES:
            self.assertIn(f'name="{n}"', m)                # ours added
        X.parseString(m)
        self.assertEqual(fr.transform(m), m)               # idempotent after merge

    def test_no_duplicate_when_one_already_present(self):
        # a file that already has CITRON must not get a second CITRON block
        half = ('<?xml version="1.0"?>\n<ruleList>\n' + fr._block("CITRON") + "</ruleList>\n")
        m = fr.transform(half)
        self.assertEqual(m.count('name="CITRON"'), 1)
        self.assertIn('name="EDEN"', m)

    def test_ensure_creates_file_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "custom_systems" / "es_find_rules.xml"
            self.assertTrue(fr.ensure_find_rules(p))       # wrote
            self.assertTrue(p.is_file())
            self.assertFalse(fr.ensure_find_rules(p))      # second call = no change
            self.assertEqual(p.read_text(), fr._canonical())


if __name__ == "__main__":
    unittest.main()
