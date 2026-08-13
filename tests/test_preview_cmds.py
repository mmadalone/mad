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

import os
import unittest
from pathlib import Path
from unittest import mock

from lib import retroarch_cfg
from lib import sdl_filter
from lib.devices import Device, SdlDevice
from lib.devices import port_of as dv_port
from lib.madsrv import preview_cmds as pc
from tests._ci import skip_on_ci
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

    def test_handheld_deck_identity_is_port1_only(self):
        # Multi-seat 2026-08-04: handheld ports 2-4 are EXTERNAL pads and must resolve their
        # own vid:pid like docked rows — only Port 1 (the Deck itself) gets the Steam-virtual
        # identity stamped (review fix: every port used to get 28de:11ff).
        r = self._route({"mode": "handheld",
                         "assign": [(1, "Steamdeck"), (2, "GC DS4 2")], "note": ""},
                        device="PS4 Controller")
        rows = {x["slot"]: x for x in r["rows"]}
        self.assertEqual(rows["P1"]["vidpid"], "28de:11ff")
        self.assertEqual(rows["P2"]["vidpid"], "054c:09cc")     # the external pad, not the Deck

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


class WiiRoute(unittest.TestCase):
    """The no-bar wii branch renders dolphin_wii_source.plan()'s seat maps as ROWS.

    It used to render one sentence, and the docked half of that sentence was the literal
    words "Classic -> pads-to-players order": the name of a mechanism where the reader
    expected an answer, and 622 pixels wide in a 614 pixel column, so it also
    ran off the right edge of the screen (reported on-screen 2026-08-13 with a screenshot).
    plan() now resolves the docked rail to real profiles and this renders short rows.

    Pinned so a future plan()-shape change cannot silently degrade into the generic
    'no DolphinBar' fallback (the branch swallows exceptions).
    """

    def _route(self, plan):
        merged = _merged(systems={"wii": {"backend": "dolphin"}})
        import lib.dolphin_wii_source as ws
        with mock.patch.object(pc.dv, "dolphinbar_present", return_value=False), \
             mock.patch.object(ws, "plan", return_value=plan):
            return pc._route_one("wii", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))

    @staticmethod
    def _rows(r):
        return [(row.get("slot"), row.get("text")) for row in r.get("rows", [])]

    def test_docked_names_the_profile_each_player_gets(self):
        r = self._route({"mode": "docked",
                         "styles": {"sideways": {1: "SideP", 2: "SideP2"}, "nunchuk": {}},
                         "cc": {1: "DS 1 = classic controller"}})
        self.assertEqual(r["kind"], "pads")
        self.assertEqual(self._rows(r), [
            ("⚠", "no DolphinBar, docked"),
            ("", "Classic Controller games"),
            ("P1", "DS 1 = classic controller"),
            ("", "Sideways games"),
            ("P1", "SideP"),
            ("P2", "SideP2"),
        ])
        self.assertNotIn("unchuk", str(r))                  # empty seat map stays silent

    def test_every_row_fits_the_column(self):
        """The clip budget, measured off the panel rather than guessed: the right column is
        48 percent of the viewport and text neither wraps nor truncates -- it runs off the
        edge. Rendered in the panel's own font that is a 38-character profile name on a seat
        row (39 clips); 41 characters is the conservative rule of thumb used here.

        The REAL row text is a profile name the owner types himself, so the fixtures below
        cannot catch a regression on their own -- test_the_owners_real_profile_names_fit does
        that half.
        """
        r = self._route({"mode": "docked", "styles": {},
                         "cc": {1: "DS 1 = classic controller", 2: "DS4 2 = classic pad"}})
        for slot, text in self._rows(r):
            self.assertLessEqual(len(f"  {slot}  {text}"), 41, (slot, text))

    @skip_on_ci        # reads this Deck's own stored Wii profile names
    def test_the_owners_real_profile_names_fit(self):
        # Nothing truncates, so a long name would simply run off the screen with no test
        # anywhere to notice. Longest on this Deck today is 31 characters.
        from lib.madsrv import dolphin_wii_pads_cmds as prefs
        names = list(prefs.priority() or prefs.docked_default() or [])
        self.assertTrue(names, "no stored Wii profile priority to check")
        for n in names:
            self.assertLessEqual(len(f"  P1  {n}"), 41, f"profile name too long to display: {n}")

    def test_headings_carry_no_pad_icon(self):
        r = self._route({"mode": "docked", "styles": {}, "cc": {1: "P"}})
        heads = [row for row in r["rows"] if row.get("noicon")]
        self.assertEqual(len(heads), 2)                     # the warning and the group title

    def test_handheld_renders_cc_seat_dict(self):
        r = self._route({"mode": "handheld",
                         "styles": {"sideways": {}, "nunchuk": {1: "NunP"}},
                         "cc": {1: "CCHand", 2: "Pad2"}})
        self.assertEqual(self._rows(r), [
            ("⚠", "no DolphinBar, handheld"),
            ("", "Classic Controller games"),
            ("P1", "CCHand"),
            ("P2", "Pad2"),
            ("", "Nunchuk games"),
            ("P1", "NunP"),
        ])

    def test_handheld_seat_one_is_the_decks_own_pad(self):
        # Its profile Device name never matches the live evdev name in Game Mode, so the
        # identity is asserted rather than joined -- same rule as the GameCube row.
        r = self._route({"mode": "handheld", "styles": {}, "cc": {1: "CCHand"}})
        self.assertEqual(r["rows"][2]["vidpid"], "28de:11ff")

    def test_nothing_seated_says_so_without_claiming_nothing_is_written(self):
        # "Wii config unchanged" was the wording here until 2026-08-14 and it is FALSE in the
        # exact state that prints it (this Deck's state today): a non-CC-capable game then
        # falls through to real-remote mode, which writes Source=2/0 into WiimoteNew.ini with
        # no backup taken, so the game-end restore reverts nothing. Permanent, not transient.
        r = self._route({"mode": "docked", "styles": {}, "cc": {}})
        self.assertEqual(r["kind"], "text")
        self.assertNotIn("unchanged", r["text"])
        self.assertIn("no DolphinBar", r["text"])
        self.assertLessEqual(len("  " + r["text"]), 41)

    def test_plan_failure_falls_back_gracefully(self):
        merged = _merged(systems={"wii": {"backend": "dolphin"}})
        import lib.dolphin_wii_source as ws
        with mock.patch.object(pc.dv, "dolphinbar_present", return_value=False), \
             mock.patch.object(ws, "plan", side_effect=RuntimeError("boom")):
            r = pc._route_one("wii", "system", merged, {}, XPORT, [], [], 0,
                              sinden_idx=(None, None, False))
        self.assertEqual(r["kind"], "text")
        self.assertIn("no DolphinBar", r["text"])


