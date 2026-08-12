"""show-launchscreen.py prepare_image cache: hit skips ffmpeg entirely, miss
scales atomically into storage/launchscreens keyed by source+mtime+size+WxH,
docked/handheld variants coexist, a theme update (new mtime) prunes only the
stale same-resolution variant, and ANY cache failure falls back to the legacy
scale-into-a-tempfile path (the splash must never be lost to a broken cache).

The module keeps tkinter imports inside main(), so importing it here is
display- and CI-safe. ffmpeg is mocked except one real round-trip that skips
when the binary is absent.

Run:  python3 -m unittest tests.test_launchscreen_cache -v
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

# A valid 1x1 red PNG.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049"
    "454e44ae426082")


def _load():
    spec = importlib.util.spec_from_file_location("show_launchscreen",
                                                  ROOT / "show-launchscreen.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PrepareImageCache(unittest.TestCase):
    def setUp(self):
        self.sl = _load()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.data_root = Path(self._td.name) / "Emulation"
        self.src = Path(self._td.name) / "launching.png"
        self.src.write_bytes(TINY_PNG)
        env = mock.patch.dict(os.environ, {"MAD_DATA_ROOT": str(self.data_root)})
        env.start()
        self.addCleanup(env.stop)
        from lib import mad_paths
        mad_paths.data_root.cache_clear()
        self.addCleanup(mad_paths.data_root.cache_clear)

        self.scaled = []

        def fake_scale(src, sw, sh, dest):
            self.scaled.append((src, sw, sh, dest))
            Path(dest).write_bytes(TINY_PNG + f"{sw}x{sh}".encode())

        p = mock.patch.object(self.sl, "_ffmpeg_scale", side_effect=fake_scale)
        p.start()
        self.addCleanup(p.stop)

    @property
    def cdir(self) -> Path:
        return self.data_root / "storage" / "launchscreens"

    def test_miss_then_hit(self):
        path1, tmp1 = self.sl.prepare_image(str(self.src), 1920, 1080)
        self.assertFalse(tmp1)
        self.assertTrue(Path(path1).is_file())
        self.assertEqual(Path(path1).parent, self.cdir)
        self.assertEqual(len(self.scaled), 1)
        path2, tmp2 = self.sl.prepare_image(str(self.src), 1920, 1080)
        self.assertEqual((path1, tmp1), (path2, tmp2))
        self.assertEqual(len(self.scaled), 1, "cache hit must not re-run ffmpeg")
        self.assertEqual([p for p in self.cdir.iterdir() if p.name.startswith(".")],
                         [], "no tmp litter")

    def test_docked_and_handheld_variants_coexist(self):
        p_dock, _ = self.sl.prepare_image(str(self.src), 1920, 1080)
        p_hand, _ = self.sl.prepare_image(str(self.src), 1280, 800)
        self.assertNotEqual(p_dock, p_hand)
        self.assertTrue(Path(p_dock).is_file())
        self.assertTrue(Path(p_hand).is_file())

    def test_theme_update_prunes_only_the_stale_same_resolution_variant(self):
        p_old, _ = self.sl.prepare_image(str(self.src), 1920, 1080)
        p_hand, _ = self.sl.prepare_image(str(self.src), 1280, 800)
        self.src.write_bytes(TINY_PNG + b"v2")          # new mtime + size
        p_new, _ = self.sl.prepare_image(str(self.src), 1920, 1080)
        self.assertNotEqual(p_old, p_new)
        self.assertFalse(Path(p_old).exists(), "stale 1080p variant pruned")
        self.assertTrue(Path(p_hand).exists(), "other resolution untouched")
        self.assertTrue(Path(p_new).is_file())

    def test_zero_byte_hit_is_treated_as_a_miss(self):
        # a hard power-off can leave a zero-length file under a valid key
        # (review 2026-08-13); serving it would kill the splash forever
        p1, _ = self.sl.prepare_image(str(self.src), 1920, 1080)
        Path(p1).write_bytes(b"")
        p2, is_temp = self.sl.prepare_image(str(self.src), 1920, 1080)
        self.assertFalse(is_temp)
        self.assertEqual(p1, p2)
        self.assertGreater(Path(p2).stat().st_size, 0, "re-scaled over the husk")
        self.assertEqual(len(self.scaled), 2)

    def test_orphaned_tmp_dotfiles_are_swept_on_miss(self):
        self.cdir.mkdir(parents=True, exist_ok=True)
        old = self.cdir / ".dead-key.12345.png"
        old.write_bytes(b"x")
        os.utime(old, (1, 1))                       # ancient
        fresh = self.cdir / ".live-key.999.png"
        fresh.write_bytes(b"y")                     # now: a concurrent scale
        self.sl.prepare_image(str(self.src), 1920, 1080)
        self.assertFalse(old.exists(), "stale orphan swept")
        self.assertTrue(fresh.exists(), "recent tmp untouched")

    def test_cache_failure_falls_back_to_tempfile(self):
        with mock.patch.object(self.sl, "_cache_dir",
                               side_effect=RuntimeError("no cache for you")):
            path, is_temp = self.sl.prepare_image(str(self.src), 1920, 1080)
        try:
            self.assertTrue(is_temp)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(len(self.scaled), 1)
        finally:
            os.unlink(path)

    def test_missing_source_falls_back_and_propagates_scale_error(self):
        # stat() fails -> fallback path; the fake scale still succeeds, which
        # mirrors the legacy behavior (ffmpeg is the one that errors on a truly
        # unreadable source, and that error propagates).
        path, is_temp = self.sl.prepare_image(str(self.src) + ".gone", 1920, 1080)
        try:
            self.assertTrue(is_temp)
        finally:
            os.unlink(path)

    def test_failed_scale_leaves_no_cache_entry(self):
        with mock.patch.object(self.sl, "_ffmpeg_scale",
                               side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.sl.prepare_image(str(self.src), 1920, 1080)
        if self.cdir.exists():
            self.assertEqual([p.name for p in self.cdir.iterdir()], [],
                             "a failed scale must leave neither entry nor tmp")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_real_ffmpeg_round_trip(self):
        # a FRESH module load bypasses the fake scaler (patched on self.sl only);
        # the MAD_DATA_ROOT redirect stays active, so this writes to the temp tree
        sl2 = _load()
        path, is_temp = sl2.prepare_image(str(self.src), 64, 48)
        self.assertFalse(is_temp)
        data = Path(path).read_bytes()
        self.assertEqual(data[1:4], b"PNG")
        path2, _ = sl2.prepare_image(str(self.src), 64, 48)
        self.assertEqual(path, path2)


if __name__ == "__main__":
    unittest.main()
