"""retroarch_cfg per-game (PG_*) sentinel writer + triple-block coexistence.

The highest-risk Phase 3 bet: one per-game override `config/<Core>/<rom>.cfg` can hold
THREE independent sentinel blocks — the router reservation (BEGIN/END), the bezel-project
overlay lines, and the NEW MAD per-game block (PG_BEGIN/PG_END) — with each writer
touching ONLY its own block. Pure: a temp core dir, RA_CONFIG_BASE + SYSTEM_CORE_MAP
monkeypatched.

Run:  python3 -m unittest tests.test_retroarch_pergame_cfg -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import retroarch_cfg as rcfg

SYS = "testsys"
ROM = "Test Game (USA)"
BEZEL = ('input_overlay = "/path/to/overlay.cfg"\n'
         'aspect_ratio_index = "22"\n')


def _bezel_present(txt: str) -> bool:
    return ('input_overlay = "/path/to/overlay.cfg"' in txt
            and 'aspect_ratio_index = "22"' in txt)


class PerGameCfg(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ra-pg-test-"))
        self.core = self.tmp / "FakeCore"
        self.core.mkdir()
        self.cfg = self.core / f"{ROM}.cfg"
        self._saved = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore"]}

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_bezel(self):
        self.cfg.write_text("# bezelproject — auto-generated\n" + BEZEL, encoding="utf-8")

    # ── the three writers coexist ──
    def test_set_game_option_preserves_router_and_bezel_byte_for_byte(self):
        self._seed_bezel()
        rcfg.write_override(SYS, ROM, {1: "X-Arcade", 2: "DualSense"})
        router_block = rcfg._SENTINEL_RE.search(self.cfg.read_text()).group(0)

        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")
        txt = self.cfg.read_text()
        # per-game block landed, router block byte-for-byte, bezel intact
        self.assertIn(rcfg.PG_BEGIN, txt)
        self.assertIn('video_smooth = "true"', txt)
        self.assertEqual(rcfg._SENTINEL_RE.search(txt).group(0), router_block)
        self.assertTrue(_bezel_present(txt))

    def test_router_clear_override_preserves_pg_block(self):
        self._seed_bezel()
        rcfg.write_override(SYS, ROM, {1: "X-Arcade"})
        rcfg.set_game_option(SYS, ROM, "menu_driver", "ozone")
        pg_block = rcfg._PG_SENTINEL_RE.search(self.cfg.read_text()).group(0)

        rcfg.clear_override(SYS, ROM)
        txt = self.cfg.read_text()
        self.assertNotIn(rcfg.BEGIN, txt)                       # router block gone
        self.assertEqual(rcfg._PG_SENTINEL_RE.search(txt).group(0), pg_block)  # PG intact
        self.assertTrue(_bezel_present(txt))

    def test_router_write_preserves_existing_pg_block(self):
        self._seed_bezel()
        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")   # PG first
        rcfg.write_override(SYS, ROM, {1: "X-Arcade"})           # router after
        txt = self.cfg.read_text()
        self.assertIn(rcfg.BEGIN, txt)
        self.assertIn(rcfg.PG_BEGIN, txt)
        self.assertIn('video_smooth = "true"', txt)
        self.assertTrue(_bezel_present(txt))

    # ── get / has / multi-key / clear ──
    def test_get_and_has_correctness(self):
        self.assertFalse(rcfg.has_game_overrides(SYS, ROM))
        self.assertEqual(rcfg.get_game_options(SYS, ROM), {})

        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")
        rcfg.set_game_option(SYS, ROM, "menu_driver", "ozone")
        self.assertTrue(rcfg.has_game_overrides(SYS, ROM))
        self.assertEqual(rcfg.get_game_options(SYS, ROM),
                         {"video_smooth": "true", "menu_driver": "ozone"})

    def test_set_is_idempotent(self):
        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")
        once = self.cfg.read_text()
        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")
        self.assertEqual(self.cfg.read_text(), once)

    def test_clear_one_key_keeps_others_and_bezel(self):
        self._seed_bezel()
        rcfg.set_game_option(SYS, ROM, "video_smooth", "true")
        rcfg.set_game_option(SYS, ROM, "menu_driver", "ozone")

        rcfg.set_game_option(SYS, ROM, "video_smooth", None)     # clear one
        self.assertEqual(rcfg.get_game_options(SYS, ROM), {"menu_driver": "ozone"})
        self.assertTrue(_bezel_present(self.cfg.read_text()))

    def test_clear_all_keys_removes_pg_block_keeps_bezel(self):
        self._seed_bezel()
        rcfg.set_game_option(SYS, ROM, "menu_driver", "ozone")
        rcfg.set_game_option(SYS, ROM, "menu_driver", None)      # last key gone
        txt = self.cfg.read_text()
        self.assertNotIn(rcfg.PG_BEGIN, txt)
        self.assertFalse(rcfg.has_game_overrides(SYS, ROM))
        self.assertTrue(_bezel_present(txt))

    def test_pg_only_file_removed_when_emptied(self):
        # No bezel/router: a pure PG file with all keys cleared is removed.
        rcfg.set_game_option(SYS, ROM, "menu_driver", "ozone")
        self.assertTrue(self.cfg.exists())
        rcfg.set_game_option(SYS, ROM, "menu_driver", None)
        self.assertFalse(self.cfg.exists())


if __name__ == "__main__":
    unittest.main()
