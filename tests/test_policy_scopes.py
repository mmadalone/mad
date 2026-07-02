"""policy_cmds write methods generalized to the four scopes (RetroArch-hub).

kind in {global, system, collection, game}; legacy shapes still work. Writes go to
a temp local policy (policy_cmds.LOCAL is monkeypatched), read back with tomllib.
"""
import tempfile
import tomllib
import unittest
from pathlib import Path

from lib.madsrv import policy_cmds


class PolicyScopesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.local = Path(self._tmp.name) / "local.toml"
        self._orig = policy_cmds.LOCAL
        policy_cmds.LOCAL = self.local

    def tearDown(self):
        policy_cmds.LOCAL = self._orig
        self._tmp.cleanup()

    def _read(self) -> dict:
        return tomllib.loads(self.local.read_text()) if self.local.exists() else {}

    def test_set_ports_game_scope(self):
        policy_cmds._set_ports({"kind": "game", "name": "nes:Zapper",
                                "order": ["A", "B"], "nports": 2})
        self.assertEqual(self._read()["games"]["nes:Zapper"]["ports"],
                         [["A", "B"], ["A", "B"]])

    def test_set_ports_global_scope(self):
        policy_cmds._set_ports({"kind": "global", "order": ["X"], "nports": 2})
        self.assertEqual(self._read()["defaults"]["ports"], [["X"], ["X"]])

    def test_set_ports_collection_require_sinden(self):
        policy_cmds._set_ports({"kind": "collection", "name": "Gun",
                                "order": ["S"], "nports": 1, "require_sinden": True})
        col = self._read()["collections"]["Gun"]
        self.assertTrue(col["require_sinden"])
        self.assertEqual(col["ports"], [["S"]])

    def test_legacy_system_shape_still_works(self):
        policy_cmds._set_ports({"kind": "system", "name": "snes",
                                "order": ["DualSense"], "nports": 2})
        self.assertEqual(self._read()["systems"]["snes"]["ports"],
                         [["DualSense"], ["DualSense"]])

    def test_clear_ports_prunes_game_husk(self):
        policy_cmds._set_ports({"kind": "game", "name": "nes:Z",
                                "order": ["A"], "nports": 1})
        policy_cmds._clear_ports({"kind": "game", "name": "nes:Z"})
        self.assertNotIn("nes:Z", self._read().get("games", {}))

    def test_set_pins_game_and_legacy_global(self):
        policy_cmds._set_pins({"kind": "game", "name": "nes:Z",
                               "pins": {"1": "054c:0ce6"}})
        self.assertEqual(self._read()["games"]["nes:Z"]["pins"], {"1": "054c:0ce6"})
        policy_cmds._set_pins({"scope": None, "pins": {"1": "g"}})   # legacy global
        self.assertEqual(self._read()["pins"], {"1": "g"})

    def test_set_scope_flag_system_revert_prunes(self):
        # system scope has a reliable base default -> value==default reverts + prunes
        policy_cmds._set_scope_flag({"kind": "system", "name": "faketestsys",
                                     "flag": "require_sinden", "value": True})
        self.assertTrue(self._read()["systems"]["faketestsys"]["require_sinden"])
        policy_cmds._set_scope_flag({"kind": "system", "name": "faketestsys",
                                     "flag": "require_sinden", "value": False})
        self.assertNotIn("faketestsys", self._read().get("systems", {}))

    def test_set_scope_flag_game_always_persists(self):
        # game scope does NOT auto-revert (review issue 2): a real override never
        # silently drops, even when it equals the naive default.
        policy_cmds._set_scope_flag({"kind": "game", "name": "nes:Z",
                                     "flag": "require_sinden", "value": False})
        self.assertIs(self._read()["games"]["nes:Z"]["require_sinden"], False)


if __name__ == "__main__":
    unittest.main()
