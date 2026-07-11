"""MAD 'On-the-go' page backend (lib/madsrv/onthego_cmds.py).

Chooser tree shape, global mode enum round-trip + watt clamp, per-system enable/watt-cap-inherit,
the res-enum divergence (PS2/PS3 offer 2x, others don't), switch/wiiu no-res + note, and the policy
round-trip. Temp local.toml + stubbed staterev. Run: python3 -m unittest tests.test_onthego_cmds -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import onthego_cmds, rpc  # noqa: F401 (import registers the methods)


def call(name, **p):
    return rpc._METHODS[name][0](p)


class OnTheGo(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        import lib.policy as policy
        self._orig = policy.LOCAL
        policy.LOCAL = self.d / "controller-policy.local.toml"
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None
        import lib.ra_handheld_input as rhi
        self._pad = rhi.PAD_OVERRIDES
        rhi.PAD_OVERRIDES = self.d / "pad-overrides.json"
        import lib.daphne_input as di
        self._deck = di.DECK_INI
        di.DECK_INI = self.d / "hypinput.deck.ini"
        # Deterministic Per-system membership: control which catalog systems "have a gamelist"
        # (default: all present) so the grid count doesn't depend on the real ~/ES-DE/gamelists.
        import lib.es_systems as esys
        self._present = {s for s, _n, _r in onthego_cmds._SYSTEMS}
        self._esys = (esys._has_gamelist, esys.load_systems)
        esys.load_systems = lambda: {s: [] for s, _n, _r in onthego_cmds._SYSTEMS}
        esys._has_gamelist = lambda s: s in self._present
        # Membership also requires >=1 VISIBLE game (not just a gamelist.xml). Default: present systems
        # have games; a test can override visible_records to model an emptied gamelist.
        import lib.es_gamelist as egl
        self._egl = egl.visible_records
        self._visible = lambda s: {"g": 1} if s in self._present else {}
        egl.visible_records = lambda s: self._visible(s)
        # Deterministic resolution backend (WS-H): don't depend on the device's configured cores.
        import lib.handheld_res as hr
        self._rb = hr._render_backend
        self._backend = {"ps2": "pcsx2", "psx": "Beetle PSX HW"}   # a scalar + a dedup-ing enum
        hr._render_backend = lambda s: hr.REGISTRY.get(self._backend.get(s))

    def tearDown(self):
        import lib.policy as policy
        policy.LOCAL = self._orig
        import lib.staterev as sr
        sr.bump = self._bump
        import lib.ra_handheld_input as rhi
        rhi.PAD_OVERRIDES = self._pad
        import lib.daphne_input as di
        di.DECK_INI = self._deck
        import lib.es_systems as esys
        esys._has_gamelist, esys.load_systems = self._esys
        import lib.es_gamelist as egl
        egl.visible_records = self._egl
        import lib.handheld_res as hr
        hr._render_backend = self._rb
        shutil.rmtree(self.d, ignore_errors=True)

    def _merged(self):
        import lib.policy as policy
        return policy.load_merged()

    def _row(self, ns, key):
        rows = [s for s in call(ns + ".get")["groups"][0]["settings"] if s["key"] == key]
        return rows[0] if rows else None

    def test_tree(self):
        secs = call("onthego.list")["tiles"][0]["sections"]
        self.assertEqual(secs[0]["arg"], "onthego_global")
        self.assertEqual(secs[1]["kind"], "grid")                   # WS-E: Per-system is a tile grid
        self.assertEqual(len(secs[1]["sections"]), 15)              # all catalog present (mocked)

    def test_persystem_grid_tiles(self):   # WS-E: each entry is an icon tile, not a plain row
        secs = call("onthego.list")["tiles"][0]["sections"]
        self.assertEqual(secs[1]["kind"], "grid")
        tiles = secs[1]["sections"]
        for t in tiles:                                             # every tile carries key + art + leaves
            self.assertIn("key", t)
            self.assertIn("art", t)
            self.assertTrue(t["sections"])
        ps2 = next(t for t in tiles if t["key"] == "ps2")          # a simple system -> ONE leaf
        self.assertEqual(len(ps2["sections"]), 1)
        self.assertEqual((ps2["sections"][0]["kind"], ps2["sections"][0]["arg"]),
                         ("settings", "onthego_ps2"))

    def test_membership_gamelist_gated(self):   # WS-E: PS1 phantom hidden, Xbox shown when present
        self._present = {s for s, _n, _r in onthego_cmds._SYSTEMS if s != "psx"}
        keys = {t["key"] for t in call("onthego.list")["tiles"][0]["sections"][1]["sections"]}
        self.assertNotIn("psx", keys)                              # no gamelist -> dropped
        self.assertIn("xbox", keys)                               # has a gamelist -> present
        self._present = {"ps2", "gc"}                              # only these two have games
        keys = {t["key"] for t in call("onthego.list")["tiles"][0]["sections"][1]["sections"]}
        self.assertEqual(keys, {"ps2", "gc"})

    def test_emptied_gamelist_is_hidden(self):   # bug: gamelist.xml exists but has 0 visible games
        # xbox: file present (_has_gamelist True) but no visible games -> must NOT show (user deleted
        # its last game; ES-DE left an empty gamelist.xml behind).
        self._visible = lambda s: {} if s == "xbox" else ({"g": 1} if s in self._present else {})
        keys = {t["key"] for t in call("onthego.list")["tiles"][0]["sections"][1]["sections"]}
        self.assertNotIn("xbox", keys)                            # emptied system dropped
        self.assertIn("ps2", keys)                               # a system with games still shows

    def test_empty_membership_drops_persystem_row(self):   # WS-E review fix: no empty grid message
        self._present = set()                                     # a user with no catalog gamelists
        labels = [s["label"] for s in call("onthego.list")["tiles"][0]["sections"]]
        self.assertNotIn("Per-system", labels)                    # row dropped -> empty grid unreachable
        self.assertEqual(labels[0], "Global")                    # Global + RetroArch still present
        self.assertIn("RetroArch (handheld)", labels)

    def test_xbox_registered_no_res(self):   # WS-E: Xbox added to the catalog, res-off
        self.assertIsNotNone(self._row("onthego_xbox", "enable"))
        self.assertIsNone(self._row("onthego_xbox", "res"))       # xemu not in the res rail yet

    def test_wiiu_folds_resolution_under_per_system(self):
        secs = call("onthego.list")["tiles"][0]["sections"]
        self.assertNotIn("cemures", {s.get("arg") for s in secs})   # NOT a top-level section
        wiiu = next(s for s in secs[1]["sections"] if s["label"] == "Wii U")
        self.assertEqual({c["arg"] for c in wiiu["sections"]}, {"onthego_wiiu", "cemures"})

    def test_daphne_handheld_editor(self):   # WS-D (D2)
        # defaults re-value coin/start to the Deck's SDL buttons (Select=5, Start=7)
        self.assertEqual(self._row("daphne_handheld", "COIN1")["value"],
                         onthego_cmds._DAPHNE_BTN_TOKENS.index("5"))
        self.assertEqual(self._row("daphne_handheld", "START1")["value"],
                         onthego_cmds._DAPHNE_BTN_TOKENS.index("7"))
        call("daphne_handheld.set", key="COIN1", value=0)          # -> A (token 1)
        import lib.daphne_input as di, lib.hypinput as hyp
        self.assertEqual(hyp.load(di.DECK_INI).button_value("COIN1"), 1)
        call("daphne_handheld.reset")
        self.assertEqual(self._row("daphne_handheld", "COIN1")["value"],
                         onthego_cmds._DAPHNE_BTN_TOKENS.index("5"))   # back to Select

    def test_daphne_input_leaf_is_handheld_editor(self):   # WS-D (D2): fold points at the new editor
        persys = call("onthego.list")["tiles"][0]["sections"][1]["sections"]
        daph = next(s for s in persys if s["label"] == "Daphne")
        inp = next(c for c in daph["sections"] if c["label"] == "Input")
        self.assertEqual((inp["kind"], inp["arg"]), ("settings", "daphne_handheld"))

    def test_daphne_lindbergh_fold_input(self):   # WS-D (now WS-E tiles: two leaves each)
        persys = call("onthego.list")["tiles"][0]["sections"][1]["sections"]
        # Lindbergh: Settings (watt cap) + Input (per-device pads page).
        lind = next(s for s in persys if s["label"] == "Sega Lindbergh")
        self.assertEqual({c["kind"] for c in lind["sections"]}, {"settings", "lindbergh_pads"})
        # Daphne: Settings (watt cap) + Input (the handheld editor -- both are settings pages).
        daph = next(s for s in persys if s["label"] == "Daphne")
        self.assertEqual({c["arg"] for c in daph["sections"]}, {"onthego_daphne", "daphne_handheld"})
        for sys in ("daphne", "lindbergh"):
            self.assertIsNone(self._row(f"onthego_{sys}", "res"))         # res_capable=False -> no res
            self.assertIsNotNone(self._row(f"onthego_{sys}", "enable"))   # enable + watt cap

    def test_global_mode_roundtrip(self):
        for idx, (detect, force) in ((1, ("manual", "handheld")),
                                     (2, ("manual", "docked")),
                                     (0, ("display", ""))):
            call("onthego_global.set", key="mode", value=str(idx))
            hh = self._merged()["handheld"]
            self.assertEqual((hh["detect"], hh["force"]), (detect, force))
            self.assertEqual(self._row("onthego_global", "mode")["value"], idx)

    def test_watt_clamp(self):
        call("onthego_global.set", key="default_watt_cap", value="99")
        self.assertEqual(self._merged()["handheld"]["default_watt_cap"], 15)

    def test_per_system_inherit(self):
        call("onthego_ps2.set", key="watt_cap", value="13")
        row = self._row("onthego_ps2", "watt_cap")
        self.assertEqual((row["value"], row["inherited"]), (13, False))
        call("onthego_ps2.set", key="watt_cap", value="inherit")
        self.assertNotIn("watt_cap", self._merged()["systems"]["ps2"]["handheld"])
        self.assertTrue(self._row("onthego_ps2", "watt_cap")["inherited"])

    def test_res_labels_per_backend_and_picker(self):   # WS-H
        # PS2 (pcsx2): PCSX2's own labels, and the row forces the full-list picker.
        row = self._row("onthego_ps2", "res")
        self.assertTrue(row.get("picker"))
        self.assertIn("2x Native (~720px)", row["options"])
        call("onthego_ps2.set", key="res", value="1")     # idx1 -> "2x"
        self.assertEqual(self._merged()["systems"]["ps2"]["handheld"]["res"], "2x")

    def test_res_dedup_and_snap_psx_beetle(self):   # WS-H
        # PS1 via Beetle PSX HW dedupes to Native/2x/4x/8x (no 3x/6x rungs) -> 4 + Inherit = 5.
        opts = self._row("onthego_psx", "res")["options"]
        self.assertEqual(len(opts), 5)
        call("onthego_psx.set", key="res", value="2")     # idx2 -> "4x" (Beetle deduped order)
        self.assertEqual(self._merged()["systems"]["psx"]["handheld"]["res"], "4x")
        # a stored NON-canonical token ('3x' renders 2x on Beetle) shows as the 2x row (index 1)
        import lib.localpolicy as lp, lib.policy as policy
        data = lp.load(policy.LOCAL)
        data["systems"]["psx"]["handheld"]["res"] = "3x"
        lp.dump(policy.LOCAL, data)
        self.assertEqual(self._row("onthego_psx", "res")["value"], 1)   # snapped to the 2x option

    def test_switch_wiiu_no_res_with_note(self):
        for ns in ("onthego_switch", "onthego_wiiu"):
            payload = call(ns + ".get")
            self.assertIsNone(self._row(ns, "res"))
            self.assertTrue(payload["note"])

    def test_enable_roundtrip(self):
        call("onthego_ps2.set", key="enable", value="1")
        self.assertTrue(self._merged()["systems"]["ps2"]["handheld"]["enabled"])

    # -- WS-C: RetroArch (handheld) pad map + hotkey combos -------------------
    def test_ra_handheld_group_in_tree(self):
        secs = call("onthego.list")["tiles"][0]["sections"]
        self.assertEqual(secs[2]["kind"], "group")
        kids = secs[2]["sections"]
        self.assertEqual({c["arg"] for c in kids if c["arg"]}, {"ra_handheld_pad", "ra_handheld_hk"})
        # WS-I: a "Per-game input" child that opens the handheld systems grid (kind ra_systems_handheld)
        pg = next(c for c in kids if c["label"] == "Per-game input")
        self.assertEqual(pg["kind"], "ra_systems_handheld")

    def test_pad_roundtrip_and_reset(self):
        import lib.ra_handheld_input as rhi
        pad = call("ra_handheld_pad.get")["groups"][0]["settings"]
        self.assertEqual(len(pad), len(onthego_cmds._PAD_ROWS) + 1)          # 14 rows + reset action
        self.assertEqual(self._row("ra_handheld_pad", "input_player1_a_btn")["value"], 0)  # A default
        call("ra_handheld_pad.set", key="input_player1_a_btn", value=1)      # A -> B
        self.assertEqual(rhi.load_pad_overrides(), {"input_player1_a_btn": "1"})
        self.assertEqual(self._row("ra_handheld_pad", "input_player1_a_btn")["value"], 1)
        call("ra_handheld_pad.set", key="input_player1_a_btn", value=0)      # back to default -> drop
        self.assertEqual(rhi.load_pad_overrides(), {})
        call("ra_handheld_pad.set", key="input_player1_b_btn", value=2)
        call("ra_handheld_pad.reset")
        self.assertEqual(rhi.load_pad_overrides(), {})

    def test_pad_rejects_unknown_key(self):
        with self.assertRaises(rpc.RpcError):
            call("ra_handheld_pad.set", key="input_player1_bogus_btn", value=1)

    def test_pad_override_merges_into_handheld_values(self):
        import lib.ra_handheld_input as rhi
        call("ra_handheld_pad.set", key="input_player1_a_btn", value=1)      # A -> B (token "1")
        self.assertEqual(rhi._handheld_values({})["input_player1_a_btn"], "1")
        self.assertEqual(rhi._handheld_values({})["input_player1_b_btn"], "1")   # untouched default

    def test_hk_default_and_roundtrip(self):
        self.assertEqual(self._row("ra_handheld_hk", "modifier_btn")["value"],
                         onthego_cmds._DECK_BTN_TOKENS.index("8"))           # R3 default
        self.assertEqual(self._row("ra_handheld_hk", "slowmotion_axis")["value"],
                         onthego_cmds._DECK_AXIS_TOKENS.index("+5"))         # R2 trigger default
        call("ra_handheld_hk.set", key="modifier_btn", value=0)             # -> token "0" (A)
        self.assertEqual(self._merged()["handheld"]["retroarch"]["modifier_btn"], "0")
        self.assertEqual(self._row("ra_handheld_hk", "modifier_btn")["value"], 0)
        call("ra_handheld_hk.set", key="slowmotion_axis", value=0)          # -> "+4" (L2)
        self.assertEqual(self._merged()["handheld"]["retroarch"]["slowmotion_axis"], "+4")

    def test_hk_reset(self):
        call("ra_handheld_hk.set", key="rewind_btn", value=0)
        self.assertEqual(self._row("ra_handheld_hk", "rewind_btn")["value"], 0)   # overridden to A
        call("ra_handheld_hk.reset")                        # drops the LOCAL override -> base default
        self.assertEqual(self._row("ra_handheld_hk", "rewind_btn")["value"],
                         onthego_cmds._DECK_BTN_TOKENS.index("9"))           # back to L1 default

    # -- WS-G: handheld quit combo (standalones) + the RA quit hotkey row -----
    def test_quit_combo_section_in_tree(self):
        secs = call("onthego.list")["tiles"][0]["sections"]
        q = next((s for s in secs if s["label"] == "Quit combo"), None)
        self.assertIsNotNone(q)
        self.assertEqual((q["kind"], q["arg"]), ("settings", "quit_handheld"))

    def test_quit_handheld_defaults_select_start(self):
        self.assertEqual(self._row("quit_handheld", "btn1")["value"],
                         onthego_cmds._DECK_EVDEV_CODES.index(314))   # Select
        self.assertEqual(self._row("quit_handheld", "btn2")["value"],
                         onthego_cmds._DECK_EVDEV_CODES.index(315))   # Start

    def test_quit_handheld_roundtrip_and_reset(self):
        call("quit_handheld.set", key="btn1", value=onthego_cmds._DECK_EVDEV_CODES.index(317))  # L3
        call("quit_handheld.set", key="btn2", value=onthego_cmds._DECK_EVDEV_CODES.index(318))  # R3
        self.assertEqual(self._merged()["quit_combo"]["handheld"]["buttons"], [317, 318])
        call("quit_handheld.set", key="hold_sec", value="3")
        self.assertEqual(self._merged()["quit_combo"]["handheld"]["hold_sec"], 3)
        call("quit_handheld.reset")                              # -> falls back to the docked combo
        self.assertNotIn("buttons", self._merged().get("quit_combo", {}).get("handheld", {}))

    def test_quit_handheld_rejects_unknown_key(self):
        with self.assertRaises(rpc.RpcError):
            call("quit_handheld.set", key="btn3", value=0)

    def test_quit_buttons_guards_corrupt_value(self):   # a hand-edited/corrupt buttons -> the default
        self.assertEqual(onthego_cmds._quit_buttons({"buttons": ["A", 5]}),
                         list(onthego_cmds._QUIT_DEFAULT))
        self.assertEqual(onthego_cmds._quit_buttons({"buttons": [317]}),   # short list padded to 2
                         [317, onthego_cmds._QUIT_DEFAULT[1]])

    def test_hk_page_has_quit_row(self):
        self.assertEqual(self._row("ra_handheld_hk", "quit_btn")["value"],
                         onthego_cmds._DECK_BTN_TOKENS.index("6"))   # default Start (+ modifier)
        call("ra_handheld_hk.set", key="quit_btn", value=0)         # -> A (token "0")
        self.assertEqual(self._merged()["handheld"]["retroarch"]["quit_btn"], "0")


if __name__ == "__main__":
    unittest.main()
