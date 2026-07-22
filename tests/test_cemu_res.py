"""Cemu (Wii U) handheld resolution: detect resolution graphic packs (dynamic), the transient
launch rail (switch the pack preset handheld, restore on exit, revert-if-unchanged, gated), and the
On-the-go per-game RPC (dynamic list + localpolicy preset store).

Run: python3 -m unittest tests.test_cemu_res -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import cemu_res, proc_guard
from lib.madsrv import cemu_games
from lib.madsrv import cemu_packs_cmds as cp
from lib.madsrv import cemu_res_cmds, rpc  # noqa: F401 (registers cemures.*)

_A = "0005000010111100"                 # has an enabled Resolution pack (stored 4K)
_B = "0005000010222200"                 # only a non-resolution pack -> excluded
_C_SHORT = "5000010333300"              # short tid in rules.txt -> must zfill to 16
_C = "0005000010333300"
_D = "0005000010444400"                 # a Resolution pack but DISABLED -> excluded
_E = "0005000010555500"                 # a Resolution pack whose group is UNNAMED (empty category)

_RES = ("\n[Preset]\nname = 640x360\ncategory = Resolution\n"
        "\n[Preset]\nname = 1280x720 (HD, Default)\ncategory = Resolution\n"
        "\n[Preset]\nname = 3840x2160 (4K)\ncategory = Resolution\n")
# WxH presets with NO `category=` -> the unnamed ("") option-group. The bug dropped these (a falsy
# `if not group` check); they must now surface.
_RES_UNNAMED = ("\n[Preset]\nname = 640x360\n"
                "\n[Preset]\nname = 1280x720\n"
                "\n[Preset]\nname = 3840x2160\n")

_SETTINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<content>
    <fullscreen>true</fullscreen>
    <GraphicPack>
        <Entry filename="graphicPacks/GameA_Res/rules.txt">
            <Preset>
                <category>Resolution</category>
                <preset>3840x2160 (4K)</preset>
            </Preset>
        </Entry>
        <Entry filename="graphicPacks/GameB_WS/rules.txt"/>
        <Entry filename="graphicPacks/GameC_Res/rules.txt"/>
        <Entry filename="graphicPacks/GameD_Res/rules.txt" disabled="true"/>
        <Entry filename="graphicPacks/GameE_Res/rules.txt"/>
    </GraphicPack>
</content>
"""


def _pack(root: Path, folder: str, titleids: str, name: str, path: str, presets: str = "") -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / "rules.txt").write_text(
        f'[Definition]\ntitleIds = {titleids}\nname = {name}\npath = "{path}"\nversion = 6\n' + presets,
        encoding="utf-8")


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.data = self.d / "data"
        self.cfg = self.d / "config"
        (self.cfg / "gameProfiles").mkdir(parents=True)
        gp = self.data / "graphicPacks"
        gp.mkdir(parents=True)
        _pack(gp, "GameA_Res", _A, "Resolution", "Game A/Graphics", _RES)
        _pack(gp, "GameB_WS", _B, "Widescreen", "Game B/Graphics")               # no resolution group
        _pack(gp, "GameC_Res", _C_SHORT, "Resolution", "Game C/Graphics", _RES)  # short tid
        _pack(gp, "GameD_Res", _D, "Resolution", "Game D/Graphics", _RES)        # disabled in settings
        _pack(gp, "GameE_Res", _E, "Resolution", "Game E/Graphics", _RES_UNNAMED)  # unnamed res group
        roms = self.d / "roms"; roms.mkdir()
        for t in ("A", "B", "C", "D", "E"):
            (roms / f"{t}.wua").write_bytes(b"x")
        self.romA = str(roms / "A.wua")
        (self.data / "title_list_cache.xml").write_text(
            "<title_list_cache>"
            + "".join(f'<title titleId="{t}" app_type="80000000"><name>Game {n}</name>'
                      f'<path>{roms / (n + ".wua")}</path></title>'
                      for t, n in ((_A, "A"), (_B, "B"), (_C, "C"), (_D, "D"), (_E, "E")))
            + "</title_list_cache>", encoding="utf-8")
        self.settings = self.cfg / "settings.xml"
        self.settings.write_text(_SETTINGS, encoding="utf-8")
        self._save = (cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS, cemu_res._RES_DIR)
        cemu_games._DATA_DIR = self.data
        cemu_games._CONFIG_DIR = self.cfg
        cp._SETTINGS = self.settings
        cemu_res._RES_DIR = self.d / "markers"
        cp._BUF.update({"ctx": None, "disk": None, "entries": None})
        self._run = proc_guard.emulator_running
        proc_guard.emulator_running = lambda name: False
        # deck_state is a SHARED module (cemu_res.deck_state is lib.deck_state); the rail tests
        # monkeypatch is_handheld/resolve_force + cemu_res.load_merged, so save + restore them here
        # to avoid leaking a fake handheld state into every later test's dock detection.
        self._patched = (cemu_res.deck_state.is_handheld, cemu_res.deck_state.resolve_force,
                         cemu_res.load_merged)
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None

    def tearDown(self):
        cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS, cemu_res._RES_DIR = self._save
        cemu_res.deck_state.is_handheld, cemu_res.deck_state.resolve_force, cemu_res.load_merged = self._patched
        proc_guard.emulator_running = self._run
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _preset(self, filename="graphicPacks/GameA_Res/rules.txt", group="Resolution"):
        e = cp._find(cp._parse_graphicpack(self.settings.read_text()), filename)
        return cp._entry_preset(e, group) if e else None


