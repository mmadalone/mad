"""backup_manifest is the browse index + restore key for a granular backup. These lock in the
behaviors browse/restore rely on:

  * build -> write -> read round-trips EXACTLY (a manifest survives disk unchanged);
  * write is atomic and lands where manifest_path() says (inside a folder, or a sidecar next to
    an archive) so a cloud browse can `rclone cat` the sidecar without unpacking;
  * validate REJECTS an empty / foreign / wrong-schema manifest (so a restore can never key off
    junk - mirrors deck-restore.sh's non-empty-manifest guard);
  * the accessors (categories/systems/items/find_item) return the right rows + tallies;
  * add_item de-dups by id (a rebuild does not double a row);
  * the CLI read/validate seam (used by deck-backup.sh) matches the API.

Run:  python3 -m unittest tests.test_backup_manifest -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm  # noqa: E402


def _sample() -> dict:
    m = bm.new_manifest("roms", created="20260724T101530")
    bm.add_item(m, category="roms", category_label="ROMs & games",
                system="nes", system_label="Nintendo Entertainment System",
                item=bm.make_item(id="nes:smb", name="Super Mario Bros.",
                                  src="/run/media/deck/1tbDeck/Emulation/roms/nes/smb.zip",
                                  rel="roms/nes/smb.zip", kind="file", size=40960,
                                  stem="smb", boxart=True, addons=["cheats"]))
    bm.add_item(m, category="roms", category_label="ROMs & games",
                system="ps3", system_label="Sony PlayStation 3",
                item=bm.make_item(id="ps3:demonsouls", name="Demon's Souls",
                                  src="/run/media/deck/1tbDeck/Emulation/roms/ps3/Demons Souls",
                                  rel="roms/ps3/Demons Souls", kind="folder", size=8 * 1024**3))
    return m


class RoundTrip(unittest.TestCase):
    def test_build_write_read_exact(self):
        m = _sample()
        with tempfile.TemporaryDirectory() as d:
            p = bm.write(m, Path(d) / bm.MANIFEST_NAME)
            self.assertTrue(p.is_file())
            back = bm.read(p)
        self.assertEqual(back, m, "manifest must survive write->read byte-for-byte in content")

    def test_write_is_atomic_no_tmp_left(self):
        m = _sample()
        with tempfile.TemporaryDirectory() as d:
            bm.write(m, Path(d) / bm.MANIFEST_NAME)
            leftovers = [f.name for f in Path(d).iterdir() if f.name.endswith(".tmp")]
            self.assertEqual(leftovers, [], "no .tmp file may be left behind")


class ManifestPath(unittest.TestCase):
    def test_folder_target_writes_inside(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "deck-config-20260724"
            folder.mkdir()
            self.assertEqual(bm.manifest_path(folder), folder / bm.MANIFEST_NAME)

    def test_archive_target_is_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            arch = Path(d) / "deck-roms-20260724.tar.gz"   # does not need to exist
            self.assertEqual(bm.manifest_path(arch),
                             Path(d) / ("deck-roms-20260724.tar.gz." + bm.MANIFEST_NAME))

    def test_read_finds_folder_and_sidecar(self):
        m = _sample()
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "mir"
            folder.mkdir()
            bm.write(m, bm.manifest_path(folder))
            self.assertTrue(bm.validate(bm.read(folder)), "read(folder) must find the inside manifest")
            arch = Path(d) / "x.tar.gz"
            bm.write(m, bm.manifest_path(arch))
            self.assertTrue(bm.validate(bm.read(arch)), "read(archive) must find the sidecar")


class Validate(unittest.TestCase):
    def test_valid_sample(self):
        self.assertTrue(bm.validate(_sample()))

    def test_empty_manifest_rejected(self):
        self.assertFalse(bm.validate(bm.new_manifest("roms")),
                         "a manifest with zero items must be rejected")

    def test_foreign_and_wrong_schema_rejected(self):
        self.assertFalse(bm.validate({}))
        self.assertFalse(bm.validate({"hello": "world"}))
        bad = _sample()
        bad["schema"] = 999
        self.assertFalse(bm.validate(bad), "a wrong schema version must be rejected")

    def test_non_dict_nested_rejected_not_raised(self):
        # REGRESSION (review, high): a schema-1 manifest with a non-dict category/system must return
        # False, never raise AttributeError (which aborted the whole sources listing).
        for junk in ({"schema": 1, "categories": {"roms": None}},
                     {"schema": 1, "categories": {"roms": {"systems": {"nes": "oops"}}}},
                     {"schema": 1, "categories": {"roms": {"systems": {"nes": {"items": "nope"}}}}}):
            self.assertFalse(bm.validate(junk))
            # accessors must also tolerate the junk (they run on a partially-valid manifest)
            bm.categories(junk)
            bm.systems(junk, "roms")
            bm.items(junk, "roms", "nes")

    def test_non_numeric_size_does_not_crash_accessors(self):
        # REGRESSION (review #2, medium): a foreign item with a non-numeric 'size' must count as 0, not
        # crash systems()/categories() (which browse a possibly-untrusted source).
        m = {"schema": 1, "kind": "granular", "categories": {"roms": {"label": "R", "systems": {
            "nes": {"label": "NES", "items": [{"id": "nes:x", "name": "X", "rel": "roms/nes/x.zip",
                                               "size": "huge"}]}}}}}
        self.assertTrue(bm.validate(m))
        self.assertEqual(bm.systems(m, "roms")[0]["size"], 0)
        self.assertEqual(bm.categories(m)[0]["size"], 0)

    def test_read_text_rejects_garbage(self):
        self.assertEqual(bm.read_text("not json {{{"), {})
        self.assertEqual(bm.read_text("[1,2,3]"), {}, "a non-object payload is not a manifest")
        self.assertEqual(bm.read_text('{"no":"categories"}'), {})


class ReadGuards(unittest.TestCase):
    """REGRESSION (review, high): read() must NOT slurp an arbitrary large file. The backup dir also
    holds deck-backup.sh's multi-GB deck-roms-*.tar archives with no sidecar."""
    def test_non_manifest_named_file_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            arch = Path(d) / "deck-roms-20260724.tar"       # NOT a manifest, no sidecar
            arch.write_bytes(b"\x00\x01\x02 not json")
            self.assertEqual(bm.read(arch), {}, "an archive file must never be read as a manifest")

    def test_oversize_manifest_named_file_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / ("x." + bm.MANIFEST_NAME)         # manifest-NAMED but too big
            valid = bm.new_manifest("roms", created="x")
            bm.add_item(valid, category="roms", category_label="R", system="nes",
                        system_label="NES", item=bm.make_item(id="nes:x", name="X", src="/x",
                                                              rel="roms/nes/x.zip"))
            big.write_text(json.dumps(valid))
            with mock.patch.object(bm, "_MAX_MANIFEST_BYTES", 4):   # smaller than the file
                self.assertEqual(bm.read(big), {}, "a file over the cap must not be read")
            # under the cap it reads fine
            self.assertTrue(bm.validate(bm.read(big)))


