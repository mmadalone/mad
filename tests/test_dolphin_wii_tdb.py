"""Tests for the GameTDB Classic-Controller capability layer (lib/dolphin_wii_tdb).

Run:  python3 -m unittest tests.test_dolphin_wii_tdb -v
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from lib import dolphin_wii_tdb as tdb


def _make_wiitdb_xml(n_cc: int) -> bytes:
    parts = ['<?xml version="1.0" encoding="utf-8"?>', "<datafile>"]
    for i in range(n_cc):
        gid = f"T{i:04d}0"                              # 6-char, unique, maker code "0" (not retail 01)
        parts.append(f'<game name="G{i}"><id>{gid}</id>'
                     f'<input players="1"><control type="classiccontroller"/></input></game>')
    parts.append("</datafile>")
    return "\n".join(parts).encode()


def _zip_bytes(xml_bytes: bytes, member: str = "wiitdb.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, xml_bytes)
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False

_FIXTURE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<datafile>
  <game name="Mario Kart Wii"><id>RMCE01</id>
    <input players="4">
      <control type="wiimote" required="true"/>
      <control type="classiccontroller" required="false"/>
    </input>
  </game>
  <game name="Wii Sports"><id>RSPE01</id>
    <input players="4">
      <control type="wiimote" required="true"/>
    </input>
  </game>
  <game name="No Input Homebrew"><id>RNIE01</id></game>
  <game name="Bad Id"><id>SHORT</id>
    <input><control type="classiccontroller"/></input>
  </game>
  <game name="GameCube VC"><id>RGCE01</id>
    <input players="2"><control type="gamecube"/></input>
  </game>
  <game name="Wiimote plus GC pad"><id>RVCE01</id>
    <input players="2">
      <control type="wiimote"/>
      <control type="gamecube"/>
    </input>
  </game>
  <game name="MotionPlus Sports"><id>RMPE01</id>
    <input players="1">
      <control type="motionplus"/>
      <control type="nunchuk"/>
    </input>
  </game>
</datafile>"""


class ParseCcIds(unittest.TestCase):
    def test_only_classiccontroller_games_with_valid_ids(self):
        ids = tdb._parse_cc_ids(io.BytesIO(_FIXTURE_XML))
        self.assertEqual(ids, {"RMCE01"})       # RSPE01 no CC; RNIE01 no input; SHORT bad id

    def test_bad_xml_is_swallowed(self):
        self.assertEqual(tdb._parse_cc_ids(io.BytesIO(b"<not xml")), set())


class ParseAll(unittest.TestCase):
    """The single-pass parser splits CC-capable ids from the motion-only hide-set."""
    def test_splits_cc_and_motion(self):
        r = tdb._parse_all(io.BytesIO(_FIXTURE_XML))
        self.assertEqual(r["cc"], {"RMCE01"})                      # only the classiccontroller game
        self.assertEqual(r["motion"], {"RSPE01", "RMPE01"})        # wiimote-only / motionplus+nunchuk
        # gamecube-only AND wiimote+gamecube are pad-drivable -> NEITHER cc NOR hidden.
        self.assertNotIn("RGCE01", r["cc"] | r["motion"])
        self.assertNotIn("RVCE01", r["cc"] | r["motion"])
        self.assertTrue(r["cc"].isdisjoint(r["motion"]))           # disjoint by construction

    def test_bad_xml_swallowed(self):
        self.assertEqual(tdb._parse_all(io.BytesIO(b"<broken")), {"cc": set(), "motion": set()})


