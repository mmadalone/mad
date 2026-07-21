"""Family x context controller seating for Cemu / Wii U (lib/cemu_seat.py).

Hermetic: a temp config_dir, MAD_FORCE_CONTEXT, faked device + SDL enumeration, and a patched
policy. Proves the binder seats the right profile per slot, re-pins the family block per pad (two
of a kind get distinct indices), keeps the GamePad's Deck co-source baked, reverts on exit, and is
a no-op when seating is disabled / nothing is assigned / the context has no map.

Run:  python3 -m unittest tests.test_cemu_seat -v
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import cemu_seat
from tests._fakes import dev, patch_sdl, sd

_DS_GUID = "030057564c050000e60c000000006800"      # DualSense
_WP_GUID = "0500a9177e0500003003000001000000"      # Wii U Pro
_DECK_GUID = "030079f6de280000ff11000001000000"    # Steam Deck built-in (matches cemu_cfg._DECK_GUIDS)


def _block(guid, mappings=True):
    maps = "<mappings><entry><mapping>1</mapping><button>0</button></entry></mappings>" if mappings else "<mappings/>"
    return (f"\t<controller>\n\t\t<api>SDLController</api>\n\t\t<uuid>0_{guid}</uuid>\n"
            f"\t\t<display_name>baked</display_name>\n\t\t{maps}\n\t</controller>\n")


def _profile(name, *guids, ptype="Wii U Pro Controller"):
    body = "".join(_block(g, mappings=(i == 0)) for i, g in enumerate(guids))
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<emulated_controller>\n'
            f"\t<type>{ptype}</type>\n\t<profile>{name}</profile>\n{body}"
            f"</emulated_controller>\n")


class CemuSeat(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        # resting active slot files (distinct content so a change is detectable)
        for slot in range(4):
            (self.d / f"controller{slot}.xml").write_text(f"<emulated_controller><profile>REST{slot}</profile></emulated_controller>\n")
        # named profiles
        (self.d / "DualSense 1.xml").write_text(_profile("DualSense 1", _DS_GUID))
        (self.d / "WiiU Pro 1.xml").write_text(_profile("WiiU Pro 1", _WP_GUID))
        (self.d / "Steamdeck.xml").write_text(_profile("Steamdeck", _DECK_GUID))
        (self.d / "DualSense 1 + Steamdeck.xml").write_text(_profile("DualSense 1 + Steamdeck", _DS_GUID, _DECK_GUID))
        (self.d / "WiiU Pro 1 + Steamdeck.xml").write_text(_profile("WiiU Pro 1 + Steamdeck", _WP_GUID, _DECK_GUID))
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        # Hermetic toggle: point install.conf at an (initially empty) temp file so the 'no deckpad if
        # external' toggle reads its DEFAULTS (handheld off = keep the Deck; docked on) unless a test
        # sets it -- never the real on-device install.conf.
        self.conf = self.d / "install.conf"
        self._saved_conf = os.environ.get("MAD_INSTALL_CONF")
        os.environ["MAD_INSTALL_CONF"] = str(self.conf)

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._saved_conf is None:
            os.environ.pop("MAD_INSTALL_CONF", None)
        else:
            os.environ["MAD_INSTALL_CONF"] = self._saved_conf
        shutil.rmtree(self.d, ignore_errors=True)

    def _hide_deck(self, *, handheld=None, docked=None):
        """Set the 'no deckpad if external' toggle for this test (True = hide/takeover)."""
        from lib import install_conf
        if handheld is not None:
            install_conf.set_value("HIDE_DECK_PAD_WHEN_EXTERNAL_HANDHELD", "1" if handheld else "0")
        if docked is not None:
            install_conf.set_value("HIDE_DECK_PAD_WHEN_EXTERNAL", "1" if docked else "0")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _pol(self, *, seating=True, ports=None, pmap=None, manage=(1, 2)):
        return {
            "handheld": {"enabled": True},
            "pins": {},
            "systems": {"wiiu": {"handheld": {"enabled": True},
                                 "ports": ports if ports is not None else [["DualSense"], ["Wii Remote Pro"]]}},
            "backends": {"cemu": {"config_dir": str(self.d), "manage_ports": list(manage),
                                  "gamepad_port": 0, "seating_enabled": seating,
                                  "handheld_profile": "",   # legacy stays a no-op when disabled
                                  "profile_map": pmap or {"docked": {}, "handheld": {}}}},
        }

    def _run(self, fn, pol, devs, sdl):
        with mock.patch("lib.policy.load_merged", lambda: pol), \
             mock.patch("lib.devices.enumerate_devices", lambda: devs), \
             patch_sdl(sdl):
            return fn()

    def _c(self, slot):
        return (self.d / f"controller{slot}.xml").read_text()

    def _bak(self, slot):
        return self.d / f"controller{slot}.xml.mad-seat-backup"

    def _uuids(self, slot):
        return re.findall(r"<uuid>(.*?)</uuid>", self._c(slot))

    def _profile_name(self, slot):
        m = re.search(r"<profile>(.*?)</profile>", self._c(slot))
        return m.group(1) if m else ""

    def _two_ds(self):
        devs = [dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller"),
                dev("054c:0ce6", "/dev/input/event11", "DualSense Wireless Controller")]
        sdl = [sd(0, "054c:0ce6", _DS_GUID, "DualSense a"),
               sd(1, "054c:0ce6", _DS_GUID, "DualSense b"),
               sd(2, "28de:1205", _DECK_GUID, "Steam Deck")]
        return devs, sdl

    def _ds_wp(self):
        devs = [dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller"),
                dev("057e:0330", "/dev/input/event11", "Nintendo Wii Remote Pro Controller")]
        sdl = [sd(0, "054c:0ce6", _DS_GUID, "DualSense"),
               sd(1, "057e:0330", _WP_GUID, "Wii U Pro"),
               sd(2, "28de:1205", _DECK_GUID, "Steam Deck")]
        return devs, sdl

    # ── the seating ─────────────────────────────────────────────────────────
    def test_handheld_seats_families(self):
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1 + Steamdeck",
                                           "Wii Remote Pro": "WiiU Pro 1 + Steamdeck",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)
        # Controller 1 (slot 0) = the Deck GamePad profile, verbatim (dev None -> no re-pin).
        self.assertEqual(self._c(0), (self.d / "Steamdeck.xml").read_text())
        # Controller 2 (slot 1) = DualSense: family block re-pinned to per-guid ordinal 0; the Deck
        # co-source block is DROPPED (external player slot, not a Deck co-driver).
        self.assertEqual(self._uuids(1), [f"0_{_DS_GUID}"])
        # Controller 3 (slot 2) = Wii U Pro: it enumerates at global SDL index 1 (behind the DualSense
        # at 0), but its per-GUID ORDINAL is 0 (the only Wii U Pro), which is what Cemu binds by -- the
        # core BUG 1 fix (the old global-index code wrote 1_ and Cemu found no second Wii U Pro).
        self.assertEqual(self._uuids(2), [f"0_{_WP_GUID}"])
        # external player slots are forced to Pro-Controller type (never a 2nd Wii U GamePad).
        self.assertIn("<type>Wii U Pro Controller</type>", self._c(1))
        self.assertIn("<type>Wii U Pro Controller</type>", self._c(2))
        for slot in (0, 1, 2):
            self.assertTrue(self._bak(slot).is_file())

    def test_two_of_a_kind_distinct_indices(self):
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1 + Steamdeck", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._two_ds()
        self._run(cemu_seat.apply, self._pol(ports=[["DualSense"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertEqual(self._uuids(1), [f"0_{_DS_GUID}"])   # first DualSense -> per-guid ordinal 0 (Deck dropped)
        self.assertEqual(self._uuids(2), [f"1_{_DS_GUID}"])   # second identical -> ordinal 1 (distinct, binds P3)

    def test_external_slot_forces_pro_type_and_drops_deck(self):
        # BUG 2 + BUG 3: a GamePad-type "+ Steamdeck" profile (a Wii U Pro pad configured as the
        # GamePad, plus a Deck co-source) assigned to an EXTERNAL family must seat as a clean Pro
        # Controller with the Deck block removed -- else the slot is an invalid 2nd GamePad and the
        # Deck shadows the player (the exact on-device failure).
        (self.d / "WiiU Pro GP.xml").write_text(
            _profile("WiiU Pro GP", _WP_GUID, _DECK_GUID, ptype="Wii U GamePad"))
        pmap = {"docked": {}, "handheld": {"Wii Remote Pro": "WiiU Pro GP", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()   # DualSense@0, Wii U Pro@1, Deck@2
        self._run(cemu_seat.apply,
                  self._pol(ports=[["Wii Remote Pro"]], pmap=pmap, manage=(1,)), devs, sdl)
        seated = self._c(1)
        self.assertIn("<type>Wii U Pro Controller</type>", seated)     # forced Pro
        self.assertNotIn("Wii U GamePad", seated)                      # not a 2nd GamePad
        self.assertEqual(self._uuids(1), [f"0_{_WP_GUID}"])            # Deck co-source dropped, ordinal 0

    # ── takeover: external pads are the players (the 'no deckpad if external' toggle) ──────────
    def test_takeover_external_becomes_players(self):
        # Toggle ON (hide the Deck, handheld) + external pads present -> the external pads are the
        # players FROM Controller 1: first external = the GamePad (P1), next = P2; the Deck is NOT
        # seated. (This is the on-device fix: a normal ES-DE launch used to make the Deck P1.)
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1 + Steamdeck",
                                           "Wii Remote Pro": "WiiU Pro 1 + Steamdeck",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()   # DualSense@0, Wii U Pro@1, Deck@2
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        # Controller 1 (slot 0) = the first external pad (Wii U Pro) forced to GamePad; Deck stripped.
        self.assertIn("<type>Wii U GamePad</type>", self._c(0))
        self.assertEqual(self._uuids(0), [f"0_{_WP_GUID}"])
        self.assertNotIn(_DECK_GUID, self._c(0))                       # Deck co-source gone
        # Controller 2 (slot 1) = the second external pad (DualSense), Pro type.
        self.assertIn("<type>Wii U Pro Controller</type>", self._c(1))
        self.assertEqual(self._uuids(1), [f"0_{_DS_GUID}"])
        # the Deck's own "Steamdeck" GamePad profile is NOT seated at Controller 1 (or anywhere).
        self.assertNotEqual(self._c(0), (self.d / "Steamdeck.xml").read_text())

    def test_takeover_no_external_keeps_deck(self):
        # Toggle ON but NO external pad connected -> nothing to take over: the Deck stays Controller 1.
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"Steam Deck": "Steamdeck"}}
        devs, sdl = [], [sd(0, "28de:1205", _DECK_GUID, "Steam Deck")]
        self._run(cemu_seat.apply, self._pol(ports=[], pmap=pmap), devs, sdl)
        self.assertEqual(self._c(0), (self.d / "Steamdeck.xml").read_text())   # Deck = Controller 1

    def test_takeover_docked_honors_docked_toggle(self):
        # The docked "no deckpad if external" toggle drives the docked context the same way.
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self._hide_deck(docked=True)
        pmap = {"handheld": {}, "docked": {"Wii Remote Pro": "WiiU Pro 1", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"]], pmap=pmap), devs, sdl)
        self.assertIn("<type>Wii U GamePad</type>", self._c(0))        # Wii U Pro = Controller 1 (GamePad)
        self.assertEqual(self._uuids(0), [f"0_{_WP_GUID}"])

    def test_takeover_compacts_port1_hole_to_gamepad(self):
        # ADVERSARIAL-REVIEW REGRESSION (2026-07-21): a HOLE at port 1 (here only the port-2 token's
        # pad is connected; equally a pin to a later player) must STILL put a pad on Controller 1 = the
        # GamePad. Resolved pads compact from Controller 1 by connection order. The old code indexed
        # slots[player-1], so a port-1 hole left the GamePad slot unseated (Deck NOT hidden despite the
        # toggle, or the game left with no GamePad).
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"}}
        devs = [dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller")]   # ONLY a DualSense
        sdl = [sd(0, "054c:0ce6", _DS_GUID, "DualSense"),
               sd(1, "28de:1205", _DECK_GUID, "Steam Deck")]
        # port 1 = Wii Remote Pro (NOT connected) -> empty; port 2 = DualSense -> the only pad.
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertIn("<type>Wii U GamePad</type>", self._c(0))        # the lone pad -> Controller 1 (GamePad)
        self.assertEqual(self._uuids(0), [f"0_{_DS_GUID}"])
        # takeover OWNS every slot: the unseated C2 is CLEARED (removed, with a backup for game-end
        # restore), not left resting -- so no stale/phantom controller survives on an unused slot.
        self.assertFalse((self.d / "controller1.xml").exists())        # C2 removed -> no phantom
        self.assertTrue(self._bak(1).is_file())                        # backup snapshot kept for restore

    def test_takeover_clears_stale_leftover_slot(self):
        # THE REPORTED BUG (2026-07-21): takeover with a Wii U Pro + ONE DualSense, but Controller 3
        # (slot 2) still holds a STALE DualSense profile from a prior 2-DualSense config. Cemu then binds
        # the single DualSense to BOTH C2 and C3 (P2 == P3). The router must CLEAR the unseated slot so no
        # phantom/duplicate player survives; game-end restore brings the resting file back (transient).
        (self.d / "controller2.xml").write_text(_profile("stale", _DS_GUID))   # C3 = leftover DualSense
        before_c3 = self._c(2)
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"Wii Remote Pro": "WiiU Pro 1", "DualSense": "DualSense 1",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()   # DualSense@0, Wii U Pro@1, Deck@2
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertIn("<type>Wii U GamePad</type>", self._c(0))        # C1 = Wii U Pro (GamePad)
        self.assertEqual(self._uuids(0), [f"0_{_WP_GUID}"])
        self.assertEqual(self._uuids(1), [f"0_{_DS_GUID}"])            # C2 = the one DualSense
        self.assertFalse((self.d / "controller2.xml").exists())        # C3 stale DualSense CLEARED -> no phantom P3
        self.assertTrue(self._bak(2).is_file())
        self._run(cemu_seat.restore, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertEqual(self._c(2), before_c3)                        # resting C3 restored on game-end
        self.assertFalse(self._bak(2).exists())

    def test_takeover_clears_planned_but_skipped_slot(self):
        # ADVERSARIAL-REVIEW gap (2026-07-21): a takeover slot that is PLANNED but SKIPPED at seat time
        # (its profile FILE is missing, though the map names it) must STILL be cleared -- the clear set is
        # derived from slots actually WRITTEN, not the plan. Here C1 (the GamePad slot) is planned for the
        # Wii U Pro but "WiiU Pro MISSING.xml" is absent, and C1's resting file is a stale DualSense that
        # duplicates the one seated on C2. Without the fix C1 stays stale -> the DualSense drives P1 AND P2.
        (self.d / "controller0.xml").write_text(_profile("stale C1", _DS_GUID))   # C1 resting = stale DualSense
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"Wii Remote Pro": "WiiU Pro MISSING", "DualSense": "DualSense 1",
                                           "Steam Deck": "Steamdeck"}}   # "WiiU Pro MISSING.xml" deliberately absent
        devs, sdl = self._ds_wp()   # DualSense@0, Wii U Pro@1, Deck@2
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertEqual(self._uuids(1), [f"0_{_DS_GUID}"])            # C2 = the one DualSense (seated)
        self.assertFalse((self.d / "controller0.xml").exists())        # C1 (planned-but-skipped, stale) CLEARED
        self.assertTrue(self._bak(0).is_file())                        # backed up for game-end restore

    def test_takeover_unassigned_first_pad_still_seats_gamepad(self):
        # ADVERSARIAL-REVIEW hardening (2026-07-21): in takeover the FIRST present pad's family is
        # UNASSIGNED (no profile) while a LATER pad IS assigned. The first SEATABLE pad must become
        # Controller 1 = the GamePad; an unassigned pad must NOT consume the GamePad slot and leave the
        # game with no GamePad. (The old resolved-order index stranded Controller 1 here.)
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"}}   # Wii Remote Pro UNassigned
        devs, sdl = self._ds_wp()   # DualSense@0, Wii U Pro@1
        # port 1 = Wii Remote Pro (present but UNASSIGNED), port 2 = DualSense (present + assigned).
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertIn("<type>Wii U GamePad</type>", self._c(0))        # the DualSense (first seatable) -> Controller 1
        self.assertEqual(self._uuids(0), [f"0_{_DS_GUID}"])
        self.assertNotIn("REST0", self._c(0))                          # C1 NOT left resting / unseated

    def test_retype_mappings_roundtrip(self):
        # Cemu numbers dpad/sticks +1 for Pro vs GamePad; ids 1-10 (face) are shared. Round-trip identity.
        from lib import cemu_cfg
        pro = "<mapping>1</mapping><mapping>10</mapping><mapping>12</mapping><mapping>25</mapping>"
        gp = cemu_cfg._retype_mappings(pro, to_gamepad=True)
        self.assertEqual(re.findall(r"<mapping>(\d+)</mapping>", gp), ["1", "10", "11", "24"])
        self.assertEqual(cemu_cfg._retype_mappings(gp, to_gamepad=False), pro)   # GamePad->Pro is the inverse

    def test_takeover_c1_retranslates_pro_ids_to_gamepad(self):
        # The takeover Controller 1 forces GamePad type; the <mapping> ids MUST be retranslated
        # (Pro dpad/stick id -> GamePad id-1) or the sticks/dpad break in-game (the exact on-device
        # symptom). Face-button ids (1-10) are unchanged.
        prof = ('<?xml version="1.0" encoding="UTF-8"?>\n<emulated_controller>\n'
                '\t<type>Wii U Pro Controller</type>\n\t<controller>\n\t\t<api>SDLController</api>\n'
                f'\t\t<uuid>0_{_WP_GUID}</uuid>\n\t\t<mappings>'
                '<entry><mapping>2</mapping><button>1</button></entry>'        # face -> unchanged
                '<entry><mapping>12</mapping><button>11</button></entry>'      # Pro dpad-up 12 -> GamePad 11
                '<entry><mapping>25</mapping><button>40</button></entry>'      # Pro stick 25 -> GamePad 24
                '</mappings>\n\t</controller>\n</emulated_controller>\n')
        (self.d / "WiiU Pro raw.xml").write_text(prof)
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"Wii Remote Pro": "WiiU Pro raw", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        self._run(cemu_seat.apply, self._pol(ports=[["Wii Remote Pro"]], pmap=pmap, manage=(1,)), devs, sdl)
        c0 = self._c(0)
        self.assertIn("<type>Wii U GamePad</type>", c0)
        self.assertEqual(re.findall(r"<mapping>(\d+)</mapping>", c0), ["2", "11", "24"])   # face; dpad 12->11; stick 25->24

    # ── family+order: the Nth pad of a family gets the Nth device-bound profile ────────────
    def test_family_order_distinct_profiles(self):
        # Two DualSenses must get DISTINCT profiles: 1st -> "DualSense 1", 2nd -> "DualSense 2"
        # (auto-derived by bumping the trailing number), instead of both reusing "DualSense 1".
        (self.d / "DualSense 2.xml").write_text(_profile("DualSense 2", _DS_GUID))
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._two_ds()
        self._run(cemu_seat.apply, self._pol(ports=[["DualSense"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertEqual(self._profile_name(0), "DualSense 1")   # 1st DualSense (Controller 1)
        self.assertEqual(self._profile_name(1), "DualSense 2")   # 2nd DualSense -> its OWN profile
        self.assertEqual(self._uuids(0)[0], f"0_{_DS_GUID}")     # + distinct device ordinals
        self.assertEqual(self._uuids(1)[0], f"1_{_DS_GUID}")

    def test_family_order_falls_back_without_second_profile(self):
        # No "DualSense 2" on disk -> the 2nd DualSense safely reuses "DualSense 1" (today's behaviour),
        # still bound to a DISTINCT physical unit via the ordinal.
        self._hide_deck(handheld=True)
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._two_ds()
        self._run(cemu_seat.apply, self._pol(ports=[["DualSense"], ["DualSense"]], pmap=pmap), devs, sdl)
        self.assertEqual(self._profile_name(0), "DualSense 1")
        self.assertEqual(self._profile_name(1), "DualSense 1")   # fallback (no "DualSense 2")
        self.assertEqual(self._uuids(1)[0], f"1_{_DS_GUID}")     # still a distinct unit

    def test_profile_for_nth_resolver(self):
        from lib import cemu_profiles
        cfg = {"config_dir": str(self.d), "profile_map": {"handheld": {"DualSense": "DualSense 1"}}}
        self.assertEqual(cemu_profiles.profile_for_nth(cfg, "DualSense", "handheld", 0, self.d), "DualSense 1")
        self.assertEqual(cemu_profiles.profile_for_nth(cfg, "DualSense", "handheld", 1, self.d), "DualSense 1")  # no file yet
        (self.d / "DualSense 2.xml").write_text("<x/>")
        self.assertEqual(cemu_profiles.profile_for_nth(cfg, "DualSense", "handheld", 1, self.d), "DualSense 2")
        cfg2 = {"config_dir": str(self.d), "profile_map": {"handheld": {"DualSense": "NoNumber"}}}
        self.assertEqual(cemu_profiles.profile_for_nth(cfg2, "DualSense", "handheld", 1, self.d), "NoNumber")  # no trailing digit

    def test_repin_keeps_cemu_baked_guid_over_system_sdl(self):
        # A hidapi pad has DIFFERENT guids in Cemu (bus 03) vs this hook's system SDL (bus 05, BT).
        # The re-pin must KEEP Cemu's guid (the profile's baked one), not the system SDL's re-derived
        # one, or Cemu never binds it. Only the ORDINAL is taken live.
        from lib import cemu_cfg
        import re as _re
        CEMU_GUID = "030057564c050000e60c000000006800"   # what Cemu computes (bus 03)
        SYS_GUID = "050057564c050000e60c000000006800"     # what the system SDL reports (bus 05)
        prof = _profile("DualSense 1", CEMU_GUID)
        d = dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller")
        sdl = [sd(0, "054c:0ce6", SYS_GUID, "DualSense")]
        u = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, d, [d], sdl))
        self.assertEqual(u, [f"0_{CEMU_GUID}"])           # kept Cemu's 03..., NOT the system 05...

    def test_repin_uses_live_guid_when_profile_model_differs(self):
        # ADVERSARIAL-REVIEW REGRESSION (2026-07-21): a DIFFERENT-model same-family pad reusing a
        # fallback profile (a DualSense Edge dropping back to the base "DualSense 1", baked for a
        # STANDARD DualSense) must NOT emit the base's baked guid -- that would collide two distinct
        # pads onto one uuid (one drives both slots, the other dead). It uses the pad's OWN live guid.
        # (Same-MODEL reuse still keeps the baked guid -- the Bluetooth bus-byte fix above.)
        from lib import cemu_cfg
        import re as _re
        DS_BAKED = "030057564c050000e60c000000006800"     # standard DualSense (product 0ce6) = the profile's baked guid
        EDGE_GUID = "030057564c050000f20d000000006800"     # DualSense Edge (product 0df2) = a DIFFERENT model
        prof = _profile("DualSense 1", DS_BAKED)           # base profile baked for the standard DualSense
        d = dev("054c:0df2", "/dev/input/event11", "DualSense Edge Wireless Controller")
        sdl = [sd(0, "054c:0df2", EDGE_GUID, "DualSense Edge")]
        u = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, d, [d], sdl))
        self.assertEqual(u, [f"0_{EDGE_GUID}"])            # the Edge's OWN guid, not the DualSense base's 0ce6

    def test_restore_reverts_all(self):
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Wii Remote Pro": "WiiU Pro 1",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        before = {s: self._c(s) for s in range(4)}
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)
        self.assertNotEqual(self._c(1), before[1])                  # something changed
        self._run(cemu_seat.restore, self._pol(pmap=pmap), devs, sdl)
        for s in range(4):
            self.assertEqual(self._c(s), before[s])                 # exact revert
            self.assertFalse(self._bak(s).exists())

    def test_gamepad_deck_block_baked(self):
        pmap = {"docked": {}, "handheld": {"Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        self._run(cemu_seat.apply, self._pol(ports=[], pmap=pmap), devs, sdl)
        self.assertIn(f"0_{_DECK_GUID}", self._uuids(0))            # Deck kept baked, not re-pinned

    # ── no-ops ────────────────────────────────────────────────────────────────
    def test_disabled_no_new_seat(self):
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1"}}
        devs, sdl = self._ds_wp()
        before = {s: self._c(s) for s in (1, 2)}
        self._run(cemu_seat.apply, self._pol(seating=False, pmap=pmap), devs, sdl)
        for s in (1, 2):
            self.assertEqual(self._c(s), before[s])                 # family seating did not run
            self.assertFalse(self._bak(s).exists())

    def test_docked_nothing_set_noop(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        devs, sdl = self._ds_wp()
        before = {s: self._c(s) for s in range(4)}
        self._run(cemu_seat.apply, self._pol(pmap={"docked": {}, "handheld": {"DualSense": "DualSense 1"}}), devs, sdl)
        for s in range(4):
            self.assertEqual(self._c(s), before[s])
            self.assertFalse(self._bak(s).exists())

    def test_unassigned_family_untouched(self):
        # DualSense assigned, Wii Remote Pro NOT -> slot 2 (the Pro) is left resting, no backup.
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        before2 = self._c(2)
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)
        self.assertNotEqual(self._c(1), "<emulated_controller><profile>REST1</profile></emulated_controller>\n")
        self.assertEqual(self._c(2), before2)                       # Pro slot untouched
        self.assertFalse(self._bak(2).exists())
    def test_repin_undercount_distinct_indices(self):
        # REGRESSION (review blocker): SDL enumerates FEWER pads of a class than evdev. The fallback
        # must still give two byte-identical pads DISTINCT uuid indices, never the same (which would
        # bind both Cemu ports to one physical pad).
        from lib import cemu_cfg
        import re as _re
        devs = [dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller"),
                dev("054c:0ce6", "/dev/input/event11", "DualSense Wireless Controller")]
        sdl = [sd(0, "28de:1205", _DECK_GUID, "Steam Deck"),
               sd(1, "054c:0ce6", _DS_GUID, "DualSense")]        # only ONE DualSense visible to SDL
        prof = _profile("DualSense 1", _DS_GUID)
        u0 = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, devs[0], devs, sdl))
        u1 = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, devs[1], devs, sdl))
        self.assertNotEqual(u0, u1)                              # NOT 'both ports on one pad'
        self.assertEqual(u0, [f"0_{_DS_GUID}"])                  # first -> per-guid ordinal 0
        self.assertEqual(u1, [f"1_{_DS_GUID}"])                  # second (SDL undercounts) -> evdev ordinal 1: distinct AND binds Cemu's 2nd pad

    def test_repin_total_class_miss_with_unrelated_pad(self):
        # ADVERSARIAL-REVIEW REGRESSION (2026-07-21): the daemon's SDL totally MISSES a >=2 pad class
        # while an unrelated pad (the always-present Steam Deck) sits in sdl_devs. The old fallback
        # len(sdl_devs)+ci based the ordinal on the TOTAL count, so the first missed twin got 1_ (which
        # Cemu matches to the SECOND physical pad -> mis-bind). The ci fallback must give 0_/1_ instead.
        from lib import cemu_cfg
        import re as _re
        devs = [dev("054c:0ce6", "/dev/input/event10", "DualSense Wireless Controller"),
                dev("054c:0ce6", "/dev/input/event11", "DualSense Wireless Controller")]
        sdl = [sd(0, "28de:1205", _DECK_GUID, "Steam Deck")]     # SDL sees ONLY the Deck (misses both DS)
        prof = _profile("DualSense 1", _DS_GUID)
        u0 = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, devs[0], devs, sdl))
        u1 = _re.findall(r"<uuid>(.*?)</uuid>", cemu_cfg.repin_profile(prof, devs[1], devs, sdl))
        self.assertEqual(u0, [f"0_{_DS_GUID}"])                  # NOT 1_ (the old mis-bind)
        self.assertEqual(u1, [f"1_{_DS_GUID}"])                  # distinct; both bind Cemu's two pads

    def test_orphan_self_heals(self):
        # a crash skips game-end: the next apply() must heal the orphan back to resting before re-seating,
        # never snapshotting a seated file as "resting". After a final restore everything is the ORIGINAL.
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Wii Remote Pro": "WiiU Pro 1",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        before = {s: self._c(s) for s in range(4)}
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)   # crash relaunch: heal + reseat
        self._run(cemu_seat.restore, self._pol(pmap=pmap), devs, sdl)
        for s in range(4):
            self.assertEqual(self._c(s), before[s])
            self.assertFalse(self._bak(s).exists())

    def test_absent_resting_slot_removed_on_restore(self):
        # a managed slot with NO resting file: seated (created), backup is the empty "was absent" marker,
        # and restore REMOVES our file (back to absent) rather than leaving it stranded.
        (self.d / "controller2.xml").unlink()
        pmap = {"docked": {}, "handheld": {"DualSense": "DualSense 1", "Wii Remote Pro": "WiiU Pro 1",
                                           "Steam Deck": "Steamdeck"}}
        devs, sdl = self._ds_wp()
        self._run(cemu_seat.apply, self._pol(pmap=pmap), devs, sdl)
        self.assertTrue((self.d / "controller2.xml").is_file())      # created
        self.assertEqual(self._bak(2).read_bytes(), b"")             # empty marker = was absent
        self._run(cemu_seat.restore, self._pol(pmap=pmap), devs, sdl)
        self.assertFalse((self.d / "controller2.xml").exists())      # removed -> back to absent
        self.assertFalse(self._bak(2).exists())


if __name__ == "__main__":
    unittest.main()
