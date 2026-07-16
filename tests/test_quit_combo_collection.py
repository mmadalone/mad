"""Per-collection quit combos.

A custom collection can carry its own hold-to-quit combo (stored under scope
`collection-<display-name>`) that OVERRIDES the game's system/per-game combo, and
that arms a quit watcher even for plain RetroArch games. The resolver that picks the
winning collection at launch is `es_collections.narrowest_combo_collection`, and the
storage round-trips through `localpolicy` even for special-character names like
`Pew-Pew-Pew!!!` -> `[quit_combo."collection-Pew-Pew-Pew!!!"]`.

Run:  python3 -m unittest tests.test_quit_combo_collection -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lib import es_collections as colls


class NarrowestComboCollection(unittest.TestCase):
    """narrowest_combo_collection: fewest-members winner AMONG the collections that (a)
    contain the ROM and (b) actually have a combo set. Deterministic via monkeypatched
    membership/size (bypasses the .cfg + lru_cache, like the other launcher tests)."""

    def setUp(self):
        self._orig = (colls.enabled_collections, colls.rom_in_collection, colls.members)
        # sizes: spiderman(2) < superheroes(3) < Fighter(5) < Pew(88)
        self._sizes = {"Fighter": 5, "spiderman": 2, "superheroes": 3, "Pew-Pew-Pew!!!": 88}
        self._member_of = {
            "/roms/spidey.mugen": {"Fighter", "spiderman", "superheroes"},
            "/roms/duckhunt.zip": {"Pew-Pew-Pew!!!"},
            "/roms/lonely.zip": set(),
        }
        colls.enabled_collections = lambda: ("Fighter", "spiderman", "superheroes", "Pew-Pew-Pew!!!")
        colls.rom_in_collection = lambda rom, name: name in self._member_of.get(rom, set())
        colls.members = lambda name: frozenset(range(self._sizes[name]))

    def tearDown(self):
        (colls.enabled_collections, colls.rom_in_collection, colls.members) = self._orig

    def test_narrowest_among_combo_collections(self):
        # spidey is in Fighter+spiderman; both have a combo -> narrower spiderman(2) wins.
        qc = {"collection-Fighter": {"buttons": [1]}, "collection-spiderman": {"buttons": [2]}}
        self.assertEqual(colls.narrowest_combo_collection("/roms/spidey.mugen", qc), "spiderman")

    def test_only_combo_collections_are_candidates(self):
        # spidey is in 3 collections, but only the broad superheroes has a combo -> it wins.
        qc = {"collection-superheroes": {"buttons": [3]}}
        self.assertEqual(colls.narrowest_combo_collection("/roms/spidey.mugen", qc), "superheroes")

    def test_no_combo_anywhere_returns_none(self):
        self.assertIsNone(colls.narrowest_combo_collection("/roms/spidey.mugen", {}))

    def test_combo_set_but_rom_not_a_member(self):
        qc = {"collection-spiderman": {"buttons": [2]}}
        self.assertIsNone(colls.narrowest_combo_collection("/roms/duckhunt.zip", qc))

    def test_pew_special_char_name(self):
        qc = {"collection-Pew-Pew-Pew!!!": {"buttons": [276, 277]}}
        self.assertEqual(colls.narrowest_combo_collection("/roms/duckhunt.zip", qc), "Pew-Pew-Pew!!!")

    def test_rom_in_no_collection(self):
        qc = {"collection-Fighter": {"buttons": [1]}}
        self.assertIsNone(colls.narrowest_combo_collection("/roms/lonely.zip", qc))

    def test_scalar_combo_value_ignored(self):
        # A hand-mangled scalar (collection-Fighter = 5) is not a combo table -> skipped.
        qc = {"collection-Fighter": 5, "collection-spiderman": {"buttons": [2]}}
        self.assertEqual(colls.narrowest_combo_collection("/roms/spidey.mugen", qc), "spiderman")

    def test_empty_rom_and_bad_quit_combo(self):
        self.assertIsNone(colls.narrowest_combo_collection("", {"collection-Fighter": {"buttons": [1]}}))
        self.assertIsNone(colls.narrowest_combo_collection("/roms/spidey.mugen", None))


class IsRetroarchSystem(unittest.TestCase):
    """is_retroarch_system tells a real RA system apart from a standalone that returned an
    empty quit_cmd because it OPTED OUT of the evdev watcher — so the game-start hook only
    arms `pkill retroarch` for actual RA games in a combo-collection (regression: an OpenBOR
    game in a combo-collection must NOT get the RA killer)."""

    def setUp(self):
        from lib import es_systems
        self._es = es_systems
        self._orig = es_systems.default_command

    def tearDown(self):
        self._es.default_command = self._orig

    def test_retroarch_core_is_true(self):
        self._es.default_command = lambda s, systems=None: "%EMULATOR_RETROARCH% -L core.so %ROM%"
        self.assertTrue(self._es.is_retroarch_system("nes"))

    def test_standalone_is_false(self):
        self._es.default_command = lambda s, systems=None: "/usr/bin/pcsx2 %ROM%"
        self.assertFalse(self._es.is_retroarch_system("ps2"))

    def test_opted_out_standalone_openbor_is_false(self):
        # OpenBOR: a real standalone command (no RA macro). quit_cmd may be "" (opt-out),
        # but it is NOT retroarch -> the hook must not arm pkill-retroarch for it.
        self._es.default_command = lambda s, systems=None: "/path/to/openbor %ROM%"
        self.assertFalse(self._es.is_retroarch_system("openbor"))

    def test_unknown_or_undefined_system_is_false(self):
        self._es.default_command = lambda s, systems=None: ""
        self.assertFalse(self._es.is_retroarch_system("bogus"))


class QuitComboCollectionStorage(unittest.TestCase):
    """The scope `collection-<name>` persists via localpolicy even when the name has
    TOML-unsafe characters — `_key()` quotes the dotted segment, and the write is
    byte-stable on re-dump (regression: a bad key would silently wipe ALL overrides)."""

    def setUp(self):
        from lib import staterev
        self._staterev = staterev
        self._orig_bump = staterev.bump
        staterev.bump = lambda *a, **k: None      # no side effects in the test

    def tearDown(self):
        self._staterev.bump = self._orig_bump

    def test_special_char_collection_scope_roundtrips_and_is_stable(self):
        import tempfile
        from lib import localpolicy
        data = {"quit_combo": {
            "buttons": [314, 315], "hold_sec": 1.0,
            "switch": {"buttons": [314, 315]},
            "lindbergh-vf5": {"buttons": [274]},
            "collection-Pew-Pew-Pew!!!": {"buttons": [276, 277]},
            "collection-racing": {"buttons": [300]},
        }}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "controller-policy.local.toml"
            localpolicy.dump(p, data)
            self.assertIn('[quit_combo."collection-Pew-Pew-Pew!!!"]', p.read_text())
            back = localpolicy.load(p)
            qc = back["quit_combo"]
            self.assertEqual(qc["collection-Pew-Pew-Pew!!!"]["buttons"], [276, 277])
            self.assertEqual(qc["collection-racing"]["buttons"], [300])
            self.assertEqual(qc["lindbergh-vf5"]["buttons"], [274])
            # Re-dumping the reloaded data is byte-identical (no key got dropped/re-mangled).
            text1 = p.read_text()
            localpolicy.dump(p, localpolicy.load(p))
            self.assertEqual(p.read_text(), text1)


if __name__ == "__main__":
    unittest.main()
