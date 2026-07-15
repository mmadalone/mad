"""bezel_cfg._set_enable_in flips input_overlay_enable via the canonical fsutil.atomic_write_text
(same-dir temp + os.replace + on-failure temp cleanup + the staterev('config') bump), NOT the old
4th inline tmp.write_text()/tmp.replace() copy that used a .mad-tmp suffix and left that temp
orphaned if the replace failed.

Regression for the 2026-07-15 review finding #33. The write still flips correctly, is a no-op when
already in the requested state, and leaves NO stray temp beside the cfg on success.
Run:  python3 -m unittest tests.test_bezel_set_enable_atomic -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import bezel_cfg


class SetEnableAtomic(unittest.TestCase):
    def _cfg(self, enabled: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "Game.cfg"
        p.write_text('input_overlay_enable = "' + enabled + '"\n'
                     'input_overlay = "/x/y.cfg"\n', encoding="utf-8")
        return p

    def test_flip_true_to_false_and_no_orphan_temp(self):
        p = self._cfg("true")
        self.assertTrue(bezel_cfg._set_enable_in(p, False))
        self.assertIn('input_overlay_enable = "false"', p.read_text())
        # The old inline path left <name>.cfg.mad-tmp on a failed replace; the fsutil path leaves
        # NO stray temp beside the cfg on success either.
        strays = [q.name for q in p.parent.iterdir() if q.name != p.name]
        self.assertEqual(strays, [], "unexpected leftover file(s): %r" % strays)

    def test_flip_false_to_true(self):
        p = self._cfg("false")
        self.assertTrue(bezel_cfg._set_enable_in(p, True))
        self.assertIn('input_overlay_enable = "true"', p.read_text())

    def test_noop_when_already_in_state_returns_false(self):
        p = self._cfg("true")
        before = p.read_text()
        self.assertFalse(bezel_cfg._set_enable_in(p, True))   # already true -> no write
        self.assertEqual(p.read_text(), before)


if __name__ == "__main__":
    unittest.main()