class DockAwareness(unittest.TestCase):
    """The page must answer FOR the context the Deck is actually in."""

    def _generic(self, handheld):
        merged = _merged(systems={"xbox": {"backend": "xemu"}},
                         backends={"xemu": {"pad_classes": [], "handheld_class": "28de:1205"}})
        with mock.patch.object(pc, "_handheld", return_value=handheld), \
             mock.patch.object(pc, "pad_label", return_value="Steam Deck"):
            return pc._route_one("xbox", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))

    def test_docked_does_not_claim_a_handheld_fallback(self):
        # THE BUG, reproduced live before the fix: DOCKED, xbox returned
        # "(no player pad -> handheld: 28de:1205)". No dock gate at all, and a raw vid:pid.
        r = self._generic(handheld=False)
        self.assertEqual(r["kind"], "text")
        self.assertNotIn("handheld", r["text"])
        self.assertNotIn("28de", r["text"])

    def test_handheld_fallback_is_a_real_p1_row(self):
        # The fallback IS the seat: one P1 pad row with the pad's identity, not an
        # explanatory text line (user request 2026-07-30).
        r = self._generic(handheld=True)
        self.assertEqual(r["kind"], "pads")
        (row,) = r["rows"]
        self.assertEqual((row["slot"], row["text"], row["vidpid"]),
                         ("P1", "Steam Deck", "28de:1205"))

    def test_handheld_profile_fallback_carries_the_deck_identity(self):
        # A PROFILE-name fallback (e.g. cemu-style "Steamdeck") still seats the Deck's
        # built-in pad, so the row's vidpid is the Steam-virtual class for the icon.
        merged = _merged(systems={"xbox": {"backend": "xemu"}},
                         backends={"xemu": {"pad_classes": [],
                                            "handheld_profile": "Steamdeck"}})
        with mock.patch.object(pc, "_handheld", return_value=True):
            r = pc._route_one("xbox", "system", merged, {}, XPORT, [], [], 0,
                              sinden_idx=(None, None, False))
        (row,) = r["rows"]
        self.assertEqual((row["slot"], row["text"], row["vidpid"]),
                         ("P1", "Steamdeck", "28de:11ff"))

    def _ra(self, handheld):
        merged = _merged(systems={"snes": {"ports": [["DualSense"]]}})
        with mock.patch.object(pc, "_handheld", return_value=handheld), \
             mock.patch.object(pc, "resolve_policy",
                               return_value={"ports": [["DualSense"]]}), \
             mock.patch.object(pc, "resolve_pins", return_value=({}, set())), \
             mock.patch.object(pc, "resolve_ports", return_value={}):   # nothing reservable
            return pc._route_one("snes", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))

    def test_handheld_no_external_pad_is_a_p1_deck_row(self):
        # resolve_ports EXCLUDES the Deck's Steam-virtual pad, the router writes no
        # reservation, and RetroArch seats the Deck itself - the OUTCOME is P1 = Deck,
        # so the page says exactly that as a pad row (user request 2026-07-30; the
        # not-reserved/driver internals stay in router.log).
        r = self._ra(handheld=True)
        self.assertEqual(r["kind"], "pads")
        (row,) = r["rows"]
        self.assertEqual(row["slot"], "P1")
        self.assertEqual(row["vidpid"], "28de:11ff")
        self.assertEqual(row["text"], "Steam Deck (SI)")   # the real pad_label vocabulary

    def test_docked_no_pad_still_says_so_plainly(self):
        r = self._ra(handheld=False)
        self.assertEqual(r["kind"], "text")
        self.assertIn("no matching pad", r["text"])


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


