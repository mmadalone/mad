"""Tests for the RetroArch hub "Per-game" backend: ragame.* (picker),
ragameset.* (per-game settings, buffered EmuSettings ns), ragamein.* (per-game
input remap, buffered EmuSettings ns of enum selectors over the native .rmp).

Run:  python3 -m unittest tests.test_retroarch_game_cmds -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import es_gamelist, es_systems, esde_settings
from lib import retroarch_cfg as rcfg
from lib import retroarch_rmp as rmp
from lib.madsrv import retroarch_game_cmds as rg
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import rpc

SYS = "testsys"


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("ragame.systems", "ragame.games",
                  "ragameset.get", "ragameset.set", "ragameset.save", "ragameset.cancel",
                  "ragamein.get", "ragamein.set", "ragamein.save", "ragamein.cancel"):
            self.assertIn(m, rpc._METHODS, m)


class AnalogDpadLabels(unittest.TestCase):
    """Locks _ANALOG_DPAD_LABELS to RetroArch's ANALOG_DPAD_* enum (adversarial
    review fix, applied inline): the list INDEX must equal the documented
    input_defines.h integer, since that index is what ragamein writes verbatim
    to input_playerN_analog_dpad_mode (see _pgin_write_item)."""

    def test_labels_match_the_documented_enum_index(self):
        self.assertEqual(rg._ANALOG_DPAD_LABELS[2], "Right Analog")
        self.assertEqual(rg._ANALOG_DPAD_LABELS[3], "Left Analog (Forced)")
        self.assertEqual(rg._ANALOG_DPAD_LABELS[6], "Twin Stick")
        self.assertEqual(rg._ANALOG_DPAD_LABELS[7], "Left+Right Analog (Forced)")
        self.assertEqual(len(rg._ANALOG_DPAD_LABELS), 9)   # NONE..TWINSTICK_FORCED


class SplitTitleId(unittest.TestCase):
    def test_splits_on_first_colon_only(self):
        self.assertEqual(rg._split_titleid("nes:Duck Hunt (World)"), ("nes", "Duck Hunt (World)"))

    def test_splits_on_first_colon_when_stem_itself_has_a_colon(self):
        self.assertEqual(rg._split_titleid("daphne:Dragon's Lair: Escape"),
                         ("daphne", "Dragon's Lair: Escape"))

    def test_rejects_missing_colon(self):
        with self.assertRaises(rpc.RpcError):
            rg._split_titleid("nes")

    def test_rejects_empty_system_or_stem(self):
        with self.assertRaises(rpc.RpcError):
            rg._split_titleid(":stem")
        with self.assertRaises(rpc.RpcError):
            rg._split_titleid("sys:")


class _RaCoreDirBase(unittest.TestCase):
    """Common temp-core-dir fixture shared by the ragame.games / ragameset /
    ragamein tests — same monkeypatch style as test_retroarch_pergame_cfg.py
    and test_retroarch_rmp.py (rcfg.RA_CONFIG_BASE / SYSTEM_CORE_MAP)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ragame-test-"))
        self.core = self.tmp / "FakeCore"
        self.core.mkdir()
        self._saved_cfg = (rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP)
        rcfg.RA_CONFIG_BASE = self.tmp
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore"]}
        self._orig_running = rcfg.proc_guard.retroarch_running
        rcfg.proc_guard.retroarch_running = lambda: False

    def tearDown(self):
        rcfg.RA_CONFIG_BASE, rcfg.SYSTEM_CORE_MAP = self._saved_cfg
        rcfg.proc_guard.retroarch_running = self._orig_running
        shutil.rmtree(self.tmp, ignore_errors=True)


