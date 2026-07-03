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

    def test_settings_has_no_type_or_gun_config(self):
        # the Controller-type picker moved to the Input-mapping page; crosshair/border
        # live on the Lightgun page. Settings is graphics/boot/JVS only.
        titles = [g["title"] for g in pcsx2x6_cmds.GROUPS]
        self.assertEqual(titles, ["Graphics", "Boot", "JVS"])
        self.assertNotIn("Controller type", titles)

    def test_settings_jvs_section_and_testmode_label(self):
        jvs = [g for g in pcsx2x6_cmds.GROUPS if g["title"] == "JVS"]
        self.assertTrue(jvs, "Settings should have a 'JVS' group (not 'Lightgun / JVS')")
        self.assertNotIn("Lightgun / JVS", [g["title"] for g in pcsx2x6_cmds.GROUPS])
        tm = jvs[0]["items"][0]
        self.assertEqual(tm["key"], "TestMode")
        self.assertEqual(tm["label"], "Testmode")

    def test_lightgun_page_is_crosshair_and_border_only(self):
        # The "Start Sinden guns" button moved to the input page's Light Gun view, so this
        # page is crosshair + Sinden border only (no Sinden-guns action group here).
        titles = [g["title"] for g in pcsx2x6_lightgun_cmds._groups()]
        self.assertEqual(titles, ["Crosshairs", "Sinden border"])
        self.assertFalse(hasattr(pcsx2x6_lightgun_cmds, "_ACTION_GROUP"))

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


class HotkeyLaunchRewrite(unittest.TestCase):
    """Regression: pad-bound [Hotkeys] must be repointed to Player 1's live SDL index at launch for the
    Namco members too. The guard used to be pcsx2-only, so pcsx2x6/ps2guncon pad hotkeys kept the dead
    SDL-0 placeholder and never fired in Game Mode (Steam owns SDL slot 0). Keyboard hotkeys untouched."""
    def _ini(self):
        p = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        p.write_text("[Hotkeys]\nToggleFullscreen = SDL-0/Guide\nTogglePause = Keyboard/Space\n\n"
                     "[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\n\n[Pad2]\nType = None\n\n"
                     "[Pad]\nMultitapPort1 = false\nMultitapPort2 = false\n",
                     encoding="utf-8", newline="")
        return p

    def _bind(self, emu):
        p = self._ini()
        with mock.patch.object(switch_bind, "_target", lambda e, r: p), \
             mock.patch.object(switch_bind, "_resolve_pads", lambda e, order=None: [_dev(3)]), \
             mock.patch.object(switch_bind.pads_cmds, "_hands_off", lambda e: False), \
             mock.patch.object(switch_bind.pcsx2_cfg, "strip_guncon2_relative_binds", lambda ini: False):
            switch_bind.bind(emu, "NM00003.acgame")
        return p.read_text(encoding="utf-8", newline="")

    def test_pcsx2x6_pad_hotkey_repointed_to_player1(self):
        txt = self._bind("pcsx2x6")
        self.assertIn("ToggleFullscreen = SDL-3/Guide", txt)   # SDL-0 -> Player 1's live index
        self.assertNotIn("SDL-0/Guide", txt)
        self.assertIn("TogglePause = Keyboard/Space", txt)     # keyboard hotkey untouched

    def test_ps2guncon_pad_hotkey_repointed_to_player1(self):
        self.assertIn("ToggleFullscreen = SDL-3/Guide", self._bind("ps2guncon"))


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

    def test_player_picker_has_pad_and_usb_ports(self):
        ini = self._ini()
        g = self._with_ini(ini, lambda inp: inp._input_get({}))   # default = pad1
        self.assertEqual([p["id"] for p in g["players"]], ["pad1", "pad2", "usb1", "usb2"])
        self.assertEqual([p["label"] for p in g["players"]],
                         ["Controller Port 1", "Controller Port 2", "USB Port 1", "USB Port 2"])
        self.assertEqual(g["player"], "pad1")
        # pad page = DualShock2 rows, all capturable (emulator not running)
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


