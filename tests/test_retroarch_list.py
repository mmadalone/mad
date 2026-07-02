"""retroarch.list — the RetroArch hub tile (Phase 2 + Phase 3). Mirrors the
Standalones tile/section contract so the C++ GuiMadPageStandalones can render
it. Wires the 5 sections whose pages already exist or are the new thin Phase 3
pages (Settings group, Controllers, Per-game, Input mapping, Bezels)."""
import tempfile
import unittest
from pathlib import Path

from lib import retroarch_cfg
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import standalones_cmds


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

    def test_one_tile_five_sections_in_order(self):
        tiles = rs._ra_hub_tiles()
        self.assertEqual(len(tiles), 1)
        t = tiles[0]
        self.assertEqual(t["key"], "retroarch")
        self.assertEqual(t["label"], "RetroArch")
        self.assertEqual([s["kind"] for s in t["sections"]],
                         ["group", "retroarch_input", "racontrollers", "ra_systems", "bezels"])

    def test_per_game_section_shape(self):
        section = rs._ra_hub_tiles()[0]["sections"][3]
        self.assertEqual(section["kind"], "ra_systems")
        self.assertEqual(section["label"], "Per-game")
        self.assertEqual(section["title"], "RetroArch — Per-game")
        # plain ASCII sublabel (no em/en-dash, no arrow) — only page TITLES use
        # the hub's em-dash convention.
        for ch in "—–→←":
            self.assertNotIn(ch, section["sublabel"])

    def test_settings_group_nests_all_categories(self):
        group = rs._ra_hub_tiles()[0]["sections"][0]
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
        # every section dict uses the same keys a Standalones section does
        want = {"label", "sublabel", "kind"}
        for s in rs._ra_hub_tiles()[0]["sections"]:
            self.assertTrue(want.issubset(s.keys()), s)


if __name__ == "__main__":
    unittest.main()
