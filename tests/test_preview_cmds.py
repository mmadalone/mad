"""Tests for preview.* (lib.madsrv.preview_cmds) — the would-route Preview page.

This module had ZERO tests, which is how three bugs shipped and stayed:

  1. COLLECTIONS RENDERED NO ICON. _items set "art" to the system name for a system and to None
     for a collection, and _preview_all then gated the lookup on that field's truthiness — so
     console_art() was never called for a collection. The field was a value and a boolean at once.
     The "▣ " label prefix was the placeholder standing in for the icon that never resolved.
  2. gc SHOWED NO ROUTE. The dispatch tests `backend == "dolphin"`, an exact match that gc's
     `dolphin_gc` misses, so gc fell into the generic standalone branch, which resolves pads from
     backends[be]["pad_classes"] — a key dolphin_gc does not have (its routing is profile-based).
     Every gc row read "(no player pad -> unchanged)".
  3. THE PAGE WAS DOCK-BLIND. The payload was byte-for-byte identical docked vs handheld. Worse,
     the generic fallback asserted "handheld: <raw vid:pid>" with NO dock gate, so DOCKED it
     claimed a handheld fallback that was not going to happen.

All three share one cause: the Preview RE-DERIVED routing instead of asking the router. These tests
pin the fixed behaviour AND the seams, so it cannot drift back.

Pure logic — no real SDL/evdev/policy: everything is monkeypatched via mock.patch.object on
preview_cmds (it imports names at module level, so patch where they are USED).

Run:  python3 -m unittest tests.test_preview_cmds -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from lib import retroarch_cfg
from lib.madsrv import preview_cmds as pc
from tests._fakes import FakeDevice

XPORT = "1.1"


def _merged(**kw):
    base = {"systems": {}, "collections": {}, "backends": {}}
    base.update(kw)
    return base


class Items(unittest.TestCase):
    """_items(): what the page lists, and what it does NOT carry any more."""

    def _items(self, merged, collections=("Fighter",)):
        with mock.patch.object(pc, "_esde_systems", return_value=set()), \
             mock.patch.object(pc.es_systems, "load_systems", return_value={}), \
             mock.patch.object(pc.es_systems, "is_standalone", return_value=False), \
             mock.patch.object(pc.es_systems, "default_command", return_value=""), \
             mock.patch.object(pc, "backend_systems", return_value=[]), \
             mock.patch.object(pc.es_collections, "enabled_collections",
                               return_value=list(collections)):
            return pc._items(merged)

    def test_collection_carries_no_art_flag_and_no_glyph(self):
        m = _merged(collections={"Fighter": {"ports": [["DualSense"]]}})
        (it,) = self._items(m)
        self.assertEqual(it["key"], "Fighter")
        self.assertEqual(it["label"], "Fighter")     # the "▣ " placeholder is gone
        self.assertNotIn("▣", it["label"])
        # The dual-role field is gone entirely: art is resolved from `key`, for every item.
        self.assertNotIn("art", it)

    def test_collection_flags_lightgun_for_the_fallback(self):
        m = _merged(collections={"Pew": {"ports": [["X-Arcade"]], "require_sinden": True}})
        (it,) = self._items(m, collections=("Pew",))
        self.assertTrue(it["lightgun"])

    def test_system_carries_no_art_flag_either(self):
        m = _merged(systems={"snes": {"ports": [["DualSense"]]}})
        its = self._items(m, collections=())
        self.assertEqual([i["key"] for i in its], ["snes"])
        self.assertNotIn("art", its[0])


class Art(unittest.TestCase):
    """Art resolution: EVERY item, systems and collections alike, with a fallback."""

    def _routes(self, art_hits: dict):
        """Run _preview_all's art loop with console_art stubbed to `art_hits`."""
        items = [{"key": "snes", "label": "snes", "kind": "system"},
                 {"key": "Fighter", "label": "Fighter", "kind": "collection",
                  "lightgun": False},
                 {"key": "Pew", "label": "Pew", "kind": "collection", "lightgun": True}]
        sysmod = "lib.madsrv.systems_cmds"
        with mock.patch.object(pc, "_items", return_value=items), \
             mock.patch(f"{sysmod}.console_art", side_effect=lambda k: art_hits.get(k)), \
             mock.patch(f"{sysmod}.resolve_art",
                        side_effect=lambda names: "GUN.png" if "icons/lightgun.png" in names
                        else "PAD.png"), \
             mock.patch(f"{sysmod}.device_icon_path", return_value=""), \
             mock.patch.object(pc, "load_merged", return_value=_merged()), \
             mock.patch.object(pc, "load_policy", return_value={}), \
             mock.patch.object(pc, "xarcade_port", return_value=XPORT), \
             mock.patch.object(pc.dv, "enumerate_devices", return_value=[]), \
             mock.patch.object(pc.dv, "sdl_devices", return_value=[]), \
             mock.patch.object(pc.dv, "detect_sinden_mouse_indices",
                               return_value=(None, None, False)), \
             mock.patch.object(pc, "_devices_wiimotes", return_value={"count": 0}), \
             mock.patch.object(pc, "_route_one", return_value={"kind": "text", "text": "x"}), \
             mock.patch.object(pc, "_handheld", return_value=False):
            return {r["key"]: r["art"] for r in pc._preview_all({})["routes"]}

    def test_collections_get_art_not_none(self):
        # THE BUG: this returned None for every collection because the lookup was gated on a
        # field _items had just set to None.
        art = self._routes({"snes": "S.png", "Fighter": "F.png", "Pew": "P.png"})
        self.assertEqual(art, {"snes": "S.png", "Fighter": "F.png", "Pew": "P.png"})

    def test_fallback_when_the_theme_has_no_console_png(self):
        # Un-gating ALONE would look fine on this rig (every collection happens to have a theme
        # dir). A name with no matching dir needs the fallback, and a lightgun one gets the gun.
        art = self._routes({})
        self.assertEqual(art, {"snes": "PAD.png", "Fighter": "PAD.png", "Pew": "GUN.png"})

    def test_systems_get_the_fallback_too(self):
        art = self._routes({"Fighter": "F.png", "Pew": "P.png"})
        self.assertEqual(art["snes"], "PAD.png")   # a system whose theme dir lacks console.png


