"""retroarch.list — the RetroArch hub tile. Sections: Settings (group), Input
mapping, Global default (racontrollers -> global order editor), Per-system
settings (priority_scopes -> the two-grid GuiMadPagePriority), Per-game
(ra_systems), Bezels."""
import tempfile
import unittest
from pathlib import Path

from lib import retroarch_cfg
from lib.madsrv import retroarch_settings as rs


class RetroArchListTest(unittest.TestCase):
    def setUp(self):
        self._orig = retroarch_cfg.RA_GLOBAL_CFG
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "retroarch.cfg"
        self.cfg.write_text("", encoding="utf-8")          # RA present
        retroarch_cfg.RA_GLOBAL_CFG = self.cfg

    def tearDown(self):
        retroarch_cfg.RA_GLOBAL_CFG = self._orig
        self._tmp.cleanup()

    def _sections(self):
        tiles = rs._ra_hub_tiles()
        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0]["key"], "retroarch")
        return tiles[0]["sections"]

    def test_sections_in_order(self):
        self.assertEqual([(s["label"], s["kind"]) for s in self._sections()],
                         [("Settings", "group"),
                          ("Input mapping", "retroarch_input"),
                          ("Global default", "racontrollers"),
                          ("Per-system settings", "priority_scopes"),
                          ("Per-game", "ra_systems"),
                          ("Bezels", "bezels")])

    def test_persystem_settings_is_priority_scopes_leaf(self):
        persys = next(s for s in self._sections() if s["label"] == "Per-system settings")
        self.assertEqual(persys["kind"], "priority_scopes")
        self.assertNotIn("sections", persys)   # a leaf that opens the two-grid page

    def test_global_default_opens_racontrollers(self):
        gd = next(s for s in self._sections() if s["label"] == "Global default")
        self.assertEqual(gd["kind"], "racontrollers")

    def test_per_game_section_shape(self):
        section = next(s for s in self._sections() if s["label"] == "Per-game")
        self.assertEqual(section["kind"], "ra_systems")
        for ch in "—–→←":                       # ASCII-only sublabels
            self.assertNotIn(ch, section["sublabel"])

    def test_settings_group_nests_all_categories(self):
        group = self._sections()[0]
        self.assertEqual(group["kind"], "group")
        self.assertEqual([s["arg"] for s in group["sections"]],
                         list(rs.CATEGORIES.keys()))
        for s, (title, _g) in zip(group["sections"], rs.CATEGORIES.values()):
            self.assertEqual(s["kind"], "settings")
            self.assertIn(title, s["title"])

    def test_hidden_when_ra_absent(self):
        retroarch_cfg.RA_GLOBAL_CFG = Path(self._tmp.name) / "nope.cfg"
        self.assertEqual(rs._ra_hub_tiles(), [])

    def test_section_shape_matches_standalones_contract(self):
        want = {"label", "sublabel", "kind"}
        for s in self._sections():
            self.assertTrue(want.issubset(s.keys()), s)


if __name__ == "__main__":
    unittest.main()
