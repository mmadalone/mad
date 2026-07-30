"""P13 - per-game emulator-specific assets (category "emucfg"): textures / per-game settings / console
saves / mods / shader / cheats keyed by a per-game ID (PCSX2 serial, Dolphin GameID, Switch/Wii U title-id).

Covers the SAFETY surface the adversarial review must gate:
  * the resolver builds correct emucfg rels (allowlisted, logical/front-door path), present-only, empty dirs
    dropped, case-insensitive per-title dirs, Ryujinx opaque save via ExtraData0;
  * owner_backend_for_rel maps each rel to the RIGHT emulator (longest prefix; switch is 1:many);
  * the engine ROUND-TRIP: backup -> corrupt live -> restore with the rule-5 snapshot in a WRITABLE dir;
  * the per-emulator RUNNING GUARD refuses the rel's owning emulator on the game-first restore;
  * the backup/restore rel_allowed LOCKSTEP (a forged emucfg rel is never stored);
  * _ALL_ASSET_KEYS still excludes the new keys (the P9 "All" expander must not balloon).

Runs against a FAKE $HOME (patch Path.home), like tests/test_emucfg.py.
Run:  python3 -m unittest tests.test_pergame_emucfg -v
"""
from __future__ import annotations

import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import emu_map, game_files, granular_backup as gb   # noqa: E402
from lib.madsrv import granular_cmds as g                    # noqa: E402


def _grp(key, label, files):
    return {"key": key, "label": label, "category": "emucfg",
            "present": bool(files), "size": sum(f["size"] for f in files), "files": files}


class OwnerAndKeys(unittest.TestCase):
    def test_owner_backend_longest_prefix(self):
        cases = {
            "emucfg/.local/share/eden/shader/0100dca0064a6000/x": "eden",
            "emucfg/.local/share/citron/nand/user/save/0/ACC/010015100b514000/x": "citron",
            "emucfg/.config/Ryujinx/bis/user/save/0000000000000001/0/x": "ryujinx",
            "emucfg/.config/Ryujinx/mods/contents/0100F2C0/y": "ryujinx",
            "emucfg/Emulation/saves/Cemu/saves/00050000/1010ed00/x": "cemu",
            "emucfg/Emulation/storage/pcsx2/textures/SLUS-1/z": "pcsx2",
            "emucfg/.var/app/org.libretro.RetroArch/config/retroarch/cheats/Nestopia/G.cht": "retroarch",
        }
        for rel, be in cases.items():
            self.assertEqual(emu_map.owner_backend_for_rel(rel), be, rel)
        self.assertIsNone(emu_map.owner_backend_for_rel("emucfg/.ssh/id_rsa"))
        self.assertIsNone(emu_map.owner_backend_for_rel("notemucfg/x"))

    def test_all_asset_keys_exclude_p13_keys(self):
        self.assertEqual(g._ALL_ASSET_KEYS, ("rom", "media", "saves", "states", "lutriscfg"))
        for k in ("textures", "console-save", "shader", "mods", "settings", "cheats"):
            self.assertNotIn(k, g._ALL_ASSET_KEYS)

    def test_emucfg_keys_covers_every_per_game_emucfg_group(self):
        # every per-game emucfg asset key the resolver can emit MUST be in _EMUCFG_KEYS, else ticking ONLY
        # that row makes plan_game_assets skip the emucfg resolve -> a silent empty backup (the graphicpacks
        # bug the final review caught). Drift guard.
        for k in ("settings", "textures", "console-save", "shader", "mods", "cheats", "graphicpacks"):
            self.assertIn(k, gb._EMUCFG_KEYS, f"{k} missing from _EMUCFG_KEYS -> {k}-only backup silently empty")

    def test_wholesale_emucfg_defaults_user_2026_07_29(self):
        # the per-emulator "Emulator config" default_ticked the user asked to turn ON (2026-07-29).
        # Read the STATIC table (never walk the real device dirs) so this is fast + device-independent.
        def d(emu, key):
            e = emu_map._emulator(emu) or {}
            return next((bool(g.get("default")) for g in e.get("groups", []) if g["key"] == key), None)
        for emu, key in [("pcsx2", "textures"), ("pcsx2", "shader"), ("dolphin", "textures"),
                         ("dolphin", "wii_nand"), ("cemu", "graphicpacks"), ("citron", "shader"),
                         ("eden", "mods"), ("eden", "shader"), ("ryujinx", "mods"), ("ryujinx", "shader"),
                         ("rpcs3", "shader"), ("xemu", "hdd"), ("retroarch", "overlays")]:
            self.assertTrue(d(emu, key), f"{emu}.{key} should default ON")
        # the new shader groups are allowlisted (restore-reachable) with the right owner.
        for rel, owner in [("emucfg/Emulation/storage/pcsx2/cache/vulkan_shaders.bin", "pcsx2"),
                           ("emucfg/.cache/rpcs3/cache/x", "rpcs3"),
                           ("emucfg/.config/Ryujinx/games/0100/cache/shader/x", "ryujinx")]:
            self.assertTrue(emu_map.rel_allowed(rel), rel)
            self.assertEqual(emu_map.owner_backend_for_rel(rel), owner)


