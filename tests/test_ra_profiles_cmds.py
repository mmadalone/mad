"""P3.2 -- the raprof.* backend: direct-write CRUD + the buffered detail.

LOCAL is redirected to a temp file so these NEVER touch the live controller-policy.local.toml.
POLICY stays real, so the 5 shipped profiles (Gamepad/Deck/...) are present in the merged view.
"""
from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from lib import policy, ra_profiles
from lib.madsrv import ra_profiles_cmds as cmds


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = Path(tmp.name) / "controller-policy.local.toml"
        for target in (policy, cmds):
            p = mock.patch.object(target, "LOCAL", self.local)
            p.start()
            self.addCleanup(p.stop)
        cmds._buf.reset()
        self.addCleanup(cmds._buf.reset)

    def local_toml(self):
        if not self.local.is_file():
            return {}
        with self.local.open("rb") as f:
            return tomllib.load(f)

    def idx(self, token):
        return cmds._TOKEN_ORDER.index(token)


class ListCreate(_Base):
    def test_list_flags_shipped_vs_user(self):
        cmds._create({"name": "MyPad"})
        by_name = {p["name"]: p for p in cmds._list({})["profiles"]}
        self.assertTrue(by_name["Gamepad"]["shipped"])
        self.assertFalse(by_name["Gamepad"]["shadowed"])
        self.assertFalse(by_name["MyPad"]["shipped"])
        self.assertTrue(by_name["MyPad"]["shadowed"])

    def test_create_appears_in_merged_empty(self):
        out = cmds._create({"name": "MyPad"})
        self.assertEqual(out["created"], "MyPad")
        hk = out["merged"]["ra_profiles"]["MyPad"]["hotkeys"]
        self.assertTrue(all(v == "" for v in hk.values()))

    def test_create_rejects_duplicate_shipped(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            cmds._create({"name": "Gamepad"})

    def test_create_rejects_blank(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            cmds._create({"name": "   "})

    def test_list_wires_the_profile_and_new_profile_icons(self):
        from lib.madsrv import systems_cmds
        with mock.patch.object(systems_cmds, "resolve_art",
                               lambda cands: "/theme/" + cands[0]):
            out = cmds._list({})
        by_name = {p["name"]: p for p in out["profiles"]}
        self.assertEqual(by_name["Gamepad"]["art"], ["/theme/icons/gamepad.png"])
        self.assertEqual(by_name["Arcade"]["art"], ["/theme/icons/arcade.png"])   # by lower-cased name
        self.assertEqual(out["new_art"], ["/theme/icons/new-profile.png"])


class DetailPayload(_Base):
    def test_get_shape_and_current_values(self):
        out = cmds._get({"profile": "Gamepad"})
        self.assertTrue(out["buffered"])
        titles = [g["title"] for g in out["groups"]]
        self.assertEqual(titles, ["Used by", "Hotkeys", "Gameplay", "Lightgun", "Options", ""])  # non-Deck
        settings = {s["key"]: s for g in out["groups"] for s in g["settings"]}
        # Gamepad modifier = l3, slowmotion = r (bumper), quit = "" (unbound)
        self.assertEqual(settings["hotkey:modifier"]["value"], self.idx("l3"))
        self.assertEqual(settings["hotkey:slowmotion"]["value"], self.idx("r"))
        self.assertEqual(settings["hotkey:quit"]["value"], self.idx(""))
        # DualSense is seeded onto Gamepad -> its switch is ON
        self.assertTrue(settings["family:DualSense"]["value"])
        self.assertFalse(settings["family:8BitDo"]["value"])   # 8BitDo -> Retro, not Gamepad

    def test_shipped_offers_reset_user_offers_delete(self):
        actions = {s["key"] for g in cmds._get({"profile": "Gamepad"})["groups"]
                   for s in g["settings"] if s.get("type") == "action"}
        self.assertEqual(actions, {"reset"})           # Gamepad is shipped
        cmds._create({"name": "MyPad"})
        actions = {s["key"] for g in cmds._get({"profile": "MyPad"})["groups"]
                   for s in g["settings"] if s.get("type") == "action"}
        self.assertEqual(actions, {"delete"})          # user-made

    def test_unknown_profile_is_enoent(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            cmds._get({"profile": "Nope"})


class EditHotkey(_Base):
    def test_set_stages_then_save_writes_local(self):
        cmds._create({"name": "MyPad"})
        d = cmds._set({"profile": "MyPad", "key": "hotkey:modifier", "value": str(self.idx("l3"))})
        self.assertTrue(d["dirty"])
        self.assertTrue(cmds._save({"profile": "MyPad"})["saved"])
        self.assertEqual(self.local_toml()["ra_profiles"]["MyPad"]["hotkeys"]["modifier"], "l3")

    def test_editing_shipped_shadows_one_key_only(self):
        cmds._set({"profile": "Gamepad", "key": "hotkey:slowmotion", "value": str(self.idx("l2"))})
        cmds._save({"profile": "Gamepad"})
        # local carries ONLY the changed key; merged keeps the rest from base
        self.assertEqual(self.local_toml()["ra_profiles"]["Gamepad"]["hotkeys"], {"slowmotion": "l2"})
        gm = policy.load_merged()["ra_profiles"]["Gamepad"]["hotkeys"]
        self.assertEqual(gm["slowmotion"], "l2")
        self.assertEqual(gm["rewind"], "l2")     # untouched, from base

    def test_cancel_discards_staged_edits(self):
        cmds._create({"name": "MyPad"})
        cmds._set({"profile": "MyPad", "key": "hotkey:modifier", "value": str(self.idx("l3"))})
        cmds._cancel({"profile": "MyPad"})
        out = cmds._get({"profile": "MyPad"})
        settings = {s["key"]: s for g in out["groups"] for s in g["settings"]}
        self.assertEqual(settings["hotkey:modifier"]["value"], self.idx(""))    # back to unbound
        # create() already wrote the empty MyPad; cancel only drops the STAGED edit, so the l3
        # was never persisted -- modifier stays "" in local.
        self.assertEqual(self.local_toml()["ra_profiles"]["MyPad"]["hotkeys"]["modifier"], "")


class Families(_Base):
    def test_assign_a_family_by_toggle(self):
        cmds._set({"profile": "Gamepad", "key": "family:8BitDo", "value": "1"})
        cmds._save({"profile": "Gamepad"})
        self.assertEqual(self.local_toml()["ra_profile_map"]["8BitDo"], "Gamepad")

    def test_unassign_writes_empty(self):
        cmds._set({"profile": "Gamepad", "key": "family:DualSense", "value": "0"})
        cmds._save({"profile": "Gamepad"})
        self.assertEqual(self.local_toml()["ra_profile_map"]["DualSense"], "")
        self.assertIsNone(ra_profiles.profile_name_for(policy.load_merged(), "DualSense"))


class DeleteReset(_Base):
    def test_delete_user_profile(self):
        cmds._create({"name": "MyPad"})
        cmds._delete({"profile": "MyPad"})
        self.assertNotIn("MyPad", self.local_toml().get("ra_profiles", {}))
        self.assertNotIn("MyPad", ra_profiles.list_profiles(policy.load_merged()))

    def test_delete_refuses_a_shipped_profile(self):
        from lib.madsrv.rpc import RpcError
        with self.assertRaises(RpcError):
            cmds._delete({"profile": "Gamepad"})

    def test_reset_drops_the_local_shadow(self):
        cmds._set({"profile": "Gamepad", "key": "hotkey:slowmotion", "value": str(self.idx("l2"))})
        cmds._save({"profile": "Gamepad"})
        self.assertIn("Gamepad", self.local_toml()["ra_profiles"])
        cmds._reset({"profile": "Gamepad"})
        self.assertNotIn("Gamepad", self.local_toml().get("ra_profiles", {}))
        self.assertEqual(policy.load_merged()["ra_profiles"]["Gamepad"]["hotkeys"]["slowmotion"], "r")


class ReviewFixes(_Base):
    def test_net_zero_family_toggle_does_not_clobber_the_base_map(self):
        # Review #1 (HIGH): toggle 8BitDo (base -> Retro) ON then OFF, plus a real hotkey change,
        # then save. The net-zero family MUST NOT be written -- an unassign("") would shadow the base
        # 8BitDo->Retro and silently strip a DIFFERENT profile's assignment.
        cmds._set({"profile": "Gamepad", "key": "family:8BitDo", "value": "1"})
        cmds._set({"profile": "Gamepad", "key": "family:8BitDo", "value": "0"})
        cmds._set({"profile": "Gamepad", "key": "hotkey:quit", "value": str(self.idx("start"))})
        self.assertTrue(cmds._save({"profile": "Gamepad"})["saved"])
        self.assertNotIn("8BitDo", self.local_toml().get("ra_profile_map", {}))
        self.assertEqual(policy.load_merged()["ra_profile_map"]["8BitDo"], "Retro")   # base intact
        self.assertEqual(self.local_toml()["ra_profiles"]["Gamepad"]["hotkeys"], {"quit": "start"})

    def test_net_zero_hotkey_edit_writes_no_shadow(self):
        # Review #5 (LOW): set modifier to its CURRENT value (l3) + change quit, save. Only the
        # net-changed field (quit) is written -- no redundant modifier shadow that could later mask
        # a base update.
        cmds._set({"profile": "Gamepad", "key": "hotkey:modifier", "value": str(self.idx("l3"))})
        cmds._set({"profile": "Gamepad", "key": "hotkey:quit", "value": str(self.idx("start"))})
        cmds._save({"profile": "Gamepad"})
        self.assertEqual(self.local_toml()["ra_profiles"]["Gamepad"]["hotkeys"], {"quit": "start"})

    def test_editing_a_deleted_profile_is_refused(self):
        # Review #3 (MED): the reused detail page does not auto-pop after Delete; a further edit must
        # be refused, not re-create the profile or write a dangling map row.
        from lib.madsrv.rpc import RpcError
        cmds._create({"name": "MyPad"})
        cmds._delete({"profile": "MyPad"})
        for rpc in (cmds._get, cmds._save,
                    lambda p: cmds._set({**p, "key": "hotkey:quit", "value": "1"})):
            with self.assertRaises(RpcError):
                rpc({"profile": "MyPad"})
        self.assertNotIn("MyPad", policy.load_merged().get("ra_profiles", {}))

    def test_delete_fails_closed_when_base_unreadable(self):
        # Review #6 (LOW): with the base policy unreadable, is_shipped can't classify -- delete must
        # refuse rather than drop a shipped profile's local edits.
        from lib.madsrv.rpc import RpcError
        with mock.patch.object(cmds, "_base_policy", lambda: {}):
            with self.assertRaises(RpcError):
                cmds._delete({"profile": "Gamepad"})


class Lightgun(_Base):
    def gidx(self, token):
        return cmds._GUN_TOKEN_ORDER.index(token)

    def test_group_present_non_deck_absent_deck(self):
        self.assertIn("Lightgun", [g["title"] for g in cmds._get({"profile": "Gamepad"})["groups"]])
        self.assertNotIn("Lightgun",
                         [g["title"] for g in cmds._get({"profile": "Deck"})["groups"]])   # not on Deck

    def test_defaults_inherit(self):
        lg = next(g for g in cmds._get({"profile": "Arcade"})["groups"] if g["title"] == "Lightgun")
        self.assertEqual([s["key"] for s in lg["settings"]],
                         [f"gun:{n}" for n in ra_profiles._GUN_BINDS] + ["mouse_index"])
        by_key = {s["key"]: s for s in lg["settings"]}
        self.assertEqual(by_key["gun:trigger"]["value"], 0)     # (inherit global cfg)
        self.assertEqual(by_key["mouse_index"]["value"], 0)     # (auto-detect / inherit)

    def test_gun_bind_roundtrip(self):
        cmds._create({"name": "Gun"})
        cmds._set({"profile": "Gun", "key": "gun:trigger", "value": str(self.gidx("mbtn:1"))})
        cmds._set({"profile": "Gun", "key": "gun:aux_a", "value": str(self.gidx("z"))})
        self.assertTrue(cmds._save({"profile": "Gun"})["saved"])
        self.assertEqual(self.local_toml()["ra_profiles"]["Gun"]["lightgun"],
                         {"trigger": "mbtn:1", "aux_a": "z"})

    def test_mouse_index_roundtrip_and_auto_clears(self):
        cmds._create({"name": "Gun"})
        cmds._set({"profile": "Gun", "key": "mouse_index", "value": "4"})     # option 4 -> Mouse 3
        cmds._save({"profile": "Gun"})
        self.assertEqual(self.local_toml()["ra_profiles"]["Gun"]["lightgun"]["mouse_index"], "3")
        cmds._set({"profile": "Gun", "key": "mouse_index", "value": "0"})     # auto-detect -> clears
        cmds._save({"profile": "Gun"})
        self.assertNotIn("lightgun", self.local_toml()["ra_profiles"]["Gun"])   # emptied table dropped

    def test_net_zero_gun_edit_writes_nothing(self):
        # inherit -> mbtn:1 -> back to inherit, plus a real hotkey change: only the hotkey is written.
        cmds._create({"name": "Gun"})
        cmds._set({"profile": "Gun", "key": "gun:trigger", "value": str(self.gidx("mbtn:1"))})
        cmds._set({"profile": "Gun", "key": "gun:trigger", "value": str(self.gidx(""))})
        cmds._set({"profile": "Gun", "key": "hotkey:quit", "value": str(self.idx("start"))})
        cmds._save({"profile": "Gun"})
        self.assertNotIn("lightgun", self.local_toml()["ra_profiles"]["Gun"])   # net-zero gun -> no table
        self.assertEqual(self.local_toml()["ra_profiles"]["Gun"]["hotkeys"]["quit"], "start")

    def test_unknown_gun_bind_rejected(self):
        from lib.madsrv.rpc import RpcError
        cmds._create({"name": "Gun"})
        with self.assertRaises(RpcError):
            cmds._set({"profile": "Gun", "key": "gun:bogus", "value": "1"})

    def test_net_zero_mouse_index_edit_writes_nothing(self):
        # mouse_index has its OWN net-change guard: Mouse 3 then back to auto (== disk "") plus a real
        # hotkey change -> only the hotkey is written, no lightgun table.
        cmds._create({"name": "Gun"})
        cmds._set({"profile": "Gun", "key": "mouse_index", "value": "4"})     # -> Mouse 3
        cmds._set({"profile": "Gun", "key": "mouse_index", "value": "0"})     # back to auto (net-zero)
        cmds._set({"profile": "Gun", "key": "hotkey:quit", "value": str(self.idx("start"))})
        cmds._save({"profile": "Gun"})
        self.assertNotIn("lightgun", self.local_toml()["ra_profiles"]["Gun"])
        self.assertEqual(self.local_toml()["ra_profiles"]["Gun"]["hotkeys"]["quit"], "start")

    def test_a_raw_gun_value_is_preserved_in_the_picker(self):
        # A hand-authored raw escape the vocabulary can't name must survive as a "(current: …)" slot.
        cmds._create({"name": "Gun"})
        data = cmds.localpolicy.load(cmds.LOCAL)
        ra_profiles.set_lightgun(data, "Gun", "aux_a", "axis:+3")
        cmds.localpolicy.dump(cmds.LOCAL, data)
        cmds._buf.reset()
        lg = next(g for g in cmds._get({"profile": "Gun"})["groups"] if g["title"] == "Lightgun")
        row = next(s for s in lg["settings"] if s["key"] == "gun:aux_a")
        self.assertEqual(row["options"][-1], "(current: axis:+3)")
        self.assertEqual(row["value"], len(cmds._GUN_TOKEN_LABELS))

    def test_out_of_range_mouse_index_shows_auto(self):
        # A junk / out-of-range stored mouse_index renders as auto-detect (index 0), never IndexError.
        cmds._create({"name": "Gun"})
        for bad in ("99", "x"):
            data = cmds.localpolicy.load(cmds.LOCAL)
            ra_profiles.set_mouse_index(data, "Gun", bad)
            cmds.localpolicy.dump(cmds.LOCAL, data)
            cmds._buf.reset()
            lg = next(g for g in cmds._get({"profile": "Gun"})["groups"] if g["title"] == "Lightgun")
            row = next(s for s in lg["settings"] if s["key"] == "mouse_index")
            self.assertEqual(row["value"], 0)


class Gameplay(_Base):
    def bidx(self, token):
        return cmds._GP_BUTTON_ORDER.index(token)

    def test_group_on_every_profile_incl_deck(self):
        for name in ("Gamepad", "Arcade", "Deck"):
            self.assertIn("Gameplay", [g["title"] for g in cmds._get({"profile": name})["groups"]])

    def test_button_row_uses_button_vocab_trigger_row_uses_trigger_vocab(self):
        gp = next(g for g in cmds._get({"profile": "Gamepad"})["groups"] if g["title"] == "Gameplay")
        self.assertEqual([s["key"] for s in gp["settings"]],
                         [f"gp:{s}" for s in ra_profiles._GAMEPLAY_EDITABLE])
        by_key = {s["key"]: s for s in gp["settings"]}
        self.assertEqual(len(by_key["gp:a_btn"]["options"]), len(cmds._GP_BUTTON_TOKENS))
        self.assertEqual(len(by_key["gp:l2_axis"]["options"]), len(cmds._GP_TRIGGER_TOKENS))
        self.assertEqual((by_key["gp:a_btn"]["value"], by_key["gp:l2_axis"]["value"]), (0, 0))  # inherit

    def test_gameplay_roundtrip(self):
        cmds._create({"name": "GP"})
        cmds._set({"profile": "GP", "key": "gp:a_btn", "value": str(self.bidx("b"))})
        cmds._set({"profile": "GP", "key": "gp:l2_axis", "value": str(cmds._GP_TRIGGER_ORDER.index("r2"))})
        self.assertTrue(cmds._save({"profile": "GP"})["saved"])
        self.assertEqual(self.local_toml()["ra_profiles"]["GP"]["gameplay"],
                         {"a_btn": "b", "l2_axis": "r2"})

    def test_net_zero_gameplay_edit_writes_nothing(self):
        cmds._create({"name": "GP"})
        cmds._set({"profile": "GP", "key": "gp:a_btn", "value": str(self.bidx("b"))})
        cmds._set({"profile": "GP", "key": "gp:a_btn", "value": str(self.bidx(""))})   # back to inherit
        cmds._set({"profile": "GP", "key": "hotkey:quit", "value": str(self.idx("start"))})
        cmds._save({"profile": "GP"})
        self.assertNotIn("gameplay", self.local_toml()["ra_profiles"]["GP"])
        self.assertEqual(self.local_toml()["ra_profiles"]["GP"]["hotkeys"]["quit"], "start")

    def test_unknown_gameplay_control_rejected(self):
        from lib.madsrv.rpc import RpcError
        cmds._create({"name": "GP"})
        with self.assertRaises(RpcError):
            cmds._set({"profile": "GP", "key": "gp:bogus", "value": "1"})

    def test_a_raw_gameplay_value_is_preserved_in_the_picker(self):
        cmds._create({"name": "GP"})
        data = cmds.localpolicy.load(cmds.LOCAL)
        ra_profiles.set_gameplay(data, "GP", "a_btn", "btn:9")
        cmds.localpolicy.dump(cmds.LOCAL, data)
        cmds._buf.reset()
        gp = next(g for g in cmds._get({"profile": "GP"})["groups"] if g["title"] == "Gameplay")
        row = next(s for s in gp["settings"] if s["key"] == "gp:a_btn")
        self.assertEqual(row["options"][-1], "(current: btn:9)")
        self.assertEqual(row["value"], len(cmds._GP_BUTTON_LABELS))

    def test_reselecting_the_current_raw_slot_keeps_the_token(self):
        # Re-picking the trailing "(current: <raw>)" option must round-trip the raw token unchanged
        # (the out-of-range branch of _gp_token_from_index returns the current working value).
        cmds._create({"name": "GP"})
        data = cmds.localpolicy.load(cmds.LOCAL)
        ra_profiles.set_gameplay(data, "GP", "a_btn", "btn:9")
        cmds.localpolicy.dump(cmds.LOCAL, data)
        cmds._buf.reset()
        cmds._get({"profile": "GP"})                                  # load into the buffer
        cmds._set({"profile": "GP", "key": "gp:a_btn", "value": str(len(cmds._GP_BUTTON_LABELS))})
        row = next(s for grp in cmds._get({"profile": "GP"})["groups"] if grp["title"] == "Gameplay"
                   for s in grp["settings"] if s["key"] == "gp:a_btn")
        self.assertEqual(row["options"][-1], "(current: btn:9)")      # token uncorrupted
        cmds._save({"profile": "GP"})
        self.assertEqual(self.local_toml()["ra_profiles"]["GP"]["gameplay"]["a_btn"], "btn:9")  # survives


if __name__ == "__main__":
    unittest.main()