class RagameGames(_RaCoreDirBase):
    def setUp(self):
        super().setUp()
        self._saved_gamelists = es_systems.GAMELISTS
        es_systems.GAMELISTS = self.tmp / "gamelists"
        es_gamelist.records.cache_clear()
        # Hermetic by default (no real controller-policy.toml lookups); the one
        # test that cares about a controller override patches its own merged.
        self._merged_patcher = mock.patch.object(rg, "load_merged", return_value={})
        self._merged_patcher.start()

    def tearDown(self):
        self._merged_patcher.stop()
        es_systems.GAMELISTS = self._saved_gamelists
        es_gamelist.records.cache_clear()
        super().tearDown()

    def _write_gamelist(self, games: list[tuple[str, str]]) -> None:
        d = es_systems.GAMELISTS / SYS
        d.mkdir(parents=True, exist_ok=True)
        body = "".join(f"<game><path>./{stem}.zip</path><name>{name}</name></game>\n"
                       for stem, name in games)
        (d / "gamelist.xml").write_text(f"<gameList>\n{body}</gameList>\n", encoding="utf-8")

    def test_hidden_games_follow_esde_show_hidden_setting(self):
        # records() flags <hidden>true</hidden>; visible_records / ragame.games
        # then hide it ONLY when ES-DE's ShowHiddenGames is off (its default),
        # mirroring exactly what ES-DE itself shows.
        d = es_systems.GAMELISTS / SYS
        d.mkdir(parents=True, exist_ok=True)
        (d / "gamelist.xml").write_text(
            "<gameList>\n"
            "<game><path>./Shown.zip</path><name>Shown Game</name></game>\n"
            "<game><path>./Secret.zip</path><name>Secret Game</name><hidden>true</hidden></game>\n"
            "</gameList>\n", encoding="utf-8")
        self.assertTrue(es_gamelist.records(SYS)["secret"]["hidden"])
        with mock.patch.object(esde_settings, "show_hidden_games", return_value=False):
            self.assertEqual(sorted(es_gamelist.visible_records(SYS)), ["shown"])
            self.assertEqual([g["stem"] for g in rg._ragame_games(SYS)["games"]], ["Shown"])
        with mock.patch.object(esde_settings, "show_hidden_games", return_value=True):
            self.assertEqual(sorted(es_gamelist.visible_records(SYS)), ["secret", "shown"])
            self.assertEqual(sorted(g["stem"] for g in rg._ragame_games(SYS)["games"]),
                             ["Secret", "Shown"])

    def test_response_carries_the_on_disk_core_list(self):
        # Phase 5b: the per-core picker reads its core list from ragame.games.
        self._write_gamelist([("Plain Game", "Plain Game")])
        self.assertEqual(rg._ragame_games(SYS)["cores"], ["FakeCore"])

    def test_games_with_no_overrides_report_default_and_empty_summary(self):
        self._write_gamelist([("Plain Game", "Plain Game")])
        r = rg._ragame_games(SYS)
        self.assertEqual(len(r["games"]), 1)
        g = r["games"][0]
        self.assertEqual(g["stem"], "Plain Game")
        self.assertFalse(g["overrides"])
        self.assertEqual(g["summary"], "")

    def test_stem_case_is_preserved_not_lowercased(self):
        self._write_gamelist([("MixedCase Game", "Mixed Case Game")])
        r = rg._ragame_games(SYS)
        self.assertEqual(r["games"][0]["stem"], "MixedCase Game")

    def test_settings_override_flags_the_game_and_builds_summary(self):
        self._write_gamelist([("Overridden Game", "Overridden Game")])
        rcfg.set_game_option(SYS, "Overridden Game", "video_smooth", "true")
        r = rg._ragame_games(SYS)
        g = r["games"][0]
        self.assertTrue(g["overrides"])
        self.assertIn("Settings", g["summary"])
        self.assertIn("1 set", g["summary"])
        self.assertIn("Input remap   default", g["summary"])
        self.assertIn("Controllers   default (global)", g["summary"])

    def test_input_remap_flags_the_game_and_builds_summary(self):
        self._write_gamelist([("Remapped Game", "Remapped Game")])
        rmp.set_game_remap(SYS, "Remapped Game", {"input_player1_btn_a": "0"})
        r = rg._ragame_games(SYS)
        g = r["games"][0]
        self.assertTrue(g["overrides"])
        self.assertIn("Settings      default", g["summary"])
        self.assertIn("Input remap", g["summary"])
        self.assertIn("A=B", g["summary"])       # btn_a (source A) remapped to id 0 (B)

    def test_controller_override_flags_the_game_via_policy_games_table(self):
        self._write_gamelist([("Controlled Game", "Controlled Game")])
        merged = {"games": {f"{SYS}:Controlled Game": {"ports": [["X-Arcade"]]}}}
        with mock.patch.object(rg, "load_merged", return_value=merged):
            r = rg._ragame_games(SYS)
        g = r["games"][0]
        self.assertTrue(g["overrides"])
        self.assertIn("Controllers   P1: X-Arcade", g["summary"])

    # ── Phase 5a: ragame.games rows carry the launched core ──
    def test_games_report_the_launched_core_field(self):
        self._write_gamelist([("Plain Game", "Plain Game")])
        # a plain game (no <altemulator>) reports its SYSTEM default core, which
        # _ragame_games resolves once via retroarch_cfg.default_core (perf).
        with mock.patch.object(rcfg, "default_core", return_value="FakeCore"):
            r = rg._ragame_games(SYS)
        self.assertEqual(r["games"][0]["core"], "FakeCore")

    def test_games_report_empty_core_when_unresolvable(self):
        self._write_gamelist([("Plain Game", "Plain Game")])
        with mock.patch.object(rcfg, "default_core", return_value=None):
            r = rg._ragame_games(SYS)
        self.assertEqual(r["games"][0]["core"], "")

    def test_no_io_for_games_without_any_cfg_or_rmp_file(self):
        # Two plain games + one with a real override — only the override's cfg/rmp
        # actually exist on disk, proving the scan is glob-then-check, not
        # per-gamelist-entry stat-ing (has_game_overrides would KeyError/loop
        # forever if it were called for a game with no core dirs at all — it
        # isn't, so this just asserts the summary math is right for a mix).
        self._write_gamelist([("A", "A"), ("B", "B"), ("C Override", "C Override")])
        rcfg.set_game_option(SYS, "C Override", "audio_mute_enable", "true")
        r = rg._ragame_games(SYS)
        by_stem = {g["stem"]: g for g in r["games"]}
        self.assertFalse(by_stem["A"]["overrides"])
        self.assertFalse(by_stem["B"]["overrides"])
        self.assertTrue(by_stem["C Override"]["overrides"])


