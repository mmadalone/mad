"""
Tests for lib/mad_paths — the MAD data-root resolver.

The load-bearing test is test_default_equals_legacy: with nothing overridden the
resolver must yield the exact historical ~/Emulation/... paths, so existing
EmuDeck installs are byte-for-byte unaffected. data_root() is lru_cached, so
every case clears the cache after mutating the environment.

Run:  python3 -m unittest tests.test_mad_paths -v
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from lib import mad_paths

_KEYS = ("MAD_DATA_ROOT", "storagePath")


class MadPaths(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        for k in _KEYS:
            os.environ.pop(k, None)
        mad_paths.data_root.cache_clear()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        mad_paths.data_root.cache_clear()

    def _set(self, **env):
        for k, v in env.items():
            os.environ[k] = v
        mad_paths.data_root.cache_clear()

    # --- the non-regression guarantee ---
    def test_default_equals_legacy(self):
        self.assertEqual(mad_paths.data_root(), Path.home() / "Emulation")
        self.assertEqual(mad_paths.storage("controller-router"),
                         Path.home() / "Emulation" / "storage" / "controller-router")
        self.assertEqual(mad_paths.storage("sinden", "smoother.ini"),
                         Path.home() / "Emulation" / "storage" / "sinden" / "smoother.ini")
        self.assertEqual(mad_paths.roms_root(), Path.home() / "Emulation" / "roms")
        self.assertEqual(mad_paths.tools_root(), Path.home() / "Emulation" / "tools")
        self.assertEqual(mad_paths.saves_root(), Path.home() / "Emulation" / "saves")
        self.assertEqual(mad_paths.bios_root(), Path.home() / "Emulation" / "bios")

    def test_env_override_redirects(self):
        self._set(MAD_DATA_ROOT="/tmp/madtest")
        self.assertEqual(mad_paths.data_root(), Path("/tmp/madtest"))
        self.assertEqual(mad_paths.storage("controller-router"),
                         Path("/tmp/madtest/storage/controller-router"))
        self.assertEqual(mad_paths.roms_root(), Path("/tmp/madtest/roms"))

    def test_storagePath_followed(self):
        self._set(storagePath="/mnt/sd/EmuStuff/storage")
        self.assertEqual(mad_paths.data_root(), Path("/mnt/sd/EmuStuff"))
        self.assertEqual(mad_paths.storage_root(), Path("/mnt/sd/EmuStuff/storage"))

    def test_explicit_root_beats_storagePath(self):
        self._set(MAD_DATA_ROOT="/tmp/explicit", storagePath="/mnt/sd/EmuStuff/storage")
        self.assertEqual(mad_paths.data_root(), Path("/tmp/explicit"))

    def test_tilde_expands(self):
        self._set(MAD_DATA_ROOT="~/Emu2")
        self.assertEqual(mad_paths.data_root(), Path.home() / "Emu2")

    def test_accessors_compose(self):
        self._set(MAD_DATA_ROOT="/tmp/x")
        self.assertEqual(mad_paths.storage("a", "b"),
                         mad_paths.storage_root() / "a" / "b")


if __name__ == "__main__":
    unittest.main()