class EdenRpcs3LiveRoute(unittest.TestCase):
    """eden/rpcs3 mirror switch_bind.bind() gate-for-gate (LIVE truth) instead of
    parsing the emulator's stored config. The stored file is the LAST bind's seats
    (disconnected pads included) — bind() rewrites it from the live pads at every
    launch, so parsing it previewed the past. These tests assert the preview calls
    the launch path's own resolver, not a re-derived result."""

    def _route(self, be, hands_off=False, target_ok=True, chosen=(),
               preview=("text", "stored-config")):
        import lib.switch_bind as sb
        from lib.madsrv import pads_cmds
        merged = _merged(systems={be: {"backend": be}}, backends={be: {}})
        fake_target = mock.Mock()
        fake_target.is_file.return_value = target_ok
        with mock.patch.object(pads_cmds, "_hands_off", return_value=hands_off), \
             mock.patch.object(sb, "_target", return_value=fake_target), \
             mock.patch.object(sb, "_resolve_pads", return_value=list(chosen)) as rp, \
             mock.patch.object(pc, "evdev_by_sdl_index", return_value={}), \
             mock.patch.object(pc, "standalone_profile_preview",
                               return_value=preview) as sp:
            r = pc._route_one(be, "system", merged, {}, XPORT, [], [], 0,
                              sinden_idx=(None, None, False))
        return r, rp, sp

    def test_managed_backend_asks_the_launch_resolver(self):
        from tests._fakes import sd
        for be in ("eden", "rpcs3"):
            r, rp, sp = self._route(be, chosen=[sd(0, "054c:0ce6", "g", "DualSense")])
            rp.assert_called_once_with(be, quiet=True)   # quiet: no phantom router.log lines
            sp.assert_not_called()                       # the stored-config path is DEAD here
            self.assertEqual(r["kind"], "pads")
            self.assertEqual((r["rows"][0]["slot"], r["rows"][0]["text"],
                              r["rows"][0]["vidpid"]),
                             ("P1", "DualSense", "054c:0ce6"))

    def test_hands_off_keeps_the_stored_config_preview(self):
        # Hands-off means the emulator's own config IS the launch truth — the ONLY
        # case where the stored-config preview is still the honest answer.
        r, rp, sp = self._route("eden", hands_off=True)
        sp.assert_called_once()
        rp.assert_not_called()
        self.assertEqual(r, {"kind": "text", "text": "stored-config"})

    def test_missing_config_file_says_untouched(self):
        # bind() returns before resolving pads when the target config is absent.
        r, rp, _ = self._route("rpcs3", target_ok=False)
        self.assertEqual(r["kind"], "text")
        self.assertIn("no config file", r["text"])
        rp.assert_not_called()

    def test_no_pads_is_honest(self):
        r, _, _ = self._route("eden", chosen=[])
        self.assertEqual(r["kind"], "text")
        self.assertIn("no player pad", r["text"])

    def test_handheld_deck_fallback_carries_the_deck_identity(self):
        # _resolve_pads re-admits the Deck virtual pad (28de:11ff) handheld with no
        # externals; the row must carry that vidpid so the Steam Deck icon resolves.
        from tests._fakes import sd
        r, _, _ = self._route("eden",
                              chosen=[sd(0, "28de:11ff", "g", "Steam Deck Controller")])
        self.assertEqual((r["rows"][0]["text"], r["rows"][0]["vidpid"]),
                         ("Steam Deck (SI)", "28de:11ff"))


class CemuLiveRoute(unittest.TestCase):
    """cemu mirrors the game-start hook's decision (cemu_seat._seat_plan /
    cemu_input_dock._gate) READ-ONLY, instead of parsing controller{0..7}.xml
    (the RESTING profiles: seats from games past, disconnected pads included)."""

    def _route(self, seating, plan=(), gate=(None, "docked -> no swap"),
               context="handheld"):
        import tempfile

        import lib.cemu_input_dock as cid
        import lib.cemu_seat as cs
        import lib.handheld_input as hh
        merged = _merged(systems={"wiiu": {"backend": "cemu"}},
                         backends={"cemu": {"seating_enabled": seating}})
        boom = AssertionError("preview must never call apply() — it WRITES")
        # HERMETIC config dir: the route mirrors apply()'s gates (config dir must
        # exist, each planned stem's profile xml must exist) against the REAL
        # filesystem — a sandbox dir with the plan's profiles keeps this CI-safe
        # (the runner has no ~/.config/Cemu; the Deck does, which hid this).
        with tempfile.TemporaryDirectory(prefix="cemu-prof-") as td:
            cfg_dir = Path(td)
            for row in plan:
                (cfg_dir / f"{row[1]}.xml").write_text("<emulated_controller/>")
            with mock.patch.object(hh, "context", return_value=context), \
                 mock.patch.object(cs, "_config_dir", return_value=cfg_dir), \
                 mock.patch.object(cs, "_seat_plan", return_value=(list(plan), [])) as sp, \
                 mock.patch.object(cid, "_gate", return_value=gate) as gt, \
                 mock.patch.object(cs, "apply", side_effect=boom), \
                 mock.patch.object(cid, "apply", side_effect=boom):
                r = pc._route_one("wiiu", "system", merged, {}, XPORT, [], [], 0,
                                  sinden_idx=(None, None, False))
        return r, sp, gt

    def test_seating_enabled_shows_the_hooks_own_plan(self):
        from tests._fakes import dev
        ds4 = dev("054c:09cc", "/dev/input/event5", "Sony DS4")
        r, sp, gt = self._route(True, plan=[(0, "Steamdeck", None, True),
                                            (1, "DS4 1", ds4, False)])
        sp.assert_called_once()
        gt.assert_not_called()
        self.assertEqual([(x["slot"], x["text"], x["vidpid"]) for x in r["rows"]],
                         [("C1", "Steamdeck", "28de:11ff"),      # Deck GamePad slot
                          ("C2", "DS4 1", "054c:09cc")])
        self.assertEqual(r["rows"][1]["icon"], "DualShock 4")    # pad_label hint, not raw name

    def test_empty_plan_reports_nothing_assigned(self):
        # Exactly apply()'s outcome — and how configured-but-DISCONNECTED profiles
        # disappear from the Preview (live truth only; Controllers pages keep them).
        r, _, _ = self._route(True, plan=[])
        self.assertEqual(r["kind"], "text")
        self.assertIn("nothing assigned for handheld", r["text"])

    def test_seating_disabled_swap_is_one_deck_row(self):
        from pathlib import Path
        r, sp, gt = self._route(False,
                                gate=(Path("/x/Steamdeck.xml"), "handheld -> Steamdeck"))
        gt.assert_called_once()
        sp.assert_not_called()
        self.assertEqual([(x["slot"], x["text"], x["vidpid"]) for x in r["rows"]],
                         [("C1", "Steamdeck", "28de:11ff")])

    def test_seating_disabled_no_swap_reports_the_gates_reason(self):
        r, _, _ = self._route(False, gate=(None, "docked -> no swap"))
        self.assertEqual(r["kind"], "text")
        self.assertIn("docked -> no swap", r["text"])


