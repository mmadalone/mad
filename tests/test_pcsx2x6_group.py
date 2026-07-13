"""Tests for the Namco 246/256 (pcsx2x6) GROUP tile: Arcade + Retail members, each a
full settings tree. Locks the section structure the standalones tile hands the C++."""
import unittest
from unittest import mock

from lib import es_systems
from lib.madsrv import standalones_cmds as sc


def _flat(secs):
    out = []
    for s in secs:
        if s.get("kind") == "group":
            out.extend(_flat(s.get("sections", [])))
        else:
            out.append((s["kind"], s.get("arg")))
    return out


class Group(unittest.TestCase):
    def setUp(self):
        self._orig_r = sc._pcsx2x6_has_guncon2_retail
        self._orig_g = sc._pcsx2x6_has_guncon2
        sc._pcsx2x6_has_guncon2 = lambda: True         # arcade lightgun present
        sc._pcsx2x6_has_guncon2_retail = lambda: True  # retail installed

    def tearDown(self):
        sc._pcsx2x6_has_guncon2_retail = self._orig_r
        sc._pcsx2x6_has_guncon2 = self._orig_g

    def _members(self):
        return {m["key"]: m for m in sc._pcsx2x6_members("")}

    def test_two_members_arcade_and_retail(self):
        m = self._members()
        self.assertEqual(sorted(m), ["pcsx2x6_arcade", "pcsx2x6_retail"])
        self.assertEqual(m["pcsx2x6_arcade"]["label"], "Arcade")
        self.assertEqual(m["pcsx2x6_retail"]["label"], "Retail")

    def test_member_top_level_sections(self):
        for key in ("pcsx2x6_arcade", "pcsx2x6_retail"):
            labels = [s["label"] for s in self._members()[key]["sections"]]
            self.assertEqual(labels, ["Graphics", "Input", "Audio", "Advanced"])

    def test_graphics_group_has_video_emulation_osd(self):
        gfx = self._members()["pcsx2x6_arcade"]["sections"][0]
        self.assertEqual(gfx["kind"], "group")
        self.assertEqual([s["label"] for s in gfx["sections"]],
                         ["Video", "Emulation", "On-Screen Display"])

    def test_video_has_nine_tab_pages(self):
        gfx = self._members()["pcsx2x6_arcade"]["sections"][0]
        video = gfx["sections"][0]
        self.assertEqual(video["label"], "Video")
        self.assertEqual(video["kind"], "group")
        args = [s["arg"] for s in video["sections"]]
        self.assertEqual(len(args), 9)
        self.assertTrue(all(a.startswith("x6a_gfx_") for a in args))
        # retail member uses the x6r_ namespaces
        rgfx = self._members()["pcsx2x6_retail"]["sections"][0]
        self.assertTrue(all(s["arg"].startswith("x6r_gfx_") for s in rgfx["sections"][0]["sections"]))

    def test_audio_and_advanced_point_at_member_namespaces(self):
        a = self._members()["pcsx2x6_arcade"]["sections"]
        aud = next(s for s in a if s["label"] == "Audio")
        adv = next(s for s in a if s["label"] == "Advanced")
        self.assertEqual((aud["kind"], aud["arg"]), ("settings", "x6a_aud"))
        self.assertEqual((adv["kind"], adv["arg"]), ("settings", "x6a_adv"))

    def test_arcade_input_group(self):
        inp = next(s for s in self._members()["pcsx2x6_arcade"]["sections"] if s["label"] == "Input")
        self.assertEqual([s["label"] for s in inp["sections"]],
                         ["Global", "Pads → players", "Controller Port 1",
                          "Controller Port 2", "USB Port 1", "USB Port 2", "JVS controls",
                          "Hotkeys", "Lightgun"])   # Lightgun gated on guncon2 (True in setUp)
        got = _flat([inp])
        self.assertIn(("settings", "x6a_global"), got)        # Global settings page
        self.assertIn(("pads_map", "pcsx2x6"), got)           # pads -> players PRESERVED
        self.assertIn(("input_map", "x6a_pad1"), got)         # per-port pad page
        self.assertIn(("input_map", "x6a_usb1"), got)         # per-port USB page
        self.assertIn(("input_map", "x6a_hk"), got)           # per-member hotkeys
        self.assertIn(("settings", "pcsx2x6_lightgun"), got)  # crosshair/Sinden PRESERVED (gated)

    def test_retail_input_group_guns_focused(self):
        # Retail is lightgun-only: Global settings + the two gun USB ports + Hotkeys. No
        # DualShock2 Controller Ports, no Pads -> players, no JVS (see the Group decision).
        inp = next(s for s in self._members()["pcsx2x6_retail"]["sections"] if s["label"] == "Input")
        labels = [s["label"] for s in inp["sections"]]
        self.assertEqual(labels, ["Global", "Gun 1 (USB Port 1)", "Gun 2 (USB Port 2)", "Hotkeys"])
        got = _flat([inp])
        self.assertIn(("settings", "x6r_global"), got)     # Global settings page (retail ns)
        self.assertIn(("input_map", "x6r_usb1"), got)      # Gun 1 (single-port guncon2_retail view)
        self.assertIn(("input_map", "x6r_usb2"), got)      # Gun 2
        self.assertIn(("input_map", "x6r_hk"), got)        # per-member hotkeys
        # gun setup -> none of the pad-centric leaves
        for absent in ("Controller Port 1", "Controller Port 2", "Pads → players", "JVS controls"):
            self.assertNotIn(absent, labels)

    def test_collapses_to_single_tile_when_no_retail(self):
        sc._pcsx2x6_has_guncon2_retail = lambda: False
        members = sc._pcsx2x6_members("")
        self.assertEqual([m["key"] for m in members], ["pcsx2x6_arcade"])

    def test_arcade_lightgun_gated_off_when_no_guncon2(self):
        sc._pcsx2x6_has_guncon2 = lambda: False
        inp = next(s for s in self._members()["pcsx2x6_arcade"]["sections"] if s["label"] == "Input")
        self.assertNotIn(("settings", "pcsx2x6_lightgun"), _flat([inp]))

    def test_jvs_controls_in_arcade_input_only(self):
        # the arcade GunCon2 "Gun Adjust" calibration toggle ([JVS] TestMode) must be reachable.
        arc = next(s for s in self._members()["pcsx2x6_arcade"]["sections"] if s["label"] == "Input")
        self.assertIn(("settings", "pcsx2x6_jvs"), _flat([arc]))
        # retail (PS2 discs) has no arcade JVS.
        ret = next(s for s in self._members()["pcsx2x6_retail"]["sections"] if s["label"] == "Input")
        self.assertNotIn("pcsx2x6_jvs", [a for _, a in _flat([ret])])

    def test_standalones_list_emits_both_members(self):
        with mock.patch.object(es_systems, "load_systems", lambda: ["pcsx2x6", "ps2"]), \
             mock.patch.object(es_systems, "_has_gamelist", lambda s: True):
            tiles = sc._standalones_list({})["tiles"]
        tile = next(t for t in tiles if t["key"] == "pcsx2x6")
        self.assertIn("members", tile)
        self.assertEqual([m["label"] for m in tile["members"]], ["Arcade", "Retail"])

    def test_retail_only_tile_reachable_without_arcade_games(self):
        # No pcsx2x6 gamelist (retail discs live under ps2), but retail installed -> the tile is
        # still present, collapsed to Retail, and GunCon2 stays reachable. Guards the relocation
        # regression the review caught (findings retail-guncon2-unreachable-*).
        with mock.patch.object(es_systems, "load_systems", lambda: ["ps2"]), \
             mock.patch.object(es_systems, "_has_gamelist", lambda s: True):
            tiles = sc._standalones_list({})["tiles"]
        tile = next((t for t in tiles if t["key"] == "pcsx2x6"), None)
        self.assertIsNotNone(tile, "Namco tile wrongly dropped when only Retail is installed")
        self.assertNotIn("members", tile)   # single member -> collapsed to sections
        self.assertIn(("input_map", "x6r_usb1"), _flat(tile["sections"]))   # retail Gun 1 reachable

    def test_tile_absent_when_neither_arcade_nor_retail(self):
        sc._pcsx2x6_has_guncon2_retail = lambda: False
        with mock.patch.object(es_systems, "load_systems", lambda: ["ps2"]), \
             mock.patch.object(es_systems, "_has_gamelist", lambda s: True):
            tiles = sc._standalones_list({})["tiles"]
        self.assertNotIn("pcsx2x6", [t["key"] for t in tiles])


if __name__ == "__main__":
    unittest.main()
