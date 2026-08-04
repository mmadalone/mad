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

    def test_malformed_vdf_raises_in_strict_mode(self):
        # strict=True lets a caller (steam-collection-gen) tell corrupt from empty.
        with self.assertRaises((ValueError, IndexError)):
            ss.parse_shortcuts(b"\x00shortcuts\x00\x00\x00junk\xff", strict=True)

    def test_truncated_at_entry_boundary_is_corrupt_not_partial(self):
        # A crash/power-loss truncation right after a complete block (missing map
        # terminators) must NOT yield a silently-partial dict: default mode returns
        # {} (never a wrong/incomplete pairing), strict mode raises so the collection
        # gen aborts instead of dropping the truncated-away titles.
        truncated = b"\x00shortcuts\x00" + _blk(0, APPID, "Alpha")  # no 0x08 0x08
        self.assertEqual(ss.parse_shortcuts(truncated), {})
        with self.assertRaises(ValueError):
            ss.parse_shortcuts(truncated, strict=True)

    def test_truncated_mid_int32_is_corrupt(self):
        cut = b"\x00shortcuts\x00" + b"\x000\x00" + b"\x02appid\x00" + b"\x20\x00"
        self.assertEqual(ss.parse_shortcuts(cut), {})
        with self.assertRaises(ValueError):
            ss.parse_shortcuts(cut, strict=True)


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


