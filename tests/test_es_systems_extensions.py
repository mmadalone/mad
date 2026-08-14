"""es_systems.extensions() -- system -> accepted rom extensions, parsed from ES-DE's
<extension> element the same way _parse_fullnames reads <fullname> (bundled, then
custom overrides by system name, cached for the run).

Covers: dual-case dedup + first-seen order; custom overriding bundled; a missing
<extension>, an unknown system, and unparseable XML all returning ().

Run:  python3 -m unittest tests.test_es_systems_extensions -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import es_systems


def _write(tmp: Path, name: str, xml: str) -> Path:
    p = tmp / name
    p.write_text(xml, encoding="utf-8")
    return p


class ParseExtensions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # es_systems caches the merged map for the run -- clear it before AND after so
        # this test's fake BUNDLED/CUSTOM never leak into (or get overwritten by) another
        # test module's real-data assertions.
        es_systems._extensions_map.cache_clear()
        self._save = (es_systems.BUNDLED, es_systems.CUSTOM)

    def tearDown(self):
        es_systems.BUNDLED, es_systems.CUSTOM = self._save
        es_systems._extensions_map.cache_clear()

    def test_dual_case_deduped_first_seen_order(self):
        xml = ("<systemList><system><name>ps2</name>"
               "<extension>.iso .ISO .chd .CHD .bin .BIN</extension></system></systemList>")
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", xml)
        es_systems.CUSTOM = self.tmp / "no-such-custom.xml"
        self.assertEqual(es_systems.extensions("ps2"), (".iso", ".chd", ".bin"))

    def test_custom_overrides_bundled_for_same_system(self):
        bundled = ("<systemList><system><name>ps2</name>"
                   "<extension>.iso .chd</extension></system></systemList>")
        custom = ("<systemList><system><name>ps2</name>"
                  "<extension>.bin .cue</extension></system></systemList>")
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", bundled)
        es_systems.CUSTOM = _write(self.tmp, "custom.xml", custom)
        # custom REPLACES bundled wholesale for this system name, same as fullnames()/load_systems()
        self.assertEqual(es_systems.extensions("ps2"), (".bin", ".cue"))

    def test_missing_extension_element_is_empty(self):
        xml = "<systemList><system><name>nes</name><fullname>NES</fullname></system></systemList>"
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", xml)
        es_systems.CUSTOM = self.tmp / "no-such-custom.xml"
        self.assertEqual(es_systems.extensions("nes"), ())

    def test_unknown_system_is_empty(self):
        xml = "<systemList><system><name>ps2</name><extension>.iso</extension></system></systemList>"
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", xml)
        es_systems.CUSTOM = self.tmp / "no-such-custom.xml"
        self.assertEqual(es_systems.extensions("nonexistent-system"), ())

    def test_unparseable_xml_is_empty(self):
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", "<not><valid xml")
        es_systems.CUSTOM = self.tmp / "no-such-custom.xml"
        self.assertEqual(es_systems.extensions("ps2"), ())

    def test_short_or_bare_dot_tokens_dropped(self):
        # "." alone (no char after the dot) and a bare word with no leading dot must be
        # dropped silently, never guessed into a real extension.
        xml = ("<systemList><system><name>weird</name>"
               "<extension>.iso . bareword .a</extension></system></systemList>")
        es_systems.BUNDLED = _write(self.tmp, "bundled.xml", xml)
        es_systems.CUSTOM = self.tmp / "no-such-custom.xml"
        self.assertEqual(es_systems.extensions("weird"), (".iso", ".a"))


class RealData(unittest.TestCase):
    """Sanity-check against the REAL bundled/custom es_systems.xml on this machine (no
    patching): confirms the parse against known-good, previously-verified upstream data."""

    def test_ps2_real_extensions(self):
        ext = es_systems.extensions("ps2")
        self.assertIn(".iso", ext)
        self.assertIn(".chd", ext)
        self.assertIn(".bin", ext)
        self.assertNotIn(".cue", ext)          # genuinely absent upstream -- not a bug to "fix"

    def test_genh_real_extensions(self):
        ext = es_systems.extensions("genh")
        self.assertIn(".cue", ext)
        self.assertIn(".zip", ext)


if __name__ == "__main__":
    unittest.main()