class RagameSystems(unittest.TestCase):
    def test_uses_present_ra_systems_and_gamelist_count(self):
        fake_systems = ["nes", "snes"]
        with mock.patch.object(rg, "present_ra_systems", return_value=fake_systems), \
             mock.patch.object(es_gamelist, "records",
                               side_effect=lambda s: {"a": {}, "b": {}} if s == "nes" else {}), \
             mock.patch.object(rg, "console_art", return_value=None):
            r = rg._ragame_systems()
        self.assertEqual([s["name"] for s in r["systems"]], ["nes", "snes"])
        self.assertEqual(r["systems"][0]["count"], 2)
        self.assertEqual(r["systems"][1]["count"], 0)


class RagameSet(_RaCoreDirBase):
    def setUp(self):
        super().setUp()
        rg._rs_buf.update({"titleid": None, "data": None, "disk": None,
                           "dirty": False, "edits": [], "base": {}})
        self.tid = f"{SYS}:My Game"

    def test_get_always_reports_exists_true_for_a_fresh_game(self):
        r = rg._ragameset_get({"titleid": self.tid})
        self.assertTrue(r["exists"])
        self.assertTrue(r["buffered"])
        self.assertFalse(r["dirty"])
        self.assertTrue(r["groups"])

    def test_bool_row_defaults_to_inherit_global(self):
        r = rg._ragameset_get({"titleid": self.tid})
        row = next(s for g in r["groups"] for s in g["settings"] if s["key"] == "video_vsync")
        self.assertEqual(row["options"][0], "Inherit global")
        self.assertEqual(row["value"], 0)

    def test_set_then_save_writes_via_retroarch_cfg_and_clears_on_inherit(self):
        rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})   # "On"
        self.assertTrue(rg._rs_buf["dirty"])
        rg._ragameset_save({"titleid": self.tid})
        self.assertEqual(rcfg.get_game_options(SYS, "My Game"), {"video_vsync": "true"})

        # Re-fetch (fresh, buffer not dirty) reflects the saved override.
        r = rg._ragameset_get({"titleid": self.tid})
        row = next(s for g in r["groups"] for s in g["settings"] if s["key"] == "video_vsync")
        self.assertEqual(row["value"], 2)

        # "Inherit global" (index 0) clears the key.
        rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 0})
        rg._ragameset_save({"titleid": self.tid})
        self.assertEqual(rcfg.get_game_options(SYS, "My Game"), {})

    def test_set_returns_precise_dirty(self):
        # The .set reply carries the backend's real dirty (buffer != disk), so the
        # C++ save prompt hides again when a value is reverted to its saved state.
        on = rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})
        self.assertTrue(on["dirty"])
        back = rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 0})
        self.assertFalse(back["dirty"])   # cleared back to the (empty) disk state

    def test_save_flips_auto_overrides_enable_on(self):
        global_cfg = self.tmp / "retroarch.cfg"
        self._saved_global = rcfg.RA_GLOBAL_CFG
        rcfg.RA_GLOBAL_CFG = global_cfg
        try:
            self.assertIsNone(rcfg.get_global_option("auto_overrides_enable"))
            rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})
            rg._ragameset_save({"titleid": self.tid})
            self.assertEqual(rcfg.get_global_option("auto_overrides_enable"), "true")
        finally:
            rcfg.RA_GLOBAL_CFG = self._saved_global

    def test_running_guard_blocks_set_and_save(self):
        rcfg.proc_guard.retroarch_running = lambda: True
        with self.assertRaises(rpc.RpcError):
            rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})
        with self.assertRaises(rpc.RpcError):
            rg._ragameset_save({"titleid": self.tid})

    def test_cancel_discards_staged_edits(self):
        rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})
        self.assertTrue(rg._rs_buf["dirty"])
        rg._ragameset_cancel({"titleid": self.tid})
        self.assertFalse(rg._rs_buf["dirty"])
        self.assertEqual(rcfg.get_game_options(SYS, "My Game"), {})

    # ── Item A: honest display over a standalone/bezel value, layer-on-top ──
    def test_standalone_aspect_with_no_pg_block_shows_the_honest_value_not_inherit(self):
        # The bezel pipeline already wrote a standalone aspect_ratio_index line
        # (no MAD PG_* block yet, exactly the ~18,764-game real-world shape) --
        # ragameset must show the TRUE effective value RA is applying, not a
        # misleading "Inherit global".
        cfg = self.core / "My Game.cfg"
        cfg.write_text(
            "# bezelproject — auto-generated\n"
            'input_overlay = "/path/to/overlay.cfg"\n'
            'aspect_ratio_index = "22"\n', encoding="utf-8")

        r = rg._ragameset_get({"titleid": self.tid})
        row = next(s for g in r["groups"] for s in g["settings"]
                  if s["key"] == "aspect_ratio_index")
        expected_label = rs._ASPECT[22]
        self.assertNotEqual(row["options"][0], "Inherit global")   # honest inherit-slot label
        self.assertNotEqual(row["value"], 0)
        self.assertEqual(row["options"][row["value"]], expected_label)

        # Picking "Inherit" (index 0, MAD's own slot) clears ONLY the PG key --
        # the standalone/bezel line is never touched, never "reverts to global".
        rg._ragameset_set({"titleid": self.tid, "key": "aspect_ratio_index", "value": 0})
        rg._ragameset_save({"titleid": self.tid})
        self.assertEqual(rcfg.get_game_options(SYS, "My Game"), {})   # no PG override written
        txt = cfg.read_text(encoding="utf-8")
        self.assertIn('aspect_ratio_index = "22"', txt)               # standalone line remains
        self.assertEqual(rcfg.base_game_options(SYS, "My Game").get("aspect_ratio_index"), "22")

        # ...and re-fetching goes right back to honestly showing that base value.
        r2 = rg._ragameset_get({"titleid": self.tid})
        row2 = next(s for g in r2["groups"] for s in g["settings"]
                   if s["key"] == "aspect_ratio_index")
        self.assertEqual(row2["options"][row2["value"]], expected_label)


