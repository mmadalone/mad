"""lib/lutris_games - the Lutris side of the steam backup tile.

Pins: the pga.db + per-game-YAML join (prefix/exe/working_dir), the containment rules
(prefix must exist under $HOME with a drive_c, deny roots refused, a config pointing
at a non-prefix dir refused), the game-dir fallback order (working_dir, then the exe's
parent, never a dir inside the prefix), shared-prefix counting, and the no-PyYAML
fallback parser. Everything runs against a synthetic tmp Lutris layout.

Run: python3 -m unittest tests.test_lutris_games -v
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import lutris_games as lg

GID = 103


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.data = self.home / ".var/app/net.lutris.Lutris/data/lutris"
        (self.data / "games").mkdir(parents=True)
        self._patches = [mock.patch.object(lg, "home", return_value=self.home),
                         mock.patch.object(lg, "data_dir", return_value=self.data)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _db(self, rows):
        """rows = [(id, name, slug, runner, directory, configpath, installed)]"""
        con = sqlite3.connect(self.data / "pga.db")
        con.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, slug TEXT,"
                    " runner TEXT, directory TEXT, configpath TEXT, installed INTEGER)")
        con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def _yml(self, configpath, prefix="", exe="", working_dir=""):
        lines = ["game:"]
        if exe:
            lines.append(f"  exe: {exe}")
        if prefix:
            lines.append(f"  prefix: {prefix}")
        if working_dir:
            lines.append(f"  working_dir: {working_dir}")
        lines += ["wine:", "  version: system"]
        (self.data / "games" / f"{configpath}.yml").write_text("\n".join(lines) + "\n")

    def _prefix(self, rel="Games/tf", users=("deck",)):
        pfx = self.home / rel
        for u in users:
            (pfx / "drive_c/users" / u / "Documents").mkdir(parents=True)
        (pfx / "drive_c/users/Public").mkdir(parents=True)
        return pfx


class PrefixResolution(Base):
    def test_prefix_and_saves_layout_resolve(self):
        pfx = self._prefix()
        gd = self.home / "games" / "Deadpool"
        (gd / "Binaries").mkdir(parents=True)
        self._db([(GID, "Deadpool", "deadpool", "wine", "", "deadpool-1", 1)])
        self._yml("deadpool-1", prefix=str(pfx), exe=str(gd / "Binaries/DP.exe"),
                  working_dir=str(gd / "Binaries"))
        self.assertEqual(lg.prefix_for(GID), pfx.resolve())
        # game dir: the working_dir/exe parent, OUTSIDE the prefix
        self.assertEqual(lg.game_dir_for(GID), (gd / "Binaries").resolve())

    def test_a_dir_without_drive_c_is_not_a_prefix(self):
        # A stray path in a hand-edited config must not let a whole home subtree ride
        # the backup as "the prefix".
        d = self.home / "Documents"
        d.mkdir(parents=True)
        self._db([(GID, "G", "g", "wine", "", "g-1", 1)])
        self._yml("g-1", prefix=str(d))
        self.assertIsNone(lg.prefix_for(GID))

    def test_deny_roots_and_outside_home_are_refused(self):
        self._db([(GID, "G", "g", "wine", "", "g-1", 1)])
        for bad in (self.home / "Emulation/x", self.home / ".var/app/x"):
            (bad / "drive_c").mkdir(parents=True)
            self._yml("g-1", prefix=str(bad))
            self.assertIsNone(lg.prefix_for(GID), bad)
        outside = Path(tempfile.mkdtemp())
        try:
            (outside / "drive_c").mkdir()
            self._yml("g-1", prefix=str(outside))
            self.assertIsNone(lg.prefix_for(GID))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_game_dir_inside_the_prefix_is_covered_by_the_prefix(self):
        pfx = self._prefix()
        inner = pfx / "drive_c/Games/TF"
        inner.mkdir(parents=True)
        self._db([(GID, "G", "g", "wine", "", "g-1", 1)])
        self._yml("g-1", prefix=str(pfx), working_dir=str(inner))
        self.assertIsNone(lg.game_dir_for(GID))

    def test_missing_db_or_config_is_none(self):
        self.assertIsNone(lg.prefix_for(GID))         # no db at all
        self._db([(GID, "G", "g", "wine", "", "g-1", 1)])
        self.assertEqual(lg.game_config(GID), {})     # db row, no yml


class SharedPrefix(Base):
    def test_counts_other_installed_games_on_the_same_prefix(self):
        # Real on this Deck: FoC/Devastation/Ultimate Spider-Man/Deadpool share one.
        pfx = self._prefix()
        self._db([(103, "FoC", "foc", "wine", "", "foc-1", 1),
                  (104, "Devastation", "dev", "wine", "", "dev-1", 1),
                  (105, "USM", "usm", "wine", "", "usm-1", 1),
                  (200, "Uninstalled", "u", "wine", "", "u-1", 0),
                  (201, "Elsewhere", "e", "wine", "", "e-1", 1)])
        for cp in ("foc-1", "dev-1", "usm-1", "u-1"):
            self._yml(cp, prefix=str(pfx))
        other = self._prefix(rel="games/Prefix")
        self._yml("e-1", prefix=str(other))
        self.assertEqual(lg.shared_prefix_count(103), 2)   # dev + usm; not the
        self.assertEqual(lg.shared_prefix_count(201), 0)   # uninstalled, not itself


class YamlFallback(Base):
    def test_game_block_parses_without_pyyaml(self):
        text = ("game:\n  args: ''\n  exe: /home/deck/games/DP.exe\n"
                "  prefix: /home/deck/Games/tf\n  working_dir: /home/deck/games/\n"
                "wine:\n  version: system\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            got = lg._parse_game_block(text)
        self.assertEqual(got["prefix"], "/home/deck/Games/tf")
        self.assertEqual(got["exe"], "/home/deck/games/DP.exe")

    def test_keys_outside_the_game_block_are_ignored(self):
        text = ("script:\n  game:\n    prefix: /evil\n"
                "game:\n  prefix: /home/deck/Games/tf\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            got = lg._parse_game_block(text)
        self.assertEqual(got["prefix"], "/home/deck/Games/tf")


if __name__ == "__main__":
    unittest.main()
