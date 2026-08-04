"""Tests for the Dolphin per-style, per-player profile picker pages (dolphin_profile_cmds).

Pages are registered via closures, so calls go through the rpc registry. Policy reads are
served from a dict (load_merged patched); writes are captured (never the real policy file).

Run:  python3 -m unittest tests.test_dolphin_profile_cmds -v
"""
from __future__ import annotations

import unittest

from lib import dolphin_profiles, dolphin_wii_profiles, dolphin_wii_source, dolphin_wii_tdb
from lib.madsrv import dolphin_profile_cmds as dpc
from lib.madsrv import rpc
from lib.madsrv.rpc import RpcError

_TID = "SMNE01"


_DECK_DEFAULT = "Steamdeck = classic controller"       # dolphin_wii_profiles.HANDHELD_DEFAULT


def _fake_wii_list(extension="Classic", include_sinden=False):
    if extension == "Classic":
        return ["CC1", _DECK_DEFAULT]
    out = ["CC1", "NunA", "SideB"]
    if include_sinden:
        out.append("Sinden Lightgun P1")
    return out


def _fake_wii_body(name):
    return None if name.startswith("Ghost") else f"Device = SDL/0/{name}\nB = X\n"


class _PageHarness(unittest.TestCase):
    def setUp(self):
        self.policy: dict = {"backends": {}}
        self.be_calls: list = []
        self.pg_calls: list = []
        self._save = (dpc.load_merged, dpc._set_backend_key, dpc._store_pergame,
                      dolphin_wii_profiles.list_profiles, dolphin_wii_profiles.profile_body,
                      dolphin_profiles.list_profiles, dolphin_profiles.profile_body,
                      dolphin_wii_source.style, dolphin_wii_tdb.has_input_data)
        dpc.load_merged = lambda: self.policy
        dpc._set_backend_key = lambda b, k, v: self.be_calls.append((b, k, v))
        dpc._store_pergame = lambda b, t, k, v: self.pg_calls.append((b, t, k, v))
        dolphin_wii_profiles.list_profiles = _fake_wii_list
        dolphin_wii_profiles.profile_body = _fake_wii_body
        dolphin_profiles.list_profiles = lambda: ["GCa", "GCb"]
        dolphin_profiles.profile_body = _fake_wii_body
        dolphin_wii_source.style = lambda tid: "sideways"
        dolphin_wii_tdb.has_input_data = lambda tid: True

    def tearDown(self):
        (dpc.load_merged, dpc._set_backend_key, dpc._store_pergame,
         dolphin_wii_profiles.list_profiles, dolphin_wii_profiles.profile_body,
         dolphin_profiles.list_profiles, dolphin_profiles.profile_body,
         dolphin_wii_source.style, dolphin_wii_tdb.has_input_data) = self._save

    def get(self, ns, **params):
        return rpc._METHODS[f"{ns}.get"][0](params)

    def set(self, ns, key, value, **params):
        params.update({"key": key, "value": value})
        return rpc._METHODS[f"{ns}.set"][0](params)

    def rows(self, ns, **params):
        return self.get(ns, **params)["groups"][0]["settings"]


