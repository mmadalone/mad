"""bezel_discover — the dynamic tile filter: a bezel system shows only if a member
ES-DE system is RetroArch AND has a gamelist with games.

Run:  python3 -m unittest tests.test_bezel_discover -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import bezel_discover, es_systems, retroarch_cfg


class HasGames(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._g = es_systems.GAMELISTS
        es_systems.GAMELISTS = self.dir

    def tearDown(self):
        es_systems.GAMELISTS = self._g
        shutil.rmtree(self.dir, ignore_errors=True)

    def _gl(self, system, xml):
        d = self.dir / system
        d.mkdir(parents=True, exist_ok=True)
        (d / "gamelist.xml").write_text(xml, encoding="utf-8")

    def test_counts_game_elements_not_the_wrapper(self):
        # naomi2's empty <gameList></gameList> must read as ZERO games (the bug that
        # would otherwise show an empty stub) — substring "<game" matches "<gameList"!
        self._gl("naomi2", "<gameList></gameList>")
        self.assertFalse(bezel_discover.has_games("naomi2"))
        self._gl("naomi", "<gameList><game><path>./a.zip</path></game></gameList>")
        self.assertTrue(bezel_discover.has_games("naomi"))
        # a <game source="..."> element still counts
        self._gl("attr", "<gameList><game source='ss'><path>./b.zip</path></game></gameList>")
        self.assertTrue(bezel_discover.has_games("attr"))
        # no gamelist file at all (e.g. Game Gear) -> zero
        self.assertFalse(bezel_discover.has_games("gamegear"))


class IsRa(unittest.TestCase):
    def setUp(self):
        self._dc = es_systems.default_command
        self._map = retroarch_cfg.SYSTEM_CORE_MAP

    def tearDown(self):
        es_systems.default_command = self._dc
        retroarch_cfg.SYSTEM_CORE_MAP = self._map

    def test_ra_vs_standalone_vs_defless(self):
        cmds = {
            "atomiswave": "wrap.sh ... -- %EMULATOR_RETROARCH% -L flycast %ROM%",  # RA
            "model3": "wrap.sh ... -- /home/deck/.../supermodel.sh %ROM%",          # standalone
            "genh": "",                                                              # def-less
        }
        es_systems.default_command = lambda s, systems=None: cmds.get(s, "")
        # genh recovered via the core map; cannonball is NOT in it -> not RA
        retroarch_cfg.SYSTEM_CORE_MAP = {"genh": ["Snes9x"]}
        self.assertTrue(bezel_discover.is_ra("atomiswave"))
        self.assertFalse(bezel_discover.is_ra("model3"))      # has a cmd, no RA macro
        self.assertTrue(bezel_discover.is_ra("genh"))         # def-less -> core-map member
        self.assertFalse(bezel_discover.is_ra("cannonball"))  # def-less + not in map


if __name__ == "__main__":
    unittest.main()
