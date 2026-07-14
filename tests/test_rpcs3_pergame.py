"""Tests for rpcs3_games (PS3 game list / serial resolver) + rpcs3_pergame_cmds
(per-game settings with key-presence inherit over custom_configs/config_<SERIAL>.yml).

Hermetic: game list points at a fixture games.yml; the per-game editor points at a temp
custom_configs dir. Proves the inherit model (present=override, "Inherit global" deletes the
key) is byte-preserving and never clobbers an existing full-dump custom config, and that the
flattened page's row ids are section-prefixed (Video/Renderer vs Audio/Renderer)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil, rpc, rpcs3_games
from lib.madsrv import rpcs3_pergame_cmds as PG
from lib.madsrv.rpc import RpcError

_S = "BLAA12345"               # a valid-shaped serial with no file
_REN = "Video::Renderer"       # section-prefixed row ids (the C++ round-trip keys)
_WCB = "Video::Write Color Buffers"
_VOL = "Audio::Master Volume"
_SPU = "Core::SPU Block Size"


class Games(unittest.TestCase):
    def setUp(self):
        import lib.es_gamelist as eg
        self.d = Path(tempfile.mkdtemp())
        self.rom = self.d / "ps3"
        self.rom.mkdir()
        self.y = self.d / "games.yml"
        self._rd, self._gy, self._titles = rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML, eg.titles
        rpcs3_games._ps3_rom_dir = lambda: self.rom
        rpcs3_games._GAMES_YML = self.y
        eg.titles = lambda system: {}          # hermetic: no gamelist -> name falls back to the stem

    def tearDown(self):
        import lib.es_gamelist as eg
        rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML, eg.titles = self._rd, self._gy, self._titles
        shutil.rmtree(self.d, ignore_errors=True)

    def _desktop(self, name, disc):
        (self.rom / f"{name}.desktop").write_text(
            f'[Desktop Entry]\nExec=/apps/rpcs3.AppImage --no-gui "{disc}"\n', encoding="utf-8")

    def test_games_use_esde_desktop_stem_for_media(self):
        # The stem MUST be the .desktop filename (ES-DE files media under it), NOT the disc name.
        self.y.write_text("BCES00002: /discs/Genji (Europe) (En,Ja).iso\n"
                          "BLES01291: /discs/Spider [BLES01291]/\n", encoding="utf-8")
        self._desktop("Genji - Days of the Blade", "/discs/Genji (Europe) (En,Ja).iso")
        self._desktop("Spider-Man - Edge of Time",
                      "/discs/Spider [BLES01291]/PS3_GAME/USRDIR/EBOOT.BIN")
        by = {x["key"]: x for x in rpcs3_games.games()}
        self.assertEqual(by["BCES00002"]["stem"], "Genji - Days of the Blade")
        self.assertEqual(by["BLES01291"]["stem"], "Spider-Man - Edge of Time")
        self.assertEqual(by["BCES00002"]["name"], "Genji - Days of the Blade")   # no gamelist -> stem

    def test_unregistered_desktop_dropped(self):
        self.y.write_text("BCES00002: /discs/Genji.iso\n", encoding="utf-8")
        self._desktop("Genji", "/discs/Genji.iso")
        self._desktop("Unregistered", "/discs/Nope.iso")         # no games.yml serial -> dropped
        self.assertEqual({x["key"] for x in rpcs3_games.games()}, {"BCES00002"})

    def test_empty_when_no_desktops(self):
        self.assertEqual(rpcs3_games.games(), [])                # empty ps3 rom dir

    def test_stem_of(self):
        self.assertEqual(rpcs3_games.stem_of("/a/Genji (Europe).iso"), "Genji (Europe)")
        self.assertEqual(rpcs3_games.stem_of("/a/Spider [BLES01291]/"), "Spider [BLES01291]")
        self.assertEqual(rpcs3_games.stem_of("/a/Some.Game [BLES00590]/"), "Some.Game [BLES00590]")

    def test_non_utf8_games_yml_no_crash(self):
        self.y.write_bytes(b"BCES00002: /discs/caf\xe9.iso\n")   # errors="replace" -> no crash
        self.assertIsNone(rpcs3_games.path_to_serial("/discs/other.iso"))

    def test_serial_rejects_trailing_newline(self):
        self.assertTrue(rpcs3_games.is_serial("BLES00590"))
        self.assertFalse(rpcs3_games.is_serial("BLES00590\n"))    # \Z, not $

    def test_name_from_gamelist_preferred(self):
        import lib.es_gamelist as eg
        eg.titles = lambda system: {"genji - days of the blade": "Genji: Days of the Blade"}
        self.y.write_text("BCES00002: /discs/Genji.iso\n", encoding="utf-8")
        self._desktop("Genji - Days of the Blade", "/discs/Genji.iso")
        g = {x["key"]: x for x in rpcs3_games.games()}
        self.assertEqual(g["BCES00002"]["name"], "Genji: Days of the Blade")   # gamelist name preferred


class Pergame(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._cc, self._run = PG._CC_DIR, PG._running
        PG._CC_DIR = self.d
        self.running = False
        PG._running = lambda: self.running
        PG._buf.update({"serial": None, "text": None, "disk": None, "dirty": False, "edits": []})
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda name: None

    def tearDown(self):
        PG._CC_DIR, PG._running = self._cc, self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _path(self, s=_S):
        return self.d / f"config_{s}.yml"

    def _rows(self, s=_S):
        return {r["key"]: r for grp in PG._pergame_get(s)["groups"] for r in grp["settings"]}

    def _fresh_buf(self):
        PG._buf.update({"serial": None, "text": None, "disk": None, "dirty": False, "edits": []})

    def test_fresh_game_all_inherited(self):
        pay = PG._pergame_get(_S)
        self.assertTrue(pay["exists"])          # MUST be true, else C++ hides all controls
        self.assertTrue(pay["buffered"])
        rows = {r["key"]: r for g in pay["groups"] for r in g["settings"]}
        self.assertEqual(rows[_REN]["options"][0], "Inherit global")
        self.assertEqual(rows[_REN]["value"], 0)
        self.assertEqual(rows[_WCB]["options"], ["Inherit global", "Off", "On"])
        self.assertEqual(rows[_WCB]["value"], 0)
        self.assertTrue(rows[_VOL]["inherit"])
        self.assertTrue(rows[_VOL]["inherited"])

    def test_row_ids_are_section_prefixed_and_unique(self):
        rows = self._rows()
        self.assertIn(_REN, rows)               # Video/Renderer
        self.assertIn("Audio::Renderer", rows)  # Audio/Renderer — distinct id, no collision
        ids = [r["key"] for g in PG._pergame_get(_S)["groups"] for r in g["settings"]]
        self.assertEqual(len(ids), len(set(ids)))   # every row id unique

    def test_nested_vulkan_keys_hidden_pergame(self):
        rows = self._rows()
        self.assertNotIn("Video::Asynchronous Texture Streaming", rows)   # global-only
        self.assertNotIn("Video::Asynchronous Queue Scheduler", rows)

    def test_override_creates_minimal_file(self):
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 2})   # OpenGL
        PG._pergame_set({"titleid": _S, "key": _WCB, "value": 2})   # On
        self.assertEqual(PG._pergame_save(_S), {"saved": True})
        txt = self._path().read_text()
        self.assertEqual(cfgutil.yaml_read(txt, "Video", "Renderer"), "OpenGL")
        self.assertEqual(cfgutil.yaml_read(txt, "Video", "Write Color Buffers"), "true")
        self.assertEqual(self._rows()[_REN]["value"], 2)            # re-opens as override

    def test_inherit_deletes_key_and_drops_empty_section(self):
        self._path().write_text("Video:\n  Renderer: OpenGL\n  Write Color Buffers: true\n")
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 0})   # Inherit global
        PG._pergame_save(_S)
        txt = self._path().read_text()
        self.assertIsNone(cfgutil.yaml_read(txt, "Video", "Renderer"))
        self.assertEqual(cfgutil.yaml_read(txt, "Video", "Write Color Buffers"), "true")
        self._fresh_buf()
        PG._pergame_set({"titleid": _S, "key": _WCB, "value": 0})
        PG._pergame_save(_S)
        self.assertEqual(self._path().read_text().strip(), "")     # section dropped when last cleared

    def test_int_override_and_inherit(self):
        PG._pergame_set({"titleid": _S, "key": _VOL, "value": 80})
        PG._pergame_save(_S)
        self.assertEqual(cfgutil.yaml_read(self._path().read_text(), "Audio", "Master Volume"), "80")
        self._fresh_buf()
        PG._pergame_set({"titleid": _S, "key": _VOL, "value": "inherit"})
        PG._pergame_save(_S)
        self.assertEqual(self._path().read_text().strip(), "")

    def test_no_empty_file_created_when_net_inherit(self):
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 2})
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 0})   # back to inherit
        self.assertEqual(PG._pergame_save(_S), {"saved": False})
        self.assertFalse(self._path().exists())

    def test_save_replays_onto_fresh_read(self):
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 2})
        self._path().write_text("Audio:\n  Master Volume: 50\n")   # foreign write while staged
        PG._pergame_save(_S)
        txt = self._path().read_text()
        self.assertEqual(cfgutil.yaml_read(txt, "Video", "Renderer"), "OpenGL")   # our edit
        self.assertEqual(cfgutil.yaml_read(txt, "Audio", "Master Volume"), "50")  # foreign preserved

    def test_full_dump_edit_no_clobber(self):
        full = ("Core:\n  PPU Decoder: Recompiler (LLVM)\n  SPU Block Size: Safe\n"
                "Video:\n  Renderer: Vulkan\n")
        self._path().write_text(full)
        PG._pergame_set({"titleid": _S, "key": _SPU, "value": 3})   # Giga
        PG._pergame_save(_S)
        txt = self._path().read_text()
        self.assertEqual(cfgutil.yaml_read(txt, "Core", "SPU Block Size"), "Giga")
        self.assertEqual(cfgutil.yaml_read(txt, "Core", "PPU Decoder"), "Recompiler (LLVM)")
        self.assertEqual(cfgutil.yaml_read(txt, "Video", "Renderer"), "Vulkan")
        self.assertEqual(len(txt.splitlines()), len(full.splitlines()))

    def test_cancel_discards(self):
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 2})
        PG._pergame_cancel(_S)
        self.assertEqual(self._rows()[_REN]["value"], 0)
        self.assertFalse(self._path().exists())

    def test_has_overrides_helper(self):
        self.assertFalse(PG._has_overrides(None))
        self.assertFalse(PG._has_overrides(""))
        self.assertFalse(PG._has_overrides("Video:\n"))              # header only
        self.assertTrue(PG._has_overrides("Video:\n  Renderer: OpenGL\n"))

    def test_bad_serial_rejected(self):
        with self.assertRaises(RpcError):
            PG._pergame_get("short")
        with self.assertRaises(RpcError):
            PG._pergame_set({"titleid": "../etc/passwd", "key": _REN, "value": 1})

    def test_running_guard(self):
        self.running = True
        with self.assertRaises(RpcError):
            PG._pergame_set({"titleid": _S, "key": _REN, "value": 1})
        with self.assertRaises(RpcError):
            PG._pergame_save(_S)

    def test_games_picker_marks_override(self):
        rd, gy = rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML
        rom = self.d / "ps3"
        rom.mkdir()
        (self.d / "games.yml").write_text("BLAA12345: /discs/TestGame.iso\n", encoding="utf-8")
        (rom / "Test Game.desktop").write_text(
            '[Desktop Entry]\nExec=/apps/rpcs3.AppImage --no-gui "/discs/TestGame.iso"\n', encoding="utf-8")
        rpcs3_games._ps3_rom_dir = lambda: rom
        rpcs3_games._GAMES_YML = self.d / "games.yml"
        try:
            self._path().write_text("Video:\n  Renderer: OpenGL\n")
            out = PG._pergame_games()
            self.assertEqual(out["system"], "ps3")
            g = {x["titleid"]: x for x in out["games"]}
            self.assertTrue(g["BLAA12345"]["override"])
            self.assertEqual(g["BLAA12345"]["name"], "Test Game")     # from the .desktop stem
            self.assertEqual(g["BLAA12345"]["summary"], "Custom settings")
        finally:
            rpcs3_games._ps3_rom_dir, rpcs3_games._GAMES_YML = rd, gy

    def test_create_has_no_trailing_newline(self):
        # Live RPCS3 configs end with NO trailing newline; the create path must match.
        PG._pergame_set({"titleid": _S, "key": _REN, "value": 2})   # OpenGL
        PG._pergame_save(_S)
        self.assertEqual(self._path().read_text(), "Video:\n  Renderer: OpenGL")
        self._fresh_buf()
        PG._pergame_set({"titleid": _S, "key": _WCB, "value": 2})   # On (2nd key, same section)
        PG._pergame_save(_S)
        self.assertEqual(self._path().read_text(),
                         "Video:\n  Renderer: OpenGL\n  Write Color Buffers: true")

    def test_full_dump_byte_identity(self):
        full = ("Core:\n  PPU Decoder: Recompiler (LLVM)\n  SPU Block Size: Safe\n"
                "Video:\n  Renderer: Vulkan\n  Resolution: 1280x720")   # no trailing newline
        self._path().write_text(full)
        PG._pergame_set({"titleid": _S, "key": _SPU, "value": 3})   # Safe -> Giga
        PG._pergame_save(_S)
        self.assertEqual(self._path().read_text(),
                         full.replace("SPU Block Size: Safe", "SPU Block Size: Giga"))

    def test_audio_renderer_routes_independently(self):
        # The section::key id fix: editing Audio/Renderer must NOT touch Video/Renderer.
        PG._pergame_set({"titleid": _S, "key": "Audio::Renderer", "value": 2})   # FAudio
        PG._pergame_save(_S)
        txt = self._path().read_text()
        self.assertEqual(cfgutil.yaml_read(txt, "Audio", "Renderer"), "FAudio")
        self.assertIsNone(cfgutil.yaml_read(txt, "Video", "Renderer"))

    def test_bak_created_on_first_save_of_existing(self):
        self._path().write_text("Core:\n  SPU Block Size: Safe\n")
        PG._pergame_set({"titleid": _S, "key": _SPU, "value": 3})
        PG._pergame_save(_S)
        self.assertTrue((self.d / f"config_{_S}.yml.bak").exists())   # rule #5 recovery snapshot

    def test_offlist_enum_slot_is_noop(self):
        # An off-list enum value (valid RPCS3, not one of our discrete options) -> a
        # "(current: N)" slot that must PRESERVE the value and never crash.
        self._path().write_text("Video:\n  Anisotropic Filter Override: 1\n")
        r = self._rows()["Video::Anisotropic Filter Override"]
        self.assertEqual(r["options"][-1], "(current: 1)")
        PG._pergame_set({"titleid": _S, "key": "Video::Anisotropic Filter Override",
                         "value": len(r["options"]) - 1})
        PG._pergame_save(_S)
        self.assertEqual(cfgutil.yaml_read(self._path().read_text(),
                                           "Video", "Anisotropic Filter Override"), "1")

    def test_cancel_and_save_reject_bad_serial(self):
        with self.assertRaises(RpcError):
            PG._pergame_cancel("../config")
        with self.assertRaises(RpcError):
            PG._pergame_save("../config")

    def test_rpc_methods_registered(self):
        for verb in ("get", "set", "save", "cancel", "games"):
            self.assertIn(f"rpcs3pg.{verb}", rpc._METHODS)


if __name__ == "__main__":
    unittest.main()
