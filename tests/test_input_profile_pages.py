"""The input-profile PICKER pages: lib/madsrv/yuzu_profile_cmds (Eden + Citron, per family),
lib/madsrv/ryujinx_profile_cmds (per player) and lib/madsrv/cemu_pgmap_cmds (Wii U per game).

Hermetic: a temp profile dir plus an in-memory controller-policy.local.toml. What matters is that
the two contexts write DISJOINT slices, a stale pick stays visible so it can be cleared, and
clearing the last pick leaves no husk behind.

Run:  python3 -m unittest tests.test_input_profile_pages -v
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib.madsrv import (cemu_hh_cmds, cemu_pgmap_cmds as pgm,  # noqa: F401 (registers cemuhh.games)
                        rpc, ryujinx_profile_cmds as ryp, yuzu_profile_cmds as yzp)

_DS_GUID = "030000004c050000e60c000000006800"      # DualSense 054c:0ce6
_WP_GUID = "050000007e0500003003000001000000"      # Wii U Pro  057e:0330


def _m(name):
    return rpc._METHODS[name][0]


class _LocalPolicy(unittest.TestCase):
    """Patches a module's localpolicy load/dump onto an in-memory dict."""

    def _patch_policy(self, mod):
        self.local: dict = {}
        for p in (mock.patch.object(mod.localpolicy, "load",
                                    lambda which: copy.deepcopy(self.local)),
                  mock.patch.object(mod.localpolicy, "dump",
                                    lambda which, data: self.local.clear()
                                    or self.local.update(copy.deepcopy(data)))):
            p.start()
            self.addCleanup(p.stop)

    def _be(self, backend):
        return self.local.get("backends", {}).get(backend, {})

    def _map(self, backend, ctx):
        return self._be(backend).get("profile_map", {}).get(ctx, {})