class GameRoot(unittest.TestCase):
    """game_dir() climbing from Steam's StartDir to the REAL game root.

    Steam sets StartDir to the folder holding the .exe, which for a repack is often a
    subfolder of the game (~/Games/Deadpool/Binaries inside a 22 GB ~/Games/Deadpool).
    Pins both signals: the NAME match widens, the peer container bounds, and with no
    name match nothing moves (widening on a guess could put another game's files in
    this game's backup, and write them back on restore)."""

    OTHER = APPID + 1

    def _resolve(self, home: Path, name: str, start_dir: Path, peers=()):
        sc = {APPID: {"name": name, "exe": "", "start_dir": str(start_dir)}}
        for i, p in enumerate(peers):
            sc[self.OTHER + i] = {"name": f"peer{i}", "exe": "", "start_dir": str(p)}
        with mock.patch.object(ss, "home", return_value=home), \
             mock.patch.object(ss, "nonsteam_shortcuts", return_value=sc):
            return ss.game_dir(APPID)

    def test_exe_subfolder_widens_to_the_named_game_root(self):
        # The reported bug: Deadpool measured 0.05 GB (Binaries) instead of 22 GB.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / "Games" / "Deadpool"
            (root / "Binaries").mkdir(parents=True)
            self.assertEqual(self._resolve(home, "Deadpool", root / "Binaries"),
                             root.resolve())

    def test_the_name_match_ignores_punctuation_and_case(self):
        # Real pairs from this Deck: folder "Ultimate.Spider.Man" / AppName
        # "Ultimate Spider-Man"; "Transformers - War for Cybertron" / "Transformers:
        # War for Cybertron".
        for folder, name in (("Ultimate.Spider.Man", "Ultimate Spider-Man"),
                             ("Transformers - War for Cybertron",
                              "Transformers: War for Cybertron"),
                             ("OutRun2006 Coast 2 Coast", "OutRun 2006: Coast 2 Coast")):
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                root = home / "Games" / folder
                (root / "Binaries").mkdir(parents=True)
                self.assertEqual(self._resolve(home, name, root / "Binaries"),
                                 root.resolve(), folder)

    def test_no_name_match_keeps_the_startdir(self):
        # No positive evidence = no widening. Under-reporting is recoverable; backing up
        # (and restoring over) a sibling game is not.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sub = home / "Games" / "UnrelatedFolder" / "Binaries"
            sub.mkdir(parents=True)
            self.assertEqual(self._resolve(home, "Deadpool", sub), sub.resolve())

    def test_a_folder_holding_two_games_is_never_returned(self):
        # A shortcut whose AppName happens to match the library folder must still not
        # widen onto it: ~/Games holds other games.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            mine = home / "Games" / "Deadpool" / "Binaries"
            mine.mkdir(parents=True)
            other = home / "Games" / "Manhunt"
            other.mkdir(parents=True)
            got = self._resolve(home, "Games", mine, peers=[other])
            self.assertEqual(got, (home / "Games" / "Deadpool").resolve())

    def test_the_deepest_container_bounds_a_nested_layout(self):
        # ~/Games AND ~/Games/Compilation both separate games; the deeper one wins, so a
        # compilation's sibling title is not swept in.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            comp = home / "Games" / "Compilation"
            mine = comp / "GameA" / "bin"
            mine.mkdir(parents=True)
            sibling = comp / "GameB" / "bin"
            sibling.mkdir(parents=True)
            solo = home / "Games" / "Solo"
            solo.mkdir(parents=True)
            got = self._resolve(home, "Compilation", mine, peers=[sibling, solo])
            self.assertEqual(got, (comp / "GameA").resolve())

    def test_a_short_name_cannot_match_a_bigger_folder(self):
        # A 3-char AppName must not prefix-match its way up the tree.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sub = home / "Games" / "ABCDEFGH" / "Binaries"
            sub.mkdir(parents=True)
            self.assertEqual(self._resolve(home, "ABC", sub), sub.resolve())

    def test_a_prefix_is_not_a_match(self):
        # Review 2026-08-04: the prefix form let "Metal Slug" widen onto a
        # MetalSlugCollection folder holding other (non-shortcut) games. Only exact
        # normalised equality is positive evidence; folder-carries-a-suffix stays at
        # the payload dir (recoverable under-capture, never over-capture).
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sub = home / "Games" / "MetalSlugCollection" / "MSX" / "Binaries"
            sub.mkdir(parents=True)
            (home / "Games" / "MetalSlugCollection" / "OtherGame").mkdir(parents=True)
            self.assertEqual(self._resolve(home, "Metal Slug", sub), sub.resolve())

    def test_short_normalised_equality_needs_three_chars(self):
        # Review 2026-08-04: normalisation strips non-ASCII, so a mostly-CJK AppName
        # can collapse to a 1-2 char token; an unfloored equality then matched an
        # unrelated short folder name. 3+ chars still admits real short titles (Ico).
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sub = home / "Games" / "2" / "Binaries"          # folder normalises to "2"
            sub.mkdir(parents=True)
            got = self._resolve(home, "バイオハザード2", sub)
            self.assertEqual(got, sub.resolve())              # no widening on "2" == "2"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / "Games" / "Ico"
            (root / "bin").mkdir(parents=True)
            self.assertEqual(self._resolve(home, "Ico", root / "bin"), root.resolve())

    def test_a_payload_that_is_itself_the_container_never_climbs(self):
        # StartDir IS the folder holding other shortcuts' games, and the AppName
        # matches a HIGHER ancestor: the answer must stay at the payload (the old
        # StartDir behaviour), not climb past the games it contains.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            payload = home / "Collection" / "Games"
            payload.mkdir(parents=True)
            a = payload / "GameA"
            b = payload / "GameB"
            a.mkdir()
            b.mkdir()
            got = self._resolve(home, "Collection", payload, peers=[a, b])
            self.assertEqual(got, payload.resolve())

    def test_a_same_game_shortcut_added_later_keeps_the_bound_stable(self):
        # Review 2026-08-04 (critic): the restore bound is recomputed from GLOBAL
        # shortcut state, so counting our OWN payload as container evidence made a
        # later-added second shortcut into the SAME game re-clamp the first one's
        # answer - and an older wide backup then refused to restore. Own payload and
        # a peer sitting exactly ON the ancestor are excluded from the evidence.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / "Games" / "Deadpool"
            (root / "Binaries").mkdir(parents=True)
            before = self._resolve(home, "Deadpool", root / "Binaries")
            after = self._resolve(home, "Deadpool", root / "Binaries", peers=[root])
            self.assertEqual(before, root.resolve())
            self.assertEqual(after, before)

    def test_a_new_peer_payload_on_disk_reclamps_without_a_vdf_change(self):
        # Review 2026-08-04: the peer set must track the FILESYSTEM, not only the
        # shortcut data - a payload dir that appears later (game installed after its
        # shortcut) must start bounding other games' widening at the next call.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            mine = home / "Games" / "Deadpool" / "Binaries"
            mine.mkdir(parents=True)
            other = home / "Games" / "Deadpool" / "OtherGame"   # peer's dir, absent yet
            got1 = self._resolve(home, "Deadpool", mine, peers=[other])
            self.assertEqual(got1, (home / "Games" / "Deadpool").resolve())
            other.mkdir()                                       # game installed; vdf unchanged
            got2 = self._resolve(home, "Deadpool", mine, peers=[other])
            self.assertEqual(got2, mine.resolve())

    def test_relative_payloads_are_refused_and_never_join_the_peer_set(self):
        # The LIVE shortcuts.vdf carries StartDir "./" (Nested Desktop) and
        # "/usr/bin" (flatpak). "./" would resolve against the calling process's cwd -
        # a cwd-dependent path must neither resolve nor bound other games.
        self.assertIsNone(ss._payload_dir({"name": "N", "exe": "", "start_dir": "./"}))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gd = home / "Games" / "TMNT"
            gd.mkdir(parents=True)
            self.assertIsNone(self._resolve(home, "Nested Desktop", Path("./")))
            self.assertIsNone(self._resolve(home, "Spotify", Path("/usr/bin")))

    def test_a_startdir_that_is_already_the_root_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / "Games" / "Deadpool"
            root.mkdir(parents=True)
            self.assertEqual(self._resolve(home, "Deadpool", root), root.resolve())

    def test_widening_never_reaches_home_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sub = home / "Deadpool" / "Binaries"
            sub.mkdir(parents=True)
            got = self._resolve(home, home.name, sub)     # AppName == $HOME's own name
            self.assertEqual(got, sub.resolve())
            self.assertNotEqual(got, home.resolve())


