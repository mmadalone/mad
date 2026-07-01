"""Tests for pcsx2_settings — the full PCSX2 global settings tree (5 category
namespaces) with buffered Save/Cancel. Hermetic: a fixture ini is generated from
the module's own GROUPS (covers all keys) plus targeted value overrides; the engine
is pointed at a temp copy. An extra reality-check runs only when the live PCSX2.ini
is present (confirms the installed build honors every offered key)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil
from lib.madsrv import pcsx2_settings as ps
from lib.madsrv import rpc


def _default_token(it):
    t = it["type"]
    if t == "bool":
        return "false"
    if t == "enum":
        return it["options_stored"][0] if it.get("write_mode") == "option" else "0"
    if t == "float":
        return str(it.get("min", 0))
    if t == "float_scaled":
        return "0"  # a valid float token (scaled value 0)
    return str(it.get("min", 0))  # int


def _build_fixture(overrides=None):
    """Render an ini covering every GROUPS key (+ overrides {(section,key): value})."""
    overrides = overrides or {}
    sections = {}
    for _ns, (_title, groups) in ps.CATEGORIES.items():
        for g in groups:
            for it in g["items"]:
                if it["type"] == "clamp":
                    for k in it["clamp_keys"]:
                        sections.setdefault(it["section"], {}).setdefault(k, "false")
                else:
                    sec, key = it["section"], it.get("name", it["key"])
                    sections.setdefault(sec, {}).setdefault(key, _default_token(it))
    for (sec, key), val in overrides.items():
        sections.setdefault(sec, {})[key] = val
    out = []
    for sec, kv in sections.items():
        out.append(f"[{sec}]")
        out.extend(f"{k} = {v}" for k, v in kv.items())
        out.append("")
    return "\n".join(out)


_OVERRIDES = {
    ("EmuCore/GS", "Renderer"): "14",
    ("EmuCore/GS", "upscale_multiplier"): "3",
    ("EmuCore/GS", "MaxAnisotropy"): "8",
    ("EmuCore/GS", "AspectRatio"): "16:9",
    ("EmuCore/GS", "VsyncEnable"): "false",
    ("EmuCore/CPU/Recompiler", "fpuOverflow"): "true",   # EE clamp = Normal (T,F,F)
    ("Framerate", "NominalScalar"): "1",
    ("Framerate", "SlomoScalar"): "0.5",
    ("SPU2/Output", "OutputVolume"): "100",
    ("SPU2/Output", "Backend"): "SDL",
    ("SPU2/Output", "SyncMode"): "TimeStretch",
}


class Pcsx2SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ini = self.dir / "PCSX2.ini"
        self.ini.write_text(_build_fixture(_OVERRIDES), newline="")
        self._orig_file = ps._FILE
        self._orig_running = ps._running
        self.running = False
        ps._FILE = self.ini
        ps._running = lambda: self.running
        ps._buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        # count staterev bumps without touching the real rev store
        import lib.staterev as sr
        self._orig_bump = sr.bump
        self.bumps = []
        sr.bump = lambda name: self.bumps.append(name)

    def tearDown(self):
        ps._FILE = self._orig_file
        ps._running = self._orig_running
        import lib.staterev as sr
        sr.bump = self._orig_bump
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, ns):
        return {s["key"]: s for g in ps._get(ns)["groups"] for s in g["settings"]}

    def _disk(self, sec, key):
        return cfgutil.ini_read(self.ini.read_text(newline=""), sec, key)

    # ── structure ────────────────────────────────────────────────────────────
    def test_five_categories_and_rpc_methods(self):
        self.assertEqual(list(ps.CATEGORIES), ["pcsx2emu", "pcsx2gfx", "pcsx2osd", "pcsx2aud", "pcsx2adv"])
        for ns in ps.CATEGORIES:
            for verb in ("get", "set", "save", "cancel"):
                self.assertIn(f"{ns}.{verb}", rpc._METHODS)

    def test_group_schema_is_wellformed(self):
        for ns, (_t, groups) in ps.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    if it["type"] == "enum" and it.get("write_mode") == "option":
                        self.assertEqual(len(it["options_display"]), len(it["options_stored"]), it["key"])
                    if it["type"] == "clamp":
                        self.assertEqual(len(it["clamp_keys"]), 3, it["key"])
                        self.assertGreaterEqual(len(it["options_display"]), 2, it["key"])

    # ── get ──────────────────────────────────────────────────────────────────
    def test_get_buffered_payload_and_values(self):
        pay = ps._get("pcsx2gfx")
        self.assertTrue(pay["buffered"])
        self.assertTrue(pay["exists"])
        self.assertFalse(pay["dirty"])
        rows = {s["key"]: s for g in pay["groups"] for s in g["settings"]}
        self.assertEqual(rows["Renderer"]["value"], 1)            # 14 -> Vulkan (index 1)
        self.assertEqual(rows["upscale_multiplier"]["value"], 2)  # '3' -> index 2
        self.assertEqual(rows["MaxAnisotropy"]["value"], 3)       # 8 -> index 3
        self.assertEqual(rows["AspectRatio"]["value"], 3)         # 16:9 -> index 3
        self.assertEqual(rows["FramerateNTSC"]["type"], "enum")   # curated rate presets

    def test_get_skips_absent_keys(self):
        # remove one key from the fixture -> not offered
        text = self.ini.read_text(newline="")
        text = cfgutil.ini_remove(text, "EmuCore/GS", "OsdShowFPS")
        self.ini.write_text(text, newline="")
        self.assertNotIn("OsdShowFPS", self._rows("pcsx2osd"))
        self.assertIn("OsdShowSpeed", self._rows("pcsx2osd"))

    # ── set / save ─────────────────────────────────────────────────────────
    def test_set_stages_and_save_writes(self):
        ps._get("pcsx2gfx")
        ps._set("pcsx2gfx", {"key": "Renderer", "value": 2})       # OpenGL (12)
        ps._set("pcsx2gfx", {"key": "upscale_multiplier", "value": 3})  # '4'
        self.assertTrue(ps._buf["dirty"])
        self.assertEqual(self._disk("EmuCore/GS", "Renderer"), "14")   # unchanged pre-save
        res = ps._save("pcsx2gfx")
        self.assertTrue(res["saved"])
        self.assertEqual(self._disk("EmuCore/GS", "Renderer"), "12")
        self.assertEqual(self._disk("EmuCore/GS", "upscale_multiplier"), "4")
        self.assertFalse(ps._buf["dirty"])
        self.assertIn("config", self.bumps)

    def test_float_whole_value_written_as_bare_int(self):
        ps._get("pcsx2osd")
        ps._set("pcsx2osd", {"key": "OsdScale", "value": 200})
        ps._save("pcsx2osd")
        self.assertEqual(self._disk("EmuCore/GS", "OsdScale"), "200")
        ps._set("pcsx2osd", {"key": "OsdScale", "value": 150.5})
        ps._save("pcsx2osd")
        self.assertEqual(self._disk("EmuCore/GS", "OsdScale"), "150.5")

    # ── review fixes: scaled-int floats, pow2 enum, presets, bounds, conflict-safe save ──
    def test_float_scaled_roundtrip(self):
        t = cfgutil.ini_replace(self.ini.read_text(newline=""), "SPU2/Output", "ExpandShift", "0.35")
        self.ini.write_text(t, newline="")
        rows = self._rows("pcsx2aud")
        self.assertEqual(rows["ExpandShift"]["type"], "int")      # scaled-int stepper
        self.assertEqual(rows["ExpandShift"]["value"], 35)        # 0.35 * 100
        self.assertEqual(rows["ExpandShift"]["min"], -100)
        ps._set("pcsx2aud", {"key": "ExpandShift", "value": -50})
        ps._save("pcsx2aud")
        self.assertEqual(self._disk("SPU2/Output", "ExpandShift"), "-0.5")   # -50/100
        ps._set("pcsx2aud", {"key": "ExpandShift", "value": 100})
        ps._save("pcsx2aud")
        self.assertEqual(self._disk("SPU2/Output", "ExpandShift"), "1")      # 100/100 -> bare int

    def test_float_scaled_clamps_to_pcsx2_range(self):
        ps._get("pcsx2aud")
        ps._set("pcsx2aud", {"key": "ExpandCenterImage", "value": 500})  # clamp to 100 -> 1.0
        ps._save("pcsx2aud")
        self.assertEqual(self._disk("SPU2/Output", "ExpandCenterImage"), "1")

    def test_blocksize_is_pow2_enum(self):
        rows = self._rows("pcsx2aud")
        self.assertEqual(rows["ExpandBlockSize"]["type"], "enum")
        ps._set("pcsx2aud", {"key": "ExpandBlockSize", "value": 4})  # index 4 -> '2048'
        ps._save("pcsx2aud")
        self.assertEqual(self._disk("SPU2/Output", "ExpandBlockSize"), "2048")

    def test_framerate_presets(self):
        rows = self._rows("pcsx2gfx")
        self.assertEqual(rows["FramerateNTSC"]["type"], "enum")
        self.assertEqual(rows["FramerateNTSC"]["value"], 0)   # fixture '59.94' -> index 0
        ps._set("pcsx2gfx", {"key": "FramerateNTSC", "value": 1})  # '60'
        ps._save("pcsx2gfx")
        self.assertEqual(self._disk("EmuCore/GS", "FramerateNTSC"), "60")

    def test_expander_bounds_match_pcsx2_clamps(self):
        rows = self._rows("pcsx2aud")
        self.assertEqual(rows["ExpandLowCutoff"]["max"], 100)
        self.assertEqual(rows["ExpandHighCutoff"]["max"], 100)
        self.assertEqual(rows["ExpandCenterImage"]["max"], 100)   # scaled 0..100 == 0..1.0

    def test_save_preserves_external_change_to_other_keys(self):
        ps._get("pcsx2emu")
        ps._set("pcsx2emu", {"key": "VsyncEnable", "value": True})   # staged
        # an external writer changes a DIFFERENT key on disk after the buffer loaded
        t = cfgutil.ini_replace(self.ini.read_text(newline=""), "EmuCore", "EnableFastBoot", "false")
        self.ini.write_text(t, newline="")
        ps._save("pcsx2emu")
        self.assertEqual(self._disk("EmuCore/GS", "VsyncEnable"), "true")    # our edit applied
        self.assertEqual(self._disk("EmuCore", "EnableFastBoot"), "false")   # external change kept

    def test_speed_preset_float_token(self):
        rows = self._rows("pcsx2emu")
        self.assertEqual(rows["NominalScalar"]["value"], 6)   # '1' -> 100% (index 6)
        self.assertEqual(rows["SlomoScalar"]["value"], 3)     # '0.5' -> 50% (index 3)
        ps._set("pcsx2emu", {"key": "NominalScalar", "value": 0})  # Unlimited -> '0'
        ps._save("pcsx2emu")
        self.assertEqual(self._disk("Framerate", "NominalScalar"), "0")

    # ── clamp composite ───────────────────────────────────────────────────────
    def test_clamp_triple_bool_atomic_and_roundtrip(self):
        self.assertEqual(self._rows("pcsx2adv")["EEClampMode"]["value"], 1)  # T,F,F = Normal
        ps._set("pcsx2adv", {"key": "EEClampMode", "value": 3})              # Full = T,T,T
        ps._save("pcsx2adv")
        for k in ("fpuOverflow", "fpuExtraOverflow", "fpuFullMode"):
            self.assertEqual(self._disk("EmuCore/CPU/Recompiler", k), "true")
        ps._set("pcsx2adv", {"key": "EEClampMode", "value": 0})              # None = F,F,F
        ps._save("pcsx2adv")
        for k in ("fpuOverflow", "fpuExtraOverflow", "fpuFullMode"):
            self.assertEqual(self._disk("EmuCore/CPU/Recompiler", k), "false")
        self.assertEqual(self._rows("pcsx2adv")["EEClampMode"]["value"], 0)

    def test_clamp_inconsistent_ondisk_degrades(self):
        # F,T,F on disk -> _clamp_index stops at first False -> 0
        t = self.ini.read_text(newline="")
        t = cfgutil.ini_replace(t, "EmuCore/CPU/Recompiler", "fpuOverflow", "false")
        t = cfgutil.ini_replace(t, "EmuCore/CPU/Recompiler", "fpuExtraOverflow", "true")
        self.ini.write_text(t, newline="")
        self.assertEqual(self._rows("pcsx2adv")["EEClampMode"]["value"], 0)

    # ── byte preservation ─────────────────────────────────────────────────────
    def test_save_is_byte_preserving(self):
        before = self.ini.read_text(newline="")
        ps._get("pcsx2emu")
        ps._set("pcsx2emu", {"key": "VsyncEnable", "value": True})
        ps._save("pcsx2emu")
        after = self.ini.read_text(newline="")
        b, a = before.splitlines(), after.splitlines()
        self.assertEqual(len(b), len(a))
        diffs = [(x, y) for x, y in zip(b, a) if x != y]
        self.assertEqual(len(diffs), 1, diffs)
        self.assertEqual(diffs[0], ("VsyncEnable = false", "VsyncEnable = true"))

    # ── cancel / isolation ────────────────────────────────────────────────────
    def test_cancel_discards(self):
        ps._get("pcsx2aud")
        ps._set("pcsx2aud", {"key": "OutputVolume", "value": 50})
        self.assertTrue(ps._buf["dirty"])
        ps._cancel("pcsx2aud")
        self.assertFalse(ps._buf["dirty"])
        self.assertEqual(self._disk("SPU2/Output", "OutputVolume"), "100")

    def test_category_switch_discards_unsaved(self):
        ps._get("pcsx2osd")
        ps._set("pcsx2osd", {"key": "OsdShowFPS", "value": True})
        ps._get("pcsx2aud")          # switch category -> fresh reload
        ps._save("pcsx2aud")         # nothing dirty
        self.assertEqual(self._disk("EmuCore/GS", "OsdShowFPS"), "false")

    def test_same_category_refetch_preserves_dirty(self):
        ps._get("pcsx2aud")
        ps._set("pcsx2aud", {"key": "OutputVolume", "value": 55})
        # a re-fetch of the SAME dirty category keeps the staged edit
        rows = self._rows("pcsx2aud")
        self.assertEqual(rows["OutputVolume"]["value"], 55)
        self.assertTrue(ps._buf["dirty"])

    # ── guards ────────────────────────────────────────────────────────────────
    def test_ebusy_guard_on_set_and_save(self):
        ps._get("pcsx2emu")
        self.running = True
        with self.assertRaises(rpc.RpcError):
            ps._set("pcsx2emu", {"key": "VsyncEnable", "value": False})
        with self.assertRaises(rpc.RpcError):
            ps._save("pcsx2emu")

    def test_unknown_key_rejected(self):
        with self.assertRaises(rpc.RpcError):
            ps._set("pcsx2gfx", {"key": "NoSuchKey", "value": 1})

    # ── reality-check against the live ini (skipped if absent) ────────────────
    def test_live_ini_has_every_offered_key(self):
        live = Path.home() / ".config/PCSX2/inis/PCSX2.ini"
        if not live.is_file():
            self.skipTest("no live PCSX2.ini on this host")
        text = live.read_text(encoding="utf-8", errors="replace", newline="")
        missing = []
        for _ns, (_t, groups) in ps.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    keys = it["clamp_keys"] if it["type"] == "clamp" else [it.get("name", it["key"])]
                    for k in keys:
                        if cfgutil.ini_read(text, it["section"], k) is None:
                            missing.append(f"[{it['section']}] {k}")
        self.assertEqual(missing, [], f"keys offered but absent from live ini: {missing}")


if __name__ == "__main__":
    unittest.main()