# ── Eden / Citron: per family ───────────────────────────────────────────────────
class YuzuPages(_LocalPolicy):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for stem, guid in (("DS 1", _DS_GUID), ("DS 2", _DS_GUID), ("WiiU Pro 1", _WP_GUID)):
            (self.d / f"{stem}.ini").write_text(
                f'[Controls]\nbutton_a="engine:sdl,port:0,guid:{guid},button:1"\n', encoding="utf-8")
        self._patch_policy(yzp)
        p = mock.patch.object(yzp, "load_merged", self._merged)
        p.start()
        self.addCleanup(p.stop)

    def _merged(self):
        out = {}
        for be in ("eden", "citron"):
            cfg = copy.deepcopy(self._be(be))
            cfg["template_profile"] = str(self.d / "T.ini")
            out[be] = cfg
        return {"backends": out}

    def _get(self, ns):
        return _m(f"{ns}.get")({})

    def _rows(self, ns):
        page = self._get(ns)
        return {r["label"]: r for g in page["groups"] for r in g["settings"]}

    def test_one_row_per_family_and_no_xarcade(self):
        rows = self._rows("eden_input_docked")
        self.assertIn("DualSense", rows)
        self.assertNotIn("X-Arcade", rows)          # family_of never returns it -> a dead row
        self.assertTrue(all(r.get("picker") for r in rows.values() if r["type"] == "enum"))

    def test_options_are_filtered_to_the_family(self):
        rows = self._rows("eden_input_docked")
        self.assertIn("DS 1", rows["DualSense"]["options"])
        self.assertNotIn("WiiU Pro 1", rows["DualSense"]["options"])   # authored on another pad
        self.assertIn("WiiU Pro 1", rows["Wii Remote Pro"]["options"])

    def test_pick_roundtrip_and_clear_leaves_no_husk(self):
        opts = self._rows("eden_input_docked")["DualSense"]["options"]
        _m("eden_input_docked.set")({"key": "family:DualSense", "value": opts.index("DS 1")})
        self.assertEqual(self._map("eden", "docked"), {"DualSense": "DS 1"})
        self.assertEqual(self._rows("eden_input_docked")["DualSense"]["value"], opts.index("DS 1"))
        _m("eden_input_docked.set")({"key": "family:DualSense", "value": 0})
        self.assertEqual(self.local, {})            # pruned all the way out

    def test_contexts_write_disjoint_slices(self):
        opts = self._rows("eden_input_docked")["DualSense"]["options"]
        _m("eden_input_docked.set")({"key": "family:DualSense", "value": opts.index("DS 1")})
        _m("eden_input_handheld.set")({"key": "family:DualSense", "value": opts.index("DS 2")})
        self.assertEqual(self._map("eden", "docked"), {"DualSense": "DS 1"})
        self.assertEqual(self._map("eden", "handheld"), {"DualSense": "DS 2"})

    def test_eden_and_citron_are_independent(self):
        opts = self._rows("eden_input_docked")["DualSense"]["options"]
        _m("eden_input_docked.set")({"key": "family:DualSense", "value": opts.index("DS 1")})
        self.assertEqual(self._map("citron", "docked"), {})

    def test_handheld_page_has_the_mirror_toggle_and_docked_does_not(self):
        hh = {r["key"] for g in self._get("eden_input_handheld")["groups"] for r in g["settings"]}
        dk = {r["key"] for g in self._get("eden_input_docked")["groups"] for r in g["settings"]}
        self.assertIn("handheld_mirrors_docked", hh)
        self.assertNotIn("handheld_mirrors_docked", dk)
        with self.assertRaises(rpc.RpcError):
            _m("eden_input_docked.set")({"key": "handheld_mirrors_docked", "value": True})

    def test_mirror_hint_names_the_inherited_profile(self):
        opts = self._rows("eden_input_docked")["DualSense"]["options"]
        _m("eden_input_docked.set")({"key": "family:DualSense", "value": opts.index("DS 1")})
        _m("eden_input_handheld.set")({"key": "handheld_mirrors_docked", "value": True})
        row = self._rows("eden_input_handheld")["DualSense"]
        self.assertEqual(row["options"][0], "(from docked: DS 1)")
        self.assertEqual(row["value"], 0)           # DISPLAY only -- index 0 still means unset

    def test_stale_pick_stays_visible_so_it_can_be_cleared(self):
        self.local = {"backends": {"eden": {"profile_map": {"docked": {"DualSense": "Deleted"}}}}}
        row = self._rows("eden_input_docked")["DualSense"]
        self.assertIn("Deleted", row["options"])
        self.assertEqual(row["options"][row["value"]], "Deleted")

    def test_bad_input_is_rejected(self):
        for params in ({"key": "nope", "value": 1},
                       {"key": "family:Nonesuch", "value": 1},
                       {"key": "family:DualSense", "value": "x"},
                       {"key": "family:DualSense", "value": 999}):
            with self.assertRaises(rpc.RpcError, msg=params):
                _m("eden_input_docked.set")(params)

    def test_repicking_a_stale_stem_is_refused(self):
        # A stored-but-deleted stem stays VISIBLE so it can be cleared; re-selecting it must not
        # silently re-store a profile that is not on disk. (Note the narrower guarantee: an index
        # that lands on a NEIGHBOURING real stem after the list shifts is still accepted -- a
        # panel-wide enum limitation PCSX2's picker documents too, bounded by the page note.)
        self.local = {"backends": {"eden": {"profile_map": {"docked": {"DualSense": "Deleted"}}}}}
        row = self._rows("eden_input_docked")["DualSense"]
        with self.assertRaises(rpc.RpcError):
            _m("eden_input_docked.set")({"key": "family:DualSense",
                                         "value": row["options"].index("Deleted")})