class Resolver(unittest.TestCase):
    """The per-emulator resolvers against a fake $HOME."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="p13h-"))
        self._p = mock.patch.object(Path, "home", staticmethod(lambda: self.home))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _size(self, p):
        return gb._path_size(p)

    def test_switch_union_present_only_and_allowlisted(self):
        tid = "0100dca0064a6000"
        # eden save (glob the account dir) + citron shader + ryujinx save (ExtraData0) + ryujinx mods (mixed case)
        eden_save = self.home / ".local/share/eden/nand/user/save/0000000000000000/ACCT" / tid
        eden_save.mkdir(parents=True)
        (eden_save / "s.dat").write_bytes(b"SAVE")
        cit_shader = self.home / ".local/share/citron/shader" / tid           # lowercase dir
        cit_shader.mkdir(parents=True)
        (cit_shader / "opengl.bin").write_bytes(b"SHADER")
        rsave = self.home / ".config/Ryujinx/bis/user/save/0000000000000001"
        rsave.mkdir(parents=True)
        (rsave / "ExtraData0").write_bytes(struct.pack("<Q", 0x0100DCA0064A6000) + b"\x00" * 8)
        (rsave / "d").write_bytes(b"X")
        rmod = self.home / ".config/Ryujinx/mods/contents" / tid.upper()      # UPPER dir, tag is lower
        rmod.mkdir(parents=True)
        (rmod / "mod.txt").write_bytes(b"MOD")
        (self.home / ".local/share/citron/load" / tid.upper()).mkdir(parents=True)  # EMPTY -> dropped

        groups = game_files._emucfg_game_assets("switch", f"Game [{tid}][v0]", None, self._size)
        bykey = {gg["key"]: gg for gg in groups}
        self.assertIn("console-save", bykey)     # eden + ryujinx saves (union)
        self.assertIn("shader", bykey)           # citron shader (case-insensitive match)
        self.assertNotIn("mods", {k for k in bykey if not bykey[k]["files"]})  # present-only
        self.assertIn("mods", bykey)             # ryujinx mods present (empty citron load dropped)
        for gg in groups:
            self.assertEqual(gg["category"], "emucfg")
            for f in gg["files"]:
                self.assertTrue(f["rel"].startswith("emucfg/"))
                self.assertTrue(emu_map.rel_allowed(f["rel"]), f["rel"])
        rels = {f["rel"] for f in bykey["console-save"]["files"]}
        self.assertTrue(any("/eden/" in r for r in rels))
        self.assertTrue(any("/Ryujinx/bis/user/save/0000000000000001" in r for r in rels))
        self.assertEqual(bykey["mods"]["files"][0]["kind"], "folder")

    def test_switch_untagged_stem_returns_nothing(self):
        # no [TITLEID] tag and no library match -> no per-game assets (never crashes)
        self.assertEqual(game_files._emucfg_game_assets("switch", "Some Game", None, self._size), [])

    def test_cemu_console_save_front_door_logical_rel(self):
        tid = "000500001010ed00"
        save = self.home / "Emulation/saves/Cemu/saves/00050000/1010ed00"
        save.mkdir(parents=True)
        (save / "game.sav").write_bytes(b"SAVE")
        with mock.patch("lib.madsrv.cemu_games._library",
                        lambda: {tid: {"name": "N", "stem": "MyWiiUGame", "path": ""}}):
            groups = game_files._emucfg_game_assets("wiiu", "MyWiiUGame", None, self._size)
        cs = {gg["key"]: gg for gg in groups}["console-save"]
        self.assertEqual(cs["files"][0]["rel"],
                         "emucfg/Emulation/saves/Cemu/saves/00050000/1010ed00")   # LOGICAL, not realpath'd
        self.assertTrue(emu_map.rel_allowed(cs["files"][0]["rel"]))

    def test_pcsx2_settings_textures_cheats(self):
        key = "SLUS-12345_ABCD1234"
        cfg = self.home / ".config/PCSX2"
        (cfg / "gamesettings").mkdir(parents=True)
        (cfg / "gamesettings" / f"{key}.ini").write_bytes(b"INI")
        tex = self.home / "Emulation/storage/pcsx2/textures/SLUS-12345"
        tex.mkdir(parents=True)
        (tex / "t.png").write_bytes(b"TEX")
        (cfg / "cheats").mkdir(parents=True)
        (cfg / "cheats" / f"{key}.pnach").write_bytes(b"CHEAT")
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.iso"]), \
             mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda p: key):
            groups = game_files._emucfg_game_assets("ps2", "Game", "LRPS2", self._size)
        bykey = {gg["key"]: gg for gg in groups}
        self.assertEqual(bykey["settings"]["files"][0]["rel"],
                         f"emucfg/.config/PCSX2/gamesettings/{key}.ini")
        self.assertEqual(bykey["textures"]["files"][0]["kind"], "folder")
        self.assertTrue(bykey["textures"]["files"][0]["rel"].endswith("/textures/SLUS-12345"))
        self.assertIn("cheats", bykey)
        for gg in groups:
            for f in gg["files"]:
                self.assertTrue(emu_map.rel_allowed(f["rel"]), f["rel"])

    def test_empty_dir_dropped(self):
        # a per-title dir that exists but is EMPTY must not show as present
        tid = "0100000000010000"
        (self.home / ".local/share/eden/shader" / tid).mkdir(parents=True)  # empty
        groups = game_files._emucfg_game_assets("switch", f"G [{tid}]", None, self._size)
        self.assertEqual(groups, [])


class Engine(unittest.TestCase):
    """plan/backup/restore of per-game emucfg assets: rule-5, running guard, lockstep."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="p13e-"))
        self._ph = mock.patch.object(Path, "home", staticmethod(lambda: self.home))
        self._ph.start()
        self._patches = [
            mock.patch.object(gb, "es_gamelist_record", lambda s, st: {}),
            mock.patch.object(gb.es_collections, "rom_root", lambda: self.home / "ROMs"),
            mock.patch.object(gb.proc_guard, "esde_running", lambda: False),
            mock.patch.object(gb.proc_guard, "emulator_running", lambda name: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._ph.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _backup(self, groups, keys):
        with mock.patch.object(game_files, "resolve_game_assets", lambda s, st, systems=None, emucfg=True, steam_heavy=True, deadline=None: groups):
            return gb.backup_game_assets([{"system": "ps2", "stem": "Game", "keys": keys}],
                                         str(self.home / "dest"), "20260729T000000",
                                         lambda e: None, lambda: False)

    def test_roundtrip_rule5(self):
        (self.home / "dest").mkdir()
        cfg = self.home / ".config/PCSX2/gamesettings"
        cfg.mkdir(parents=True)
        ini = cfg / "SLUS-1_A.ini"
        ini.write_bytes(b"ORIG-INI")
        save = self.home / "Emulation/saves/Cemu/saves/00050000/1010ed00"
        save.mkdir(parents=True)
        (save / "game.sav").write_bytes(b"ORIG-SAVE")
        groups = [
            _grp("settings", "Game settings",
                 [{"src": str(ini), "rel": "emucfg/.config/PCSX2/gamesettings/SLUS-1_A.ini",
                   "kind": "file", "size": 8}]),
            _grp("console-save", "Console save",
                 [{"src": str(save), "rel": "emucfg/Emulation/saves/Cemu/saves/00050000/1010ed00",
                   "kind": "folder", "size": 9}]),
        ]
        out = self._backup(groups, ["settings", "console-save"])
        setdir = Path(out["path"])
        self.assertEqual(out["copied"], 2)
        self.assertTrue((setdir / "emucfg/.config/PCSX2/gamesettings/SLUS-1_A.ini").is_file())
        self.assertTrue((setdir / "emucfg/Emulation/saves/Cemu/saves/00050000/1010ed00/game.sav").is_file())

        # corrupt the live copies, then restore -> rule-5 brings the originals back
        ini.write_bytes(b"CORRUPT")
        (save / "game.sav").write_bytes(b"CORRUPT")
        summary = gb.restore_game_assets(str(setdir),
                                         [{"system": "ps2", "stem": "Game", "keys": ["settings", "console-save"]}],
                                         "20260729T111111", lambda e: None, lambda: False)
        self.assertEqual(ini.read_bytes(), b"ORIG-INI")
        self.assertEqual((save / "game.sav").read_bytes(), b"ORIG-SAVE")
        self.assertGreaterEqual(summary.get("restored", 0), 2)
        # rule-5 snapshot(s) landed in a WRITABLE dir UNDER the fake home (the /home-unwritable fix), holding
        # the corrupt versions.
        snaps = summary.get("snapshots", [])
        self.assertTrue(snaps)
        for snap in snaps:
            self.assertTrue(snap.startswith(str(self.home)), snap)

    def test_running_guard_refuses_owner(self):
        (self.home / "dest").mkdir()
        save = self.home / ".local/share/eden/nand/user/save/0000000000000000/ACCT/0100dca0064a6000"
        save.mkdir(parents=True)
        (save / "s.dat").write_bytes(b"S")
        rel = "emucfg/.local/share/eden/nand/user/save/0000000000000000/ACCT/0100dca0064a6000"
        groups = [_grp("console-save", "Console save",
                       [{"src": str(save), "rel": rel, "kind": "folder", "size": 1}])]
        out = self._backup(groups, ["console-save"])
        # restore with EDEN running -> refuse (the rel's owner is eden, not the ES-DE system "ps2")
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda name: name == "eden"):
            with self.assertRaises(RuntimeError) as cm:
                gb.restore_game_assets(out["path"],
                                       [{"system": "ps2", "stem": "Game", "keys": ["console-save"]}],
                                       "T2", lambda e: None, lambda: False)
        self.assertIn("eden", str(cm.exception).lower())
        # a DIFFERENT emulator running does NOT block this restore
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda name: name == "citron"):
            gb.restore_game_assets(out["path"],
                                   [{"system": "ps2", "stem": "Game", "keys": ["console-save"]}],
                                   "T3", lambda e: None, lambda: False)

    def test_backup_lockstep_skips_non_allowlisted(self):
        (self.home / "dest").mkdir()
        secret = self.home / ".ssh"
        secret.mkdir()
        (secret / "id_rsa").write_bytes(b"SECRET")
        # a (hypothetical) resolver slip emitting an emucfg rel OUTSIDE the emulator dirs must NOT be stored,
        # or backup would hold a rel that restore rejects (and it would put ~/.ssh in a backup).
        groups = [_grp("cheats", "Cheats",
                       [{"src": str(secret / "id_rsa"), "rel": "emucfg/.ssh/id_rsa",
                         "kind": "file", "size": 6}])]
        with mock.patch.object(game_files, "resolve_game_assets", lambda s, st, systems=None, emucfg=True, steam_heavy=True, deadline=None: groups):
            manifest, plan = gb.plan_game_assets([{"system": "ps2", "stem": "Game", "keys": ["cheats"]}],
                                                 "T", lambda e: None, lambda: False)
        self.assertEqual(plan, [])                        # forged rel skipped
        self.assertEqual(gb.backup_manifest.items(manifest, "emucfg", "ps2"), [])


