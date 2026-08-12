"""launch-info differential contract: the batched mode must reproduce each legacy
query mode byte-for-byte (same underlying lib calls, same precedence), for BOTH
ROM escaping conventions (quit-combo-watcher.sh passes ES-DE's backslash-escaped
form; sinden.sh pre-strips), and its shlex-quoted output must round-trip values
containing spaces, quotes and $() through bash `source`/`eval`.

The underlying es_systems/es_collections/policy functions are monkeypatched at
the LIB modules (shared by both paths), so this tests the router GLUE — argument
passing, stripping, precedence, quoting, key ordering — not the rig's policy.

Run:  python3 -m unittest tests.test_launch_info_mode -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shlex
import unittest
from pathlib import Path
from unittest import mock

from lib import deck_state as _deck_state          # noqa: F401 (imported for patch targets)
from lib import es_collections as _colls
from lib import es_systems as _es
from lib import policy as _policy

ROOT = Path(__file__).resolve().parent.parent

ROM_PLAIN = "/tmp/roms/nes/Duck Hunt (World).zip"
ROM_ESCAPED = r"/tmp/roms/nes/Duck\ Hunt\ \(World\).zip"
NASTY_QUIT = """pkill -TERM -f 'a b'; sleep 1; echo "$(x)" | grep -v '"'"""


def _load_router():
    spec = importlib.util.spec_from_file_location("cr_launch_info", ROOT / "controller-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class LaunchInfoDifferential(unittest.TestCase):
    def setUp(self):
        self.cr = _load_router()
        self.pol = {"quit_combo": {"collection-pew": {"buttons": ["a"]}},
                    "collections": {"pew": {"require_sinden": True}},
                    "handheld": {"enabled": True}}
        self.seen_roms = []

        def combo_col(rom, qc):
            self.seen_roms.append(rom)
            return "pew" if "Duck Hunt" in rom else None

        patches = [
            mock.patch.object(self.cr, "load_policy", return_value=self.pol),
            mock.patch.object(_colls, "narrowest_combo_collection", side_effect=combo_col),
            mock.patch.object(_colls, "collection_for_rom",
                              side_effect=lambda rom: "pew" if "Duck Hunt" in rom else None),
            mock.patch.object(_es, "load_systems", return_value={"SENTINEL": []}),
            mock.patch.object(_es, "quit_cmd",
                              side_effect=lambda system, pol, systems=None:
                              NASTY_QUIT if system == "ps2" else ""),
            mock.patch.object(_es, "lightgun_ra_quit_cmd",
                              side_effect=lambda rom, pol, system, systems=None:
                              "pkill -TERM -f retroarch" if "Duck Hunt" in rom else ""),
            mock.patch.object(_es, "is_retroarch_system",
                              side_effect=lambda system, systems=None: system == "nes"),
            mock.patch.object(_es, "resolved_command",
                              side_effect=lambda system, stem, systems=None: {
                                  "nes": "X controller-router-wrap.sh nes ...",
                                  "ps2": "X mad-standalone-launch.py pcsx2 ...",
                                  "switch": "X mad-switch-launch.py eden ...",
                                  "gba": "%EMULATOR_RETROARCH% plain",
                              }.get(system, "")),
            mock.patch.object(_policy, "load_merged",
                              return_value={"handheld": {"enabled": True, "force": "handheld"}}),
            # a rig-level MAD_FORCE_CONTEXT would override the force token
            mock.patch.dict(os.environ, {"MAD_FORCE_CONTEXT": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cr.main(["controller-router.py"] + argv)
        return rc, buf.getvalue()

    def _launch_info(self, rom: str, system: str, extra: list[str] | None = None) -> dict:
        rc, out = self._run(["launch-info"] + (extra or []) + [rom, "name", system, "full"])
        self.assertEqual(rc, 0)
        kv = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            parsed = shlex.split(v)
            kv[k] = parsed[0] if parsed else ""
        return kv

    # -- differential agreement with each legacy mode --------------------------

    def test_agrees_with_legacy_modes_escaped_and_stripped(self):
        for rom in (ROM_ESCAPED, ROM_PLAIN):
            with self.subTest(rom=rom):
                li = self._launch_info(rom, "nes")
                rc, out = self._run(["quit-combo-collection", rom])
                self.assertEqual(li["MAD_LI_QUIT_COMBO_COLLECTION"], out.strip())
                self.assertEqual(rc, 0)
                _, out = self._run(["quit-cmd", "nes"])
                self.assertEqual(li["MAD_LI_QUIT_CMD"], out.rstrip("\n"))
                _, out = self._run(["lightgun-quit-cmd", rom, "nes"])
                self.assertEqual(li["MAD_LI_LIGHTGUN_QUIT_CMD"], out.rstrip("\n"))
                rc, _ = self._run(["is-retroarch", "", "", "nes"])
                self.assertEqual(li["MAD_LI_IS_RETROARCH"], "1" if rc == 0 else "0")
                rc, _ = self._run(["lightgun-rom", rom])
                self.assertEqual(li["MAD_LI_LIGHTGUN_ROM"], "1" if rc == 0 else "0")

    def test_both_escapings_produce_identical_output(self):
        self.assertEqual(self._launch_info(ROM_ESCAPED, "nes"),
                         self._launch_info(ROM_PLAIN, "nes"))

    def test_libs_always_see_the_stripped_rom(self):
        self._launch_info(ROM_ESCAPED, "nes")
        self._run(["quit-combo-collection", ROM_ESCAPED])
        self.assertTrue(self.seen_roms)
        for rom in self.seen_roms:
            self.assertNotIn("\\", rom)

    # -- value fidelity --------------------------------------------------------

    def test_nasty_quit_cmd_round_trips_shlex(self):
        li = self._launch_info(ROM_PLAIN, "ps2")
        self.assertEqual(li["MAD_LI_QUIT_CMD"], NASTY_QUIT)

    def test_handheld_matches_inline_probe_semantics(self):
        li = self._launch_info(ROM_PLAIN, "nes")
        self.assertEqual(li["MAD_LI_HANDHELD"], "1")
        with mock.patch.object(_policy, "load_merged", return_value={}):
            li = self._launch_info(ROM_PLAIN, "nes")
            self.assertEqual(li["MAD_LI_HANDHELD"], "0")
        with mock.patch.object(_policy, "load_merged", side_effect=RuntimeError):
            # a broken policy read means "docked", exactly like the old `&& HH=`
            li = self._launch_info(ROM_PLAIN, "nes")
            self.assertEqual(li["MAD_LI_HANDHELD"], "0")

    def test_setup_needed_gate(self):
        self.assertEqual(self._launch_info(ROM_PLAIN, "nes")["MAD_LI_SETUP_NEEDED"], "0")
        self.assertEqual(self._launch_info(ROM_PLAIN, "ps2")["MAD_LI_SETUP_NEEDED"], "0")
        self.assertEqual(self._launch_info(ROM_PLAIN, "switch")["MAD_LI_SETUP_NEEDED"], "0")
        self.assertEqual(self._launch_info(ROM_PLAIN, "gba")["MAD_LI_SETUP_NEEDED"], "1")
        # empty/unknown command MUST mean "run setup" (ES-DE-bundled RA systems)
        self.assertEqual(self._launch_info(ROM_PLAIN, "unknownsys")["MAD_LI_SETUP_NEEDED"], "1")

    def test_setup_needed_uses_the_per_game_resolved_command(self):
        # a per-game RA <altemulator> under a binder-default system keeps setup
        with mock.patch.object(_es, "resolved_command",
                               side_effect=lambda system, stem, systems=None:
                               "%EMULATOR_RETROARCH% percore" if stem == "Duck Hunt (World)"
                               else "X mad-standalone-launch.py pcsx2 ..."):
            self.assertEqual(self._launch_info(ROM_PLAIN, "ps2")["MAD_LI_SETUP_NEEDED"], "1")

    # -- output contract -------------------------------------------------------

    def test_completeness_marker_is_last(self):
        rc, out = self._run(["launch-info", ROM_PLAIN, "n", "nes", "f"])
        self.assertEqual(rc, 0)
        keys = [line.split("=", 1)[0] for line in out.splitlines()]
        self.assertEqual(keys[-2:], ["MAD_LI_ROM", "MAD_LI_SYSTEM"])
        self.assertEqual(keys[0], "MAD_LI_QUIT_COMBO_COLLECTION")

    def test_no_lightgun_omits_the_key_entirely(self):
        rc, out = self._run(["launch-info", "--no-lightgun", ROM_PLAIN, "n", "nes", "f"])
        self.assertEqual(rc, 0)
        self.assertNotIn("MAD_LI_LIGHTGUN_ROM", out)
        self.assertIn("MAD_LI_SETUP_NEEDED", out)

    def test_rom_and_system_echo_the_validated_values(self):
        li = self._launch_info(ROM_ESCAPED, "nes")
        self.assertEqual(li["MAD_LI_ROM"], ROM_PLAIN)   # unescaped form
        self.assertEqual(li["MAD_LI_SYSTEM"], "nes")


if __name__ == "__main__":
    unittest.main()
