"""Switch per-game empty-section hiding: a game with no add-ons / no cheats must drop those tiles.

switch_games.listing(hide_fn=...) attaches a per-game "hide" list (menu-leaf keys to omit); the C++
per-game browser drops any leaf whose key is in it. Each emu's games handler feeds a hide_fn built
from the addons/cheats modules' has_content(tid). This guards the plumbing (listing) and the two
has_content styles: the citron/eden wrapper (bool of the existing enumerator) and the ryujinx custom
filesystem check.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import citron_cheats_cmds as cc
from lib.madsrv import ryujinx_cheats_cmds as rc
from lib.madsrv import switch_games


class SwitchListingHide(unittest.TestCase):
    def setUp(self):
        self._orig = switch_games._library
        switch_games._library = lambda: {"0100000000001000": {"name": "G", "stem": "g"}}

    def tearDown(self):
        switch_games._library = self._orig

    def test_hide_fn_attaches_hide_list(self):
        rows = switch_games.listing(lambda t: False, None, lambda t: ["addons", "cheats"])
        self.assertEqual(rows[0]["hide"], ["addons", "cheats"])

    def test_empty_hide_and_no_hide_fn_omit_the_field(self):
        self.assertNotIn("hide", switch_games.listing(lambda t: False, None, lambda t: [])[0])
        self.assertNotIn("hide", switch_games.listing(lambda t: False)[0])   # no hide_fn at all


class HasContent(unittest.TestCase):
    _TID = "0100000000001000"

    def test_citron_cheats_has_content(self):
        d = Path(tempfile.mkdtemp())
        orig = cc._LOAD
        cc._LOAD = d
        try:
            self.assertFalse(cc.has_content(self._TID))          # no cheats dir -> empty
            cd = d / self._TID / "MyMod" / "cheats"
            cd.mkdir(parents=True)
            (cd / "1234567890abcdef.txt").write_text("[Infinite HP]\n040000000 1\n", encoding="utf-8")
            self.assertTrue(cc.has_content(self._TID))            # a real cheat -> content
        finally:
            cc._LOAD = orig
            shutil.rmtree(d, ignore_errors=True)

    def test_ryujinx_cheats_has_content(self):
        d = Path(tempfile.mkdtemp())
        orig = rc._cheats_dir
        rc._cheats_dir = lambda tid: d / tid.upper()
        try:
            self.assertFalse(rc.has_content(self._TID))          # no cheats/*.txt -> empty
            cdir = d / self._TID.upper()
            cdir.mkdir(parents=True)
            (cdir / "1122334455667788.txt").write_text("[Moon Jump]\n", encoding="utf-8")
            self.assertTrue(rc.has_content(self._TID))
        finally:
            rc._cheats_dir = orig
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