class GcRowIdentity(unittest.TestCase):
    """gc rows carry a device identity: handheld = the Deck (asserted, the profile's
    Device string never matches the live evdev name in Game Mode), docked = the
    resolved pad's vid:pid."""

    def _route(self, plan, device="DualSense Wireless Controller"):
        import lib.dolphin_gc_dock as dk
        import lib.dolphin_gc_pads as gp
        import lib.dolphin_profiles as dp
        merged = _merged(systems={"gc": {"backend": "dolphin_gc"}},
                         backends={"dolphin_gc": {}})
        idx = ({}, {"DualSense Wireless Controller": "054c:0ce6"})
        with mock.patch.object(dk, "plan", return_value=plan), \
             mock.patch.object(gp, "_connected_index", return_value=idx), \
             mock.patch.object(dp, "profile_device", return_value=device):
            return pc._route_one("gc", "system", merged, {}, XPORT, [], [], 0,
                                 sinden_idx=(None, None, False))

    def test_handheld_row_is_the_deck(self):
        r = self._route({"mode": "handheld", "assign": [(1, "Steamdeck")], "note": ""})
        self.assertEqual(r["rows"][0]["vidpid"], "28de:11ff")
        self.assertNotIn("icon", r["rows"][0])   # identity via vidpid, no name-join guess

    def test_docked_row_carries_the_pads_vidpid(self):
        r = self._route({"mode": "docked", "assign": [(1, "GC Dualsense 1")], "note": ""})
        self.assertEqual(r["rows"][0]["vidpid"], "054c:0ce6")
        self.assertEqual(r["rows"][0]["icon"], "DualSense")


class TokenIcons(unittest.TestCase):
    """device_icon_path resolves profile-string rows via the name-token table and
    the Deck's vid:pid — against the REAL art dirs, same as test_preview.Icons."""

    @staticmethod
    def _stem(p):
        return (p or "").rsplit("/", 1)[-1]

    def test_profile_stem_steamdeck_resolves(self):
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertEqual(self._stem(device_icon_path("Steamdeck")), "steamdeck.png")

    def test_ds4_token_beats_the_steamdeck_token(self):
        # "DS4 1 + Steamdeck" names the PAD first; table order makes ds4 win.
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertEqual(self._stem(device_icon_path("DS4 1 + Steamdeck")),
                         "dualshock.png")

    def test_profile_with_dualsense_token_resolves(self):
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertEqual(self._stem(device_icon_path("GC Dualsense 1")),
                         "dualsense.png")

    def test_deck_vidpid_resolves_the_steamdeck_icon(self):
        # The row plumbing fix: routes now pass vidpid, so the 28de:11ff override
        # finally fires ("Steam Deck (SI)" alone matches no asset).
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertEqual(self._stem(device_icon_path("Steam Deck (SI)", "28de:11ff")),
                         "steamdeck.png")

    def test_xarcade_label_still_beats_everything(self):
        from lib.madsrv.systems_cmds import device_icon_path
        self.assertEqual(self._stem(device_icon_path("X-Arcade P7", "045e:02a1")),
                         "xarcade.png")


if __name__ == "__main__":
    unittest.main()


