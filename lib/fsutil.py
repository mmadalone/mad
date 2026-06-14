"""
fsutil.py — atomic file writes + recoverable deletes for the launcher tools.

Three things every config/gamelist/cfg writer in this repo needs, that were
previously re-implemented per-file (or skipped, leaving truncated files on a
mid-write crash):

  * ``atomic_write_text`` / ``atomic_write_bytes`` / ``atomic_write`` — write to
    a sibling temp file in the SAME directory, then ``os.replace()`` it onto the
    target, so the live file is only ever swapped whole and can never be left
    half-written. Canonicalises THREE existing inline copies:
    ``lib/localpolicy.dump`` (88-98), ``lib/retroarch_cfg._atomic_write``
    (213-217), and the open('w')/write_text bodies in the standalone *_cfg.py
    backends.

  * ``atomic_write_json`` — thin JSON convenience over ``atomic_write_text``;
    replaces the non-atomic ``path.write_text(json.dumps(...))`` in
    ``tester_cmds._write_json`` and the Tk testers' save paths.

  * ``recoverable_delete`` — project rule #5 ("never delete user data; move it
    to a recoverable _TMP and REPORT the path"). Canonicalises the inline
    move-to-_TMP in ``steam-collection-sync.py`` (141-155). Returns the _TMP dir
    so the caller can print it.

  * ``atomic_replace_artwork`` — fixes the unlink-before-copy artwork race in
    ``steam-fetch-media.place`` (93-97): copy the new art to a temp, replace
    onto the final name, and only THEN sweep the differently-suffixed siblings,
    so an interruption never leaves a game with no artwork at all.

Pure stdlib; importable as ``from lib.fsutil import ...`` (top-level scripts) or
``from . import fsutil`` (sibling lib modules).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import time
from pathlib import Path

from . import staterev

__all__ = [
    "atomic_write_text",
    "atomic_write_bytes",
    "atomic_write",
    "atomic_write_json",
    "recoverable_delete",
    "atomic_replace_artwork",
]

# Suffix for the in-place temp file. Matches retroarch_cfg's '.router-tmp' so a
# stray temp left by a hard kill is recognisably ours.
_TMP_SUFFIX = ".router-tmp"


def _atomic_swap(target: Path, write_tmp) -> None:
    """Run ``write_tmp(tmp_path)`` to populate a sibling temp, then atomically
    ``os.replace`` it onto ``target``. Creates parent dirs first; on any OSError
    the temp is removed and the error re-raised, so a failed write never leaves a
    stray temp or a half-written target. (Lifted from localpolicy.dump /
    retroarch_cfg._atomic_write — one copy now instead of three.)"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # with_name(name + suffix), not with_suffix(...), so multi-dot and
    # extension-less names ("gamelist") get a valid same-dir temp too.
    tmp = target.with_name(target.name + _TMP_SUFFIX)
    try:
        write_tmp(tmp)
        os.replace(tmp, target)
    except BaseException:
        # BaseException, not just OSError: write_tmp may raise e.g.
        # UnicodeEncodeError (a corrupt game-title surrogate, or a caller passing
        # encoding='ascii' on non-ASCII text) — still clean up the stray temp.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(target: Path, content: str, *,
                      encoding: str = "utf-8",
                      backup_once_suffix: str | None = None) -> None:
    """Atomically write text ``content`` to ``target`` (see ``_atomic_swap``).

    ``backup_once_suffix`` (e.g. ``'.router-backup'``): if given and ``target``
    already exists and ``target + suffix`` does NOT yet exist, ``copy2`` the
    current target to that backup ONCE before swapping — a one-time "original"
    snapshot for the supermodel/sinden cfg rewriters that want to preserve the
    file as it was before the tool first touched it.
    """
    target = Path(target)
    if backup_once_suffix:
        backup = target.with_name(target.name + backup_once_suffix)
        if target.exists() and not backup.exists():
            shutil.copy2(target, backup)
    _atomic_swap(target, lambda tmp: tmp.write_text(content, encoding=encoding))
    # A config/cfg/settings write happened (this is the chokepoint for text +
    # JSON saves) — invalidate revision-cached page data. Over-bumping is safe.
    staterev.bump("config")


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Atomically write binary ``data`` to ``target`` (see ``_atomic_swap``)."""
    _atomic_swap(target, lambda tmp: tmp.write_bytes(data))


def atomic_write(target: Path, data) -> None:
    """Atomically write ``data`` accepting str (UTF-8) OR bytes — dispatches to
    the text/bytes helper by type."""
    if isinstance(data, (bytes, bytearray)):
        atomic_write_bytes(target, bytes(data))
    elif isinstance(data, str):
        atomic_write_text(target, data)
    else:
        raise TypeError(
            f"atomic_write expects str or bytes, got {type(data).__name__}")


def atomic_write_json(path: Path, data, *, indent: int = 2) -> None:
    """Atomically write ``data`` as JSON. Replaces the non-atomic
    ``path.write_text(json.dumps(...))`` so every JSON save is crash-safe."""
    atomic_write_text(Path(path), json.dumps(data, indent=indent))


def recoverable_delete(paths, *, tmp_base: Path, tag: str,
                       recovery_note: str) -> Path:
    """Move ``paths`` into a recoverable ``_TMP`` dir instead of deleting them
    (project rule #5 — never ``rm`` user data).

    Creates ``tmp_base / f'_TMP_{tag}-<YYYYmmdd-HHMMSS>'``, ``shutil.move``s each
    path into it (basename collisions get a ``__N`` suffix so nothing is
    clobbered), and writes/append a ``RECOVERY.txt`` holding ``recovery_note``
    plus a per-file "moved-name <- original-path" manifest. Returns the _TMP dir
    Path so the caller can PRINT it (rule #5 requires reporting the real path).

    ``tmp_base`` MUST be on the SAME filesystem as the files for an instant move:
    SD-card media → ``Path('/run/media/deck/1tbDeck')``; ``/home`` files →
    ``Path.home()/'Downloads'/'_TMP'`` (a cross-fs base still works — shutil
    falls back to copy+delete — just slower).

    A single ``Path`` may be passed instead of a list. Missing paths are noted
    and skipped rather than raising.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = [Path(p) for p in paths]

    ts = time.strftime("%Y%m%d-%H%M%S")
    tmp = Path(tmp_base) / f"_TMP_{tag}-{ts}"
    tmp.mkdir(parents=True, exist_ok=True)

    manifest: list[str] = []
    err: BaseException | None = None
    try:
        for p in paths:
            if not p.exists() and not p.is_symlink():
                manifest.append(f"(not found, skipped)\t<-\t{p}")
                continue
            dest = tmp / p.name
            if dest.exists():  # two sources share a basename — don't clobber
                n = 1
                while (tmp / f"{p.stem}__{n}{p.suffix}").exists():
                    n += 1
                dest = tmp / f"{p.stem}__{n}{p.suffix}"
            shutil.move(str(p), str(dest))
            manifest.append(f"{dest.name}\t<-\t{p}")
    except BaseException as exc:  # disk-full / card-yanked mid-batch, etc.
        err = exc
        try:                      # let callers report where partial moves landed
            exc.tmp_dir = tmp
        except Exception:
            pass
        raise
    finally:
        # RECOVERY.txt is written even when a move raised partway through, so the
        # files already moved are never left in an undocumented _TMP (rule #5).
        block = [recovery_note.rstrip("\n"), ""]
        if err is not None:
            block += [f"*** INTERRUPTED ({type(err).__name__}: {err}) — only the "
                      "files listed below were moved; any others remain at their "
                      "original paths. ***", ""]
        block += [f"Moved here {ts} by recoverable_delete — rule #5 (never delete "
                  "user data; this is recoverable).",
                  "Restore: move each entry below back to the original path on "
                  "the right.", ""]
        block += manifest
        block.append("")
        # append-mode so a same-second second call with the same tag (sharing
        # this dir) extends the note; best-effort so a failing write here never
        # masks the original move error.
        try:
            with (tmp / "RECOVERY.txt").open("a", encoding="utf-8") as f:
                f.write("\n".join(block) + "\n")
        except OSError:
            pass
    return tmp


def atomic_replace_artwork(dst_dir: Path, stem: str, src_path: Path) -> Path:
    """Place ``src_path`` as ``dst_dir/<stem><src ext>`` without ever leaving the
    game with NO artwork (fixes the unlink-before-copy race in
    ``steam-fetch-media.place``).

    Copies ``src_path`` to a same-dir temp, ``os.replace``s it onto the final
    name, and ONLY AFTER that succeeds removes the OTHER differently-suffixed
    ``<stem>.*`` siblings (e.g. a stale ``Game.jpg`` when we just wrote
    ``Game.png``). Returns the final path. So an interruption leaves either the
    old art or the new art in place — never nothing.
    """
    dst_dir = Path(dst_dir)
    src_path = Path(src_path)
    dst_dir.mkdir(parents=True, exist_ok=True)

    suffix = src_path.suffix
    final = dst_dir / (stem + suffix)
    tmp = dst_dir / (stem + _TMP_SUFFIX + suffix)  # e.g. Game.router-tmp.png
    try:
        shutil.copy2(src_path, tmp)
        os.replace(tmp, final)
    except BaseException:  # any failure: clean the temp, never leave it behind
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    # Now that the new art is safely the final file, sweep stale siblings with a
    # different extension. Compare by name so the just-written file is never
    # caught by its own glob.
    for other in dst_dir.glob(glob.escape(stem) + ".*"):
        if other.name == final.name:
            continue
        try:
            other.unlink()
        except OSError:
            pass
    return final


if __name__ == "__main__":  # smoke test: `python3 lib/fsutil.py`
    import tempfile as _tf

    _w = Path(_tf.mkdtemp(prefix="fsutil-selftest-"))
    try:
        _t = _w / "a" / "b.txt"
        atomic_write_text(_t, "round-trip ☺\n")
        assert _t.read_text(encoding="utf-8") == "round-trip ☺\n"
        assert not list(_w.rglob("*.router-tmp")), "stray temp left behind"

        atomic_write_bytes(_w / "b.bin", b"\x00\xff")
        assert (_w / "b.bin").read_bytes() == b"\x00\xff"
        atomic_write_json(_w / "c.json", {"k": 1, "v": [1, 2]})
        assert json.loads((_w / "c.json").read_text()) == {"k": 1, "v": [1, 2]}

        _v = _w / "victim.txt"
        _v.write_text("x")
        _base = _w / "tmpbase"
        _base.mkdir()
        _ret = recoverable_delete(_v, tmp_base=_base, tag="selftest",
                                  recovery_note="smoke")
        assert _ret.parent == _base and _ret.name.startswith("_TMP_selftest-")
        assert (_ret / "RECOVERY.txt").exists() and not _v.exists()

        _src = _w / "s.png"
        _src.write_bytes(b"P")
        _fin = atomic_replace_artwork(_w / "art", "Game", _src)
        assert _fin == _w / "art" / "Game.png" and _fin.read_bytes() == b"P"
    finally:
        shutil.rmtree(_w, ignore_errors=True)
    print("fsutil self-test OK")