class StylePages(_PageHarness):
    def test_dock_sideways_rows_keys_and_labels(self):
        rows = self.rows("dolphin_wii_dock_sideways")
        self.assertEqual([r["key"] for r in rows],
                         ["docked_profile_sideways", "docked_profile_sideways_p2",
                          "docked_profile_sideways_p3", "docked_profile_sideways_p4"])
        self.assertEqual([r["label"] for r in rows],
                         ["Player 1", "Player 2", "Player 3", "Player 4"])
        self.assertTrue(all(r["picker"] for r in rows))

    def test_sinden_only_on_player1(self):
        rows = self.rows("dolphin_wii_dock_nunchuk")
        self.assertIn("Sinden Lightgun P1", rows[0]["options"])
        for r in rows[1:]:
            self.assertNotIn("Sinden Lightgun P1", r["options"])

    def test_hh_classic_default_label_and_cc_list(self):
        rows = self.rows("dolphin_wii_hh_classic")
        self.assertEqual(rows[0]["options"][0], dpc._CC_DEFAULT_LABEL)
        self.assertEqual(rows[0]["options"][1:], ["CC1", _DECK_DEFAULT])   # Classic-only list
        self.assertEqual(rows[1]["key"], "undocked_profile_p2")
        self.assertEqual(rows[1]["options"], ["(none)", "CC1", _DECK_DEFAULT])

    def test_hh_classic_rejects_the_implicit_p1_default_on_other_rows(self):
        # Player 1 UNSET still resolves to the built-in Deck default at launch; picking that
        # same stem on Player 2 would validate here and then be dup-dropped by the rail,
        # leaving a pick that can never seat (review fix: the implicit map covers it).
        idx = self.rows("dolphin_wii_hh_classic")[1]["options"].index(_DECK_DEFAULT)
        with self.assertRaises(RpcError):
            self.set("dolphin_wii_hh_classic", "undocked_profile_p2", idx)
        # ...but once Player 1 is EXPLICITLY something else, the default stem is free
        self.policy = {"backends": {"dolphin_wii": {"undocked_profile": "CC1"}}}
        self.set("dolphin_wii_hh_classic", "undocked_profile_p2", idx)
        self.assertEqual(self.be_calls, [("dolphin_wii", "undocked_profile_p2", _DECK_DEFAULT)])

    def test_gc_player_profiles_page(self):
        rows = self.rows("dolphin_gc_hh_profiles")
        self.assertEqual([r["key"] for r in rows],
                         ["undocked_profile", "undocked_profile_p2",
                          "undocked_profile_p3", "undocked_profile_p4"])
        self.assertEqual(rows[0]["options"], ["(none)", "GCa", "GCb"])

    def test_set_writes_the_legacy_p1_key(self):
        # P1 options: ["(none)", "CC1", "NunA", "SideB", "Sinden Lightgun P1"]
        self.set("dolphin_wii_dock_sideways", "docked_profile_sideways", 2)
        self.assertEqual(self.be_calls, [("dolphin_wii", "docked_profile_sideways", "NunA")])

    def test_set_zero_clears(self):
        self.set("dolphin_wii_dock_sideways", "docked_profile_sideways_p2", 0)
        self.assertEqual(self.be_calls, [("dolphin_wii", "docked_profile_sideways_p2", None)])

    def test_duplicate_on_the_same_page_rejected(self):
        self.policy = {"backends": {"dolphin_wii": {"docked_profile_sideways": "NunA"}}}
        with self.assertRaises(RpcError):
            # P2 options: ["(none)", "CC1", "NunA", "SideB"] -> index 2 = NunA (already P1)
            self.set("dolphin_wii_dock_sideways", "docked_profile_sideways_p2", 2)
        self.assertEqual(self.be_calls, [])

    def test_same_profile_on_another_page_is_fine(self):
        self.policy = {"backends": {"dolphin_wii": {"docked_profile_nunchuk": "NunA"}}}
        self.set("dolphin_wii_dock_sideways", "docked_profile_sideways_p2", 2)
        self.assertEqual(self.be_calls,
                         [("dolphin_wii", "docked_profile_sideways_p2", "NunA")])

    def test_stale_stored_stem_visible_but_not_settable(self):
        self.policy = {"backends": {"dolphin_wii": {"docked_profile_sideways_p2": "GhostP"}}}
        rows = self.rows("dolphin_wii_dock_sideways")
        self.assertIn("GhostP", rows[1]["options"])          # visible for clearing
        idx = rows[1]["options"].index("GhostP")
        with self.assertRaises(RpcError):
            self.set("dolphin_wii_dock_sideways", "docked_profile_sideways_p2", idx)

    def test_unknown_key_rejected(self):
        with self.assertRaises(RpcError):
            self.set("dolphin_wii_dock_sideways", "docked_profile_other", 1)

    def test_retired_namespaces_are_gone(self):
        for ns in ("dolphin_wii_dock", "dolphin_wii_dock_hh", "dolphin_gc_dock"):
            self.assertNotIn(f"{ns}.get", rpc._METHODS)
            self.assertNotIn(f"{ns}.set", rpc._METHODS)


