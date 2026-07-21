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

    def tearDown(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

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
