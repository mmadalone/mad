"""Tests for the per-game PCSX2 input STORE (pcsx2pgin.*) + the profile picker pages
(pcsx2prof*/pcsx2profpg*) + the launch-time router enforcement.

The per-button editors were REPLACED by docked/handheld input-profile pickers (2026-08-04):
profiles are authored in PCSX2's own UI; MAD picks one per context (global + per-game) and the
launch binder copies its [PadN] bodies into the global ini transiently. Legacy `binds` in the
store are tolerated forever (never destroy user data) but no longer read or badged.

Run:  python3 -m unittest tests.test_pcsx2_pergame_input -v
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import inifile, pcsx2_cfg, switch_bind
from lib.madsrv import pcsx2_pergame_input_cmds as pgin
from lib.madsrv import pcsx2_profile_cmds as prof
from lib.madsrv import rpc, standalones_cmds

ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2")
TID = "SLUS-21665_BBE4D862"


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("pcsx2prof.get", "pcsx2prof.set", "pcsx2profhh.get", "pcsx2profhh.set",
                  "pcsx2profpg.games", "pcsx2profpg.get", "pcsx2profpg.set",
                  "pcsx2profpghh.games", "pcsx2profpghh.get", "pcsx2profpghh.set",
                  "pcsx2pgin.pads_get", "pcsx2pgin.pads_set_order"):
            self.assertIn(m, rpc._METHODS, m)

    def test_editor_rpcs_gone(self):
        # The per-button editors are REMOVED — none of their RPCs may resurface.
        for m in ("pcsx2.input_get", "pcsx2.input_set", "pcsx2.input_clear",
                  "pcsx2pgin.input_get", "pcsx2pgin.input_set", "pcsx2pgin.selector_set",
                  "pcsx2pgin.input_save", "pcsx2pgin.input_cancel", "pcsx2pgin.games"):
            self.assertNotIn(m, rpc._METHODS, m)

    def test_ps2_pergame_is_game_first(self):
        # STANDING RULE mad-pergame-game-first: ONE Per-game row (settings_pergame_menu) -> pick a
        # game once -> [Settings, Input->[Controllers, Input profiles]], every leaf editing the
        # picked title.
        secs = standalones_cmds._sections_for(ENTRY)
        pg = next(s for s in secs if s["label"] == "Per-game")
        self.assertEqual((pg["kind"], pg["arg"]), ("settings_pergame_menu", "pcsx2pg"))
        leaves = {l["label"]: l for l in pg["sections"]}
        self.assertEqual((leaves["Settings"]["kind"], leaves["Settings"]["arg"]),
                         ("pergame_settings", "pcsx2pg"))
        inp = {c["label"]: c for c in leaves["Input"]["sections"]}   # Input is a sub-group
        self.assertEqual((inp["Controllers"]["kind"], inp["Controllers"]["arg"]),
                         ("pergame_pads", "pcsx2pgin"))
        self.assertEqual((inp["Input profiles"]["kind"], inp["Input profiles"]["arg"]),
                         ("pergame_settings", "pcsx2profpg"))
        self.assertNotIn("Mappings", inp)

    def test_ps2_tile_input_group(self):
        # Tile = the DOCKED door: Input profiles replaces Mappings; the rest is untouched.
        secs = standalones_cmds._sections_for(ENTRY)
        grp = next(s for s in secs if s["label"] == "Input")
        rows = {r["label"]: r for r in grp["sections"]}
        self.assertEqual((rows["Input profiles"]["kind"], rows["Input profiles"]["arg"]),
                         ("settings", "pcsx2prof"))
        self.assertNotIn("Mappings", rows)
        for keep in ("Device visibility", "Pads to players", "Hotkeys"):
            self.assertIn(keep, rows)


class _FakeLocalPolicy:
    """A dict-backed localpolicy stand-in (atomic write + staterev not needed in tests)."""

    def __init__(self):
        self.data: dict = {}

    def load(self, path):
        return copy.deepcopy(self.data)

    def dump(self, path, data):
        self.data = copy.deepcopy(data)


class GlobalProfilePicker(unittest.TestCase):
    """pcsx2prof / pcsx2profhh — options list, stale-stem visibility, set/clear round-trip."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "inputprofiles").mkdir()
        for stem in ("DS4", "Steamdeck"):
            (self.d / "inputprofiles" / f"{stem}.ini").write_text("[Pad1]\nCross = SDL-0/FaceSouth\n")
        self.lp = _FakeLocalPolicy()
        self.base = {"config_file": str(self.d / "inis" / "PCSX2.ini")}
        patches = [
            mock.patch.object(prof, "localpolicy", self.lp),
            mock.patch.object(prof, "load_merged", self._merged),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _merged(self):
        be = dict(self.base)
        be.update(self.lp.data.get("backends", {}).get("pcsx2", {}))
        return {"backends": {"pcsx2": be}}

    def test_get_lists_stems_with_none_first(self):
        r = rpc._METHODS["pcsx2prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["key"], "profile_docked")
        self.assertEqual(row["options"][0], "(none — current layout)")
        self.assertEqual(row["options"][1:], ["DS4", "Steamdeck"])
        self.assertEqual(row["value"], 0)
        self.assertTrue(row["picker"])

    def test_set_and_clear_round_trip(self):
        _set = rpc._METHODS["pcsx2prof.set"][0]
        _set({"key": "profile_docked", "value": "1"})            # DS4
        self.assertEqual(self.lp.data["backends"]["pcsx2"]["profile_docked"], "DS4")
        r = rpc._METHODS["pcsx2prof.get"][0]({})
        self.assertEqual(r["groups"][0]["settings"][0]["value"], 1)
        _set({"key": "profile_docked", "value": "0"})            # (none) -> key popped
        self.assertNotIn("profile_docked",
                         self.lp.data.get("backends", {}).get("pcsx2", {}))

    def test_handheld_ns_writes_its_own_key(self):
        rpc._METHODS["pcsx2profhh.set"][0]({"key": "profile_handheld", "value": "2"})
        self.assertEqual(self.lp.data["backends"]["pcsx2"]["profile_handheld"], "Steamdeck")
        self.assertNotIn("profile_docked", self.lp.data["backends"]["pcsx2"])

    def test_stale_stem_stays_visible(self):
        self.lp.data = {"backends": {"pcsx2": {"profile_docked": "Deleted"}}}
        r = rpc._METHODS["pcsx2prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertIn("Deleted", row["options"])                 # appended, not hidden
        self.assertEqual(row["options"][row["value"]], "Deleted")

    def test_bad_key_and_index_rejected(self):
        _set = rpc._METHODS["pcsx2prof.set"][0]
        with self.assertRaises(rpc.RpcError):
            _set({"key": "bogus", "value": "1"})
        with self.assertRaises(rpc.RpcError):
            _set({"key": "profile_docked", "value": "99"})
        with self.assertRaises(rpc.RpcError):
            _set({"key": "profile_docked", "value": "x"})

    def test_set_rejects_a_stem_deleted_between_get_and_set(self):
        # REVIEW FIX: selecting the stale appended option (its file is gone) must EINVAL
        # instead of silently storing a pick the launch would then ignore.
        self.lp.data = {"backends": {"pcsx2": {"profile_docked": "Deleted"}}}
        r = rpc._METHODS["pcsx2prof.get"][0]({})
        idx = r["groups"][0]["settings"][0]["options"].index("Deleted")
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["pcsx2prof.set"][0]({"key": "profile_docked", "value": str(idx)})
        rpc._METHODS["pcsx2prof.set"][0]({"key": "profile_docked", "value": "0"})  # clearing works
        self.assertNotIn("profile_docked",
                         self.lp.data.get("backends", {}).get("pcsx2", {}))


class PergameProfilePicker(unittest.TestCase):
    """pcsx2profpg / pcsx2profpghh — per-game picks + the relocated port selectors."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "inputprofiles").mkdir()
        for stem in ("DS4", "Steamdeck"):
            (self.d / "inputprofiles" / f"{stem}.ini").write_text("[Pad1]\nCross = SDL-0/FaceSouth\n")
        self._st = pgin._STORE
        pgin._STORE = self.d / "pergame-input.json"
        self.lp = _FakeLocalPolicy()
        self.base = {"config_file": str(self.d / "inis" / "PCSX2.ini"),
                     "profile_docked": "DS4"}
        for p in (mock.patch.object(prof, "localpolicy", self.lp),
                  mock.patch.object(prof, "load_merged",
                                    lambda: {"backends": {"pcsx2": dict(self.base)}})):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        pgin._STORE = self._st

    def test_get_shows_inherit_with_global_stem(self):
        r = rpc._METHODS["pcsx2profpg.get"][0]({"titleid": TID})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["options"][0], "(inherit global: DS4)")
        self.assertEqual(row["value"], 0)
        ports = {s["key"]: s for s in r["groups"][1]["settings"]}
        self.assertEqual(set(ports), {"usb1", "usb2", "pad2"})

    def test_handheld_page_has_no_ports_group(self):
        r = rpc._METHODS["pcsx2profpghh.get"][0]({"titleid": TID})
        self.assertEqual(len(r["groups"]), 1)
        self.assertEqual(r["groups"][0]["settings"][0]["label"], "Handheld profile")

    def test_set_profile_and_clear_prunes(self):
        _set = rpc._METHODS["pcsx2profpg.set"][0]
        _set({"titleid": TID, "key": "profile", "value": "1"})   # DS4 (idx 1 = first stem)
        self.assertEqual(pgin.load_entry(TID)["profiles"]["docked"], "DS4")
        _set({"titleid": TID, "key": "profile", "value": "0"})   # inherit -> pruned
        self.assertIsNone(pgin.load_entry(TID))
        self.assertNotIn(TID, pgin._load())

    def test_contexts_are_independent(self):
        rpc._METHODS["pcsx2profpg.set"][0]({"titleid": TID, "key": "profile", "value": "1"})
        rpc._METHODS["pcsx2profpghh.set"][0]({"titleid": TID, "key": "profile", "value": "2"})
        e = pgin.load_entry(TID)
        self.assertEqual(e["profiles"], {"docked": "DS4", "handheld": "Steamdeck"})
        rpc._METHODS["pcsx2profpg.set"][0]({"titleid": TID, "key": "profile", "value": "0"})
        self.assertEqual(pgin.load_entry(TID)["profiles"], {"handheld": "Steamdeck"})

    def test_port_selectors_store_same_values_as_before(self):
        _set = rpc._METHODS["pcsx2profpg.set"][0]
        _set({"titleid": TID, "key": "usb1", "value": "1"})      # None (port off)
        _set({"titleid": TID, "key": "pad2", "value": "2"})      # Off
        e = pgin.load_entry(TID)
        self.assertEqual((e["usb1"], e.get("usb2"), e["pad2"]), ("None", None, False))
        _set({"titleid": TID, "key": "usb1", "value": "0"})      # inherit -> pop
        _set({"titleid": TID, "key": "pad2", "value": "0"})
        self.assertIsNone(pgin.load_entry(TID))

    def test_handheld_ns_rejects_port_keys(self):
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["pcsx2profpghh.set"][0]({"titleid": TID, "key": "usb1", "value": "1"})

    def test_legacy_binds_survive_profile_edits(self):
        pgin._save({TID: {"binds": {"docked": {"1": {"Cross": "FaceWest"}}}}})
        rpc._METHODS["pcsx2profpg.set"][0]({"titleid": TID, "key": "profile", "value": "1"})
        e = pgin._load()[TID]
        self.assertEqual(e["binds"], {"docked": {"1": {"Cross": "FaceWest"}}})   # untouched
        rpc._METHODS["pcsx2profpg.set"][0]({"titleid": TID, "key": "profile", "value": "0"})
        self.assertIn(TID, pgin._load())            # binds-only entry NOT pruned (never destroy data)

    def test_games_badge_profiles_not_legacy_binds(self):
        pgin._save({TID: {"profiles": {"docked": "DS4"}},
                    "SLES-00001_00000001": {"binds": {"docked": {"1": {"Cross": "FaceWest"}}}}})
        fake = [{"key": TID, "name": "Simpsons"},
                {"key": "SLES-00001_00000001", "name": "Other"}]
        with mock.patch.object(prof.pcsx2_games, "games", lambda: fake):
            out = {g["titleid"]: g["override"] for g in prof._games_payload()["games"]}
        self.assertTrue(out[TID])                                # profile pick badges
        self.assertFalse(out["SLES-00001_00000001"])             # legacy binds are inert -> no badge

    def test_bad_titleid_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["pcsx2profpg.get"][0]({"titleid": "../x"})


class StoreHygiene(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st = pgin._STORE
        pgin._STORE = self.d / "pergame-input.json"

    def tearDown(self):
        pgin._STORE = self._st

    def test_load_entry_ignores_empty(self):
        pgin._save({TID: {"usb1": None, "usb2": None, "pad2": None, "binds": {}}})
        self.assertIsNone(pgin.load_entry(TID))

    def test_profiles_count_as_content(self):
        pgin._save({TID: {"profiles": {"docked": "DS4"}}})
        self.assertIsNotNone(pgin.load_entry(TID))
        self.assertFalse(pgin._is_empty({"profiles": {"docked": "DS4"}}))
        self.assertTrue(pgin._is_empty({"profiles": {}}))
        self.assertTrue(pgin._is_empty({"profiles": "junk"}))    # husk reads as absent

    def test_legacy_binds_count_in_is_empty_but_not_badge(self):
        e = {"binds": {"docked": {"1": {"Cross": "FaceWest"}}}}
        self.assertFalse(pgin._is_empty(e))                      # never auto-pruned
        self.assertFalse(pgin._has_input_override(e))            # but inert -> no badge

    def test_normalize_migrates_flat_binds_and_heals_profiles_husk(self):
        e = {"binds": {"1": {"Cross": "FaceWest"}}, "profiles": "junk"}
        pgin._normalize_entry(e)
        self.assertEqual(e["binds"], {"docked": {"1": {"Cross": "FaceWest"}}})
        self.assertNotIn("profiles", e)

    def test_corrupt_store_backed_up_not_wiped(self):
        pgin._STORE.parent.mkdir(parents=True, exist_ok=True)
        pgin._STORE.write_text("{ not valid json", encoding="utf-8")
        self.assertEqual(pgin._load(), {})                      # degrades to empty
        self.assertTrue(pgin._STORE.with_name(pgin._STORE.name + ".bad").exists())


class Router(unittest.TestCase):
    def test_set_section_type_preserves_other_keys(self):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text("[USB1]\nType = guncon2\nguncon2_Trigger = Pointer-0/LeftButton\n"
                       "\n[EmuCore]\nx = 1\n", encoding="utf-8")
        self.assertTrue(pcsx2_cfg.set_section_type(ini, "USB1", "None"))
        body = inifile.section_body(ini.read_text(), "USB1")
        self.assertIn("Type = None", body)
        self.assertIn("guncon2_Trigger = Pointer-0/LeftButton", body)      # binds untouched
        self.assertFalse(pcsx2_cfg.set_section_type(ini, "USB1", "None"))  # idempotent no-op

    def test_merge_overrides(self):
        # _merge_overrides survives for RPCS3's per-button path (PS2 no longer uses it).
        merged = switch_bind._merge_overrides({1: {"Cross": "FaceSouth"}},
                                              {"1": {"Circle": "FaceEast"}, "2": {"Cross": "FaceWest"}})
        self.assertEqual(merged[1], {"Cross": "FaceSouth", "Circle": "FaceEast"})
        self.assertEqual(merged[2], {"Cross": "FaceWest"})

    def test_merge_overrides_skips_non_dict(self):
        merged = switch_bind._merge_overrides({}, {"1": {"Cross": "FaceSouth"}, "2": "corrupt"})
        self.assertEqual(merged, {1: {"Cross": "FaceSouth"}})   # non-dict player value skipped, no raise

    def test_pad2_off_targets_multitap_slot(self):
        self.assertEqual(switch_bind._pcsx2_p2_section(2), "Pad2")   # 2-pad: Player 2 = Pad2
        self.assertEqual(switch_bind._pcsx2_p2_section(4), "Pad3")   # multitap: Player 2 = Pad3

    def _seed(self, body):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text(body, encoding="utf-8")
        side = switch_bind._sidecar(ini)
        side.write_text(json.dumps({"emu": "pcsx2", "input": switch_bind._snapshot("pcsx2", ini)}))
        return ini, side

    def test_apply_pergame_ports(self):
        ini, side = self._seed("[Pad1]\nType = DualShock2\n\n[Pad2]\nType = DualShock2\n"
                               "\n[USB1]\nType = guncon2\n\n[USB2]\nType = None\n")
        switch_bind._apply_pcsx2_pergame_ports(ini, {"usb1": "None", "pad2": False}, side, 2)
        text = ini.read_text()
        self.assertIn("Type = None", inifile.section_body(text, "USB1"))    # port disabled
        self.assertIn("Type = None", inifile.section_body(text, "Pad2"))    # Player 2 off (2-pad -> Pad2)
        self.assertEqual((inifile.section_body(text, "USB2") or "").strip(), "Type = None")  # inherit, untouched
        # USB1 is NOT in the base snapshot (lazy) -> it must now be recorded so restore reverts it
        snap = json.loads(side.read_text())["input"]
        self.assertIn("USB1", snap)
        self.assertIn("Type = guncon2", snap["USB1"])                        # pre-write body captured

    def test_no_override_launch_leaves_usb_alone(self):
        # base snapshot must NOT include USB sections (lazy) so a normal launch never reverts USB
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text("[Pad1]\nType = DualShock2\n\n[USB1]\nType = guncon2\n", encoding="utf-8")
        self.assertNotIn("USB1", switch_bind._snapshot("pcsx2", ini))
        self.assertNotIn("USB2", switch_bind._snapshot("pcsx2", ini))

    def test_apply_record_then_restore_reverts_usb(self):
        # lazy-record path: USB1 not in base snapshot, apply records + flips it, restore reverts it
        ini, side = self._seed("[Pad1]\nType = DualShock2\n\n[USB1]\nType = guncon2\n\n[USB2]\nType = None\n")
        switch_bind._apply_pcsx2_pergame_ports(ini, {"usb1": "None"}, side, 0)   # 0 pads (lightgun case)
        self.assertIn("Type = None", inifile.section_body(ini.read_text(), "USB1"))
        switch_bind.restore_target(ini)
        self.assertIn("Type = guncon2", inifile.section_body(ini.read_text(), "USB1"))  # reverted
        self.assertFalse(side.exists())


DS5, DS4, XBOX = "054c:0ce6", "054c:09cc", "045e:02a1"
_UNIVERSE = [DS5, DS4, XBOX]     # fake global display order for the pad universe


class _FakePad:
    def __init__(self, index, vidpid, name):
        self.index, self.vidpid, self.name = index, vidpid, name


class PergamePads(unittest.TestCase):
    """Per-game pad -> player order (pcsx2pgin.pads_get / .pads_set_order): the reorder store,
    row ordering, inherit-drop, and that a pad-order-only entry does NOT badge the input picker."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st = pgin._STORE
        pgin._STORE = self.d / "pergame-input.json"
        conn = [_FakePad(0, DS5, "DualSense"), _FakePad(1, DS4, "DualShock 4")]  # Xbox NOT connected
        self._saved = {n: getattr(pgin.pads_cmds, n)
                       for n in ("_real_pads", "_pad_labels", "_type_universe", "managed_players")}
        pgin.pads_cmds._real_pads = lambda pump=True: list(conn)
        pgin.pads_cmds._pad_labels = lambda real: {
            d.index: pgin.mad_config.KNOWN_PADS.get(d.vidpid, d.vidpid) for d in real}
        pgin.pads_cmds._type_universe = lambda emu, connected_vps=(): list(_UNIVERSE)
        pgin.pads_cmds.managed_players = lambda emu: 2

    def tearDown(self):
        pgin._STORE = self._st
        for n, fn in self._saved.items():
            setattr(pgin.pads_cmds, n, fn)

    def test_rpcs_and_section_registered(self):
        for m in ("pcsx2pgin.pads_get", "pcsx2pgin.pads_set_order"):
            self.assertIn(m, rpc._METHODS, m)
        def flat(secs):
            out = []
            for s in secs:
                if s.get("kind") == "group":
                    out.extend(flat(s.get("sections", [])))
                else:
                    out.append((s["kind"], s.get("arg")))
            return out
        kinds = flat(standalones_cmds._sections_for(ENTRY))
        # the per-game pads page is reached through the game-first Per-game menu (pick a game ->
        # Input -> Controllers). The pads_get/pads_set_order RPCs it calls are registered (checked above).
        self.assertIn(("settings_pergame_menu", "pcsx2pg"), kinds)

    def test_get_default_is_global_order_with_connected_flags(self):
        r = pgin._pads_get({"titleid": TID})
        self.assertEqual([row["id"] for row in r["pads"]], _UNIVERSE)   # nothing stored -> global order
        self.assertEqual(r["players"], 2)
        conn = {row["id"]: row["connected"] for row in r["pads"]}
        self.assertTrue(conn[DS5] and conn[DS4])
        self.assertFalse(conn[XBOX])
        self.assertIn("●", next(row["label"] for row in r["pads"] if row["id"] == DS5))

    def test_set_order_stores_and_reorders_get(self):
        pgin._pads_set_order({"titleid": TID, "order": [XBOX, DS5, DS4]})
        self.assertEqual(pgin.load_entry(TID)["pads"], [XBOX, DS5, DS4])
        r = pgin._pads_get({"titleid": TID})
        self.assertEqual([row["id"] for row in r["pads"]], [XBOX, DS5, DS4])   # per-game order first

    def test_partial_order_keeps_rest_global(self):
        pgin._save({TID: {"pads": [XBOX]}})                                # only Xbox pinned
        r = pgin._pads_get({"titleid": TID})
        self.assertEqual([row["id"] for row in r["pads"]], [XBOX, DS5, DS4])  # rest keep global order

    def test_set_matching_global_order_clears(self):
        pgin._pads_set_order({"titleid": TID, "order": [XBOX, DS5, DS4]})
        self.assertIsNotNone(pgin.load_entry(TID))
        pgin._pads_set_order({"titleid": TID, "order": list(_UNIVERSE)})    # dragged back to global
        self.assertIsNone(pgin.load_entry(TID))                            # inherit -> dropped
        self.assertNotIn(TID, pgin._load())

    def test_empty_order_clears(self):
        pgin._pads_set_order({"titleid": TID, "order": [XBOX, DS5, DS4]})
        pgin._pads_set_order({"titleid": TID, "order": []})
        self.assertIsNone(pgin.load_entry(TID))

    def test_profiles_only_entry_survives_pads_round_trip(self):
        # A profiles-only entry must never be pruned by a pads_set_order that stores nothing.
        pgin._save({TID: {"profiles": {"docked": "DS4"}}})
        pgin._pads_set_order({"titleid": TID, "order": list(_UNIVERSE)})   # == global -> no pads stored
        self.assertEqual(pgin.load_entry(TID)["profiles"], {"docked": "DS4"})

    def test_is_empty_accounts_for_pads(self):
        self.assertTrue(pgin._is_empty({"pads": []}))
        self.assertFalse(pgin._is_empty({"pads": [XBOX]}))

    def test_disconnected_pinned_unknown_class_stays_visible(self):
        # Regression (adversarial review): an exotic pad (not in KNOWN_PADS) pinned for a game must
        # stay a row while unplugged, so a re-Apply (sends only shown rows) can't silently drop it.
        EXOTIC = "1234:5678"                                     # not connected, not in _UNIVERSE
        pgin._save({TID: {"pads": [EXOTIC, DS5, DS4]}})
        ids = [row["id"] for row in pgin._pads_get({"titleid": TID})["pads"]]
        self.assertEqual(ids[0], EXOTIC)                         # pinned Player 1, still shown
        row = next(x for x in pgin._pads_get({"titleid": TID})["pads"] if x["id"] == EXOTIC)
        self.assertFalse(row["connected"])                       # shown as disconnected
        pgin._pads_set_order({"titleid": TID, "order": ids})     # re-Apply the shown order
        self.assertEqual(pgin.load_entry(TID)["pads"][0], EXOTIC)  # pin survives (not inherit-dropped)

    def test_excluded_class_never_appended_as_row(self):
        pgin._save({TID: {"pads": ["28de:1205", DS5, DS4]}})    # Steam Deck = never pinnable
        ids = [row["id"] for row in pgin._pads_get({"titleid": TID})["pads"]]
        self.assertNotIn("28de:1205", ids)

    def test_launch_lookup_returns_pad_order(self):
        pgin._save({TID: {"pads": [XBOX, DS5]}})
        with mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda rom: TID):
            entry = switch_bind._pcsx2_pergame("pcsx2", "/roms/ps2/game.iso")
        self.assertEqual(entry["pads"], [XBOX, DS5])
        self.assertIsNone(switch_bind._pcsx2_pergame("xemu", "/x"))   # non-pcsx2 -> None


if __name__ == "__main__":
    unittest.main()
