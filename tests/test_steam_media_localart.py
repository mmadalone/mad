"""
Tests for steam-fetch-media.py's LOCAL (offline) art lookup.

Steam moved the per-app library art into per-asset hash subdirs
(<appid>/<hash>/library_hero.jpg) instead of the old flat <appid>/library_hero.jpg.
These tests pin that the lookup handles BOTH layouts, picks exact filenames (so
library_hero_blur.jpg can't shadow library_hero.jpg), prefers the 600x900 capsule
for the cover, and only falls back to library_capsule.jpg when it's portrait.

Run: python3 -m unittest tests.test_steam_media_localart -v
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _img(path: Path, w: int, h: int) -> bool:
    """Write a real WxH jpg via ffmpeg; return False if ffmpeg is unavailable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"color=c=red:s={w}x{h}", "-frames:v", "1", str(path)],
            capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


class LocalArtLookup(unittest.TestCase):
    def setUp(self):
        self.mod = _load("sfm_localart", "steam-fetch-media.py")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.mod.LIBCACHE = self.tmp                       # redirect cache root

    def app(self, appid):
        d = self.tmp / str(appid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def touch(self, *parts):
        p = self.tmp.joinpath(*[str(x) for x in parts])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    # ── libcache_asset: both layouts ───────────────────────────────────────
    def test_hashed_subdir_layout(self):
        want = self.touch(900, "deadbeef", "library_hero.jpg")
        self.assertEqual(self.mod.libcache_asset(900, "library_hero.jpg"), want)

    def test_flat_layout(self):
        want = self.touch(901, "logo.png")
        self.assertEqual(self.mod.libcache_asset(901, "logo.png"), want)

    def test_flat_wins_over_nested(self):
        flat = self.touch(902, "logo.png")
        self.touch(902, "aaaa", "logo.png")
        self.assertEqual(self.mod.libcache_asset(902, "logo.png"), flat)

    def test_blur_does_not_shadow_hero(self):
        self.touch(903, "h1", "library_hero_blur.jpg")
        real = self.touch(903, "h2", "library_hero.jpg")
        self.assertEqual(self.mod.libcache_asset(903, "library_hero.jpg"), real)

    def test_missing_app_dir(self):
        self.assertIsNone(self.mod.libcache_asset(999, "logo.png"))

    def test_name_priority_order(self):
        self.touch(904, "c", "library_capsule.jpg")
        first = self.touch(904, "a", "library_600x900.jpg")
        # first name present wins even though both exist
        self.assertEqual(
            self.mod.libcache_asset(904, "library_600x900.jpg", "library_capsule.jpg"),
            first)

    # ── local_cover: 600x900 preferred, capsule only if portrait ──────────
    def test_cover_prefers_600x900(self):
        want = self.touch(905, "x", "library_600x900.jpg")
        self.touch(905, "y", "library_capsule.jpg")
        self.assertEqual(self.mod.local_cover(905), want)

    def test_cover_capsule_fallback_portrait(self):
        cap = self.touch(906, "z", "library_capsule.jpg")
        self.mod.is_portrait = lambda p: True              # decouple from ffprobe
        self.assertEqual(self.mod.local_cover(906), cap)

    def test_cover_capsule_rejected_when_landscape(self):
        self.touch(907, "z", "library_capsule.jpg")
        self.mod.is_portrait = lambda p: False
        self.assertIsNone(self.mod.local_cover(907))

    # ── is_portrait: real images (skipped if ffmpeg absent) ───────────────
    def test_is_portrait_real_images(self):
        port = self.tmp / "p.jpg"
        land = self.tmp / "l.jpg"
        if not (_img(port, 300, 450) and _img(land, 450, 300)):
            self.skipTest("ffmpeg unavailable")
        self.assertTrue(self.mod.is_portrait(port))
        self.assertFalse(self.mod.is_portrait(land))


if __name__ == "__main__":
    unittest.main()