class Accessors(unittest.TestCase):
    def test_categories_counts_and_size(self):
        cats = bm.categories(_sample())
        self.assertEqual(len(cats), 1)
        c = cats[0]
        self.assertEqual(c["key"], "roms")
        self.assertEqual(c["n_systems"], 2)
        self.assertEqual(c["n_items"], 2)
        self.assertEqual(c["size"], 40960 + 8 * 1024**3)

    def test_systems_sorted_with_tallies(self):
        syss = bm.systems(_sample(), "roms")
        self.assertEqual([s["key"] for s in syss], ["nes", "ps3"])
        self.assertEqual(syss[0]["n_items"], 1)
        self.assertEqual(syss[0]["label"], "Nintendo Entertainment System")

    def test_items_and_find(self):
        m = _sample()
        its = bm.items(m, "roms", "nes")
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]["boxart"], True)
        self.assertEqual(its[0]["addons"], ["cheats"])
        self.assertIsNotNone(bm.find_item(m, "roms", "nes", "nes:smb"))
        self.assertIsNone(bm.find_item(m, "roms", "nes", "nes:nope"))
        self.assertEqual(bm.items(m, "roms", "absent"), [])


class AddItem(unittest.TestCase):
    def test_dedup_by_id_replaces(self):
        m = bm.new_manifest("roms")
        for size in (10, 20):
            bm.add_item(m, category="roms", category_label="ROMs", system="nes",
                        system_label="NES",
                        item=bm.make_item(id="nes:smb", name="SMB", src="/x", rel="roms/nes/smb.zip",
                                          size=size))
        its = bm.items(m, "roms", "nes")
        self.assertEqual(len(its), 1, "same id must not duplicate")
        self.assertEqual(its[0]["size"], 20, "the later item wins")

    def test_make_item_rejects_bad_restart_and_kind(self):
        with self.assertRaises(ValueError):
            bm.make_item(id="a", name="a", src="/x", rel="y", restart="reboot")
        with self.assertRaises(ValueError):
            bm.make_item(id="a", name="a", src="/x", rel="y", kind="symlink")


class Cli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "lib.backup_manifest", *args],
                              cwd=str(ROOT), capture_output=True, text=True)

    def test_cli_read_and_validate(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "mir"
            folder.mkdir()
            bm.write(_sample(), bm.manifest_path(folder))
            r = self._run("read", str(folder))
            self.assertEqual(r.returncode, 0)
            self.assertTrue(bm.validate(json.loads(r.stdout)))
            self.assertEqual(self._run("validate", str(folder)).returncode, 0)

    def test_cli_validate_rejects_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(self._run("validate", d).returncode, 0,
                                "an empty dir has no manifest -> non-zero")


if __name__ == "__main__":
    unittest.main()
