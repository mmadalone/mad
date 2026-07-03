"""Tests for retroarch_settings — the full categorized RetroArch GLOBAL settings
tree (7 namespaces), LIVE-SAVE via retroarch_cfg.set_global_option. Hermetic: a
fixture retroarch.cfg is generated from the module's own GROUPS (covers every
declared key) plus targeted value overrides, and retroarch_cfg is pointed at a
temp copy (RA_GLOBAL_CFG + the .mad-bak path). A reality-check runs only when the
live retroarch.cfg is present (confirms the installed RA honors every offered key)."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from lib import proc_guard
from lib import retroarch_cfg
from lib.madsrv import retroarch_cmds  # noqa: F401  (registers retroarch.* — collision check)
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import rpc

_EXPECTED_NS = ["raset_video", "raset_audio", "raset_latency", "raset_saves",
                "raset_osd", "raset_menu", "raset_input"]


def _default_token(it):
    t = it["type"]
    if t == "bool":
        return "false"
    if t == "resolution":                        # _eopt: stored value is the option string
        return it["options"][0]
    if t == "enum":
        return "0" if it.get("stored") == "index" else it["options"][0]
    if t == "float":
        return f"{float(it['min']):.6f}"
    return str(it["min"])  # int


# a few overrides so the get() mapping is exercised on real, non-default values
_OVERRIDES = {
    "video_driver": "glcore",            # opt-enum index 1
    "video_vsync": "true",
    "aspect_ratio_index": "22",          # idx-enum -> value 22 (Core provided)
    "menu_thumbnails": "3",              # idx-enum -> value 3 (Boxart)
    "audio_driver": "pulse",             # opt-enum index 1
    "audio_latency": "192",
    "audio_volume": "0.000000",
    "input_poll_type_behavior": "2",     # idx-enum -> value 2 (Late)
    "input_max_users": "8",
    "menu_driver": "rgui",               # opt-enum index 2
}


def _all_items():
    for _ns, (_t, groups) in rs.CATEGORIES.items():
        for g in groups:
            for it in g["items"]:
                yield it


def _build_fixture(overrides=None):
    """One `key = "token"` line per declared key (RA's exact serialization)."""
    overrides = overrides or {}
    lines = []
    for it in _all_items():
        tok = overrides.get(it["key"], _default_token(it))
        lines.append(f'{it["key"]} = "{tok}"')
    return "\n".join(lines) + "\n"


class RetroArchSettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "retroarch.cfg"
        self.cfg.write_text(_build_fixture(_OVERRIDES), newline="")
        # point retroarch_cfg at the temp copy (both the cfg and its backup path)
        self._orig_cfg = retroarch_cfg.RA_GLOBAL_CFG
        self._orig_bak = retroarch_cfg._GLOBAL_BAK
        retroarch_cfg.RA_GLOBAL_CFG = self.cfg
        retroarch_cfg._GLOBAL_BAK = self.dir / "retroarch.cfg.mad-bak"
        # emulator-running guard, monkeypatched
        self._orig_running = proc_guard.retroarch_running
        self.running = False
        proc_guard.retroarch_running = lambda: self.running

    def tearDown(self):
        retroarch_cfg.RA_GLOBAL_CFG = self._orig_cfg
        retroarch_cfg._GLOBAL_BAK = self._orig_bak
        proc_guard.retroarch_running = self._orig_running
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, ns):
        return {s["key"]: s for g in rs._get(ns)["groups"] for s in g["settings"]}

    def _disk(self, key):
        return retroarch_cfg.get_global_option(key)

    # ── structure ────────────────────────────────────────────────────────────
    def test_eight_categories_and_rpc_methods(self):
        self.assertEqual(list(rs.CATEGORIES), _EXPECTED_NS)
        for ns in rs.CATEGORIES:
            self.assertIn(f"{ns}.get", rpc._METHODS)
            self.assertIn(f"{ns}.set", rpc._METHODS)
            # live-save: no buffered save/cancel verbs
            self.assertNotIn(f"{ns}.save", rpc._METHODS)
            self.assertNotIn(f"{ns}.cancel", rpc._METHODS)

    def test_no_rpc_collision_with_retroarch_cmds(self):
        # the new namespaces must not shadow the existing retroarch.* surface
        for ns in rs.CATEGORIES:
            self.assertFalse(ns.startswith("retroarch."), ns)
        # the flat global-defaults page (retroarch.get/.set) was retired in Phase 4;
        # the live RetroArch input surface must still be registered.
        self.assertNotIn("retroarch.get", rpc._METHODS)
        self.assertNotIn("retroarch.set", rpc._METHODS)
        self.assertIn("retroarch.input_get", rpc._METHODS)

    def test_group_schema_is_wellformed(self):
        for ns, (_t, groups) in rs.CATEGORIES.items():
            self.assertTrue(groups, ns)
            for g in groups:
                for it in g["items"]:
                    if it["type"] == "enum":
                        self.assertTrue(it["options"], it["key"])
                    if it["type"] in ("int", "float"):
                        for k in ("min", "max", "step"):
                            self.assertIn(k, it, it["key"])
                        self.assertLessEqual(it["min"], it["max"], it["key"])

    def test_keys_are_unique_per_namespace(self):
        for ns, (_t, groups) in rs.CATEGORIES.items():
            keys = [it["key"] for g in groups for it in g["items"]]
            self.assertEqual(len(keys), len(set(keys)), f"dup key in {ns}")

    def test_no_input_bind_keys(self):
        bind = re.compile(r"(^input_player\d+_)|(_(btn|axis|mbtn)$)")
        offenders = [it["key"] for it in _all_items() if bind.search(it["key"])]
        self.assertEqual(offenders, [], f"input-bind keys leaked into settings: {offenders}")

    # ── get ──────────────────────────────────────────────────────────────────
    def test_all_categories_load_nonempty(self):
        for ns in rs.CATEGORIES:
            pay = rs._get(ns)
            self.assertTrue(pay["exists"])
            self.assertFalse(pay["running"])
            self.assertFalse(pay.get("buffered", False))   # live-save: not buffered
            self.assertTrue(pay["groups"], ns)
            for g in pay["groups"]:
                self.assertTrue(g["settings"], f"{ns}/{g['title']}")

    def test_get_values_and_types(self):
        v = self._rows("raset_video")
        self.assertEqual(v["video_driver"]["type"], "resolution")
        self.assertEqual(v["video_driver"]["value"], "glcore")   # resolution: stored string
        self.assertTrue(v["video_vsync"]["value"])               # "true"
        self.assertEqual(v["aspect_ratio_index"]["value"], 22)   # idx-enum
        self.assertGreaterEqual(len(v["aspect_ratio_index"]["options"]), 23)
        m = self._rows("raset_menu")
        self.assertEqual(m["menu_thumbnails"]["value"], 3)       # idx-enum -> Boxart
        self.assertEqual(m["menu_driver"]["value"], "rgui")      # resolution: stored string
        a = self._rows("raset_audio")
        self.assertEqual(a["audio_driver"]["value"], "pulse")    # resolution: stored string
        self.assertEqual(a["audio_latency"]["value"], 192)
        self.assertEqual(a["audio_volume"]["type"], "float")
        i = self._rows("raset_input")
        self.assertEqual(i["input_poll_type_behavior"]["value"], 2)  # Late
        self.assertEqual(i["input_max_users"]["value"], 8)

    def test_get_skips_absent_keys(self):
        # remove one key from the fixture -> it's no longer offered, sibling stays
        text = "".join(ln for ln in self.cfg.read_text(newline="").splitlines(keepends=True)
                       if not ln.startswith("fps_show ="))
        self.cfg.write_text(text, newline="")
        rows = self._rows("raset_osd")
        self.assertNotIn("fps_show", rows)
        self.assertIn("memory_show", rows)

    # ── set (live-save) ────────────────────────────────────────────────────────
    def test_set_bool_is_byte_preserving(self):
        before = self.cfg.read_text(newline="")
        res = rs._set("raset_video", {"key": "video_smooth", "value": "1"})
        self.assertTrue(res["value"])
        after = self.cfg.read_text(newline="")
        b, a = before.splitlines(), after.splitlines()
        self.assertEqual(len(b), len(a))
        diffs = [(x, y) for x, y in zip(b, a) if x != y]
        self.assertEqual(diffs, [('video_smooth = "false"', 'video_smooth = "true"')])

    def test_menu_swap_ok_cancel_present_and_roundtrips(self):
        # menu_swap moved here from the retired flat retroarch.get/.set page (Phase 4
        # cleanup); keep the specific typo-guard on the key + its bool round-trip.
        rows = self._rows("raset_input")
        self.assertIn("menu_swap_ok_cancel_buttons", rows, "menu_swap key missing from raset_input")
        self.assertEqual(rows["menu_swap_ok_cancel_buttons"]["type"], "bool")
        res = rs._set("raset_input", {"key": "menu_swap_ok_cancel_buttons", "value": "1"})
        self.assertTrue(res["value"])
        self.assertEqual(self._disk("menu_swap_ok_cancel_buttons"), "true")
        rs._set("raset_input", {"key": "menu_swap_ok_cancel_buttons", "value": "0"})
        self.assertEqual(self._disk("menu_swap_ok_cancel_buttons"), "false")

    def test_set_int_clamps_and_writes(self):
        rs._set("raset_input", {"key": "input_max_users", "value": "5"})
        self.assertEqual(self._disk("input_max_users"), "5")
        rs._set("raset_input", {"key": "input_max_users", "value": "99"})  # clamp to 16
        self.assertEqual(self._disk("input_max_users"), "16")

    def test_set_string_enum_roundtrip(self):
        # _eopt is type "resolution": the C++ sends the option STRING back, and it is
        # stored + echoed as that string (immune to option-list index shifts).
        res = rs._set("raset_audio", {"key": "audio_driver", "value": "alsa"})
        self.assertEqual(self._disk("audio_driver"), "alsa")
        self.assertEqual(res["value"], "alsa")

    def test_set_enum_index_roundtrip(self):
        res = rs._set("raset_video", {"key": "aspect_ratio_index", "value": "1"})  # 16:9
        self.assertEqual(self._disk("aspect_ratio_index"), "1")
        self.assertEqual(res["value"], 1)

    def test_set_float_six_decimals(self):
        res = rs._set("raset_video", {"key": "video_refresh_rate", "value": "60.0"})
        self.assertEqual(self._disk("video_refresh_rate"), "60.000000")
        self.assertEqual(res["value"], 60.0)

    def test_set_out_of_range_enum_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rs._set("raset_audio", {"key": "audio_driver", "value": "99"})

    def test_all_eidx_index_roundtrip(self):
        # every _eidx key stores the integer INDEX the C++ sends (locks the index
        # flavour against a dropped "stored":"index" marker or a reorder regression)
        for ns, (_t, groups) in rs.CATEGORIES.items():
            for g in groups:
                for it in g["items"]:
                    if it["type"] == "enum" and it.get("stored") == "index":
                        last = str(len(it["options"]) - 1)
                        rs._set(ns, {"key": it["key"], "value": last})
                        self.assertEqual(self._disk(it["key"]), last,
                                         f"{it['key']} must store the index")

    def test_eopt_option_tokens_locked(self):
        # lock the driver option strings so a typo (e.g. vulkan->vulcan) is caught
        self.assertEqual(rs._item_by_key("raset_video", "video_driver")["options"],
                         ["vulkan", "glcore", "gl"])
        self.assertEqual(rs._item_by_key("raset_audio", "audio_driver")["options"],
                         ["pipewire", "pulse", "alsathread", "alsa"])
        self.assertEqual(rs._item_by_key("raset_audio", "audio_resampler")["options"],
                         ["sinc", "CC", "nearest"])
        self.assertEqual(rs._item_by_key("raset_menu", "menu_driver")["options"],
                         ["ozone", "xmb", "rgui", "glui"])

    def test_resolution_immune_to_index_shift(self):
        # a non-curated on-disk value must not shift the write: setting a curated
        # STRING stores that string, never a stale index (Phase 1 review issue 1)
        retroarch_cfg.set_global_option("audio_driver", "sdl2")   # non-curated value
        rs._set("raset_audio", {"key": "audio_driver", "value": "alsa"})
        self.assertEqual(self._disk("audio_driver"), "alsa")

    # ── guards ──────────────────────────────────────────────────────────────
    def test_ebusy_guard_on_set(self):
        self.running = True
        with self.assertRaises(rpc.RpcError):
            rs._set("raset_video", {"key": "video_vsync", "value": "0"})
        # unchanged on disk
        self.assertEqual(self._disk("video_vsync"), "true")

    def test_unknown_key_rejected(self):
        with self.assertRaises(rpc.RpcError):
            rs._set("raset_video", {"key": "no_such_key", "value": "1"})

    def test_set_does_not_leak_across_namespaces(self):
        # a video key is not settable through the audio namespace
        with self.assertRaises(rpc.RpcError):
            rs._set("raset_audio", {"key": "video_vsync", "value": "1"})

    # ── reality-check against the live cfg (skipped if absent) ─────────────────
    def test_live_cfg_has_every_offered_key(self):
        live = self._orig_cfg
        if not live.is_file():
            self.skipTest("no live retroarch.cfg on this host")
        text = live.read_text(encoding="utf-8", errors="replace", newline="")
        present = set()
        for ln in text.splitlines():
            mm = re.match(r'\s*(\w+)\s*=', ln)
            if mm:
                present.add(mm.group(1))
        missing = sorted({it["key"] for it in _all_items()} - present)
        self.assertEqual(missing, [], f"keys offered but absent from live cfg: {missing}")


if __name__ == "__main__":
    unittest.main()
