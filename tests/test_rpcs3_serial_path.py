"""rpcs3_games: reading a PS3 serial out of the disc PATH when games.yml cannot help.

RPCS3's games.yml only registers games it was pointed at as a disc or a folder. A title
INSTALLED to the virtual hard drive is never written there, so games.yml alone can never
resolve it and MAD dropped it from every per-game picker with no way for the user to fix
it (verified live: TMNT Turtles in Time Re-Shelled and TMNT Out of the Shadows).

Covers: the virtual-hard-drive layout resolving; the dir-style [SERIAL] tag resolving;
games.yml winning over a conflicting path; the fallback still running when PyYAML or
games.yml is absent (the guard-order trap: those early returns must skip only the yml
lookup, not the fallback); and the decoys on this Deck that must NOT resolve.

Run:  python3 -m unittest tests.test_rpcs3_serial_path -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import rpcs3_games

_HDD = "/data/rpcs3/dev_hdd0/game"


class SerialFromPath(unittest.TestCase):
    """_serial_from_path() alone: no games.yml involved at all."""

    def test_virtual_hard_drive_install_resolves(self):
        self.assertEqual(
            rpcs3_games._serial_from_path(f"{_HDD}/NPUB30107/USRDIR/EBOOT.BIN"), "NPUB30107")

    def test_bracket_tagged_directory_resolves(self):
        self.assertEqual(
            rpcs3_games._serial_from_path("/roms/ps3/Asura's Wrath [BLUS30721]/PS3_GAME/USRDIR/EBOOT.BIN"),
            "BLUS30721")

    def test_gamedata_sibling_is_not_a_serial(self):
        """NPEA00362GAMEDATA is a real directory in dev_hdd0/game on this Deck. It carries a
        serial as a PREFIX but is save data, not a game, and must never resolve."""
        self.assertIsNone(
            rpcs3_games._serial_from_path(f"{_HDD}/NPEA00362GAMEDATA/USRDIR/EBOOT.BIN"))

    def test_serial_shaped_component_needs_the_dev_hdd0_anchor(self):
        """A bare serial-shaped path component is NOT enough. Without the dev_hdd0/game anchor
        any four-letters-five-digits directory would resolve, which is how a decoy gets picked."""
        self.assertIsNone(rpcs3_games._serial_from_path("/roms/ps3/NPUB30107/EBOOT.BIN"))
        self.assertIsNone(rpcs3_games._serial_from_path("/roms/ps3/BLES01291/PS3_GAME/EBOOT.BIN"))

    def test_two_different_serials_are_refused_not_guessed(self):
        """Writing per-game settings under the wrong serial fails SILENTLY, so an ambiguous
        path must resolve to nothing rather than to a coin flip."""
        self.assertIsNone(
            rpcs3_games._serial_from_path("/roms/ps3/[BLES00001] and [BLES00002]/EBOOT.BIN"))

    def test_same_serial_twice_is_not_ambiguous(self):
        self.assertEqual(
            rpcs3_games._serial_from_path(f"{_HDD}/NPUB30107/x/dev_hdd0/game/NPUB30107/EBOOT.BIN"),
            "NPUB30107")

    def test_empty_and_serial_free_paths(self):
        self.assertIsNone(rpcs3_games._serial_from_path(""))
        self.assertIsNone(rpcs3_games._serial_from_path("/roms/ps3/Some Game.iso"))

    def test_lowercase_serial_does_not_resolve(self):
        """RPCS3 serials are uppercase; matching lowercase would let an unrelated path shape in."""
        self.assertIsNone(rpcs3_games._serial_from_path(f"{_HDD}/npub30107/USRDIR/EBOOT.BIN"))


class PathToSerialOrder(unittest.TestCase):
    """path_to_serial(): games.yml first, path fallback second, and the guard-order trap."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.y = self.d / "games.yml"
        self._gy, self._yaml = rpcs3_games._GAMES_YML, rpcs3_games.yaml
        rpcs3_games._GAMES_YML = self.y

    def tearDown(self):
        rpcs3_games._GAMES_YML, rpcs3_games.yaml = self._gy, self._yaml
        shutil.rmtree(self.d, ignore_errors=True)

    def _desktop(self, disc: str) -> str:
        p = self.d / "Game.desktop"
        p.write_text(f'[Desktop Entry]\nExec=/apps/rpcs3.AppImage --no-gui "{disc}"\n',
                     encoding="utf-8")
        return str(p)

    def test_games_yml_wins_over_the_path(self):
        """RPCS3's own register is authoritative where it has an answer, even when the path
        also carries a serial. We only ever fill a genuine gap."""
        disc = f"{_HDD}/NPUB30107/USRDIR/EBOOT.BIN"
        self.y.write_text(f"BLES99999: {disc}\n", encoding="utf-8")
        self.assertEqual(rpcs3_games.path_to_serial(self._desktop(disc)), "BLES99999")

    def test_path_fallback_when_games_yml_has_no_entry(self):
        self.y.write_text("BLES99999: /roms/ps3/Something Else.iso\n", encoding="utf-8")
        disc = f"{_HDD}/NPUB31217/USRDIR/EBOOT.BIN"
        self.assertEqual(rpcs3_games.path_to_serial(self._desktop(disc)), "NPUB31217")

    def test_path_fallback_when_games_yml_is_missing(self):
        """THE GUARD-ORDER TRAP. The 'no games.yml' early return must skip only the yml lookup.
        Hoisted into path_to_serial it would skip the fallback too, and a machine with no
        register would resolve nothing at all."""
        self.assertFalse(self.y.exists())
        disc = f"{_HDD}/NPUB31217/USRDIR/EBOOT.BIN"
        self.assertEqual(rpcs3_games.path_to_serial(self._desktop(disc)), "NPUB31217")

    def test_path_fallback_when_pyyaml_is_unavailable(self):
        """Same trap for the missing-PyYAML guard."""
        rpcs3_games.yaml = None
        self.y.write_text("BLES99999: /roms/ps3/Something Else.iso\n", encoding="utf-8")
        disc = f"{_HDD}/NPUB31217/USRDIR/EBOOT.BIN"
        self.assertEqual(rpcs3_games.path_to_serial(self._desktop(disc)), "NPUB31217")

    def test_plain_disc_path_not_a_desktop_shortcut(self):
        self.assertEqual(
            rpcs3_games.path_to_serial(f"{_HDD}/NPUB30107/USRDIR/EBOOT.BIN"), "NPUB30107")

    def test_unresolvable_is_still_none(self):
        self.assertIsNone(rpcs3_games.path_to_serial(self._desktop("/roms/ps3/Mystery.iso")))
        self.assertIsNone(rpcs3_games.path_to_serial(""))


if __name__ == "__main__":
    unittest.main()