class GuardKeying(unittest.TestCase):
    """The emucfg running guard keys on the item's REL owner, with a system fallback (review #1/#10)."""

    def _manifest(self, system, item_id, rel):
        bm = gb.backup_manifest
        m = bm.new_manifest("granular", created="x")
        bm.add_item(m, category="emucfg", category_label="Emulator config", system=system,
                    system_label=system,
                    item=bm.make_item(id=item_id, name="n", src="/x", rel=rel, kind="file", size=1,
                                      extra={"group": "config"}))
        return m

    def test_guard_owner_from_rel_even_when_id_differs(self):
        # forged manifest: id != rel; the guard MUST key on the REL's owner (eden), not the id.
        m = self._manifest("switch", "bogus-id", "emucfg/.local/share/eden/load/0100DCA0064A6000/mod")
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda be: be == "eden"):
            with self.assertRaises(RuntimeError) as cm:
                gb._refuse_running_emucfg(m, [("switch", "bogus-id")])
        self.assertIn("eden", str(cm.exception).lower())

    def test_guard_falls_back_to_system_when_rel_not_allowlisted(self):
        # a category backup whose rel isn't perfectly allowlisted still guards via system (== emulator).
        m = self._manifest("pcsx2", "emucfg/.config/PCSX2/PCSX2.ini", "emucfg/.config/PCSX2/PCSX2.ini")
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda be: be == "pcsx2"):
            with self.assertRaises(RuntimeError):
                gb._refuse_running_emucfg(m, [("pcsx2", "emucfg/.config/PCSX2/PCSX2.ini")])

    def test_guard_passes_when_owner_not_running(self):
        m = self._manifest("switch", "id", "emucfg/.local/share/citron/load/0100DCA0064A6000/mod")
        with mock.patch.object(gb.proc_guard, "emulator_running", lambda be: be == "eden"):
            gb._refuse_running_emucfg(m, [("switch", "id")])   # citron owner, only eden running -> no raise


