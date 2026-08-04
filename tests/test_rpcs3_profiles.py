"""
Tests for the RPCS3 docked/handheld input-profile core: lib/rpcs3_profiles (stem
resolution chain + the resting-target ladder) + rpcs3_cfg.apply_profile_players
(transient Player-block injection that composes with assign_devices' Device
re-seating and the PS-button chord) + the launch rail's multi-target restore
sweep. Pure given (store entry, backend cfg, yml files): no hardware, temp files
only. Mirrors tests/test_pcsx2_profiles.py.

Run:  python3 -m unittest tests.test_rpcs3_profiles -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from lib import policy, rpcs3_cfg, rpcs3_profiles, switch_bind
from lib.madsrv import pads_cmds, rpcs3_games
from lib.madsrv import rpcs3_pergame_input_cmds as pgin
from tests._fakes import patch_sdl, sd

DS5 = "054c:0ce6"
DS4 = "054c:09cc"
_S = "BLES00590"


def _sdl_block(**cfg):
    return {"Handler": "SDL", "Device": "Old Pad 1",
            "Config": {"Cross": "South", "Circle": "East", **cfg}, "Buddy Device": "Null"}


def _native_block():
    return {"Handler": "DualSense", "Device": "DualSense Pad #1",
            "Config": {}, "Buddy Device": "Null"}


def _write_yml(p: Path, doc: dict) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


class ResolutionChain(unittest.TestCase):
    CFG = {"profile_docked": "DS4pad", "profile_handheld": "Steamdeck"}

    def test_pergame_beats_global(self):
        entry = {"profiles": {"docked": "Custom"}}
        self.assertEqual(rpcs3_profiles.resolve(entry, self.CFG, "docked"), "Custom")

    def test_unset_pergame_falls_to_global(self):
        self.assertEqual(rpcs3_profiles.resolve({}, self.CFG, "docked"), "DS4pad")
        self.assertEqual(rpcs3_profiles.resolve(None, self.CFG, "handheld"), "Steamdeck")

    def test_handheld_never_inherits_docked(self):
        # docked set at BOTH layers, handheld unset everywhere -> None, not the docked pick.
        cfg = {"profile_docked": "DS4pad"}
        entry = {"profiles": {"docked": "Custom"}}
        self.assertIsNone(rpcs3_profiles.resolve(entry, cfg, "handheld"))

    def test_husk_tolerance(self):
        self.assertIsNone(rpcs3_profiles.resolve({"profiles": "junk"}, {}, "docked"))
        self.assertIsNone(rpcs3_profiles.resolve({"profiles": {"docked": 7}}, {}, "docked"))
        self.assertIsNone(rpcs3_profiles.resolve({}, {"profile_docked": "  "}, "docked"))
        self.assertIsNone(rpcs3_profiles.resolve({}, "junk", "docked"))

    def test_pathy_stems_rejected(self):
        for bad in ("../etc/passwd", "a/b", "a\\b", ".hidden", ""):
            self.assertFalse(rpcs3_profiles.valid_stem(bad), bad)
            self.assertIsNone(rpcs3_profiles.resolve({}, {"profile_docked": bad}, "docked"))
        self.assertTrue(rpcs3_profiles.valid_stem("Steamdeck"))
        self.assertTrue(rpcs3_profiles.valid_stem("Race Wheel 2"))

    def test_dirs_derived_from_config_file(self):
        cfg = {"config_file": "/tmp/rpcs3/input_configs/global/Default.yml"}
        self.assertEqual(rpcs3_profiles.profiles_dir(cfg),
                         Path("/tmp/rpcs3/input_configs/global"))
        self.assertEqual(rpcs3_profiles.input_configs_dir(cfg),
                         Path("/tmp/rpcs3/input_configs"))
        default = rpcs3_profiles.profiles_dir({})
        self.assertTrue(str(default).endswith(".config/rpcs3/input_configs/global"))

    def test_profile_path_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_yml(d / "Steamdeck.yml", {"Player 1 Input": _sdl_block()})
            self.assertIsNotNone(rpcs3_profiles.profile_path(d, "Steamdeck"))
            self.assertIsNone(rpcs3_profiles.profile_path(d, "Gone"))
            self.assertIsNone(rpcs3_profiles.profile_path(d, "../Steamdeck"))

    def test_list_stems_excludes_dotfiles_and_non_yml(self):
        # LOAD-BEARING: pathlib's glob DOES match dotfiles, so valid_stem is what keeps
        # the .mad-input-overrides.yml sidecar (and any dotfile) out of the picker.
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_yml(d / "Default.yml", {})
            _write_yml(d / "Steamdeck.yml", {})
            _write_yml(d / ".mad-input-overrides.yml", {"docked": {1: {"L2": "LT"}}})
            (d / "Default.yml.bak").write_text("x", encoding="utf-8")
            (d / "Default.yml.router-backup").write_text("x", encoding="utf-8")
            self.assertEqual(rpcs3_profiles.list_stems(d), ["Default", "Steamdeck"])


class TargetLadder(unittest.TestCase):
    """resting_target mirrors RPCS3's own pad_thread::Init ladder: per-title custom ->
    Active Configurations pointer (per-title entry, then global) -> global Default.yml."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.default = _write_yml(self.root / "global/Default.yml", {})
        self.steamdeck = _write_yml(self.root / "global/Steamdeck.yml", {})
        self.cfg = {"config_file": str(self.default)}

    def _pointer(self, mapping):
        (self.root / "active_input_configurations.yml").write_text(
            yaml.safe_dump({"Active Configurations": mapping}), encoding="utf-8")

    def test_per_title_custom_config_wins(self):
        per = _write_yml(self.root / _S / "Default.yml", {})
        self._pointer({"global": "Steamdeck"})
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, _S), per)
        # a different serial has no custom dir -> falls to the active global
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, "BCES00002"), self.steamdeck)

    def test_active_global_pointer_honored(self):
        self._pointer({"global": "Steamdeck"})
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.steamdeck)
        self.assertEqual(rpcs3_profiles.active_global(self.cfg), "Steamdeck")

    def test_per_title_pointer_entry_beats_global_entry(self):
        self._pointer({"global": "Default", _S: "Steamdeck"})
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, _S), self.steamdeck)
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.default)

    def test_pointer_naming_missing_file_falls_to_default(self):
        self._pointer({"global": "Gone"})
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.default)

    def test_missing_or_corrupt_pointer_falls_to_default(self):
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.default)
        (self.root / "active_input_configurations.yml").write_text(
            "{{{not yaml", encoding="utf-8")
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.default)
        self.assertEqual(rpcs3_profiles.active_global(self.cfg), "Default")

    def test_legacy_active_profiles_yml_is_never_read(self):
        # REGRESSION (verified vs RPCS3 master): active_profiles.yml is the pre-2023
        # pointer; master neither reads nor migrates it. A divergent legacy file must not
        # steer the ladder.
        (self.root / "active_profiles.yml").write_text(
            yaml.safe_dump({"Active Profiles": {"global": "Steamdeck"}}), encoding="utf-8")
        self.assertEqual(rpcs3_profiles.resting_target(self.cfg, None), self.default)

    def test_switch_bind_target_falls_back_on_failure(self):
        from lib import policy
        saved = policy.load_merged
        policy.load_merged = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(switch_bind._rpcs3_target("/roms/ps3/x.iso"),
                             switch_bind._RPCS3_YML)
        finally:
            policy.load_merged = saved