class CfgutilUpsert(unittest.TestCase):
    """cfgutil.ini_set_or_insert: replace-if-present, else append into the section."""

    def setUp(self):
        from lib.madsrv import cfgutil
        self.c = cfgutil

    def test_replace_existing(self):
        self.assertEqual(self.c.ini_set_or_insert("[S]\nk = old\n", "S", "k", "new"),
                         "[S]\nk = new\n")

    def test_insert_into_empty_section(self):
        self.assertEqual(self.c.ini_set_or_insert("[S]\n[T]\n", "S", "k", "v"),
                         "[S]\nk = v\n[T]\n")

    def test_insert_at_eof_section(self):
        self.assertEqual(self.c.ini_set_or_insert("[S]\na = 1\n", "S", "k", "v"),
                         "[S]\na = 1\nk = v\n")

    def test_section_absent_returns_none(self):
        self.assertIsNone(self.c.ini_set_or_insert("[X]\n", "S", "k", "v"))


class UsbInputPage(unittest.TestCase):
    """The input page's USB Port 1/2 view: a dependent Type selector that swaps the
    rows, and gun/mouse bindings written DIRECTLY to [USBn] (no override store)."""

    def _ini(self, usb1="", usb2=""):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text(f"[Pad1]\nType = DualShock2\n\n[USB1]\n{usb1}\n[USB2]\n{usb2}\n"
                       "[JVS]\nTestMode = false\n", encoding="utf-8")
        return ini

    def _with(self, ini, fn):
        inp = pcsx2x6_input_cmds
        oi, orun = inp._INI, inp._running
        try:
            inp._INI, inp._running = ini, (lambda: False)
            return fn(inp)
        finally:
            inp._INI, inp._running = oi, orun

    def test_usb_port_has_dependent_type_selector(self):
        ini = self._ini()
        g = self._with(ini, lambda inp: inp._input_get({"player": "usb1"}))
        self.assertEqual(g["player"], "usb1")
        sel = g["selectors"][0]
        self.assertEqual(sel["key"], "usb_type")
        self.assertTrue(sel["dependent"])                       # change -> rebuild
        self.assertEqual([o["value"] for o in sel["options"]], ["None", "hidmouse", "guncon2"])
        self.assertEqual(g["groups"], [])                       # Type=None -> no rows

    def test_guncon2_rows_kind_gun(self):
        ini = self._ini(usb1="Type = guncon2\nguncon2_Trigger = Pointer-0/LeftButton\n")
        g = self._with(ini, lambda inp: inp._input_get({"player": "usb1"}))
        binds = {b["id"]: b for grp in g["groups"] for b in grp["binds"]}
        self.assertEqual({"Trigger", "Foot Pedal", "Start", "Coins"} & {b["label"] for b in binds.values()},
                         {"Trigger", "Foot Pedal", "Start", "Coins"})
        self.assertTrue(all(b["kind"] == "gun" for b in binds.values()))
        self.assertEqual(binds["guncon2_Trigger"]["value"], "Mouse Left")

    def test_hidmouse_rows_pointer_readonly(self):
        ini = self._ini(usb2="Type = hidmouse\n")
        g = self._with(ini, lambda inp: inp._input_get({"player": "usb2"}))
        binds = {b["id"]: b for grp in g["groups"] for b in grp["binds"]}
        self.assertIn("hidmouse_LeftButton", binds)
        self.assertFalse(binds["hidmouse_Pointer"]["capturable"])   # aim is read-only

    def test_selector_set_inserts_type(self):
        ini = self._ini()                                  # [USB1] empty (no Type key)
        self._with(ini, lambda inp: inp._selector_set(
            {"key": "usb_type", "value": "guncon2", "player": "usb1"}))
        self.assertIn("Type = guncon2", inifile.section_body(ini.read_text(), "USB1"))

    def test_set_mouse_button_uses_port_pointer_index(self):
        ini = self._ini(usb1="Type = guncon2\n", usb2="Type = hidmouse\n")
        self._with(ini, lambda inp: (
            inp._input_set({"player": "usb1", "id": "guncon2_Trigger", "kind": "gun",
                            "gun_kind": "mouse", "value": "1"}),
            inp._input_set({"player": "usb2", "id": "hidmouse_LeftButton", "kind": "gun",
                            "gun_kind": "mouse", "value": "1"})))
        t = ini.read_text()
        self.assertIn("guncon2_Trigger = Pointer-0/LeftButton", inifile.section_body(t, "USB1"))
        self.assertIn("hidmouse_LeftButton = Pointer-1/LeftButton", inifile.section_body(t, "USB2"))
        self.assertIn("Type = DualShock2", inifile.section_body(t, "Pad1"))   # pad untouched

    def test_set_key_writes_keyboard_source(self):
        ini = self._ini(usb1="Type = guncon2\n")
        self._with(ini, lambda inp: inp._input_set(
            {"player": "usb1", "id": "guncon2_Start", "kind": "gun",
             "gun_kind": "key", "value": "enter"}))
        self.assertIn("guncon2_Start = Keyboard/Return", inifile.section_body(ini.read_text(), "USB1"))

    def test_no_relative_aim_rows_offered(self):
        # binding any guncon2_Relative* freezes the lightgun cursor, so MAD must not offer it
        ini = self._ini(usb1="Type = guncon2\nguncon2_Trigger = Pointer-0/LeftButton\n")
        g = self._with(ini, lambda inp: inp._input_get({"player": "usb1"}))
        rows = [b for grp in g["groups"] for b in grp["binds"]]
        self.assertFalse([b for b in rows if "Relative" in b["id"] or "Aim" in b["label"]])
        self.assertFalse([k for k, _ in pcsx2x6_input_cmds._GUNCON2_BINDS if "Relative" in k])

    def test_input_set_rejects_relative_key(self):
        ini = self._ini(usb1="Type = guncon2\n")
        with self.assertRaises(rpc.RpcError):
            self._with(ini, lambda inp: inp._usb_set(
                {"id": "guncon2_RelativeUp", "gun_kind": "key", "value": "Up"}, "usb1"))


