"""On-the-go per-game Wii page (lib/madsrv/dolphin_wii_hh_cmds).

.games filters (drop lightgun, hide motion-only, show auto-CC + data-gap); .get shows the resolution
enum always and the Force-CC row only for non-auto-CC games; .set persists to the per-game policy
table with pruning.

Run:  python3 -m unittest tests.test_dolphin_wii_hh -v
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lib.madsrv import dolphin_wii_hh_cmds as hh
from lib.madsrv.rpc import RpcError

_CHOICES = [("native", "Native"), ("2x", "2x"), ("4x", "4x"), ("inherit", "Inherit (leave as-is)")]
# after _res_choices reorders: [inherit, native, 2x, 4x]  -> tokens index: inherit=0 native=1 2x=2 4x=3


class Games(unittest.TestCase):
    def setUp(self):
        self._save = (hh.dolphin_games._roms, hh.gids.gameids, hh.es_gamelist.titles,
                      hh.dolphin_wii_source._is_lightgun, hh.dolphin_wii_tdb.is_cc_capable,
                      hh.dolphin_wii_tdb.is_hidden_motion, hh.handheld_res.resolution_choices,
                      hh.load_merged)
        self.roms = [Path("/ROMs/wii/gun.rvz"), Path("/ROMs/wii/cc.rvz"),
                     Path("/ROMs/wii/motion.rvz"), Path("/ROMs/wii/gap.wad")]
        hh.dolphin_games._roms = lambda system: self.roms
        hh.gids.gameids = lambda roms: {"/ROMs/wii/gun.rvz": "RGUE01", "/ROMs/wii/cc.rvz": "RMCE01",
                                        "/ROMs/wii/motion.rvz": "RSPE01", "/ROMs/wii/gap.wad": "WR5PEY"}
        hh.es_gamelist.titles = lambda system: {"cc": "AAA Mario Kart", "gap": "ZZZ Retro City"}
        hh.dolphin_wii_source._is_lightgun = lambda rom: rom.endswith("gun.rvz")
        hh.dolphin_wii_tdb.is_cc_capable = lambda gid: gid == "RMCE01"
        hh.dolphin_wii_tdb.is_hidden_motion = lambda gid: gid == "RSPE01"
        hh.handheld_res.resolution_choices = lambda s: list(_CHOICES)
        hh.load_merged = lambda: {}

    def tearDown(self):
        (hh.dolphin_games._roms, hh.gids.gameids, hh.es_gamelist.titles,
         hh.dolphin_wii_source._is_lightgun, hh.dolphin_wii_tdb.is_cc_capable,
         hh.dolphin_wii_tdb.is_hidden_motion, hh.handheld_res.resolution_choices,
         hh.load_merged) = self._save

    def test_filters_lightgun_and_motion(self):
        out = hh._games({})
        ids = [g["titleid"] for g in out["games"]]
        self.assertEqual(ids, ["RMCE01", "WR5PEY"])   # gun dropped, motion hidden, sorted by name
        self.assertEqual(out["system"], "wii")

    def test_names_from_gamelist_with_stem_fallback(self):
        by = {g["titleid"]: g for g in hh._games({})["games"]}
        self.assertEqual(by["RMCE01"]["name"], "AAA Mario Kart")   # from the gamelist
        self.assertEqual(by["WR5PEY"]["name"], "ZZZ Retro City")

    def test_summary_and_override_from_pergame(self):
        hh.load_merged = lambda: {"backends": {"dolphin_wii": {"pergame": {
            "WR5PEY": {"force_cc": True, "hhres": "2x"}}}}}
        by = {g["titleid"]: g for g in hh._games({})["games"]}
        self.assertTrue(by["WR5PEY"]["override"])
        self.assertIn("Force CC", by["WR5PEY"]["summary"])
        self.assertIn("2x", by["WR5PEY"]["summary"])
        self.assertFalse(by["RMCE01"]["override"])     # no per-game entry
        self.assertEqual(by["RMCE01"]["summary"], "")

    def test_empty_list_note(self):
        hh.dolphin_games._roms = lambda system: []
        out = hh._games({})
        self.assertEqual(out["games"], [])
        self.assertTrue(out["note"])


class GetPage(unittest.TestCase):
    def setUp(self):
        self._save = (hh.dolphin_wii_tdb.is_cc_capable, hh.handheld_res.resolution_choices,
                      hh.handheld_res.snap_token, hh.proc_guard.emulator_running, hh.load_merged)
        hh.handheld_res.resolution_choices = lambda s: list(_CHOICES)
        hh.handheld_res.snap_token = lambda s, t: t
        hh.proc_guard.emulator_running = lambda name: False
        hh.load_merged = lambda: {}

    def tearDown(self):
        (hh.dolphin_wii_tdb.is_cc_capable, hh.handheld_res.resolution_choices,
         hh.handheld_res.snap_token, hh.proc_guard.emulator_running, hh.load_merged) = self._save

    def test_datagap_game_has_res_and_forcecc(self):
        hh.dolphin_wii_tdb.is_cc_capable = lambda gid: False
        hh.load_merged = lambda: {"backends": {"dolphin_wii": {"pergame": {
            "WR5PEY": {"force_cc": True, "hhres": "2x"}}}}}
        r = hh._get({"titleid": "WR5PEY"})
        titles = [g["title"] for g in r["groups"]]
        self.assertEqual(titles, ["Handheld resolution", "Classic Controller"])
        res = r["groups"][0]["settings"][0]
        self.assertEqual(res["options"][0], "Inherit (per-system default)")   # inherit first
        self.assertEqual(res["value"], 2)             # "2x" -> tokens[inherit,native,2x,4x] index 2
        force = r["groups"][1]["settings"][0]
        self.assertEqual(force["key"], "force_cc")
        self.assertEqual(force["value"], 1)           # forced
        self.assertIn("no controller data", r["note"])

    def test_autocc_game_hides_forcecc(self):
        hh.dolphin_wii_tdb.is_cc_capable = lambda gid: True
        r = hh._get({"titleid": "RMCE01"})
        self.assertEqual([g["title"] for g in r["groups"]], ["Handheld resolution"])   # no force-CC group
        self.assertEqual(r["groups"][0]["settings"][0]["value"], 0)   # inherit default
        self.assertIn("already supports a Classic Controller", r["note"])

    def test_bad_titleid_rejected(self):
        with self.assertRaises(RpcError):
            hh._get({"titleid": "short"})


class SetPage(unittest.TestCase):
    def setUp(self):
        self._save = (hh.handheld_res.resolution_choices, hh.localpolicy.load, hh.localpolicy.dump)
        hh.handheld_res.resolution_choices = lambda s: list(_CHOICES)
        self.store = {"data": {}}
        hh.localpolicy.load = lambda p: self.store["data"]
        hh.localpolicy.dump = lambda p, d: self.store.__setitem__("data", d)

    def tearDown(self):
        (hh.handheld_res.resolution_choices, hh.localpolicy.load, hh.localpolicy.dump) = self._save

    def _pergame(self):
        return (self.store["data"].get("backends", {}).get("dolphin_wii", {}).get("pergame", {}))

    def test_set_res_stores_token(self):
        hh._set({"titleid": "WR5PEY", "key": "res", "value": 2})     # tokens index 2 = "2x"
        self.assertEqual(self._pergame()["WR5PEY"]["hhres"], "2x")

    def test_set_res_inherit_clears_and_prunes(self):
        self.store["data"] = {"backends": {"dolphin_wii": {"pergame": {"WR5PEY": {"hhres": "2x"}}}}}
        hh._set({"titleid": "WR5PEY", "key": "res", "value": 0})     # index 0 = inherit -> clear
        self.assertNotIn("dolphin_wii", self.store["data"].get("backends", {}))   # pruned empty tables

    def test_set_force_cc_on_and_off(self):
        hh._set({"titleid": "WR5PEY", "key": "force_cc", "value": 1})
        self.assertIs(self._pergame()["WR5PEY"]["force_cc"], True)
        hh._set({"titleid": "WR5PEY", "key": "force_cc", "value": 0})
        self.assertNotIn("dolphin_wii", self.store["data"].get("backends", {}))   # cleared + pruned

    def test_res_and_forcecc_coexist(self):
        hh._set({"titleid": "WR5PEY", "key": "res", "value": 3})       # "4x"
        hh._set({"titleid": "WR5PEY", "key": "force_cc", "value": 1})
        self.assertEqual(self._pergame()["WR5PEY"], {"hhres": "4x", "force_cc": True})

    def test_res_out_of_range_rejected(self):
        with self.assertRaises(RpcError):
            hh._set({"titleid": "WR5PEY", "key": "res", "value": 99})

    def test_unknown_key_rejected(self):
        with self.assertRaises(RpcError):
            hh._set({"titleid": "WR5PEY", "key": "bogus", "value": 1})


if __name__ == "__main__":
    unittest.main()
