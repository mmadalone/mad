"""policy_settings_cmds: per-system controller-policy flag toggles on the
Standalones tiles (X-Arcade warn; wii adds DolphinBar/Sinden/hands-off). Pure
selection logic + get/set delegating to policy.set_system_flag (mocked).
Run: python3 -m unittest tests.test_policy_settings -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.madsrv import policy_settings_cmds as ps   # noqa: E402
from lib.madsrv.rpc import RpcError                 # noqa: E402


class Flags(unittest.TestCase):
    def test_arcade_system_gets_no_xarcade_warn_only(self):
        merged = {"systems": {"daphne": {"category": "arcade"}}}
        self.assertEqual(ps._flags_for("daphne", merged),
                         [("warn_when_no_xarcade", "Warn when the X-Arcade is NOT present")])

    def test_console_system_gets_only_xarcade_warn(self):
        merged = {"systems": {"ps2": {"category": "console"}}}
        self.assertEqual([k for k, _ in ps._flags_for("ps2", merged)],
                         ["warn_when_only_xarcade"])

    def test_wii_gets_warn_plus_three_extra(self):
        merged = {"systems": {"wii": {"category": "console"}}}
        self.assertEqual([k for k, _ in ps._flags_for("wii", merged)],
                         ["warn_when_only_xarcade", "require_dolphinbar",
                          "require_sinden", "router_skip"])


class TileSections(unittest.TestCase):
    def test_sections_one_per_warn_system(self):
        with mock.patch.object(ps, "SYSFLAGS", {"ps2": [("warn_when_only_xarcade", "x")]}):
            secs = ps.tile_flag_sections(["ps2"], "PlayStation 2")
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0]["kind"], "settings")
        self.assertEqual(secs[0]["arg"], "sysflags_ps2")
        self.assertEqual(secs[0]["label"], "X-Arcade warning")

    def test_multi_system_tile_gets_a_section_each(self):
        with mock.patch.object(ps, "SYSFLAGS",
                               {"wii": [("warn_when_only_xarcade", "x")],
                                "gc": [("warn_when_only_xarcade", "x")]}):
            secs = ps.tile_flag_sections(["wii", "gc"], "Wii")
        self.assertEqual([(s["label"], s["arg"]) for s in secs],
                         [("Controller options", "sysflags_wii"),
                          ("X-Arcade warning", "sysflags_gc")])

    def test_non_warn_system_yields_no_section(self):
        with mock.patch.object(ps, "SYSFLAGS", {}):
            self.assertEqual(ps.tile_flag_sections(["ps2"], "x"), [])


class GetSet(unittest.TestCase):
    def test_get_reads_default_then_override(self):
        with mock.patch.object(ps, "load_merged",
                               return_value={"systems": {"xbox": {"category": "console"}}}):
            it = ps._sysflags_get("xbox")["groups"][0]["settings"][0]
        self.assertEqual(it["key"], "warn_when_only_xarcade")
        self.assertIs(it["value"], True)     # warn default ON
        with mock.patch.object(ps, "load_merged", return_value={"systems": {
                "xbox": {"category": "console", "warn_when_only_xarcade": False}}}):
            it = ps._sysflags_get("xbox")["groups"][0]["settings"][0]
        self.assertIs(it["value"], False)

    def test_set_delegates_to_policy_and_rereads(self):
        seen = {}
        merged_after = {"systems": {"xbox": {"category": "console",
                                             "warn_when_only_xarcade": False}}}
        with mock.patch.object(ps.policy_cmds, "_set_system_flag",
                               side_effect=lambda params: seen.update(params)), \
             mock.patch.object(ps, "load_merged", return_value=merged_after):
            r = ps._sysflags_set("xbox", {"key": "warn_when_only_xarcade", "value": "0"})
        self.assertEqual(seen, {"system": "xbox", "flag": "warn_when_only_xarcade",
                                "value": False})
        self.assertIs(r["value"], False)

    def test_set_rejects_unknown_flag(self):
        with mock.patch.object(ps, "load_merged",
                               return_value={"systems": {"xbox": {"category": "console"}}}):
            with self.assertRaises(RpcError):
                ps._sysflags_set("xbox", {"key": "bogus", "value": "1"})


class MugenTile(unittest.TestCase):
    def test_mugen_in_standalones_catalog(self):
        from lib.madsrv import standalones_cmds as sc
        self.assertIn("mugen", [t["key"] for t in sc.STANDALONES])


if __name__ == "__main__":
    unittest.main()
