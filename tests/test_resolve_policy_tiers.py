"""routing.resolve_policy four-tier cascade (RetroArch-hub plan, phase 0).

Most-specific-wins: per-game [games."<sys>:<rom>"] > per-collection > per-system.
A per-game rule inherits its system entry as the base, then overrides wholesale.
"""
import unittest

from lib import routing

POLICY = {
    "systems": {
        "nes": {"category": "console", "ports": [["A"], ["A"]],
                "require_sinden": False},
    },
    "collections": {
        "Lightgun": {"inherits": "nes", "ports": [["SINDEN"]],
                     "require_sinden": True},
    },
    "games": {
        "nes:Zapper Game": {"ports": [["ZAP"], ["ZAP"]]},
    },
}


class ResolvePolicyTiersTest(unittest.TestCase):
    def test_system_tier(self):
        r = routing.resolve_policy(POLICY, "nes")
        self.assertEqual(r["ports"], [["A"], ["A"]])

    def test_collection_wins_over_system(self):
        r = routing.resolve_policy(POLICY, "nes", "Lightgun")
        self.assertEqual(r["ports"], [["SINDEN"]])
        self.assertTrue(r["require_sinden"])

    def test_pergame_wins_and_inherits_system(self):
        r = routing.resolve_policy(POLICY, "nes", None, "Zapper Game")
        self.assertEqual(r["ports"], [["ZAP"], ["ZAP"]])     # per-game ports win
        self.assertEqual(r["category"], "console")            # inherited from system
        self.assertIs(r.get("require_sinden"), False)         # inherited from system

    def test_pergame_wins_over_collection(self):
        r = routing.resolve_policy(POLICY, "nes", "Lightgun", "Zapper Game")
        self.assertEqual(r["ports"], [["ZAP"], ["ZAP"]])     # game beats collection

    def test_no_rom_skips_pergame(self):
        r = routing.resolve_policy(POLICY, "nes", None, None)
        self.assertEqual(r["ports"], [["A"], ["A"]])

    def test_unknown_game_falls_through_to_system(self):
        r = routing.resolve_policy(POLICY, "nes", None, "Some Other Game")
        self.assertEqual(r["ports"], [["A"], ["A"]])

    def test_pergame_cannot_unskip_base_handsoff(self):
        pol = {"systems": {"ps2": {"category": "console", "router_skip": True}},
               "games": {"ps2:G": {"router_skip": False, "ports": [["X"]]}}}
        r = routing.resolve_policy(pol, "ps2", None, "G")
        self.assertIs(r["router_skip"], True)          # clamp re-asserted
        self.assertEqual(r["ports"], [["X"]])          # other overrides still apply

    def test_collection_inherits_cannot_unskip_base_handsoff(self):
        pol = {"systems": {"ps2": {"router_skip": True}},
               "collections": {"C": {"inherits": "ps2", "router_skip": False}}}
        self.assertIs(routing.resolve_policy(pol, "ps2", "C")["router_skip"], True)

    def test_nondict_game_husk_falls_through(self):
        pol = {"systems": {"nes": {"ports": [["A"]]}}, "games": {"nes:Z": "junk"}}
        self.assertEqual(routing.resolve_policy(pol, "nes", None, "Z")["ports"], [["A"]])

    def test_nondict_games_table_falls_through(self):
        pol = {"systems": {"nes": {"ports": [["A"]]}}, "games": "oops"}
        self.assertEqual(routing.resolve_policy(pol, "nes", None, "Z")["ports"], [["A"]])


if __name__ == "__main__":
    unittest.main()
