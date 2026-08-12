"""Bash tests for the launch-info hook wiring (game-start 02 producer + the
04/quit-combo-watcher/sinden consumers). The hooks are launch-critical and had
zero direct coverage; these run the REPO hook scripts with a fake $HOME (stub
controller-router.py, stub quit-combo-watcher.py, stub sinden-start.sh) and a
private $XDG_RUNTIME_DIR, asserting: the cache fast path makes ZERO router
calls, the legacy fallback fires on a missing/mismatched cache with the exact
pre-batching call sequence, QUIT precedence, the Lindbergh QUIT override and
collection combo re-key survive, and the producer installs atomically and
omits the lightgun key when the Sinden consumer is absent.

NOTE the hooks really execute `pkill -f quit-combo-watcher.py`: tests that
reach the spawn are skipped when a real watcher is running (mid-game).

Run:  python3 -m unittest tests.test_launch_info_hooks -v
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks" / "game-start"

ROM_ESC = r"/tmp/roms/nes/Duck\ Hunt\ \(World\).zip"
ROM_PLAIN = "/tmp/roms/nes/Duck Hunt (World).zip"

_STUB_ROUTER = """#!/usr/bin/env bash
echo "$*" >> "$HOME/router-calls.log"
case "$1" in
  launch-info)  printf '%s' "${STUB_LAUNCH_INFO:-}"; exit "${STUB_LI_RC:-0}" ;;
  quit-combo-collection) printf '%s\\n' "${STUB_COL:-}"; [ -n "${STUB_COL:-}" ] ;;
  quit-cmd)     printf '%s\\n' "${STUB_QUIT:-}" ;;
  lightgun-quit-cmd) printf '%s\\n' "${STUB_LGQUIT:-}" ;;
  is-retroarch) exit "${STUB_ISRA_RC:-1}" ;;
  lightgun-rom) exit "${STUB_LGROM_RC:-1}" ;;
  setup)        exit 0 ;;
esac
exit 0
"""

_STUB_WATCHER = """import json, os, sys
with open(os.path.expanduser("~/watcher-args.json"), "w") as f:
    json.dump(sys.argv[1:], f)
