"""Tests for rpcs3_patches (patch.yml index + patch_config.yml read/write) and
rpcs3_patches_cmds (the buffered per-game "Manage patches" editor).

Hermetic: a fixture patch.yml exercises the real-file quirks -- DUPLICATE top-level
PPU-<hash> keys (must be preserved, not last-wins), DUPLICATE anchors (tolerated), raw
`01.00` version strings (must NOT float-collapse), enum + range Configurable Values, and a
`Group` (alternatives). patch_config.yml is written to a temp dir; the disk index cache and
memory cache are redirected/reset per test. Proves the on-disk shape matches RPCS3
save_config: full PPU- prefix, string version keys, bare-number config values, Enabled only
when true, Configurable Values only when a value differs from the patch default.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import rpcs3_patches as rp
from lib.madsrv import rpcs3_patches_cmds as cmd
from lib.madsrv.rpc import RpcError

_S = "BLUS30443"       # Demon's Souls (fixture)
_OTHER = "BLES99999"   # a second game (must be preserved across writes)

_FIXTURE = """\
Version: 1.2

Anchors:
  des_title: &des_title
    "Demon's Souls":
      BLUS30443: [01.00]
  ar_cfg: &ar_cfg
    "Aspect Ratio":
      Type: double_enum
      Value: &v329 3.555555555555556
      Allowed Values:
        "32:9": *v329
        "21:9": 2.4
        "4:3": 1.333333333333333

PPU-aaaa:
  "Unlock FPS":
    Games: *des_title
    Patch:
      - [ be16, 0x1, 0x2 ]
  "Aspect Ratio":
    Games: *des_title
    Configurable Values: *ar_cfg
    Patch:
      - [ bef32, 0x3, "Aspect Ratio" ]
  "HUD 32:9":
    Games: *des_title
    Group: "HUD"
    Patch:
      - [ be32, 0x4, 0x5 ]
  "HUD 21:9":
    Games: *des_title
    Group: "HUD"
    Patch:
      - [ be32, 0x6, 0x7 ]
  "FOV":
    Games: *des_title
    Configurable Values:
      "FOV":
        Type: double_range
        Value: 0.75
        Min: 0.1
        Max: 1
    Patch:
      - [ bef32, 0x8, "FOV" ]

Anchors:
  redef: &v329 9.9

PPU-aaaa:
  "Extra Patch":
    Games: *des_title
    Patch:
      - [ be16, 0x9, 0xa ]

PPU-bbbb:
  "Other Game Patch":
    Games:
      "Other":
        BLES99999: [02.00]
    Patch:
      - [ be16, 0xb, 0xc ]

PPU-cccc:
  "Split Screen":
    Games:
      "UC3":
        BCES00569: [01.00]
    Configurable Values:
      "Screen Type":
        Type: double_enum
        Value: 2e-37
        Allowed Values:
          "Horizontal": 2e-37
          "Vertical": 4e-37
    Patch:
      - [ bef32, 0x1, "Screen Type" ]
  "Hex FOV":
    Games:
      "UC3":
        BCES00569: [01.00]
    Configurable Values:
      "FOV":
        Type: long_enum
        Value: 0x3cc0428C
        Allowed Values:
          "70": 0x3cc0428C
          "80": 0x3cc042F0
    Patch:
      - [ bef32, 0x2, "FOV" ]
