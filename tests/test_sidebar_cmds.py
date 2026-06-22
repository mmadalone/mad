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

    def test_core_is_hideable_now(self):
        # Universal hide: even a core row hides under FORCE_HIDE (was always-visible before).
        self.assertFalse(_vis({"FORCE_HIDE_PREVIEW": "1"})["preview"])

    def test_sidebar_never_hidden(self):
        # The toggle page is the escape hatch — FORCE_HIDE can't remove it.
        self.assertTrue(_vis({"FORCE_HIDE_SIDEBAR": "1"})["sidebar"])

    def test_can_hide_field(self):
        rows = {s["key"]: s["can_hide"] for s in sc._sections(conf={})}
        self.assertFalse(rows["sidebar"])
        self.assertTrue(rows["preview"])
        self.assertTrue(rows["lightgun"])

    def test_registration(self):
        from lib.madsrv.rpc import _METHODS
        self.assertIn("sidebar.sections", _METHODS)
        self.assertIn("sidebar.set", _METHODS)
        self.assertIn("sidebar.set_order", _METHODS)

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

    def test_set_rejects_sidebar_hide_bad_mode_and_unknown(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            sc._sidebar_set({"key": "sidebar", "mode": "hide"})   # escape hatch, can't hide
        with self.assertRaises(RpcError):
            sc._sidebar_set({"key": "lightgun", "mode": "bogus"})
        with self.assertRaises(RpcError):
            sc._sidebar_set({"key": "nope", "mode": "show"})       # unknown section

    def test_set_allows_core_hide(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        try:
            sc._sidebar_set({"key": "preview", "mode": "hide"})   # core is hideable now
            self.assertIn("FORCE_HIDE_PREVIEW=1", conf.read_text())
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_default_order_is_catalog(self):
        order = [s["key"] for s in sc._sections(conf={})]
        self.assertEqual(order, [k for k, _, _, _ in sc._SECTIONS])

    def test_set_order_persists_and_reorders(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        try:
            sc._sidebar_set_order({"order": ["backup", "preview", "sidebar"]})
            self.assertIn("SIDEBAR_ORDER=backup,preview,sidebar", conf.read_text())
            order = [s["key"] for s in sc._sections()]
            self.assertEqual(order[:3], ["backup", "preview", "sidebar"])
            self.assertEqual(set(order), {k for k, _, _, _ in sc._SECTIONS})
            self.assertEqual(len(order), len(sc._SECTIONS))       # no dupes/drops
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_set_order_drops_unknown_keys(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        try:
            res = sc._sidebar_set_order({"order": ["backup", "bogus", "preview"]})
            self.assertEqual(res["order"], ["backup", "preview"])
            self.assertIn("SIDEBAR_ORDER=backup,preview", conf.read_text())
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_set_order_dedupes_duplicate_keys(self):
        conf = Path(tempfile.mkdtemp()) / "install.conf"
        os.environ["MAD_INSTALL_CONF"] = str(conf)
        try:
            res = sc._sidebar_set_order({"order": ["backup", "backup", "preview"]})
            self.assertEqual(res["order"], ["backup", "preview"])      # deduped, first-wins
            order = [s["key"] for s in sc._sections()]
            self.assertEqual(len(order), len(sc._SECTIONS))            # no duplicate rows
            self.assertEqual(len(order), len(set(order)))
        finally:
            del os.environ["MAD_INSTALL_CONF"]

    def test_sections_dedupes_saved_order(self):
        # Defense in depth: a hand-edited SIDEBAR_ORDER with dupes still yields unique rows.
        order = [s["key"] for s in sc._sections(conf={"SIDEBAR_ORDER": "backup,backup,preview"})]
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(order[:2], ["backup", "preview"])

    def test_sections_ignores_blank_and_unknown_order(self):
        for raw in ("", "   ", ",,,", "ghost,more-ghost"):
            order = [s["key"] for s in sc._sections(conf={"SIDEBAR_ORDER": raw})]
            self.assertEqual(order, [k for k, _, _, _ in sc._SECTIONS], raw)


if __name__ == "__main__":
    unittest.main()