"""


def _real_watcher_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "quit-combo-watcher.py"],
                       capture_output=True, text=True)
    return r.returncode == 0


class HookHarness(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name) / "home"
        self.run_dir = Path(self._td.name) / "run"
        rt = self.home / "Emulation" / "tools" / "launchers"
        rt.mkdir(parents=True)
        self.run_dir.mkdir()
        (self.home / "Emulation" / "storage" / "sinden" / "logs").mkdir(parents=True)
        router = rt / "controller-router.py"
        router.write_text(_STUB_ROUTER)
        router.chmod(0o755)
        (rt / "quit-combo-watcher.py").write_text(_STUB_WATCHER)
        start = rt / "sinden-start.sh"
        start.write_text("#!/usr/bin/env bash\ntouch \"$HOME/sinden-started\"\n")
        start.chmod(0o755)

    def env(self, **stub) -> dict:
        e = dict(os.environ)
        e.update(HOME=str(self.home), XDG_RUNTIME_DIR=str(self.run_dir))
        # The legacy handheld probe in quit-combo-watcher.sh imports the REAL
        # repo lib (python3 -c keeps the cwd on sys.path), whose policy enables
        # handheld detection — pin the dock state so the assertions don't depend
        # on whether this rig/CI runner has a DRM display connected.
        e["MAD_FORCE_CONTEXT"] = "docked"
        for k, v in stub.items():
            e[k] = v
        return e

    def hook(self, name: str, argv: list[str], **stub) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(HOOKS / name)] + argv,
                              capture_output=True, text=True, timeout=60,
                              env=self.env(**stub))

    def router_calls(self) -> list[str]:
        f = self.home / "router-calls.log"
        return f.read_text().splitlines() if f.exists() else []

    def write_cache(self, **over) -> None:
        kv = {"MAD_LI_QUIT_COMBO_COLLECTION": "", "MAD_LI_QUIT_CMD": "",
              "MAD_LI_LIGHTGUN_QUIT_CMD": "", "MAD_LI_IS_RETROARCH": "0",
              "MAD_LI_LIGHTGUN_ROM": "0", "MAD_LI_HANDHELD": "0",
              "MAD_LI_SETUP_NEEDED": "1", "MAD_LI_ROM": ROM_PLAIN,
              "MAD_LI_SYSTEM": "nes"}
        kv.update(over)
        text = "".join(f"{k}={shlex.quote(v)}\n" for k, v in kv.items() if v is not None)
        (self.run_dir / "mad-launch-info.env").write_text(text)

    def watcher_args(self, timeout: float = 5.0) -> list[str] | None:
        f = self.home / "watcher-args.json"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if f.exists():
                return json.loads(f.read_text())
            time.sleep(0.05)
        return None


class Producer02(HookHarness):
    PAYLOAD = ("MAD_LI_QUIT_CMD='pkill -TERM -f foo'\n"
               f"MAD_LI_ROM={shlex.quote(ROM_PLAIN)}\nMAD_LI_SYSTEM=nes\n")

    def test_installs_the_cache_atomically(self):
        r = self.hook("02-launch-info.sh", [ROM_ESC, "n", "nes", "f"],
                      STUB_LAUNCH_INFO=self.PAYLOAD)
        self.assertEqual(r.returncode, 0)
        out = self.run_dir / "mad-launch-info.env"
        self.assertEqual(out.read_text(), self.PAYLOAD)
        self.assertEqual(list(self.run_dir.glob("*.tmp.*")), [])
        log = (self.home / "Emulation/storage/sinden/logs/es-de-hooks.log").read_text()
        self.assertIn("launch-info:", log)
        self.assertIn("ms ->", log)

    def test_failure_removes_the_cache_and_still_exits_zero(self):
        (self.run_dir / "mad-launch-info.env").write_text("stale")
        r = self.hook("02-launch-info.sh", [ROM_ESC, "n", "nes", "f"],
                      STUB_LAUNCH_INFO="partial", STUB_LI_RC="3")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.run_dir / "mad-launch-info.env").exists())
        self.assertEqual(list(self.run_dir.glob("*.tmp.*")), [])

    def test_no_lightgun_flag_follows_the_sinden_consumer(self):
        self.hook("02-launch-info.sh", [ROM_ESC, "n", "nes", "f"],
                  STUB_LAUNCH_INFO=self.PAYLOAD)
        self.assertIn("--no-lightgun", self.router_calls()[-1])
        sindendir = self.home / "ES-DE" / "scripts" / "game-start"
        sindendir.mkdir(parents=True)
        (sindendir / "sinden.sh").write_text("#!/bin/sh\n")
        self.hook("02-launch-info.sh", [ROM_ESC, "n", "nes", "f"],
                  STUB_LAUNCH_INFO=self.PAYLOAD)
        self.assertNotIn("--no-lightgun", self.router_calls()[-1])


class QuitComboConsumer(HookHarness):
    def test_cache_hit_makes_zero_router_calls_and_early_exits(self):
        self.write_cache()          # empty QUIT everywhere, IS_RETROARCH=0
        r = self.hook("quit-combo-watcher.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.router_calls(), [])
        self.assertIsNone(self.watcher_args(timeout=0.3))

    def test_cache_hit_spawns_watcher_with_cached_quit(self):
        if _real_watcher_running():
            self.skipTest("real quit-combo-watcher active (mid-game); hook would pkill it")
        self.write_cache(MAD_LI_QUIT_CMD="pkill -TERM -f foo", MAD_LI_HANDHELD="1",
                         MAD_LI_SYSTEM="ps2")
        r = self.hook("quit-combo-watcher.sh", [ROM_ESC, "n", "ps2", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.router_calls(), [])
        args = self.watcher_args()
        self.assertIsNotNone(args, "watcher was not spawned")
        self.assertEqual(args, ["--system", "ps2", "--handheld",
                                "--quit-cmd", "pkill -TERM -f foo"])

    def test_cache_hit_ra_collection_killer_and_combo_rekey(self):
        if _real_watcher_running():
            self.skipTest("real quit-combo-watcher active (mid-game); hook would pkill it")
        self.write_cache(MAD_LI_QUIT_COMBO_COLLECTION="pew", MAD_LI_IS_RETROARCH="1")
        r = self.hook("quit-combo-watcher.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        args = self.watcher_args()
        self.assertEqual(args, ["--system", "collection-pew",
                                "--quit-cmd", "pkill -TERM -f retroarch"])

    def test_lindbergh_quit_override_survives_the_cache(self):
        if _real_watcher_running():
            self.skipTest("real quit-combo-watcher active (mid-game); hook would pkill it")
        rom = r"/tmp/roms/lindbergh/hotd4\ dir/game.lindbergh"
        self.write_cache(MAD_LI_QUIT_CMD="pkill -TERM -f lindbergh",
                         MAD_LI_SYSTEM="lindbergh",
                         MAD_LI_ROM=rom.replace("\\", ""))
        r = self.hook("quit-combo-watcher.sh",
                      [rom, "n", "lindbergh", "f"])
        self.assertEqual(r.returncode, 0)
        args = self.watcher_args()
        self.assertIsNotNone(args)
        self.assertEqual(args[1], "lindbergh-game")
        quit_cmd = args[args.index("--quit-cmd") + 1]
        self.assertIn("readlink /proc/$p/exe", quit_cmd)
        self.assertIn("pkill -KILL -f lindbergh", quit_cmd)

    def test_missing_cache_falls_back_to_legacy_call_sequence(self):
        if _real_watcher_running():
            self.skipTest("real quit-combo-watcher active (mid-game); hook would pkill it")
        r = self.hook("quit-combo-watcher.sh", [ROM_ESC, "n", "nes", "f"],
                      STUB_COL="pew", STUB_ISRA_RC="0")
        self.assertEqual(r.returncode, 0)
        calls = self.router_calls()
        self.assertEqual([c.split()[0] for c in calls],
                         ["quit-combo-collection", "quit-cmd",
                          "lightgun-quit-cmd", "is-retroarch"])
        # the escaped ROM is forwarded raw, exactly as before the batching
        self.assertEqual(calls[0], f"quit-combo-collection {ROM_ESC}")
        args = self.watcher_args()
        self.assertEqual(args, ["--system", "collection-pew",
                                "--quit-cmd", "pkill -TERM -f retroarch"])

    def test_mismatched_cache_is_ignored(self):
        if _real_watcher_running():
            self.skipTest("real quit-combo-watcher active (mid-game); hook would pkill it")
        self.write_cache(MAD_LI_QUIT_CMD="pkill -TERM -f WRONG",
                         MAD_LI_ROM="/some/other/rom.zip")
        self.hook("quit-combo-watcher.sh", [ROM_ESC, "n", "nes", "f"],
                  STUB_QUIT="pkill -TERM -f right")
        self.assertTrue(self.router_calls())
        args = self.watcher_args()
        self.assertEqual(args[args.index("--quit-cmd") + 1], "pkill -TERM -f right")


class SetupGateConsumer(HookHarness):
    def test_cached_skip_calls_nothing(self):
        self.write_cache(MAD_LI_SETUP_NEEDED="0")
        r = self.hook("04-controller-router-setup.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.router_calls(), [])

    def test_cached_needed_runs_setup(self):
        self.write_cache(MAD_LI_SETUP_NEEDED="1")
        r = self.hook("04-controller-router-setup.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual([c.split()[0] for c in self.router_calls()], ["setup"])

    def test_missing_cache_fails_open_to_setup(self):
        # no cache: the legacy probe DOES import the real repo lib (cwd is on
        # python3 -c's sys.path), but every es_systems data path is $HOME-anchored
        # and the fake HOME has no ES-DE tree -> empty command -> legacy
        # fail-open behavior: run setup
        r = self.hook("04-controller-router-setup.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual([c.split()[0] for c in self.router_calls()], ["setup"])

    def test_missing_cache_legacy_wrap_skip_still_skips(self):
        # the fallback's wrap-only skip must survive verbatim: seed a custom
        # es_systems.xml in the fake HOME whose nes command is wrap-wrapped and
        # assert the probe skips setup with zero router calls
        cs = self.home / "ES-DE" / "custom_systems"
        cs.mkdir(parents=True)
        (cs / "es_systems.xml").write_text(
            "<systemList><system><name>nes</name>"
            "<command label=\"Nestopia UE\">X controller-router-wrap.sh nes %ROM%"
            "</command></system></systemList>")
        r = self.hook("04-controller-router-setup.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.router_calls(), [])


class SindenConsumer(HookHarness):
    def test_cached_lightgun_starts_driver_and_marks(self):
        self.write_cache(MAD_LI_LIGHTGUN_ROM="1")
        r = self.hook("sinden.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.router_calls(), [])
        self.assertTrue((self.home / "sinden-started").exists())
        self.assertTrue((self.home / "Emulation/storage/sinden/.esde-hook-started-driver").exists())

    def test_cached_non_lightgun_skips_driver(self):
        self.write_cache(MAD_LI_LIGHTGUN_ROM="0")
        r = self.hook("sinden.sh", [ROM_ESC, "n", "nes", "f"])
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.home / "sinden-started").exists())

    def test_omitted_key_falls_back_to_router(self):
        self.write_cache(MAD_LI_LIGHTGUN_ROM=None)     # --no-lightgun cache shape
        r = self.hook("sinden.sh", [ROM_ESC, "n", "nes", "f"], STUB_LGROM_RC="0")
        self.assertEqual(r.returncode, 0)
        calls = self.router_calls()
        self.assertEqual([c.split()[0] for c in calls], ["lightgun-rom"])
        # sinden.sh pre-strips the escapes before calling the router, as always
        self.assertEqual(calls[0], f"lightgun-rom {ROM_PLAIN}")
        self.assertTrue((self.home / "sinden-started").exists())


if __name__ == "__main__":
    unittest.main()