"""

_UC = "BCES00569"      # Uncharted-3-like fixture game (tiny-sentinel enum + hex enum)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.pyml = self.d / "patch.yml"
        self.pyml.write_text(_FIXTURE, encoding="utf-8")
        self._save = (rp._PATCH_YML, rp._PATCH_CONFIG, rp._index_file, dict(rp._MEM))
        rp._PATCH_YML = self.pyml
        rp._PATCH_CONFIG = self.d / "patch_config.yml"
        rp._index_file = lambda: self.d / "patch-index.json"
        rp._MEM.update(key=None, idx=None)
        self.running = False
        self._run = cmd._running
        cmd._running = lambda: self.running
        cmd._buf.update(serial=None, state=None, disk=None, dirty=False)
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda name: None

    def tearDown(self):
        rp._PATCH_YML, rp._PATCH_CONFIG, rp._index_file, mem = self._save
        rp._MEM.update(mem)
        cmd._running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)


class Index(Base):
    def test_duplicate_toplevel_hash_preserved(self):
        # safe_load would collapse the two PPU-aaaa nodes (losing 5 patches); the node walk keeps both.
        descs = {p["desc"] for p in rp.patches_for(_S)}
        self.assertIn("Unlock FPS", descs)
        self.assertIn("Extra Patch", descs)          # from the SECOND PPU-aaaa block

    def test_duplicate_anchor_tolerated(self):
        # &v329 is redefined; a naive Composer raises. We just need a clean parse.
        self.assertTrue(rp.load_index())             # non-empty -> parsed without error

    def test_version_kept_as_string(self):
        p = next(p for p in rp.patches_for(_S) if p["desc"] == "Unlock FPS")
        vers = [t["version"] for t in p["targets"]]
        self.assertEqual(vers, ["01.00"])
        self.assertIsInstance(vers[0], str)

    def test_anchor_alias_resolved_in_cfg(self):
        p = next(p for p in rp.patches_for(_S) if p["desc"] == "Aspect Ratio")
        opts, dflt, is_long = rp.value_options(p["cfg"]["Aspect Ratio"])
        labels = [l for l, _ in opts]
        self.assertEqual(labels, ["32:9", "21:9", "4:3"])
        self.assertFalse(is_long)
        self.assertTrue(rp._close(dflt, 3.555555555555556))   # via the *v329 alias

    def test_range_options(self):
        p = next(p for p in rp.patches_for(_S) if p["desc"] == "FOV")
        opts, dflt, is_long = rp.value_options(p["cfg"]["FOV"])
        nums = [n for _, n in opts]
        self.assertTrue(rp._close(dflt, 0.75))
        self.assertTrue(any(rp._close(n, 0.1) for n in nums))   # min present
        self.assertTrue(any(rp._close(n, 1.0) for n in nums))   # max present
        self.assertTrue(any(rp._close(n, 0.75) for n in nums))  # default present

    def test_other_game_isolated(self):
        self.assertEqual([p["desc"] for p in rp.patches_for(_OTHER)], ["Other Game Patch"])
        self.assertEqual(rp.patches_for("BLUS00000"), [])       # unknown serial -> none

    def test_index_disk_cache_roundtrip(self):
        rp.load_index()
        self.assertTrue((self.d / "patch-index.json").is_file())
        rp._MEM.update(key=None, idx=None)                      # force a fresh read from disk
        self.assertIn(_S, rp.load_index())

    def test_close_distinguishes_tiny_values(self):
        # H1: the old max(1.0,...) floor merged sub-1 values; 2e-37 vs 4e-37 must be distinct.
        self.assertFalse(rp._close(4e-37, 2e-37))
        self.assertTrue(rp._close(2e-37, 2e-37))
        self.assertTrue(rp._close(2.4, 2.4 + 1e-12))           # still tolerant of float drift

    def test_tiny_sentinel_enum_written(self):
        # H1 end-to-end: picking 'Vertical' (4e-37) must actually write a Configurable Values block.
        p = next(p for p in rp.patches_for(_UC) if p["desc"] == "Split Screen")
        opts, dflt, _l = rp.value_options(p["cfg"]["Screen Type"])
        self.assertEqual([l for l, _ in opts], ["Horizontal", "Vertical"])
        self.assertTrue(rp._close(dflt, 2e-37))

    def test_hex_config_param_dropped(self):
        # H2: a hex-bit-pattern default is non-parseable -> no picker options (enable-only).
        p = next(p for p in rp.patches_for(_UC) if p["desc"] == "Hex FOV")
        opts, dflt, _l = rp.value_options(p["cfg"]["FOV"])
        self.assertEqual(opts, [])
        self.assertIsNone(dflt)

    def test_config_root_path(self):
        # C1: patch_config.yml lives at the config ROOT, not the patches/ subdir.
        self.assertEqual(self._save[1], Path.home() / ".config/rpcs3/patch_config.yml")
        self.assertNotIn("patches", self._save[1].parent.name)

    def test_reads_unquoted_version_keys_as_strings(self):
        # C2: RPCS3 writes version keys UNQUOTED; safe_load would float-collapse them.
        rp._PATCH_CONFIG.write_text(
            "PPU-aaaa:\n  Unlock FPS:\n    Demon's Souls:\n      BLUS30443:\n"
            "        01.00:\n          Enabled: true\n", encoding="utf-8")
        cfg = rp.read_config()
        ver_keys = list(cfg["PPU-aaaa"]["Unlock FPS"]["Demon's Souls"]["BLUS30443"].keys())
        self.assertEqual(ver_keys, ["01.00"])
        self.assertIsInstance(ver_keys[0], str)
        # and a save must not corrupt that string key to 1.0
        rp.write_config(cfg)
        self.assertIn("'01.00':", rp._PATCH_CONFIG.read_text())
        self.assertNotIn("1.0:", rp._PATCH_CONFIG.read_text())

    def test_build_index_none_on_missing_file(self):
        # L2: a read failure returns None (not {}), so load_index won't cache an empty index.
        self.assertIsNone(rp._build_index(self.d / "does-not-exist.yml"))


class Render(Base):
    def test_groups_layout(self):
        g = cmd._patch_get(_S)
        self.assertTrue(g["exists"] and g["buffered"])
        titles = [grp["title"] for grp in g["groups"]]
        self.assertIn("Patches", titles)             # simple toggles cluster
        self.assertIn("Aspect Ratio", titles)        # configurable patch -> own group
        self.assertIn("HUD", titles)                 # Group alternatives cluster
        hud = next(grp for grp in g["groups"] if grp["title"] == "HUD")
        self.assertIn("Alternatives", hud["note"])
        ar = next(grp for grp in g["groups"] if grp["title"] == "Aspect Ratio")
        labels = [s["label"] for s in ar["settings"]]
        self.assertEqual(labels[0], "Enabled")       # own-group enable row reads "Enabled"
        self.assertEqual(labels[1], "Value")         # single-param value row reads "Value"

    def test_empty_state_note(self):
        g = cmd._patch_get("BLUS00000")              # valid-shape serial, no patches
        self.assertEqual(g["groups"], [])
        self.assertIn("No patches", g["note"])

    def test_bad_serial_rejected(self):
        with self.assertRaises(RpcError):
            cmd._patch_get("not-a-serial")


class SaveLoad(Base):
    def _enable(self, desc, on=True):
        cmd._patch_set({"titleid": _S, "key": f"en::{desc}", "value": 1 if on else 0})

    def test_enable_writes_config(self):
        self._enable("Unlock FPS")
        self.assertEqual(cmd._patch_save(_S), {"saved": True})
        data = rp.read_config()
        node = data["PPU-aaaa"]["Unlock FPS"]["Demon's Souls"]["BLUS30443"]
        self.assertEqual(list(node.keys()), ["01.00"])          # string version key
        self.assertEqual(node["01.00"], {"Enabled": True})

    def test_configurable_value_bare_number(self):
        self._enable("Aspect Ratio")
        opts, _d, _l = rp.value_options(
            next(p for p in rp.patches_for(_S) if p["desc"] == "Aspect Ratio")["cfg"]["Aspect Ratio"])
        idx = next(i for i, (l, n) in enumerate(opts) if l == "21:9")
        cmd._patch_set({"titleid": _S, "key": "cv::Aspect Ratio::Aspect Ratio", "value": idx})
        cmd._patch_save(_S)
        leaf = rp.read_config()["PPU-aaaa"]["Aspect Ratio"]["Demon's Souls"]["BLUS30443"]["01.00"]
        self.assertTrue(leaf["Enabled"])
        v = leaf["Configurable Values"]["Aspect Ratio"]
        self.assertNotIsInstance(v, str)                        # bare number
        self.assertTrue(rp._close(v, 2.4))

    def test_default_value_omits_config_block(self):
        self._enable("Aspect Ratio")                            # value left at default (32:9)
        cmd._patch_save(_S)
        leaf = rp.read_config()["PPU-aaaa"]["Aspect Ratio"]["Demon's Souls"]["BLUS30443"]["01.00"]
        self.assertEqual(leaf, {"Enabled": True})               # no Configurable Values at default

    def test_disable_prunes_but_keeps_others(self):
        self._enable("Unlock FPS")
        self._enable("Aspect Ratio")
        cmd._patch_save(_S)
        self._enable("Unlock FPS", on=False)
        cmd._patch_save(_S)
        node = rp.read_config()["PPU-aaaa"]
        self.assertNotIn("Unlock FPS", node)                    # pruned
        self.assertIn("Aspect Ratio", node)                     # preserved

    def test_other_game_config_preserved(self):
        # pre-seed patch_config with another game's entry (as RPCS3 would), then write ours
        rp.write_config({"PPU-bbbb": {"Other Game Patch": {"Other": {_OTHER: {"02.00": {"Enabled": True}}}}}})
        self._enable("Unlock FPS")
        cmd._patch_save(_S)
        data = rp.read_config()
        self.assertIn("PPU-aaaa", data)                         # ours added
        self.assertTrue(data["PPU-bbbb"]["Other Game Patch"]["Other"][_OTHER]["02.00"]["Enabled"])

    def test_reload_reflects_saved_state(self):
        self._enable("Unlock FPS")
        cmd._patch_save(_S)
        cmd._reload(_S)
        self.assertTrue(cmd._buf["disk"]["Unlock FPS"]["enabled"])

    def test_legacy_scalar_enabled_read(self):
        rp.write_config({"PPU-aaaa": {"Unlock FPS": {"Demon's Souls": {"BLUS30443": {"01.00": True}}}}})
        g = cmd._patch_get(_S)
        row = next(s for grp in g["groups"] for s in grp["settings"] if s["key"] == "en::Unlock FPS")
        self.assertEqual(row["value"], 1)                       # legacy `<ver>: true` -> enabled

    def test_offlist_current_value_noop(self):
        # a config value not among options is preserved as "(current: …)" and selecting it is a no-op
        rp.write_config({"PPU-aaaa": {"Aspect Ratio": {"Demon's Souls": {"BLUS30443":
            {"01.00": {"Enabled": True, "Configurable Values": {"Aspect Ratio": 1.777}}}}}}})
        g = cmd._patch_get(_S)
        row = next(s for grp in g["groups"] for s in grp["settings"]
                   if s["key"] == "cv::Aspect Ratio::Aspect Ratio")
        self.assertTrue(row["options"][-1].startswith("(current:"))
        self.assertEqual(row["value"], len(row["options"]) - 1)

    def test_running_guard(self):
        self.running = True
        with self.assertRaises(RpcError):
            cmd._patch_set({"titleid": _S, "key": "en::Unlock FPS", "value": 1})
        with self.assertRaises(RpcError):
            cmd._patch_save(_S)

    def test_cancel_reverts(self):
        self._enable("Unlock FPS")
        self.assertTrue(cmd._buf["dirty"])
        cmd._patch_cancel(_S)
        self.assertFalse(cmd._buf["dirty"])
        self.assertFalse(cmd._buf["state"]["Unlock FPS"]["enabled"])

    def _ar_index(self, label):
        opts, _d, _l = rp.value_options(
            next(p for p in rp.patches_for(_S) if p["desc"] == "Aspect Ratio")["cfg"]["Aspect Ratio"])
        return next(i for i, (l, n) in enumerate(opts) if l == label)

    def test_value_change_while_disabled_not_dirty(self):
        # L1: setting a value on an Off patch must NOT mark dirty (it is never written).
        cmd._patch_get(_S)                                     # load buffer
        cmd._patch_set({"titleid": _S, "key": "cv::Aspect Ratio::Aspect Ratio",
                        "value": self._ar_index("21:9")})
        self.assertFalse(cmd._buf["dirty"])                    # Aspect Ratio is still disabled
        self.assertEqual(cmd._patch_save(_S), {"saved": False})
        self.assertFalse(rp._PATCH_CONFIG.exists())            # no stray empty file created

    def test_pick_value_off_then_enable_persists(self):
        # L1 REGRESSION GUARD: pick a value while Off, THEN enable, THEN save -> value survives
        # (a naive dirty-gated _ensure would reload on the enable and discard the pick).
        cmd._patch_get(_S)
        cmd._patch_set({"titleid": _S, "key": "cv::Aspect Ratio::Aspect Ratio",
                        "value": self._ar_index("21:9")})       # while Off (non-dirty)
        cmd._patch_set({"titleid": _S, "key": "en::Aspect Ratio", "value": 1})  # then enable
        cmd._patch_save(_S)
        leaf = rp.read_config()["PPU-aaaa"]["Aspect Ratio"]["Demon's Souls"]["BLUS30443"]["01.00"]
        self.assertTrue(rp._close(leaf["Configurable Values"]["Aspect Ratio"], 2.4))

    def test_hex_value_preserved_on_unrelated_save(self):
        # H2 residual: a hex-param value RPCS3's Patch Manager set must survive a MAD save of an
        # UNRELATED patch for that serial (apply_state merges, doesn't overwrite the leaf).
        rp.write_config({"PPU-cccc": {"Hex FOV": {"UC3": {_UC: {"01.00":
            {"Enabled": True, "Configurable Values": {"FOV": 123.0}}}}}}})
        cmd._patch_set({"titleid": _UC, "key": "en::Split Screen", "value": 1})   # unrelated toggle
        cmd._patch_save(_UC)
        leaf = rp.read_config()["PPU-cccc"]["Hex FOV"]["UC3"][_UC]["01.00"]
        self.assertEqual(leaf.get("Configurable Values", {}).get("FOV"), 123.0)   # preserved

    def test_hex_patch_enable_writes_no_null(self):
        # H2: enabling a hex-configurable patch writes only Enabled, never `FOV: null`.
        cmd._patch_set({"titleid": _UC, "key": "en::Hex FOV", "value": 1})
        cmd._patch_save(_UC)
        leaf = rp.read_config()["PPU-cccc"]["Hex FOV"]["UC3"][_UC]["01.00"]
        self.assertEqual(leaf, {"Enabled": True})              # no Configurable Values / null
        self.assertNotIn("null", rp._PATCH_CONFIG.read_text())

    def test_tiny_sentinel_value_persists(self):
        # H1 end-to-end: picking Vertical (4e-37) is written (not merged into the default).
        cmd._patch_set({"titleid": _UC, "key": "en::Split Screen", "value": 1})
        opts, _d, _l = rp.value_options(
            next(p for p in rp.patches_for(_UC) if p["desc"] == "Split Screen")["cfg"]["Screen Type"])
        vidx = next(i for i, (l, n) in enumerate(opts) if l == "Vertical")
        cmd._patch_set({"titleid": _UC, "key": "cv::Split Screen::Screen Type", "value": vidx})
        cmd._patch_save(_UC)
        leaf = rp.read_config()["PPU-cccc"]["Split Screen"]["UC3"][_UC]["01.00"]
        self.assertTrue(rp._close(leaf["Configurable Values"]["Screen Type"], 4e-37))

    def test_unparseable_config_refused(self):
        # M1: a present-but-unparseable patch_config.yml must NOT be overwritten (data loss).
        rp._PATCH_CONFIG.write_text("PPU-x:\n  : : bad\n   tabs\tand junk\n", encoding="utf-8")
        self.assertIsNone(rp.read_config())                    # parse failure signalled as None
        self._enable("Unlock FPS")
        with self.assertRaises(RpcError):
            cmd._patch_save(_S)


if __name__ == "__main__":
    unittest.main()
