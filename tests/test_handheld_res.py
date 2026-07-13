"""Backend-aware handheld internal-resolution rail (lib/handheld_res.py).

Ladder snap-down + the two fixed native-token bugs; byte-stable apply/revert per writer_kind
(opt/ini/yaml); only-ever-LOWER; revert-if-user-edited; crash-orphan self-heal; dispatch to the
right backend (RA core vs standalone) incl. unknown-backend no-op; the docked/off/inherit gates;
transitional legacy-orphan heal. Temp config tree + MAD_FORCE_CONTEXT.
Run: python3 -m unittest tests.test_handheld_res -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import handheld_res as hr
from lib import retroarch_cfg
from lib.madsrv import cfgutil


def _pol(system, res="2x", *, enabled=True, sys_enabled=True):
    return {"handheld": {"enabled": enabled},
            "systems": {system: {"handheld": {"enabled": sys_enabled, "res": res}}}}


# ── pure ladder math (no fixtures) ───────────────────────────────────────────
class Ladder(unittest.TestCase):
    def test_snapdown(self):
        b = hr.REGISTRY["Beetle PSX HW"].keys[0]
        self.assertEqual([b.value_for_factor(f) for f in (1, 2, 3, 4, 6, 8)],
                         ["1x(native)", "2x", "2x", "4x", "4x", "8x"])
        fly = hr.REGISTRY["Flycast"].keys[0]
        self.assertEqual(fly.value_for_factor(2), "1280x960")
        self.assertEqual(fly.value_for_factor(4), "2560x1920")

    def test_fixed_native_tokens(self):
        # These two were invalid before (wrote "1x"/"1X"); must be real enum members now.
        self.assertEqual(hr.REGISTRY["Beetle PSX HW"].keys[0].value_for_factor(1), "1x(native)")
        self.assertEqual(hr.REGISTRY["Kronos"].keys[0].value_for_factor(1), "original")

    def test_yaba_ranks_high_members(self):
        y = hr.REGISTRY["YabaSanshiro"].keys[0]
        self.assertLess(y.rank("2x"), y.rank("1080p"))     # so a docked 1080p can be lowered to 2x
        self.assertEqual(y.value_for_factor(8), "4x")      # never targets 720p/1080p/4k

    def test_scalar_and_wxh_ranks(self):
        d = hr.REGISTRY["dolphin"].keys[0]
        self.assertEqual((d.rank("3"), d.rank("0")), (3.0, 0.0))
        m = hr.REGISTRY["Mupen64Plus-Next"].keys[0]
        self.assertEqual(m.rank("1280x960"), 1280.0 * 960.0)


# ── shared fixture harness ───────────────────────────────────────────────────
class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cfg = self.d / "config"
        self.cfg.mkdir()
        self.mdir = self.d / "handheld-res"
        self._patches = [mock.patch.object(hr, "_DIR", self.mdir),
                         mock.patch.object(retroarch_cfg, "RA_CONFIG_BASE", self.cfg)]
        for p in self._patches:
            p.start()
        os.environ["MAD_FORCE_CONTEXT"] = "handheld"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("MAD_FORCE_CONTEXT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _mk_opt(self, core, name, body):
        d = self.cfg / core
        d.mkdir(parents=True, exist_ok=True)
        f = d / (name + ".opt")
        f.write_text(body)
        return f

    def _markers(self):
        return list(self.mdir.glob("*.json")) if self.mdir.exists() else []

    def _apply(self, system, rom, policy, *, core=None, standalone=None, target=None, target_fn=None):
        import contextlib
        with contextlib.ExitStack() as es:
            es.enter_context(mock.patch.object(hr, "load_merged", lambda: policy))
            es.enter_context(mock.patch.object(
                retroarch_cfg, "launched_core", lambda s, st, systems=None: core))
            es.enter_context(mock.patch.object(
                hr.es_systems, "resolved_command", lambda s, st, systems=None: "cmd"))
            es.enter_context(mock.patch.object(
                hr.es_systems, "standalone_backend_id", lambda c: standalone))
            if target_fn:
                es.enter_context(mock.patch.object(hr, target_fn, lambda rom: target))
            hr.apply(system, rom)


# ── opt backends (RetroArch cores) ───────────────────────────────────────────
class OptBackends(Base):
    def _opt_val(self, f, key):
        return retroarch_cfg.read_opt(f, key)

    def test_beetle_roundtrip_byte_stable(self):
        f = self._mk_opt("Beetle PSX HW", "Game", 'beetle_psx_hw_internal_resolution = "8x"\n')
        before = f.read_bytes()
        self._apply("psx", "/roms/Game.chd", _pol("psx", "2x"), core="Beetle PSX HW")
        self.assertEqual(self._opt_val(f, "beetle_psx_hw_internal_resolution"), "2x")
        self.assertEqual(len(self._markers()), 1)
        hr.sweep_all()
        self.assertEqual(f.read_bytes(), before)          # byte-identical restore
        self.assertFalse(self._markers())

    def test_only_lower(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "2x"\n')
        self._apply("psx", "/roms/G.chd", _pol("psx", "8x"), core="Beetle PSX HW")  # 8x > 2x
        self.assertEqual(self._opt_val(f, "beetle_psx_hw_internal_resolution"), "2x")  # untouched
        self.assertFalse(self._markers())

    def test_native_token_is_valid(self):
        f = self._mk_opt("Kronos", "K", 'kronos_resolution_mode = "8X"\n')
        self._apply("saturn", "/roms/K.chd", _pol("saturn", "native"), core="Kronos")
        self.assertEqual(self._opt_val(f, "kronos_resolution_mode"), "original")   # not "1X"
        hr.sweep_all()
        self.assertEqual(self._opt_val(f, "kronos_resolution_mode"), "8X")

    def test_revert_if_user_edited(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "8x"\n')
        self._apply("psx", "/roms/G.chd", _pol("psx", "2x"), core="Beetle PSX HW")   # -> 2x
        retroarch_cfg.write_opt(f, "beetle_psx_hw_internal_resolution", "4x")         # user edit
        hr.sweep_all()
        self.assertEqual(self._opt_val(f, "beetle_psx_hw_internal_resolution"), "4x")  # kept

    def test_mupen_two_keys(self):
        body = ('mupen64plus-43screensize = "2560x1920"\n'
                'mupen64plus-169screensize = "1920x1080"\n')
        f = self._mk_opt("Mupen64Plus-Next", "N64", body)
        before = f.read_bytes()
        self._apply("n64", "/roms/N64.z64", _pol("n64", "2x"), core="Mupen64Plus-Next")
        self.assertEqual(self._opt_val(f, "mupen64plus-43screensize"), "1280x960")
        self.assertEqual(self._opt_val(f, "mupen64plus-169screensize"), "1280x720")
        hr.sweep_all()
        self.assertEqual(f.read_bytes(), before)

    def test_yaba_default_core(self):
        f = self._mk_opt("YabaSanshiro", "S", 'yabasanshiro_resolution_mode = "1080p"\n')
        self._apply("saturn", "/roms/S.chd", _pol("saturn", "2x"), core="YabaSanshiro")
        self.assertEqual(self._opt_val(f, "yabasanshiro_resolution_mode"), "2x")
        hr.sweep_all()
        self.assertEqual(self._opt_val(f, "yabasanshiro_resolution_mode"), "1080p")


# ── ini / yaml (standalone) backends ─────────────────────────────────────────
class StandaloneBackends(Base):
    def test_dolphin_ini(self):
        gfx = self.d / "GFX.ini"
        gfx.write_text("[Settings]\nInternalResolution = 3\nAspectRatio = 0\n")
        before = gfx.read_bytes()
        self._apply("gc", "/roms/g.rvz", _pol("gc", "2x"), core=None, standalone="dolphin",
                    target=(gfx, "Settings"), target_fn="_dolphin_target")
        self.assertEqual(cfgutil.ini_read(gfx.read_text(), "Settings", "InternalResolution"), "2")
        hr.sweep_all()
        self.assertEqual(gfx.read_bytes(), before)

    def test_dolphin_auto_untouched(self):
        gfx = self.d / "GFX.ini"
        gfx.write_text("[Settings]\nInternalResolution = 0\n")           # 0 = Auto
        self._apply("gc", "/roms/g.rvz", _pol("gc", "4x"), core=None, standalone="dolphin",
                    target=(gfx, "Settings"), target_fn="_dolphin_target")
        self.assertEqual(cfgutil.ini_read(gfx.read_text(), "Settings", "InternalResolution"), "0")
        self.assertFalse(self._markers())

    def test_pcsx2_ini(self):
        ini = self.d / "PCSX2.ini"
        ini.write_text("[EmuCore/GS]\nupscale_multiplier = 3\n")
        self._apply("ps2", "/roms/g.iso", _pol("ps2", "2x"), core=None, standalone="pcsx2",
                    target=ini, target_fn="_pcsx2_target")
        self.assertEqual(cfgutil.ini_read(ini.read_text(), "EmuCore/GS", "upscale_multiplier"), "2")
        hr.sweep_all()
        self.assertEqual(cfgutil.ini_read(ini.read_text(), "EmuCore/GS", "upscale_multiplier"), "3")

    def test_rpcs3_yaml_native(self):
        cy = self.d / "config.yml"
        cy.write_text("Video:\n  Resolution Scale: 200\n")
        self._apply("ps3", "/roms/g/", _pol("ps3", "native"), core=None, standalone="rpcs3",
                    target=cy, target_fn="_rpcs3_target")
        self.assertEqual(cfgutil.yaml_read(cy.read_text(), "Video", "Resolution Scale"), "100")
        hr.sweep_all()
        self.assertEqual(cfgutil.yaml_read(cy.read_text(), "Video", "Resolution Scale"), "200")

    def test_escaped_rom_stripped_for_target(self):
        # ES-DE passes hooks a backslash-escaped path; the standalone target resolver (which
        # stats/realpaths the file) must receive the REAL path, else a spaced filename with a
        # per-game override silently falls back to the global config.
        ini = self.d / "PCSX2.ini"
        ini.write_text("[EmuCore/GS]\nupscale_multiplier = 3\n")
        seen = {}
        def cap(rom):
            seen["rom"] = rom
            return ini
        import contextlib
        with contextlib.ExitStack() as es:
            es.enter_context(mock.patch.object(hr, "load_merged", lambda: _pol("ps2", "2x")))
            es.enter_context(mock.patch.object(
                retroarch_cfg, "launched_core", lambda s, st, systems=None: None))
            es.enter_context(mock.patch.object(
                hr.es_systems, "resolved_command", lambda s, st, systems=None: "cmd"))
            es.enter_context(mock.patch.object(
                hr.es_systems, "standalone_backend_id", lambda c: "pcsx2"))
            es.enter_context(mock.patch.object(hr, "_pcsx2_target", cap))
            hr.apply("ps2", r"/roms/ps2/Gran\ Turismo\ 4\ (USA).iso")
        self.assertEqual(seen["rom"], "/roms/ps2/Gran Turismo 4 (USA).iso")


# ── Dolphin Wii per-game handheld resolution ─────────────────────────────────
class WiiPerGame(Base):
    """[backends.dolphin_wii.pergame.<id>].hhres overrides the per-system token (and applies even when
    per-system res is 'inherit'), reusing the same _effective target + marker/revert machinery."""

    def _pol(self, sys_res, hhres=None, gid="RSPE01", system="wii"):
        pol = {"handheld": {"enabled": True},
               "systems": {system: {"handheld": {"enabled": True, "res": sys_res}}}}
        if hhres is not None:
            pol["backends"] = {"dolphin_wii": {"pergame": {gid: {"hhres": hhres}}}}
        return pol

    def _gfx(self, val="3"):
        gfx = self.d / "GFX.ini"
        gfx.write_text(f"[Settings]\nInternalResolution = {val}\nAspectRatio = 0\n")
        return gfx

    def _res(self, gfx):
        return cfgutil.ini_read(gfx.read_text(), "Settings", "InternalResolution")

    def _apply_dolphin(self, system, rom, policy, gfx, gid="RSPE01"):
        import contextlib
        from lib import dolphin_wii_tdb
        with contextlib.ExitStack() as es:
            es.enter_context(mock.patch.object(hr, "load_merged", lambda: policy))
            es.enter_context(mock.patch.object(
                retroarch_cfg, "launched_core", lambda s, st, systems=None: None))
            es.enter_context(mock.patch.object(
                hr.es_systems, "resolved_command", lambda s, st, systems=None: "cmd"))
            es.enter_context(mock.patch.object(
                hr.es_systems, "standalone_backend_id", lambda c: "dolphin"))
            es.enter_context(mock.patch.object(hr, "_dolphin_target", lambda rom: (gfx, "Settings")))
            es.enter_context(mock.patch.object(dolphin_wii_tdb, "_resolve", lambda rom: gid))
            hr.apply(system, rom)

    def test_pergame_overrides_inherit(self):
        gfx = self._gfx("3")
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("inherit", hhres="native"), gfx)
        self.assertEqual(self._res(gfx), "1")          # per-game native applied despite per-system inherit
        self.assertEqual(len(self._markers()), 1)
        hr.sweep_all()
        self.assertEqual(self._res(gfx), "3")          # reverted -> docked untouched

    def test_pergame_beats_system_token(self):
        gfx = self._gfx("3")
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("2x", hhres="native"), gfx)
        self.assertEqual(self._res(gfx), "1")          # per-game native (1) wins over per-system 2x
        hr.sweep_all()
        self.assertEqual(self._res(gfx), "3")

    def test_no_pergame_uses_system_token(self):
        gfx = self._gfx("3")
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("2x"), gfx)   # no pergame entry
        self.assertEqual(self._res(gfx), "2")          # per-system 2x applied (fallback)
        hr.sweep_all()
        self.assertEqual(self._res(gfx), "3")

    def test_pergame_inherit_falls_back_to_system(self):
        gfx = self._gfx("3")
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("2x", hhres="inherit"), gfx)
        self.assertEqual(self._res(gfx), "2")          # hhres 'inherit' -> per-system 2x
        hr.sweep_all()
        self.assertEqual(self._res(gfx), "3")

    def test_both_inherit_is_noop(self):
        gfx = self._gfx("3")
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("inherit", hhres="inherit"), gfx)
        self.assertEqual(self._res(gfx), "3")
        self.assertFalse(self._markers())

    def test_pergame_never_upscales(self):
        gfx = self._gfx("2")                            # docked/global = 2x
        self._apply_dolphin("wii", "/roms/wii/g.rvz", self._pol("inherit", hhres="4x"), gfx)  # picks higher
        self.assertEqual(self._res(gfx), "2")          # downshift-only: 4 > 2 -> no change
        self.assertFalse(self._markers())

    def test_gc_not_affected_by_wii_pergame(self):
        # GameCube uses the dolphin backend too, but per-game hhres is Wii-only -> gc + inherit no-ops.
        gfx = self._gfx("3")
        self._apply_dolphin("gc", "/roms/gc/g.rvz",
                            self._pol("inherit", hhres="native", system="gc"), gfx)
        self.assertEqual(self._res(gfx), "3")
        self.assertFalse(self._markers())


# ── dispatch + gates ─────────────────────────────────────────────────────────
class DispatchGates(Base):
    def test_unknown_backend_noop(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "8x"\n')
        self._apply("dreamcast", "/roms/G.chd", _pol("dreamcast", "2x"),
                    core=None, standalone="redream")     # redream not in registry
        self.assertFalse(self._markers())
        self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "8x")

    def test_docked_noop(self):
        os.environ["MAD_FORCE_CONTEXT"] = "docked"
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "8x"\n')
        self._apply("psx", "/roms/G.chd", _pol("psx", "2x"), core="Beetle PSX HW")
        self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "8x")
        self.assertFalse(self._markers())

    def test_feature_off_and_inherit_and_sysoff(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "8x"\n')
        for pol in (_pol("psx", "2x", enabled=False),
                    _pol("psx", "inherit"),
                    _pol("psx", "2x", sys_enabled=False)):
            self._apply("psx", "/roms/G.chd", pol, core="Beetle PSX HW")
        self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "8x")
        self.assertFalse(self._markers())


# ── crash orphan + transitional legacy heal ──────────────────────────────────
class SweepHealing(Base):
    def test_crash_orphan_self_heal(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "2x"\n')
        # simulate a crash after apply: the file is low + a stale marker exists, no revert ran
        self.mdir.mkdir(parents=True, exist_ok=True)
        import json
        (self.mdir / "m.json").write_text(json.dumps(
            {"backend": "Beetle PSX HW", "writer_kind": "opt", "section": None, "path": str(f),
             "keys": {"beetle_psx_hw_internal_resolution": {"prev": "8x", "low": "2x"}}}))
        hr.sweep_all()
        self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "8x")
        self.assertFalse(self._markers())

    def test_opt_revert_keeps_marker_on_io_failure(self):
        f = self._mk_opt("Beetle PSX HW", "G", 'beetle_psx_hw_internal_resolution = "8x"\n')
        self._apply("psx", "/roms/G.chd", _pol("psx", "2x"), core="Beetle PSX HW")   # -> 2x, marker
        self.assertTrue(self._markers())
        # isolate the unified rail from the transitional legacy sweeps for this fault-injection
        with mock.patch("lib.ra_res.sweep_all", lambda: None), \
             mock.patch("lib.dolphin_res.sweep_all", lambda: None), \
             mock.patch("lib.switch_bind._res_sweep_all", lambda: None):
            with mock.patch.object(retroarch_cfg, "write_opt", side_effect=OSError("disk full")):
                hr.sweep_all()                        # revert WRITE fails
            self.assertTrue(self._markers())          # marker KEPT for retry (not dropped)
            self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "2x")
            hr.sweep_all()                            # a later (working) sweep heals it
        self.assertEqual(retroarch_cfg.read_opt(f, "beetle_psx_hw_internal_resolution"), "8x")
        self.assertFalse(self._markers())

    def test_legacy_sweeps_invoked(self):
        called = {"ra": 0, "dolphin": 0, "switch": 0}
        with mock.patch("lib.ra_res.sweep_all", lambda: called.__setitem__("ra", called["ra"] + 1)), \
             mock.patch("lib.dolphin_res.sweep_all", lambda: called.__setitem__("dolphin", called["dolphin"] + 1)), \
             mock.patch("lib.switch_bind._res_sweep_all", lambda: called.__setitem__("switch", called["switch"] + 1)):
            hr.sweep_all()
        self.assertEqual(called, {"ra": 1, "dolphin": 1, "switch": 1})


class PickerLabels(unittest.TestCase):
    """WS-H: resolution_choices/snap_token turn the abstract ladder into per-backend real labels."""

    def _choices(self, backend_id, system="sys"):
        with mock.patch.object(hr, "_render_backend", lambda s: hr.REGISTRY[backend_id]):
            return hr.resolution_choices(system)

    def test_wxh_backends_literal(self):
        labels = [l for _, l in self._choices("Flycast")]
        self.assertEqual(labels[:3], ["640x480", "1280x960", "1920x1440"])
        self.assertEqual(labels[-1], "Inherit (leave as-is)")
        # Mupen labels off the 16:9 (last) key
        self.assertIn("1280x720", [l for _, l in self._choices("Mupen64Plus-Next")])

    def test_dolphin_exact_wxh(self):
        labels = [l for _, l in self._choices("dolphin")]
        self.assertEqual(labels[0], "Native (640x528)")
        self.assertEqual(labels[1], "2x (1280x1056)")

    def test_rpcs3_percent_of_720p(self):
        labels = [l for _, l in self._choices("rpcs3")]
        self.assertEqual(labels[0], "720p (100%)")
        self.assertEqual(labels[1], "1440p (200%)")

    def test_pcsx2_own_hints(self):
        labels = [l for _, l in self._choices("pcsx2")]
        self.assertIn("2x Native (~720px)", labels)
        self.assertIn("8x Native (~2880px)", labels)

    def test_enum_backends_dedupe(self):
        # Beetle PSX HW: 3x/6x snap to 2x/4x -> distinct rungs native/2x/4x/8x (+inherit)
        self.assertEqual([t for t, _ in self._choices("Beetle PSX HW")],
                         ["native", "2x", "4x", "8x", "inherit"])
        # YabaSanshiro caps at 4x -> native/2x/4x (+inherit)
        self.assertEqual([t for t, _ in self._choices("YabaSanshiro")],
                         ["native", "2x", "4x", "inherit"])

    def test_snap_token_to_canonical(self):
        with mock.patch.object(hr, "_render_backend", lambda s: hr.REGISTRY["Beetle PSX HW"]):
            self.assertEqual(hr.snap_token("psx", "3x"), "2x")   # 3x renders 2x on Beetle
            self.assertEqual(hr.snap_token("psx", "6x"), "4x")
            self.assertEqual(hr.snap_token("psx", "8x"), "8x")
            self.assertEqual(hr.snap_token("psx", "inherit"), "inherit")

    def test_unregistered_backend_falls_back_to_abstract(self):
        with mock.patch.object(hr, "_render_backend", lambda s: None):
            self.assertEqual([t for t, _ in hr.resolution_choices("sys")],
                             ["native", "2x", "3x", "4x", "6x", "8x", "inherit"])
            self.assertEqual(hr.snap_token("sys", "3x"), "3x")   # abstract: pass through


if __name__ == "__main__":
    unittest.main()
