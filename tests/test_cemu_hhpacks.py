"""On-the-go > Wii U > Per-game: the handheld graphic-pack pages and the browser feed behind them.

The rail itself (apply/revert) is covered in tests/test_cemu_res.py; this is the UI layer -- the row
model, the inherit slot, what the Resolution page owns, and which leaves a given game hides.

Run: python3 -m unittest tests.test_cemu_hhpacks -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lib import cemu_hhpacks, cemu_res
from lib.madsrv import cemu_games
from lib.madsrv import cemu_hh_cmds, cemu_hhpacks_cmds, cemu_res_cmds, rpc  # noqa: F401 (registers the methods)
from lib.madsrv import cemu_packs_cmds as cp

_A = "0005000010111100"          # res pack (enabled, 4K) + a multi-option Graphics pack
_B = "0005000010222200"          # one Mods pack, no resolution pack
_C = "0005000010333300"          # no packs at all

_RES = ("\n[Preset]\nname = 640x360\ncategory = Resolution\n"
        "\n[Preset]\nname = 1280x720 (HD, Default)\ncategory = Resolution\n"
        "\n[Preset]\nname = 3840x2160 (4K)\ncategory = Resolution\n")
# One pack carrying the resolution group AND a second group: the Resolution page must take only the
# first, leaving the rest of the pack editable here.
_RES_PLUS = _RES + "\n[Preset]\nname = On\ncategory = Bloom\n\n[Preset]\nname = Off\ncategory = Bloom\n"
_MULTI = ("\n[Preset]\ncategory = Shadows\nname = High\ndefault = 1\n"
          "\n[Preset]\ncategory = Shadows\nname = Low\n"
          "\n[Preset]\ncategory = AA\nname = FXAA\ndefault = 1\n"
          "\n[Preset]\ncategory = AA\nname = Off\n")

_SETTINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<content>
    <GraphicPack>
        <Entry filename="graphicPacks/A_Res/rules.txt">
            <Preset>
                <category>Resolution</category>
                <preset>3840x2160 (4K)</preset>
            </Preset>
        </Entry>
        <Entry filename="graphicPacks/A_Multi/rules.txt">
            <Preset>
                <category>Shadows</category>
                <preset>Low</preset>
            </Preset>
        </Entry>
        <Entry filename="graphicPacks/B_Mod/rules.txt" disabled="true"/>
        <Entry filename="graphicPacks/B_Res/rules.txt" disabled="true"/>
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
        self.data, self.cfg = self.d / "data", self.d / "config"
        (self.cfg / "gameProfiles").mkdir(parents=True)
        gp = self.data / "graphicPacks"
        gp.mkdir(parents=True)
        _pack(gp, "A_Res", _A, "Resolution", "Game A/Graphics", _RES_PLUS)
        _pack(gp, "A_Multi", _A, "Extra effects", "Game A/Graphics", _MULTI)
        _pack(gp, "B_Mod", _B, "60FPS", "Game B/Mods")
        # A resolution pack that is OFF docked: the handheld pages can switch it on, which flips
        # who owns this title's resolution for that launch only.
        _pack(gp, "B_Res", _B, "Resolution", "Game B/Graphics", _RES)
        roms = self.d / "roms"
        roms.mkdir()
        for n in ("A", "B", "C"):
            (roms / f"{n}.wua").write_bytes(b"x")
        (self.data / "title_list_cache.xml").write_text(
            "<title_list_cache>"
            + "".join(f'<title titleId="{t}" app_type="80000000"><name>Game {n}</name>'
                      f'<path>{roms / (n + ".wua")}</path></title>'
                      for t, n in ((_A, "A"), (_B, "B"), (_C, "C")))
            + "</title_list_cache>", encoding="utf-8")
        self.settings = self.cfg / "settings.xml"
        self.settings.write_text(_SETTINGS, encoding="utf-8")

        self._save = (cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS, cemu_res._RES_DIR)
        cemu_games._DATA_DIR = self.data
        cemu_games._CONFIG_DIR = self.cfg
        cp._SETTINGS = self.settings
        cemu_res._RES_DIR = self.d / "markers"
        import lib.policy as policy
        self._local = policy.LOCAL
        policy.LOCAL = self.d / "local.toml"
        import lib.staterev as sr
        self._bump = sr.bump
        sr.bump = lambda n: None
        # deck_state and load_merged are SHARED module attributes; the rail tests below swap them,
        # so save and restore here rather than leaking a fake handheld state into later tests.
        self._patched = (cemu_res.deck_state.is_handheld, cemu_res.deck_state.resolve_force,
                         cemu_res.load_merged)

    def tearDown(self):
        cemu_games._DATA_DIR, cemu_games._CONFIG_DIR, cp._SETTINGS, cemu_res._RES_DIR = self._save
        (cemu_res.deck_state.is_handheld, cemu_res.deck_state.resolve_force,
         cemu_res.load_merged) = self._patched
        import lib.policy as policy
        policy.LOCAL = self._local
        import lib.staterev as sr
        sr.bump = self._bump
        shutil.rmtree(self.d, ignore_errors=True)

    def _call(self, name, **p):
        return rpc._METHODS[name][0](p)

    def _page(self, cat, tid=_A):
        return self._call(f"cemu_hhpacks_{cp.catkey(cat)}.get", titleid=tid)

    def _group(self, cat, title, tid=_A):
        return next(g for g in self._page(cat, tid)["groups"] if g["title"] == title)

    def _row(self, cat, title, label, tid=_A):
        return next(s for s in self._group(cat, title, tid)["settings"] if s["label"] == label)


class Rows(_Base):
    def test_every_row_rests_on_same_as_docked_showing_the_docked_value(self):
        row = self._row("Graphics", "Extra effects", "Shadows")
        self.assertEqual(row["value"], 0)
        # settings.xml holds Shadows=Low for this pack, so THAT is what "same as docked" must name --
        # not the pack's own default (High), which is what the game would render only if unset.
        self.assertEqual(row["options"][0], "Same as docked (Low)")
        # a group with no <Preset> falls back to the pack default
        self.assertEqual(self._row("Graphics", "Extra effects", "AA")["options"][0],
                         "Same as docked (FXAA)")

    def test_enable_row_is_three_way_and_names_the_docked_state(self):
        row = self._row("Graphics", "Extra effects", "Handheld")
        self.assertEqual(row["options"], ["Same as docked (on)", "On", "Off"])
        self.assertEqual(row["value"], 0)
        off = self._row("Mods", "60FPS", "Handheld", tid=_B)      # disabled="true" in settings.xml
        self.assertEqual(off["options"][0], "Same as docked (off)")

    def test_a_pack_off_docked_says_so(self):
        self.assertIn("Off in your docked setup", self._group("Mods", "60FPS", tid=_B)["note"])

    def test_resolution_group_is_hidden_but_the_rest_of_the_pack_is_not(self):
        labels = [s["label"] for s in self._group("Graphics", "Resolution")["settings"]]
        self.assertNotIn("Resolution", labels)        # owned by the Resolution page
        self.assertIn("Bloom", labels)                # the same pack's other option stays editable
        self.assertIn("Handheld", labels)             # and it can still be switched off
        self.assertIn("Resolution page", self._group("Graphics", "Resolution")["note"])

    def test_page_never_claims_the_emulator_must_be_closed(self):
        # Unlike its docked twin this writes MAD's own policy, so a running Cemu is irrelevant. The
        # "running" flag renders "close it before changing these", which would be a lie here.
        self.assertFalse(self._page("Graphics")["running"])

    def test_empty_category_says_so(self):
        page = self._page("Cheats")
        self.assertEqual(page["groups"], [])
        self.assertIn("no graphic packs in this category", page["note"])


class Writes(_Base):
    def test_pick_and_clear_an_option(self):
        key = "graphicPacks/A_Multi/rules.txt" + cp._SEP + "Shadows"
        self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=1)   # slot 1 -> presets[0]
        self.assertEqual(cemu_hhpacks.for_title(_A)["graphicPacks/A_Multi/rules.txt"]["options"],
                         {"Shadows": "High"})
        self.assertEqual(self._row("Graphics", "Extra effects", "Shadows")["value"], 1)
        self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=0)   # slot 0 -> clear
        self.assertEqual(cemu_hhpacks.for_title(_A), {})

    def test_stores_the_raw_preset_name_not_the_displayed_one(self):
        # The Resolution pack's Bloom group is fine, but a "(Default)"-tagged name is the trap: the
        # row DISPLAYS it stripped and the rail matches what Cemu writes, so the store must keep the
        # full name.
        key = "graphicPacks/A_Res/rules.txt" + cp._SEP + "Resolution"
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Res/rules.txt", "Resolution",
                                "1280x720 (HD, Default)")
        self.assertEqual(
            cemu_hhpacks.for_title(_A)["graphicPacks/A_Res/rules.txt"]["options"]["Resolution"],
            "1280x720 (HD, Default)")
        self.assertTrue(key)          # the row itself is hidden; this pins the STORE's fidelity

    def test_force_on_off_and_back_to_inherit(self):
        key = "graphicPacks/A_Multi/rules.txt" + cp._SEP + cemu_hhpacks_cmds._ENABLED_KEY
        for idx, expected in ((1, True), (2, False)):
            self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=idx)
            self.assertIs(
                cemu_hhpacks.for_title(_A)["graphicPacks/A_Multi/rules.txt"]["enabled"], expected)
            self.assertEqual(self._row("Graphics", "Extra effects", "Handheld")["value"], idx)
        self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=0)
        self.assertEqual(cemu_hhpacks.for_title(_A), {})

    def test_nothing_here_touches_cemus_own_config(self):
        before = self.settings.read_bytes()
        key = "graphicPacks/A_Multi/rules.txt" + cp._SEP + "Shadows"
        self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=2)
        self._call("cemu_hhpacks_graphics.set", titleid=_A, key="graphicPacks/A_Multi/rules.txt"
                   + cp._SEP + cemu_hhpacks_cmds._ENABLED_KEY, value=2)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_bad_input_is_refused(self):
        from lib.madsrv.rpc import RpcError
        key = "graphicPacks/A_Multi/rules.txt" + cp._SEP + "Shadows"
        for kwargs in ({"titleid": "nope", "key": key, "value": 1},
                       {"titleid": _A, "key": "no-separator", "value": 1},
                       {"titleid": _A, "key": "graphicPacks/Gone/rules.txt" + cp._SEP + "X", "value": 1},
                       {"titleid": _A, "key": key, "value": 99}):
            with self.assertRaises(RpcError):
                self._call("cemu_hhpacks_graphics.set", **kwargs)


class Browser(_Base):
    def _games(self):
        return {g["titleid"]: g for g in self._call("cemuhh.games")["games"]}

    def test_lists_every_installed_game_for_the_wiiu_media(self):
        got = self._games()
        self.assertEqual(set(got), {_A, _B, _C})
        self.assertEqual(self._call("cemuhh.games")["system"], "wiiu")
        self.assertTrue(all("stem" in g for g in got.values()))

    def test_hides_only_what_a_game_cannot_use(self):
        got = self._games()
        self.assertEqual(set(got[_A]["hide"]),
                         {"packs_enhancements", "packs_mods", "packs_workarounds",
                          "packs_cheats", "packs_other"})          # A has Graphics packs + a res pack
        self.assertIn("res", got[_B]["hide"])                      # B's only pack is not a res pack
        self.assertNotIn("packs_mods", got[_B]["hide"])
        self.assertEqual(set(got[_C]["hide"]), {"packs", "res"})   # C has nothing

    def test_input_is_never_hidden(self):
        # It is the guarantee that at least one leaf survives: an all-hidden game pushes an empty
        # grid, which the panel renders with the standalone empty state.
        for g in self._games().values():
            self.assertNotIn("input", g.get("hide", []))

    def test_badge_reads_handheld_state_only(self):
        got = self._games()
        self.assertFalse(got[_A]["override"])
        # the automatic 720p cap must NOT badge: every game with a res pack gets it, so badging it
        # would mark the whole library as customised
        self.assertEqual(got[_A]["summary"], "")
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Multi/rules.txt", "Shadows", "High")
        got = self._games()
        self.assertTrue(got[_A]["override"])
        self.assertIn("packs", got[_A]["summary"])

    def test_forcing_the_res_pack_off_hides_the_resolution_leaf(self):
        self.assertNotIn("res", self._games()[_A]["hide"])
        cemu_hhpacks.set_enabled(_A, "graphicPacks/A_Res/rules.txt", False)
        # with its only resolution pack off for handheld, there is nothing for that page to drive
        self.assertIn("res", self._games()[_A]["hide"])


class ReviewRegressions(_Base):
    """One test per defect the pre-ship adversarial review confirmed. Each fails on the code as it
    stood when the review ran."""

    def _plan(self, tid=_A, res_preset=None):
        """The rail's plan for this title, with the policy the pages actually wrote."""
        from lib.policy import load_merged
        pol = load_merged()
        if res_preset is not None:
            pol.setdefault("systems", {}).setdefault("wiiu", {}).setdefault("handheld", {}) \
               .setdefault("res_presets", {})[tid] = res_preset
        return cemu_res._plan(pol, tid, cp.read_entries())

    def _groups_for(self, plan, filename):
        rec = next((r for r in plan if cp._norm(r["filename"]) == cp._norm(filename)), None)
        return {g["group"]: g["applied"] for g in rec["groups"]} if rec else {}

    def test_the_resolution_page_wins_over_a_stale_hidden_pack_override(self):
        # The packs page hides the owner's resolution group, but a value can be stored while the row
        # is briefly visible (force the pack off, the row returns, pick something, set it back).
        # Nothing prunes it, and it used to take the record slot and DROP the Resolution page's pick.
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Res/rules.txt", "Resolution", "640x360")
        plan = self._plan(res_preset="1280x720 (HD, Default)")
        self.assertEqual(self._groups_for(plan, "graphicPacks/A_Res/rules.txt").get("Resolution"),
                         "1280x720 (HD, Default)")

    def test_writing_the_owned_resolution_group_is_refused_and_clears_the_stale_value(self):
        from lib.madsrv.rpc import RpcError
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Res/rules.txt", "Resolution", "640x360")
        key = "graphicPacks/A_Res/rules.txt" + cp._SEP + "Resolution"
        with self.assertRaises(RpcError):
            self._call("cemu_hhpacks_graphics.set", titleid=_A, key=key, value=1)
        self.assertEqual(cemu_hhpacks.for_title(_A), {})     # and the orphan is gone

    def test_a_stale_preset_or_group_is_not_applied(self):
        # A pack update can rename an option out from under a stored override. The row then shows
        # "same as docked" (or vanishes), so the user cannot clear it -- applying it anyway would
        # write a value the pack does not offer.
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Multi/rules.txt", "Shadows", "Ultra")
        cemu_hhpacks.set_option(_A, "graphicPacks/A_Multi/rules.txt", "GoneGroup", "X")
        self.assertEqual(self._groups_for(self._plan(), "graphicPacks/A_Multi/rules.txt"), {})

    def test_resolution_page_resolves_the_handheld_owner_not_the_docked_one(self):
        # B's resolution pack is off docked and switched ON for handheld: the browser shows the
        # Resolution leaf, so the page behind it must work rather than deny the pack exists.
        cemu_hhpacks.set_enabled(_B, "graphicPacks/B_Res/rules.txt", True)
        page = self._call("cemures.get", titleid=_B)
        self.assertTrue(page["groups"], "the leaf is shown, so the page must not be empty")
        self.assertIn("640x360", page["groups"][0]["settings"][0]["options"])
        self._call("cemures.set", titleid=_B, key="preset", value=1)   # and a preset can be stored

    def test_no_resolution_badge_while_the_leaf_is_hidden(self):
        self._call("cemures.set", titleid=_A, key="preset", value=1)
        cemu_hhpacks.set_enabled(_A, "graphicPacks/A_Res/rules.txt", False)
        g = next(x for x in self._call("cemuhh.games")["games"] if x["titleid"] == _A)
        self.assertIn("res", g["hide"])
        self.assertNotIn("resolution", g["summary"])   # promising what the rail will not do

    def test_a_pack_the_rail_created_reads_as_off_docked(self):
        # With a marker outstanding, docked_baseline is what the pages trust. A pack the rail
        # invented was reported as enabled docked, so the row read "same as docked (on)" and
        # clearing the override did the opposite of what it promised.
        cemu_hhpacks.set_enabled(_A, "graphicPacks/A_New/rules.txt", True)
        _pack(self.data / "graphicPacks", "A_New", _A, "Brand new", "Game A/Graphics", _MULTI)
        from lib.policy import load_merged
        cemu_res._RES_DIR.mkdir(parents=True, exist_ok=True)
        recs = cemu_res._plan(load_merged(), _A, cp.read_entries())
        import json
        cemu_res._marker(self.settings).write_text(json.dumps(
            {"v": 2, "path": str(self.settings), "tid": _A, "recs": recs}), encoding="utf-8")
        base = cemu_res.docked_baseline(self.settings)
        self.assertTrue(base["graphicPacks/A_New/rules.txt"]["disabled"])
        self.assertEqual(self._row("Graphics", "Brand new", "Handheld")["options"][0],
                         "Same as docked (off)")


