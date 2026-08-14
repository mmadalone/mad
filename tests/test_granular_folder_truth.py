"""Backup per-game rows: the ROM FOLDER counts too, not just ES-DE's gamelist.

ES-DE only writes a <game> into its gamelist once something about that game changes (scraped,
played, favourited, edited). A rom copied in and not yet touched had no entry, so it could not
be picked for a per-game backup and was silently skipped by "back up everything". Measured on
this Deck 2026-08-14: 57 games invisible, 53 of them in genh.

The contract that matters most here is the one that must NOT change: a game the user
deliberately HID in ES-DE stays hidden. The merge checks the folder against records(), which
includes hidden games, so a hidden rom is already known to the gamelist and is never re-added
through the back door. This Deck has 118 of them.

Run:  python3 -m unittest tests.test_granular_folder_truth -v
"""
from __future__ import annotations

import unittest

from lib.madsrv import granular_cmds as gc


def _rec(name, *, hidden=False):
    return {"name": name, "stem": name, "desc": "", "altemulator": "", "hidden": hidden}


class LibraryRows(unittest.TestCase):
    def setUp(self):
        self._saved = (gc.es_gamelist.records, gc.es_gamelist.visible_records,
                       gc.rom_folder.entries)
        self.records = {}                 # everything ES-DE knows, hidden included
        self.folder = {}                  # what is actually in the rom folder
        self.show_hidden = False
        gc.es_gamelist.records = lambda system: dict(self.records)
        gc.es_gamelist.visible_records = self._visible
        gc.rom_folder.entries = lambda system: dict(self.folder)

    def tearDown(self):
        (gc.es_gamelist.records, gc.es_gamelist.visible_records,
         gc.rom_folder.entries) = self._saved

    def _visible(self, system):
        if self.show_hidden:
            return dict(self.records)
        return {k: r for k, r in self.records.items() if not r.get("hidden")}

    def _in_folder(self, *stems):
        self.folder = {s.lower(): {"stem": s, "path": f"/roms/nes/{s}.zip", "kind": "file"}
                       for s in stems}

    # -- the gap this closes -----------------------------------------------
    def test_folder_only_game_becomes_backupable(self):
        self.records = {"known": _rec("Known")}
        self._in_folder("Known", "Never Scraped")
        rows = gc._library_rows("nes")
        self.assertEqual(sorted(rows), ["known", "never scraped"])
        self.assertEqual(rows["never scraped"]["name"], "Never Scraped")

    def test_gamelist_record_wins_for_a_stem_both_know(self):
        """ES-DE's scraped name and its original spelling must survive the merge."""
        self.records = {"sonic the hedgehog": _rec("Sonic The Hedgehog (World)")}
        self._in_folder("sonic the hedgehog")
        rows = gc._library_rows("nes")
        self.assertEqual(rows["sonic the hedgehog"]["name"], "Sonic The Hedgehog (World)")

    # -- the contract that must NOT change ---------------------------------
    def test_a_hidden_game_is_not_re_added_by_the_folder(self):
        """THE ONE THAT IS WORST TO GET WRONG. The rom is on disk and the user hid it on purpose.
        Checking the folder against records() (hidden included) rather than visible_records() is
        what keeps it hidden; getting that wrong resurfaces curation the user performed."""
        self.records = {"shown": _rec("Shown"), "hidden one": _rec("Hidden One", hidden=True)}
        self._in_folder("shown", "hidden one")
        self.assertEqual(sorted(gc._library_rows("nes")), ["shown"])

    def test_hidden_games_reappear_when_es_de_is_showing_them(self):
        """The mirror of the above: with ES-DE's own 'Show hidden games' on, they come back,
        because visible_records() returns them and we simply pass that through."""
        self.records = {"shown": _rec("Shown"), "hidden one": _rec("Hidden One", hidden=True)}
        self._in_folder("shown", "hidden one")
        self.show_hidden = True
        self.assertEqual(sorted(gc._library_rows("nes")), ["hidden one", "shown"])

    # -- shape and ordering -------------------------------------------------
    def test_folder_row_has_the_same_shape_as_a_gamelist_record(self):
        self.records = {"known": _rec("Known")}
        self._in_folder("New Game")
        row = gc._library_rows("nes")["new game"]
        self.assertEqual(set(row), set(_rec("x")))
        self.assertEqual(row["stem"], "New Game")
        self.assertFalse(row["hidden"])

    def test_an_empty_name_does_not_sort_to_the_top(self):
        """The browse list sorts on `rec.get("name") or stem`. With `.get("name", stem)` a record
        whose name key is present but EMPTY sorts as '' and piles at the top of the list."""
        rows = {"zelda": {"name": ""}, "alpha": {"name": "Alpha"}}
        order = [k for k, _ in sorted(rows.items(),
                                      key=lambda kv: (kv[1].get("name") or kv[0]).lower())]
        self.assertEqual(order, ["alpha", "zelda"])

    # -- degradation --------------------------------------------------------
    def test_unreadable_folder_degrades_to_the_gamelist(self):
        self.records = {"known": _rec("Known")}
        self.folder = {}
        self.assertEqual(sorted(gc._library_rows("nes")), ["known"])

    def test_folder_read_that_raises_does_not_break_the_picker(self):
        self.records = {"known": _rec("Known")}

        def _boom(system):
            raise RuntimeError("card yanked")

        gc.rom_folder.entries = _boom
        self.assertEqual(sorted(gc._library_rows("nes")), ["known"])

    def test_steam_is_left_alone(self):
        """Steam games do not live in a rom folder; their own enumerator owns that tile."""
        self.records = {"a game": _rec("A Game")}
        self._in_folder("something else")
        self.assertEqual(sorted(gc._library_rows("steam")), ["a game"])


if __name__ == "__main__":
    unittest.main()
