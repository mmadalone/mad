"""controller-router lazy imports: the read-only query modes must answer without
loading the device/emulator machinery (evdev drags asyncio; the rpcs3 writer
drags yaml -> ssl; cemu drags xml.sax) — that import tax was 5-6x per game
launch (AUDIT-2026-08-12 PERFORMANCE-1/5). Each mode runs in a SUBPROCESS so
sys.modules is pristine (the suite itself imports evdev elsewhere), with
MAD_DATA_ROOT pointed at a temp dir so nothing touches the live storage tree.

Also pins down pin-node, which DOES need enumerate_devices: it must load the
heavy set itself (a NameError here would be silent — the mode has no shell
caller today).

Run:  python3 -m unittest tests.test_router_lazy_imports -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# One harness: run main() in-process (in the child), then report which heavy
# modules ended up in sys.modules. Exit code is passed through in the payload.
_HARNESS = r"""
import json, runpy, sys
argv = json.loads(sys.argv[1])
sys.argv = ["controller-router.py"] + argv
code = 0
try:
    runpy.run_path(%r, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
heavy = [m for m in ("evdev", "yaml", "xml.sax", "asyncio") if m in sys.modules]
print(json.dumps({"rc": code, "heavy": heavy}))
""" % str(ROOT / "controller-router.py")

QUERY_MODES = {
    "quit-cmd": ["quit-cmd", "nes"],
    "quit-systems": ["quit-systems"],
    "is-retroarch": ["is-retroarch", "nes"],
    "lightgun-quit-cmd": ["lightgun-quit-cmd", r"Duck\ Hunt\ \(World\).zip", "nes"],
    "quit-combo-collection": ["quit-combo-collection", r"Duck\ Hunt\ \(World\).zip"],
    "collection-of": ["collection-of", "Duck Hunt (World).zip"],
    # lightgun-rom on a NON-wii ROM: must answer from collections alone, without
    # the dolphin_wii_source fallback import (which pulls devices).
    "lightgun-rom": ["lightgun-rom", "/tmp/roms/ps2/Some Game.iso"],
    # the batched mode inherits the same diet on a non-wii ROM
    "launch-info": ["launch-info", r"/tmp/roms/ps2/Some\ Game.iso", "n", "ps2", "f"],
    "launch-info-no-lightgun": ["launch-info", "--no-lightgun",
                                "/tmp/roms/wii/Game.rvz", "n", "wii", "f"],
}


class QueryModesStayLight(unittest.TestCase):
    def _run(self, argv: list[str]) -> dict:
        with tempfile.TemporaryDirectory() as td:
            # HOME is redirected too: cleanup's joypad-driver restore writes the
            # RetroArch global cfg under Path.home()/.var, which MAD_DATA_ROOT
            # does not cover — the live rig's config must stay untouched.
            env = dict(os.environ, MAD_DATA_ROOT=td, HOME=td)
            r = subprocess.run(
                [sys.executable, "-c", _HARNESS, json.dumps(argv)],
                capture_output=True, text=True, cwd=ROOT, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, f"harness died: {r.stderr}")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_query_modes_never_load_heavy_modules(self):
        for label, argv in QUERY_MODES.items():
            with self.subTest(mode=label):
                out = self._run(argv)
                self.assertEqual(out["heavy"], [],
                                 f"{label} loaded {out['heavy']}")

    @unittest.skipUnless(os.path.isdir("/dev/input"),
                         "no /dev/input (containerized runner)")
    def test_pin_node_loads_heavy_and_exits_zero(self):
        # Regression: pin-node needs enumerate_devices, which is lazy now — a
        # missed _load_heavy() would NameError (nonzero rc), not just answer empty.
        # (enumerate_devices os.listdirs /dev/input unguarded, hence the skip.)
        out = self._run(["pin-node", "openbor", "1"])
        self.assertEqual(out["rc"], 0)

    def test_setup_paths_still_resolve_heavy_names(self):
        # cleanup on an unknown system is a no-op but exercises _load_heavy()
        # end-to-end in a real interpreter (clear_override must resolve).
        out = self._run(["cleanup", "rom", "name", "no-such-system", "full"])
        self.assertEqual(out["rc"], 0)
        self.assertIn("evdev", out["heavy"])


if __name__ == "__main__":
    unittest.main()
