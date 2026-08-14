"""ps2_disc -- derive PCSX2's per-game override key (`<SERIAL>_<CRC>`, or bare `<CRC>`)
straight from a PS2 disc image, instead of parsing PCSX2's own binary gamelist.cache
(lib/madsrv/pcsx2_games.py). PCSX2 stores per-game overrides at
`~/.config/PCSX2/gamesettings/<key>.ini`; that cache only updates when the PCSX2 GUI
rescans its library, so a freshly added disc has no key MAD can use until the user opens
PCSX2 once. Reading the disc directly makes a new disc configurable immediately.

THE DERIVATION (PCSX2's own logic, not a guess):
  1. Read SYSTEM.CNF from the disc's ISO9660 filesystem.
  2. Its `BOOT2 = cdrom0:\\SLES_529.50;1` line names the boot ELF.
  3. SERIAL comes from that value; see _serial_from_boot2 for PCSX2's exact rules,
     including when PCSX2 CLEARS the serial entirely (correction 3 below).
  4. CRC is PCSX2's ElfObject::GetCRC: an XOR fold of the boot ELF's bytes as little-
     endian uint32 words, over WHOLE words only (correction 1 below).
  5. key = f"{SERIAL}_{CRC:08X}", or bare f"{CRC:08X}" when the serial was cleared.

A WRONG KEY IS WORSE THAN NO KEY: it silently writes a settings file PCSX2 never reads.
So identify() prefers "cannot identify" (None) over anything uncertain, and every
internal failure carries an explicit final/non-final classification (see
lib/ident_cache.py's docstring) -- when genuinely unsure this always leans non-final,
since a wrongly-permanent failure would brand a perfectly good disc unreadable forever
in ident_cache's on-disk store.

Stdlib only, plus a subprocess call to EmuDeck's bundled chdman5 for .chd. identify()
NEVER RAISES -- every failure path is caught, all the way out.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

_CHDMAN = Path("/home/deck/Emulation/tools/chdconv/chdman5")
_CHD_TIMEOUT = 60                                    # seconds -- never let a stuck chdman5 hang identify()
_CHD_LAYOUTS = ((2048, 0), (2448, 0), (2448, 24))    # plain 2048; chdman-normalised MODE1 (2448/0,
                                                      # user data first, no sync/header stored); raw
                                                      # MODE2_RAW/XA-Form1 (2448/24, sync+header+
                                                      # subheader precede the user data). Verified
                                                      # against 72 real PCSX2 .chd/.iso/.bin discs
                                                      # 2026-08-14: MODE1 discs failed at (2448,24)
                                                      # until (2448,0) was added -- chdman does not
                                                      # store a sync/header for a MODE1 (non-_RAW)
                                                      # track, only for MODE2_RAW.
_BIN_LAYOUTS = ((2352, 24), (2352, 16), (2448, 24), (2448, 16), (2048, 0))
_PLAIN_EXTS = {".iso", ".img", ".dump"}

# PCSX2's own wildcard test for a valid serial-shaped string: four chars, a '_' or '-'
# separator, three chars, a dot, two-or-more chars (e.g. "SLES_529.50" / "SLES-529.50").
# A BOOT2 filename that fails this is not a real serial (homebrew, a bare .ELF, ...) and
# PCSX2 clears it rather than keeping a bogus label -- see _serial_from_boot2.
_SERIAL_RE = re.compile(r"^.{4}[_-].{3}\..{2,}$")


class _DiscFail(Exception):
    """Internal control-flow only -- identify() catches this at the top and turns it into
    the (key, why, final) contract. `why` must already be short, plain-English, and free
    of file paths / exception text (identify()'s callers show it to a non-technical user
    in the MAD panel)."""

    def __init__(self, why: str, final: bool) -> None:
        super().__init__(why)
        self.why = why
        self.final = final


# ── the four load-bearing corrections (see the module docstring's numbering) ──────────
def _elf_crc(data: bytes) -> int:
    """PCSX2's ElfObject::GetCRC: XOR-fold the ELF as little-endian u32 words.

    CORRECTION 1: fold over len(data)//4 WHOLE words only. A trailing 1-3 byte remainder
    is DROPPED, never padded -- padding it changes the CRC, and therefore the whole
    filename PCSX2 will never read."""
    whole = (len(data) // 4) * 4
    crc = 0
    for (word,) in struct.iter_unpack("<I", data[:whole]):
        crc ^= word
    return crc


def _serial_from_boot2(value: str) -> str:
    """The SERIAL label from a SYSTEM.CNF BOOT2 value, e.g.
    'cdrom0:\\SLES_529.50;1' -> 'SLES-52950'.

    CORRECTION 2, four steps: (a) the part after the last '\\'; if there is none, the part
    after the last ':'; if neither, the whole string. (b) cut at the last ';' (the
    ISO9660 version suffix). (c) VALIDATE against PCSX2's own wildcard test, _SERIAL_RE
    ('????_???.??*' / '????-???.??*'); a value that fails is CLEARED to "" rather than
    kept as a best guess (correction 3: PCSX2 then names the override file by bare CRC
    alone). (d) remove '.', turn '_' into '-', uppercase."""
    v = value.strip()
    if "\\" in v:
        tail = v.rsplit("\\", 1)[1]
    elif ":" in v:
        tail = v.rsplit(":", 1)[1]
    else:
        tail = v
    tail = tail.rsplit(";", 1)[0]
    if not _SERIAL_RE.match(tail):
        return ""                                     # cleared, not guessed -- see correction 3
    return tail.replace(".", "").replace("_", "-").upper()


def _key(serial: str, crc: int) -> str:
    """'<SERIAL>_<CRC:08X>', or bare '<CRC:08X>' when the serial was cleared.

    CORRECTION 3: when _serial_from_boot2 clears the serial, PCSX2 names the override
    file '<CRC>.ini' with no serial part at all -- callers of identify() must be able to
    handle that bare-CRC shape, not assume a serial is always present."""
    crc_hex = f"{crc:08X}"
    return f"{serial}_{crc_hex}" if serial else crc_hex


def _boot_components(boot2_value: str) -> list[str]:
    """The ISO9660 path components (root to boot file, device prefix stripped) named by a
    BOOT2 value, e.g. 'cdrom0:\\SLES_529.50;1' -> ['SLES_529.50;1']; a subdirectory boot
    ('cdrom0:\\SUBDIR\\GAME.ELF;1') -> ['SUBDIR', 'GAME.ELF;1']. Handles the no-backslash
    form ('cdrom0:SLES_529.50;1') the same way _serial_from_boot2 does. Components still
    carry their raw ';N' suffix -- _resolve_path strips it per component, not here (that
    is CORRECTION 4, applied where the actual disc walk happens)."""
    val = boot2_value.strip().replace("/", "\\")
    parts = val.split("\\")
    if len(parts) > 1 and parts[0].endswith(":") and parts[0].rstrip(":").lower().startswith("cdrom"):
        parts = parts[1:]                             # 'cdrom0:' consumed its own component
    elif len(parts) == 1 and ":" in parts[0]:
        parts = [parts[0].rsplit(":", 1)[1]]           # no backslash: prefix glued to the filename
    return [p for p in parts if p]


# ── ISO9660 primitives, shared by every container type ────────────────────────────────
def _dir_entries(source, lba: int, size: int) -> list[tuple[str, int, int, bool]]:
    """Parse the ISO9660 directory records in one extent into [(name, ext_lba, ext_len,
    is_dir)]. `name` still carries its ';N' version suffix -- callers normalise it at the
    comparison site (CORRECTION 4 applies the same normalisation to the wanted name and
    the disc name alike). A malformed individual record is skipped rather than aborting
    the whole directory."""
    data = source.read(lba, size)
    out: list[tuple[str, int, int, bool]] = []
    off, n = 0, len(data)
    while off < n:
        ln = data[off]
        if ln == 0:
            # a zero-length "record" marks padding to the next logical sector boundary --
            # directory records never straddle a 2048-byte sector.
            off = (off // 2048 + 1) * 2048
            continue
        if off + ln > n:
            break                                     # truncated tail -- keep what parsed
        rec = data[off:off + ln]
        try:
            ext_lba = struct.unpack_from("<I", rec, 2)[0]
            ext_len = struct.unpack_from("<I", rec, 10)[0]
            flags = rec[25]
            nlen = rec[32]
            name = rec[33:33 + nlen].decode("latin-1")
        except (struct.error, IndexError, UnicodeDecodeError):
            off += ln
            continue
        out.append((name, ext_lba, ext_len, bool(flags & 0x02)))
        off += ln
    return out


def _resolve_path(source, root_lba: int, root_size: int,
                   components: list[str]) -> tuple[int, int] | None:
    """Walk ISO9660 PATH COMPONENTS from the root directory down to a target file,
    returning its (lba, EXACT length) or None if any step is missing.

    CORRECTION 4: every component -- not just the last -- has its ';N' version suffix
    stripped and is matched case-insensitively (both the wanted name and the disc's own
    directory-record name go through the same normalisation, since a stray version
    suffix could show up on either side). A directory-flagged record is rejected on the
    FINAL (must-be-a-file) step, and a file-flagged record is rejected on every
    INTERMEDIATE (must-be-a-directory) step, so a same-named file and directory can never
    be confused for one another. The boot ELF is not always in the root directory."""
    lba, size = root_lba, root_size
    last = len(components) - 1
    for i, raw in enumerate(components):
        want = raw.split(";")[0].upper()
        need_dir = i != last
        hit = None
        for name, ext_lba, ext_len, is_dir in _dir_entries(source, lba, size):
            if name.split(";")[0].upper() != want:
                continue
            if is_dir != need_dir:
                continue
            hit = (ext_lba, ext_len)
            break
        if hit is None:
            return None
        lba, size = hit
    return lba, size


def _read_pvd(source) -> tuple[int, int]:
    """The root directory's (lba, size) from the Primary Volume Descriptor at LBA 16.
    Raises _DiscFail(final=True) if the CD001 magic is missing -- this genuinely isn't an
    ISO9660 volume. An I/O problem while reading raises separately, from source.read()
    itself, as non-final (a real read error is not the same conclusion as "not a disc")."""
    pvd = source.read(16, 2048)
    if pvd[1:6] != b"CD001":
        raise _DiscFail("Not a readable disc image", True)
    root = pvd[156:156 + 34]
    root_lba = struct.unpack_from("<I", root, 2)[0]
    root_size = struct.unpack_from("<I", root, 10)[0]
    return root_lba, root_size


def _boot2_value(cnf_text: str) -> str | None:
    """The RHS of SYSTEM.CNF's 'BOOT2 = ...' line, whitespace-trimmed, or None. Only
    BOOT2 is recognised (the PS2 key); a bare 'BOOT' line marks a PS1 disc, which is out
    of scope here and must not be misread as a PS2 game."""
    for line in cnf_text.splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip().upper() == "BOOT2":
            return v.strip()
    return None


def _identify_iso9660(source) -> str:
    """Full derivation over an already-opened ISO9660 SOURCE: SYSTEM.CNF -> BOOT2 line ->
    boot ELF -> XOR-fold CRC -> key. Raises _DiscFail with a plain-English reason on any
    failure; never returns a partial or guessed key."""
    root_lba, root_size = _read_pvd(source)
    cnf_hit = _resolve_path(source, root_lba, root_size, ["SYSTEM.CNF"])
    if cnf_hit is None:
        raise _DiscFail("No SYSTEM.CNF on this disc", True)
    cnf_lba, cnf_size = cnf_hit
    cnf_text = source.read(cnf_lba, cnf_size).decode("latin-1", "replace")
    boot2 = _boot2_value(cnf_text)
    if not boot2:
        raise _DiscFail("This disc does not name a boot program", True)
    components = _boot_components(boot2)
    if not components:
        raise _DiscFail("The boot program named on this disc is missing", True)
    elf_hit = _resolve_path(source, root_lba, root_size, components)
    if elf_hit is None:
        raise _DiscFail("The boot program named on this disc is missing", True)
    elf_lba, elf_len = elf_hit
    elf_data = source.read(elf_lba, elf_len)          # EXACT recorded length, never sector-rounded
    crc = _elf_crc(elf_data)
    serial = _serial_from_boot2(boot2)
    return _key(serial, crc)


# ── plain raw-sector sources: .iso/.img/.dump (fixed 2048/0) and .bin (probed) ───────
class _FileSource:
    """A .read(lba, nbytes) source backed by an on-disk raw image with a fixed (stride,
    offset) physical sector layout -- stride = physical bytes per sector, offset = where
    the 2048-byte logical user payload starts within each physical sector."""

    def __init__(self, path: Path, stride: int, offset: int) -> None:
        self._f = open(path, "rb")                    # OSError propagates to the caller
        self._stride = stride
        self._offset = offset

    def read(self, lba: int, nbytes: int) -> bytes:
        out = bytearray()
        remaining = nbytes
        cur = lba
        while remaining > 0:
            want = min(2048, remaining)
            try:
                self._f.seek(cur * self._stride + self._offset)
                chunk = self._f.read(want)
            except OSError as exc:
                raise _DiscFail("Not a readable disc image", False) from exc
            if len(chunk) < want:
                # short read: either genuinely truncated, or a transient hiccup (e.g. an
                # SD card that just dropped out mid-read) -- never brand the disc bad for
                # it (final=False), per the module docstring's "when in doubt" rule.
                raise _DiscFail("Not a readable disc image", False)
            out += chunk
            remaining -= want
            cur += 1
        return bytes(out)

    def close(self) -> None:
        try:
            self._f.close()
        except OSError:
            pass

    def __enter__(self) -> _FileSource:
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def _open_plain(path: Path, stride: int, offset: int) -> _FileSource:
    try:
        return _FileSource(path, stride, offset)
    except OSError as exc:
        raise _DiscFail("Not a readable disc image", False) from exc


def _open_bin(path: Path) -> _FileSource:
    """Probe candidate raw-sector layouts for a .bin -- it doesn't self-describe its
    stride the way a .chd's metadata can. Tries 2352 and 2448-byte physical sectors at
    the two data offsets PS2 discs actually use, then a 2048 fallback (a mislabeled plain
    image), by checking for the CD001 magic at LBA 16 under each. A genuine read problem
    (not merely a magic mismatch) propagates immediately rather than trying more
    layouts -- it will fail identically under every stride."""
    for stride, offset in _BIN_LAYOUTS:
        source = _open_plain(path, stride, offset)
        try:
            pvd = source.read(16, 2048)
        except _DiscFail:
            source.close()
            raise
        if pvd[1:6] == b"CD001":
            return source
        source.close()
    raise _DiscFail("Not a readable disc image", True)


# ── .cue: resolve the first data track's file, then treat it as .bin ─────────────────
_CUE_FILE_RE = re.compile(r'^\s*FILE\s+"?([^"]+?)"?\s+\S+\s*$', re.IGNORECASE)
_CUE_TRACK_RE = re.compile(r'^\s*TRACK\s+\d+\s+(\S+)\s*$', re.IGNORECASE)


def _resolve_case_insensitive(directory: Path, name: str) -> Path | None:
    direct = directory / name
    if direct.is_file():
        return direct
    want = name.lower()
    try:
        for child in directory.iterdir():
            if child.name.lower() == want:
                return child
    except OSError:
        pass
    return None


def _cue_data_file(cue_path: Path) -> Path:
    """The first referenced FILE that has at least one non-AUDIO TRACK (the disc's first
    data track), resolved relative to the cue's OWN directory, matched CASE
    INSENSITIVELY -- the filename spelled inside the .cue text often doesn't match the
    actual on-disk casing."""
    try:
        text = cue_path.read_text(encoding="latin-1")
    except OSError as exc:
        raise _DiscFail("Not a readable disc image", False) from exc
    blocks: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        m = _CUE_FILE_RE.match(line)
        if m:
            current = []
            blocks.append((m.group(1), current))
            continue
        m = _CUE_TRACK_RE.match(line)
        if m and current is not None:
            current.append(m.group(1).upper())
    for fname, tracks in blocks:
        if any(t != "AUDIO" for t in tracks):
            base = Path(fname.replace("\\", "/")).name  # ignore any path baked into the cue text
            resolved = _resolve_case_insensitive(cue_path.parent, base)
            if resolved is None:
                raise _DiscFail("The disc file listed in this cue sheet is missing", True)
            return resolved
    raise _DiscFail("This cue sheet has no data track", True)


# ── .chd: partial extraction via EmuDeck's bundled chdman5 ────────────────────────────
class _ChdSource:
    """A .read(lba, nbytes) source backed by a .chd, via chdman5's PARTIAL extraction
    (extractraw -isb/-ib) -- never the whole disc. Every extracted slice lands in its own
    tempfile.mkdtemp() dir, removed in close()/__exit__ even on failure."""

    def __init__(self, path: Path, stride: int, offset: int) -> None:
        self._path = path
        self._stride = stride
        self._offset = offset
        self._tmpdir = tempfile.mkdtemp(prefix="mad-ps2disc-")

    def _extract(self, start_byte: int, length: int) -> bytes:
        out = Path(self._tmpdir) / "slice.bin"
        try:
            r = subprocess.run(
                [str(_CHDMAN), "extractraw", "-i", str(self._path), "-o", str(out),
                 "-f", "-isb", str(start_byte), "-ib", str(length)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_CHD_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise _DiscFail("This disc took too long to read", False) from exc
        except OSError as exc:
            raise _DiscFail("chdman is not installed", False) from exc
        if r.returncode != 0:
            raise _DiscFail("This disc could not be read", False)
        try:
            return out.read_bytes()
        except OSError as exc:
            raise _DiscFail("This disc could not be read", False) from exc
        finally:
            try:
                out.unlink()
            except OSError:
                pass

    def read(self, lba: int, nbytes: int) -> bytes:
        n_sectors = (nbytes + 2047) // 2048
        blob = self._extract(lba * self._stride, n_sectors * self._stride)
        if self._offset == 0 and self._stride == 2048:
            data = blob
        else:
            data = b"".join(
                blob[i * self._stride + self._offset:i * self._stride + self._offset + 2048]
                for i in range(n_sectors)
            )
        if len(data) < nbytes:
            raise _DiscFail("Not a readable disc image", False)
        return data[:nbytes]

    def close(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def __enter__(self) -> _ChdSource:
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def _open_chd(path: Path) -> _ChdSource:
    """Probe the supported .chd sector layouts (see _CHD_LAYOUTS) by reading the PVD
    under each. A genuine chdman5 problem (missing binary, a timeout, a failing
    extraction) propagates immediately as non-final -- it is never a reason to keep
    guessing layouts, only a magic mismatch is."""
    if not os.access(_CHDMAN, os.X_OK):
        raise _DiscFail("chdman is not installed", False)
    for stride, offset in _CHD_LAYOUTS:
        source = _ChdSource(path, stride, offset)
        try:
            pvd = source.read(16, 2048)
        except _DiscFail:
            source.close()
            raise
        if pvd[1:6] == b"CD001":
            return source
        source.close()
    raise _DiscFail("Not a readable disc image", True)


# ── public entry point ────────────────────────────────────────────────────────────────
def _identify(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in _PLAIN_EXTS:
        with _open_plain(p, 2048, 0) as source:
            return _identify_iso9660(source)
    if ext == ".bin":
        with _open_bin(p) as source:
            return _identify_iso9660(source)
    if ext == ".cue":
        target = _cue_data_file(p)
        with _open_bin(target) as source:
            return _identify_iso9660(source)
    if ext == ".chd":
        with _open_chd(p) as source:
            return _identify_iso9660(source)
    # Any other extension (.cso/.zso/.gz/.mdf/.m3u/.elf/.isz/.nrg/.ciso/...) is out of
    # scope -- a wrong answer here would be undetectable, so this NEVER attempts a read.
    raise _DiscFail("This disc format is not supported yet", True)


def identify(path: str) -> tuple[str | None, str, bool]:
    """The `resolver` contract of lib/ident_cache.py: derive PCSX2's <SERIAL>_<CRC> (or
    bare <CRC>) key straight from a disc image. NEVER RAISES -- every failure path is
    caught here and turned into a plain-English reason plus a final/non-final call (see
    the module docstring for why that split matters). When genuinely unsure this always
    prefers final=False: a wrongly-permanent failure would brand a good disc unreadable
    forever in ident_cache's on-disk store."""
    try:
        key = _identify(Path(path))
        return key, "", True
    except _DiscFail as fail:
        return None, fail.why, fail.final
    except Exception:
        # A bug in THIS module, not a fact about the disc -- never let it escape as an
        # exception, and never treat it as a permanent verdict on the disc either.
        return None, "Could not read this disc image", False
