"""
Tests for the MAD Standalones → Controllers page logic:
  * controller-TYPE priority (pads_cmds._type_universe / _ordered / _strip_rank)
  * the dynamic+alphabetical Standalones list (standalones_cmds._standalones_list)
  * the configured-controller name shown on the input-map pages
  * the SDL-GUID → vid:pid → friendly-name helpers (mad_config)

Pure logic with light monkeypatching (no SDL/evdev hardware, no pytest — stdlib
unittest, matching the rest of tests/).

Run:  python3 -m unittest tests.test_mad_pages -v
"""
from __future__ import annotations

import unittest
from collections import namedtuple

from lib import mad_config
from lib.madsrv import pads_cmds, standalones_cmds, eden_input_cmds, ryujinx_input_cmds

SD = namedtuple("SdlDevice", "index vidpid guid name")
DS5 = "054c:0ce6"
DS4 = "054c:09cc"
WIIU = "057e:0330"
XBOX = "045e:02a1"
DECK = "28de:1205"


class GuidHelpers(unittest.TestCase):
    def test_vidpid_from_sdl_guid(self):
        self.assertEqual(mad_config.vidpid_from_sdl_guid("03008fe54c050000cc09000000006800"), DS4)
        self.assertEqual(mad_config.vidpid_from_sdl_guid("03000000de2800000512000000026800"), DECK)
        self.assertEqual(mad_config.vidpid_from_sdl_guid("short"), "")

    def test_pad_name(self):
        self.assertEqual(mad_config.pad_name(DS4), "DualShock 4")
        self.assertEqual(mad_config.pad_name("9999:9999"), "")


class TypeUniverse(unittest.TestCase):
    def setUp(self):
        self._orig = pads_cmds._stored_order

    def tearDown(self):
        pads_cmds._stored_order = self._orig

    def _stub(self, order):
        pads_cmds._stored_order = lambda emu, _o=order: list(_o)

    def test_wiiupro_listed_for_eden_and_ryujinx(self):
        # The current SDL3 Ryubing build drives the Wii U Pro, so it is listed for BOTH emulators
        # (older Ryujinx builds excluded it; that stale filter was removed).
        self._stub([])
        self.assertIn(WIIU, pads_cmds._type_universe("eden"))
        self.assertIn(WIIU, pads_cmds._type_universe("ryujinx"))

    def test_deck_and_virtual_excluded(self):
        self._stub([])
        u = pads_cmds._type_universe("pcsx2")
        self.assertNotIn(DECK, u)
        self.assertNotIn("28de:11ff", u)

    def test_configured_first_then_rest_no_dupes(self):
        self._stub([XBOX])
        u = pads_cmds._type_universe("pcsx2")
        self.assertEqual(u[0], XBOX)                 # configured class first
        self.assertEqual(len(u), len(set(u)))        # no duplicates
        self.assertIn(DS5, u)                        # the rest still present

    def test_legacy_instance_ids_tolerated(self):
        # old per-instance editor wrote '<vidpid>' + '<vidpid>#2' for two same pads
        self._stub([WIIU, WIIU + "#2", DS4])
        u = pads_cmds._type_universe("eden")
        self.assertEqual(u[0], WIIU)
        self.assertEqual(u[1], DS4)
        self.assertEqual(len(u), len(set(u)))        # the '#2' collapsed away

    def test_connected_unknown_class_appended(self):
        self._stub([])
        u = pads_cmds._type_universe("pcsx2", connected_vps=["1234:5678"])
        self.assertIn("1234:5678", u)

    def test_stored_unknown_disconnected_class_kept(self):
        # L3: a saved priority for a class that's neither in KNOWN_PADS nor connected
        # must survive (else the next Apply, rebuilt from rows, silently drops it).
        self._stub(["9999:8888"])
        u = pads_cmds._type_universe("pcsx2")            # not connected, not known
        self.assertEqual(u[0], "9999:8888")             # kept at its stored priority


class OrderedClassPriority(unittest.TestCase):
    def setUp(self):
        self._orig = pads_cmds._stored_order

    def tearDown(self):
        pads_cmds._stored_order = self._orig

    def test_same_class_grouped_and_ranked(self):
        pads_cmds._stored_order = lambda emu: [DS4, DS5]      # DS4 type first
        pads = [SD(0, DS4, "g", "DS4a"), SD(1, DS4, "g", "DS4b"), SD(2, DS5, "g", "DS")]
        got = [(d.index, d.vidpid) for d in pads_cmds._ordered("x", pads)]
        self.assertEqual(got, [(0, DS4), (1, DS4), (2, DS5)])  # both DS4s before DS

    def test_unranked_class_is_the_rest(self):
        pads_cmds._stored_order = lambda emu: [DS5]
        pads = [SD(0, XBOX, "g", "X"), SD(1, DS5, "g", "DS")]
        got = [d.vidpid for d in pads_cmds._ordered("x", pads)]
        self.assertEqual(got, [DS5, XBOX])           # ranked first, unranked appended

    def test_legacy_gap_does_not_let_unranked_win(self):
        # M1: legacy '#N' makes ranks non-contiguous ({DS5:0, DS4:2}); the unranked
        # fallback must exceed every assigned rank, else it ties the configured DS4 and
        # an unconfigured pad wins Player 1 by SDL index.
        pads_cmds._stored_order = lambda emu: [DS5, DS5 + "#2", DS4]
        pads = [SD(0, XBOX, "g", "X"), SD(1, DS4, "g", "DS4")]
        got = [d.vidpid for d in pads_cmds._ordered("x", pads)]
        self.assertEqual(got, [DS4, XBOX])           # configured DS4 first, not unranked Xbox

    def test_strip_rank(self):
        self.assertEqual(pads_cmds._strip_rank("054c:09cc#2"), DS4)
        self.assertEqual(pads_cmds._strip_rank(DS4), DS4)