class ApplyProfilePlayers(unittest.TestCase):
    def _files(self, profile_doc: dict, target_doc: dict | None = None):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        target = _write_yml(d / "Default.yml", target_doc if target_doc is not None else {
            "Player 1 Input": _native_block(),
            "Miscellaneous": {"Pad handling sleep": 1000}})
        prof = _write_yml(d / "Steamdeck.yml", profile_doc)
        return target, prof

    def test_sdl_blocks_copied_compacted_and_capped(self):
        # P1 SDL + P2 native + P3 SDL + P4 Null: usable = P1, P3 -> compacted to P1, P2.
        target, prof = self._files({
            "Player 1 Input": _sdl_block(Cross="North"),
            "Player 2 Input": _native_block(),
            "Player 3 Input": _sdl_block(Cross="West"),
            "Player 4 Input": {"Handler": "Null", "Device": "Null", "Config": {},
                               "Buddy Device": "Null"},
        })
        applied = rpcs3_cfg.apply_profile_players(target, prof, 2)
        self.assertEqual(applied, [("Player 1 Input", "Player 1 Input"),
                                   ("Player 3 Input", "Player 2 Input")])
        out = yaml.safe_load(target.read_text(encoding="utf-8"))
        self.assertEqual(out["Player 1 Input"]["Config"]["Cross"], "North")
        self.assertEqual(out["Player 2 Input"]["Config"]["Cross"], "West")
        # cap: with 1 seated pad only the first usable block lands
        target2, prof2 = self._files({
            "Player 1 Input": _sdl_block(Cross="North"),
            "Player 3 Input": _sdl_block(Cross="West")})
        applied2 = rpcs3_cfg.apply_profile_players(target2, prof2, 1)
        self.assertEqual(applied2, [("Player 1 Input", "Player 1 Input")])
        self.assertNotIn("Player 2 Input", yaml.safe_load(target2.read_text()))

    def test_no_usable_blocks_is_noop(self):
        target, prof = self._files({
            "Player 1 Input": _native_block(),
            "Player 2 Input": {"Handler": "SDL", "Device": "X 1", "Config": {},
                               "Buddy Device": "Null"}})       # empty Config = unusable
        before = target.read_text(encoding="utf-8")
        self.assertEqual(rpcs3_cfg.apply_profile_players(target, prof, 2), [])
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_missing_profile_or_target_is_noop(self):
        target, prof = self._files({"Player 1 Input": _sdl_block()})
        self.assertEqual(rpcs3_cfg.apply_profile_players(target, prof.parent / "gone.yml", 1), [])
        self.assertEqual(rpcs3_cfg.apply_profile_players(target.parent / "gone2.yml", prof, 1), [])

    def test_corrupt_target_is_noop(self):
        target, prof = self._files({"Player 1 Input": _sdl_block()})
        target.write_text(": {{{not yaml", encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        self.assertEqual(rpcs3_cfg.apply_profile_players(target, prof, 1), [])
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_non_player_keys_untouched(self):
        target, prof = self._files({"Player 1 Input": _sdl_block(Cross="North")})
        rpcs3_cfg.apply_profile_players(target, prof, 1)
        out = yaml.safe_load(target.read_text(encoding="utf-8"))
        self.assertEqual(out["Miscellaneous"], {"Pad handling sleep": 1000})

    def test_first_write_takes_pristine_router_backup(self):
        # REVIEW FIX: on a fresh target the one-time .router-backup must capture the
        # RESTING layout, not the just-injected profile (assign_devices' own backup runs
        # after the injection and must find this one already present).
        target, prof = self._files({"Player 1 Input": _sdl_block(Cross="North")})
        resting_text = target.read_text(encoding="utf-8")
        rpcs3_cfg.apply_profile_players(target, prof, 1)
        backup = target.with_name(target.name + ".router-backup")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), resting_text)   # pristine
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(target), manage=2,
                                     overrides={})
        self.assertEqual(backup.read_text(encoding="utf-8"), resting_text)   # not re-copied

    def test_device_precedes_buddy_device_in_output(self):
        # lib/standalone_preview scrapes the block with an unanchored `Device:` regex —
        # if `Buddy Device:` came first it would scrape "Null". RPCS3-native order pinned.
        target, prof = self._files({"Player 1 Input": _sdl_block(Cross="North")})
        rpcs3_cfg.apply_profile_players(target, prof, 1)
        text = target.read_text(encoding="utf-8")
        block = text[text.index("Player 1 Input"):]
        self.assertLess(block.index("Device:"), block.index("Buddy Device:"))
        self.assertLess(block.index("Handler:"), block.index("Device:"))


