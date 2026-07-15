"""nonsteam_rungameids() must pair each shortcut's appid with ITS OWN appname.

Regression for the 2026-07-15 review finding #26. The old code scanned appids and appnames
as two independent passes over shortcuts.vdf and zipped them by position. If any block had
an appid but no lowercase 'appname' match (Steam's key casing has varied -- 'appname' vs
'AppName' -- or a block is nameless), zip truncated and every LATER pair shifted, so a
generated launcher .sh got a different game's rungameid and Steam booted the wrong game.
The structural per-block parser cannot shift. These tests feed adversarial synthetic vdf
blobs that would mis-pair under the old zip and assert every game maps to its own rungameid.

Run: python3 -m unittest tests.test_steam_rungameid_pairing -v
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCG = _load("steam_collection_gen", "steam-collection-gen.py")


def _rgid(appid: int) -> int:
    return ((appid & 0xFFFFFFFF) << 32) | 0x02000000


def _block(index: int, appid_bytes: bytes, name: str | None,
           name_key: bytes = b"appname", extra: bytes = b"") -> bytes:
    b = b"\x00" + str(index).encode() + b"\x00"
    b += b"\x02appid\x00" + appid_bytes
    if name is not None:
        b += b"\x01" + name_key + b"\x00" + name.encode("utf-8") + b"\x00"
    b += extra
    b += b"\x08"                              # end of this shortcut block
    return b


def _vdf(*blocks: bytes) -> bytes:
    return b"\x00shortcuts\x00" + b"".join(blocks) + b"\x08" + b"\x08"


class SteamRungameidPairing(unittest.TestCase):
    def _run(self, data: bytes) -> dict:
        with tempfile.NamedTemporaryFile(suffix=".vdf", delete=False) as fh:
            fh.write(data)
            p = Path(fh.name)
        saved = SCG.SHORTCUTS
        SCG.SHORTCUTS = [p]
        try:
            return SCG.nonsteam_rungameids()
        finally:
            SCG.SHORTCUTS = saved
            p.unlink()

    def test_happy_path_pairs_each_block(self):
        data = _vdf(
            _block(0, (10).to_bytes(4, "little", signed=True), "Alpha"),
            _block(1, (20).to_bytes(4, "little", signed=True), "Beta"),
            _block(2, (30).to_bytes(4, "little", signed=True), "Gamma"),
        )
        self.assertEqual(self._run(data),
                         {"Alpha": _rgid(10), "Beta": _rgid(20), "Gamma": _rgid(30)})

    def test_capitalized_appname_key_does_not_shift(self):
        # The MIDDLE block uses "AppName" (capital) -- the exact old failure: the lowercase
        # scan misses it, so zip pairs Gamma with Beta's appid. Structural parse must not.
        data = _vdf(
            _block(0, (10).to_bytes(4, "little", signed=True), "Alpha"),
            _block(1, (20).to_bytes(4, "little", signed=True), "Beta", name_key=b"AppName"),
            _block(2, (30).to_bytes(4, "little", signed=True), "Gamma"),
        )
        got = self._run(data)
        self.assertEqual(got["Alpha"], _rgid(10))
        self.assertEqual(got["Beta"], _rgid(20))     # matched case-insensitively
        self.assertEqual(got["Gamma"], _rgid(30))    # NOT _rgid(20): no shift

    def test_nameless_block_is_skipped_not_shifted(self):
        # Block 1 has an appid but no appname at all. Old zip would give Gamma Beta's appid.
        data = _vdf(
            _block(0, (10).to_bytes(4, "little", signed=True), "Alpha"),
            _block(1, (20).to_bytes(4, "little", signed=True), None),
            _block(2, (30).to_bytes(4, "little", signed=True), "Gamma"),
        )
        got = self._run(data)
        self.assertEqual(got, {"Alpha": _rgid(10), "Gamma": _rgid(30)})

    def test_appid_bytes_with_nul_and_map_end_are_read_whole(self):
        # appid whose 4 bytes contain 0x00 and 0x08 (0x08 == the map-terminator byte).
        # A byte-scan would misread it; the type-aware int32 read takes exactly 4 bytes.
        appid_bytes = b"\x08\x00\x08\x7f"
        appid = int.from_bytes(appid_bytes, "little", signed=True)
        data = _vdf(
            _block(0, appid_bytes, "Tricky"),
            _block(1, (30).to_bytes(4, "little", signed=True), "After"),
        )
        self.assertEqual(self._run(data), {"Tricky": _rgid(appid), "After": _rgid(30)})

    def test_nested_tags_and_int64_fields_are_walked_over(self):
        # A realistic block also carries a nested "tags" map and a uint64 LastPlayTime.
        tags = b"\x00tags\x00" + b"\x01" + b"0" + b"\x00" + b"fun" + b"\x00" + b"\x08"
        lastplay = b"\x07LastPlayTime\x00" + (1234567890).to_bytes(8, "little")
        data = _vdf(
            _block(0, (10).to_bytes(4, "little", signed=True), "Alpha", extra=tags + lastplay),
            _block(1, (20).to_bytes(4, "little", signed=True), "Beta"),
        )
        self.assertEqual(self._run(data), {"Alpha": _rgid(10), "Beta": _rgid(20)})

    def test_malformed_vdf_returns_empty_never_wrong(self):
        # Unknown type byte / truncation -> emit nothing rather than a mis-mapped game.
        self.assertEqual(self._run(b"\x00shortcuts\x00\x00\x00garbage\xff\xff"), {})

    def test_no_shortcuts_file_returns_empty(self):
        saved = SCG.SHORTCUTS
        SCG.SHORTCUTS = []
        try:
            self.assertEqual(SCG.nonsteam_rungameids(), {})
        finally:
            SCG.SHORTCUTS = saved


if __name__ == "__main__":
    unittest.main()