class GcRoute(unittest.TestCase):
    """gc asks the router (dolphin_gc_dock.plan) instead of re-deriving."""

    def _route(self, plan, device="DualSense Wireless Controller", index=None):
        merged = _merged(systems={"gc": {"backend": "dolphin_gc"}},
                         backends={"dolphin_gc": {"undocked_profile": "Steamdeck"}})
        import lib.dolphin_gc_dock as dk
        import lib.dolphin_gc_pads as gp
        import lib.dolphin_profiles as dp
        idx = ({}, {"DualSense Wireless Controller": "054c:0ce6",
                    "PS4 Controller": "054c:09cc",
                    "Nintendo Wii Remote Pro Controller": "057e:0330"}) if index is None else index
        with mock.patch.object(dk, "plan", return_value=plan), \
             mock.patch.object(gp, "_connected_index", return_value=idx), \
             mock.patch.object(dp, "profile_device", return_value=device):
            return pc._route_one("gc", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))

    def test_docked_renders_the_planned_ports(self):
        # THE BUG: gc used to fall through to the generic pad_classes branch and render
        # "(no player pad -> unchanged)" no matter what was plugged in.
        r = self._route({"mode": "docked", "assign": [(1, "GC WiiU 1"), (2, "GC Dualsense 1")],
                         "note": ""})
        self.assertEqual(r["kind"], "pads")
        self.assertEqual([(x["slot"], x["text"]) for x in r["rows"]],
                         [("P1", "GC WiiU 1"), ("P2", "GC Dualsense 1")])

    def test_handheld_renders_the_undocked_profile(self):
        r = self._route({"mode": "handheld", "assign": [(1, "Steamdeck")], "note": ""})
        self.assertEqual([(x["slot"], x["text"]) for x in r["rows"]], [("P1", "Steamdeck")])

    def test_empty_plan_explains_itself(self):
        r = self._route({"mode": "docked", "assign": [], "note": "normal mapping"})
        self.assertEqual(r["kind"], "text")
        self.assertIn("normal mapping", r["text"])

    # --- the icon HINT. Row art is resolved from the label vocabulary (pad_labels), NOT from
    # Dolphin's raw Device string. Shipping the Device string made "GC DS4 1/2" render the generic
    # pad ("PS4 Controller" matches no art) while "GC Dualsense 1" worked by LUCK, because its
    # first word happens to match dualsense.png. Miquel caught it on screen.

    def test_ds4_profile_resolves_the_ds4_icon(self):
        r = self._route({"mode": "docked", "assign": [(1, "GC DS4 1")], "note": ""},
                        device="PS4 Controller")
        row = r["rows"][0]
        self.assertEqual(row["icon"], "DualShock 4")        # NOT the raw "PS4 Controller"
        self.assertEqual(row["text"], "GC DS4 1")           # the profile name stays the answer

    def test_wiiu_pro_profile_resolves_its_icon_too(self):
        # Same latent break, same fix: the raw Device string starts with "Nintendo".
        r = self._route({"mode": "docked", "assign": [(1, "GC WiiU Pro 1")], "note": ""},
                        device="Nintendo Wii Remote Pro Controller")
        self.assertEqual(r["rows"][0]["icon"], "Wii U Pro")

    def test_icon_hint_is_omitted_when_the_pad_is_absent(self):
        # A profile whose device is not connected: no hint rather than a wrong one. _row_icon_name
        # then falls back to the profile name, which is the honest last resort.
        r = self._route({"mode": "docked", "assign": [(1, "GC DS4 1")], "note": ""},
                        device="Some Unplugged Pad")
        self.assertNotIn("icon", r["rows"][0])

    def test_an_xarcade_profile_name_still_wins_over_the_hint(self):
        # 045e:02a1 is shared with a real Xbox 360 pad, so the profile NAME is the reliable
        # X-Arcade signal and must beat the vid:pid-derived hint. Guards _row_icon_name's contract.
        r = self._route({"mode": "docked", "assign": [(1, "GC X-Arcade 1")], "note": ""},
                        device="Nintendo Wii Remote Pro Controller")
        self.assertEqual(pc._row_icon_name(r["rows"][0]), "GC X-Arcade 1")

    def test_gc_never_reaches_the_generic_pad_classes_branch(self):
        # The regression guard for the ROOT CAUSE. dolphin_gc has no pad_classes; if this branch
        # were ever reordered below the generic `be and be != "retroarch"` fallthrough, gc would
        # silently go back to rendering "(no player pad)". Prove the router's plan is what runs.
        r = self._route({"mode": "docked", "assign": [(1, "GC WiiU 1")], "note": ""})
        self.assertEqual(r["kind"], "pads")
        self.assertNotIn("no player pad", str(r))


