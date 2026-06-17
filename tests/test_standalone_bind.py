"""
Tests for the Standalones launch-binder writers — currently pcsx2_cfg.assign_devices
(the explicit ordered pads -> [Pad1..N] writer the launch wrapper calls). Pure given
(players, PCSX2.ini): no hardware, runs against a temp copy of the fixture.

Run:  python3 -m unittest tests.test_standalone_bind -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import inifile, pcsx2_cfg, rpcs3_cfg, switch_bind, xemu_cfg
from lib.madsrv import pads_cmds
from tests._fakes import patch_sdl, sd

DECK = "28de:1205"
XFIX = Path(__file__).parent / "fixtures" / "xemu" / "xemu.toml"

FIX = Path(__file__).parent / "fixtures" / "pcsx2" / "PCSX2.ini"
RFIX = Path(__file__).parent / "fixtures" / "rpcs3" / "Default.yml"

DS5 = "054c:0ce6"
DS4 = "054c:09cc"


def _player(text, k):
    return (rpcs3_cfg.yaml.safe_load(text) or {}).get(f"Player {k} Input", {})


def _pad(text, n):
    return inifile.section_body(text, f"Pad{n}") or ""


class Pcsx2AssignDevices(unittest.TestCase):
    def _run(self, players, manage=2):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            shutil.copy2(FIX, ini)
            pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=manage)
            return ini.read_text(encoding="utf-8")

    def test_order_maps_to_pads_by_sdl_index(self):
        # players in priority order -> Pad1=first pad's SDL index, Pad2=second.
        text = self._run([sd(1, DS5, "g1", "DualSense"), sd(2, DS4, "g2", "DualShock4")])
        self.assertIn("SDL-1/", _pad(text, 1))
        self.assertIn("SDL-2/", _pad(text, 2))
        self.assertIn("Type = DualShock2", _pad(text, 1))

    def test_order_is_respected(self):
        # reversed priority -> reversed pad indices.
        text = self._run([sd(2, DS4, "g2", "DualShock4"), sd(1, DS5, "g1", "DualSense")])
        self.assertIn("SDL-2/", _pad(text, 1))
        self.assertIn("SDL-1/", _pad(text, 2))

    def test_one_pad_disables_the_rest(self):
        text = self._run([sd(3, DS5, "g1", "DualSense")])
        self.assertIn("SDL-3/", _pad(text, 1))
        self.assertEqual(_pad(text, 2).strip(), "Type = None")

    def test_unrelated_sections_preserved(self):
        text = self._run([sd(1, DS5, "g1", "DualSense")])
        self.assertIn("TogglePause = Keyboard/Space", text)  # [Hotkeys] untouched

    def test_missing_ini_raises(self):
        with self.assertRaises(FileNotFoundError):
            pcsx2_cfg.assign_devices([sd(0, DS5, "g", "x")], ini_path="/nonexistent/PCSX2.ini")


class XemuAssignDevices(unittest.TestCase):
    """xemu binds [input.bindings] portN = '<guid>' for the ordered pads (value = GUID,
    not SDL index); non-port keys preserved, ports beyond the pad count left unbound."""

    def _bindings(self, players, manage=4):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "xemu.toml"
            shutil.copy2(XFIX, toml)
            xemu_cfg.assign_devices(players, config_path=str(toml), manage=manage)
            return inifile.section_body(toml.read_text(encoding="utf-8"), "input.bindings") or ""

    def test_order_maps_to_ports_by_guid(self):
        body = self._bindings([sd(0, DS5, "gA", "DualSense"), sd(1, DS4, "gB", "DS4")])
        self.assertIn("port1 = 'gA'", body)
        self.assertIn("port2 = 'gB'", body)
        self.assertNotIn("port3", body)              # only 2 pads -> ports 3/4 unbound
        self.assertIn("keyboard = ", body)           # non-port key preserved

    def test_order_is_respected(self):
        body = self._bindings([sd(1, DS4, "gB", "DS4"), sd(0, DS5, "gA", "DualSense")])
        self.assertIn("port1 = 'gB'", body)
        self.assertIn("port2 = 'gA'", body)

    def test_missing_toml_raises(self):
        with self.assertRaises(FileNotFoundError):
            xemu_cfg.assign_devices([sd(0, DS5, "g", "x")], config_path="/nonexistent/xemu.toml")


class Pcsx2BindRestoreRoundtrip(unittest.TestCase):
    """PCSX2 is TRANSIENT (also launched via Steam UI on the go): the launch wrapper
    snapshots [Pad*] -> binds MAD's order -> restores on exit so the Steam-UI resting
    config survives. The restore must return the [Pad*] sections to their pre-bind bytes."""

    def test_pcsx2_is_transient(self):
        self.assertIn("pcsx2", switch_bind._TRANSIENT)
        self.assertIn(switch_bind._PCSX2_INI, list(switch_bind._known_configs()))

    def test_snapshot_bind_restore_returns_original(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            shutil.copy2(FIX, ini)
            original = ini.read_text(encoding="utf-8")

            snap = switch_bind._snapshot("pcsx2", ini)        # what bind() stashes
            side = switch_bind._sidecar(ini)
            side.write_text(json.dumps({"emu": "pcsx2", "input": snap}), encoding="utf-8")

            pcsx2_cfg.assign_devices([sd(1, DS5, "g", "DualSense")], ini_path=str(ini))
            self.assertIn("SDL-1/", _pad(ini.read_text(encoding="utf-8"), 1))  # changed

            switch_bind.restore_target(ini)                   # game-end restore
            restored = ini.read_text(encoding="utf-8")
            self.assertEqual(_pad(restored, 1), _pad(original, 1))
            self.assertEqual(_pad(restored, 2), _pad(original, 2))
            self.assertIn("TogglePause = Keyboard/Space", restored)  # [Hotkeys] kept
            self.assertFalse(side.exists())                   # sidecar consumed


_PCSX2_MULTITAP_INI = (
    "[Pad]\nMultitapPort1 = false\nMultitapPort2 = false\nPointerXScale = 8\n\n"
    + "".join(f"[Pad{k}]\nType = None\n\n" for k in range(1, 9))
    + "[Hotkeys]\nTogglePause = Keyboard/Space\n"
)


class Pcsx2MultitapMapping(unittest.TestCase):
    """PS2 maps priority pads to PCSX2 [PadN] slots PORT-1-FIRST and toggles
    [Pad] MultitapPort1/2: 1-2 pads = ports 1&2 no multitap; 3-4 = one multitap on
    port 1 (Pad1,Pad3,Pad4,Pad5); 5-8 = both multitaps. Verified pad→port mapping."""

    def _run(self, n):
        players = [sd(i, DS5, f"g{i}", f"DS{i}") for i in range(n)]
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            ini.write_text(_PCSX2_MULTITAP_INI, encoding="utf-8")
            pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8)
            return ini.read_text(encoding="utf-8")

    def _mt(self, text):
        body = inifile.section_body(text, "Pad") or ""
        return ("MultitapPort1 = true" in body, "MultitapPort2 = true" in body)

    def test_slot_plan_table(self):
        self.assertEqual(pcsx2_cfg._slot_plan(2), ([1, 2], False, False))
        self.assertEqual(pcsx2_cfg._slot_plan(4), ([1, 3, 4, 5], True, False))
        self.assertEqual(pcsx2_cfg._slot_plan(8), ([1, 3, 4, 5, 2, 6, 7, 8], True, True))

    def test_two_pads_ports_no_multitap(self):
        text = self._run(2)
        self.assertIn("SDL-0/", _pad(text, 1))
        self.assertIn("SDL-1/", _pad(text, 2))           # P2 on port 2 (Pad2)
        self.assertEqual(self._mt(text), (False, False))

    def test_four_pads_one_multitap_on_port1(self):
        text = self._run(4)
        # players 1-4 (idx 0-3) -> Pad1,Pad3,Pad4,Pad5
        self.assertIn("SDL-0/", _pad(text, 1))
        self.assertIn("SDL-1/", _pad(text, 3))
        self.assertIn("SDL-2/", _pad(text, 4))
        self.assertIn("SDL-3/", _pad(text, 5))
        self.assertEqual(_pad(text, 2).strip(), "Type = None")   # port 2 unused
        self.assertEqual(self._mt(text), (True, False))
        self.assertIn("PointerXScale = 8", inifile.section_body(text, "Pad"))  # other key kept

    def test_eight_pads_both_multitaps(self):
        text = self._run(8)
        for slot, idx in [(1, 0), (3, 1), (4, 2), (5, 3), (2, 4), (6, 5), (7, 6), (8, 7)]:
            self.assertIn(f"SDL-{idx}/", _pad(text, slot))
        self.assertEqual(self._mt(text), (True, True))


class Pcsx2MultitapRestore(unittest.TestCase):
    """Binding 3+ pads flips MultitapPort1/2; the on-exit restore must revert the
    [Pad] section (and the [PadN] blocks) to the resting Steam-UI config."""

    def test_restore_reverts_multitap_and_pads(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            ini.write_text(_PCSX2_MULTITAP_INI, encoding="utf-8")
            orig = ini.read_text(encoding="utf-8")

            snap = switch_bind._snapshot("pcsx2", ini)
            self.assertIn("Pad", snap)                   # [Pad] captured
            side = switch_bind._sidecar(ini)
            side.write_text(json.dumps({"emu": "pcsx2", "input": snap}), encoding="utf-8")

            players = [sd(i, DS5, f"g{i}", f"DS{i}") for i in range(4)]
            pcsx2_cfg.assign_devices(players, ini_path=str(ini), manage=8)
            self.assertIn("MultitapPort1 = true", inifile.section_body(ini.read_text(), "Pad"))

            switch_bind.restore_target(ini)
            restored = ini.read_text(encoding="utf-8")
            self.assertEqual(inifile.section_body(restored, "Pad"),
                             inifile.section_body(orig, "Pad"))      # multitap back to false
            for k in range(1, 9):
                self.assertEqual(_pad(restored, k), _pad(orig, k))   # all slots reverted
            self.assertIn("TogglePause = Keyboard/Space", restored)
            self.assertFalse(side.exists())


class XemuBindRestoreRoundtrip(unittest.TestCase):
    """xemu is TRANSIENT too: snapshot [input.bindings] -> bind -> restore on exit."""

    def test_xemu_is_transient(self):
        self.assertIn("xemu", switch_bind._TRANSIENT)
        self.assertIn(switch_bind._XEMU_TOML, list(switch_bind._known_configs()))

    def test_snapshot_bind_restore_returns_original(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "xemu.toml"
            shutil.copy2(XFIX, toml)
            original = toml.read_text(encoding="utf-8")

            snap = switch_bind._snapshot("xemu", toml)
            side = switch_bind._sidecar(toml)
            side.write_text(json.dumps({"emu": "xemu", "input": snap}), encoding="utf-8")

            xemu_cfg.assign_devices([sd(0, DS5, "gA", "DualSense")], config_path=str(toml))
            self.assertIn("port1 = 'gA'", toml.read_text(encoding="utf-8"))   # changed

            switch_bind.restore_target(toml)
            restored = toml.read_text(encoding="utf-8")
            self.assertEqual(inifile.section_body(restored, "input.bindings"),
                             inifile.section_body(original, "input.bindings"))
            self.assertFalse(side.exists())


class Rpcs3AssignDevices(unittest.TestCase):
    """RPCS3 binds each `Player N Input` -> Device '<name> <rank>' (rank = 1-based
    position among same-named SDL devices by index); managed slots beyond the pad
    count -> Handler: Null. manage defaults to 4."""

    def _run(self, players, sdl=None, manage=7):
        with tempfile.TemporaryDirectory() as d:
            yml = Path(d) / "Default.yml"
            shutil.copy2(RFIX, yml)
            with patch_sdl(players if sdl is None else sdl):
                rpcs3_cfg.assign_devices(players, config_path=str(yml), manage=manage)
            return yml.read_text(encoding="utf-8")

    def test_order_maps_to_players_by_name_rank(self):
        text = self._run([sd(0, DS5, "gA", "DualSense"), sd(1, DS4, "gB", "DualShock4")])
        self.assertEqual(_player(text, 1)["Device"], "DualSense 1")
        self.assertEqual(_player(text, 2)["Device"], "DualShock4 1")
        self.assertEqual(_player(text, 1)["Handler"], "SDL")

    def test_order_is_respected(self):
        text = self._run([sd(1, DS4, "gB", "DualShock4"), sd(0, DS5, "gA", "DualSense")])
        self.assertEqual(_player(text, 1)["Device"], "DualShock4 1")
        self.assertEqual(_player(text, 2)["Device"], "DualSense 1")

    def test_two_identical_get_distinct_ranks(self):
        # rank is by SDL INDEX, not priority order: P1=the idx-2 pad -> '… 2'.
        p_hi, p_lo = sd(2, DS4, "g", "Wireless Controller"), sd(0, DS4, "g", "Wireless Controller")
        text = self._run([p_hi, p_lo], sdl=[p_lo, p_hi])
        self.assertEqual(_player(text, 1)["Device"], "Wireless Controller 2")
        self.assertEqual(_player(text, 2)["Device"], "Wireless Controller 1")

    def test_one_pad_nulls_the_rest(self):
        text = self._run([sd(0, DS5, "gA", "DualSense")])
        self.assertEqual(_player(text, 1)["Device"], "DualSense 1")
        for k in (2, 3, 4, 5, 6, 7):                     # manage=7 (PS3 max)
            self.assertEqual(_player(text, k)["Handler"], "Null")

    def test_missing_yml_raises(self):
        with self.assertRaises(FileNotFoundError):
            rpcs3_cfg.assign_devices([sd(0, DS5, "g", "x")],
                                     config_path="/nonexistent/Default.yml")


class Rpcs3BindRestoreRoundtrip(unittest.TestCase):
    """RPCS3 is TRANSIENT (also launched via Steam UI on the go): snapshot the
    `Player N Input` blocks -> bind MAD's order -> restore on exit. Restore returns the
    original Player blocks, removes any block the bind added, and keeps other settings."""

    def test_rpcs3_is_transient(self):
        self.assertIn("rpcs3", switch_bind._TRANSIENT)
        self.assertIn(switch_bind._RPCS3_YML, list(switch_bind._known_configs()))

    def test_snapshot_bind_restore_returns_original(self):
        with tempfile.TemporaryDirectory() as d:
            yml = Path(d) / "Default.yml"
            # 2 Player blocks + a non-input top-level setting that must survive intact.
            # Null handlers are the STRING "Null" (quoted), exactly as RPCS3 writes them.
            yml.write_text(
                "Player 1 Input:\n  Handler: Keyboard\n  Device: 'Keyboard'\n"
                "  Config: {x: 1}\n  Buddy Device: 'Null'\n"
                "Player 2 Input:\n  Handler: 'Null'\n  Device: 'Null'\n  Config: {}\n"
                "  Buddy Device: 'Null'\n"
                "Miscellaneous:\n  Pad handling sleep: 1000\n",
                encoding="utf-8")

            snap = switch_bind._snapshot("rpcs3", yml)          # what bind() stashes
            side = switch_bind._sidecar(yml)
            side.write_text(json.dumps({"emu": "rpcs3", "input": snap}), encoding="utf-8")

            with patch_sdl([sd(0, DS5, "gA", "DualSense")]):
                rpcs3_cfg.assign_devices([sd(0, DS5, "gA", "DualSense")], config_path=str(yml))
            bound = rpcs3_cfg.yaml.safe_load(yml.read_text(encoding="utf-8"))
            self.assertEqual(bound["Player 1 Input"]["Device"], "DualSense 1")   # changed
            self.assertIn("Player 4 Input", bound)               # bind added slots 3,4

            switch_bind.restore_target(yml)                      # game-end restore
            rdata = rpcs3_cfg.yaml.safe_load(yml.read_text(encoding="utf-8"))
            self.assertEqual(rdata["Player 1 Input"]["Handler"], "Keyboard")
            self.assertEqual(rdata["Player 2 Input"]["Handler"], "Null")
            self.assertNotIn("Player 3 Input", rdata)            # bind-added blocks gone
            self.assertNotIn("Player 4 Input", rdata)
            self.assertEqual(rdata["Miscellaneous"]["Pad handling sleep"], 1000)  # kept
            self.assertFalse(side.exists())                      # sidecar consumed


class HandheldFallback(unittest.TestCase):
    """The Deck pad (handheld_class) is bound ONLY when no external pad is present."""

    def _resolve(self, pads, hh):
        saved = {n: getattr(pads_cmds, n) for n in
                 ("_real_pads", "_supported", "_ordered", "_handheld_class")}
        pads_cmds._real_pads = lambda pump=True: list(pads)
        pads_cmds._supported = lambda emu, ps: list(ps)
        pads_cmds._ordered = lambda emu, ps, allp: list(ps)
        pads_cmds._handheld_class = lambda emu: hh
        try:
            return [d.vidpid for d in switch_bind._resolve_pads("pcsx2")]
        finally:
            for n, fn in saved.items():
                setattr(pads_cmds, n, fn)

    def test_external_present_drops_the_deck(self):
        got = self._resolve([sd(0, DECK, "g", "Steam Deck"), sd(1, DS4, "g", "DS4")], hh=DECK)
        self.assertEqual(got, [DS4])          # Deck dropped — external wins

    def test_only_deck_falls_back_to_it(self):
        got = self._resolve([sd(0, DECK, "g", "Steam Deck")], hh=DECK)
        self.assertEqual(got, [DECK])         # no external -> Deck is P1

    def test_no_handheld_class_keeps_the_deck(self):
        # emus with no handheld_class (e.g. ryujinx) treat the Deck as a normal pad.
        got = self._resolve([sd(0, DECK, "g", "Steam Deck"), sd(1, DS4, "g", "DS4")], hh="")
        self.assertEqual(got, [DECK, DS4])    # no fallback -> no Switch regression


if __name__ == "__main__":
    unittest.main()
