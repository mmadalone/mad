"""Byte-preserving engine tests for the Dolphin ("Wii / GameCube") settings tree
(lib/madsrv/dolphin_settings.py). Multi-file (Dolphin.ini + GFX.ini), instant-save.

Covers: get offers only present keys (+ create-items' defaults); set is byte-stable
(only the one value token changes); enum index/option + bool True/False + float;
create-in-section (Overclock -> [Core], Volume -> [DSP]); the MSAA+SSAA "Anti-aliasing"
composite; and the running-guard (EBUSY) refusal.

Run:  python3 -m unittest tests.test_dolphin_settings -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import lib.proc_guard as proc_guard
from lib.madsrv import dolphin_settings as ds
from lib.madsrv.rpc import RpcError

_DOLPHIN_INI = """\
[Core]
CPUThread = True
EnableCheats = True
EmulationSpeed = 1.00000000
MMU = False
LoadGameIntoMemory = False
GFXBackend = Vulkan
DSPHLE = True
AudioStretch = False
OverrideRegionSettings = False
AutoDiscChange = True
SIDevice0 = 6
SIDevice1 = 6
SIDevice2 = 6
SIDevice3 = 6
WiimoteContinuousScanning = False
WiimoteEnableSpeaker = True
WiimoteControllerInterface = False
[DSP]
Backend = Cubeb
DSPThread = True
[Display]
Fullscreen = True
[Interface]
ConfirmStop = False
OnScreenDisplayMessages = True
PauseOnFocusLost = False
UsePanicHandlers = False
"""

_GFX_INI = """\
[Enhancements]
ArbitraryMipmapDetection = True
DisableCopyFilter = True
ForceTrueColor = True
[Hacks]
EFBToTextureEnable = True
XFBToTextureEnable = True
DeferEFBCopies = True
EFBScaledCopy = True
EFBAccessEnable = True
EFBEmulateFormatChanges = False
SkipDuplicateXFBs = True
ImmediateXFBEnable = False
VISkip = True
BBoxEnable = False
[Settings]
InternalResolution = 3
AspectRatio = 0
ShowFPS = False
wideScreenHack = False
ShaderCompilationMode = 2
BackendMultithreading = True
WaitForShadersBeforeStarting = False
EnableGPUTextureDecoding = True
FastDepthCalc = True
HiresTextures = True
CacheHiresTextures = True
SaveTextureCacheToState = True
EnableMods = True
MSAA = 0x00000001
SSAA = False
[Hardware]
MaxAnisotropy = 3
VSync = True
"""


class DolphinEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "Dolphin.ini").write_text(_DOLPHIN_INI)
        (self.tmp / "GFX.ini").write_text(_GFX_INI)
        self._orig_dir, self._orig_files = ds._DIR, ds._FILES
        ds._DIR = self.tmp
        ds._FILES = {ds.DOLPHIN: self.tmp / ds.DOLPHIN, ds.GFX: self.tmp / ds.GFX}
        self._orig_run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda *a, **k: False

    def tearDown(self):
        ds._DIR, ds._FILES = self._orig_dir, self._orig_files
        proc_guard.emulator_running = self._orig_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _groups(self, ns):
        return ds.PAGES[ns][1]

    def _get(self, ns):
        return ds._do_get(self._groups(ns))

    def _set(self, ns, key, value):
        return ds._do_set(self._groups(ns), {"key": key, "value": value})

    def _line(self, fname, key):
        for ln in (self.tmp / fname).read_text().splitlines():
            if ln.startswith(key + " "):
                return ln
        return None

    def _in(self, fname, section, key):
        from lib.madsrv import cfgutil
        return cfgutil.ini_read((self.tmp / fname).read_text(), section, key)

    # -- get -------------------------------------------------------------------
    def test_every_page_gets(self):
        for ns in ds.PAGES:
            r = self._get(ns)
            self.assertTrue(r["exists"], ns)
            self.assertTrue(r["groups"], ns)

    def test_enum_values_in_range(self):
        for ns in ds.PAGES:
            for g in self._get(ns)["groups"]:
                for s in g["settings"]:
                    if s["type"] == "enum":
                        self.assertTrue(0 <= s["value"] < len(s["options"]), f"{ns}:{s['key']}")

    # -- set: byte-stable single value ----------------------------------------
    def test_bool_writes_capitalized(self):
        before = (self.tmp / "Dolphin.ini").read_text()
        self.assertEqual(self._set("dolphin_general", "CPUThread", "0")["value"], False)
        self.assertEqual(self._line("Dolphin.ini", "CPUThread"), "CPUThread = False")
        # exactly one line changed
        after = (self.tmp / "Dolphin.ini").read_text()
        self.assertEqual(sum(a != b for a, b in zip(before.splitlines(), after.splitlines())), 1)

    def test_enum_index(self):
        self._set("dolphin_gfx_general", "AspectRatio", 2)
        self.assertEqual(self._line("GFX.ini", "AspectRatio"), "AspectRatio = 2")

    def test_enum_option(self):
        self._set("dolphin_gfx_general", "GFXBackend", 1)      # -> OGL
        self.assertEqual(self._line("Dolphin.ini", "GFXBackend"), "GFXBackend = OGL")

    def test_sidevice_noncontiguous_option(self):
        # SIDevice options are stored ints; index 3 -> stored "5" (GBA real link)
        self._set("dolphin_gc", "SIDevice0", 3)
        self.assertEqual(self._line("Dolphin.ini", "SIDevice0"), "SIDevice0 = 5")

    def test_float(self):
        self._set("dolphin_general", "EmulationSpeed", 1.5)
        self.assertEqual(self._line("Dolphin.ini", "EmulationSpeed"), "EmulationSpeed = 1.5")

    # -- create-in-section -----------------------------------------------------
    def test_create_overclock_in_core(self):
        self.assertIsNone(self._line("Dolphin.ini", "OverclockEnable"))   # absent to start
        self._set("dolphin_advanced", "OverclockEnable", "1")
        self._set("dolphin_advanced", "Overclock", 1.25)
        self.assertEqual(self._line("Dolphin.ini", "OverclockEnable"), "OverclockEnable = True")
        self.assertEqual(self._line("Dolphin.ini", "Overclock"), "Overclock = 1.25")
        # created inside [Core], not a stray/duplicate section
        text = (self.tmp / "Dolphin.ini").read_text()
        self.assertEqual(text.count("[Core]"), 1)
        self.assertLess(text.index("OverclockEnable"), text.index("[DSP]"))

    def test_create_volume_in_dsp(self):
        self._set("dolphin_audio", "Volume", 80)
        self.assertEqual(self._line("Dolphin.ini", "Volume"), "Volume = 80")
        text = (self.tmp / "Dolphin.ini").read_text()
        self.assertGreater(text.index("Volume ="), text.index("[DSP]"))

    def test_create_default_shown_before_set(self):
        # Overclock is absent on disk but shown with its default via create.
        adv = {s["key"]: s for g in self._get("dolphin_advanced")["groups"] for s in g["settings"]}
        self.assertIn("OverclockEnable", adv)
        self.assertEqual(adv["OverclockEnable"]["value"], False)

    # -- AA composite ----------------------------------------------------------
    def test_aa_composite_roundtrip(self):
        enh = {s["key"]: s for g in self._get("dolphin_gfx_enh")["groups"] for s in g["settings"]}
        self.assertIn("_aa", enh)
        self.assertEqual(enh["_aa"]["value"], 0)              # MSAA=1,SSAA=False -> None
        self._set("dolphin_gfx_enh", "_aa", 2)                # -> 4x MSAA
        self.assertEqual(self._line("GFX.ini", "MSAA"), "MSAA = 0x00000004")
        self.assertEqual(self._line("GFX.ini", "SSAA"), "SSAA = False")
        self._set("dolphin_gfx_enh", "_aa", 5)                # -> 4x SSAA
        self.assertEqual(self._line("GFX.ini", "MSAA"), "MSAA = 0x00000004")
        self.assertEqual(self._line("GFX.ini", "SSAA"), "SSAA = True")

    # -- guards ----------------------------------------------------------------
    def test_running_refuses(self):
        proc_guard.emulator_running = lambda *a, **k: True
        with self.assertRaises(RpcError) as cm:
            self._set("dolphin_general", "CPUThread", "0")
        self.assertEqual(cm.exception.code, "EBUSY")

    def test_absent_noncreate_key_not_offered(self):
        # A present-only page never invents a key that Dolphin didn't write.
        gc = {s["key"] for g in self._get("dolphin_gc")["groups"] for s in g["settings"]}
        self.assertEqual(gc, {"SIDevice0", "SIDevice1", "SIDevice2", "SIDevice3"})

    # -- MaxAnisotropy version-drift (section detection) ------------------------
    def _aniso(self):
        enh = {s["key"]: s for g in self._get("dolphin_gfx_enh")["groups"] for s in g["settings"]}
        return enh["MaxAnisotropy"]

    def test_maxaniso_hardware_fallback(self):
        # Fixture has MaxAnisotropy only in [Hardware]=3 (older-build layout) -> use it.
        af = self._aniso()
        self.assertEqual(af["options"][af["value"]], "8x")
        self._set("dolphin_gfx_enh", "MaxAnisotropy", af["options"].index("16x"))
        self.assertEqual(self._in("GFX.ini", "Hardware", "MaxAnisotropy"), "4")

    def test_maxaniso_prefers_enhancements(self):
        # Build 2606/master writes MaxAnisotropy in [Enhancements]; must read/write THERE,
        # not the stale [Hardware] copy (the HIGH review finding).
        p = self.tmp / "GFX.ini"
        p.write_text(p.read_text().replace("[Enhancements]\n", "[Enhancements]\nMaxAnisotropy = 4\n"))
        af = self._aniso()
        self.assertEqual(af["options"][af["value"]], "16x")            # read [Enhancements]=4
        self._set("dolphin_gfx_enh", "MaxAnisotropy", af["options"].index("2x"))
        self.assertEqual(self._in("GFX.ini", "Enhancements", "MaxAnisotropy"), "1")  # written here
        self.assertEqual(self._in("GFX.ini", "Hardware", "MaxAnisotropy"), "3")      # stale copy untouched

    # -- AA composite: synthetic "(current)" option is a no-op, not EINVAL ------
    def test_aa_unknown_combo_reselect_is_noop(self):
        p = self.tmp / "GFX.ini"
        p.write_text(p.read_text().replace("MSAA = 0x00000001", "MSAA = 0x00000010"))  # 16x, not in _AA_MAP
        enh = {s["key"]: s for g in self._get("dolphin_gfx_enh")["groups"] for s in g["settings"]}
        aa = enh["_aa"]
        self.assertGreater(len(aa["options"]), len(ds._AA_MAP))         # synthetic "(current)" appended
        before = p.read_text()
        self._set("dolphin_gfx_enh", "_aa", aa["value"])               # re-select current -> no error
        self.assertEqual(p.read_text(), before)                        # and no write

    # -- create-item offered only when its section exists ----------------------
    def test_volume_not_offered_without_dsp_section(self):
        import re
        p = self.tmp / "Dolphin.ini"
        p.write_text(re.sub(r"(?ms)^\[DSP\].*?(?=^\[)", "", p.read_text()))   # drop [DSP]
        aud = {s["key"] for g in self._get("dolphin_audio")["groups"] for s in g["settings"]}
        self.assertNotIn("Volume", aud)                                # create-item skipped, not offered
        with self.assertRaises(RpcError):
            self._set("dolphin_audio", "Volume", 50)


if __name__ == "__main__":
    unittest.main()