# ── Ryujinx: per player ─────────────────────────────────────────────────────────
class RyujinxPages(_LocalPolicy):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.pdir = self.d / "profiles" / "controller"
        self.pdir.mkdir(parents=True)
        for stem in ("Pro 1", "Pro 2"):
            (self.pdir / f"{stem}.json").write_text(json.dumps(
                {"left_joycon": {"button_a": stem}}), encoding="utf-8")
        self._patch_policy(ryp)
        p = mock.patch.object(ryp, "load_merged", self._merged)
        p.start()
        self.addCleanup(p.stop)

    def _merged(self):
        cfg = copy.deepcopy(self._be("ryujinx"))
        cfg["config_file"] = str(self.d / "Config.json")
        return {"backends": {"ryujinx": cfg}}

    def _rows(self, ns):
        page = _m(f"{ns}.get")({})
        return {r["label"]: r for g in page["groups"] for r in g["settings"]}

    def test_four_player_rows(self):
        rows = self._rows("ryujinx_input_docked")
        self.assertEqual([l for l in rows if l.startswith("Player")],
                         ["Player 1", "Player 2", "Player 3", "Player 4"])

    def test_pick_roundtrip_and_clear_prunes(self):
        opts = self._rows("ryujinx_input_docked")["Player 1"]["options"]
        _m("ryujinx_input_docked.set")({"key": "p1", "value": opts.index("Pro 1")})
        self.assertEqual(self._map("ryujinx", "docked"), {"p1": "Pro 1"})
        _m("ryujinx_input_docked.set")({"key": "p1", "value": 0})
        self.assertEqual(self.local, {})

    def test_two_players_cannot_share_one_profile(self):
        opts = self._rows("ryujinx_input_docked")["Player 1"]["options"]
        _m("ryujinx_input_docked.set")({"key": "p1", "value": opts.index("Pro 1")})
        with self.assertRaises(rpc.RpcError):
            _m("ryujinx_input_docked.set")({"key": "p2", "value": opts.index("Pro 1")})
        _m("ryujinx_input_docked.set")({"key": "p2", "value": opts.index("Pro 2")})   # distinct is fine
        self.assertEqual(self._map("ryujinx", "docked"), {"p1": "Pro 1", "p2": "Pro 2"})

    def test_contexts_are_disjoint(self):
        opts = self._rows("ryujinx_input_docked")["Player 1"]["options"]
        _m("ryujinx_input_docked.set")({"key": "p1", "value": opts.index("Pro 1")})
        _m("ryujinx_input_handheld.set")({"key": "p1", "value": opts.index("Pro 2")})
        self.assertEqual(self._map("ryujinx", "docked"), {"p1": "Pro 1"})
        self.assertEqual(self._map("ryujinx", "handheld"), {"p1": "Pro 2"})

    def test_handheld_note_separates_the_two_meanings_of_handheld(self):
        page = _m("ryujinx_input_handheld.get")({})
        note = " ".join(g.get("note", "") for g in page["groups"])
        self.assertIn("Deck's own screen", note)
        self.assertIn("Handheld", note)

    def test_bad_input_is_rejected(self):
        for params in ({"key": "p9", "value": 1}, {"key": "nope", "value": 1},
                       {"key": "p1", "value": 999}, {"key": "p1", "value": "x"}):
            with self.assertRaises(rpc.RpcError, msg=params):
                _m("ryujinx_input_docked.set")(params)