# ── detection (dynamic) ─────────────────────────────────────────────────────────
class Detect(_Base):
    def test_resolution_titleids_dynamic_filter(self):
        tids = cp.resolution_titleids()
        # A (named res pack) + C (short tid zfilled) + E (UNNAMED-category res group -- the bug fix);
        # B non-res + D disabled excluded.
        self.assertEqual(set(tids), {_A, _C, _E})
        self.assertEqual(tids[_A]["group"], "Resolution")
        self.assertEqual(tids[_A]["presets"],
                         ["640x360", "1280x720 (HD, Default)", "3840x2160 (4K)"])
        self.assertEqual(tids[_E]["group"], "")           # unnamed group kept, not dropped
        self.assertEqual(tids[_E]["presets"], ["640x360", "1280x720", "3840x2160"])

    def test_resolution_group_predicate(self):
        # Key by unique FILENAME, not name: the fixture has FOUR packs named "Resolution" (GameA/C/D
        # with real resolution groups + GameE with an UNNAMED group), so `{pk["name"]: pk}` kept
        # whichever _scan_packs()'s rglob returned LAST -- an order-dependent (filesystem) flake that
        # made packs["Resolution"] sometimes resolve to GameE (group "") and fail the assert.
        packs = {pk["filename"]: pk for pk in cp._scan_packs()}
        self.assertEqual(
            cp.resolution_group(packs["graphicPacks/GameA_Res/rules.txt"]), "Resolution")
        self.assertIsNone(
            cp.resolution_group(packs["graphicPacks/GameB_WS/rules.txt"]))   # no resolution group

    def test_resolution_group_prefers_main_over_gamepad(self):
        # a multi-screen pack: handheld should lower the TV/main render, not the GamePad screen
        pk = {"options": {"Gamepad Resolution": ["1x", "2x"], "TV Resolution": ["1x", "2x"],
                          "Shadows": ["Low", "High"]}}
        self.assertEqual(cp.resolution_group(pk), "TV Resolution")
        self.assertEqual(cp.resolution_group({"options": {"TV Resolution": ["1x"],
                                                          "Resolution": ["1x"]}}), "Resolution")

    def test_resolution_group_unnamed_by_wxh(self):
        # a pack that leaves the resolution group unnamed (WxH presets, no <category>)
        self.assertEqual(cp.resolution_group({"options": {"": ["640x360", "1280x720", "3840x2160"]}}), "")
        # a non-resolution pack (aspect ratios / quality) is NOT matched by the WxH fallback
        self.assertIsNone(cp.resolution_group({"options": {"Aspect Ratio": ["16:9", "21:9"],
                                                           "Shadows": ["Low", "High"]}}))

    def test_default_handheld_preset_720p_or_below(self):
        # canonical 1280x720 (any suffix) wins outright
        self.assertEqual(cp.default_handheld_preset(
            ["640x360", "1280x720 (HD, Default)", "3840x2160 (4K)"]), "1280x720 (HD, Default)")
        # no exact 720p -> the largest height at or below 720
        self.assertEqual(cp.default_handheld_preset(["640x360", "960x540", "1920x1080"]), "960x540")
        # nothing at or below 720p -> None (caller leaves the game unchanged)
        self.assertIsNone(cp.default_handheld_preset(["1920x1080 (Native)", "3840x2160"]))
        # equal height -> smallest width (avoid an ultrawide)
        self.assertEqual(cp.default_handheld_preset(["1720x720", "1600x720"]), "1600x720")