class SteamLibraries(unittest.TestCase):
    """library_roots()/compatdata_roots()/compatdata_dir(): a prefix is found in the
    library that actually holds it, not only the home one. Steam creates a prefix in
    whichever library is the default install target."""

    def setUp(self):
        ss._LIBRARY_CACHE["entry"] = (None, [])
        ss._PEERS_CACHE["entry"] = (None, ())

    tearDown = setUp

    def _home_with_library(self, home: Path, extra: Path):
        vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
        vdf.parent.mkdir(parents=True, exist_ok=True)
        vdf.write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n'
                       '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                       % (home / ".local/share/Steam", extra))

    def test_library_roots_lists_home_first_then_the_vdf_entries(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
            home, extra = Path(tmp), Path(sd)
            self._home_with_library(home, extra)
            with mock.patch.object(ss, "home", return_value=home):
                roots = [str(r) for r in ss.library_roots()]
            self.assertEqual(roots[0], str((home / ".local/share/Steam").resolve()))
            self.assertIn(str(extra.resolve()), roots)

    def test_compatdata_dir_finds_the_prefix_in_a_non_home_library(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
            home, extra = Path(tmp), Path(sd)
            self._home_with_library(home, extra)
            pfx = extra / "steamapps" / "compatdata" / str(APPID)
            pfx.mkdir(parents=True)
            with mock.patch.object(ss, "home", return_value=home):
                self.assertEqual(Path(os.path.realpath(str(ss.compatdata_dir(APPID)))),
                                 pfx.resolve())
                self.assertIn(str((extra / "steamapps" / "compatdata").resolve()),
                              [str(Path(os.path.realpath(str(r))))
                               for r in ss.compatdata_roots()])

    def test_compatdata_dir_falls_back_to_the_home_root(self):
        # Nothing created yet (a fresh-Deck restore): the answer is where Steam WOULD
        # create it, so the restore lands somewhere Steam will actually look.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
            home, extra = Path(tmp), Path(sd)
            self._home_with_library(home, extra)
            with mock.patch.object(ss, "home", return_value=home):
                got = ss.compatdata_dir(APPID)
                self.assertEqual(got, ss.compatdata_root() / str(APPID))

    def test_the_home_prefix_wins_when_two_libraries_hold_the_appid(self):
        # Home is first in library_roots() BY DESIGN (Steam's default install target):
        # with the same appid present in both, the home one is the answer.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
            home, extra = Path(tmp), Path(sd)
            self._home_with_library(home, extra)
            home_pfx = home / ".local/share/Steam/steamapps/compatdata" / str(APPID)
            home_pfx.mkdir(parents=True)
            (extra / "steamapps" / "compatdata" / str(APPID)).mkdir(parents=True)
            with mock.patch.object(ss, "home", return_value=home):
                self.assertEqual(ss.compatdata_dir(APPID), home_pfx)

    def test_home_roots_are_implicit_without_any_vdf(self):
        # A fresh/odd install with no libraryfolders.vdf at all: the two home root
        # spellings must still be there - nothing else guarantees the home library.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(ss, "home", return_value=home):
                roots = [str(r) for r in ss.library_roots()]
            self.assertIn(str((home / ".local/share/Steam").resolve()), roots)

    def test_a_vdf_rewrite_invalidates_the_cache(self):
        # The cache is keyed on the vdf's (path, mtime_ns): a rewritten vdf (Steam
        # adds a library) must change the answer at the next call, not at restart.
        with tempfile.TemporaryDirectory() as tmp, \
             tempfile.TemporaryDirectory() as sd1, tempfile.TemporaryDirectory() as sd2:
            home = Path(tmp)
            self._home_with_library(home, Path(sd1))
            with mock.patch.object(ss, "home", return_value=home):
                first = [str(r) for r in ss.library_roots()]
                self.assertNotIn(str(Path(sd2).resolve()), first)
                self._home_with_library(home, Path(sd2))
                vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
                st = vdf.stat()
                os.utime(vdf, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
                second = [str(r) for r in ss.library_roots()]
            self.assertIn(str(Path(sd2).resolve()), second)

    def test_concurrent_readers_never_see_a_torn_or_empty_list(self):
        # The (sig, value) pair lives in ONE dict slot as ONE tuple: madsrv handlers
        # run on a thread pool, and a two-statement publish let a reader pair the new
        # sig with the initial empty value (review 2026-08-04). Hammer the cache from
        # two threads while a third invalidates; a well-formed, never-empty list must
        # come back every time.
        import threading
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sd:
            home, extra = Path(tmp), Path(sd)
            self._home_with_library(home, extra)
            vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
            bad, stop = [], threading.Event()

            def reader():
                with mock.patch.object(ss, "home", return_value=home):
                    while not stop.is_set():
                        roots = ss.library_roots()
                        if not roots:
                            bad.append(len(roots))

            def invalidator():
                n = 0
                while not stop.is_set():
                    n += 1
                    st = vdf.stat()
                    os.utime(vdf, ns=(st.st_atime_ns, st.st_mtime_ns + n))

            threads = [threading.Thread(target=reader) for _ in range(2)]
            threads.append(threading.Thread(target=invalidator))
            for t in threads:
                t.start()
            import time
            time.sleep(0.2)
            stop.set()
            for t in threads:
                t.join()
            self.assertEqual(bad, [])

    def test_a_payload_inside_a_non_home_compatdata_is_refused(self):
        # The deny guard must cover every library, not just the home one, or a game
        # installed into an SD-library prefix would be offered twice.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            extra = home / "sdlib"
            self._home_with_library(home, extra)
            inside = extra / "steamapps" / "compatdata" / str(APPID) / "pfx"
            inside.mkdir(parents=True)
            sc = {APPID: {"name": "G", "exe": "", "start_dir": str(inside)}}
            with mock.patch.object(ss, "home", return_value=home), \
                 mock.patch.object(ss, "nonsteam_shortcuts", return_value=sc):
                self.assertIsNone(ss.game_dir(APPID))


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
