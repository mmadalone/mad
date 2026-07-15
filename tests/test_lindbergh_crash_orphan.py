"""lindbergh.save must never bake a CRASH ORPHAN's transient [EVDEV] into the rule-#5 one-time .bak
or the saved docked ini. If ES-DE dies mid-game before the game-end [EVDEV] revert, the live ini
keeps the transient launch device bindings alongside a stale <ini>.mad-restore. _load_buffer now
sweeps that orphan first (reverts [EVDEV] to its canonical pre-launch form + drops the sidecar), so
both the edit buffer and the first Settings .bak stay pristine.

Regression for the 2026-07-15 review finding #13 (the exact hazard lindbergh_pads.apply_handheld_settings
already documents for the handheld path). Run: python3 -m unittest tests.test_lindbergh_crash_orphan -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import lindbergh_pads as P
from lib.madsrv import lindbergh_cmds as L

_CANON = ('[Display]\nWIDTH = 1280\n\n[EVDEV]\n'
          'PLAYER_1_BUTTON_1 = "OLD1"\nPLAYER_2_BUTTON_1 = "OLD2"\nTEST_BUTTON = "KEEP"\n')
_TRANSIENT = _CANON.replace('"OLD1"', '"TRANSIENT_DEV"')   # what a launch materialized into [EVDEV]


class LindberghCrashOrphan(unittest.TestCase):
    def _game(self, ini_text: str) -> Path:
        gd = Path(tempfile.mkdtemp()) / "id5.lindbergh"
        gd.mkdir(parents=True)
        (gd / "id5.lindbergh.commands").write_text("game.elf\n")
        (gd / "game.elf").write_text("x")
        P.ini_of(gd).write_text(ini_text)
        self._orig_gamedir = L._gamedir
        L._gamedir = lambda _t, _gd=gd: _gd     # titleid -> our temp gamedir
        return gd

    def tearDown(self):
        if hasattr(self, "_orig_gamedir"):
            L._gamedir = self._orig_gamedir

    def test_orphan_swept_on_load_then_bak_and_save_stay_pristine(self):
        gd = self._game(_TRANSIENT)                         # live ini = transient (post-materialize)
        ini = P.ini_of(gd)
        sidecar = ini.with_name(ini.name + P.RESTORE_SUFFIX)
        sidecar.write_text(_CANON)                          # stale .mad-restore = pre-launch canonical

        L._load_buffer("id5")

        # buffer + on-disk ini reverted to canonical; the orphan sidecar is gone
        self.assertIn('"OLD1"', L._buf["text"])
        self.assertNotIn('"TRANSIENT_DEV"', L._buf["text"])
        self.assertFalse(sidecar.exists())
        self.assertIn('"OLD1"', ini.read_text())
        self.assertNotIn('"TRANSIENT_DEV"', ini.read_text())

        # a Settings edit + Save: the one-time .bak AND the saved ini must be free of transient bindings
        L._buf["text"] = L._buf["text"].replace("WIDTH = 1280", "WIDTH = 640")
        L._buf["dirty"] = True
        L._save({"titleid": "id5"})

        bak = ini.with_suffix(ini.suffix + ".bak")
        self.assertTrue(bak.exists())
        self.assertIn('"OLD1"', bak.read_text())            # rule-#5 recovery snapshot is pristine
        self.assertNotIn('"TRANSIENT_DEV"', bak.read_text())
        self.assertIn("WIDTH = 640", ini.read_text())       # the user's edit survived
        self.assertNotIn('"TRANSIENT_DEV"', ini.read_text())

    def test_no_orphan_load_is_noop(self):
        gd = self._game(_CANON)                             # no .mad-restore -> nothing to sweep
        ini = P.ini_of(gd)
        before = ini.read_text()
        L._load_buffer("id5")
        self.assertEqual(ini.read_text(), before)          # on-disk ini untouched
        self.assertIn('"OLD1"', L._buf["text"])


if __name__ == "__main__":
    unittest.main()