class SelfClosingBlock(_Base):
    def test_an_empty_self_closing_graphicpack_node_is_filled_not_duplicated(self):
        # Cemu writes the container unconditionally, so a profile with no enabled packs leaves
        # `<GraphicPack/>`. The block regex used to miss it, so the rail appended a SECOND node:
        # Cemu then read the first (empty) one and MAD the second, forever.
        self.settings.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<content>\n    <GraphicPack/>\n</content>\n',
            encoding="utf-8")
        self.assertEqual(cp.read_entries(), [])
        cemu_hhpacks.set_enabled(_A, "graphicPacks/A_Multi/rules.txt", True)
        from lib.policy import load_merged
        cemu_res.deck_state.is_handheld = lambda *a, **k: True
        cemu_res.deck_state.resolve_force = lambda *a, **k: "handheld"
        pol = load_merged()
        pol.setdefault("handheld", {})["enabled"] = True
        pol["systems"]["wiiu"]["handheld"]["enabled"] = True
        cemu_res.load_merged = lambda: pol          # tearDown restores it
        cemu_res.apply(str(self.d / "roms" / "A.wua"))
        text = self.settings.read_text()
        self.assertEqual(text.count("<GraphicPack"), 1, text)
        self.assertIn("A_Multi", text)


class PolicyEscaping(unittest.TestCase):
    def test_every_control_char_survives_a_policy_round_trip(self):
        # Pack paths and option-group names are third-party text and now land in TOML KEYS. One
        # unescaped control char makes the whole file unparseable, load() returns {} and the next
        # save persists the wipe of every override in it.
        import tempfile as tf
        from lib import localpolicy
        bad = "".join(chr(c) for c in list(range(0x00, 0x09)) + [0x0b, 0x0c]
                      + list(range(0x0e, 0x20)) + [0x7f])
        with tf.TemporaryDirectory() as d:
            p = Path(d) / "local.toml"
            localpolicy.dump(p, {"systems": {"wiiu": {"handheld": {"packs": {
                _A: {f"graphicPacks/{bad}/rules.txt": {"options": {bad: bad}}}}}}}})
            back = localpolicy.load(p)
            self.assertTrue(back, "the file must still parse")
            row = back["systems"]["wiiu"]["handheld"]["packs"][_A][f"graphicPacks/{bad}/rules.txt"]
            self.assertEqual(row["options"][bad], bad)


if __name__ == "__main__":
    unittest.main()