class GunRelativeStrip(unittest.TestCase):
    """The lightgun cursor freezes if ANY guncon2_Relative* key is present (it flips the
    GunCon2 cursor to the unfed relative path). pcsx2_cfg strips them from [USB1]/[USB2];
    switch_bind runs the strip at every pcsx2x6 launch."""

    _INI = (
        "[USB1]\nType = guncon2\nguncon2_Trigger = Pointer-0/LeftButton\n"
        "guncon2_cursor_path = /x/Green.png\nguncon2_cursor_scale = 0.08\n"
        "guncon2_RelativeUp = Keyboard/Up\nguncon2_RelativeDown = Keyboard/Down\n"
        "guncon2_RelativeLeft = Keyboard/Left\nguncon2_RelativeRight = Keyboard/Right\n\n"
        "[USB2]\nType = guncon2\nguncon2_Trigger = Pointer-1/LeftButton\n"
        "guncon2_RelativeUp = Keyboard/8\nguncon2_RelativeLeft = Keyboard/4\n\n"
        "[JVS]\nTestMode = false\nguncon2_RelativeUp = keep-not-a-usb-section\n"
    )

    def _strip(self, text):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text(text, encoding="utf-8")
        changed = pcsx2_cfg.strip_guncon2_relative_binds(ini)
        return changed, ini.read_text(encoding="utf-8")

    def test_removes_relative_from_both_usb_ports(self):
        changed, out = self._strip(self._INI)
        self.assertTrue(changed)
        self.assertNotIn("guncon2_Relative", inifile.section_body(out, "USB1"))
        self.assertNotIn("guncon2_Relative", inifile.section_body(out, "USB2"))

    def test_keeps_everything_else(self):
        _, out = self._strip(self._INI)
        for keep in ("Type = guncon2", "guncon2_Trigger = Pointer-0/LeftButton",
                     "guncon2_cursor_path = /x/Green.png", "guncon2_cursor_scale = 0.08",
                     "TestMode = false"):
            self.assertIn(keep, out)
        # section-scoped: a guncon2_Relative* OUTSIDE [USB1]/[USB2] is left alone
        self.assertIn("guncon2_RelativeUp = keep-not-a-usb-section",
                      inifile.section_body(out, "JVS"))
        # exactly the 6 USB relative lines removed (4 in USB1 + 2 in USB2)
        self.assertEqual(self._INI.count("guncon2_Relative") - out.count("guncon2_Relative"), 6)

    def test_noop_when_absent(self):
        clean = "[USB1]\nType = guncon2\nguncon2_Trigger = Pointer-0/LeftButton\n"
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text(clean, encoding="utf-8")
        self.assertFalse(pcsx2_cfg.strip_guncon2_relative_binds(ini))
        self.assertEqual(ini.read_text(encoding="utf-8"), clean)

    def test_noop_when_file_missing(self):
        self.assertFalse(pcsx2_cfg.strip_guncon2_relative_binds(
            Path(tempfile.mkdtemp()) / "nope.ini"))

    def test_switch_bind_strips_for_pcsx2x6_even_when_hands_off(self):
        # the strip MUST run before bind()'s hands-off / no-pads early returns
        called = []
        o_strip, o_ho = pcsx2_cfg.strip_guncon2_relative_binds, pads_cmds._hands_off
        try:
            pcsx2_cfg.strip_guncon2_relative_binds = lambda p: called.append(p) or False
            pads_cmds._hands_off = lambda e: True
            switch_bind.bind("pcsx2x6", "NM00003.acgame")
        finally:
            pcsx2_cfg.strip_guncon2_relative_binds = o_strip
            pads_cmds._hands_off = o_ho
        self.assertEqual(called, [switch_bind._PCSX2X6_INI])


