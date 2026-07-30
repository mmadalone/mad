"""lib/steam_shortcuts — the single owner of non-Steam shortcut facts.

Pins: the structural vdf parse (appid/exe/StartDir from the SAME block, keys
case-insensitive, appids stored signed-int32 but exposed UNSIGNED), the rungameid
algebra a launcher .sh round-trips through, the gamelist join (alive vs dead
shortcuts), and game_dir()'s containment (deny roots, $HOME-only, no compatdata).
Everything is synthetic/monkeypatched — no real Steam install is touched.

Run: python3 -m unittest tests.test_steam_shortcuts -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import steam_shortcuts as ss

# A realistic shortcut appid: > 2^31, so the vdf's signed int32 form is NEGATIVE.
APPID = 0xF4E30020


def _blk(idx: int, appid: int, name=None, exe=None, start_dir=None,
         name_key: bytes = b"appname") -> bytes:
    b = b"\x00" + str(idx).encode() + b"\x00"
    b += b"\x02appid\x00" + (appid & 0xFFFFFFFF).to_bytes(4, "little")
    if name is not None:
        b += b"\x01" + name_key + b"\x00" + name.encode("utf-8") + b"\x00"
    if exe is not None:
        b += b"\x01Exe\x00" + exe.encode("utf-8") + b"\x00"
    if start_dir is not None:
        b += b"\x01StartDir\x00" + start_dir.encode("utf-8") + b"\x00"
    b += b"\x08"
    return b


def _vdf(*blocks: bytes) -> bytes:
    return b"\x00shortcuts\x00" + b"".join(blocks) + b"\x08" + b"\x08"


class ParseShortcuts(unittest.TestCase):
    def test_appid_is_exposed_unsigned(self):
        # The vdf stores int32 (negative for real shortcut appids); the rungameid
        # algebra needs the unsigned low-32 form, so parse_shortcuts keys on that.
        got = ss.parse_shortcuts(_vdf(_blk(0, APPID, "Punisher")))
        self.assertEqual(list(got), [APPID])
        self.assertEqual(got[APPID]["name"], "Punisher")

    def test_exe_unquoted_and_launch_options_dropped(self):
        got = ss.parse_shortcuts(_vdf(
            _blk(0, APPID, "OutRun", exe='"/home/deck/games/OutRun/OR2006.exe" -wide',
                 start_dir='"/home/deck/games/OutRun/"')))
        self.assertEqual(got[APPID]["exe"], "/home/deck/games/OutRun/OR2006.exe")
        self.assertEqual(got[APPID]["start_dir"], "/home/deck/games/OutRun/")

    def test_keys_match_case_insensitively(self):
        # Steam's key casing has varied (appname vs AppName) — same guarantee the
        # rungameid pairing tests pin, extended to the new fields.
        got = ss.parse_shortcuts(_vdf(_blk(0, APPID, "Beta", name_key=b"AppName",
                                           exe="/usr/bin/flatpak run x")))
        self.assertEqual(got[APPID]["name"], "Beta")
        self.assertEqual(got[APPID]["exe"], "/usr/bin/flatpak")

    def test_malformed_vdf_yields_nothing(self):
        self.assertEqual(ss.parse_shortcuts(b"\x00shortcuts\x00\x00\x00junk\xff"), {})


class RungameidAlgebra(unittest.TestCase):
    def test_roundtrip(self):
        rgid = ss.rungameid_of(APPID)
        self.assertTrue(ss.is_nonsteam(rgid))
        self.assertEqual(ss.appid_of(rgid), APPID)

    def test_steam_proper_ids_are_not_nonsteam(self):
        self.assertFalse(ss.is_nonsteam(2357570))          # a plain Steam appid
        self.assertFalse(ss.is_nonsteam((APPID << 32)))    # wrong low-32 marker

    def test_launcher_rungameid_reads_the_sh(self):
        rgid = ss.rungameid_of(APPID)
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(f"#!/bin/sh\nexec steam steam://rungameid/{rgid}\n")
            p = fh.name
        try:
            self.assertEqual(ss.launcher_rungameid(p), rgid)
        finally:
            os.unlink(p)

    def test_launcher_rungameid_none_when_unreadable(self):
        self.assertIsNone(ss.launcher_rungameid("/nonexistent/launcher.sh"))


class NonsteamGames(unittest.TestCase):
    """The gamelist join: which steam-system launchers are non-Steam shortcuts, and
    whether each is still alive in Steam."""

    def _games(self, records, roms, shortcuts):
        from lib import es_gamelist, game_files
        with mock.patch.object(es_gamelist, "visible_records", return_value=records), \
             mock.patch.object(game_files, "resolve_rom",
                               side_effect=lambda sys_, stem: roms.get(stem, [])), \
             mock.patch.object(ss, "nonsteam_shortcuts", return_value=shortcuts):
            return ss.nonsteam_games()

    def _sh(self, tmp, stem, rgid) -> str:
        p = Path(tmp) / f"{stem}.sh"
        p.write_text(f"exec steam steam://rungameid/{rgid}\n")
        return str(p)

    def test_join_alive_dead_and_steam_proper(self):
        with tempfile.TemporaryDirectory() as tmp:
            alive_sh = self._sh(tmp, "Punisher", ss.rungameid_of(APPID))
            dead_sh = self._sh(tmp, "Manhunt", ss.rungameid_of(APPID + 1))
            proper_sh = self._sh(tmp, "Huntdown", 2357570)   # Steam-proper: plain appid
            games = self._games(
                {"punisher": {"stem": "Punisher", "name": "The Punisher"},
                 "manhunt": {"stem": "Manhunt", "name": "Manhunt"},
                 "huntdown": {"stem": "Huntdown", "name": "Huntdown"},
                 "norom": {"stem": "NoRom", "name": "No Rom"}},
                {"Punisher": [alive_sh], "Manhunt": [dead_sh], "Huntdown": [proper_sh]},
                {APPID: {"name": "The Punisher", "exe": "", "start_dir": ""}})
            self.assertEqual(set(games), {"Punisher", "Manhunt"})   # proper + rom-less excluded
            self.assertTrue(games["Punisher"]["alive"])
            self.assertFalse(games["Manhunt"]["alive"])              # dead shortcut flagged
            self.assertEqual(games["Punisher"]["appid"], APPID)
            self.assertEqual(games["Punisher"]["name"], "The Punisher")


class GameDir(unittest.TestCase):
    """game_dir(): exists + inside $HOME + not compatdata + not a deny root."""

    def _game_dir(self, home: Path, start_dir: str, exe: str = ""):
        sc = {APPID: {"name": "G", "exe": exe, "start_dir": start_dir}}
        with mock.patch.object(ss, "home", return_value=home), \
             mock.patch.object(ss, "nonsteam_shortcuts", return_value=sc):
            return ss.game_dir(APPID)

    def test_repack_dir_inside_home_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gd = home / "games" / "OutRun2006"
            gd.mkdir(parents=True)
            self.assertEqual(self._game_dir(home, str(gd)), gd.resolve())

    def test_exe_parent_is_the_fallback_when_startdir_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gd = home / "Games" / "TMNT"
            gd.mkdir(parents=True)
            self.assertEqual(self._game_dir(home, "", exe=str(gd / "game.exe")),
                             gd.resolve())

    def test_deny_roots_and_escapes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for rel in ("Emulation/roms/ps2", ".config/Cemu", "ES-DE/gamelists",
                        "Applications", ".local/share/x", "Downloads/x"):
                d = home / rel
                d.mkdir(parents=True, exist_ok=True)
                self.assertIsNone(self._game_dir(home, str(d)), rel)
            self.assertIsNone(self._game_dir(home, str(home)))       # $HOME itself
            outside = Path(tempfile.mkdtemp())                        # outside $HOME
            try:
                self.assertIsNone(self._game_dir(home, str(outside)))
            finally:
                outside.rmdir()

    def test_compatdata_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cd = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            cd.mkdir(parents=True)
            self.assertIsNone(self._game_dir(home, str(cd)))

    def test_missing_dir_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertIsNone(self._game_dir(home, str(home / "games" / "gone")))


class IsLutris(unittest.TestCase):
    def _sc(self, exe: str = "", options: str = ""):
        sc = {APPID: {"name": "G", "exe": exe, "start_dir": "", "options": options}}
        return mock.patch.object(ss, "nonsteam_shortcuts", return_value=sc)

    def test_lutris_uri_in_the_launch_options_is_lutris(self):
        # The REAL shape on this Deck: exe is /usr/bin/flatpak for every flatpak
        # shortcut; only the options say WHICH app runs.
        with self._sc(exe="/usr/bin/flatpak",
                      options="run net.lutris.Lutris lutris:rungameid/117"):
            self.assertTrue(ss.is_lutris(APPID))
            self.assertEqual(ss.lutris_game_id(APPID), 117)

    def test_lutris_uri_in_the_exe_works_too(self):
        with self._sc(exe="lutris:rungameid/42"):
            self.assertEqual(ss.lutris_game_id(APPID), 42)

    def test_lutris_uri_in_the_exe_ARGUMENTS_is_found(self):
        # A %command%-wrapped shortcut (lsfg frame-gen etc.) keeps the payload as the
        # exe field's own arguments - the cleaned `exe` drops them, `exe_raw` keeps them.
        sc = {APPID: {"name": "G", "exe": "/usr/bin/flatpak",
                      "exe_raw": '"/usr/bin/flatpak" run net.lutris.Lutris '
                                 "lutris:rungameid/1",
                      "start_dir": "", "options": "~/lsfg %command%"}}
        with mock.patch.object(ss, "nonsteam_shortcuts", return_value=sc):
            self.assertEqual(ss.lutris_game_id(APPID), 1)

    def test_a_plain_flatpak_shortcut_is_NOT_lutris(self):
        # Kodi/Spotify/the mGBA Pokemon shortcuts: flatpak exe, no lutris: URI. The old
        # basename heuristic classified ALL of them as Lutris.
        with self._sc(exe="/usr/bin/flatpak", options="run tv.kodi.Kodi"):
            self.assertFalse(ss.is_lutris(APPID))
            self.assertIsNone(ss.lutris_game_id(APPID))

    def test_a_plain_proton_exe_is_not(self):
        with self._sc(exe="/home/deck/games/OutRun/OR2006.exe"):
            self.assertFalse(ss.is_lutris(APPID))


class LaunchOptions(unittest.TestCase):
    def test_parse_keeps_the_launch_options(self):
        data = _vdf(_blk(0, APPID, "Deadpool", exe='"/usr/bin/flatpak"'))
        # append an options field manually: rebuild the block with LaunchOptions
        blk = (b"\x00" + b"0" + b"\x00"
               + b"\x02appid\x00" + (APPID & 0xFFFFFFFF).to_bytes(4, "little")
               + b"\x01appname\x00Deadpool\x00"
               + b"\x01Exe\x00\"/usr/bin/flatpak\"\x00"
               + b"\x01LaunchOptions\x00run net.lutris.Lutris lutris:rungameid/117\x00"
               + b"\x08")
        got = ss.parse_shortcuts(b"\x00shortcuts\x00" + blk + b"\x08\x08")
        self.assertEqual(got[APPID]["options"],
                         "run net.lutris.Lutris lutris:rungameid/117")


if __name__ == "__main__":
    unittest.main()