class OpenborDeckTakeover(unittest.TestCase):
    """BUG 4 (2026-08-13): Preview claimed the Deck was P1 for OpenBOR while the launch seated the
    external pad, and the owner saw exactly that: Preview said Steam Deck, the game gave DualSense.

    Same root cause as bugs 1 to 3 above. The standalone-backend branch RE-DERIVES seating by
    sorting the SDL view on `pad_classes` order alone. `[backends.openbor]` deliberately lists the
    Deck's Game-Mode pad (28de:11ff) AHEAD of the DualSense, so that sort puts the Deck first, full
    stop. What it never applied is the KEEP-vs-TAKEOVER rule the LAUNCH applies in
    mad-openbor-pads.build_plan: docked, with a real external pad present, the Deck is not seated at
    all (sdl_filter._hide_deck_when_external, default ON docked / OFF handheld). Every other seat
    DECIDER already reads that same helper: sdl_filter twice, cemu_seat once (through its own
    one-line wrapper), the merger once. openbor.sh and mugen.sh do hardcode the Deck vid:pid
    pair into SDL_GAMECONTROLLER_IGNORE_DEVICES, but both sit on the merger path where the
    whitelist is twins-only, so they are insurance and decide no seat.
    Preview was the only one that did not, which is why it was the only one that disagreed.

    Note the Deck's identity here is the SDL/Game-Mode form 28de:11ff, NOT the physical 28de:1205:
    with Steam Input off the physical pad exposes no gamepad node in Game Mode, so the virtual pad
    is the only form the Deck's own controls take. That is why this is invisible from a desktop
    session and only shows up in the panel.

    FIXED TWICE. The first fix applied the KEEP-vs-TAKEOVER helper here, which cured this case
    and left the ORDERING re-derived -- so a second divergence surfaced the same day (see
    OpenborXarcadeOrdering below) and the branch now asks the merger outright. These cases stay
    exactly as they were: they describe an outcome, not a mechanism, and the outcome must hold
    whichever way the answer is obtained.
    """

    DECK = "28de:11ff"
    DS = "054c:0ce6"

    def _route(self, vidpids, hide_deck):
        """Preview's OpenBOR row for the given pad set. Returns [(slot, vidpid), ...].

        BOTH views are built, and they must agree: since 2026-08-13 this branch asks the
        real merger (lib.openbor_seating.build_plan), which works on EVDEV devices, while
        every other standalone still reads the SDL view. Feeding only one of them would let
        this suite pass against a branch that seats nothing.
        """
        sdl = [SdlDevice(index=i, vidpid=vp, guid="", name=n)
               for i, (vp, n) in enumerate(vidpids)]
        devs = [Device(name=n, path=f"/dev/input/event{20 + i}", is_joypad=True,
                       is_mouse=False, is_keyboard=False, js_index=i, mouse_index=None,
                       vid=int(vp.split(":")[0], 16), pid=int(vp.split(":")[1], 16))
                for i, (vp, n) in enumerate(vidpids)]
        merged = _merged(
            systems={"openbor": {"backend": "openbor"}},
            backends={"openbor": {"pad_classes": ["x-arcade", self.DECK, self.DS, "054c:09cc"],
                                  "handheld_class": self.DECK}})
        with mock.patch.object(sdl_filter, "_hide_deck_when_external", return_value=hide_deck), \
             mock.patch.object(pc, "_handheld", return_value=not hide_deck):
            r = pc._route_one("openbor", "system", merged, {}, XPORT, devs, sdl, 0,
                              sinden_idx=(None, None, False))
        return [(row.get("slot"), row.get("vidpid")) for row in r.get("rows", [])], r

    def test_docked_with_an_external_pad_the_deck_is_not_seated(self):
        # THE BUG. Docked + DualSense: the launch seats the DualSense at P1 and does not seat the
        # Deck at all (verified against mad-openbor-pads.py --probe on the live machine).
        rows, r = self._route([(self.DECK, "Microsoft X-Box 360 pad 0"),
                               (self.DS, "DualSense Wireless Controller")], hide_deck=True)
        self.assertEqual(r.get("kind"), "pads", r)
        self.assertEqual(rows, [("P1", self.DS)],
                         "docked with an external pad, Preview must not seat the Deck")

    def test_handheld_keeps_the_deck_first_which_is_the_point_of_the_toggle(self):
        # Undocked, the Deck's own pad IS your controller even with an external attached, and
        # pad_classes ranks it first on purpose. Preview must show that, not hide it.
        rows, _ = self._route([(self.DECK, "Microsoft X-Box 360 pad 0"),
                               (self.DS, "DualSense Wireless Controller")], hide_deck=False)
        self.assertEqual(rows, [("P1", self.DECK), ("P2", self.DS)])

    def test_the_deck_alone_is_still_p1_docked(self):
        # The takeover rule needs a REAL external pad to fire; the Deck on its own still plays.
        rows, _ = self._route([(self.DECK, "Microsoft X-Box 360 pad 0")], hide_deck=True)
        self.assertEqual(rows, [("P1", self.DECK)])

    def test_two_externals_are_unaffected(self):
        # No Steam-virtual pad in the view at all: the rule must be a no-op, not a re-order.
        rows, _ = self._route([(self.DS, "DualSense Wireless Controller"),
                               ("054c:09cc", "PS4 Controller")], hide_deck=True)
        self.assertEqual(rows, [("P1", self.DS), ("P2", "054c:09cc")])


