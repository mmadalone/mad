"""es_gamelist.titles — rom-stem -> human <name> from an ES-DE gamelist, via a
tolerant regex (NOT ElementTree, which crashes on ES-DE's multi-root gamelists).

Run:  python3 -m unittest tests.test_es_gamelist -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from lib import es_gamelist, es_systems

# ES-DE writes <alternativeEmulator> as a SECOND root before <gameList> -> ET.parse raises.
MULTIROOT = """<?xml version="1.0"?>
<alternativeEmulator>
  <label>FinalBurn Neo</label>
</alternativeEmulator>
<gameList>
  <game>
    <path>./xmcota.zip</path>
    <name>X-Men: Children of the Atom</name>
  </game>
  <game source="ScreenScraper">
    <path>./dolphin.zip</path>
    <name>Dolphin Blue</name>
  </game>
</gameList>
"""

EMPTY = '<?xml version="1.0"?>\n<gameList></gameList>\n'

ENTITY = ('<?xml version="1.0"?>\n<gameList>\n'
          '  <game><path>./foo&amp;bar.zip</path><name>Foo &amp; Bar</name></game>\n'
          '</gameList>\n')


class EsGamelistTitles(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._saved = es_systems.GAMELISTS
        es_systems.GAMELISTS = self.dir
        es_gamelist.titles.cache_clear()

    def tearDown(self):
        es_systems.GAMELISTS = self._saved
        es_gamelist.titles.cache_clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, system, xml):
        d = self.dir / system
        d.mkdir(parents=True, exist_ok=True)
        (d / "gamelist.xml").write_text(xml, encoding="utf-8")

    def test_multiroot_parses_where_etree_crashes(self):
        self._write("fba", MULTIROOT)
        # ET.parse genuinely fails on this structure (so regex is REQUIRED, not a nicety)
        with self.assertRaises(ET.ParseError):
            ET.parse(self.dir / "fba" / "gamelist.xml")
        t = es_gamelist.titles("fba")
        self.assertEqual(t["xmcota"], "X-Men: Children of the Atom")
        self.assertEqual(t["dolphin"], "Dolphin Blue")   # <game source="..."> variant counts
        self.assertEqual(len(t), 2)

    def test_empty_and_missing_return_empty(self):
        self._write("naomi2", EMPTY)
        self.assertEqual(es_gamelist.titles("naomi2"), {})
        self.assertEqual(es_gamelist.titles("nonexistent"), {})

    def test_entity_unescaped_in_name_and_stem(self):
        self._write("arcade", ENTITY)
        t = es_gamelist.titles("arcade")
        self.assertEqual(t.get("foo&bar"), "Foo & Bar")   # both name + stem-key unescaped

    def test_titles_for_unions_members(self):
        self._write("genesis", '<gameList><game><path>./sonic.md</path><name>Sonic</name></game></gameList>')
        self._write("megadrive", '<gameList><game><path>./gunstar.md</path><name>Gunstar</name></game></gameList>')
        u = es_gamelist.titles_for(["genesis", "megadrive"])
        self.assertEqual(u.get("sonic"), "Sonic")
        self.assertEqual(u.get("gunstar"), "Gunstar")


if __name__ == "__main__":
    unittest.main()