class Capability(unittest.TestCase):
    def setUp(self):
        self._orig = (tdb._load, tdb.dolphin_gameids.gameid,
                      tdb.dolphin_gameids.gameids, tdb._overrides)
        tdb._load = lambda: {"generated": 1_000_000, "source": "x",
                             "ids": ["RMCE01", "RSBE01"]}
        tdb._overrides = lambda: set()
        tdb._reset()                              # force reload from the patched _load

    def tearDown(self):
        (tdb._load, tdb.dolphin_gameids.gameid,
         tdb.dolphin_gameids.gameids, tdb._overrides) = self._orig
        tdb._reset()

    def test_direct_id_membership(self):
        self.assertTrue(tdb.is_cc_capable("RMCE01"))
        self.assertTrue(tdb.is_cc_capable("RSBE01"))
        self.assertFalse(tdb.is_cc_capable("RSPE01"))     # Wii Sports: not CC

    def test_prefix_fallback_rescues_a_hack(self):
        # An uncatalogued hack keeps the retail prefix (RMCE) with a custom maker code; RMCE01 is
        # a CC retail game (ends "01"), so the hack inherits CC.
        self.assertTrue(tdb.is_cc_capable("RMCE99"))
        # A hack whose retail sibling (RSPE01) is NOT in the CC set is still not CC.
        self.assertFalse(tdb.is_cc_capable("RSPE77"))

    def test_override_allowlist(self):
        tdb._overrides = lambda: {"ZZZZ99"}
        self.assertTrue(tdb.is_cc_capable("ZZZZ99"))       # not in set, not a prefix, but forced
        self.assertFalse(tdb.is_cc_capable("YYYY88"))

    def test_rom_path_resolves_via_dolphin_tool(self):
        tdb.dolphin_gameids.gameid = lambda rom: "RMCE01"
        self.assertTrue(tdb.is_cc_capable("/ROMs/wii/Mario Kart.rvz"))
        tdb.dolphin_gameids.gameid = lambda rom: None      # unresolvable -> fail-closed
        self.assertFalse(tdb.is_cc_capable("/ROMs/wii/Homebrew.iso"))

    def test_cc_capable_games_batch(self):
        tdb.dolphin_gameids.gameids = lambda roms: {
            "/a.rvz": "RMCE01", "/b.rvz": "RSPE01", "/c.rvz": None}
        got = tdb.cc_capable_games(["/a.rvz", "/b.rvz", "/c.rvz"])
        self.assertEqual(got, {"/a.rvz": True, "/b.rvz": False, "/c.rvz": False})

    def test_status_reports_available_and_count(self):
        st = tdb.status()
        self.assertTrue(st["available"])
        self.assertEqual(st["count"], 2)
        self.assertIsInstance(st["age_days"], int)


class RetailPrefixSemantics(unittest.TestCase):
    """The NSMBW case: a family where the retail base (SMNE01) is NOT CC but a few hacks ADD CC.
    The CC hacks match directly; uncatalogued hacks of the family are NOT auto-flipped."""
    def setUp(self):
        self._orig = (tdb._load, tdb._overrides)
        # SMNE03 is a CC-adding hack (catalogued); SMNE01 (retail NSMBW) is absent = not CC.
        tdb._load = lambda: {"generated": 1, "source": "x", "ids": ["SMNE03", "RMCE01"]}
        tdb._overrides = lambda: set()
        tdb._reset()

    def tearDown(self):
        (tdb._load, tdb._overrides) = self._orig
        tdb._reset()

    def test_catalogued_cc_hack_matches_directly(self):
        self.assertTrue(tdb.is_cc_capable("SMNE03"))       # in the set by exact id

    def test_family_not_flipped_when_retail_is_not_cc(self):
        # Retail SMNE01 is not CC, so an uncatalogued SMNE hack is NOT rescued (no whole-family flip).
        self.assertFalse(tdb.is_cc_capable("SMNE01"))
        self.assertFalse(tdb.is_cc_capable("SMNE77"))
        # Contrast: RMCE01 IS CC retail, so an uncatalogued RMCE hack IS rescued.
        self.assertTrue(tdb.is_cc_capable("RMCE77"))