class OpenborXarcadeOrdering(unittest.TestCase):
    """BUG 5 (2026-08-13, same evening): with an Xbox 360 Wireless Receiver plugged in and the
    X-Arcade cabinet unplugged, the OpenBOR row said "P1 Xbox 360" while
    `mad-openbor-pads.py --probe` on the very same machine said P1 DualSense, P2 Xbox 360.

    The cabinet is 045e:02a1 and so is a plain Xbox pad -- byte-identical on every evdev field.
    The merger has two rules for that: the IDENTIFIED cabinet's halves lead (USB interface order),
    and everything else ranks by pad_classes WITH the token removed, which drops a non-cabinet
    045e to last. Preview had neither: it expanded "x-arcade" to a bare 045e:02a1 and sorted on
    list position, and [backends.openbor] lists the token FIRST. So the one pad the user never
    ticked was announced as Player 1.

    The row now comes from lib.openbor_seating.build_plan itself, so these cases are really
    asking "did Preview ask, or did it guess again".
    """

    XA, DS, DECK = "045e:02a1", "054c:0ce6", "28de:11ff"
    CLASSES = ["x-arcade", DECK, DS, "054c:09cc"]

    def _route(self, pads, xport=XPORT):
        """pads = [(vidpid, name, phys)]; returns [(slot, vidpid), ...]."""
        sdl = [SdlDevice(index=i, vidpid=vp, guid="", name=n)
               for i, (vp, n, _ph) in enumerate(pads)]
        devs = [Device(name=n, path=f"/dev/input/event{20 + i}", is_joypad=True,
                       is_mouse=False, is_keyboard=False, js_index=i, mouse_index=None,
                       vid=int(vp.split(":")[0], 16), pid=int(vp.split(":")[1], 16), phys=ph)
                for i, (vp, n, ph) in enumerate(pads)]
        merged = _merged(systems={"openbor": {"backend": "openbor"}},
                         backends={"openbor": {"pad_classes": self.CLASSES,
                                               "handheld_class": self.DECK}})
        with mock.patch.object(sdl_filter, "_hide_deck_when_external", return_value=True), \
             mock.patch.object(pc, "_handheld", return_value=False), \
             mock.patch.object(pc, "pad_label", side_effect=lambda v, vp, n, p, x: n):
            r = pc._route_one("openbor", "system", merged, {}, xport, devs, sdl, 0,
                              sinden_idx=(None, None, False))
        return [(row.get("slot"), row.get("vidpid")) for row in r.get("rows", [])], r

    def test_a_non_cabinet_xbox_pad_is_seated_LAST_not_first(self):
        # THE BUG, in the exact live shape: receiver at 1.2.3, cabinet identified at 1.1.
        rows, _ = self._route([(self.XA, "Xbox 360 Wireless Receiver", "usb-x-1.2.3/input0"),
                               (self.DS, "DualSense Wireless Controller", "")])
        self.assertEqual(rows, [("P1", self.DS), ("P2", self.XA)])

    def test_the_identified_cabinet_still_leads(self):
        # Same two pads, but this 045e IS at the identified port. The token is first in
        # pad_classes, so the cabinet takes P1 -- which is the whole reason it is listed first.
        rows, _ = self._route([(self.DS, "DualSense Wireless Controller", ""),
                               (self.XA, "X-Arcade", "usb-x-1.1/input0")])
        self.assertEqual(rows, [("P1", self.XA), ("P2", self.DS)])

    def test_preview_agrees_with_the_merger_pad_for_pad(self):
        # The contract, stated directly: whatever build_plan says, the rows say.
        from lib import openbor_seating
        pads = [(self.XA, "Xbox 360 Wireless Receiver", "usb-x-1.2.3/input0"),
                (self.DS, "DualSense Wireless Controller", ""),
                ("054c:09cc", "PS4 Controller", "")]
        rows, _ = self._route(pads)
        devs = [Device(name=n, path=f"/dev/input/event{20 + i}", is_joypad=True,
                       is_mouse=False, is_keyboard=False, js_index=i, mouse_index=None,
                       vid=int(vp.split(":")[0], 16), pid=int(vp.split(":")[1], 16), phys=ph)
                for i, (vp, n, ph) in enumerate(pads)]
        with mock.patch.object(sdl_filter, "_hide_deck_when_external", return_value=True):
            plan = openbor_seating.build_plan(devs, self.CLASSES, XPORT)
        self.assertEqual([vp for _slot, vp in rows],
                         [f"{d.vid:04x}:{d.pid:04x}" for d, _c in plan])