class RagameIn(_RaCoreDirBase):
    def setUp(self):
        super().setUp()
        rg._in_buf.update({"titleid": None, "data": None, "disk": None, "dirty": False,
                           "edits": []})
        self.tid = f"{SYS}:My Game"

    def test_get_always_reports_exists_true_for_a_fresh_game(self):
        r = rg._ragamein_get({"titleid": self.tid})
        self.assertTrue(r["exists"])
        self.assertTrue(r["buffered"])
        titles = [g["title"] for g in r["groups"]]
        self.assertEqual(titles, ["Buttons (Player 1)", "Buttons (Player 2)", "Device", "Port"])

    def test_button_row_defaults_to_inherit_global(self):
        r = rg._ragamein_get({"titleid": self.tid})
        row = next(s for g in r["groups"] for s in g["settings"]
                  if s["key"] == "input_player1_btn_a")
        self.assertEqual(row["options"][0], "Inherit global")
        self.assertEqual(row["options"][1:], list(rmp.BUTTON_LABELS))
        self.assertEqual(row["value"], 0)

    def test_set_button_then_save_writes_rmp_and_inherit_clears(self):
        # value = 1 (index 0 is "Inherit global") + target id 0 ("B") -> option index 1
        rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 1})
        self.assertTrue(rg._in_buf["dirty"])
        rg._ragamein_save({"titleid": self.tid})
        self.assertEqual(rmp.get_game_remap(SYS, "My Game"), {"input_player1_btn_a": "0"})

        rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 0})
        rg._ragamein_save({"titleid": self.tid})
        self.assertEqual(rmp.get_game_remap(SYS, "My Game"), {})

    def test_device_row_offers_the_five_documented_device_types(self):
        r = rg._ragamein_get({"titleid": self.tid})
        row = next(s for g in r["groups"] for s in g["settings"]
                  if s["key"] == "input_libretro_device_p1")
        self.assertEqual(row["options"], ["Inherit global", "RetroPad", "Analog",
                                          "Light gun", "Mouse", "None"])

    def test_set_returns_precise_dirty(self):
        # The .set reply carries the backend's real dirty (buffer != disk), so the
        # C++ save prompt hides again when a value is reverted to its saved state.
        on = rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 1})
        self.assertTrue(on["dirty"])
        back = rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 0})
        self.assertFalse(back["dirty"])   # cleared back to the (empty) disk state

    def test_set_device_then_save_stores_the_raw_libretro_device_id(self):
        # "Light gun" is option index 3 -> RETRO_DEVICE_LIGHTGUN (4)
        rg._ragamein_set({"titleid": self.tid, "key": "input_libretro_device_p1", "value": 3})
        rg._ragamein_save({"titleid": self.tid})
        self.assertEqual(rmp.get_game_remap(SYS, "My Game"),
                         {"input_libretro_device_p1": "4"})

    # ── Item B: merge staged edits onto the CURRENT on-disk .rmp, not the
    # stale buffer -- a foreign key changed by RetroArch's own Quick Menu
    # while MAD's page was open must survive a MAD save of a DIFFERENT key.
    def test_save_merges_onto_fresh_disk_state_not_a_stale_buffer(self):
        # Seed a .rmp as if RetroArch's own Quick Menu had already saved one.
        rmp.set_game_remap(SYS, "My Game", {"input_player1_btn_a": "5"})
        # MAD opens the page -- buffer snapshots that current disk state.
        rg._ragamein_get({"titleid": self.tid})
        # While MAD's page sits open, the user changes something in
        # RetroArch's own Quick Menu -- a key MAD's buffer never staged an
        # edit for.
        rmp.set_game_remap(SYS, "My Game", {"input_player1_btn_a": "5",
                                            "input_player1_btn_b": "7"})
        # MAD edits a DIFFERENT key and saves.
        rg._ragamein_set({"titleid": self.tid, "key": "input_libretro_device_p1", "value": 3})
        rg._ragamein_save({"titleid": self.tid})

        on_disk = rmp.get_game_remap(SYS, "My Game")
        self.assertEqual(on_disk.get("input_player1_btn_b"), "7")        # foreign edit survived
        self.assertEqual(on_disk.get("input_player1_btn_a"), "5")        # untouched key survived
        self.assertEqual(on_disk.get("input_libretro_device_p1"), "4")   # MAD's own edit applied

    def test_save_flips_auto_remaps_and_input_remap_binds_enable_on(self):
        global_cfg = self.tmp / "retroarch.cfg"
        self._saved_global = rcfg.RA_GLOBAL_CFG
        rcfg.RA_GLOBAL_CFG = global_cfg
        try:
            rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 1})
            rg._ragamein_save({"titleid": self.tid})
            self.assertEqual(rcfg.get_global_option("auto_remaps_enable"), "true")
            self.assertEqual(rcfg.get_global_option("input_remap_binds_enable"), "true")
        finally:
            rcfg.RA_GLOBAL_CFG = self._saved_global

    def test_running_guard_blocks_set_and_save(self):
        rcfg.proc_guard.retroarch_running = lambda: True
        with self.assertRaises(rpc.RpcError):
            rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 1})
        with self.assertRaises(rpc.RpcError):
            rg._ragamein_save({"titleid": self.tid})


