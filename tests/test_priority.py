"""Tests for priority.get's "warn" field (lib.madsrv.backends_cmds) — the
per-system X-Arcade presence-warning toggle that moved off the RetroArch hub's
global Controllers root (see tests/test_racontrollers.py) onto each system's
own Priority/Controllers editor. Reuses the SAME category resolution +
warn-flag selection the Systems page uses (lib.madsrv.systems_cmds
resolve_category / _warn_flag) so the two pages never disagree about which
system gets which warning. Pure logic — merged is monkeypatched via
mock.patch.object on backends_cmds (module-level `from ..policy import
load_merged`, so it must be patched where it's used).

Run: python3 -m unittest tests.test_priority -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib.madsrv import backends_cmds as bc
from lib.madsrv.rpc import RpcError


class PriorityGetWarn(unittest.TestCase):
    def _get(self, params, merged):
        with mock.patch.object(bc, "load_merged", return_value=merged):
            return bc._priority_get(params)

    def test_arcade_system_gets_no_xarcade_warn(self):
        merged = {"systems": {"mame": {"category": "arcade"}}, "collections": {}}
        r = self._get({"kind": "system", "name": "mame"}, merged)
        self.assertEqual(r["warn"], {"key": "warn_when_no_xarcade",
                                     "label": "Warn when the X-Arcade is NOT present",
                                     "value": True})

    def test_mugen_and_openbor_count_as_arcade_even_without_category(self):
        merged = {"systems": {"mugen": {}, "openbor": {}}, "collections": {}}
        for name in ("mugen", "openbor"):
            r = self._get({"kind": "system", "name": name}, merged)
            self.assertEqual(r["warn"]["key"], "warn_when_no_xarcade")

    def test_console_system_gets_only_xarcade_warn(self):
        merged = {"systems": {"nes": {"category": "console"}}, "collections": {}}
        r = self._get({"kind": "system", "name": "nes"}, merged)
        self.assertEqual(r["warn"], {"key": "warn_when_only_xarcade",
                                     "label": "Warn when only the X-Arcade is present",
                                     "value": True})

    def test_system_with_no_warn_category_omits_warn_key(self):
        merged = {"systems": {"switch": {"category": "handheld"}}, "collections": {}}
        r = self._get({"kind": "system", "name": "switch"}, merged)
        self.assertNotIn("warn", r)

    def test_system_with_no_category_at_all_omits_warn_key(self):
        merged = {"systems": {"mystery": {}}, "collections": {}}
        r = self._get({"kind": "system", "name": "mystery"}, merged)
        self.assertNotIn("warn", r)

    def test_warn_value_reads_explicit_override(self):
        merged = {"systems": {"mame": {"category": "arcade",
                                       "warn_when_no_xarcade": False}},
                  "collections": {}}
        r = self._get({"kind": "system", "name": "mame"}, merged)
        self.assertIs(r["warn"]["value"], False)

    def test_category_resolved_through_inherits_chain(self):
        merged = {"systems": {"base": {"category": "arcade"},
                              "child": {"inherits": "base"}},
                  "collections": {}}
        r = self._get({"kind": "system", "name": "child"}, merged)
        self.assertEqual(r["warn"]["key"], "warn_when_no_xarcade")

    def test_collection_kind_has_no_warn_key(self):
        merged = {"systems": {},
                  "collections": {"lightgun": {"ports": [["Xbox"]],
                                               "require_sinden": True}}}
        r = self._get({"kind": "collection", "name": "lightgun"}, merged)
        self.assertNotIn("warn", r)
        self.assertTrue(r["require_sinden"])   # collection path unchanged

    def test_unknown_kind_still_raises_einval(self):
        with self.assertRaises(RpcError):
            self._get({"kind": "bogus", "name": "x"}, {"systems": {}, "collections": {}})


if __name__ == "__main__":
    unittest.main()
