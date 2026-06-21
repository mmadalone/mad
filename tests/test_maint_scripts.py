"""
Behavior tests for the parameterized maintenance CLIs.

The scripts are top-level files with hyphenated names, so they're loaded via
spec_from_file_location (importing does NOT run main() — those are __main__-guarded).
Each test patches the module's resolved path constants to a temp tree, so nothing
touches the real ~/ROMs / ES-DE media. clean-manual-cruft runs top-to-bottom on
import, so it's exercised end-to-end as a subprocess driven purely by env vars —
which also proves the parameterization (no hardcoded /1tbDeck) and the rule-#5
recoverable move.

Run: python3 -m unittest tests.test_maint_scripts -v
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    """Load a hyphenated top-level script as a module (main() stays unrun)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _games_block(path_rel: str) -> str:
    return f"\t<game>\n\t\t<path>./{path_rel}</path>\n\t\t<name>Game</name>\n\t</game>\n"


def _hidden_for(xml: str) -> dict:
    """Map each game's ./path -> True/False (is it <hidden>?)."""
    out = {}
    for m in re.finditer(r"\t<game>(.*?)</game>", xml, re.S):
        blk = m.group(1)
        pm = re.search(r"<path>\./([^<]+)</path>", blk)
        if pm:
            out[pm.group(1)] = bool(re.search(r"<hidden>\s*true", blk))
    return out


class Dedup(unittest.TestCase):
    def setUp(self):
        self.mod = _load("dedup_disc_gamelists", "dedup-disc-gamelists.py")
        self.tmp = Path(tempfile.mkdtemp())
        self.roms = self.tmp / "roms"
        self.gl = self.tmp / "gamelists"
        self.mod.ROMROOT = str(self.roms)
        self.mod.GLROOT = str(self.gl)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup(self, sysname, m3u_refs, gl_paths):
        rd = self.roms / sysname
        rd.mkdir(parents=True)
        for m3u, refs in m3u_refs.items():
            (rd / m3u).write_text("\n".join(refs) + "\n")
            for r in refs:
                (rd / r).write_text("disc")
        gd = self.gl / sysname
        gd.mkdir(parents=True)
        body = "".join(_games_block(p) for p in gl_paths)
        (gd / "gamelist.xml").write_text(
            f'<?xml version="1.0"?>\n<gameList>\n{body}</gameList>\n')

    def test_single_disc_hides_m3u_shows_disc(self):
        self._setup("psx", {"Game.m3u": ["Game.chd"]}, ["Game.m3u", "Game.chd"])
        self.mod.dedup("psx")
        h = _hidden_for((self.gl / "psx" / "gamelist.xml").read_text())
        self.assertTrue(h["Game.m3u"], "single-disc m3u should be hidden")
        self.assertFalse(h["Game.chd"], "the single disc file should stay visible")

    def test_multi_disc_keeps_m3u_hides_discs(self):
        self._setup("psx", {"Game.m3u": ["Game (Disc 1).chd", "Game (Disc 2).chd"]},
                    ["Game.m3u", "Game (Disc 1).chd", "Game (Disc 2).chd"])
        self.mod.dedup("psx")
        h = _hidden_for((self.gl / "psx" / "gamelist.xml").read_text())
        self.assertFalse(h["Game.m3u"], "multi-disc m3u should stay visible")
        self.assertTrue(h["Game (Disc 1).chd"], "multi-disc parts should be hidden")
        self.assertTrue(h["Game (Disc 2).chd"], "multi-disc parts should be hidden")


class FixMediaNames(unittest.TestCase):
    def setUp(self):
        self.mod = _load("fix_media_names", "fix-media-names-for-dir-as-file.py")
        self.tmp = Path(tempfile.mkdtemp())
        self.mod.ROMS_ROOT = str(self.tmp / "roms")
        self.mod.MEDIA_ROOT = str(self.tmp / "media")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_appends_dir_as_file_extension(self):
        # ROM is a "Game.cue/" directory-as-file; media is the plain stem "Game.png".
        (Path(self.mod.ROMS_ROOT) / "psx" / "Game.cue").mkdir(parents=True)
        covers = Path(self.mod.MEDIA_ROOT) / "psx" / "covers"
        covers.mkdir(parents=True)
        (covers / "Game.png").write_text("art")
        n = self.mod.fix_system("psx", apply=True)
        self.assertEqual(n, 1)
        self.assertTrue((covers / "Game.cue.png").is_file())
        self.assertFalse((covers / "Game.png").exists())


class CleanManualCruftE2E(unittest.TestCase):
    """End-to-end: env-driven media root + rule-#5 recoverable move (no /1tbDeck)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_recovers_keeps_live_and_moves_orphan(self):
        media = self.tmp / "media"
        mandir = media / "psx" / "manuals"
        mandir.mkdir(parents=True)
        (mandir / "Orphan.pdf").write_text("pdf")       # no matching game -> _TMP
        (mandir / "Live.pdf").write_text("pdf")         # matches ./Live.zip -> untouched
        (mandir / "Game.cue.pdf").write_text("pdf")     # wrong-named, game valid -> rename in place
        # gamelist makes "Game" and "Live" valid stems; ROM tree is empty.
        esde = self.tmp / "esde"
        gld = esde / "gamelists" / "psx"
        gld.mkdir(parents=True)
        (gld / "gamelist.xml").write_text(
            '<?xml version="1.0"?>\n<gameList>\n'
            '\t<game>\n\t\t<path>./Game.cue</path>\n\t\t<name>Game</name>\n\t</game>\n'
            '\t<game>\n\t\t<path>./Live.zip</path>\n\t\t<name>Live</name>\n\t</game>\n'
            '</gameList>\n')
        (esde / "settings").mkdir(parents=True)
        (esde / "settings" / "es_settings.xml").write_text(
            f'<?xml version="1.0"?>\n<string name="ROMDirectory" value="{self.tmp / "roms"}" />\n')
        (self.tmp / "roms").mkdir()

        env = dict(os.environ,
                   MAD_MEDIA_ROOT=str(media),
                   ESDE_APPDATA_DIR=str(esde),
                   MAD_INSTALL_CONF=str(self.tmp / "none.conf"))
        r = subprocess.run([sys.executable, str(ROOT / "clean-manual-cruft.py"), "--apply"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # live manual left alone
        self.assertTrue((mandir / "Live.pdf").is_file(), "live manual must be untouched")
        # recover: wrong-named renamed to Game.pdf IN PLACE (not moved to _TMP)
        self.assertTrue((mandir / "Game.pdf").is_file(), "recoverable manual should be renamed in place")
        self.assertFalse((mandir / "Game.cue.pdf").exists())
        # orphan moved to a recoverable _TMP beside the media root (DM.parent == self.tmp)
        self.assertFalse((mandir / "Orphan.pdf").exists(), "orphan should be moved")
        tmpdirs = list(self.tmp.glob("_TMP_manuals-cruft-psx-*"))
        self.assertEqual(len(tmpdirs), 1, f"expected one recoverable _TMP; got {tmpdirs}")
        self.assertTrue((tmpdirs[0] / "RECOVERY.txt").is_file())
        self.assertTrue((tmpdirs[0] / "Orphan.pdf").is_file())
        self.assertFalse((tmpdirs[0] / "Game.pdf").exists(), "recovered file must not be in _TMP")


if __name__ == "__main__":
    unittest.main()
