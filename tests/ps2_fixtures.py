"""Builds tiny, spec-correct synthetic ISO9660 disc images for tests/test_ps2_disc.py.
No real disc images are ever committed to the repo -- every test disc here is a few KB
of hand-built ISO9660 structure: a Primary Volume Descriptor at LBA 16, a root
directory, SYSTEM.CNF, and (for the corrections lib/ps2_disc.py exists to get right) a
boot file that can live in a subdirectory and be reached through an intermediate
';1'-suffixed path component.

Not a test module itself -- imported by tests/test_ps2_disc.py. Stdlib only.
"""
from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048
Entry = tuple[str, int, int, bool]                     # (name, lba, exact_length, is_dir)


def dir_record(name: bytes, lba: int, length: int, is_dir: bool) -> bytes:
    """One ISO9660 directory record, both-byte-order fields included (a real disc always
    carries both; lib/ps2_disc.py only reads the little-endian half, matching the working
    proof-of-concept scripts, but the fixture itself stays spec-correct)."""
    nlen = len(name)
    rec = bytearray(33 + nlen)
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into(">I", rec, 6, lba)
    struct.pack_into("<I", rec, 10, length)
    struct.pack_into(">I", rec, 14, length)
    rec[25] = 0x02 if is_dir else 0x00                  # file flags: bit 1 = directory
    struct.pack_into("<H", rec, 28, 1)                  # volume sequence number
    struct.pack_into(">H", rec, 30, 1)
    rec[32] = nlen
    rec[33:33 + nlen] = name
    if len(rec) % 2 == 1:
        rec += b"\x00"                                  # ISO9660 pads records to even length
    rec[0] = len(rec)
    return bytes(rec)


class IsoBuilder:
    """Allocates 2048-byte sectors starting at LBA 17 (16 is reserved for the PVD) and
    assembles a raw .iso image. `add_file`/`add_dir` return an Entry ready to hand to
    `dir_listing`/`build` (directly, or nested inside another `add_dir` call)."""

    def __init__(self) -> None:
        self._next_lba = 17
        self._sectors: dict[int, bytes] = {}

    def _place(self, data: bytes) -> tuple[int, int]:
        lba = self._next_lba
        n_sectors = max(1, (len(data) + SECTOR - 1) // SECTOR)
        padded = data.ljust(n_sectors * SECTOR, b"\x00")
        for i in range(n_sectors):
            self._sectors[lba + i] = padded[i * SECTOR:(i + 1) * SECTOR]
        self._next_lba += n_sectors
        return lba, len(data)                           # the EXACT length, never sector-rounded

    def add_file(self, name: str, data: bytes) -> Entry:
        lba, length = self._place(data)
        return name, lba, length, False

    def add_dir(self, name: str, children: list[Entry]) -> Entry:
        lba, length = self._place(self.dir_listing(children))
        return name, lba, length, True

    @staticmethod
    def dir_listing(children: list[Entry]) -> bytes:
        out = b""
        for name, lba, length, is_dir in children:
            out += dir_record(name.encode("latin-1"), lba, length, is_dir)
        return out

    def build(self, root_children: list[Entry]) -> bytes:
        """Place the root directory extent, build the PVD at LBA 16, and return the whole
        image (a plain 2048-byte-sector stream, ready to write as a .iso)."""
        _, root_lba, root_len, _ = self.add_dir("\x00", root_children)
        pvd = bytearray(SECTOR)
        pvd[0] = 1                                      # volume descriptor type: Primary
        pvd[1:6] = b"CD001"
        pvd[6] = 1                                      # volume descriptor version
        root_rec = dir_record(b"\x00", root_lba, root_len, True)
        pvd[156:156 + len(root_rec)] = root_rec
        total_sectors = max(self._sectors, default=16) + 1
        out = bytearray(total_sectors * SECTOR)
        out[16 * SECTOR:17 * SECTOR] = bytes(pvd)
        for lba, sector in self._sectors.items():
            out[lba * SECTOR:(lba + 1) * SECTOR] = sector
        return bytes(out)


def iso_bytes(*, boot2: str, boot_name: str, boot_data: bytes,
              boot_subdir: str | None = None, include_system_cnf: bool = True,
              extra_root: list[Entry] | None = None) -> bytes:
    """Build a minimal synthetic disc image (plain 2048-byte sectors) and return its
    bytes -- the shared body behind make_iso(); also used directly by the .bin/.cue
    tests via to_raw_bin() below, since those containers are just a different physical
    packing of the same logical ISO9660 structure.

    BOOT2 is written verbatim into SYSTEM.CNF's 'BOOT2 = ...' line -- callers exercise the
    parsing corrections (no backslash, an intermediate ';1', a bad-shaped serial, ...) by
    varying this string. BOOT_NAME/BOOT_DATA are the boot ELF's REAL on-disc filename
    (without ';1' -- the builder always adds exactly one, like a real disc) and bytes. If
    BOOT_SUBDIR is given the boot file is placed inside that subdirectory instead of the
    root (BOOT2 must then reference it there for identify() to find it).
    include_system_cnf=False omits SYSTEM.CNF entirely, for the "missing" test."""
    b = IsoBuilder()
    root: list[Entry] = list(extra_root or [])
    boot_entry = b.add_file(f"{boot_name};1", boot_data)
    root.append(b.add_dir(boot_subdir, [boot_entry]) if boot_subdir is not None else boot_entry)
    if include_system_cnf:
        cnf = f"BOOT2 = {boot2}\r\n".encode("latin-1")
        root.append(b.add_file("SYSTEM.CNF;1", cnf))
    return b.build(root)


def make_iso(dest: Path, **kwargs) -> Path:
    """iso_bytes(**kwargs), written to DEST. Returns DEST."""
    dest.write_bytes(iso_bytes(**kwargs))
    return dest


def to_raw_bin(image2048: bytes, stride: int = 2352, offset: int = 24) -> bytes:
    """Repack a plain 2048-byte-sector image (as built by IsoBuilder/iso_bytes) into a
    raw STRIDE-byte-sector image with the 2048-byte user payload at OFFSET within each
    sector -- simulating a real .bin's larger physical sectors (sync/header/subheader
    before the payload, ECC/EDC after) for the .bin/.cue container tests."""
    n = len(image2048) // SECTOR
    out = bytearray()
    for i in range(n):
        out += b"\x00" * offset
        out += image2048[i * SECTOR:(i + 1) * SECTOR]
        out += b"\x00" * (stride - offset - SECTOR)
    return bytes(out)
