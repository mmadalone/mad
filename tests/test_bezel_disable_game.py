"""bezel_cfg.disable_game must only rewrite a per-game cfg WE generated, using the same
anchored _is_tool_generated() gate as install()/assign_bezel() -- not a loose `SENTINEL in text`
substring. A hand-made cfg that merely mentions bezelproject in passing (e.g. "## bezelproject")
and carries an input_overlay_enable line would otherwise be silently rewritten, flipping the
user's intentionally-disabled overlay -- mutating a cfg House Rule #5 says must never be touched.

Regression for the 2026-07-15 review finding #14.
Run:  python3 -m unittest tests.test_bezel_disable_game -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import bezel_cfg

# A real system key with cores, so disable_game walks a cfg path. megadrive spans
# the genesis + megadrive rom dirs and has libretro cores.
_KEY = "megadrive"
_GAME = "Sonic The Hedgehog"

_HAND_MADE = (
    "## bezelproject-inspired layout, hand-tuned -- DO NOT let MAD touch this\n"
    'input_overlay_enable = "false"\n'                # user INTENTIONALLY disabled
    'input_overlay = "/home/deck/my/custom.cfg"\n'
)
_TOOL_MADE = (
    "# bezelproject - auto-generated, safe to delete\n"
    'input_overlay_enable = "false"\n'
    'input_overlay = "/home/deck/.../overlays/GameBezels/Genesis/Sonic.cfg"\n'
)


class DisableGameGate(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._base = bezel_cfg.CONFIG_BASE
        bezel_cfg.CONFIG_BASE = self.d
        s = bezel_cfg._by_key(_KEY)
        self.cores = s[5] if s else []
        self.assertTrue(self.cores, "test needs a system with cores")

    def tearDown(self):
        bezel_cfg.CONFIG_BASE = self._base
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, text):
        for core in self.cores:
            p = self.d / core / f"{_GAME}.cfg"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def _read_one(self):
        core = self.cores[0]
        return (self.d / core / f"{_GAME}.cfg").read_text()

    def test_hand_made_cfg_is_not_touched(self):
        # "## bezelproject" trips the OLD loose substring but not the anchored gate.
        self._write(_HAND_MADE)
        res = bezel_cfg.disable_game(_KEY, _GAME, on=True)   # try to ENABLE (flip user's "false")
        self.assertEqual(res["changed"], 0, "hand-made cfg must be left untouched")
        self.assertEqual(self._read_one(), _HAND_MADE, "content must be byte-identical")

    def test_tool_generated_cfg_is_rewritten(self):
        # A genuine sentinel line (anchored) IS ours -> the toggle applies.
        self._write(_TOOL_MADE)
        res = bezel_cfg.disable_game(_KEY, _GAME, on=True)
        self.assertGreaterEqual(res["changed"], 1)
        self.assertIn('input_overlay_enable = "true"', self._read_one())


if __name__ == "__main__":
    unittest.main()