class EmptyDatabase(unittest.TestCase):
    def setUp(self):
        self._orig = (tdb._load, tdb.dolphin_gameids.gameid, tdb._overrides)
        tdb._load = lambda: {"generated": 0, "source": "", "ids": []}
        tdb._overrides = lambda: set()
        tdb.dolphin_gameids.gameid = lambda rom: "RMCE01"
        tdb._reset()

    def tearDown(self):
        (tdb._load, tdb.dolphin_gameids.gameid, tdb._overrides) = self._orig
        tdb._reset()

    def test_offline_no_data_fails_closed(self):
        self.assertFalse(tdb.is_cc_capable("RMCE01"))      # empty set -> nothing is CC
        self.assertFalse(tdb.is_cc_capable("/ROMs/wii/Mario Kart.rvz"))
        st = tdb.status()
        self.assertFalse(st["available"])
        self.assertIsNone(st["age_days"])


class HiddenMotion(unittest.TestCase):
    """is_hidden_motion: fail-OPEN, exact-id only (no prefix fallback, no override)."""
    def setUp(self):
        self._orig = (tdb._load, tdb.dolphin_gameids.gameid)
        tdb._load = lambda: {"generated": 1, "source": "x",
                             "ids": ["RMCE01"], "motion_ids": ["RSPE01", "RMPE01"]}
        tdb._reset()

    def tearDown(self):
        (tdb._load, tdb.dolphin_gameids.gameid) = self._orig
        tdb._reset()

    def test_known_motion_is_hidden(self):
        self.assertTrue(tdb.is_hidden_motion("RSPE01"))
        self.assertTrue(tdb.is_hidden_motion("RMPE01"))

    def test_cc_game_not_hidden(self):
        self.assertFalse(tdb.is_hidden_motion("RMCE01"))           # CC game stays shown

    def test_datagap_unknown_fails_open(self):
        # A game GameTDB has no record of (WiiWare etc.) -> False -> stays VISIBLE (data gap).
        self.assertFalse(tdb.is_hidden_motion("WR5PEY"))

    def test_no_prefix_fallback(self):
        # A hack sharing a motion game's 4-char prefix is NOT auto-hidden (exact-id only).
        self.assertFalse(tdb.is_hidden_motion("RSPE77"))

    def test_rom_path_resolves_then_fails_open_when_unresolvable(self):
        tdb.dolphin_gameids.gameid = lambda rom: "RSPE01"
        self.assertTrue(tdb.is_hidden_motion("/ROMs/wii/WiiSports.rvz"))
        tdb.dolphin_gameids.gameid = lambda rom: None              # unresolvable -> fail-OPEN (shown)
        self.assertFalse(tdb.is_hidden_motion("/ROMs/wii/Homebrew.iso"))

    def test_old_cache_without_field_hides_nothing(self):
        tdb._load = lambda: {"generated": 1, "source": "x", "ids": ["RMCE01"]}   # no motion_ids
        tdb._reset()
        self.assertFalse(tdb.is_hidden_motion("RSPE01"))           # backward-compat: nothing hidden


class BundledData(unittest.TestCase):
    def test_bundled_cc_ids_is_present_and_sane(self):
        # The shipped offline dataset must load and contain known CC-capable titles.
        tdb._reset()
        self.assertTrue(tdb.is_cc_capable("RMCE01"))       # Mario Kart Wii
        self.assertTrue(tdb.is_cc_capable("RSBE01"))       # Smash Bros Brawl
        self.assertFalse(tdb.is_cc_capable("SMNE01"))      # New Super Mario Bros Wii (no CC)
        # and the shipped motion hide-set is populated:
        self.assertTrue(tdb.is_hidden_motion("SMNE01"))    # NSMBW: motion-only -> hidden handheld
        self.assertFalse(tdb.is_hidden_motion("RMCE01"))   # CC game -> shown
        tdb._reset()