class PsButtonFilter(unittest.TestCase):
    """ps_button_overrides: only the PS-button chord survives the override sidecar at
    launch — gameplay keys (superseded by input profiles) are inert but preserved on disk."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ovr = self.d / ".mad-input-overrides.yml"
        self._ovf, rpcs3_cfg._OVERRIDES_FILE = rpcs3_cfg._OVERRIDES_FILE, self.ovr
        self.addCleanup(setattr, rpcs3_cfg, "_OVERRIDES_FILE", self._ovf)

    def test_filters_gameplay_keys_per_context(self):
        self.ovr.write_text(yaml.safe_dump(
            {"docked": {1: {"L2": "LT", "Cross": "East", "PS Button": "Back&Start"},
                        2: {"Cross": "West"}},
             "handheld": {1: {"PS Button": "Guide"}}}), encoding="utf-8")
        self.assertEqual(rpcs3_cfg.ps_button_overrides("docked"),
                         {1: {"PS Button": "Back&Start"}})       # gameplay + P2 filtered out
        self.assertEqual(rpcs3_cfg.ps_button_overrides("handheld"),
                         {1: {"PS Button": "Guide"}})

    def test_legacy_flat_sidecar_reads_docked(self):
        # the REAL on-device shape (pre-context flat, int player keys)
        self.ovr.write_text(yaml.safe_dump(
            {1: {"L2": "LT", "Left Stick Up": "LS Y+", "PS Button": "Back&Start"}}),
            encoding="utf-8")
        self.assertEqual(rpcs3_cfg.ps_button_overrides("docked"),
                         {1: {"PS Button": "Back&Start"}})
        self.assertEqual(rpcs3_cfg.ps_button_overrides("handheld"), {})

    def test_assign_devices_default_merges_only_the_chord(self):
        # REGRESSION: overrides=None used to load the FULL sidecar — a stale gameplay remap
        # (Cross=East) must no longer reach the transient profile; the PS chord still does.
        self.ovr.write_text(yaml.safe_dump(
            {1: {"Cross": "East", "PS Button": "Back&Start"}}), encoding="utf-8")
        yml = _write_yml(self.d / "Default.yml", {"Player 1 Input": _native_block()})
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(yml), manage=2)
        out = yaml.safe_load(yml.read_text(encoding="utf-8"))
        self.assertEqual(out["Player 1 Input"]["Config"]["PS Button"], "Back&Start")
        self.assertEqual(out["Player 1 Input"]["Config"]["Cross"], "South")   # template, not East


class ComposesWithAssignDevices(unittest.TestCase):
    """End-to-end: profile injection -> assign_devices preserves the injected Config,
    re-seats Device per pad and layers the PS chord on top (the compose the feature
    rides on)."""

    def test_profile_layout_survives_with_reseated_device(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        target = _write_yml(d / "Default.yml", {"Player 1 Input": _native_block()})
        prof = _write_yml(d / "Race.yml", {"Player 1 Input": _sdl_block(Cross="North",
                                                                       R2="RS")})
        rpcs3_cfg.apply_profile_players(target, prof, 2)
        players = [sd(0, DS5, "ga", "DualSense"), sd(1, DS4, "gb", "PS4 Controller")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(target), manage=7,
                                     overrides={1: {"PS Button": "Back&Start"}})
        out = yaml.safe_load(target.read_text(encoding="utf-8"))
        p1 = out["Player 1 Input"]
        self.assertEqual(p1["Handler"], "SDL")
        self.assertEqual(p1["Device"], "DualSense 1")            # re-seated
        self.assertEqual(p1["Config"]["Cross"], "North")         # profile layout preserved
        self.assertEqual(p1["Config"]["R2"], "RS")
        self.assertEqual(p1["Config"]["PS Button"], "Back&Start")   # chord layered on top
        # player beyond the profile's usable blocks -> canonical template
        self.assertEqual(out["Player 2 Input"]["Config"]["Cross"], "South")


class SidecarRestoreRoundTrip(unittest.TestCase):
    """snapshot -> inject profile -> assign_devices -> restore reverts the Player blocks
    to their resting values — including on a PER-TITLE target — and the multi-target
    sweep finds a stale sidecar on a target the next launch doesn't use."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self._yml = switch_bind._RPCS3_YML
        self._ic = switch_bind._RPCS3_INPUT_CONFIGS
        switch_bind._RPCS3_YML = self.root / "global/Default.yml"
        switch_bind._RPCS3_INPUT_CONFIGS = self.root
        self.addCleanup(setattr, switch_bind, "_RPCS3_YML", self._yml)
        self.addCleanup(setattr, switch_bind, "_RPCS3_INPUT_CONFIGS", self._ic)
        _write_yml(switch_bind._RPCS3_YML, {"Player 1 Input": _native_block()})
        # _rpcs3_configs consults the policy config_file too — point it at the same
        # scratch tree (test isolation: never enumerate the real ~/.config/rpcs3).
        self._lm = policy.load_merged
        policy.load_merged = lambda: {"backends": {"rpcs3": {
            "config_file": str(switch_bind._RPCS3_YML)}}}
        self.addCleanup(setattr, policy, "load_merged", self._lm)

    def _roundtrip(self, target: Path):
        resting = yaml.safe_load(target.read_text(encoding="utf-8"))
        side = switch_bind._sidecar(target)
        side.write_text(json.dumps(
            {"emu": "rpcs3", "input": switch_bind._snapshot("rpcs3", target)}),
            encoding="utf-8")
        prof = _write_yml(self.root / "global/Race.yml",
                          {"Player 1 Input": _sdl_block(Cross="North")})
        rpcs3_cfg.apply_profile_players(target, prof, 1)
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(target), manage=7)
        self.assertEqual(yaml.safe_load(target.read_text())["Player 1 Input"]["Config"]["Cross"],
                         "North")                                 # injected + preserved
        switch_bind.restore_target(target)
        self.assertEqual(yaml.safe_load(target.read_text(encoding="utf-8")), resting)
        self.assertFalse(side.exists())

    def test_global_target_roundtrip(self):
        self._roundtrip(switch_bind._RPCS3_YML)

    def test_per_title_target_roundtrip(self):
        per = _write_yml(self.root / _S / "Default.yml",
                         {"Player 1 Input": _native_block(), "KEEP": {"x": 1}})
        self._roundtrip(per)
        self.assertEqual(yaml.safe_load(per.read_text())["KEEP"], {"x": 1})

    def test_configs_enumeration_and_stale_sidecar_sweep(self):
        steam = _write_yml(self.root / "global/Steamdeck.yml",
                           {"Player 1 Input": _native_block()})
        per = _write_yml(self.root / _S / "Default.yml", {"Player 1 Input": _native_block()})
        cfgs = list(switch_bind._rpcs3_configs())
        self.assertEqual(cfgs[0], switch_bind._RPCS3_YML)         # Default first
        self.assertIn(steam, cfgs)
        self.assertIn(per, cfgs)
        # Stale sidecar on a target the NEXT launch doesn't use (crash during a Steamdeck-
        # active session, then the user switches back): restore_all must still revert it.
        resting = yaml.safe_load(steam.read_text(encoding="utf-8"))
        switch_bind._sidecar(steam).write_text(json.dumps(
            {"emu": "rpcs3", "input": switch_bind._snapshot("rpcs3", steam)}),
            encoding="utf-8")
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(steam), manage=7)
        self.assertNotEqual(yaml.safe_load(steam.read_text()), resting)   # dirty
        # Drive the exact sweep loop bind()/restore_all run over _rpcs3_configs (calling the
        # real restore_all here would also walk the OTHER emulators' live home-dir configs —
        # not hermetic). _known_configs must surface the same candidates for restore_all.
        self.assertTrue(set(cfgs) <= set(switch_bind._known_configs()))
        for cfg in switch_bind._rpcs3_configs():
            if switch_bind._sidecar(cfg).exists():
                switch_bind.restore_target(cfg)
        self.assertEqual(yaml.safe_load(steam.read_text(encoding="utf-8")), resting)
        self.assertFalse(switch_bind._sidecar(steam).exists())

    def test_policy_config_file_tree_covered_by_sweep(self):
        # REVIEW FIX (major): [backends.rpcs3].config_file may point at a Flatpak/portable
        # tree DIFFERENT from the hardcoded default — the sweep/restore enumeration must
        # cover the policy tree too, or a sidecar written next to the ladder target there
        # is never reverted and the "transient" injection turns permanent.
        ptree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ptree, True)
        pdefault = _write_yml(ptree / "global/Default.yml",
                              {"Player 1 Input": _native_block()})
        policy.load_merged = lambda: {"backends": {"rpcs3": {"config_file": str(pdefault)}}}
        resting = yaml.safe_load(pdefault.read_text(encoding="utf-8"))
        switch_bind._sidecar(pdefault).write_text(json.dumps(
            {"emu": "rpcs3", "input": switch_bind._snapshot("rpcs3", pdefault)}),
            encoding="utf-8")
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(pdefault), manage=7)
        self.assertNotEqual(yaml.safe_load(pdefault.read_text()), resting)   # dirty
        cfgs = list(switch_bind._rpcs3_configs())
        self.assertIn(pdefault, cfgs)                          # policy tree enumerated…
        self.assertIn(switch_bind._RPCS3_YML, cfgs)            # …and the default tree kept
        for cfg in cfgs:
            if switch_bind._sidecar(cfg).exists():
                switch_bind.restore_target(cfg)
        self.assertEqual(yaml.safe_load(pdefault.read_text(encoding="utf-8")), resting)
        self.assertFalse(switch_bind._sidecar(pdefault).exists())

    def test_configs_survive_policy_failure(self):
        policy.load_merged = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        cfgs = list(switch_bind._rpcs3_configs())
        self.assertIn(switch_bind._RPCS3_YML, cfgs)            # hardcoded floor still there


