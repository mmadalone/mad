"""Ryujinx ExtraData0 save decoder (P13): title-id <- LE u64 ProgramId at offset 0 of each save's
ExtraData0, mapping the opaque sequential SaveDataId dir back to a title-id.

Run:  python3 -m unittest tests.test_ryujinx_saves -v
"""
from __future__ import annotations

import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import ryujinx_saves as rs   # noqa: E402


def _mksave(base: Path, index: str, program_id: int) -> Path:
    d = base / index
    d.mkdir(parents=True)
    # ExtraData0 = the Horizon SaveDataExtraData blob; only offset 0 (u64 LE ProgramId) matters here.
    (d / "ExtraData0").write_bytes(struct.pack("<Q", program_id) + b"\x00" * 56)
    (d / "0").mkdir()
    return d


class Decoder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ryu-"))
        self.save = self.tmp / "save"
        self.meta = self.tmp / "saveMeta"
        self.save.mkdir()
        self.meta.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_title_to_index_maps_le_u64(self):
        _mksave(self.save, "0000000000000001", 0x01007EF00011E000)
        _mksave(self.save, "0000000000000002", 0x0100F2C0115B6000)
        m = rs.title_to_index(self.save)
        self.assertEqual(m["01007ef00011e000"], "0000000000000001")
        self.assertEqual(m["0100f2c0115b6000"], "0000000000000002")

    def test_zero_program_id_skipped(self):
        _mksave(self.save, "0000000000000001", 0)          # system/temporary save, no title
        self.assertEqual(rs.title_to_index(self.save), {})

    def test_short_extradata_skipped(self):
        d = self.save / "0000000000000001"
        d.mkdir()
        (d / "ExtraData0").write_bytes(b"\x01\x02")          # < 8 bytes
        self.assertEqual(rs.title_to_index(self.save), {})

    def test_missing_extradata_skipped(self):
        (self.save / "0000000000000001").mkdir()             # no ExtraData0 at all
        self.assertEqual(rs.title_to_index(self.save), {})

    def test_duplicate_title_keeps_first(self):
        _mksave(self.save, "0000000000000001", 0x0100DCA0064A6000)
        _mksave(self.save, "0000000000000009", 0x0100DCA0064A6000)
        self.assertEqual(rs.title_to_index(self.save)["0100dca0064a6000"], "0000000000000001")

    def test_save_paths_returns_save_and_meta(self):
        _mksave(self.save, "0000000000000005", 0x0100DCA0064A6000)
        (self.meta / "0000000000000005").mkdir()
        paths = rs.save_paths("0100dca0064a6000", self.save, self.meta)
        self.assertEqual([p.name for p in paths], ["0000000000000005", "0000000000000005"])
        self.assertTrue(all(p.is_dir() for p in paths))

    def test_save_paths_no_meta_still_returns_save(self):
        _mksave(self.save, "0000000000000005", 0x0100DCA0064A6000)   # meta dir absent
        paths = rs.save_paths("0100dca0064a6000", self.save, self.meta)
        self.assertEqual([p.name for p in paths], ["0000000000000005"])

    def test_save_paths_missing_title(self):
        self.assertEqual(rs.save_paths("dead0000dead0000", self.save, self.meta), [])

    def test_missing_base_dir(self):
        self.assertEqual(rs.title_to_index(self.tmp / "nope"), {})


if __name__ == "__main__":
    unittest.main()
