"""bezel_cfg widescreen skip — install() must not force a 4:3 bezel onto a game the user
configured for Flycast widescreen (reicast_widescreen_hack/cheats enabled in its .opt).

Run:  python3 -m unittest tests.test_bezel_widescreen -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import bezel_cfg


class WidescreenSkip(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._cb = bezel_cfg.CONFIG_BASE
        bezel_cfg.CONFIG_BASE = self.dir

    def tearDown(self):
        bezel_cfg.CONFIG_BASE = self._cb
        shutil.rmtree(self.dir, ignore_errors=True)

    def _opt(self, core, game, body):
        d = self.dir / core
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{game}.opt").write_text(body, encoding="utf-8")

    def test_hack_or_cheats_enabled_is_widescreen(self):
        self._opt("Flycast", "wshack", 'reicast_widescreen_hack = "enabled"\n')
        self._opt("Flycast", "wscheat", 'some_other = "x"\nreicast_widescreen_cheats = "enabled"\n')
        self._opt("Flycast", "narrow", 'reicast_widescreen_hack = "disabled"\n'
                                       'reicast_widescreen_cheats = "disabled"\n')
        self.assertTrue(bezel_cfg._has_widescreen_on("wshack", ["Flycast"]))
        self.assertTrue(bezel_cfg._has_widescreen_on("wscheat", ["Flycast"]))
        self.assertFalse(bezel_cfg._has_widescreen_on("narrow", ["Flycast"]))
        # no .opt at all -> the user never enabled widescreen -> 4:3 default -> not skipped
        self.assertFalse(bezel_cfg._has_widescreen_on("noopt", ["Flycast"]))

    def test_non_flycast_core_never_matches(self):
        # a non-Flycast .opt has no reicast_ keys, so the guard is a no-op for the 17 systems
        self._opt("Snes9x", "mario", 'snes9x_overclock = "enabled"\n')
        self.assertFalse(bezel_cfg._has_widescreen_on("mario", ["Snes9x"]))


if __name__ == "__main__":
    unittest.main()