class PergamePages(_PageHarness):
    def test_wii_pergame_rows(self):
        rows = self.rows("dolphin_wii_pg_profiles", titleid=_TID)
        self.assertEqual([r["key"] for r in rows],
                         ["docked_profile", "docked_profile_p2",
                          "docked_profile_p3", "docked_profile_p4"])
        self.assertEqual(rows[0]["options"][0], "(inherit global)")
        self.assertIn("Sinden Lightgun P1", rows[0]["options"])
        self.assertNotIn("Sinden Lightgun P1", rows[1]["options"])

    def test_gc_pergame_pages_rows(self):
        for ns, base in (("dolphin_gc_pg_profiles", "docked_profile"),
                         ("dolphin_gc_hh", "handheld_profile")):
            rows = self.rows(ns, titleid=_TID)
            self.assertEqual([r["key"] for r in rows],
                             [base, f"{base}_p2", f"{base}_p3", f"{base}_p4"])
            self.assertEqual(rows[0]["options"], ["(inherit global)", "GCa", "GCb"])

    def test_pergame_set_and_clear(self):
        self.set("dolphin_wii_pg_profiles", "docked_profile_p2", 2, titleid=_TID)
        self.set("dolphin_wii_pg_profiles", "docked_profile_p2", 0, titleid=_TID)
        self.assertEqual(self.pg_calls,
                         [("dolphin_wii", _TID, "docked_profile_p2", "NunA"),
                          ("dolphin_wii", _TID, "docked_profile_p2", None)])

    def test_pergame_duplicate_within_the_game_rejected(self):
        self.policy = {"backends": {"dolphin_wii": {
            "pergame": {_TID: {"docked_profile": "NunA"}}}}}
        with self.assertRaises(RpcError):
            self.set("dolphin_wii_pg_profiles", "docked_profile_p2", 2, titleid=_TID)
        # ...but the SAME stem on a DIFFERENT game is fine
        self.set("dolphin_wii_pg_profiles", "docked_profile_p2", 2, titleid="RMCE01")
        self.assertEqual(self.pg_calls,
                         [("dolphin_wii", "RMCE01", "docked_profile_p2", "NunA")])

    def test_wii_note_distinguishes_unknown_from_pointer(self):
        dolphin_wii_source.style = lambda tid: "other"
        dolphin_wii_tdb.has_input_data = lambda tid: True
        note = self.get("dolphin_wii_pg_profiles", titleid=_TID)["note"]
        self.assertIn("pointer or motion", note)
        dolphin_wii_tdb.has_input_data = lambda tid: False
        note = self.get("dolphin_wii_pg_profiles", titleid=_TID)["note"]
        self.assertIn("not in GameTDB", note)

    def test_wii_style_label_delegates_to_the_launch_ladder(self):
        for token, label in (("cc", "Classic controller"), ("sideways", "Sideways (curated)"),
                             ("nunchuk", "Nunchuk (detected)"), ("other", "Pointer/other")):
            dolphin_wii_source.style = lambda tid, _t=token: _t
            self.assertEqual(dpc.wii_style(_TID), label)


class PolicyWriters(unittest.TestCase):
    """The REAL _set_backend_key/_store_pergame writers (the page harness stubs them):
    pruning cascades + every write going through localpolicy.dump (the staterev bump)."""

    def setUp(self):
        self._save = (dpc.localpolicy.load, dpc.localpolicy.dump)
        self.store = {"data": {}}
        self.dumps = []
        dpc.localpolicy.load = lambda p: self.store["data"]
        dpc.localpolicy.dump = lambda p, d: (self.store.__setitem__("data", d),
                                             self.dumps.append(p))

    def tearDown(self):
        (dpc.localpolicy.load, dpc.localpolicy.dump) = self._save

    def test_backend_key_set_and_prune(self):
        dpc._set_backend_key("dolphin_wii", "docked_profile_sideways_p2", "ProfX")
        self.assertEqual(self.store["data"]["backends"]["dolphin_wii"],
                         {"docked_profile_sideways_p2": "ProfX"})
        dpc._set_backend_key("dolphin_wii", "docked_profile_sideways_p2", None)
        self.assertNotIn("dolphin_wii", self.store["data"].get("backends", {}))   # pruned
        self.assertEqual(len(self.dumps), 2)                # EVERY write went through dump

    def test_pergame_set_and_prune_cascade(self):
        dpc._store_pergame("dolphin_gc", "GALE01", "handheld_profile_p3", "GCa")
        self.assertEqual(self.store["data"]["backends"]["dolphin_gc"]["pergame"]["GALE01"],
                         {"handheld_profile_p3": "GCa"})
        dpc._store_pergame("dolphin_gc", "GALE01", "handheld_profile_p3", None)
        self.assertNotIn("dolphin_gc", self.store["data"].get("backends", {}))    # full cascade
        self.assertEqual(len(self.dumps), 2)

    def test_pergame_clear_keeps_siblings(self):
        self.store["data"] = {"backends": {"dolphin_gc": {"pergame": {
            "GALE01": {"docked_profile": "GCa", "docked_profile_p2": "GCb"}}}}}
        dpc._store_pergame("dolphin_gc", "GALE01", "docked_profile_p2", None)
        self.assertEqual(self.store["data"]["backends"]["dolphin_gc"]["pergame"]["GALE01"],
                         {"docked_profile": "GCa"})


if __name__ == "__main__":
    unittest.main()