class RagameSetPerCore(_RaCoreDirBase):
    """Phase 5b per-core picker: the optional `core` param drives per-core
    read+write for ragameset; absent -> all-cores multi-write (unchanged)."""

    def setUp(self):
        super().setUp()
        (self.tmp / "CoreB").mkdir()
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore", "CoreB"]}
        rg._rs_buf.update({"titleid": None, "core": None, "data": None, "disk": None,
                           "dirty": False, "edits": [], "base": {}})
        self.tid = f"{SYS}:My Game"

    def _cfg(self, core):
        return self.tmp / core / "My Game.cfg"

    def test_save_without_core_multi_writes_every_core(self):
        rg._ragameset_set({"titleid": self.tid, "key": "video_vsync", "value": 2})
        rg._ragameset_save({"titleid": self.tid})
        self.assertTrue(self._cfg("FakeCore").exists())
        self.assertTrue(self._cfg("CoreB").exists())

    def test_save_with_core_writes_only_that_core(self):
        rg._ragameset_set({"titleid": self.tid, "core": "CoreB",
                           "key": "video_vsync", "value": 2})
        rg._ragameset_save({"titleid": self.tid, "core": "CoreB"})
        self.assertTrue(self._cfg("CoreB").exists())
        self.assertFalse(self._cfg("FakeCore").exists())

    def test_get_reads_the_picked_core(self):
        # Distinct overrides in each core (both carry a PG block), so the read
        # genuinely isolates the PICKED core -- get_game_options otherwise returns
        # the first core that has ANY override, masking a broken prefer_core.
        rcfg.set_game_option(SYS, "My Game", "video_vsync", "true", only_core="CoreB")
        rcfg.set_game_option(SYS, "My Game", "video_vsync", "false", only_core="FakeCore")

        def vsync(core):
            r = rg._ragameset_get({"titleid": self.tid, "core": core})
            return next(s for g in r["groups"] for s in g["settings"]
                        if s["key"] == "video_vsync")["value"]
        self.assertEqual(vsync("CoreB"), 2)         # "On" -> the confirmed true index
        self.assertNotEqual(vsync("FakeCore"), 2)   # reads FakeCore's "false", not CoreB

    def test_get_on_an_empty_picked_core_shows_empty_not_a_sibling(self):
        # Regression (adversarial review): picking a core with no override of its
        # own must show Inherit/empty, NOT fall through to a sibling core's block.
        rcfg.set_game_option(SYS, "My Game", "video_vsync", "true", only_core="FakeCore")
        r = rg._ragameset_get({"titleid": self.tid, "core": "CoreB"})   # CoreB is empty
        row = next(s for g in r["groups"] for s in g["settings"] if s["key"] == "video_vsync")
        self.assertEqual(row["value"], 0)   # Inherit (empty), not FakeCore's "On" (2)