class DockAwareness(unittest.TestCase):
    """The page must answer FOR the context the Deck is actually in."""

    def _generic(self, handheld):
        merged = _merged(systems={"xbox": {"backend": "xemu"}},
                         backends={"xemu": {"pad_classes": [], "handheld_class": "28de:1205"}})
        with mock.patch.object(pc, "_handheld", return_value=handheld), \
             mock.patch.object(pc, "pad_label", return_value="Steam Deck"):
            return pc._route_one("xbox", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))["text"]

    def test_docked_does_not_claim_a_handheld_fallback(self):
        # THE BUG, reproduced live before the fix: DOCKED, xbox returned
        # "(no player pad -> handheld: 28de:1205)". No dock gate at all, and a raw vid:pid.
        t = self._generic(handheld=False)
        self.assertNotIn("handheld", t)
        self.assertNotIn("28de", t)

    def test_handheld_names_the_pad_not_a_raw_vidpid(self):
        t = self._generic(handheld=True)
        self.assertIn("handheld", t)
        self.assertIn("Steam Deck", t)
        self.assertNotIn("28de", t)

    def _ra(self, handheld):
        merged = _merged(systems={"snes": {"ports": [["DualSense"]]}})
        with mock.patch.object(pc, "_handheld", return_value=handheld), \
             mock.patch.object(pc, "resolve_policy",
                               return_value={"ports": [["DualSense"]]}), \
             mock.patch.object(pc, "resolve_pins", return_value=({}, set())), \
             mock.patch.object(pc, "resolve_ports", return_value={}):   # nothing reservable
            return pc._route_one("snes", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))["text"]

    def test_handheld_no_external_pad_tells_the_truth(self):
        # THE BUG: resolve_ports EXCLUDES the Deck's Steam-virtual pad (28de:11ff, the only form
        # the Deck takes in Game Mode), so it returns {} and no reservation is written -- but
        # RetroArch seats the Deck itself and the game plays. The page said "no matching pad
        # connected", a confidently wrong answer.
        t = self._ra(handheld=True)
        self.assertNotIn("no matching pad", t)
        self.assertIn("Deck", t)
        self.assertIn("sdl2", t)          # names the driver that makes the bind numbers mean anything

    def test_docked_no_pad_still_says_so_plainly(self):
        self.assertIn("no matching pad", self._ra(handheld=False))