class DaphneOnlyMeansTheCabinet(unittest.TestCase):
    """The same 2026-08-13 report, other half: the daphne row also named the Xbox receiver P1.

    [backends.hypseus] lists ONLY "x-arcade". That token means THE CABINET -- sdl_filter resolves
    it port-aware and hides everything else -- but this page expanded it to a bare 045e:02a1, so
    any Xbox-looking pad answered to it. Daphne is also the one backend whose no-pad outcome MAD
    decides itself (docked with nothing listed connected, the blocklist hides every pad including
    the Deck's), so the fallback line has to say that rather than "unchanged".
    """

    XA, DS = "045e:02a1", "054c:0ce6"

    def _route(self, pads, handheld=False, xport=XPORT, xa_ruled_out=True):
        """Daphne's row. BOTH dock gates are pinned through MAD_FORCE_CONTEXT, and the device
        scan is patched, because this row is now decided by asking sdl_filter what the launch
        will really hand the emulator -- so leaving either one on the live machine would make
        the result depend on whether this Deck is docked and what is plugged into it."""
        sdl = [SdlDevice(index=i, vidpid=vp, guid="", name=n)
               for i, (vp, n, _ph) in enumerate(pads)]
        devs = [Device(name=n, path=f"/dev/input/event{20 + i}", is_joypad=True,
                       is_mouse=False, is_keyboard=False, js_index=i, mouse_index=None,
                       vid=int(vp.split(":")[0], 16), pid=int(vp.split(":")[1], 16), phys=ph)
                for i, (vp, n, ph) in enumerate(pads)]
        merged = _merged(systems={"daphne": {"backend": "hypseus"}},
                         backends={"hypseus": {"pad_classes": ["x-arcade"],
                                               "sdl_priority": True,
                                               "handheld_class": "28de:11ff"}})
        present = {vp for vp, _n, _ph in pads}
        if any(ph and dv_port(ph) == xport for _vp, _n, ph in pads):
            present.add("x-arcade")
        saved = os.environ.get("MAD_FORCE_CONTEXT")
        os.environ["MAD_FORCE_CONTEXT"] = "handheld" if handheld else "docked"
        self.addCleanup(lambda: os.environ.__setitem__("MAD_FORCE_CONTEXT", saved)
                        if saved is not None else os.environ.pop("MAD_FORCE_CONTEXT", None))
        with mock.patch.object(pc, "_handheld", return_value=handheld), \
             mock.patch.object(sdl_filter, "_scan", return_value=(present, xa_ruled_out)), \
             mock.patch.object(pc, "pad_label", side_effect=lambda v, vp, n, p, x: n):
            return pc._route_one("daphne", "system", merged, {}, xport, devs, sdl, 0,
                                 sinden_idx=(None, None, False))

    def test_a_non_cabinet_xbox_pad_is_not_shown_as_a_player(self):
        r = self._route([(self.XA, "Xbox 360 Wireless Receiver", "usb-x-1.2.3/input0"),
                         (self.DS, "DualSense Wireless Controller", "")])
        self.assertEqual(r["kind"], "text")
        self.assertNotIn("Xbox", r["text"])

    def test_docked_with_nothing_listed_connected_says_no_controller(self):
        r = self._route([(self.XA, "Xbox 360 Wireless Receiver", "usb-x-1.2.3/input0")])
        self.assertIn("no controller", r["text"])
        self.assertNotIn("unchanged", r["text"])

    def test_undocked_it_is_the_deck_fallback_not_a_dead_row(self):
        r = self._route([(self.XA, "Xbox 360 Wireless Receiver", "usb-x-1.2.3/input0")],
                        handheld=True)
        self.assertEqual(r["kind"], "pads")
        self.assertEqual(r["rows"][0]["vidpid"], "28de:11ff")

    def test_the_real_cabinet_is_shown(self):
        r = self._route([(self.XA, "X-Arcade", "usb-x-1.1/input0")])
        self.assertEqual(r["kind"], "pads")
        self.assertEqual([row["vidpid"] for row in r["rows"]], [self.XA])

    def test_a_backend_listing_the_raw_vidpid_still_admits_any_xbox_pad(self):
        # mugen's policy lists "045e:02a1" rather than the token, and that entry means
        # exactly what it says. Only the TOKEN is port-aware.
        sdl = [SdlDevice(index=0, vidpid=self.XA, guid="", name="Xbox 360 Wireless Receiver")]
        devs = [Device(name="Xbox 360 Wireless Receiver", path="/dev/input/event20",
                       is_joypad=True, is_mouse=False, is_keyboard=False, js_index=0,
                       mouse_index=None, vid=0x045e, pid=0x02a1, phys="usb-x-1.2.3/input0")]
        merged = _merged(systems={"model3": {"backend": "supermodel"}},
                         backends={"supermodel": {"pad_classes": [self.XA]}})
        with mock.patch.object(pc, "_handheld", return_value=False), \
             mock.patch.object(pc, "pad_label", side_effect=lambda v, vp, n, p, x: n):
            r = pc._route_one("model3", "system", merged, {}, XPORT, devs, sdl, 0,
                              sinden_idx=(None, None, False))
        self.assertEqual(r["kind"], "pads")
        self.assertEqual([row["vidpid"] for row in r["rows"]], [self.XA])


