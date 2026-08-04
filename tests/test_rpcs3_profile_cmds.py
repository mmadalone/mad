"""Tests for the RPCS3 input-profile picker pages (rpcs3prof / rpcs3profhh / rpcs3profpg /
rpcs3profpghh) + their tree placement. The per-button editors were REPLACED by the pickers
(2026-08-04): profiles are authored in RPCS3's own UI; MAD picks one per context (global +
per-game) and the launch binder copies its Player blocks into the resting target transiently.
Legacy `binds` in the store are tolerated forever (never destroy user data) but no longer
read or badged.

Run:  python3 -m unittest tests.test_rpcs3_profile_cmds -v
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from lib.madsrv import rpc, standalones_cmds
from lib.madsrv import rpcs3_pergame_input_cmds as pgin
from lib.madsrv import rpcs3_profile_cmds as prof
from lib.madsrv import rpcs3_ps_cmds  # noqa: F401 — import IS the rpcs3ps.* registration

ENTRY = next(s for s in standalones_cmds.STANDALONES if s["key"] == "rpcs3")
_S = "BLES00590"


class Registration(unittest.TestCase):
    def test_rpcs_registered(self):
        for m in ("rpcs3prof.get", "rpcs3prof.set", "rpcs3profhh.get", "rpcs3profhh.set",
                  "rpcs3profpg.games", "rpcs3profpg.get", "rpcs3profpg.set",
                  "rpcs3profpghh.games", "rpcs3profpghh.get", "rpcs3profpghh.set",
                  "rpcs3ps.input_get", "rpcs3ps.input_set",
                  "rpcs3ps.input_save", "rpcs3ps.input_cancel"):
            self.assertIn(m, rpc._METHODS, m)

    def test_editor_rpcs_gone(self):
        # The per-button editors are REMOVED — none of their RPCs may resurface.
        for m in ("rpcs3.input_get", "rpcs3.input_set", "rpcs3.input_save",
                  "rpcs3.input_cancel", "rpcs3pgin.input_get", "rpcs3pgin.input_set",
                  "rpcs3pgin.input_clear", "rpcs3pgin.input_save", "rpcs3pgin.input_cancel",
                  "rpcs3pgin.games"):
            self.assertNotIn(m, rpc._METHODS, m)

    def test_ps3_tile_input_group(self):
        # Tile = the DOCKED door: Input profiles replaces Mappings; PS button survives as the
        # one input_map leaf (no context key — docked door).
        secs = standalones_cmds._sections_for(ENTRY)
        grp = next(s for s in secs if s["label"] == "Input")
        rows = {r["label"]: r for r in grp["sections"]}
        self.assertEqual((rows["Input profiles"]["kind"], rows["Input profiles"]["arg"]),
                         ("settings", "rpcs3prof"))
        self.assertEqual((rows["PS button"]["kind"], rows["PS button"]["arg"]),
                         ("input_map", "rpcs3ps"))
        self.assertNotIn("Mappings", rows)
        for keep in ("Device visibility", "Pads to players"):
            self.assertIn(keep, rows)

    def test_ps3_pergame_is_game_first(self):
        secs = standalones_cmds._sections_for(ENTRY)
        pg = next(s for s in secs if s["label"] == "Per-game")
        self.assertEqual((pg["kind"], pg["arg"]), ("settings_pergame_menu", "rpcs3pg"))
        leaves = {l["label"]: l for l in pg["sections"]}
        self.assertEqual((leaves["Input profiles"]["kind"], leaves["Input profiles"]["arg"]),
                         ("pergame_settings", "rpcs3profpg"))
        self.assertNotIn("Mappings", leaves)

    def test_ps_button_art_alias(self):
        self.assertEqual(standalones_cmds._CAT_ART_ALIAS.get("ps-button"), "hotkeys")


class _FakeLocalPolicy:
    """A dict-backed localpolicy stand-in (atomic write + staterev not needed in tests)."""

    def __init__(self):
        self.data: dict = {}

    def load(self, path):
        return copy.deepcopy(self.data)

    def dump(self, path, data):
        self.data = copy.deepcopy(data)


def _seed_profiles(root: Path, stems=("Default", "Steamdeck", "RaceWheel")):
    g = root / "global"
    g.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (g / f"{stem}.yml").write_text(yaml.safe_dump(
            {"Player 1 Input": {"Handler": "SDL", "Device": "X 1",
                                "Config": {"Cross": "South"}, "Buddy Device": "Null"}}),
            encoding="utf-8")
    (g / ".mad-input-overrides.yml").write_text("docked: {}\n", encoding="utf-8")
    return g


class GlobalProfilePicker(unittest.TestCase):
    """rpcs3prof / rpcs3profhh — options list (ACTIVE stem excluded), stale-stem visibility,
    set/clear round-trip."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _seed_profiles(self.root)
        self.lp = _FakeLocalPolicy()
        self.base = {"config_file": str(self.root / "global" / "Default.yml")}
        for p in (mock.patch.object(prof, "localpolicy", self.lp),
                  mock.patch.object(prof, "load_merged", self._merged)):
            p.start()
            self.addCleanup(p.stop)

    def _merged(self):
        be = dict(self.base)
        be.update(self.lp.data.get("backends", {}).get("rpcs3", {}))
        return {"backends": {"rpcs3": be}}

    def _pointer(self, mapping):
        (self.root / "active_input_configurations.yml").write_text(
            yaml.safe_dump({"Active Configurations": mapping}), encoding="utf-8")

    def test_get_names_the_resting_config_first_and_excludes_dotfiles(self):
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["key"], "profile_docked")
        # the zero option NAMES the config RPCS3 itself rests on (no fuzzy "(none)")
        self.assertEqual(row["options"][0], "Default")
        # the resting config isn't duplicated as a pick; sidecar dotfile never listed
        self.assertEqual(row["options"][1:], ["RaceWheel", "Steamdeck"])
        self.assertEqual(row["value"], 0)
        self.assertTrue(row["picker"])
        self.assertIn("'Default' is the config RPCS3 itself has active", r["note"])

    def test_active_config_switch_moves_the_zero_label(self):
        self._pointer({"global": "Steamdeck"})
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["options"][0], "Steamdeck")           # now the resting one…
        self.assertEqual(row["options"][1:], ["Default", "RaceWheel"])   # …Default pickable

    def test_broken_pointer_zero_label_falls_to_default(self):
        # pointer names a MISSING file -> RPCS3 actually rests on Default; the label and
        # the exclusion must follow the REAL resting file, not the broken pointer stem
        self._pointer({"global": "Gone"})
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["options"][0], "Default")
        self.assertEqual(row["options"][1:], ["RaceWheel", "Steamdeck"])

    def test_pick_equal_to_resting_reads_as_the_zero_option(self):
        # the user picked Steamdeck, then made Steamdeck ACTIVE inside RPCS3: the stored
        # pick now IS the resting layout -> the row shows the zero option (a launch
        # identity-skips it anyway); picking index 0 clears the stored stem
        self._pointer({"global": "Steamdeck"})
        self.lp.data = {"backends": {"rpcs3": {"profile_docked": "Steamdeck"}}}
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        self.assertEqual(r["groups"][0]["settings"][0]["value"], 0)
        rpc._METHODS["rpcs3prof.set"][0]({"key": "profile_docked", "value": "0"})
        self.assertNotIn("profile_docked",
                         self.lp.data.get("backends", {}).get("rpcs3", {}))

    def test_set_and_clear_round_trip(self):
        _set = rpc._METHODS["rpcs3prof.set"][0]
        _set({"key": "profile_docked", "value": "1"})              # RaceWheel
        self.assertEqual(self.lp.data["backends"]["rpcs3"]["profile_docked"], "RaceWheel")
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        self.assertEqual(r["groups"][0]["settings"][0]["value"], 1)
        _set({"key": "profile_docked", "value": "0"})              # (none) -> key popped + pruned
        self.assertNotIn("profile_docked",
                         self.lp.data.get("backends", {}).get("rpcs3", {}))

    def test_handheld_ns_writes_its_own_key(self):
        rpc._METHODS["rpcs3profhh.set"][0]({"key": "profile_handheld", "value": "2"})
        self.assertEqual(self.lp.data["backends"]["rpcs3"]["profile_handheld"], "Steamdeck")
        self.assertNotIn("profile_docked", self.lp.data["backends"]["rpcs3"])

    def test_stale_stem_stays_visible(self):
        self.lp.data = {"backends": {"rpcs3": {"profile_docked": "Deleted"}}}
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        row = r["groups"][0]["settings"][0]
        self.assertIn("Deleted", row["options"])                   # appended, not hidden
        self.assertEqual(row["options"][row["value"]], "Deleted")

    def test_bad_key_and_index_rejected(self):
        _set = rpc._METHODS["rpcs3prof.set"][0]
        with self.assertRaises(rpc.RpcError):
            _set({"key": "bogus", "value": "1"})
        with self.assertRaises(rpc.RpcError):
            _set({"key": "profile_docked", "value": "99"})
        with self.assertRaises(rpc.RpcError):
            _set({"key": "profile_docked", "value": "x"})

    def test_set_rejects_a_stem_deleted_between_get_and_set(self):
        # Selecting the stale appended option (its file is gone) must EINVAL instead of
        # silently storing a pick the launch would then ignore.
        self.lp.data = {"backends": {"rpcs3": {"profile_docked": "Deleted"}}}
        r = rpc._METHODS["rpcs3prof.get"][0]({})
        idx = r["groups"][0]["settings"][0]["options"].index("Deleted")
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["rpcs3prof.set"][0]({"key": "profile_docked", "value": str(idx)})
        rpc._METHODS["rpcs3prof.set"][0]({"key": "profile_docked", "value": "0"})  # clearing works
        self.assertNotIn("profile_docked",
                         self.lp.data.get("backends", {}).get("rpcs3", {}))


