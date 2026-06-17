"""
Tests for lib/es_systems_standalone.seed_standalone — the minimal custom_systems
generator for a no-EmuDeck install.

Uses the running ES-DE's real bundled es_systems.xml as the Cat-A source and the
committed Cat-B template. The keystone test proves a system OMITTED from the custom
file still resolves from bundled (so the minimal file strands nothing).

Run:  python3 -m unittest tests.test_standalone_systems -v
"""
from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from lib import es_systems_standalone as S
from lib.esde_paths import bundled_es_systems

BUNDLED = bundled_es_systems()
TEMPLATE = Path(__file__).resolve().parent.parent / "data" / "standalone" / "es_systems.mad-special.xml"
LAUNCHERS = Path("/X/launchers")   # sentinel — proves %MAD_LAUNCHERS% is resolved


@unittest.skipUnless(BUNDLED.is_file(), "bundled es_systems.xml not available")
class StandaloneSeed(unittest.TestCase):
    def _seed(self):
        custom = Path(tempfile.mkdtemp()) / "custom_systems" / "es_systems.xml"
        res = S.seed_standalone(custom, bundled_path=BUNDLED,
                                template_path=TEMPLATE, launchers=LAUNCHERS)
        return custom, res

    def test_creates_file(self):
        custom, res = self._seed()
        self.assertTrue(res.get("created"))
        self.assertTrue(custom.is_file())
        ET.parse(custom)   # parses as XML

    def test_cat_a_wrapped_with_binders(self):
        custom, _ = self._seed()
        t = custom.read_text(encoding="utf-8")
        self.assertIn("<name>switch</name>", t)
        self.assertIn("/X/launchers/mad-switch-launch.py ryujinx %ROM% --", t)
        self.assertIn("/X/launchers/mad-switch-launch.py eden %ROM% --", t)
        self.assertIn("/X/launchers/mad-standalone-launch.py pcsx2 %ROM% --", t)
        self.assertIn("/X/launchers/mad-standalone-launch.py rpcs3 %ROM% --", t)
        self.assertIn("/X/launchers/mad-standalone-launch.py xemu %ROM% --", t)

    def test_cat_b_present_and_resolved(self):
        custom, _ = self._seed()
        t = custom.read_text(encoding="utf-8")
        for s in ("model2", "mugen", "openbor", "naomi", "gameandwatch", "daphne", "sinden"):
            self.assertIn(f"<name>{s}</name>", t)
        # %MAD_LAUNCHERS% placeholder resolved to the sentinel launchers dir
        self.assertIn("/X/launchers/model2-m2emu.sh", t)
        self.assertNotIn("%MAD_LAUNCHERS%", t)

    def test_omitted_system_absent_from_custom(self):
        # snes/psx etc. are NOT seeded — they inherit their bundled definition
        custom, _ = self._seed()
        t = custom.read_text(encoding="utf-8")
        self.assertNotIn("<name>snes</name>", t)
        self.assertNotIn("<name>psx</name>", t)

    def test_keystone_overlay_inherits_bundled(self):
        # the exact overlay load_systems() performs: bundled then custom-by-name.
        from lib import es_systems as es
        custom, _ = self._seed()
        bundled = es._parse(BUNDLED)
        cust = es._parse(custom)
        merged = dict(bundled); merged.update(cust)
        # switch served by the WRAPPED custom command
        self.assertTrue(any("mad-switch-launch.py" in txt for _, txt in merged["switch"]))
        # snes only in bundled -> still present after overlay (not stranded)
        self.assertIn("snes", bundled)
        self.assertNotIn("snes", cust)
        self.assertIn("snes", merged)

    def test_idempotent(self):
        custom, _ = self._seed()
        before = custom.read_text(encoding="utf-8")
        res2 = S.seed_standalone(custom, bundled_path=BUNDLED,
                                 template_path=TEMPLATE, launchers=LAUNCHERS)
        self.assertEqual(res2.get("added"), [])          # nothing new on re-run
        self.assertEqual(before, custom.read_text(encoding="utf-8"))  # byte-identical
        # and a .bak was written (reversible)
        self.assertTrue(custom.with_suffix(custom.suffix + ".bak").is_file())

    def test_does_not_clobber_curated_block(self):
        # a pre-existing curated <system> with the same name is left untouched
        custom = Path(tempfile.mkdtemp()) / "custom_systems" / "es_systems.xml"
        custom.parent.mkdir(parents=True)
        custom.write_text('<?xml version="1.0"?>\n<systemList>\n'
                          '    <system>\n        <name>switch</name>\n'
                          '        <command label="MINE">/my/custom %ROM%</command>\n'
                          '    </system>\n</systemList>\n', encoding="utf-8")
        res = S.seed_standalone(custom, bundled_path=BUNDLED,
                                template_path=TEMPLATE, launchers=LAUNCHERS)
        self.assertIn("switch", res["skipped"])
        t = custom.read_text(encoding="utf-8")
        self.assertIn("/my/custom %ROM%", t)             # curated command preserved
        self.assertEqual(t.count("<name>switch</name>"), 1)   # not duplicated


class StandaloneSeedNoBundled(unittest.TestCase):
    """When the bundled es_systems.xml can't be resolved (e.g. the AppDir isn't
    extracted yet on a fresh standalone install), Cat-A must be reported as
    UNAVAILABLE — never silently dropped while reporting success."""

    def test_unavailable_reported_not_silent(self):
        custom = Path(tempfile.mkdtemp()) / "custom_systems" / "es_systems.xml"
        res = S.seed_standalone(custom, bundled_path=Path("/no/such/es_systems.xml"),
                                template_path=TEMPLATE, launchers=LAUNCHERS)
        self.assertEqual(set(res.get("unavailable", [])), {"switch", "ps2", "ps3", "xbox"})
        t = custom.read_text(encoding="utf-8")
        self.assertNotIn("<name>switch</name>", t)        # not seeded (bundled missing)
        self.assertIn("<name>sinden</name>", t)           # Cat-B template still applies
        self.assertFalse(res.get("error"))                # file still written (Cat-B)


if __name__ == "__main__":
    unittest.main()