class PlannedJoypadDriver(unittest.TestCase):
    """retroarch_cfg.planned_joypad_driver: ONE decision, shared by the router and the Preview."""

    def test_docked_is_udev(self):
        self.assertEqual(retroarch_cfg.planned_joypad_driver({}, False), "udev")

    def test_handheld_defaults_to_sdl2(self):
        self.assertEqual(retroarch_cfg.planned_joypad_driver({}, True), "sdl2")

    def test_handheld_honours_the_policy_override(self):
        pol = {"handheld": {"retroarch": {"joypad_driver": "udev"}}}
        self.assertEqual(retroarch_cfg.planned_joypad_driver(pol, True), "udev")

    def test_docked_ignores_the_handheld_override(self):
        pol = {"handheld": {"retroarch": {"joypad_driver": "sdl2"}}}
        self.assertEqual(retroarch_cfg.planned_joypad_driver(pol, False), "udev")

    def test_tolerates_a_malformed_policy(self):
        for pol in ({"handheld": "nonsense"}, {"handheld": {"retroarch": "nope"}}, {}):
            self.assertEqual(retroarch_cfg.planned_joypad_driver(pol, True), "sdl2")


class EsdeSystemsVisibleGate(unittest.TestCase):
    """_esde_systems() keys off VISIBLE GAMES, not the gamelist FILE. An emptied
    `<gameList/>` stub (e.g. an xbox system whose ROMs are gone but a leftover
    gamelist.xml remains) is hidden by ES-DE and must not count — checking only
    that the file existed let it leak into the would-route preview."""

    def test_empty_stub_excluded_real_system_kept(self):
        import tempfile
        from pathlib import Path
        from lib import es_gamelist, es_systems, esde_settings
        with tempfile.TemporaryDirectory() as td:
            gl = Path(td) / "gamelists"
            (gl / "hasgames").mkdir(parents=True)
            (gl / "hasgames" / "gamelist.xml").write_text(
                '<?xml version="1.0"?>\n<gameList>\n'
                '  <game><path>./a.iso</path><name>A Game</name></game>\n'
                '</gameList>\n', encoding="utf-8")
            (gl / "empty").mkdir(parents=True)     # the xbox-stub shape
            (gl / "empty" / "gamelist.xml").write_text(
                '<?xml version="1.0"?>\n<gameList />\n', encoding="utf-8")
            (gl / "nofile").mkdir(parents=True)    # a dir with no gamelist.xml (model3 shape)
            es_gamelist.records.cache_clear()
            try:
                with mock.patch.object(esde_settings, "APPDATA", Path(td)), \
                     mock.patch.object(es_systems, "GAMELISTS", gl):
                    got = pc._esde_systems()
            finally:
                es_gamelist.records.cache_clear()
        self.assertEqual(got, {"hasgames"})        # empty + nofile both dropped


class PhantomSystemsGate(unittest.TestCase):
    """A system configured in the policy but with NO games in ES-DE must not
    appear in the would-route list. naomi2 (ports in the policy, no gamelist)
    used to leak because loop 2 — RA systems with reserved ports — had no gate."""

    def _keys(self, merged, esde):
        with mock.patch.object(pc, "_esde_systems", return_value=set(esde)), \
             mock.patch.object(pc.es_systems, "load_systems", return_value={}), \
             mock.patch.object(pc.es_systems, "is_standalone", return_value=False), \
             mock.patch.object(pc.es_systems, "default_command", return_value=""), \
             mock.patch.object(pc, "backend_systems", return_value=[]), \
             mock.patch.object(pc.es_collections, "enabled_collections", return_value=[]):
            return [it["key"] for it in pc._items(merged)]

    def test_ported_system_with_no_games_is_dropped(self):
        m = _merged(systems={"snes": {"ports": [["DualSense"]]},
                             "naomi2": {"ports": [["DualSense"]]}})
        self.assertEqual(self._keys(m, esde={"snes"}), ["snes"])

    def test_gate_is_fail_open_when_gamelists_are_unreadable(self):
        # esde empty (gamelists dir missing) => show everything, never hide all.
        m = _merged(systems={"naomi2": {"ports": [["DualSense"]]}})
        self.assertEqual(self._keys(m, esde=set()), ["naomi2"])


if __name__ == "__main__":
    unittest.main()