class BindFrontDoor(unittest.TestCase):
    """Drive switch_bind.bind("rpcs3", rom) through the FRONT DOOR (review fix: the wiring
    inside bind() — ladder target, in-bind orphan sweep, resolve->apply chain, chord merge —
    was only pinned piecewise). Promotes the Phase-3 headless smoke into the suite."""

    ROM_DISC = "/roms/ps3/DS.iso"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        gdir = self.root / "input_configs" / "global"
        self.default = _write_yml(gdir / "Default.yml", {"Player 1 Input": _native_block()})
        self.active = _write_yml(gdir / "Active.yml",
                                 {"Player 1 Input": _native_block(),
                                  "Miscellaneous": {"Pad handling sleep": 1000}})
        (self.root / "input_configs" / "active_input_configurations.yml").write_text(
            yaml.safe_dump({"Active Configurations": {"global": "Active"}}), encoding="utf-8")
        self.prof = _write_yml(gdir / "Race.yml",
                               {"Player 1 Input": _sdl_block(Cross="North")})
        self.ovr = gdir / ".mad-input-overrides.yml"
        self.ovr.write_text(yaml.safe_dump(
            {1: {"L2": "LT", "PS Button": "Back&Start"}}), encoding="utf-8")
        rom = self.root / "DS.desktop"
        rom.write_text(f'[Desktop Entry]\nExec=/apps/rpcs3.AppImage --no-gui "{self.ROM_DISC}"\n',
                       encoding="utf-8")
        self.rom = str(rom)
        (self.root / "games.yml").write_text(f"BLES00590: {self.ROM_DISC}\n", encoding="utf-8")

        be = {"config_file": str(self.default), "profile_docked": "Race"}
        for mod, attr, val in [
            (policy, "load_merged", lambda: {"backends": {"rpcs3": be}}),
            (switch_bind, "_RPCS3_YML", self.default),
            (switch_bind, "_RPCS3_INPUT_CONFIGS", self.root / "input_configs"),
            (rpcs3_cfg, "_OVERRIDES_FILE", self.ovr),
            (rpcs3_games, "_GAMES_YML", self.root / "games.yml"),
            (pgin, "_STORE", self.root / "pergame-input.json"),
            (pads_cmds, "_hands_off", lambda emu: False),
            (pads_cmds, "_stored_order", lambda emu: [DS5]),
            (switch_bind, "_resolve_pads",
             lambda emu, order=None: [sd(0, DS5, "ga", "DualSense")]),
        ]:
            self.addCleanup(setattr, mod, attr, getattr(mod, attr))
            setattr(mod, attr, val)
        self._env = os.environ.pop("MAD_FORCE_CONTEXT", None)
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        if self._env is not None:
            os.environ["MAD_FORCE_CONTEXT"] = self._env

    def _bind(self):
        with patch_sdl([sd(0, DS5, "ga", "DualSense")]):
            return switch_bind.bind("rpcs3", self.rom)

    def test_bind_targets_ladder_injects_profile_and_chord(self):
        bound = self._bind()
        self.assertEqual(bound, [DS5])
        # the ACTIVE config was bound, not the hardcoded Default
        self.assertEqual(yaml.safe_load(self.default.read_text()),
                         {"Player 1 Input": _native_block()})
        out = yaml.safe_load(self.active.read_text(encoding="utf-8"))
        p1 = out["Player 1 Input"]
        self.assertEqual(p1["Config"]["Cross"], "North")           # profile injected
        self.assertEqual(p1["Device"], "DualSense 1")              # seated
        self.assertEqual(p1["Config"]["PS Button"], "Back&Start")  # chord layered
        self.assertNotIn("L2", p1["Config"])                       # gameplay sidecar inert
        self.assertTrue(switch_bind._sidecar(self.active).exists())
        self.assertEqual(out["Miscellaneous"], {"Pad handling sleep": 1000})

    def test_bind_orphan_sweep_reverts_a_stale_other_target(self):
        # a prior crash left a dirty Default.yml + sidecar; THIS launch targets Active.yml —
        # bind()'s sweep must still revert the other file before snapshotting.
        resting = yaml.safe_load(self.default.read_text(encoding="utf-8"))
        switch_bind._sidecar(self.default).write_text(json.dumps(
            {"emu": "rpcs3", "input": switch_bind._snapshot("rpcs3", self.default)}),
            encoding="utf-8")
        players = [sd(0, DS5, "g", "DualSense")]
        with patch_sdl(players):
            rpcs3_cfg.assign_devices(players, config_path=str(self.default), manage=7)
        self.assertNotEqual(yaml.safe_load(self.default.read_text()), resting)
        self._bind()
        self.assertEqual(yaml.safe_load(self.default.read_text(encoding="utf-8")), resting)
        self.assertFalse(switch_bind._sidecar(self.default).exists())

    def test_bind_then_restore_roundtrip(self):
        resting = yaml.safe_load(self.active.read_text(encoding="utf-8"))
        self._bind()
        for cfg in switch_bind._rpcs3_configs():
            if switch_bind._sidecar(cfg).exists():
                switch_bind.restore_target(cfg)
        self.assertEqual(yaml.safe_load(self.active.read_text(encoding="utf-8")), resting)

    def test_handheld_context_resolves_its_own_axis(self):
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"
        pgin._STORE.write_text(json.dumps(
            {"BLES00590": {"profiles": {"handheld": "Race"}}}), encoding="utf-8")
        self._bind()
        p1 = yaml.safe_load(self.active.read_text())["Player 1 Input"]
        self.assertEqual(p1["Config"]["Cross"], "North")           # handheld per-game pick
        self.assertNotEqual(p1["Config"].get("PS Button"), "Back&Start")   # docked chord
                                                                           # not leaked


if __name__ == "__main__":
    unittest.main()