class RagameInPerCore(_RaCoreDirBase):
    """Phase 5b per-core picker: `core` drives per-core read+write for ragamein."""

    def setUp(self):
        super().setUp()
        (self.tmp / "CoreB").mkdir()
        rcfg.SYSTEM_CORE_MAP = {SYS: ["FakeCore", "CoreB"]}
        rg._in_buf.update({"titleid": None, "core": None, "data": None, "disk": None,
                           "dirty": False, "edits": []})
        self.tid = f"{SYS}:My Game"

    def _rmp(self, core):
        return self.tmp / "remaps" / core / "My Game.rmp"

    def test_save_without_core_multi_writes_every_core(self):
        rg._ragamein_set({"titleid": self.tid, "key": "input_player1_btn_a", "value": 1})
        rg._ragamein_save({"titleid": self.tid})
        self.assertTrue(self._rmp("FakeCore").exists())
        self.assertTrue(self._rmp("CoreB").exists())

    def test_save_with_core_writes_only_that_core(self):
        rg._ragamein_set({"titleid": self.tid, "core": "CoreB",
                          "key": "input_player1_btn_a", "value": 1})
        rg._ragamein_save({"titleid": self.tid, "core": "CoreB"})
        self.assertTrue(self._rmp("CoreB").exists())
        self.assertFalse(self._rmp("FakeCore").exists())

    def test_get_reloads_when_the_picked_core_changes(self):
        rg._ragamein_get({"titleid": self.tid, "core": "CoreB"})
        self.assertEqual(rg._in_buf["core"], "CoreB")
        rg._ragamein_get({"titleid": self.tid, "core": "FakeCore"})
        self.assertEqual(rg._in_buf["core"], "FakeCore")

    def test_per_core_save_on_empty_core_does_not_clone_a_sibling(self):
        # Regression (adversarial review): picking an EMPTY core, changing ONE
        # key, and saving must write ONLY that key -- NOT clone a sibling core's
        # whole .rmp (the pre-fix fall-through read did exactly that, since .rmp
        # is a whole-file write, not a per-key delta).
        rg._ragamein_set({"titleid": self.tid, "core": "FakeCore",
                          "key": "input_libretro_device_p1", "value": 3})
        rg._ragamein_save({"titleid": self.tid, "core": "FakeCore"})
        rg._ragamein_set({"titleid": self.tid, "core": "CoreB",
                          "key": "input_player1_btn_a", "value": 1})
        rg._ragamein_save({"titleid": self.tid, "core": "CoreB"})
        self.assertEqual(set(rmp.get_game_remap(SYS, "My Game", only_core="CoreB")),
                         {"input_player1_btn_a"})               # NOT cloned
        self.assertEqual(rmp.get_game_remap(SYS, "My Game", only_core="FakeCore"),
                         {"input_libretro_device_p1": "4"})     # sibling untouched


if __name__ == "__main__":
    unittest.main()
