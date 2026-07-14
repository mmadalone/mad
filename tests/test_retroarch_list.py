"""retroarch.list - the RetroArch hub tile, canonical shape (P8): System / Video /
Audio / Input canonical groups, then Per-system controllers (priority_scopes -> the
two-grid GuiMadPagePriority), Bezels, and a frozen Per-game (ra_systems). Built by
retroarch_settings._ra_hub_tiles via mad_tree.section_order (the old flat "Settings"
umbrella is dissolved; the 7 raset_* pages distribute across the groups)."""
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
        # Canonical top level (P8): System/Video/Audio/Input groups, then the two extras
        # (Per-system controllers, Bezels), then the frozen Per-game slot.
        self.assertEqual([(s["label"], s["kind"]) for s in self._sections()],
                         [("System", "group"),
                          ("Video", "group"),
                          ("Audio", "settings"),
                          ("Input", "group"),
                          ("Per-system controllers", "priority_scopes"),
                          ("Bezels", "bezels"),
                          ("Per-game", "ra_systems")])

    def test_persystem_controllers_is_priority_scopes_leaf(self):
        persys = next(s for s in self._sections() if s["label"] == "Per-system controllers")
        self.assertEqual(persys["kind"], "priority_scopes")
        self.assertNotIn("sections", persys)   # a leaf that opens the two-grid page

    def test_global_default_opens_racontrollers(self):
        inp = next(s for s in self._sections() if s["label"] == "Input")   # now inside the Input group
        gd = next(s for s in inp["sections"] if s["label"] == "Default controller order")
        self.assertEqual(gd["kind"], "racontrollers")

    def test_per_game_section_shape(self):
        section = next(s for s in self._sections() if s["label"] == "Per-game")
        self.assertEqual(section["kind"], "ra_systems")
        for ch in "—–→←":                       # ASCII-only sublabels
            self.assertNotIn(ch, section["sublabel"])

    def test_all_category_pages_reachable(self):
        # The old single "Settings" group is dissolved; the 7 raset_* category pages now live
        # across the System/Video/Audio/Input canonical groups (Audio is a direct top-level leaf).
        # Every one must still be reachable as a settings row (guards no-page-lost).
        args = set()
        for s in self._sections():
            if s["kind"] == "settings":
                args.add(s["arg"])
            for sub in s.get("sections", []):
                if sub["kind"] == "settings":
                    args.add(sub["arg"])
        self.assertEqual(args, set(rs.CATEGORIES.keys()))

    def test_hidden_when_ra_absent(self):
        retroarch_cfg.RA_GLOBAL_CFG = Path(self._tmp.name) / "nope.cfg"
        self.assertEqual(rs._ra_hub_tiles(), [])

    def test_section_shape_matches_standalones_contract(self):
        want = {"label", "sublabel", "kind"}
        for s in self._sections():
            self.assertTrue(want.issubset(s.keys()), s)


if __name__ == "__main__":
    unittest.main()