class GenericStandaloneOrdering(unittest.TestCase):
    """The non-merger standalones (pcsx2, xemu, supermodel, hypseus...) read the SDL view.

    Two things must hold and neither had a test: seats follow pad_classes PRIORITY rather than
    SDL enumeration order, and the "x-arcade" token holds its own position in that list rather
    than being pinned to the front (only the merger puts the cabinet first unconditionally).
    """

    XA, DS, DS4 = "045e:02a1", "054c:0ce6", "054c:09cc"

    def _rows(self, classes, pads, xport=XPORT):
        sdl = [SdlDevice(index=i, vidpid=vp, guid="", name=n)
               for i, (vp, n, _ph) in enumerate(pads)]
        devs = [Device(name=n, path=f"/dev/input/event{20 + i}", is_joypad=True,
                       is_mouse=False, is_keyboard=False, js_index=i, mouse_index=None,
                       vid=int(vp.split(":")[0], 16), pid=int(vp.split(":")[1], 16), phys=ph)
                for i, (vp, n, ph) in enumerate(pads)]
        # supermodel, NOT pcsx2: pcsx2/eden/rpcs3/cemu/dolphin* each have their own branch
        # ABOVE the generic one (they ask switch_bind / cemu_seat / dolphin_gc_dock), so a
        # pcsx2 fixture would silently exercise the wrong code -- and, with no policy
        # patched, the LIVE machine's pads. Supermodel is a genuine generic-branch backend.
        merged = _merged(systems={"model3": {"backend": "supermodel"}},
                         backends={"supermodel": {"pad_classes": classes}})
        with mock.patch.object(pc, "_handheld", return_value=False), \
             mock.patch.object(sdl_filter, "_hide_deck_when_external", return_value=True), \
             mock.patch.object(pc, "pad_label", side_effect=lambda v, vp, n, p, x: n):
            r = pc._route_one("model3", "system", merged, {}, xport, devs, sdl, 0,
                              sinden_idx=(None, None, False))
        return [row["vidpid"] for row in r.get("rows", [])], r

    def test_priority_beats_enumeration_order(self):
        # DS4 enumerates FIRST but is listed SECOND: the DualSense still takes P1.
        rows, _ = self._rows([self.DS, self.DS4],
                             [(self.DS4, "PS4 Controller", ""),
                              (self.DS, "DualSense Wireless Controller", "")])
        self.assertEqual(rows, [self.DS, self.DS4])

    def test_the_cabinet_holds_its_listed_position_not_the_front(self):
        # Token listed LAST -> the cabinet is P2 behind the DualSense. (The merger's
        # cabinet-first rule is the merger's; it must not leak into this branch.)
        rows, _ = self._rows([self.DS, "x-arcade"],
                             [(self.XA, "X-Arcade", "usb-x-1.1/input0"),
                              (self.DS, "DualSense Wireless Controller", "")])
        self.assertEqual(rows, [self.DS, self.XA])

    def test_the_cabinet_leads_when_the_token_is_listed_first(self):
        rows, _ = self._rows(["x-arcade", self.DS],
                             [(self.DS, "DualSense Wireless Controller", ""),
                              (self.XA, "X-Arcade", "usb-x-1.1/input0")])
        self.assertEqual(rows, [self.XA, self.DS])

    def test_both_cabinet_halves_are_seated_not_collapsed_to_one_row(self):
        # NOT an ordering test, deliberately. It used to claim to pin "their order must not be
        # enumeration luck" while asserting [XA, XA] -- two identical strings, so no order was
        # observable and reversing both sort keys survived it. And the rule it named belongs
        # to the MERGER: daphne and supermodel receive a single 045e:02a1 whitelist entry, so
        # MAD imposes no order on these two halves at all and asserting one would be fiction.
        # USB-interface order is pinned where it is real, in tests/test_openbor_pads.py.
        rows, _ = self._rows(["x-arcade"],
                             [(self.XA, "X-Arcade", "usb-x-1.1/input0"),
                              (self.XA, "X-Arcade", "usb-x-1.1/input0")])
        self.assertEqual(len(rows), 2, "both halves are separate players")


class HeadingRowsCarryNoArt(unittest.TestCase):
    """The `noicon` -> icon_path="" mapping in _preview_all's row loop.

    It had ZERO coverage: misspelling the flag left all 3838 tests green while every Wii
    heading row silently gained a generic gamepad picture -- which is the one thing the flag
    exists to prevent. Two agents hit that independently on 2026-08-13, one of them by
    actually introducing the typo. WiiRoute cannot catch it: it calls _route_one directly and
    never enters the loop that resolves icon_path.
    """

    def _icon_paths(self, rows):
        seen = {}
        with mock.patch.object(pc, "_items", return_value=[{"key": "wii", "label": "wii",
                                                            "kind": "system"}]), \
             mock.patch.object(pc, "_route_one", return_value={"kind": "pads", "rows": rows}), \
             mock.patch.object(pc, "load_merged", return_value=_merged()), \
             mock.patch.object(pc, "load_policy", return_value={}), \
             mock.patch.object(pc.dv, "enumerate_devices", return_value=[]), \
             mock.patch.object(pc.dv, "sdl_devices", return_value=[]), \
             mock.patch("lib.madsrv.systems_cmds.device_icon_path",
                        side_effect=lambda name, vidpid="", **kw: f"art:{name}"), \
             mock.patch("lib.madsrv.systems_cmds.console_art", return_value=""), \
             mock.patch("lib.madsrv.systems_cmds.resolve_art", return_value=""):
            out = pc._preview_all({})
        for r in out["routes"]:
            for row in r["route"]["rows"]:
                seen[row["text"]] = row.get("icon_path")
        return seen

    def test_a_heading_row_resolves_to_no_art_at_all(self):
        got = self._icon_paths([{"slot": "", "text": "Classic Controller games", "noicon": True},
                                {"slot": "P1", "text": "DS 1", "vidpid": "054c:0ce6"}])
        self.assertEqual(got["Classic Controller games"], "",
                         "a plain heading must not resolve to the generic gamepad art")
        self.assertTrue(got["DS 1"], "a real seat row must still get its pad art")
