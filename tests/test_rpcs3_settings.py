"""Tests for rpcs3_settings — the full RPCS3 (PS3) global settings tree (5 buffered
category namespaces over config.yml) + the rpcs3_engine YAML codec + the PS3 tile shell.

Hermetic: a fixture config.yml is generated from the module's own GROUPS (covers every
key) plus targeted overrides; the engine is pointed at a temp copy. A reality-check runs
only when the live ~/.config/rpcs3/config.yml is present (confirms the installed build
honors every offered key — the same discipline as test_pcsx2_settings)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lib.madsrv import cfgutil, rpc, rpcs3_engine
from lib.madsrv import rpcs3_settings as R
from lib.madsrv.rpc import RpcError


def _default_token(it):
    t = it["type"]
    if t == "bool":
        return "false"
    if t == "enum":
        return it["options_stored"][0]
    return str(it.get("min", 0))  # int


def _build_fixture(overrides=None, drop=()):
    """Render a config.yml covering every GROUPS key (+ overrides {(section,key): value},
    minus any (section,key) in `drop`). Flat 2-space indent (the Vulkan sub-block keys are
    written flat under Video: — yaml_read matches any indented key in the block)."""
    overrides = overrides or {}
    sections = {}
    for _ns, (_title, groups) in R.CATEGORIES.items():
        for g in groups:
            for it in g["items"]:
                sec, key = it["section"], it.get("name", it["key"])
                if (sec, key) in drop:
                    continue
                sections.setdefault(sec, {}).setdefault(key, _default_token(it))
    for (sec, key), val in overrides.items():
        if (sec, key) in drop:
            continue
        sections.setdefault(sec, {})[key] = val
    out = []
    for sec, kv in sections.items():
        out.append(f"{sec}:")
        out.extend(f"  {k}: {v}" for k, v in kv.items())
    return "\n".join(out) + "\n"


_OVERRIDES = {
    ("Video", "Renderer"): "Vulkan",
    ("Video", "Resolution"): "1920x1080",
    ("Video", "Anisotropic Filter Override"): "0",
    ("Video", "Write Color Buffers"): "false",
    ("Core", "SPU Block Size"): "Mega",
    ("Core", "Clocks scale"): "100",
    ("Audio", "Master Volume"): "100",
    ("System", "Language"): "English (US)",
}


class Rpcs3SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.yml"
        self.cfg.write_text(_build_fixture(_OVERRIDES), newline="")
        self._orig_file, self._orig_running = R._FILE, R._running
        self.running = False
        R._FILE = self.cfg
        R._running = lambda: self.running
        R._buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        import lib.staterev as sr
        self._orig_bump = sr.bump
        self.bumps = []
        sr.bump = lambda name: self.bumps.append(name)

    def tearDown(self):
        R._FILE, R._running = self._orig_file, self._orig_running
        import lib.staterev as sr
        sr.bump = self._orig_bump
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, ns):
        return {s["key"]: s for g in R._get(ns)["groups"] for s in g["settings"]}

    def _disk(self, sec, key):
        return cfgutil.yaml_read(self.cfg.read_text(newline=""), sec, key)

    # ── structure ────────────────────────────────────────────────────────────
    def test_five_categories_and_rpc_methods(self):
        self.assertEqual(list(R.CATEGORIES), ["rpcs3cpu", "rpcs3gpu", "rpcs3aud", "rpcs3adv", "rpcs3emu"])
        for ns in R.CATEGORIES:
            for verb in ("get", "set", "save", "cancel"):
                self.assertIn(f"{ns}.{verb}", rpc._METHODS)

    def test_group_schema_wellformed(self):
        for ns, (_t, groups) in R.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    self.assertEqual(it["file"], R._F)
                    if it["type"] == "enum":
                        self.assertEqual(it.get("write_mode"), "option", it["key"])
                        self.assertEqual(len(it["options_display"]), len(it["options_stored"]), it["key"])

    def test_get_buffered_payload_and_values(self):
        pay = R._get("rpcs3gpu")
        self.assertTrue(pay["buffered"])
        self.assertTrue(pay["exists"])
        self.assertFalse(pay["dirty"])
        rows = {s["key"]: s for g in pay["groups"] for s in g["settings"]}
        self.assertEqual(rows["Renderer"]["value"], 0)               # Vulkan -> index 0
        self.assertEqual(rows["Renderer"]["options"][:3], ["Vulkan", "OpenGL", "Null"])
        self.assertEqual(rows["Anisotropic Filter Override"]["value"], 0)  # "0" -> index 0 (Automatic)

    def test_get_skips_absent_keys(self):
        # A key not present in config.yml is simply not offered (version-safe).
        self.cfg.write_text(_build_fixture(_OVERRIDES, drop=[("Video", "Write Color Buffers")]), newline="")
        self.assertNotIn("Write Color Buffers", self._rows("rpcs3gpu"))
        self.assertIn("Renderer", self._rows("rpcs3gpu"))

    # ── set / save (byte-preserving) ─────────────────────────────────────────
    def test_set_stages_and_save_writes_enum_bool_int(self):
        R._get("rpcs3gpu")
        R._set("rpcs3gpu", {"key": "Renderer", "value": 1})            # Vulkan -> OpenGL
        R._set("rpcs3gpu", {"key": "Write Color Buffers", "value": 1})  # false -> true
        R._set("rpcs3gpu", {"key": "Anisotropic Filter Override", "value": 4})  # "0" -> "16"
        self.assertTrue(R._buf["dirty"])
        self.assertEqual(self._disk("Video", "Renderer"), "Vulkan")    # unchanged pre-save
        before = self.cfg.read_text(newline="")
        res = R._save("rpcs3gpu")
        self.assertTrue(res["saved"])
        after = self.cfg.read_text(newline="")
        self.assertEqual(self._disk("Video", "Renderer"), "OpenGL")
        self.assertEqual(self._disk("Video", "Write Color Buffers"), "true")
        self.assertEqual(self._disk("Video", "Anisotropic Filter Override"), "16")
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))  # byte-preserving (no lines added)
        self.assertIn("config", self.bumps)

    def test_set_returns_precise_dirty(self):
        R._get("rpcs3cpu")
        on = R._set("rpcs3cpu", {"key": "Clocks scale", "value": 150})
        self.assertTrue(on["dirty"])
        back = R._set("rpcs3cpu", {"key": "Clocks scale", "value": 100})   # back to disk value
        self.assertFalse(back["dirty"])

    def test_running_guard_refuses_set_and_save(self):
        R._get("rpcs3gpu")
        self.running = True
        with self.assertRaises(RpcError):
            R._set("rpcs3gpu", {"key": "Renderer", "value": 1})
        with self.assertRaises(RpcError):
            R._save("rpcs3gpu")

    def test_save_replays_onto_fresh_read(self):
        # An external write to ANOTHER key between load and save must survive.
        R._get("rpcs3gpu")
        R._set("rpcs3gpu", {"key": "Renderer", "value": 1})
        t = cfgutil.yaml_replace(self.cfg.read_text(newline=""), "Video", "Frame limit", "60")
        self.cfg.write_text(t, newline="")                 # foreign edit while staged
        R._save("rpcs3gpu")
        self.assertEqual(self._disk("Video", "Renderer"), "OpenGL")   # our edit applied
        self.assertEqual(self._disk("Video", "Frame limit"), "60")    # foreign edit preserved

    def test_cancel_discards(self):
        R._get("rpcs3aud")
        R._set("rpcs3aud", {"key": "Master Volume", "value": 42})
        R._cancel("rpcs3aud")
        self.assertEqual(self._rows("rpcs3aud")["Master Volume"]["value"], 100)
        self.assertEqual(self._disk("Audio", "Master Volume"), "100")

    def test_enum_preserves_unknown_ondisk_value(self):
        # A stored token outside the curated list is preserved (prepended at index 0).
        t = cfgutil.yaml_replace(self.cfg.read_text(newline=""), "Video", "Renderer", "SomeFutureRenderer")
        self.cfg.write_text(t, newline="")
        row = self._rows("rpcs3gpu")["Renderer"]
        self.assertEqual(row["value"], 0)
        self.assertEqual(row["options"][0], "SomeFutureRenderer")

    # ── engine codec unit checks ─────────────────────────────────────────────
    def test_write_item_enokey_on_absent(self):
        text = "Video:\n  Renderer: Vulkan\n"
        it = {"key": "Nonexistent Key", "type": "bool", "section": "Video", "file": R._F}
        with self.assertRaises(RpcError):
            rpcs3_engine.write_item(text, it, 1)

    # ── enum token correctness (non-circular) ────────────────────────────────
    def test_every_enum_token_round_trips(self):
        """Every option of every enum must WRITE its exact stored token and READ BACK to
        the same index — proves display/stored pairing AND that each token stores verbatim
        (the fixture-seed default can't hide a mistyped non-default token)."""
        for ns, (_t, groups) in R.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    if it["type"] != "enum":
                        continue
                    self.cfg.write_text(_build_fixture(_OVERRIDES), newline="")
                    R._buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
                    for i, tok in enumerate(it["options_stored"]):
                        R._get(ns)
                        R._set(ns, {"key": it["key"], "value": i})
                        R._save(ns)
                        self.assertEqual(self._disk(it["section"], it["key"]), tok,
                                         f"{it['key']} idx {i}: stored token")
                        self.assertEqual(self._rows(ns)[it["key"]]["value"], i,
                                         f"{it['key']} idx {i}: read-back index")

    def test_live_enum_tokens_are_known(self):
        """Each enum's CURRENT on-disk token in the live config.yml must be one we offer —
        a mistyped token would leave the user's real value outside our list."""
        live = Path.home() / ".config/rpcs3/config.yml"
        if not live.is_file():
            self.skipTest("no live RPCS3 config.yml")
        text = live.read_text(newline="")
        for _ns, (_t, groups) in R.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    if it["type"] != "enum":
                        continue
                    tok = cfgutil.yaml_read(text, it["section"], it["key"])
                    if tok is None:
                        continue
                    self.assertIn(tok, it["options_stored"],
                                  f"{it['key']}: live token {tok!r} not in options_stored")

    # ── byte-preservation on realistic structures ────────────────────────────
    def test_save_touches_only_the_target_line(self):
        R._get("rpcs3gpu")
        R._set("rpcs3gpu", {"key": "Renderer", "value": 1})    # Vulkan -> OpenGL
        before = self.cfg.read_text(newline="").splitlines()
        R._save("rpcs3gpu")
        after = self.cfg.read_text(newline="").splitlines()
        self.assertEqual(len(before), len(after))
        diffs = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(diffs, [("  Renderer: Vulkan", "  Renderer: OpenGL")])  # exactly one line

    def test_nested_vulkan_keys_write_in_place(self):
        """The two Vulkan-subblock keys sit at DEEPER indent under Video:; editing one must
        preserve its 4-space indent and leave the sub-block header + a post-sub-block Video
        key untouched."""
        cfg = ("Core:\n  PPU Decoder: Recompiler (LLVM)\n"
               "Video:\n"
               "  Renderer: Vulkan\n"
               "  Multithreaded RSX: false\n"
               "  Vulkan:\n"
               "    Asynchronous Texture Streaming: false\n"
               "    Asynchronous Queue Scheduler: Safe\n"
               "  Write Color Buffers: false\n")
        self.cfg.write_text(cfg, newline="")
        R._buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        R._get("rpcs3gpu")
        R._set("rpcs3gpu", {"key": "Asynchronous Texture Streaming", "value": 1})  # nested bool
        R._set("rpcs3gpu", {"key": "Write Color Buffers", "value": 1})             # post-sub-block
        R._save("rpcs3gpu")
        out = self.cfg.read_text(newline="")
        self.assertIn("    Asynchronous Texture Streaming: true\n", out)   # 4-space indent kept
        self.assertIn("  Write Color Buffers: true\n", out)
        self.assertIn("    Asynchronous Queue Scheduler: Safe\n", out)     # neighbour untouched
        self.assertIn("  Vulkan:\n", out)                                  # sub-block header intact
        self.assertEqual(len(out.splitlines()), len(cfg.splitlines()))

    def test_no_trailing_newline_preserved_editing_final_key(self):
        """The live config.yml ends with NO trailing newline; editing the final key of the
        final section must not add one."""
        cfg = "Core:\n  Clocks scale: 100\nAudio:\n  Master Volume: 100"   # no trailing \n
        self.cfg.write_text(cfg, newline="")
        R._buf.update({"ns": None, "text": None, "disk": None, "dirty": False, "edits": []})
        R._get("rpcs3aud")
        R._set("rpcs3aud", {"key": "Master Volume", "value": 80})
        R._save("rpcs3aud")
        out = self.cfg.read_text(newline="")
        self.assertEqual(out, "Core:\n  Clocks scale: 100\nAudio:\n  Master Volume: 80")
        self.assertFalse(out.endswith("\n"))

    # ── live reality-check (only when the real config.yml is present) ─────────
    def test_live_config_offers_every_key(self):
        live = Path.home() / ".config/rpcs3/config.yml"
        if not live.is_file():
            self.skipTest("no live RPCS3 config.yml")
        copy = self.dir / "live.yml"
        shutil.copy2(live, copy)
        R._FILE = copy
        for ns, (_t, groups) in R.CATEGORIES.items():
            got = {s["key"] for g in R._get(ns)["groups"] for s in g["settings"]}
            want = {it["key"] for g in groups for it in g["items"]}
            missing = want - got
            self.assertFalse(missing, f"{ns}: live config.yml missing offered keys {missing}")


class Rpcs3TileTest(unittest.TestCase):
    def test_rpcs3_tile_is_grouped_input_plus_settings(self):
        from lib.madsrv import standalones_cmds as S
        entry = next(s for s in S.STANDALONES if s["key"] == "rpcs3")
        self.assertNotIn("settings_ns", entry)                  # bespoke tree, not the single-Settings path
        secs = S._sections_for(entry, ["ps3"])
        top = [x["label"] for x in secs]
        self.assertEqual(top, ["Input", "Settings", "Per-game"])
        by = {x["label"]: x for x in secs}
        self.assertEqual(by["Input"]["kind"], "group")
        self.assertEqual([r["label"] for r in by["Input"]["sections"]],
                         ["Device visibility", "Mappings", "Pads → players"])
        self.assertEqual([r["arg"] for r in by["Settings"]["sections"]],
                         ["rpcs3cpu", "rpcs3gpu", "rpcs3aud", "rpcs3adv", "rpcs3emu"])
        self.assertTrue(all(r["kind"] == "settings" for r in by["Settings"]["sections"]))
        pg = by["Per-game"]
        self.assertEqual(pg["kind"], "settings_pergame_menu")
        self.assertEqual(pg["arg"], "rpcs3pg")
        # Mappings is a DIRECT leaf (not a 1-child Input group -> no redundant submenu).
        # Manage patches (P4) is a game-scoped settings page over patch.yml -> patch_config.yml.
        self.assertEqual([(r["label"], r["kind"], r["arg"]) for r in pg["sections"]],
                         [("Settings", "pergame_settings", "rpcs3pg"),
                          ("Mappings", "pergame_input", "rpcs3pgin"),
                          ("Manage patches", "pergame_settings", "rpcs3patch")])


if __name__ == "__main__":
    unittest.main()
