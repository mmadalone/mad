"""mugen_cmds - the MUGEN / Ikemen GO per-game config tree.

CI-safe: everything runs against a temp MUGEN_ROOT fixture (fake .mugen launchers +
config.ini), never ~/ROMs or a live engine. Guards the two fragile contracts:
  1. .mugen exec-line parsing -> (mode, folder), incl. the quoted-path shape that
     once made every game parse to None, and basename != folder.
  2. byte-preserving writes: only the one edited value line changes; comments,
     alignment and every other key survive; a one-time .bak is made.
"""
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil, mugen_cmds
from lib.madsrv.rpc import RpcError

# A minimal but realistic config.ini covering every section the descriptor touches.
_CONFIG = """\
; Ikemen GO config
[Options]
Difficulty            = 5
Life                  = 100
Time                  = 99
Match.Wins            = 2
Credits               = 10
AutoGuard             = 0
QuickContinue         = 0
[Config]
Motif             = data/ikemen1/system.def
Players           = 4
ZoomActive        = 1
FirstRun          = 0
[Video]
RenderMode              = Vulkan 1.3
GameWidth               = 1280
GameHeight              = 720
Fullscreen              = 1
Borderless              = 0
VSync                   = 1
MSAA                    = 0
Framerate               = 60
KeepAspect              = 1
[Sound]
SampleRate           = 44100
StereoEffects        = 1
MasterVolume         = 100
WavVolume            = 80
BGMVolume            = 75
AudioDucking         = 0
"""

_LAUNCHER = '#!/usr/bin/env bash\nexec "$HOME/Emulation/tools/launchers/mugen.sh" {mode} "{target}"\n'


class MugenCmds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig_root = mugen_cmds.MUGEN_ROOT
        mugen_cmds.MUGEN_ROOT = self.root
        # ikemen game whose .mugen basename != its config folder (AvengersVsX-Men -> AvX)
        self._mk_launcher("AvengersVsX-Men", "ikemen", "AvX")
        self._mk_config("AvX")
        # native game: target is <folder>/<binary>
        self._mk_launcher("tmntxjlTurboRB", "native", "tmntxjlTurboRB/TMNTXJLT_Linux")
        self._mk_config("tmntxjlTurboRB")
        # a launcher with no config.ini yet (JSON-only / never launched)
        self._mk_launcher("NoCfg", "ikemen", "NoCfg")
        (self.root / "NoCfg").mkdir()

    def tearDown(self):
        mugen_cmds.MUGEN_ROOT = self._orig_root
        self.tmp.cleanup()

    def _mk_launcher(self, name, mode, target):
        (self.root / f"{name}.mugen").write_text(_LAUNCHER.format(mode=mode, target=target))

    def _mk_config(self, folder):
        d = self.root / folder / "save"
        d.mkdir(parents=True)
        (d / "config.ini").write_text(_CONFIG)

    # -- parsing / path resolution --------------------------------------------
    def test_parse_ikemen_quoted_path(self):
        mode, target = mugen_cmds._parse_launcher(self.root / "AvengersVsX-Men.mugen")
        self.assertEqual((mode, target), ("ikemen", "AvX"))

    def test_parse_native_folder_from_binary_path(self):
        ini = mugen_cmds._config_ini("tmntxjlTurboRB")
        self.assertEqual(ini, (self.root / "tmntxjlTurboRB" / "save" / "config.ini").resolve())

    def test_config_ini_basename_differs_from_folder(self):
        ini = mugen_cmds._config_ini("AvengersVsX-Men")
        self.assertEqual(ini.parent.parent.name, "AvX")
        self.assertTrue(ini.is_file())

    def test_traversal_guard(self):
        for bad in ("../etc/passwd", "a/b", ".."):
            with self.assertRaises(RpcError):
                mugen_cmds._config_ini(bad)

    def test_unknown_titleid_raises(self):
        with self.assertRaises(RpcError):
            mugen_cmds._config_ini("DoesNotExist")

    # -- game list -------------------------------------------------------------
    def test_games_lists_all_and_flags_configless(self):
        games = {g["titleid"]: g for g in mugen_cmds._games()}
        self.assertEqual(set(games), {"AvengersVsX-Men", "tmntxjlTurboRB", "NoCfg"})
        self.assertIn("Per-game config", games["AvengersVsX-Men"]["summary"])
        self.assertIn("Launch the game once", games["NoCfg"]["summary"])
        # stem == titleid so the media browser resolves art by the .mugen basename
        self.assertEqual(games["AvengersVsX-Men"]["stem"], "AvengersVsX-Men")

    # -- read: every descriptor item resolves against a real config -----------
    def test_descriptor_keys_all_readable(self):
        ini = mugen_cmds._config_ini("AvengersVsX-Men")
        res = cfgutil.do_get(mugen_cmds.GROUPS, ini, cfgutil.ini_read,
                             proc="mugen", label="M.U.G.E.N")
        got = {s["key"] for grp in res["groups"] for s in grp["settings"]}
        want = {it["key"] for grp in mugen_cmds.GROUPS for it in grp["items"]}
        self.assertEqual(got, want, "a descriptor item points at a missing section/key")

    def test_dotted_key_maps_to_ini_name(self):
        ini = mugen_cmds._config_ini("AvengersVsX-Men")
        res = cfgutil.do_get(mugen_cmds.GROUPS, ini, cfgutil.ini_read,
                             proc="mugen", label="M.U.G.E.N")
        mw = next(s for grp in res["groups"] for s in grp["settings"] if s["key"] == "MatchWins")
        self.assertEqual(mw["value"], 2)   # read from [Options] Match.Wins = 2

    # -- write: byte-preserving, one line, one .bak ---------------------------
    def test_write_is_byte_preserving(self):
        ini = mugen_cmds._config_ini("AvengersVsX-Men")
        before = ini.read_text()
        cfgutil.do_set(mugen_cmds.GROUPS, {"key": "Difficulty", "value": 7}, ini,
                       cfgutil.ini_read, cfgutil.ini_replace, proc="mugen", label="M.U.G.E.N")
        after = ini.read_text()
        diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(diff, [("Difficulty            = 5", "Difficulty            = 7")])
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        self.assertTrue(ini.with_suffix(".ini.bak").is_file())

    def test_enum_index_writes_stored_token(self):
        ini = mugen_cmds._config_ini("AvengersVsX-Men")
        # MSAA options_stored = ["0","2","4","8","16","32"]; index 2 -> "4"
        cfgutil.do_set(mugen_cmds.GROUPS, {"key": "MSAA", "value": 2}, ini,
                       cfgutil.ini_read, cfgutil.ini_replace, proc="mugen", label="M.U.G.E.N")
        self.assertEqual(cfgutil.ini_read(ini.read_text(), "Video", "MSAA"), "4")


if __name__ == "__main__":
    unittest.main()
