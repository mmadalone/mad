"""deck-backup.sh's ITEM SELECTION — what would actually end up in the archive.

Driven through `--list-items`, which prints the resolved paths and exits: no tar,
no du of huge trees. That matters here — an adversarial review once ran the real
script and filled /tmp with 4x926MB tars. Never invoke this script without
--list-items or --sizes from a test.

Why this file exists: c02c833 believed it had closed the OpenBOR backup hole and
had covered only half of it (Saves/, not the .openbor manifests), and nothing
could tell — there was no way to assert what the item list contains.

Run:  python3 -m unittest tests.test_backup_items -v
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deck-backup.sh"


class BackupItemSelection(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        # A miniature OpenBOR library: two launchable games (folder + manifest),
        # one folder deliberately NOT in ES-DE (no manifest — the real rig has two:
        # MIWv100.old and Maximun_Carnage_Returns).
        for game in ("GameA", "GameB", "NotInEsde"):
            (self.home / "OpenBor" / game / "Saves").mkdir(parents=True)
            (self.home / "OpenBor" / game / "Saves" / f"{game}.cfg").write_bytes(b"\0" * 8)
        for game in ("GameA", "GameB"):
            (self.home / "OpenBor" / f"{game}.openbor").write_text(
                f"DIR={game}\nEXE={game}.exe\nPREFIX={self.home}/prefix\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def _items(self, *extra: str) -> list[str]:
        # tests/__init__.py pins $MAD_DATA_ROOT to a FIXED throwaway tree (structural isolation for
        # the whole suite, so no test can leak into the real ~/Emulation) — but this file swaps
        # $HOME per-test, and deck-backup.sh's saves/storage/bios roots are $MAD_DATA_ROOT-relative.
        # Left alone, a fixture under self.home/Emulation/* would silently resolve against the
        # WRONG (shared, unrelated) tree. Pin MAD_DATA_ROOT here too, to $HOME/Emulation — exactly
        # what the script itself defaults to when MAD_DATA_ROOT is unset — so per-test isolation
        # actually holds for those roots as well.
        env = dict(os.environ, HOME=str(self.home), BACKUP_DEST=str(self.home / "dest"),
                   MAD_DATA_ROOT=str(self.home / "Emulation"))
        r = subprocess.run([str(SCRIPT), "--list-items", *extra], capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, f"--list-items failed: {r.stderr[-500:]}")
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def test_the_openbor_manifests_are_archived(self):
        # THE GAP (fixed 2026-07-17). ES-DE launches a game by reading DIR/EXE/PREFIX
        # out of ~/OpenBor/<Game>.openbor, and NOTHING backed them up: --roms tars
        # $ROM_ROOT, but ~/ROMs/openbor is a symlink to ~/OpenBor that tar does not
        # follow, so that archive holds one symlink entry and zero bytes.
        items = self._items()
        for game in ("GameA", "GameB"):
            self.assertIn(str(self.home / "OpenBor" / f"{game}.openbor"), items,
                          f"{game}'s launch manifest is in no archive")

    def test_the_openbor_saves_are_archived(self):
        # The other half (c02c833). Controls + high scores + progress live here.
        items = self._items()
        for game in ("GameA", "GameB"):
            self.assertIn(str(self.home / "OpenBor" / game / "Saves"), items)

    def test_the_games_themselves_are_not_archived(self):
        # Deliberate: games are re-downloadable, and adding ~/OpenBor wholesale
        # would drag GBs into the always-on core archive.
        items = self._items()
        self.assertNotIn(str(self.home / "OpenBor"), items,
                         "the whole OpenBOR tree (the games) went into the core archive")

    def test_a_rig_with_no_openbor_at_all_is_fine(self):
        # An unmatched glob must contribute nothing, not a literal "*.openbor" path.
        import shutil
        shutil.rmtree(self.home / "OpenBor")
        items = self._items()
        self.assertFalse([i for i in items if "OpenBor" in i or "*" in i],
                         "an empty glob leaked a literal path into the item list")

    def test_saves_hub_is_archived_when_present(self):
        # Locks the cloud push-precious contract (2026-08-12 batch, item 1): --list-items is
        # cloud's single source of truth for what to sync, and $SAVES_DIR (the EmuDeck saves
        # hub) must keep appearing there with --saves on, even though the LOCAL archive now
        # excludes it from the config tar/mirror in favor of its own deck-saves-<ts> archive
        # (see deck-backup.sh section 1 / ARCHIVE_ITEMS).
        (self.home / "Emulation" / "saves").mkdir(parents=True)
        items = self._items("--saves")
        self.assertIn(str(self.home / "Emulation" / "saves"), items)

    def test_eden_citron_precious_slices_are_archived(self):
        # Eden + Citron (Switch) precious slices: config, keys, user saves, mods. Before
        # 2026-08-12 both emulators' saves + prod/title keys existed in NO backup anywhere.
        # Decoys prove the slicing is exact, not "back up the whole emulator dir": shader/dump
        # are re-acquirable caches and nand/user/Contents is the (~24G) installed-title dump -
        # none of the three may ride along.
        for emu in ("eden", "citron"):
            (self.home / ".config" / emu).mkdir(parents=True)
            (self.home / ".local" / "share" / emu / "keys").mkdir(parents=True)
            (self.home / ".local" / "share" / emu / "nand" / "user" / "save").mkdir(parents=True)
            (self.home / ".local" / "share" / emu / "load").mkdir(parents=True)
        # decoys — eden only (mirrors the plan's fixture), must NOT be archived
        (self.home / ".local" / "share" / "eden" / "shader").mkdir(parents=True)
        (self.home / ".local" / "share" / "eden" / "dump").mkdir(parents=True)
        (self.home / ".local" / "share" / "eden" / "nand" / "user" / "Contents").mkdir(parents=True)

        items = self._items("--emu")
        for emu in ("eden", "citron"):
            base = self.home / ".local" / "share" / emu
            self.assertIn(str(self.home / ".config" / emu), items)
            self.assertIn(str(base / "keys"), items)
            self.assertIn(str(base / "nand" / "user" / "save"), items)
            self.assertIn(str(base / "load"), items)
        eden = self.home / ".local" / "share" / "eden"
        self.assertNotIn(str(eden), items, "the whole eden data dir must not ride wholesale")
        self.assertNotIn(str(eden / "shader"), items)
        self.assertNotIn(str(eden / "nand" / "user" / "Contents"), items)


if __name__ == "__main__":
    unittest.main()