class PergameProfilePicker(unittest.TestCase):
    """rpcs3profpg / rpcs3profpghh — per-game picks: inherit label, context independence,
    legacy-binds preservation, badge semantics, games payload."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _seed_profiles(self.root)
        self._st, pgin._STORE = pgin._STORE, self.root / "pergame-input.json"
        self.lp = _FakeLocalPolicy()
        self.base = {"config_file": str(self.root / "global" / "Default.yml"),
                     "profile_docked": "RaceWheel"}
        for p in (mock.patch.object(prof, "localpolicy", self.lp),
                  mock.patch.object(prof, "load_merged",
                                    lambda: {"backends": {"rpcs3": dict(self.base)}})):
            p.start()
            self.addCleanup(p.stop)
        import lib.staterev as sr
        self._bump, sr.bump = sr.bump, lambda name: None

    def tearDown(self):
        pgin._STORE = self._st
        import lib.staterev as sr
        sr.bump = self._bump

    def _store(self):
        return json.loads(pgin._STORE.read_text()) if pgin._STORE.exists() else {}

    def test_get_shows_inherit_with_global_stem(self):
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["options"][0], "(inherit global: RaceWheel)")
        self.assertEqual(row["value"], 0)
        self.assertEqual(len(r["groups"]), 1)                      # no ports group on PS3

    def test_set_and_clear_round_trip_prunes_entry(self):
        _set = rpc._METHODS["rpcs3profpg.set"][0]
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        idx = r["groups"][0]["settings"][0]["options"].index("Steamdeck")
        _set({"titleid": _S, "key": "profile", "value": str(idx)})
        self.assertEqual(self._store()[_S], {"profiles": {"docked": "Steamdeck"}})
        _set({"titleid": _S, "key": "profile", "value": "0"})      # inherit -> entry pruned
        self.assertNotIn(_S, self._store())

    def test_contexts_are_independent(self):
        pgset, hhset = rpc._METHODS["rpcs3profpg.set"][0], rpc._METHODS["rpcs3profpghh.set"][0]
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        idx = r["groups"][0]["settings"][0]["options"].index("Steamdeck")
        pgset({"titleid": _S, "key": "profile", "value": str(idx)})
        rh = rpc._METHODS["rpcs3profpghh.get"][0]({"titleid": _S})
        rowh = rh["groups"][0]["settings"][0]
        # handheld global unset -> the inherit label NAMES the resting config it falls to
        self.assertEqual(rowh["options"][0], "(inherit global: Default)")
        self.assertEqual(rowh["value"], 0)                         # docked pick doesn't leak
        idxh = rowh["options"].index("RaceWheel")
        hhset({"titleid": _S, "key": "profile", "value": str(idxh)})
        self.assertEqual(self._store()[_S]["profiles"],
                         {"docked": "Steamdeck", "handheld": "RaceWheel"})
        hhset({"titleid": _S, "key": "profile", "value": "0"})     # clear handheld only
        self.assertEqual(self._store()[_S]["profiles"], {"docked": "Steamdeck"})

    def test_legacy_binds_survive_profile_edits(self):
        # never destroy user data: a legacy per-button entry keeps its binds (folded under
        # "binds") through a pick + clear, and the emptied pick doesn't prune the entry.
        pgin._STORE.write_text(json.dumps({_S: {"docked": {"1": {"Cross": "East"}}}}),
                               encoding="utf-8")
        _set = rpc._METHODS["rpcs3profpg.set"][0]
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        idx = r["groups"][0]["settings"][0]["options"].index("Steamdeck")
        _set({"titleid": _S, "key": "profile", "value": str(idx)})
        self.assertEqual(self._store()[_S],
                         {"binds": {"docked": {"1": {"Cross": "East"}}},
                          "profiles": {"docked": "Steamdeck"}})
        _set({"titleid": _S, "key": "profile", "value": "0"})
        self.assertEqual(self._store()[_S], {"binds": {"docked": {"1": {"Cross": "East"}}}})

    def test_per_title_config_game_gets_honest_label_and_full_list(self):
        # REVIEW FIX: a game resting on its own input_configs/<SERIAL>/Default.yml — no
        # global stem is an identity there (exclusion off: Default becomes pickable, so
        # forcing the globally-active profile onto the game is expressible) and the
        # inherit label says what actually rests instead of naming a global config.
        (self.root / _S).mkdir()
        (self.root / _S / "Default.yml").write_text("Player 1 Input:\n  Handler: 'Null'\n",
                                                    encoding="utf-8")
        self.base.pop("profile_docked")                    # no global pick -> fallback label
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        row = r["groups"][0]["settings"][0]
        self.assertEqual(row["options"][0], "(inherit: this game's own RPCS3 config)")
        self.assertEqual(row["options"][1:], ["Default", "RaceWheel", "Steamdeck"])
        # a game WITHOUT a per-title config keeps the normal shape in the same session
        r2 = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": "BCES00002"})
        row2 = r2["groups"][0]["settings"][0]
        self.assertEqual(row2["options"][0], "(inherit global: Default)")
        self.assertEqual(row2["options"][1:], ["RaceWheel", "Steamdeck"])
        # with a global pick set, the label names the pick (it DOES apply over per-title)
        self.base["profile_docked"] = "RaceWheel"
        r3 = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        self.assertEqual(r3["groups"][0]["settings"][0]["options"][0],
                         "(inherit global: RaceWheel)")

    def test_stale_pergame_stem_rejected_on_set(self):
        pgin._STORE.write_text(json.dumps({_S: {"profiles": {"docked": "Deleted"}}}),
                               encoding="utf-8")
        r = rpc._METHODS["rpcs3profpg.get"][0]({"titleid": _S})
        opts = r["groups"][0]["settings"][0]["options"]
        self.assertIn("Deleted", opts)                             # visible for clearing
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["rpcs3profpg.set"][0](
                {"titleid": _S, "key": "profile", "value": str(opts.index("Deleted"))})

    def test_bad_serial_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rpc._METHODS["rpcs3profpg.get"][0]({"titleid": "not-a-serial"})

    def test_games_badge_profiles_not_legacy_binds(self):
        pgin._STORE.write_text(json.dumps(
            {_S: {"profiles": {"handheld": "Steamdeck"}},
             "BCES00002": {"docked": {"1": {"Cross": "East"}}}}), encoding="utf-8")
        fake_games = [{"key": _S, "name": "Demon's Souls", "path": "/roms/ps3/DS.desktop"},
                      {"key": "BCES00002", "name": "Genji", "path": "/roms/ps3/G.desktop"}]
        with mock.patch.object(prof.rpcs3_games, "games", lambda: fake_games), \
             mock.patch.object(prof.rpcs3_games, "stem_of", lambda p: Path(p).stem):
            r = rpc._METHODS["rpcs3profpghh.games"][0]({})
        by = {g["titleid"]: g for g in r["games"]}
        self.assertEqual(r["system"], "ps3")
        self.assertTrue(by[_S]["override"])                        # a pick badges
        self.assertEqual(by[_S]["summary"], "Custom input")
        self.assertFalse(by["BCES00002"]["override"])              # legacy binds do NOT badge
        self.assertEqual(by["BCES00002"]["stem"], "G")


if __name__ == "__main__":
    unittest.main()
