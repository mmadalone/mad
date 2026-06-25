"""Tests for the pcsx2x6 (Namco 246/256) standalone tile.

pcsx2x6 reuses the (already golden-tested) pcsx2_cfg writer, pointed at its PORTABLE
ini. These tests lock in the pcsx2x6-SPECIFIC invariants:
  • sections: Settings / Input mapping / Controllers, plus a Lightgun section that
    appears ONLY when a USB port = guncon2,
  • NON-transient (unlike pcsx2); launch target is the portable ini; 2 players,
  • the pad bind / input remap NEVER disturb the guncon2 ([USB1/2]) or [JVS] regions,
  • the input-map page offers P1/P2 and SEEDS an SDL DualShock2 block on first remap
    (so the keyboard [Pad1] is editable without launching first),
  • the identified X-Arcade is labelled "X-Arcade" in the pads picker.

Run:  python3 -m unittest tests.test_pcsx2x6 -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from lib import inifile, pcsx2_cfg, switch_bind, proc_guard
from lib.madsrv import pads_cmds, rpc, standalones_cmds
from lib.madsrv import pcsx2x6_cmds, pcsx2x6_input_cmds, pcsx2x6_lightgun_cmds  # noqa: F401

FIX = Path(__file__).parent / "fixtures" / "pcsx2x6" / "PCSX2.ini"
DS5 = "054c:0ce6"   # DualSense
PORTABLE = "Applications/pcsx2x6/PCSX2x6/inis/PCSX2.ini"
GUN_SECTIONS = ("USB1", "USB2", "JVS")
ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "pcsx2x6")


def _dev(index, vidpid=DS5, name="DualSense"):
    return SimpleNamespace(index=index, vidpid=vidpid, name=name, guid="g")


def _all_settings(payload):
    return [s for g in payload["groups"] for s in g["settings"]]


class Sections(unittest.TestCase):
    def test_lightgun_section_gated_on_guncon2(self):
        orig = standalones_cmds._pcsx2x6_has_guncon2
        try:
            standalones_cmds._pcsx2x6_has_guncon2 = lambda: True
            with_gun = [(s["kind"], s.get("arg")) for s in standalones_cmds._sections_for(ENTRY)]
            standalones_cmds._pcsx2x6_has_guncon2 = lambda: False
            without = [s["kind"] for s in standalones_cmds._sections_for(ENTRY)]
        finally:
            standalones_cmds._pcsx2x6_has_guncon2 = orig
        self.assertEqual(with_gun, [("settings", "pcsx2x6"), ("input_map", "pcsx2x6"),
                                    ("pads_map", "pcsx2x6"), ("settings", "pcsx2x6_lightgun")])
        self.assertEqual(without, ["settings", "input_map", "pads_map"])  # no Lightgun

    def test_has_guncon2_reads_usb_type(self):
        # the helper keys off [USB1]/[USB2] Type == guncon2 in the portable ini
        self.assertTrue(callable(standalones_cmds._pcsx2x6_has_guncon2))

    def test_rpcs_registered(self):
        for m in ("pcsx2x6.get", "pcsx2x6.set", "pcsx2x6.input_get", "pcsx2x6.input_set",
                  "pcsx2x6_lightgun.get", "pcsx2x6_lightgun.set"):
            self.assertIn(m, rpc._METHODS, m)


class SettingsLightgunSplit(unittest.TestCase):
    """The controller-type picker lives on Settings; crosshair/border/Start move to the
    Lightgun page. (Both read the live portable ini; assert structure, not values.)"""

    def test_settings_has_type_picker_not_gun_config(self):
        titles = [g["title"] for g in pcsx2x6_cmds.GROUPS]
        self.assertIn("Controller type", titles)
        self.assertNotIn("Crosshairs", titles)
        self.assertNotIn("Sinden border", titles)
        # the two per-port Type pickers write [USB1]/[USB2] Type
        picker = [it for g in pcsx2x6_cmds.GROUPS if g["title"] == "Controller type"
                  for it in g["items"]]
        self.assertEqual({it["section"] for it in picker}, {"USB1", "USB2"})
        self.assertEqual({it.get("name", it["key"]) for it in picker}, {"Type"})
        self.assertIn("guncon2", picker[0]["options_stored"])
        self.assertIn("hidmouse", picker[0]["options_stored"])

    def test_settings_jvs_section_and_testmode_label(self):
        jvs = [g for g in pcsx2x6_cmds.GROUPS if g["title"] == "JVS"]
        self.assertTrue(jvs, "Settings should have a 'JVS' group (not 'Lightgun / JVS')")
        self.assertNotIn("Lightgun / JVS", [g["title"] for g in pcsx2x6_cmds.GROUPS])
        tm = jvs[0]["items"][0]
        self.assertEqual(tm["key"], "TestMode")
        self.assertEqual(tm["label"], "Testmode")

    def test_lightgun_page_has_gun_config_and_start_only(self):
        titles = [g["title"] for g in pcsx2x6_lightgun_cmds._groups()]
        self.assertEqual(titles, ["Crosshairs", "Sinden border"])
        actions = pcsx2x6_lightgun_cmds._ACTION_GROUP["settings"]
        self.assertEqual([a["label"] for a in actions], ["▶ Start Sinden guns"])  # no Calibrate
        self.assertEqual(actions[0]["rpc"], "sinden.driver")
        self.assertEqual(actions[0]["args"], {"action": "start"})

    def test_crosshair_image_picker_scans_dir(self):
        lg = pcsx2x6_lightgun_cmds
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "Green.png").write_bytes(b"")
            (dd / "Red.png").write_bytes(b"")
            with mock.patch.object(lg, "_CROSSHAIR_DIR", dd):
                items = lg._crosshair_items()
        paths = [it for it in items if it.get("name", it["key"]) == "guncon2_cursor_path"]
        self.assertEqual({it["section"] for it in paths}, {"USB1", "USB2"})   # per gun
        self.assertEqual(paths[0]["options_display"], ["Green", "Red"])       # sorted stems
        self.assertTrue(all(p.endswith(".png") for p in paths[0]["options_stored"]))

    def test_crosshair_picker_omitted_when_no_images(self):
        lg = pcsx2x6_lightgun_cmds
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(lg, "_CROSSHAIR_DIR", Path(d)):   # empty dir
                keys = {it.get("name", it["key"]) for it in lg._crosshair_items()}
        self.assertNotIn("guncon2_cursor_path", keys)   # no images -> no picker
        self.assertIn("guncon2_cursor_scale", keys)     # size row always present


class BindWiring(unittest.TestCase):
    def test_non_transient(self):
        self.assertNotIn("pcsx2x6", switch_bind._TRANSIENT)
        self.assertIn("pcsx2", switch_bind._TRANSIENT)

    def test_two_managed_players(self):
        self.assertEqual(switch_bind._PLAYERS["pcsx2x6"], 2)
        self.assertEqual(pads_cmds._EMUS["pcsx2x6"]["players"], 2)

    def test_target_is_portable_ini(self):
        self.assertTrue(str(switch_bind._target("pcsx2x6", "NM00003.acgame")).endswith(PORTABLE))

    def test_handheld_class_is_deck(self):
        self.assertEqual(pads_cmds._handheld_class("pcsx2x6"), "28de:1205")


class GunSafety(unittest.TestCase):
    def _bind(self, players):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        shutil.copy2(FIX, ini)
        before = {s: inifile.section_body(ini.read_text(), s) for s in GUN_SECTIONS}
        pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=2)
        text = ini.read_text()
        after = {s: inifile.section_body(text, s) for s in GUN_SECTIONS}
        return before, after, text

    def test_single_dualsense_baked_ds2_guns_untouched(self):
        before, after, text = self._bind([_dev(0)])
        pad1 = inifile.section_body(text, "Pad1")
        self.assertIn("Cross = SDL-0/FaceSouth", pad1)
        self.assertNotIn("Keyboard/", pad1)
        self.assertEqual(before, after)

    def test_two_players_no_multitap_guns_untouched(self):
        before, after, text = self._bind([_dev(0), _dev(1)])
        self.assertIn("Cross = SDL-1/FaceSouth", inifile.section_body(text, "Pad2"))
        self.assertIn("MultitapPort1 = false", inifile.section_body(text, "Pad"))
        self.assertEqual(before, after)


class InputMap(unittest.TestCase):
    def _ini(self):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        shutil.copy2(FIX, ini)
        return ini

    def _with_ini(self, ini, fn):
        inp = pcsx2x6_input_cmds
        oi, orun = inp._INI, inp._running
        try:
            inp._INI, inp._running = ini, (lambda: False)
            return fn(inp)
        finally:
            inp._INI, inp._running = oi, orun

    def test_two_players_all_capturable(self):
        ini = self._ini()
        g = self._with_ini(ini, lambda inp: inp._input_get({}))
        self.assertEqual([p["label"] for p in g["players"]], ["Player 1", "Player 2"])
        self.assertTrue(all(b["capturable"] for grp in g["groups"] for b in grp["binds"]))

    def test_remap_writes_store_not_ini(self):
        ini = self._ini()
        before = ini.read_text()
        self._with_ini(ini, lambda inp: inp._input_set(
            {"id": "Cross", "kind": "btn", "value": 0x131, "player": "1"}))  # BTN_EAST
        # the remap goes to the per-player store, NOT [PadN]; the ini is untouched
        self.assertEqual(pcsx2_cfg.load_input_overrides(ini).get(1, {}).get("Cross"), "FaceEast")
        self.assertEqual(ini.read_text(), before)

    def test_p2_remap_survives_single_pad_launch(self):
        # H1 regression: a Player-2 remap must survive a later 1-pad launch (it lives in
        # the store, not the wiped [Pad2]).
        ini = self._ini()
        pcsx2_cfg.save_input_overrides(ini, {2: {"Triangle": "FaceEast"}})
        ov = pcsx2_cfg.load_input_overrides(ini)
        pcsx2_cfg.assign_devices([_dev(0)], ini_path=str(ini), manage=2, overrides=ov)  # 1 pad
        self.assertEqual(inifile.section_body(ini.read_text(), "Pad2").strip(), "Type = None")
        self.assertEqual(pcsx2_cfg.load_input_overrides(ini).get(2, {}).get("Triangle"), "FaceEast")
        pcsx2_cfg.assign_devices([_dev(0), _dev(1)], ini_path=str(ini), manage=2,
                                 overrides=pcsx2_cfg.load_input_overrides(ini))   # 2 pads
        self.assertIn("Triangle = SDL-1/FaceEast", inifile.section_body(ini.read_text(), "Pad2"))


class XArcadeLabel(unittest.TestCase):
    def test_identified_xarcade_labelled(self):
        xa = _dev(0, "045e:02a1", "Xbox 360 Wireless Receiver")
        oreal, olbl = pads_cmds._real_pads, pads_cmds._pad_labels
        orun, oho = proc_guard.emulator_running, pads_cmds._hands_off
        try:
            pads_cmds._real_pads = lambda pump=True: [xa]
            pads_cmds._pad_labels = lambda real: {0: "X-Arcade"}
            proc_guard.emulator_running = lambda e: False
            pads_cmds._hands_off = lambda e: False
            res = pads_cmds._pads_get({"emu": "pcsx2x6"})
        finally:
            pads_cmds._real_pads, pads_cmds._pad_labels = oreal, olbl
            proc_guard.emulator_running, pads_cmds._hands_off = orun, oho
        row = next(r for r in res["pads"] if r["vidpid"] == "045e:02a1")
        self.assertIn("X-Arcade", row["label"])
        self.assertNotIn("Xbox 360", row["label"])


class BackupSidecar(unittest.TestCase):
    """MAD Backup/Restore must carry the per-player .mad-input-overrides.json sidecar
    alongside its .ini (review HIGH-1: it held remaps the .ini alone doesn't)."""

    def test_backup_copies_sidecar(self):
        from lib import mad_backup
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d); live = dd / "live"; live.mkdir()
            ini = live / "PCSX2.ini"; ini.write_text("[Pad]\n", encoding="utf-8")
            sc = live / mad_backup._OVERRIDES_NAME
            sc.write_text('{"2": {"Cross": "FaceWest"}}', encoding="utf-8")
            snap = dd / "snap"
            mad_backup.do_backup({"pcsx2": ini}, snap=snap)
            snapsc = snap / ("pcsx2_" + mad_backup._OVERRIDES_NAME)
            self.assertTrue(snapsc.is_file())
            self.assertIn("FaceWest", snapsc.read_text(encoding="utf-8"))

    def test_restore_brings_back_sidecar(self):
        from lib import mad_backup
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d); live = dd / "live"; live.mkdir(); snap = dd / "snap"
            ini = live / "PCSX2.ini"; ini.write_text("[Pad]\n", encoding="utf-8")
            sc = live / mad_backup._OVERRIDES_NAME
            sc.write_text('{"2": {"Cross": "FaceWest"}}', encoding="utf-8")
            mad_backup.do_backup({"pcsx2": ini}, snap=snap)
            sc.write_text("{}", encoding="utf-8")               # mutate the live sidecar
            trash = dd / "trash"; trash.mkdir()

            def fake_retire(paths, **kw):                       # move-aside, no ~/Downloads/_TMP
                for p in paths:
                    shutil.move(str(p), str(trash / p.name))
                return trash

            with mock.patch.object(mad_backup, "process_running", lambda *a, **k: False), \
                 mock.patch.object(mad_backup.fsutil, "recoverable_delete", fake_retire):
                mad_backup.do_restore({"pcsx2": ini}, snap=snap)
            self.assertIn("FaceWest", sc.read_text(encoding="utf-8"))   # remap restored
            self.assertEqual(ini.read_text(encoding="utf-8"), "[Pad]\n")


if __name__ == "__main__":
    unittest.main()
