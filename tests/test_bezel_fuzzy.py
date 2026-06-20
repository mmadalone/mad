"""Phase-3 fuzzy bezel matching: norm/rank/tie-break (pure) + the path-based ops
(_owned_unmatched, auto_match normalized-equal additive wiring, prune_unowned,
fuzzy_unmatched/fuzzy_candidates). Uses a prod-like tmp layout with the real 'snes' key.

Run:  python3 -m unittest tests.test_bezel_fuzzy -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import bezel_cfg, bezel_match


class Norm(unittest.TestCase):
    def test_strips_region_tags_punctuation_articles(self):
        self.assertEqual(bezel_match.norm("Cannon Fodder (1993)(Virgin)[!][Amiga-CD32]"),
                         "cannon fodder")
        self.assertEqual(bezel_match.norm("Alien Breed - Tower Assault (Europe) (OCS, ECS) (Disk 1)"),
                         "alien breed tower assault")
        self.assertEqual(bezel_match.norm("The Chaos Engine"), "chaos engine")  # article dropped
        self.assertEqual(bezel_match.norm("ATR - All Terrain Racing (1995)(Team 17)[!]"),
                         "atr all terrain racing")
        self.assertEqual(bezel_match.norm("Sonic The Hedgehog v2 (1991)"), "sonic hedgehog")  # vN tag
        # `_` is a regex word char, so an ATTACHED `_v1.6` is NOT a standalone vN token —
        # it survives as "v1 6" (byte-identical to wire-bezels.norm; such names fall to fuzzy).
        self.assertEqual(bezel_match.norm("AllTerrainRacing_v1.6"), "allterrainracing v1 6")

    def test_keeps_sequel_numbers(self):
        self.assertNotEqual(bezel_match.norm("Chuck Rock"), bezel_match.norm("Chuck Rock 2"))


class Rank(unittest.TestCase):
    def test_exact_norm_scores_one_orders_desc_drops_junk(self):
        bez = ["Alien Breed 3D (Europe) (AGA) (Disk 1)", "Alien Breed (Europe)", "Zzz Unrelated"]
        res = bezel_match.rank_candidates("Alien Breed 3D (1995)(Ocean)[!]", bezel_match.normed(bez), n=3)
        self.assertEqual(res[0][0], "Alien Breed 3D (Europe) (AGA) (Disk 1)")
        self.assertAlmostEqual(res[0][1], 1.0, places=3)
        self.assertTrue(all(res[i][1] >= res[i + 1][1] for i in range(len(res) - 1)))  # descending
        self.assertNotIn("Zzz Unrelated", [r[0] for r in res])  # below cutoff

    def test_empty_target_returns_nothing(self):
        self.assertEqual(bezel_match.rank_candidates("(unl)", bezel_match.normed(["X"])), [])


class NormMapTiebreak(unittest.TestCase):
    def test_norm_map_groups_by_normalized_name(self):
        m = bezel_match.norm_map(["Chuck Rock (Disk 1)", "Chuck Rock (Disk 2)", "Gloom (AGA)"])
        self.assertEqual(sorted(m["chuck rock"]), ["Chuck Rock (Disk 1)", "Chuck Rock (Disk 2)"])
        self.assertEqual(m["gloom"], ["Gloom (AGA)"])

    def test_tiebreak_is_rom_aware(self):
        self.assertEqual(bezel_cfg._norm_tiebreak(["Solo"], "rom"), "Solo")
        # CD32 ROM takes the CD32 bezel; a plain/floppy ROM takes the non-CD32 bezel:
        self.assertEqual(bezel_cfg._norm_tiebreak(["Akira (CD32)", "Akira"], "Akira (CD32)"), "Akira (CD32)")
        self.assertEqual(bezel_cfg._norm_tiebreak(["Akira (CD32)", "Akira"], "Akira (1994)"), "Akira")
        # N1 regression: a non-CD32 ROM must NOT grab the CD32 bezel
        self.assertEqual(bezel_cfg._norm_tiebreak(["Foo", "Foo (CD32)"], "Foo"), "Foo")
        # genuinely ambiguous (no unique disambiguator) -> interactive review
        self.assertIsNone(bezel_cfg._norm_tiebreak(["A Floppy", "B Floppy"], "rom"))
        self.assertIsNone(bezel_cfg._norm_tiebreak([], "rom"))


class _Fixture(unittest.TestCase):
    """Prod-like tmp layout using the real 'snes' key (rom dirs snes/sfc, subdir SNES,
    first core Snes9x). OVERLAY_BASE keeps the '.../GameBezels' tail so the sentinel cfgs
    carry the '/GameBezels/SNES/' marker _game_cfgs requires."""
    KEY, SUBDIR, CORE, ROMDIR = "snes", "SNES", "Snes9x", "snes"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._save = (bezel_cfg.ROMS, bezel_cfg.OVERLAY_BASE, bezel_cfg.CONFIG_BASE, bezel_cfg._HOME)
        bezel_cfg.ROMS = self.root / "ROMs"
        bezel_cfg.OVERLAY_BASE = self.root / "overlays" / "GameBezels"
        bezel_cfg.CONFIG_BASE = self.root / "config"
        bezel_cfg._HOME = self.root            # so _tmp_dir() writes under the tmp root, not ~/Downloads
        bezel_cfg._NORMED_CACHE.clear()
        (bezel_cfg.ROMS / self.ROMDIR).mkdir(parents=True)
        (bezel_cfg.OVERLAY_BASE / self.SUBDIR).mkdir(parents=True)
        (bezel_cfg.CONFIG_BASE / self.CORE).mkdir(parents=True)

    def tearDown(self):
        (bezel_cfg.ROMS, bezel_cfg.OVERLAY_BASE, bezel_cfg.CONFIG_BASE, bezel_cfg._HOME) = self._save
        bezel_cfg._NORMED_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def rom(self, stem, ext="zip"):
        (bezel_cfg.ROMS / self.ROMDIR / f"{stem}.{ext}").write_text("")

    def bezel(self, stem):
        (bezel_cfg.OVERLAY_BASE / self.SUBDIR / f"{stem}.cfg").write_text("overlay\n")
        (bezel_cfg.OVERLAY_BASE / self.SUBDIR / f"{stem}.png").write_text("img")

    def wired(self, stem):
        overlay = bezel_cfg.OVERLAY_BASE / self.SUBDIR / f"{stem}.cfg"
        (bezel_cfg.CONFIG_BASE / self.CORE / f"{stem}.cfg").write_text(
            bezel_cfg._PER_GAME_CFG.format(overlay=overlay, enabled="true"))


class Owned(_Fixture):
    def test_owned_and_unmatched(self):
        self.rom("Super Mario World")
        self.rom("Zelda")
        self.wired("Super Mario World")
        self.assertEqual(bezel_cfg._owned_rom_stems(self.KEY), {"Super Mario World", "Zelda"})
        self.assertEqual(bezel_cfg._owned_unmatched(self.KEY), {"Zelda"})


class AutoMatch(_Fixture):
    def test_wires_unique_norm_skips_ambiguous(self):
        self.rom("Super Mario World (USA)")
        self.rom("Star Fox (Europe) (Rev 1)")
        self.rom("Ambiguous Game (1994)")
        self.bezel("Super Mario World")
        self.bezel("Star Fox")
        self.bezel("Ambiguous Game (Disk 1)")
        self.bezel("Ambiguous Game (Disk 2)")     # two bezels share the norm -> ambiguous
        res = bezel_cfg.auto_match(self.KEY)
        self.assertEqual(res["norm_games"], 2)
        smw = bezel_cfg.CONFIG_BASE / self.CORE / "Super Mario World (USA).cfg"
        self.assertTrue(smw.exists())
        self.assertIn("Super Mario World.cfg", smw.read_text())   # points at the matched bezel
        self.assertFalse((bezel_cfg.CONFIG_BASE / self.CORE / "Ambiguous Game (1994).cfg").exists())

    def test_wires_exact_named_in_standalone(self):
        # an exact-named unwired game (no exact pass ran first) is still auto-wired by a
        # standalone auto_match — the N3 widescreen-count guard must not suppress this.
        self.rom("Exact Name")
        self.bezel("Exact Name")
        res = bezel_cfg.auto_match(self.KEY)
        self.assertEqual(res["norm_games"], 1)
        self.assertTrue((bezel_cfg.CONFIG_BASE / self.CORE / "Exact Name.cfg").exists())

    def test_additive_never_rewrites_existing(self):
        self.rom("Already Wired (USA)")
        self.bezel("Already Wired")
        self.wired("Already Wired (USA)")
        before = (bezel_cfg.CONFIG_BASE / self.CORE / "Already Wired (USA).cfg").read_text()
        res = bezel_cfg.auto_match(self.KEY)
        self.assertEqual(res["norm_games"], 0)
        self.assertEqual((bezel_cfg.CONFIG_BASE / self.CORE / "Already Wired (USA).cfg").read_text(),
                         before)


class Prune(_Fixture):
    def test_moves_unowned_keeps_owned_recoverable(self):
        self.rom("Owned Game")
        self.bezel("Owned Game")
        self.bezel("Unowned Game")
        self.wired("Owned Game")
        self.wired("Unowned Game")        # no ROM -> pruned
        res = bezel_cfg.prune_unowned(self.KEY)
        self.assertEqual((res["games"], res["moved"]), (1, 1))
        self.assertTrue((bezel_cfg.CONFIG_BASE / self.CORE / "Owned Game.cfg").exists())
        self.assertFalse((bezel_cfg.CONFIG_BASE / self.CORE / "Unowned Game.cfg").exists())
        self.assertTrue(res["tmp"] and Path(res["tmp"]).is_dir())   # recoverable _TMP


class Fuzzy(_Fixture):
    def test_list_excludes_wired_and_candidates_rank(self):
        self.rom("Alien Breed 3D (1995)(Ocean)[!]")
        self.rom("Wired One (USA)")
        self.bezel("Alien Breed 3D (Europe) (AGA)")
        self.bezel("Totally Different Game")
        self.wired("Wired One (USA)")     # already wired -> not in the work list
        lst = bezel_cfg.fuzzy_unmatched(self.KEY)
        self.assertEqual([r["game"] for r in lst], ["Alien Breed 3D (1995)(Ocean)[!]"])
        cands = bezel_cfg.fuzzy_candidates(self.KEY, "Alien Breed 3D (1995)(Ocean)[!]")
        self.assertEqual(cands[0]["name"], "Alien Breed 3D (Europe) (AGA)")
        self.assertAlmostEqual(cands[0]["score"], 1.0, places=3)
        self.assertTrue(cands[0]["preview"].endswith("Alien Breed 3D (Europe) (AGA).png"))


if __name__ == "__main__":
    unittest.main()