class Refresh(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_cache = tdb._CACHE
        self._orig_urlopen = tdb.urllib.request.urlopen
        tdb._CACHE = Path(self.tmpdir) / "cc_ids.json"
        tdb._reset()

    def tearDown(self):
        tdb.urllib.request.urlopen = self._orig_urlopen
        tdb._CACHE = self._orig_cache
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        tdb._reset()

    def _serve(self, blob):
        tdb.urllib.request.urlopen = lambda req, timeout=None: _FakeResp(blob)

    def test_valid_refresh_writes_cache_and_is_queryable(self):
        self._serve(_zip_bytes(_make_wiitdb_xml(550)))
        self.assertTrue(tdb.refresh())
        self.assertTrue(tdb._CACHE.is_file())
        self.assertTrue(tdb.is_cc_capable("T00000"))       # a parsed id
        self.assertFalse(tdb.is_cc_capable("ZZZZ99"))

    def test_refresh_captures_motion_ids(self):
        # 550 CC games (clears the floor) + one motion-only game; the motion set persists + is queryable.
        parts = ['<?xml version="1.0"?>', "<datafile>"]
        for i in range(550):
            parts.append(f'<game><id>C{i:04d}0</id><input>'
                         f'<control type="classiccontroller"/></input></game>')
        parts.append('<game><id>MOT010</id><input><control type="wiimote"/></input></game>')
        parts.append("</datafile>")
        self._serve(_zip_bytes("\n".join(parts).encode()))
        self.assertTrue(tdb.refresh())
        tdb._reset()
        self.assertTrue(tdb.is_hidden_motion("MOT010"))            # motion id persisted + queryable
        self.assertFalse(tdb.is_hidden_motion("C00000"))          # a CC game is never motion
        self.assertIn("MOT010", json.loads(tdb._CACHE.read_text())["motion_ids"])   # written to cache

    def test_zip_without_wiitdb_keeps_cache(self):
        self._serve(_zip_bytes(b"nope", member="readme.txt"))
        self.assertFalse(tdb.refresh())
        self.assertFalse(tdb._CACHE.is_file())             # never written

    def test_truncated_xml_rejected_cache_untouched(self):
        bad = b'<?xml version="1.0"?>\n<datafile><game><id>T00000</id><input><control type="classic'
        self._serve(_zip_bytes(bad))
        self.assertFalse(tdb.refresh())
        self.assertFalse(tdb._CACHE.is_file())

    def test_too_few_ids_below_floor_keeps_cache(self):
        self._serve(_zip_bytes(_make_wiitdb_xml(3)))       # < _MIN_CC_IDS
        self.assertFalse(tdb.refresh())
        self.assertFalse(tdb._CACHE.is_file())

    def test_network_error_keeps_cache(self):
        def boom(req, timeout=None):
            raise OSError("network down")
        tdb.urllib.request.urlopen = boom
        self.assertFalse(tdb.refresh())
        self.assertFalse(tdb._CACHE.is_file())

    def test_refresh_replaces_a_stale_cache_atomically(self):
        tdb._CACHE.parent.mkdir(parents=True, exist_ok=True)
        tdb._CACHE.write_text(json.dumps({"generated": 1, "source": "old", "ids": ["OLD001"]}))
        self._serve(_zip_bytes(_make_wiitdb_xml(550)))
        self.assertTrue(tdb.refresh())
        tdb._reset()
        self.assertFalse(tdb.is_cc_capable("OLD001"))      # stale entry gone
        self.assertTrue(tdb.is_cc_capable("T00000"))


class LoadPrecedence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = (tdb._CACHE, tdb._BUNDLED)
        tdb._CACHE = Path(self.tmpdir) / "cc_ids.json"
        tdb._BUNDLED = Path(self.tmpdir) / "bundled.json"
        tdb._BUNDLED.write_text(json.dumps({"generated": 1, "source": "b", "ids": ["BBBB01"]}))
        tdb._reset()

    def tearDown(self):
        (tdb._CACHE, tdb._BUNDLED) = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        tdb._reset()

    def test_bundled_used_when_no_user_cache(self):
        self.assertTrue(tdb.is_cc_capable("BBBB01"))

    def test_user_cache_wins_over_bundled(self):
        tdb._CACHE.write_text(json.dumps({"generated": 2, "source": "u", "ids": ["UUUU01"]}))
        tdb._reset()
        self.assertTrue(tdb.is_cc_capable("UUUU01"))
        self.assertFalse(tdb.is_cc_capable("BBBB01"))      # user cache replaces bundled


if __name__ == "__main__":
    unittest.main()