# ── Wii U: per game ─────────────────────────────────────────────────────────────
class CemuPerGamePage(_LocalPolicy):
    TID = "0005000010111100"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for nm, ty in (("DualSense 1", "Wii U Pro Controller"),
                       ("DualSense 2", "Wii U Pro Controller"),
                       ("Steamdeck", "Wii U GamePad")):
            (self.d / f"{nm}.xml").write_text(
                f"<emulated_controller><type>{ty}</type></emulated_controller>", encoding="utf-8")
        self._patch_policy(pgm)
        for target in (pgm, pgm.cin):
            p = mock.patch.object(target, "load_merged", self._merged)
            p.start()
            self.addCleanup(p.stop)

    def _merged(self):
        cfg = copy.deepcopy(self._be("cemu"))
        cfg["config_dir"] = str(self.d)
        cfg.setdefault("profile_map", {"docked": {"DualSense": "DualSense 1"}, "handheld": {}})
        return {"backends": {"cemu": cfg}}

    def _rows(self, ns):
        page = _m(f"{ns}.get")({"titleid": self.TID})
        return {r["label"]: r for g in page["groups"] for r in g["settings"]}

    def _pergame(self, ctx):
        return self._be("cemu").get("pergame", {}).get(self.TID, {}).get(ctx, {})

    def test_zero_option_names_what_it_inherits(self):
        self.assertEqual(self._rows("cemu_pgmap_docked")["DualSense"]["options"][0],
                         "Same as all games: DualSense 1")
        # a family with no global pick says so plainly instead of naming a phantom
        self.assertEqual(self._rows("cemu_pgmap_docked")["Xbox"]["options"][0], "Same as all games")

    def test_gamepad_family_only_offers_gamepad_typed_profiles(self):
        deck = self._rows("cemu_pgmap_docked")["Steam Deck"]["options"]
        self.assertIn("Steamdeck", deck)
        self.assertNotIn("DualSense 1", deck)              # Pro-typed, invalid on the GamePad slot
        ds = self._rows("cemu_pgmap_docked")["DualSense"]["options"]
        self.assertNotIn("Steamdeck", ds)

    def test_override_roundtrip_and_clear_prunes_every_table(self):
        opts = self._rows("cemu_pgmap_docked")["DualSense"]["options"]
        _m("cemu_pgmap_docked.set")({"titleid": self.TID, "key": "family:DualSense",
                                     "value": opts.index("DualSense 2")})
        self.assertEqual(self._pergame("docked"), {"DualSense": "DualSense 2"})
        _m("cemu_pgmap_docked.set")({"titleid": self.TID, "key": "family:DualSense", "value": 0})
        self.assertEqual(self.local, {})                   # context, game and pergame all pruned

    def test_contexts_are_disjoint(self):
        opts = self._rows("cemu_pgmap_docked")["DualSense"]["options"]
        _m("cemu_pgmap_docked.set")({"titleid": self.TID, "key": "family:DualSense",
                                     "value": opts.index("DualSense 2")})
        self.assertEqual(self._pergame("handheld"), {})

    def test_bad_titleid_is_rejected(self):
        for tid in ("", "nope", "dead"):
            with self.assertRaises(rpc.RpcError, msg=tid):
                _m("cemu_pgmap_docked.get")({"titleid": tid})

    def test_neither_door_browses_games_itself(self):
        # Both doors are leaves of a game-first per-game menu now: docked browses via cemu.games,
        # handheld via cemuhh.games. A second browser here would be a second answer to "which games
        # are customised", and the handheld one would badge docked state.
        self.assertNotIn("cemu_pgmap_handheld.games", rpc._METHODS)
        self.assertNotIn("cemu_pgmap_docked.games", rpc._METHODS)
        self.assertIn("cemuhh.games", rpc._METHODS)

    def test_native_pin_is_warned_about_on_the_page(self):
        gp = self.d / "gameProfiles"
        gp.mkdir()
        (gp / f"{self.TID}.ini").write_text("[Controller]\ncontroller1 = DualSense 1\n",
                                            encoding="utf-8")
        with mock.patch.object(pgm.cemu_games, "_CONFIG_DIR", self.d):
            note = _m("cemu_pgmap_docked.get")({"titleid": self.TID})["note"]
        self.assertIn("Cemu's own pin", note)
        # Cemu's ini keys are 1-BASED (controller1..controller8, the same numbers the "Cemu's own
        # pin" page labels its rows with), so the warning must name Controller 1 VERBATIM. This
        # assertion previously said "Controller 2" and locked in an off-by-one that sent the user
        # to the wrong row to clear the conflict.
        self.assertIn("Controller 1", note)
        self.assertNotIn("Controller 2", note)


if __name__ == "__main__":
    unittest.main()
