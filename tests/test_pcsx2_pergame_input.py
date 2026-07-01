"""Tests for per-game PCSX2 input (pcsx2pgin.*) + its launch-time router enforcement.

Backend: RPC registration + PS2-tile section; input_get selectors (USB1/USB2/Player 2) + per-player
binds (per-game override vs the resolved global default); input_set / selector_set store + inherit-clear
+ empty-entry prune; EBUSY + bad-titleid guards; games override flag.
Router: pcsx2_cfg.set_section_type preserves other keys; switch_bind._merge_overrides;
_apply_pcsx2_pergame_ports; and a snapshot -> apply -> restore round-trip proving [USB1] reverts on exit.

Run:  python3 -m unittest tests.test_pcsx2_pergame_input -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import inifile, pcsx2_cfg, switch_bind
from lib.madsrv import pcsx2_pergame_input_cmds as pgin
from lib.madsrv import rpc, standalones_cmds

ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2")
TID = "SLUS-21665_BBE4D862"


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("pcsx2pgin.games", "pcsx2pgin.input_get", "pcsx2pgin.input_set",
                  "pcsx2pgin.selector_set"):
            self.assertIn(m, rpc._METHODS, m)

    def test_input_pergame_section_on_ps2_tile(self):
        def flat(secs):
            out = []
            for s in secs:
                if s.get("kind") == "group":
                    out.extend(flat(s.get("sections", [])))
                else:
                    out.append((s["kind"], s.get("arg")))
            return out
        kinds = flat(standalones_cmds._sections_for(ENTRY))
        # per-game input is now the Per-game group's "Input" row, which opens the C++ inputmenu
        # (Controllers + Mappings sub-chooser) for the picked game.
        self.assertIn(("input_pergame_menu", "pcsx2pgin"), kinds)


class Backend(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._st, self._gi = pgin._STORE, pgin._GLOBAL_INI
        pgin._STORE = self.d / "pergame-input.json"
        pgin._GLOBAL_INI = self.d / "PCSX2.ini"           # absent -> no global remaps -> baked defaults

    def tearDown(self):
        pgin._STORE, pgin._GLOBAL_INI = self._st, self._gi

    def _get(self, **params):
        return pgin._input_get({"titleid": TID, **params})

    def _set(self, **params):
        return pgin._input_set({"titleid": TID, **params})

    def _selset(self, **params):
        return pgin._selector_set({"titleid": TID, **params})

    def _sel(self, r, key):
        return next(s for s in r["selectors"] if s["key"] == key)

    def test_get_defaults_all_inherit(self):
        r = self._get()
        self.assertEqual([p["id"] for p in r["players"]], ["1", "2"])
        self.assertEqual({s["key"] for s in r["selectors"]}, {"usb1", "usb2", "pad2"})
        self.assertEqual(self._sel(r, "usb1")["value"], "")     # inherit
        self.assertEqual(self._sel(r, "pad2")["value"], "")
        cross = next(b for g in r["groups"] for b in g["binds"] if b["id"] == "Cross")
        self.assertEqual(cross["value"], "A / ✕")               # resolved global default

    def test_set_button_dpad_stick_trigger(self):
        self._set(id="Cross", kind="btn", value=0x130)          # BTN_SOUTH -> FaceSouth
        self._set(id="Up", kind="hat", value="h0up")            # -> DPadUp
        self._set(id="LUp", kind="axis", value="-left_y")       # L-stick up -> -LeftY
        self._set(id="L2", kind="axis", value="+trigger_left")  # analog trigger -> +LeftTrigger
        self._set(id="R2", kind="axis", value="+trigger_right")
        e = pgin.load_entry(TID)["binds"]["1"]
        self.assertEqual(e["Cross"], "FaceSouth")
        self.assertEqual(e["Up"], "DPadUp")
        self.assertEqual(e["LUp"], "-LeftY")
        self.assertEqual(e["L2"], "+LeftTrigger")
        self.assertEqual(e["R2"], "+RightTrigger")
        cross = next(b for g in self._get()["groups"] for b in g["binds"] if b["id"] == "Cross")
        self.assertEqual(cross["value"], "A / ✕")               # override shows same label here

    def test_groups_include_sticks_and_triggers(self):
        titles = [g["title"] for g in self._get()["groups"]]
        self.assertIn("Analog sticks", titles)
        self.assertIn("Triggers", titles)
        trig = next(g for g in self._get()["groups"] if g["title"] == "Triggers")
        self.assertEqual([b["id"] for b in trig["binds"]], ["L2", "R2"])
        self.assertTrue(all(b["kind"] == "axis" for b in trig["binds"]))   # analog, not digital
        # L2/R2 are NOT also under Buttons (no same-key double row)
        btns = next(g for g in self._get()["groups"] if g["title"] == "Buttons")
        self.assertFalse({"L2", "R2"} & {b["id"] for b in btns["binds"]})

    def test_per_player_binds(self):
        self._set(id="Cross", kind="btn", value=0x131, player="2")   # East on P2
        self.assertEqual(pgin.load_entry(TID)["binds"]["2"]["Cross"], "FaceEast")
        self.assertNotIn("1", pgin.load_entry(TID).get("binds", {}))

    def test_selectors_store_and_show(self):
        self._selset(key="usb1", value="None")                  # port off
        self._selset(key="pad2", value="off")
        e = pgin.load_entry(TID)
        self.assertEqual((e["usb1"], e.get("usb2"), e["pad2"]), ("None", None, False))
        r = self._get()
        self.assertEqual(self._sel(r, "usb1")["value"], "None")
        self.assertEqual(self._sel(r, "usb2")["value"], "")     # inherit
        self.assertEqual(self._sel(r, "pad2")["value"], "off")
        # USB selector is enable/disable ONLY (no bind-less device-enable option)
        self.assertEqual([o["value"] for o in self._sel(r, "usb1")["options"]], ["", "None"])

    def test_usb_rejects_device_token(self):
        with self.assertRaises(rpc.RpcError):
            self._selset(key="usb1", value="guncon2")           # enabling a device is not offered

    def test_inherit_clears_and_prunes(self):
        self._selset(key="usb1", value="None")
        self.assertIsNotNone(pgin.load_entry(TID))
        self._selset(key="usb1", value="")                      # Inherit -> remove
        self.assertIsNone(pgin.load_entry(TID))                 # entry now empty -> pruned
        self.assertNotIn(TID, pgin._load())

    def test_load_entry_ignores_empty(self):
        pgin._save({TID: {"usb1": None, "usb2": None, "pad2": None, "binds": {}}})
        self.assertIsNone(pgin.load_entry(TID))

    def test_no_running_guard_store_edits_always_work(self):
        # the store is decoupled from PCSX2's live config, so edits succeed regardless of state
        self._set(id="Cross", kind="btn", value=0x130)
        self._selset(key="usb1", value="None")
        self.assertIsNotNone(pgin.load_entry(TID))

    def test_corrupt_store_backed_up_not_wiped(self):
        pgin._STORE.parent.mkdir(parents=True, exist_ok=True)
        pgin._STORE.write_text("{ not valid json", encoding="utf-8")
        self.assertEqual(pgin._load(), {})                      # degrades to empty
        self.assertTrue(pgin._STORE.with_name(pgin._STORE.name + ".bad").exists())  # preserved for recovery

    def test_non_dict_binds_ignored_no_crash(self):
        pgin._save({TID: {"binds": "corrupt"}})
        self.assertIsNone(pgin.load_entry(TID))                 # treated as empty
        self.assertTrue(self._get()["groups"])                  # input_get must not raise

    def test_bad_titleid_and_selector(self):
        with self.assertRaises(rpc.RpcError):
            pgin._input_get({"titleid": "../x"})
        with self.assertRaises(rpc.RpcError):
            self._selset(key="bogus", value="x")
        with self.assertRaises(rpc.RpcError):
            self._selset(key="usb1", value="notadevice")

    def test_games_override_flag(self):
        pgin._save({TID: {"usb1": "None"}})
        fake = [{"key": TID, "name": "Simpsons"}, {"key": "SLES-00001_00000001", "name": "Other"}]
        with mock.patch.object(pgin.pcsx2_games, "games", lambda: fake):
            out = {g["titleid"]: g["override"] for g in pgin._games({})["games"]}
        self.assertTrue(out[TID])
        self.assertFalse(out["SLES-00001_00000001"])


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
        # the per-game pads page is reached through the Per-game "Input" row (C++ inputmenu ->
        # Controllers). The pads_get/pads_set_order RPCs it calls are registered (checked above).
        self.assertIn(("input_pergame_menu", "pcsx2pgin"), kinds)

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

    def test_pad_order_only_entry_real_but_no_input_badge(self):
        pgin._save({TID: {"pads": [XBOX, DS5, DS4]}})
        self.assertIsNotNone(pgin.load_entry(TID))            # a pad-order-only entry is not "empty"
        fake = [{"key": TID, "name": "Simpsons"}]
        with mock.patch.object(pgin.pcsx2_games, "games", lambda: fake):
            out = {g["titleid"]: g["override"] for g in pgin._games({})["games"]}
        self.assertFalse(out[TID])                            # pad order != input override -> no badge
        pgin._save({TID: {"pads": [XBOX], "usb1": "None"}})   # but a USB override DOES badge
        with mock.patch.object(pgin.pcsx2_games, "games", lambda: fake):
            out = {g["titleid"]: g["override"] for g in pgin._games({})["games"]}
        self.assertTrue(out[TID])

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
        with mock.patch.object(pgin.pcsx2_games, "path_to_key", lambda rom: TID):
            entry = switch_bind._pcsx2_pergame("pcsx2", "/roms/ps2/game.iso")
        self.assertEqual(entry["pads"], [XBOX, DS5])
        self.assertIsNone(switch_bind._pcsx2_pergame("xemu", "/x"))   # non-pcsx2 -> None


if __name__ == "__main__":
    unittest.main()