# ── transient rail ──────────────────────────────────────────────────────────────
class Rail(_Base):
    def _handheld(self, on=True, enabled=True, wiiu=True, preset="1280x720 (HD, Default)"):
        cemu_res.load_merged = lambda: {
            "handheld": {"enabled": enabled},
            "systems": {"wiiu": {"handheld": {"enabled": wiiu,
                        "res_presets": {_A: preset} if preset else {}}}}}
        cemu_res.deck_state.is_handheld = lambda *a, **k: on
        cemu_res.deck_state.resolve_force = lambda *a, **k: ("handheld" if on else "docked")

    def test_apply_then_restore_roundtrip(self):
        self._handheld()
        cemu_res.apply(self.romA)
        self.assertEqual(self._preset(), "1280x720 (HD, Default)")   # lowered
        self.assertTrue(list((self.d / "markers").glob("*.json")))
        self.assertIn("GameB_WS", self.settings.read_text())          # other entries byte-preserved
        cemu_res.sweep_all()
        self.assertEqual(self._preset(), "3840x2160 (4K)")            # restored
        self.assertFalse(list((self.d / "markers").glob("*.json")))   # marker cleaned

    def test_revert_if_unchanged_preserves_user_edit(self):
        self._handheld()
        cemu_res.apply(self.romA)
        # user (or Cemu) changes the preset underneath -> sweep must NOT clobber it
        self.settings.write_text(self.settings.read_text().replace(
            "<preset>1280x720 (HD, Default)</preset>", "<preset>640x360</preset>"))
        cemu_res.sweep_all()
        self.assertEqual(self._preset(), "640x360")

    def test_docked_is_noop(self):
        self._handheld(on=False)
        before = self.settings.read_text()
        cemu_res.apply(self.romA)
        self.assertEqual(self.settings.read_text(), before)
        self.assertFalse(list((self.d / "markers").glob("*.json")))

    def test_feature_off_and_not_participating_noop(self):
        for kw in ({"enabled": False}, {"wiiu": False}):
            self._handheld(**kw)
            before = self.settings.read_text()
            cemu_res.apply(self.romA)
            self.assertEqual(self.settings.read_text(), before, kw)

    def test_unset_applies_720p_default(self):
        self._handheld(preset="")                        # participating + handheld + nothing stored
        cemu_res.apply(self.romA)
        self.assertEqual(self._preset(), "1280x720 (HD, Default)")   # auto 720p default applied
        self.assertTrue(list((self.d / "markers").glob("*.json")))   # marker written (restores on exit)

    def test_keep_sentinel_is_noop(self):
        self._handheld(preset=cp.KEEP)                   # explicit Keep -> leave the resting preset
        before = self.settings.read_text()
        cemu_res.apply(self.romA)
        self.assertEqual(self.settings.read_text(), before)
        self.assertFalse(list((self.d / "markers").glob("*.json")))

    def test_no_upshift_when_resting_below_720p(self):
        # game already renders below 720p docked (640x360); the auto 720p default must NOT raise it.
        self._handheld(preset="")                        # unset -> auto-default
        self.settings.write_text(self.settings.read_text().replace(
            "<preset>3840x2160 (4K)</preset>", "<preset>640x360</preset>"))
        before = self.settings.read_text()
        cemu_res.apply(self.romA)
        self.assertEqual(self.settings.read_text(), before)   # no upshift
        self.assertFalse(list((self.d / "markers").glob("*.json")))

    def test_stale_stored_falls_back_to_default(self):
        # a stored preset no longer in the pack (stale) -> the auto downshift default, NOT a silent
        # no-op (so the rail matches what the picker pre-selects).
        self._handheld(preset="9999x9999")               # resting is 4K -> 720p is a downshift
        cemu_res.apply(self.romA)
        self.assertEqual(self._preset(), "1280x720 (HD, Default)")

    def test_no_res_pack_game_noop(self):
        self._handheld()
        cemu_res.load_merged = lambda: {"handheld": {"enabled": True}, "systems": {"wiiu":
            {"handheld": {"enabled": True, "res_presets": {_B: "640x360"}}}}}
        before = self.settings.read_text()
        cemu_res.apply(str(self.d / "roms" / "B.wua"))   # GameB has no resolution pack
        self.assertEqual(self.settings.read_text(), before)

    def test_orphan_self_heals_on_next_sweep(self):
        self._handheld()
        cemu_res.apply(self.romA)                        # marker + lowered, "crash" (no restore)
        cemu_res.sweep_all()                             # a later launch's start-sweep heals it
        self.assertEqual(self._preset(), "3840x2160 (4K)")

    def test_marker_kept_when_revert_write_fails(self):
        self._handheld()
        cemu_res.apply(self.romA)
        orig = cemu_res.cfgutil.atomic_write
        cemu_res.cfgutil.atomic_write = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            cemu_res.sweep_all()                         # revert write fails
        finally:
            cemu_res.cfgutil.atomic_write = orig
        self.assertTrue(list((self.d / "markers").glob("*.json")))    # marker KEPT for retry
        self.assertEqual(self._preset(), "1280x720 (HD, Default)")    # not yet reverted
        cemu_res.sweep_all()                             # a later (working) sweep heals it
        self.assertEqual(self._preset(), "3840x2160 (4K)")
        self.assertFalse(list((self.d / "markers").glob("*.json")))


