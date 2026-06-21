"""Tests for lib/madsrv/sidebar_cmds.py: capability auto-hide + FORCE_* overrides, that CORE
rows are always visible, and sidebar.set's writes. Probes are monkeypatched (no real hardware).
Run: python3 -m unittest tests.test_sidebar_cmds -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.madsrv import sidebar_cmds as sc   # noqa: E402

CORE = ("preview", "systems", "priority", "players", "quit-combo",
        "standalones", "retroarch", "gamepads", "splash", "backup", "sidebar")


def _vis(conf):
    return {s["key"]: s["visible"] for s in sc._sections(conf=conf)}


class Sidebar(unittest.TestCase):
    def setUp(self):
        self._orig = dict(sc._PROBES)
        for k in sc._PROBES:                 # default: all capabilities OFF
            sc._PROBES[k] = lambda: False

    def tearDown(self):
        sc._PROBES.clear()
        sc._PROBES.update(self._orig)

    def test_core_always_visible(self):
        v = _vis({})
        for k in CORE:
            self.assertTrue(v[k], k)

    def test_capability_gating(self):
        v = _vis({})                          # all caps off
        self.assertFalse(v["lightgun"])
        self.assertFalse(v["x-arcade"])
        self.assertFalse(v["bezelproject"])
        sc._PROBES["sinden"] = lambda: True
        self.assertTrue(_vis({})["lightgun"])

    def test_force_show_overrides_missing_capability(self):
        self.assertTrue(_vis({"FORCE_SHOW_LIGHTGUN": "1"})["lightgun"])

    def test_force_hide_overrides_present_capability(self):
        sc._PROBES["sinden"] = lambda: True
        self.assertFalse(_vis({"FORCE_HIDE_LIGHTGUN": "1"})["lightgun"])

    def test_core_ignores_force_hide(self):
        self.assertTrue(_vis({"FORCE_HIDE_PREVIEW": "1"})["preview"])

    def test_registration(self):
        from lib.madsrv.rpc import _METHODS
        self.assertIn("sidebar.sections", _METHODS)
        self.assertIn("sidebar.set", _METHODS)

    def test_set_writes_keys(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        try:
            sc._sidebar_set({"key": "lightgun", "mode": "show"})
            t = conf.read_text()
            self.assertIn("FORCE_SHOW_LIGHTGUN=1", t)
            self.assertIn("FORCE_HIDE_LIGHTGUN=0", t)
            sc._sidebar_set({"key": "lightgun", "mode": "hide"})
            t = conf.read_text()
            self.assertIn("FORCE_SHOW_LIGHTGUN=0", t)
            self.assertIn("FORCE_HIDE_LIGHTGUN=1", t)
            sc._sidebar_set({"key": "lightgun", "mode": "auto"})
            t = conf.read_text()
            self.assertIn("FORCE_SHOW_LIGHTGUN=0", t)
            self.assertIn("FORCE_HIDE_LIGHTGUN=0", t)
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_set_rejects_core_and_bad_mode(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            sc._sidebar_set({"key": "preview", "mode": "hide"})   # core, not togglable
        with self.assertRaises(RpcError):
            sc._sidebar_set({"key": "lightgun", "mode": "bogus"})


if __name__ == "__main__":
    unittest.main()