class ConfiguredPadName(unittest.TestCase):
    def test_eden_reads_guid(self):
        text = ('[Controls]\nplayer_0_button_a="engine:sdl,port:0,'
                'guid:03008fe54c050000cc09000000006800,button:0"\n')
        self.assertEqual(eden_input_cmds._configured_pad(text, "player_0"), "DualShock 4")

    def test_eden_no_binding(self):
        self.assertEqual(eden_input_cmds._configured_pad("[Controls]\n", "player_0"), "")

    def test_ryujinx_bound_vs_unbound(self):
        self.assertEqual(
            ryujinx_input_cmds._configured_pad({"id": "0-00000003-054c-0000-cc09-000000006800"}),
            "DualShock 4")
        self.assertEqual(
            ryujinx_input_cmds._configured_pad({"id": ryujinx_input_cmds._UNBOUND_ID}), "")
        self.assertEqual(ryujinx_input_cmds._configured_pad(None), "")


class StandalonesList(unittest.TestCase):
    """Only emulators whose systems have a gamelist appear, sorted by label."""

    def setUp(self):
        from lib import es_gamelist, es_systems
        self._es = es_systems
        self._egl = es_gamelist
        self._load, self._has = es_systems.load_systems, es_systems._has_gamelist
        self._vis = es_gamelist.visible_records
        # The Namco 246/256 tile can now appear on the retail (-datapath) setup alone, decoupled
        # from a pcsx2x6 gamelist. Neutralize the real-device retail/arcade gates so these general
        # filtering scenarios stay hermetic (the Namco tile's own gating is covered by
        # test_pcsx2x6_group).
        self._r6 = standalones_cmds._pcsx2x6_has_guncon2_retail
        self._g6 = standalones_cmds._pcsx2x6_has_guncon2
        standalones_cmds._pcsx2x6_has_guncon2_retail = lambda: False
        standalones_cmds._pcsx2x6_has_guncon2 = lambda: False

    def tearDown(self):
        self._es.load_systems, self._es._has_gamelist = self._load, self._has
        self._egl.visible_records = self._vis
        standalones_cmds._pcsx2x6_has_guncon2_retail = self._r6
        standalones_cmds._pcsx2x6_has_guncon2 = self._g6

    def _stub(self, with_games):
        allsys = {s: [] for s in ("ps2", "ps3", "xbox", "switch", "wiiu", "gc",
                                   "wii", "model2", "model3", "openbor", "daphne")}
        self._es.load_systems = lambda: dict(allsys)
        self._es._has_gamelist = lambda s, _g=set(with_games): s in _g
        # A system counts as present only with >=1 VISIBLE game (not just a gamelist.xml on disk).
        self._egl.visible_records = lambda s, _g=set(with_games): {"g": 1} if s in _g else {}

    def test_filtered_and_alphabetical(self):
        self._stub({"ps2", "xbox"})            # only PS2 + Xbox have games
        labels = [t["label"] for t in standalones_cmds._standalones_list({})["tiles"]]
        self.assertEqual(labels, ["PlayStation 2", "Xbox"])   # filtered + A→Z

    def test_switch_group_shows_when_games(self):
        self._stub({"switch"})
        keys = [t["key"] for t in standalones_cmds._standalones_list({})["tiles"]]
        self.assertEqual(keys, ["switch"])

    def test_none_when_no_games(self):
        self._stub(set())
        self.assertEqual(standalones_cmds._standalones_list({})["tiles"], [])

    def test_emptied_gamelist_hidden(self):
        # xbox's gamelist.xml still EXISTS but has 0 visible games (user deleted its last game);
        # it must drop off, while ps2 (real games) stays. Guards the visible_records gate.
        self._es.load_systems = lambda: {"ps2": [], "xbox": []}
        self._es._has_gamelist = lambda s: s in {"ps2", "xbox"}          # both files present
        self._egl.visible_records = lambda s: {"g": 1} if s == "ps2" else {}
        labels = [t["label"] for t in standalones_cmds._standalones_list({})["tiles"]]
        self.assertEqual(labels, ["PlayStation 2"])                      # emptied xbox dropped


if __name__ == "__main__":
    unittest.main()