# ── On-the-go RPC (dynamic list + localpolicy store) ────────────────────────────
class RPC(_Base):
    def setUp(self):
        super().setUp()
        import lib.policy as policy
        self._local = policy.LOCAL
        policy.LOCAL = self.d / "controller-policy.local.toml"

    def tearDown(self):
        import lib.policy as policy
        policy.LOCAL = self._local
        super().tearDown()

    def _call(self, name, **p):
        return rpc._METHODS[name][0](p)

    def test_games_dynamic_filter(self):
        got = {g["titleid"] for g in self._call("cemures.games")["games"]}
        self.assertEqual(got, {_A, _C, _E})              # incl. E (unnamed-category res group)

    def test_get_lists_presets_plus_keep(self):
        opts = self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]["options"]
        self.assertEqual(opts[0], "Keep (no change)")
        self.assertIn("640x360", opts)
        self.assertIn("1280x720", opts[2])               # "(Default)" tag stripped for display

    def test_set_get_roundtrip_localpolicy(self):
        import lib.policy as policy
        self._call("cemures.set", titleid=_A, key="preset", value=2)   # idx 2 -> presets[1]
        row = self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]
        self.assertEqual(row["value"], 2)
        # stored the FULL preset name, and the rail reads the same store
        self.assertEqual(
            policy.load_merged()["systems"]["wiiu"]["handheld"]["res_presets"][_A],
            "1280x720 (HD, Default)")
        self._call("cemures.set", titleid=_A, key="preset", value=0)   # Keep -> store the sentinel
        self.assertEqual(
            policy.load_merged()["systems"]["wiiu"]["handheld"]["res_presets"][_A], cp.KEEP)
        self.assertEqual(self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]["value"], 0)

    def test_get_unset_preselects_720p(self):
        # nothing stored + resting is 4K -> the picker pre-selects the 720p downshift (a genuine lower)
        row = self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]
        self.assertEqual(row["value"], 2)                # opts = [Keep, 640x360, 1280x720, 3840x2160]

    def test_get_keep_when_resting_at_or_below_720p(self):
        # game already at/below 720p docked -> the picker shows Keep, matching the rail's no-op
        self.settings.write_text(self.settings.read_text().replace(
            "<preset>3840x2160 (4K)</preset>", "<preset>640x360</preset>"))
        row = self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]
        self.assertEqual(row["value"], 0)

    def test_get_stale_stored_preselects_default(self):
        # a stale stored name (not in the pack) -> picker falls to the 720p default, agreeing with apply
        cemu_res_cmds._store(_A, "9999x9999")
        row = self._call("cemures.get", titleid=_A)["groups"][0]["settings"][0]
        self.assertEqual(row["value"], 2)


if __name__ == "__main__":
    unittest.main()
