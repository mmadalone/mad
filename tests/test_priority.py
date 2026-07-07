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

    def test_system_toggles_include_warn_then_router_skip(self):
        # RA-hub Controllers editor now renders a toggles list: the X-Arcade warn
        # (when the category has one) followed by Hands-off (router_skip).
        merged = {"systems": {"nes": {"category": "console"}}, "collections": {}}
        r = self._get({"kind": "system", "name": "nes"}, merged)
        self.assertEqual([t["key"] for t in r["toggles"]],
                         ["warn_when_only_xarcade", "router_skip"])
        # back-compat: the single "warn" object is still emitted for old binaries.
        self.assertEqual(r["warn"]["key"], "warn_when_only_xarcade")

    def test_system_with_no_warn_still_gets_router_skip_toggle(self):
        merged = {"systems": {"switch": {"category": "handheld"}}, "collections": {}}
        r = self._get({"kind": "system", "name": "switch"}, merged)
        self.assertEqual([t["key"] for t in r["toggles"]], ["router_skip"])
        self.assertNotIn("warn", r)

    def test_ra_options_available_gates_on_core_dirs(self):
        # Drives the per-system editor's "RetroArch options" button visibility.
        merged = {"systems": {"nes": {"category": "console"}}, "collections": {}}
        with mock.patch.object(bc, "core_dirs_for_system", return_value=["/core"]):
            self.assertTrue(
                self._get({"kind": "system", "name": "nes"}, merged)["ra_options_available"])
        with mock.patch.object(bc, "core_dirs_for_system", return_value=[]):
            self.assertFalse(
                self._get({"kind": "system", "name": "nes"}, merged)["ra_options_available"])

    def test_router_skip_toggle_reads_policy_value(self):
        merged = {"systems": {"nes": {"category": "console", "router_skip": True}},
                  "collections": {}}
        r = self._get({"kind": "system", "name": "nes"}, merged)
        rs_tog = next(t for t in r["toggles"] if t["key"] == "router_skip")
        self.assertIs(rs_tog["value"], True)

    def test_collection_and_game_have_no_toggles(self):
        merged = {"systems": {}, "games": {},
                  "collections": {"lightgun": {"ports": [["Xbox"]]}}}
        self.assertNotIn("toggles",
                         self._get({"kind": "collection", "name": "lightgun"}, merged))
        self.assertNotIn("toggles", self._get({"kind": "game", "name": "nes:x"}, merged))

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


class PriorityGetGame(unittest.TestCase):
    """priority.get kind="game" — RetroArch-hub Phase 3: a game with no `ports`
    of its own inherits its system's resolved order; a game WITH its own
    `ports` replaces it wholesale; neither `warn` nor `require_sinden` appear."""

    def _get(self, params, merged):
        with mock.patch.object(bc, "load_merged", return_value=merged):
            return bc._priority_get(params)

    def test_game_with_no_own_ports_inherits_system_order(self):
        merged = {"systems": {"nes": {"ports": [["8BitDo", "Xbox"]]}},
                  "collections": {}, "games": {}}
        r = self._get({"kind": "game", "name": "nes:Duck Hunt (World)"}, merged)
        self.assertEqual(r["kind"], "game")
        self.assertEqual(r["order"][:2], ["8BitDo", "Xbox"])
        self.assertFalse(r["configured"])

    def test_game_with_own_ports_replaces_system_wholesale(self):
        merged = {"systems": {"nes": {"ports": [["8BitDo", "Xbox"]]}},
                  "collections": {},
                  "games": {"nes:Duck Hunt (World)": {"ports": [["DualSense"]]}}}
        r = self._get({"kind": "game", "name": "nes:Duck Hunt (World)"}, merged)
        self.assertEqual(r["order"][0], "DualSense")
        self.assertNotIn("8BitDo", r["order"][:1])
        self.assertTrue(r["configured"])

    def test_game_scope_has_no_warn_or_require_sinden(self):
        merged = {"systems": {"mame": {"category": "arcade"}}, "collections": {},
                  "games": {}}
        r = self._get({"kind": "game", "name": "mame:sf2"}, merged)
        self.assertNotIn("warn", r)
        self.assertNotIn("require_sinden", r)

    def test_game_with_no_system_entry_still_resolves(self):
        merged = {"systems": {}, "collections": {}, "games": {}}
        r = self._get({"kind": "game", "name": "nes:sonic"}, merged)
        # No configured order anywhere -> falls back to the full known-family
        # list (same "append remaining known families" composition as system/
        # collection scope), not an empty list.
        self.assertEqual(r["order"], bc.controller_families(merged))
        self.assertFalse(r["configured"])

    def test_game_name_splits_on_first_colon_only(self):
        merged = {"systems": {"daphne": {"ports": [["Xbox"]]}}, "collections": {},
                  "games": {}}
        r = self._get({"kind": "game", "name": "daphne:Dragon's Lair: Escape"}, merged)
        self.assertEqual(r["order"][0], "Xbox")

    def test_game_on_an_inherits_only_system_resolves_the_parents_order(self):
        # Adversarial review fix, applied inline: priority.get kind="game" now
        # calls resolve_system (not the raw [systems.<name>] entry), so an
        # inherits-only system whose OWN table has no `ports` at all (the real
        # controller-policy.toml shape: mame/fba/neogeo/... all `inherits =
        # "arcade"` with every port defined only on [systems.arcade]) resolves
        # through the chain instead of falling back to the 2-port default.
        merged = {"systems": {
                      "arcade": {"category": "arcade",
                                "ports": [["X-Arcade", "8BitDo", "DualSense", "Xbox"],
                                          ["X-Arcade", "8BitDo", "DualSense", "Xbox"],
                                          ["DualSense", "8BitDo", "Xbox", "Steam Deck"],
                                          ["DualSense", "8BitDo", "Xbox", "Steam Deck"]]},
                      "mame": {"inherits": "arcade"}},
                  "collections": {}, "games": {}}
        r = self._get({"kind": "game", "name": "mame:sf2"}, merged)
        self.assertEqual(r["order"][:4], ["X-Arcade", "8BitDo", "DualSense", "Xbox"])
        self.assertEqual(r["nports"], 4)      # arcade's REAL 4-port config, not the 2-port default
        self.assertFalse(r["configured"])

    def test_own_husk_without_ports_still_inherits_system(self):
        # A hand-edited/empty game entry (pins-only, say) must NOT be mistaken
        # for "own ports" — falls through to the system's order.
        merged = {"systems": {"nes": {"ports": [["Xbox"]]}}, "collections": {},
                  "games": {"nes:foo": {"pins": {"1": "uniq:x"}}}}
        r = self._get({"kind": "game", "name": "nes:foo"}, merged)
        self.assertEqual(r["order"][0], "Xbox")
        self.assertFalse(r["configured"])


if __name__ == "__main__":
    unittest.main()