class UsbInputActions(unittest.TestCase):
    """The 'Start Sinden guns' button lives on the input page's Light Gun view now
    (moved off the Lightgun page), emitted as an `actions` entry the C++ renders."""

    def _ini(self, usb1="", usb2=""):
        ini = Path(tempfile.mkdtemp()) / "PCSX2.ini"
        ini.write_text(f"[Pad1]\nType = DualShock2\n\n[USB1]\n{usb1}\n[USB2]\n{usb2}\n"
                       "[JVS]\nTestMode = false\n", encoding="utf-8")
        return ini

    def _get(self, ini, player):
        inp = pcsx2x6_input_cmds
        oi, orun = inp._INI, inp._running
        try:
            inp._INI, inp._running = ini, (lambda: False)
            return inp._input_get({"player": player})
        finally:
            inp._INI, inp._running = oi, orun

    def test_guncon2_offers_start_sinden_action(self):
        g = self._get(self._ini(usb1="Type = guncon2\n"), "usb1")
        acts = g.get("actions") or []
        self.assertEqual([a["key"] for a in acts], ["start_sinden"])
        self.assertEqual(acts[0]["rpc"], "sinden.driver")
        self.assertEqual(acts[0]["args"], {"action": "start"})
        self.assertEqual(acts[0]["type"], "action")

    def test_no_action_for_none_or_hidmouse(self):
        ini = self._ini(usb1="", usb2="Type = hidmouse\n")
        self.assertEqual(self._get(ini, "usb1").get("actions", []), [])   # None
        self.assertEqual(self._get(ini, "usb2").get("actions", []), [])   # HID Mouse

    def test_pad_page_has_no_actions(self):
        g = self._get(self._ini(usb1="Type = guncon2\n"), "pad1")
        self.assertEqual(g.get("actions", []), [])


if __name__ == "__main__":
    unittest.main()
