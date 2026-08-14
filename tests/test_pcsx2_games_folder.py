"""pcsx2_games: the ROM FOLDER decides which PS2 games exist, PCSX2 only supplies identity.

PCSX2's gamelist.cache is only rewritten when its desktop window scans, so a disc copied in
since that scan did not exist as far as any per-game page was concerned. games() now takes its
row set from the folder and asks the cache only for each disc's <SERIAL>_<CRC>, deriving that
itself when the cache has no matching record.

Covers: a folder-only disc appears; a FRESH cache entry wins and nothing is derived; a STALE
entry (size or mtime moved) is ignored and derived instead; a cache ghost whose file is gone
never produces a row; the bare-CRC key shape survives end to end; games() and path_to_key()
agree for the same file (they must, or settings are written under a key the launch path never
looks up); and an unreadable folder degrades to the cache-only list instead of blanking.

Run:  python3 -m unittest tests.test_pcsx2_games_folder -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import pcsx2_games


class FolderTruth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rom = self.tmp / "ps2"
        self.rom.mkdir()
        self.derived: list[str] = []            # every path we were asked to derive
        self._saved = (pcsx2_games.rom_folder.entries, pcsx2_games.parse_cache,
                       pcsx2_games.ps2_gameids.ident, pcsx2_games.es_gamelist.titles)
        pcsx2_games.rom_folder.entries = self._entries
        pcsx2_games.es_gamelist.titles = lambda system: {}
        pcsx2_games.ps2_gameids.ident = self._ident
        self._cache_rows: list[dict] = []
        pcsx2_games.parse_cache = lambda path: list(self._cache_rows)

    def tearDown(self):
        (pcsx2_games.rom_folder.entries, pcsx2_games.parse_cache,
         pcsx2_games.ps2_gameids.ident, pcsx2_games.es_gamelist.titles) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fakes -------------------------------------------------------------
    def _entries(self, system):
        out = {}
        for p in sorted(self.rom.iterdir()):
            out[p.stem.lower()] = {"stem": p.stem, "path": str(p), "kind": "file"}
        return out

    def _ident(self, path):
        """Stand-in for the disc reader: the key is the file's own text, so a test can say
        exactly what a disc 'contains' without building a real ISO."""
        self.derived.append(os.path.realpath(str(path)))
        try:
            body = Path(path).read_text().strip()
        except OSError:
            return None
        return {"id": body or None, "why": "", "final": True}

    def _disc(self, name: str, key: str) -> Path:
        p = self.rom / name
        p.write_text(key)
        return p

    def _cached(self, path: Path, key: str, *, fresh: bool = True) -> None:
        st = path.stat()
        self._cache_rows.append({
            "serial": key.rpartition("_")[0], "crc": 0, "title": "", "title_en": f"Cached {key}",
            "region": 0, "path": str(path), "key": key,
            "size": st.st_size if fresh else st.st_size + 1,
            "mtime": int(st.st_mtime) if fresh else int(st.st_mtime) - 999,
        })

    # -- the folder decides which games exist ------------------------------
    def test_disc_absent_from_the_cache_still_appears(self):
        """The whole point: a disc copied in since PCSX2 last scanned is listed anyway."""
        self._disc("New Game.iso", "SLES-11111_AAAAAAAA")
        games = pcsx2_games.games()
        self.assertEqual([g["key"] for g in games], ["SLES-11111_AAAAAAAA"])
        self.assertEqual(games[0]["name"], "New Game")      # no cache title, no gamelist name

    def test_fresh_cache_entry_wins_and_nothing_is_derived(self):
        """PCSX2's own answer is authoritative while its record still matches the file, and it
        costs nothing. Deriving anyway would be slower for no gain."""
        p = self._disc("Known.iso", "IGNORED-BY-THE-TEST")
        self._cached(p, "SLES-22222_BBBBBBBB")
        games = pcsx2_games.games()
        self.assertEqual([g["key"] for g in games], ["SLES-22222_BBBBBBBB"])
        self.assertEqual(games[0]["name"], "Cached SLES-22222_BBBBBBBB")
        self.assertEqual(self.derived, [])                  # never touched the disc

    def test_stale_cache_entry_is_ignored_and_derived_instead(self):
        """A record from before the file changed describes a different disc. Trusting it would
        write settings under the previous disc's key."""
        p = self._disc("Replaced.iso", "SLES-33333_CCCCCCCC")
        self._cached(p, "SLES-99999_DEADBEEF", fresh=False)
        games = pcsx2_games.games()
        self.assertEqual([g["key"] for g in games], ["SLES-33333_CCCCCCCC"])
        self.assertEqual(self.derived, [os.path.realpath(str(p))])

    def test_cache_ghost_with_no_file_produces_no_row(self):
        """A game removed from the library must not linger just because the cache remembers it.

        NOTE the real disc alongside it. An EMPTY folder is indistinguishable from an unreadable
        one (both give no entries), so it deliberately takes the card-out fallback and would show
        the ghost. The folder-truth path is what is under test here, so the folder is not empty."""
        self._disc("Still Here.iso", "SLES-77777_00000001")
        gone = self.rom / "Deleted.iso"
        self._cache_rows.append({"serial": "SLES-44444", "crc": 0, "title": "", "title_en": "Ghost",
                                 "region": 0, "path": str(gone), "key": "SLES-44444_EEEEEEEE",
                                 "size": 1, "mtime": 1})
        self.assertEqual([g["key"] for g in pcsx2_games.games()], ["SLES-77777_00000001"])

    def test_unidentifiable_disc_is_dropped_not_crashed(self):
        """Until the panel can draw a greyed row, an unreadable disc stays invisible, exactly as
        it is today. It must never become a row that looks ordinary and then fails when opened."""
        self._disc("Readable.iso", "SLES-55555_11111111")
        (self.rom / "Broken.iso").write_text("")            # _ident returns id=None
        self.assertEqual([g["key"] for g in pcsx2_games.games()], ["SLES-55555_11111111"])

    def test_bare_crc_key_survives_end_to_end(self):
        """A disc whose boot label fails PCSX2's own serial test is keyed by checksum alone.
        games() must not assume a serial is always present."""
        self._disc("Homebrew.iso", "83C9749E")
        games = pcsx2_games.games()
        self.assertEqual(games[0]["key"], "83C9749E")
        self.assertEqual(games[0]["serial"], "")
        self.assertEqual(games[0]["crc"], 0x83C9749E)

    def test_two_copies_of_one_disc_yield_one_row(self):
        self._disc("Game.iso", "SLES-66666_22222222")
        self._disc("Game (copy).iso", "SLES-66666_22222222")
        self.assertEqual(len(pcsx2_games.games()), 1)

    # -- the picker and the launch path must agree -------------------------
    def test_games_and_path_to_key_agree(self):
        """If these two disagree the setting is written under one key and read under another,
        which does nothing at all and reports no error."""
        self._disc("Derived.iso", "SLES-77777_33333333")
        p = self._disc("Cached.iso", "IGNORED")
        self._cached(p, "SLES-88888_44444444")
        for g in pcsx2_games.games():
            self.assertEqual(pcsx2_games.path_to_key(g["path"]), g["key"], g["path"])

    # -- degradation -------------------------------------------------------
    def test_unreadable_folder_degrades_to_the_cache_list(self):
        """SD card out, or ES-DE publishes no extensions for ps2: fall back to what PCSX2 knows
        rather than showing an empty page."""
        p = self._disc("OnCard.iso", "IGNORED")
        self._cached(p, "SLES-12345_55555555")
        pcsx2_games.rom_folder.entries = lambda system: {}
        self.assertEqual([g["key"] for g in pcsx2_games.games()], ["SLES-12345_55555555"])

    def test_folder_read_that_raises_does_not_break_the_picker(self):
        p = self._disc("OnCard.iso", "IGNORED")
        self._cached(p, "SLES-12345_55555555")

        def _boom(system):
            raise RuntimeError("card yanked mid-scan")

        pcsx2_games.rom_folder.entries = _boom
        self.assertEqual([g["key"] for g in pcsx2_games.games()], ["SLES-12345_55555555"])

    # -- the audit fence ---------------------------------------------------
    def test_identity_audit_reports_a_disagreement(self):
        p = self._disc("Conflict.iso", "SLES-00001_AAAAAAAA")
        self._cached(p, "SLES-00002_BBBBBBBB")              # fresh, but we derive something else
        rows = pcsx2_games.identity_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pcsx2"], "SLES-00002_BBBBBBBB")
        self.assertEqual(rows[0]["derived"], "SLES-00001_AAAAAAAA")

    def test_identity_audit_is_empty_when_they_agree(self):
        p = self._disc("Agree.iso", "SLES-00003_CCCCCCCC")
        self._cached(p, "SLES-00003_CCCCCCCC")
        self.assertEqual(pcsx2_games.identity_audit(), [])


if __name__ == "__main__":
    unittest.main()