class ReviewFixes(unittest.TestCase):
    """Resolver-side review fixes against a fake $HOME."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="p13rf-"))
        self._p = mock.patch.object(Path, "home", staticmethod(lambda: self.home))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _size(self, p):
        return gb._path_size(p)

    def test_ra_cheats_reads_sibling_cheats_dir_not_config_subdir(self):
        from lib import retroarch_cfg
        rabase = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/config"   # ends in /config
        cht = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/cheats/Nestopia/Game.cht"
        cht.parent.mkdir(parents=True)
        cht.write_bytes(b"CHEAT")
        wrong = rabase / "cheats/Nestopia/Game.cht"                                       # the OLD wrong path
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(b"WRONG")
        with mock.patch.object(retroarch_cfg, "RA_CONFIG_BASE", rabase), \
             mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: "Nestopia"):
            groups = game_files._ra_cheats_assets(self.home, "nes", "Game", "Nestopia", self._size)
        files = groups[0]["files"]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["rel"].endswith("/retroarch/cheats/Nestopia/Game.cht"))
        self.assertNotIn("/config/cheats/", files[0]["rel"])

    def test_dolphin_resolves_via_bounded_gameid(self):
        # _dolphin_assets resolves the GameID via a BOUNDED gameid() (cache-first, capped shell-out) so an
        # uncached game - or an SD-symlink realpath that misses the ~/ROMs-keyed cache - still gets its rows.
        from lib import dolphin_gameids
        gs = self.home / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GameSettings"
        gs.mkdir(parents=True)
        (gs / "GALE01.ini").write_bytes(b"INI")
        seen = {}

        def fake_gameid(p, timeout=40):
            seen["timeout"] = timeout
            return "GALE01"

        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.rvz"]), \
             mock.patch.object(dolphin_gameids, "gameid", fake_gameid):
            groups = game_files._dolphin_assets(self.home, "gc", "Melee", self._size)
        self.assertIn("settings", {gg["key"] for gg in groups})
        self.assertLessEqual(seen["timeout"], 12)    # bounded well under the asset-list RPC budget

    def test_resolve_game_assets_emucfg_false_skips_emucfg_groups(self):
        from lib import retroarch_cfg
        tid = "0100dca0064a6000"
        save = self.home / ".local/share/citron/nand/user/save/0000000000000000/ACCT" / tid
        save.mkdir(parents=True)
        (save / "s").write_bytes(b"S")
        with mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: None):
            off = game_files.resolve_game_assets("switch", f"G [{tid}]", None, emucfg=False)
            on = game_files.resolve_game_assets("switch", f"G [{tid}]", None, emucfg=True)
        self.assertFalse(any(gg["category"] == "emucfg" for gg in off))
        self.assertTrue(any(gg["category"] == "emucfg" and gg["key"] == "console-save" for gg in on))

    def test_duplicate_cheats_merged_for_ps2_via_ra_core(self):
        from lib import retroarch_cfg
        key = "SLUS-1_A"
        cfg = self.home / ".config/PCSX2"
        (cfg / "cheats").mkdir(parents=True)
        (cfg / "cheats" / f"{key}.pnach").write_bytes(b"PNACH")
        rcheat = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/cheats/LRPS2/Game.cht"
        rcheat.parent.mkdir(parents=True)
        rcheat.write_bytes(b"CHT")
        rabase = self.home / ".var/app/org.libretro.RetroArch/config/retroarch/config"
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.iso"]), \
             mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda p: key), \
             mock.patch.object(retroarch_cfg, "RA_CONFIG_BASE", rabase), \
             mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: "LRPS2"):
            groups = game_files._emucfg_game_assets("ps2", "Game", "LRPS2", self._size)
        cheats = [gg for gg in groups if gg["key"] == "cheats"]
        self.assertEqual(len(cheats), 1, "pnach + .cht collapse into ONE cheats group")
        rels = {f["rel"] for f in cheats[0]["files"]}
        self.assertTrue(any(r.endswith(".pnach") for r in rels))
        self.assertTrue(any(r.endswith(".cht") for r in rels))

    def test_lockstep_control_char_rel_skipped_on_backup(self):
        # the rel is under an allowlisted dir but has a control char -> restore would reject it, so backup
        # must NOT store it (lockstep).
        groups = [_grp("cheats", "Cheats",
                       [{"src": "/x", "rel": "emucfg/.config/PCSX2/cheats/bad\nname.pnach",
                         "kind": "file", "size": 1}])]
        with mock.patch.object(game_files, "resolve_game_assets",
                               lambda s, st, systems=None, emucfg=True, steam_heavy=True, deadline=None: groups):
            manifest, plan = gb.plan_game_assets([{"system": "ps2", "stem": "G", "keys": ["cheats"]}],
                                                 "T", lambda e: None, lambda: False)
        self.assertEqual(plan, [])

    def test_ps2_folder_memcard_save_by_serial(self):
        key = "SLUS-21273_ABCD"
        saves = self.home / "Emulation/saves/pcsx2/saves"
        gsave = saves / "Mcd001_converted.ps2" / "BASLUS-21273PON"   # FOLDER memcard, per-serial subdir
        gsave.mkdir(parents=True)
        (gsave / "save.bin").write_bytes(b"SAVE")
        (saves / "Mcd001.ps2").write_bytes(b"SHARED-FILE-MEMCARD")   # FILE memcard -> shared, NOT per-game
        other = saves / "Mcd001_converted.ps2" / "BASLUS-99999"      # a different game -> must not match
        other.mkdir(parents=True)
        (other / "x").write_bytes(b"OTHER")
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.iso"]), \
             mock.patch("lib.madsrv.pcsx2_games.path_to_key", lambda p: key):
            groups = {gg["key"]: gg for gg in game_files._pcsx2_assets(self.home, "ps2", "G", self._size)}
        cs = groups["console-save"]
        self.assertTrue(cs["present"])
        rels = {f["rel"] for f in cs["files"]}
        self.assertEqual(rels, {"emucfg/Emulation/saves/pcsx2/saves/Mcd001_converted.ps2/BASLUS-21273PON"})
        self.assertTrue(emu_map.rel_allowed(next(iter(rels))))

    def test_gc_gci_folder_save_by_gameid(self):
        from lib import dolphin_gameids
        data = self.home / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
        card = data / "GC/USA/Card A"
        card.mkdir(parents=True)
        (card / "64-GSWE-RogueLeader.gci").write_bytes(b"GCI")
        (card / "64-GALE-Melee.gci").write_bytes(b"OTHER")           # different game code -> must not match
        (data / "GC/USA/MemoryCardA.USA.raw").write_bytes(b"RAW")    # shared raw memcard -> not per-game
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.rvz"]), \
             mock.patch.object(dolphin_gameids, "gameid", lambda p, timeout=40: "GSWE64"):
            groups = {gg["key"]: gg for gg in game_files._dolphin_assets(self.home, "gc", "RL", self._size)}
        cs = groups["console-save"]
        self.assertTrue(cs["present"])
        rels = {f["rel"] for f in cs["files"]}
        self.assertEqual(len(rels), 1)
        self.assertTrue(next(iter(rels)).endswith("GC/USA/Card A/64-GSWE-RogueLeader.gci"))
        self.assertTrue(emu_map.rel_allowed(next(iter(rels))))

    def test_ryujinx_per_game_shader_parity(self):
        tid = "0100dca0064a6000"
        cache = self.home / ".config/Ryujinx/games" / tid / "cache" / "shader"
        cache.mkdir(parents=True)
        (cache / "opengl.bin").write_bytes(b"SHADER")
        groups = {gg["key"]: gg for gg in game_files._emucfg_game_assets("switch", f"G [{tid}]", None, self._size)}
        self.assertIn("shader", groups)
        rels = {f["rel"] for f in groups["shader"]["files"]}
        self.assertTrue(any("/Ryujinx/games/" in r and "/cache" in r for r in rels))
        self.assertTrue(all(emu_map.rel_allowed(r) for r in rels))

    def test_list_emulators_skips_folder_walk(self):
        # the tile screen must NOT size-walk the big folder groups (textures) even though they now default ON;
        # the group view (list_files) still walks + reports them.
        (self.home / ".config/PCSX2/inis").mkdir(parents=True)
        (self.home / ".config/PCSX2/inis/PCSX2.ini").write_bytes(b"cfg")
        tex = self.home / "Emulation/storage/pcsx2/textures/SLUS-1"
        tex.mkdir(parents=True)
        (tex / "big.png").write_bytes(b"X" * 50000)
        tiles = {t["key"]: t for t in emu_map.list_emulators()}
        files = {gg["key"]: gg for gg in emu_map.list_files("pcsx2")}
        self.assertGreaterEqual(files["textures"]["size"], 50000)         # group view walked the folder
        self.assertLess(tiles["pcsx2"]["size"], 50000)                    # tile screen did NOT

    def test_ra_save_rows_only_for_ra_systems(self):
        # RA "Save"/"Save state" appear ONLY for a RA-core (or mGBA) launch; a standalone-emulator system
        # (ps2/gc/wii/wiiu/switch) omits them (its save is the "Console save").
        from lib import retroarch_cfg, es_gamelist, es_collections, esde_settings, mad_paths

        def run(system, corename):
            with mock.patch.object(retroarch_cfg, "save_corename", lambda s, st, systems=None: corename), \
                 mock.patch.object(es_gamelist, "rom_paths", lambda s: {}), \
                 mock.patch.object(es_gamelist, "media_for", lambda s, st, kinds=None: {}), \
                 mock.patch.object(es_collections, "rom_root", lambda: self.home / "ROMs"), \
                 mock.patch.object(esde_settings, "media_root", lambda: self.home / "media"), \
                 mock.patch.object(mad_paths, "saves_root", lambda: self.home / "saves"):
                return {gg["key"] for gg in game_files.resolve_game_assets(system, "X")}

        self.assertFalse({"saves", "states"} & run("ps2", None))    # standalone -> no RA rows
        self.assertLessEqual({"saves", "states"}, run("nes", "Nestopia"))   # RA core -> both rows

    def test_wii_nand_console_save(self):
        from lib import dolphin_gameids
        data = self.home / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
        nand = data / "Wii/title/00010000/52375050/data"        # R7PP -> 52375050 (Punch-Out)
        nand.mkdir(parents=True)
        (nand / "banner.bin").write_bytes(b"SAVE")
        with mock.patch.object(game_files, "resolve_rom", lambda s, st: ["/rom.rvz"]), \
             mock.patch.object(dolphin_gameids, "gameid", lambda p, timeout=40: "R7PP01"):
            groups = {gg["key"]: gg for gg in game_files._dolphin_assets(self.home, "wii", "PunchOut", self._size)}
        cs = groups["console-save"]
        self.assertTrue(cs["present"])
        self.assertTrue(next(iter(f["rel"] for f in cs["files"])).endswith("Wii/title/00010000/52375050/data"))
        self.assertTrue(emu_map.rel_allowed(cs["files"][0]["rel"]))

    def test_cemu_graphic_packs_by_titleid(self):
        tid = "000500001014b800"
        base = self.home / ".local/share/Cemu/graphicPacks/downloadedGraphicPacks"
        gp = base / "NSMBU_60FPS"
        gp.mkdir(parents=True)
        (gp / "rules.txt").write_text(f"titleIds = {tid.upper()},0005000010132400\nname = 60FPS\n")
        (gp / "patch.asm").write_bytes(b"PATCH")
        other = base / "Other"
        other.mkdir(parents=True)
        (other / "rules.txt").write_text("titleIds = 0005000010999900\nname = x\n")   # different title
        with mock.patch("lib.madsrv.cemu_games._library",
                        lambda: {tid: {"name": "N", "stem": "NSMBU", "path": ""}}):
            groups = {gg["key"]: gg for gg in game_files._emucfg_game_assets("wiiu", "NSMBU", None, self._size)}
        gpk = groups["graphicpacks"]
        self.assertTrue(gpk["present"])
        rels = {f["rel"] for f in gpk["files"]}
        self.assertEqual(len(rels), 1)                          # only the pack that targets this title-id
        self.assertTrue(next(iter(rels)).endswith("NSMBU_60FPS"))
        self.assertTrue(emu_map.rel_allowed(next(iter(rels))))


class MediaFor(unittest.TestCase):
    """es_gamelist.media_for: exact <stem>.<ext> wins; the fake-extension <stem>.<romext>.<ext> is only a
    fallback (final-review fix - it must not shadow the canonical file or bind a longer-stemmed sibling)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="mf-"))
        self._ph = mock.patch.object(Path, "home", staticmethod(lambda: self.home))
        self._ph.start()
        from lib import esde_settings, es_gamelist
        self._pm = mock.patch.object(esde_settings, "media_root", lambda: self.home / "media")
        self._pm.start()
        # media_for reads the game's real rom extension (for the fake-extension media fallback) via rom_paths,
        # so mock it hermetically - otherwise the test would only pass by reading the real device gamelist.
        self._pr = mock.patch.object(es_gamelist, "rom_paths", lambda s: {
            "nes": {"game": "./Game.nes", "sonic": "./Sonic.nes"},
            "lindbergh": {"rambo": "./rambo.lindbergh"},
        }.get(s, {}))
        self._pr.start()

    def tearDown(self):
        self._pr.stop()
        self._pm.stop()
        self._ph.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _cover(self, system, name):
        d = self.home / "media" / system / "covers"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"IMG")

    def test_exact_wins_over_fake_extension_sibling(self):
        from lib import es_gamelist
        self._cover("nes", "Game.png")
        self._cover("nes", "Game.zip.png")
        self.assertTrue(es_gamelist.media_for("nes", "Game")["covers"].endswith("/Game.png"))

    def test_fake_extension_matched_as_fallback(self):
        from lib import es_gamelist
        self._cover("lindbergh", "rambo.lindbergh.jpg")     # no exact rambo.jpg -> relaxed fallback
        self.assertTrue(es_gamelist.media_for("lindbergh", "rambo")["covers"].endswith("/rambo.lindbergh.jpg"))

    def test_no_cross_game_bind_from_longer_stem(self):
        from lib import es_gamelist
        self._cover("nes", "Sonic.The.Hedgehog.png")        # belongs to a DIFFERENT game
        self.assertIsNone(es_gamelist.media_for("nes", "Sonic")["covers"])


if __name__ == "__main__":
    unittest.main()
