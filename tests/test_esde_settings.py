"""
Tests for esde_settings.set_value — the atomic es_settings.xml writer the
installer uses to select the MAD theme. Pure given (name, value, temp file):
no ES-DE, runs against a temp copy.

Run:  python3 -m unittest tests.test_esde_settings -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import esde_settings

SAMPLE = (
    '<?xml version="1.0"?>\n'
    '<bool name="NavigationSounds" value="true" />\n'
    '<string name="Theme" value="oldtheme" />\n'
    '<int name="SoundVolumeNavigation" value="70" />\n'
)


class SetValue(unittest.TestCase):
    def _tmp(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "es_settings.xml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_replace_existing_returns_true(self):
        p = self._tmp(SAMPLE)
        self.assertTrue(esde_settings.set_value("Theme", "pixel-es-de", settings=p))
        out = p.read_text(encoding="utf-8")
        self.assertIn('<string name="Theme" value="pixel-es-de" />', out)

    def test_only_the_target_line_changes(self):
        p = self._tmp(SAMPLE)
        esde_settings.set_value("Theme", "pixel-es-de", settings=p)
        src = SAMPLE.splitlines()
        out = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(src), len(out))
        diff = [(a, b) for a, b in zip(src, out) if a != b]
        self.assertEqual(len(diff), 1)               # exactly the Theme line
        self.assertIn("Theme", diff[0][0])

    def test_noop_when_already_set(self):
        p = self._tmp(SAMPLE.replace("oldtheme", "pixel-es-de"))
        before = p.read_text(encoding="utf-8")
        self.assertFalse(esde_settings.set_value("Theme", "pixel-es-de", settings=p))
        self.assertEqual(before, p.read_text(encoding="utf-8"))   # byte-identical

    def test_append_when_absent(self):
        p = self._tmp('<?xml version="1.0"?>\n'
                      '<bool name="NavigationSounds" value="true" />\n')
        self.assertTrue(esde_settings.set_value("Theme", "pixel-es-de", settings=p))
        out = p.read_text(encoding="utf-8")
        self.assertIn('<string name="Theme" value="pixel-es-de" />', out)
        self.assertIn('<bool name="NavigationSounds" value="true" />', out)

    def test_missing_file_is_best_effort_false(self):
        p = Path(tempfile.mkdtemp()) / "nope.xml"
        self.assertFalse(esde_settings.set_value("Theme", "pixel-es-de", settings=p))

    def test_value_is_xml_escaped(self):
        p = self._tmp(SAMPLE)
        esde_settings.set_value("Theme", 'a&b"<>', settings=p)
        out = p.read_text(encoding="utf-8")
        self.assertIn('value="a&amp;b&quot;&lt;&gt;"', out)


if __name__ == "__main__":
    unittest.main()
