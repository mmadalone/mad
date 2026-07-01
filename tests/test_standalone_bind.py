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


class InifileRemoveSection(unittest.TestCase):
    """inifile.remove_section — used by the PCSX2 restore to undo bind-added sections."""

    def test_removes_middle_section_keeps_neighbours(self):
        t = "[A]\nx = 1\n\n[B]\ny = 2\n\n[C]\nz = 3\n"
        out = inifile.remove_section(t, "B")
        self.assertIsNone(inifile.section_body(out, "B"))
        self.assertEqual(inifile.section_body(out, "A"), "x = 1")
        self.assertEqual(inifile.section_body(out, "C"), "z = 3")

    def test_removes_last_section(self):
        out = inifile.remove_section("[A]\nx = 1\n\n[B]\ny = 2\n", "B")
        self.assertIsNone(inifile.section_body(out, "B"))
        self.assertEqual(inifile.section_body(out, "A"), "x = 1")

    def test_absent_is_noop(self):
        t = "[A]\nx = 1\n"
        self.assertEqual(inifile.remove_section(t, "Z"), t)

    def test_pad_not_matched_by_pad1(self):  # [Pad] vs [Pad1] disambiguation
        t = "[Pad]\nMultitapPort1 = true\n\n[Pad1]\nType = None\n"
        out = inifile.remove_section(t, "Pad")
        self.assertIsNone(inifile.section_body(out, "Pad"))
        self.assertEqual(inifile.section_body(out, "Pad1"), "Type = None")


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

    def test_restore_removes_sections_the_bind_added(self):
        # EmuDeck-default shape: only [Pad1]/[Pad2] — no [Pad], no [Pad3..8]. The 4-pad
        # bind creates [Pad] + [Pad3..5]; restore must DELETE those (not just revert the
        # ones that existed), else multitap + phantom port-1 binds drift into a later
        # Steam-UI launch. Regression test for the snapshot/restore section-existence gap.
        emudeck = ("[Pad1]\nType = None\n\n[Pad2]\nType = None\n\n"
                   "[Hotkeys]\nTogglePause = Keyboard/Space\n")
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "PCSX2.ini"
            ini.write_text(emudeck, encoding="utf-8")
            orig = ini.read_text(encoding="utf-8")

            snap = switch_bind._snapshot("pcsx2", ini)
            self.assertIsNone(snap.get("Pad"))               # absent recorded as None
            self.assertIsNone(snap.get("Pad3"))
            side = switch_bind._sidecar(ini)
            side.write_text(json.dumps({"emu": "pcsx2", "input": snap}), encoding="utf-8")

            pcsx2_cfg.assign_devices([sd(i, DS5, f"g{i}", f"DS{i}") for i in range(4)],
                                     ini_path=str(ini), manage=8)
            bound = ini.read_text(encoding="utf-8")
            self.assertIn("MultitapPort1 = true", inifile.section_body(bound, "Pad"))  # bind added it

            switch_bind.restore_target(ini)
            restored = ini.read_text(encoding="utf-8")
            self.assertEqual(restored, orig)                 # byte-identical to pre-bind
            self.assertIsNone(inifile.section_body(restored, "Pad"))    # [Pad] gone
            self.assertIsNone(inifile.section_body(restored, "Pad3"))   # [Pad3..8] gone
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
    count -> Handler: Null. manage defaults to 7 (PS3 max)."""

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

    def test_name_overrides_honored(self):
        # ps3 is router_skip, so assign_devices is the LIVE path — it must apply the
        # documented [backends.rpcs3].name_overrides (RPCS3 binds by SDL name), for BOTH
        # the Device string and the same-name rank grouping.
        import lib.policy as pol
        orig = pol.load_merged
        pol.load_merged = lambda: {"backends": {"rpcs3": {"name_overrides": {DS4: "PS4 Controller"}}}}
        try:
            text = self._run([sd(0, DS4, "gB", "Wireless Controller")])
        finally:
            pol.load_merged = orig
        self.assertEqual(_player(text, 1)["Device"], "PS4 Controller 1")  # override, not raw name


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
        pads_cmds._ordered = lambda emu, ps, allp=None, order=None: list(ps)
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


class OrderedDisplayFallback(unittest.TestCase):
    """pads_cmds._ordered: an UNRANKED class falls back to the page DISPLAY order
    (KNOWN_PADS family order) before SDL index, so the shown Player-1 pad wins at
    launch with nothing applied — while a CONFIGURED class still beats display order."""

    def _patch_order(self, stored):
        self._saved = pads_cmds._stored_order
        pads_cmds._stored_order = lambda emu, _s=stored: list(_s)

    def tearDown(self):
        if hasattr(self, "_saved"):
            pads_cmds._stored_order = self._saved

    def test_unranked_uses_display_order_not_sdl_index(self):
        self._patch_order([])                       # nothing configured for pcsx2
        # DualShock 4 at the LOWER SDL index, DualSense higher: display order still wins.
        pads = [sd(0, DS4, "g", "DS4"), sd(1, DS5, "g", "DualSense")]
        got = [d.vidpid for d in pads_cmds._ordered("pcsx2", pads)]
        self.assertEqual(got, [DS5, DS4])           # DualSense = Player 1 (KNOWN_PADS order)

    def test_same_class_tiebreak_stays_sdl_index(self):
        self._patch_order([])
        pads = [sd(3, DS4, "g", "DS4 b"), sd(1, DS4, "g", "DS4 a")]
        got = [d.index for d in pads_cmds._ordered("pcsx2", pads)]
        self.assertEqual(got, [1, 3])               # same class -> SDL index between them

    def test_configured_class_beats_display_order(self):
        self._patch_order([DS4])                    # DS4 explicitly ranked first
        pads = [sd(0, DS5, "g", "DualSense"), sd(1, DS4, "g", "DS4")]
        got = [d.vidpid for d in pads_cmds._ordered("pcsx2", pads)]
        self.assertEqual(got, [DS4, DS5])           # stored rank overrides KNOWN_PADS order


class ManagedPlayers(unittest.TestCase):
    """pads_cmds.managed_players: the single bind-cap source — merged policy first
    (manage_pads / manage_players / manage_ports, local override included), else the
    per-emu _EMUS fallback (which must be the SAFE default)."""

    def _mp(self, emu, merged):
        import lib.policy as pol
        saved = pol.load_merged
        pol.load_merged = lambda: merged
        try:
            return pads_cmds.managed_players(emu)
        finally:
            pol.load_merged = saved

    def test_reads_manage_pads(self):
        self.assertEqual(self._mp("pcsx2", {"backends": {"pcsx2": {"manage_pads": 2}}}), 2)

    def test_reads_manage_players_and_ports(self):
        self.assertEqual(self._mp("rpcs3", {"backends": {"rpcs3": {"manage_players": 7}}}), 7)
        self.assertEqual(self._mp("xemu", {"backends": {"xemu": {"manage_ports": 4}}}), 4)

    def test_local_override_wins(self):             # PS2 4-player opt-in
        self.assertEqual(self._mp("pcsx2", {"backends": {"pcsx2": {"manage_pads": 4}}}), 4)

    def test_fallback_when_absent_is_safe(self):    # no policy key -> safe _EMUS default (2)
        self.assertEqual(self._mp("pcsx2", {"backends": {}}),
                         pads_cmds._EMUS["pcsx2"]["players"])
        self.assertEqual(pads_cmds._EMUS["pcsx2"]["players"], 2)

    def test_bad_value_ignored(self):               # bool / 0 / non-int -> fallback, not crash
        self.assertEqual(self._mp("pcsx2", {"backends": {"pcsx2": {"manage_pads": True}}}),
                         pads_cmds._EMUS["pcsx2"]["players"])


class ManagedPlayersCap(unittest.TestCase):
    """switch_bind._resolve_pads caps to managed_players: PS2 with 4 pads binds 2 ->
    _slot_plan(2) = NO multitap (the "most PS2 games get no input" fix); the 4-player
    opt-in restores the single port-1 multitap."""

    def _resolve_with_policy(self, pads, merged):
        import lib.policy as pol
        saved_lm = pol.load_merged
        saved = {n: getattr(pads_cmds, n) for n in ("_real_pads", "_supported", "_ordered")}
        pol.load_merged = lambda: merged
        pads_cmds._real_pads = lambda pump=True: list(pads)
        pads_cmds._supported = lambda emu, ps: list(ps)
        pads_cmds._ordered = lambda emu, ps, allp=None, order=None: list(ps)
        try:
            return [d.vidpid for d in switch_bind._resolve_pads("pcsx2")]
        finally:
            pol.load_merged = saved_lm
            for n, fn in saved.items():
                setattr(pads_cmds, n, fn)

    def _four_pads(self):
        return [sd(0, DS5, "g", "A"), sd(1, DS4, "g", "B"),
                sd(2, DS5, "g", "C"), sd(3, DS4, "g", "D")]

    def test_default_two_no_multitap(self):
        got = self._resolve_with_policy(self._four_pads(),
                                        {"backends": {"pcsx2": {"manage_pads": 2}}})
        self.assertEqual(len(got), 2)
        _, mt1, mt2 = pcsx2_cfg._slot_plan(len(got))
        self.assertFalse(mt1)
        self.assertFalse(mt2)

    def test_opt_in_four_restores_one_multitap(self):
        got = self._resolve_with_policy(self._four_pads(),
                                        {"backends": {"pcsx2": {"manage_pads": 4}}})
        self.assertEqual(len(got), 4)
        _, mt1, mt2 = pcsx2_cfg._slot_plan(len(got))
        self.assertTrue(mt1)
        self.assertFalse(mt2)


class Pcsx2Calibration(unittest.TestCase):
    """PS2 binds to PCSX2's OWN controller numbering (Steam owns it in Game Mode; we read it
    from PCSX2's emulog and reuse it, cached per controller set)."""

    EMULOG = (
        "[ 2.0] SDLInputSource: Opened gamepad 7 (instance id 7, player id 6): DualSense Wireless Controller\n"
        "[ 2.1] SDLInputSource: Opened gamepad 5 (instance id 5, player id 4): PS4 Controller\n"
        "[ 2.2] SDLInputSource: Opened gamepad 22 (instance id 22, player id 2): DualSense Wireless Controller\n"
        "[ 2.3] SDLInputSource: Opened gamepad 12 (instance id 12, player id 0): Steam Deck Controller\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {n: getattr(switch_bind, n)
                       for n in ("_PCSX2_EMULOG", "_PCSX2_CALIB_CACHE", "_pad_signature")}
        switch_bind._PCSX2_EMULOG = self.tmp / "emulog.txt"
        switch_bind._PCSX2_CALIB_CACHE = self.tmp / "calib.json"
        switch_bind._PCSX2_EMULOG.write_text(self.EMULOG, encoding="utf-8")
        switch_bind._pad_signature = lambda: "SIG-A"

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(switch_bind, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_norm_name_bridges_sdl2_sdl3(self):
        self.assertEqual(switch_bind._norm_pad_name("Xbox 360 Wireless Controller"),
                         switch_bind._norm_pad_name("Xbox 360 Controller"))
        self.assertEqual(switch_bind._norm_pad_name("DualSense Wireless Controller"), "dualsense")

    def test_emulog_parse(self):
        slots = switch_bind._pcsx2_emulog_slots()
        self.assertEqual(slots["dualsense"], [6, 2])   # two DualSenses keep distinct slots
        self.assertEqual(slots["ps4"], [4])
        self.assertEqual(slots["steam deck"], [0])

    def test_calibrate_matches_chosen_by_name(self):
        chosen = [sd(2, DS5, "g", "DualSense Wireless Controller"),
                  sd(0, DS4, "g", "PS4 Controller")]
        pads, _ = switch_bind._calibrate_pcsx2(chosen)
        self.assertEqual([d.index for d in pads], [6, 4])   # PCSX2's own numbers, not the raw index

    def test_unmatched_pad_falls_back_to_raw_index(self):
        chosen = [sd(9, "2dc8:2810", "g", "8BitDo FC30")]   # not in the emulog
        pads, _ = switch_bind._calibrate_pcsx2(chosen)
        self.assertEqual(pads[0].index, 9)                  # best-effort raw index

    def test_cache_hit_means_no_relearn(self):
        chosen = [sd(2, DS5, "g", "DualSense Wireless Controller")]
        switch_bind._calibrate_pcsx2(chosen)      # pending=SIG-A
        switch_bind._calibrate_pcsx2(chosen)      # files SIG-A into the cache
        switch_bind._PCSX2_EMULOG.unlink()        # now only the cache can answer
        pads, _ = switch_bind._calibrate_pcsx2(chosen)
        self.assertEqual(pads[0].index, 6)        # served from the cached mapping

    def test_foreign_emulog_does_not_poison_cache(self):
        chosen = [sd(2, DS5, "g", "DualSense Wireless Controller"),
                  sd(0, DS4, "g", "PS4 Controller")]
        switch_bind._calibrate_pcsx2(chosen)      # pending=SIG-A
        switch_bind._calibrate_pcsx2(chosen)      # files SIG-A -> good mapping
        # a PCSX2 run started OUTSIDE the wrapper (Desktop/EmuDeck) with only the Deck overwrites
        # the emulog; the next wrapper bind must NOT mis-file it under SIG-A.
        switch_bind._PCSX2_EMULOG.write_text(
            "[ 2.0] SDLInputSource: Opened gamepad 1 (instance id 1, player id 0): Steam Deck Controller\n",
            encoding="utf-8")
        pads, _ = switch_bind._calibrate_pcsx2(chosen)
        self.assertEqual([d.index for d in pads], [6, 4])   # cached mapping preserved, not poisoned


class Pcsx2Blacklist(unittest.TestCase):
    """Device-visibility default + per-device overrides (smart default hides only the
    non-gamepad guns/Wii-Nav; every real gamepad stays visible)."""

    def test_default_hides_only_nongamepads(self):
        from lib.madsrv import pcsx2_blacklist_cmds as bl
        self.assertTrue(bl.is_hidden("pcsx2", "16c0:0f38", stored=[]))   # Sinden gun
        self.assertTrue(bl.is_hidden("pcsx2", "4d41:0001", stored=[]))   # Wii Nav
        self.assertFalse(bl.is_hidden("pcsx2", "054c:0ce6", stored=[]))  # DualSense
        self.assertFalse(bl.is_hidden("pcsx2", "045e:02a1", stored=[]))  # X-Arcade / Xbox

    def test_overrides(self):
        from lib.madsrv import pcsx2_blacklist_cmds as bl
        self.assertFalse(bl.is_hidden("pcsx2", "16c0:0f38", stored=["~16c0:0f38"]))  # force-show a gun
        self.assertTrue(bl.is_hidden("pcsx2", "045e:02a1", stored=["045e:02a1"]))    # force-hide a pad


class Pcsx2PergameResolveOrder(unittest.TestCase):
    """A per-game pad order (Phase 2 v2) overrides the global order at resolve time, BEFORE the
    managed_players truncation — so it can promote a normally-excluded pad into Player 1. The real
    pads_cmds._ordered is exercised (only _real_pads/_supported + the policy are faked)."""

    XBOX = "045e:02a1"

    def _resolve(self, pads, order):
        import lib.policy as pol
        saved_lm = pol.load_merged
        saved = {n: getattr(pads_cmds, n) for n in ("_real_pads", "_supported")}
        pol.load_merged = lambda: {"backends": {"pcsx2": {"manage_pads": 2}}}
        pads_cmds._real_pads = lambda pump=True: list(pads)
        pads_cmds._supported = lambda emu, ps: list(ps)
        try:
            return [d.vidpid for d in switch_bind._resolve_pads("pcsx2", order=order)]
        finally:
            pol.load_merged = saved_lm
            for n, fn in saved.items():
                setattr(pads_cmds, n, fn)

    def _three(self):
        # SDL-index order = DualSense, DualShock 4, Xbox (Xbox is the natural 3rd).
        return [sd(0, DS5, "g", "DualSense"), sd(1, DS4, "g", "DualShock 4"),
                sd(2, self.XBOX, "g", "Xbox 360")]

    def test_order_promotes_pad_into_top_two(self):
        got = self._resolve(self._three(), order=[self.XBOX, DS5, DS4])
        self.assertEqual(got, [self.XBOX, DS5])   # Xbox promoted to Player 1, then truncated to 2

    def test_no_order_still_truncates_to_managed(self):
        self.assertEqual(len(self._resolve(self._three(), order=None)), 2)

    def test_corrupt_order_falls_back_to_global_no_crash(self):
        # Regression (adversarial review): a hand-corrupted non-list 'pads' value must NOT crash the
        # bind (which would swallow the error and bind NO pads) nor, as a string, bypass the global
        # order — the isinstance guard in _ordered makes it fall back to the global priority.
        for bad in (5, True, "054c:0ce6", {"x": 1}):
            got = self._resolve(self._three(), order=bad)
            self.assertEqual(len(got), 2)      # no raise; still capped, global order used

    def test_order_preserved_through_calibration(self):
        # calibration remaps each pad's SDL index by NAME but must keep the reordered list ORDER.
        reordered = [sd(2, self.XBOX, "g", "Xbox 360"), sd(0, DS5, "g", "DualSense")]
        cal, _ = switch_bind._calibrate_pcsx2(reordered)   # no emulog -> raw indices, order intact
        self.assertEqual([d.vidpid for d in cal], [self.XBOX, DS5])


if __name__ == "__main__":
    unittest.main()
