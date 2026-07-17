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


class DetailPayload(_Base):
    def test_get_shape_and_current_values(self):
        out = cmds._get({"profile": "Gamepad"})
        self.assertTrue(out["buffered"])
        titles = [g["title"] for g in out["groups"]]
        self.assertEqual(titles, ["Used by", "Hotkeys", "Options", ""])
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


if __name__ == "__main__":
    unittest.main()
