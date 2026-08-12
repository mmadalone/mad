"""Backup page methods (MAD native panel, phase 5A).

The file logic lives in lib/mad_backup (Tk-free since R5); this module only
wraps it in RPC methods plus two Streams around deck-backup.sh:

- backup.sizes      -> stream of {key, bytes} per category (deck-backup.sh
                       --sizes), {done:true} at the end. Sizes are cached for
                       the daemon's lifetime (one panel session) so re-entering
                       the page replays them instantly instead of re-running du.
- backup.run_full   -> stream of {line} per script output line, {done, rc} at
                       the end. The child dies with the daemon (Tk parity) —
                       the page warns not to close MAD while it runs.

Dispatch classes: backup.restore / backup.reset_local are FAST — they write
controller-policy.local.toml and every local.toml writer must run inline on
the stdin thread (single-writer invariant). The read-only/emulator-file ops
run on the worker pool (slow=True).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .. import mad_backup
from ..mad_config import backup_targets
from ..policy import load_merged
from .env_hygiene import clean_env
from .rpc import RpcError, Stream, method

LAUNCHERS = Path(__file__).resolve().parents[2]
SCRIPT = LAUNCHERS / "deck-backup.sh"

SIZE_KEYS = ("esde", "emu", "saves", "bios", "cores", "bezels",
             "rpcs3games", "pcsx2tex", "ryujinxgames", "roms", "romsint", "openbor", "media")
# include-map key -> deck-backup.sh flag stem (the Tk run_full map).
FULL_FLAGS = {"esde": "esde", "emu": "emu", "saves": "saves", "bios": "bios",
              "cores": "cores", "bezels": "bezels", "rpcs3games": "rpcs3",
              "pcsx2tex": "pcsx2tex", "ryujinxgames": "ryujinx",
              "roms": "roms", "romsint": "romsint", "openbor": "openbor", "media": "media"}

_SIZES_CACHE: dict[str, int] = {}     # daemon-lifetime; guarded by _SIZES_LOCK
_SIZES_LOCK = threading.Lock()
_SIZES_CURRENT: dict = {"stream": None}  # single-flight: one du sweep at a time
_RUN_ACTIVE = threading.Lock()        # one full backup at a time


def _targets() -> dict:
    return backup_targets(load_merged())


DEST_FILE = LAUNCHERS / ".backup-dest"           # remembers the user's chosen destination
DEFAULT_DEST = os.path.expanduser("~/deck-config-backups")


def _source_roots() -> list:
    """The big-library trees deck-backup.sh archives (realpath'd), from its own
    --print-source-roots (the single source of truth - never duplicate the path list here). A dest
    inside one of these would make each successive full backup swallow the prior archives sitting
    there. Best-effort: on any error the list is empty (the LAUNCHERS guard still applies)."""
    try:
        out = subprocess.run([str(SCRIPT), "--print-source-roots"],
                             capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    return [os.path.realpath(ln.strip()) for ln in out.stdout.splitlines()
            if ln.strip().startswith("/")]


def _validate_dest(raw: str) -> str:
    """Expand + sanity-check a user-picked backup destination; return its abspath or
    raise RpcError. Refuses the MAD code tree and any tree being backed up (a backup would
    archive its own growing output), and anything that isn't a writable directory (creating it
    if the parent already exists — the picker only ever hands us an existing folder, this is
    defence in depth)."""
    path = os.path.abspath(os.path.expanduser(raw or ""))
    # Resolve symlinks for the forbidden-location checks so a symlinked dest can't slip a backup
    # into the MAD code tree or a backed-up tree. LAUNCHERS is already .resolve()'d.
    real = os.path.realpath(path)
    launchers = str(LAUNCHERS)
    if real == launchers or real.startswith(launchers + os.sep):
        raise RpcError("EINVAL", "pick a folder outside the MAD code tree")
    for root in _source_roots():
        if real == root or real.startswith(root + os.sep):
            raise RpcError("EINVAL", "pick a folder outside the trees being backed up")
    if not os.path.isdir(path):
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise RpcError("EINVAL", f"no such folder: {path}")
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise RpcError("EINVAL", f"can't create {path}: {exc}")
    if not os.access(path, os.W_OK):
        raise RpcError("EINVAL", f"folder is not writable: {path}")
    return path


def _remembered_dest() -> str:
    """The remembered destination if it still resolves to a usable writable dir, else
    the built-in default. Read-only (never creates a dir) so a stale/removed drive
    simply falls back instead of failing."""
    try:
        stored = DEST_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if stored and os.path.isdir(stored) and os.access(stored, os.W_OK):
        return os.path.abspath(stored)
    return DEFAULT_DEST


FORMAT_FILE = LAUNCHERS / ".backup-format"       # remembers the config-archive FORMAT choice
COMPRESS_FILE = LAUNCHERS / ".backup-compress"   # legacy boolean choice (migrated on first read)
# zstd=.tar.zst (the script's actual default now), gzip=.tar.gz (legacy alias - deck-backup.sh
# itself aliases a "gzip" --format to zstd, so no .tar.gz is ever written), store=.tar,
# mirror=browsable folder. _remembered_format()'s default/migration below is DELIBERATELY left
# "gzip" (not normalized to zstd here) - see backup.set_format's docstring.
_FORMATS = ("gzip", "store", "mirror", "zstd")


def _remembered_format() -> str:
    """The remembered config-archive format (default 'gzip'). Reads .backup-format; if that's absent
    or invalid, migrates the older boolean .backup-compress choice ("0" -> store, else gzip)."""
    try:
        fmt = FORMAT_FILE.read_text(encoding="utf-8").strip()
        if fmt in _FORMATS:
            return fmt
    except OSError:
        pass
    try:
        return "store" if COMPRESS_FILE.read_text(encoding="utf-8").strip() == "0" else "gzip"
    except OSError:
        return "gzip"


class _ScriptStream(Stream):
    """Shared child-process plumbing for the deck-backup.sh streams.

    The script is silent for minutes between output lines (du/tar legs), so a
    stopped check inside the read loop alone never fires — the thread blocks
    in readline. Each child therefore runs in its OWN process group and a
    stop-watcher thread killpg()s it the moment stopped is set: the readline
    returns via EOF, run() unwinds, and cleanup() (also killpg, idempotent)
    remains the belt-and-braces for every other path. Plain terminate() would
    hit only bash and orphan the in-flight tar/du grandchild."""

    def __init__(self):
        super().__init__()
        self._proc = None

    def _spawn(self, argv: list, merge_stderr: bool = False):
        # stdin MUST be /dev/null — the daemon's stdin is the protocol pipe.
        self._proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, start_new_session=True, env=clean_env())
        threading.Thread(target=self._stop_watcher, daemon=True,
                         name=f"{self.token}-stopwatch").start()
        return self._proc

    def _stop_watcher(self):
        self.stopped.wait()
        self._kill_child()

    def _kill_child(self):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.wait(timeout=1)  # Reap; no zombie until GC.
        except (OSError, subprocess.TimeoutExpired):
            pass

    def cleanup(self):
        self._kill_child()


class SizesStream(_ScriptStream):
    """Replays cached category sizes, then computes the missing ones via
    deck-backup.sh --sizes (du over big trees — seconds to minutes)."""

    def run(self):
        with _SIZES_LOCK:
            cached = dict(_SIZES_CACHE)
        for key, n in cached.items():
            self.emit({"key": key, "bytes": n})
        if all(k in cached for k in SIZE_KEYS):
            self.emit({"done": True})
            return
        proc = self._spawn([str(SCRIPT), "--sizes"])
        for line in proc.stdout:
            if self.stopped.is_set():
                break
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                with _SIZES_LOCK:
                    _SIZES_CACHE[parts[0]] = int(parts[1])
                self.emit({"key": parts[0], "bytes": int(parts[1])})
        if self.stopped.is_set():
            return  # Killed — don't claim completion.
        proc.wait()
        self.emit({"done": True})


class RunFullStream(Stream):
    """Runs deck-backup.sh --yes DETACHED as a registered job (kind backup.run_full)
    and tails its .out - so a full local backup survives the panel closing and shows
    in the Transfers tile like every other transfer. Event shapes unchanged ({line}
    per line, {done, rc} at the end). Holds _RUN_ACTIVE for the tail's life; stopping
    the STREAM does not stop the JOB (transfers.stop does)."""

    def __init__(self, argv: list):
        super().__init__()
        self._argv = argv
        self._tail = None

    def run(self):
        from . import cloud_cmds
        rc = -1
        tail = None
        try:
            job_id, proc = cloud_cmds._spawn_registered(
                self._argv, kind="backup.run_full", source="panel",
                self_registering=False)
            tail = cloud_cmds._JobTailStream(job_id, proc=proc, clears_marker=False)
            # The tail is a helper we drive INLINE (never start()ed), so drop the stream
            # registration Stream.__init__ made for it - nothing would ever pop that
            # entry, leaking one dead token per full backup.
            from .rpc import _STREAMS, _STREAMS_LOCK
            with _STREAMS_LOCK:
                _STREAMS.pop(tail.token, None)
            tail.stopped = self.stopped          # one stop flag: stopping me stops the tail
            tail.emit = self.emit                # events flow out under MY token
            tail.run()                           # its finally emits the terminal {done, rc}
        except Exception as exc:
            print(f"backup.run_full: {exc!r}", file=sys.stderr)   # visible in mad-backend.log
            if tail is None:                     # spawn failed before the tail could report
                self.emit({"done": True, "rc": rc})
        finally:
            _RUN_ACTIVE.release()

    def cleanup(self):
        pass   # tail-only: never kill the detached backup job


@method("backup.sizes")
def _backup_sizes(params):
    """Single-flight: section re-entry mid-sweep re-attaches to the running
    stream instead of piling up parallel du storms. The response carries the
    cache snapshot so a late subscriber doesn't miss already-pushed keys."""
    from .rpc import _STREAMS, _STREAMS_LOCK
    with _SIZES_LOCK:
        cached = dict(_SIZES_CACHE)
    current = _SIZES_CURRENT["stream"]
    with _STREAMS_LOCK:
        live = current is not None and current.token in _STREAMS
    if live:
        return {"stream": current.token, "already": True, "sizes": cached}
    stream = SizesStream()
    _SIZES_CURRENT["stream"] = stream
    try:
        return {"stream": stream.start(), "sizes": cached}
    except Exception:
        _SIZES_CURRENT["stream"] = None
        raise


@method("backup.run_full")
def _backup_run_full(params):
    if not _RUN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a full backup is already running")
    try:
        # The in-daemon lock only covers the TAIL now: the backup itself is a detached
        # job that outlives the panel (and _RUN_ACTIVE is fresh after a panel reopen),
        # and deck-backup.sh has no lock of its own - so consult the registry too, or a
        # second run would write the same archive concurrently. Same precheck shape as
        # cloud_cmds._stream_op, with the gameplay-frozen message it uses.
        from . import cloud_cmds
        for j in cloud_cmds._registry().live_jobs():
            if j.get("kind") == "backup.run_full":
                if j.get("paused_by") == "gameplay":
                    raise RpcError("EBUSY", "the backup is paused during gameplay - quit "
                                            "the game (or flip BACKUP DURING GAMEPLAY) "
                                            "and try again")
                raise RpcError("EBUSY", "a full backup is already running")
        include = params.get("include") or {}
        argv = [str(SCRIPT), "--yes"]
        dest = params.get("dest")
        if dest:                                 # optional user-picked destination
            argv += ["--dest", _validate_dest(dest)]
        fmt = params.get("format")               # config-archive format: gzip | store | mirror
        if fmt in _FORMATS:
            argv += ["--format", fmt]
        elif "compress" in params:               # back-compat: older bool param still honored
            argv.append("--compress" if params.get("compress") else "--no-compress")
        for key, flag in FULL_FLAGS.items():
            argv.append(f"--{flag}" if include.get(key) else f"--no-{flag}")
        return {"stream": RunFullStream(argv).start()}
    except Exception:
        # A validation error or a failed start()/spawn all leave the lock ours to drop
        # (start() never reached run()'s finally); release before propagating.
        _RUN_ACTIVE.release()
        raise


@method("backup.snapshot", slow=True)
def _backup_snapshot(params):
    return {"message": mad_backup.do_backup(_targets())}


@method("backup.restore")            # FAST: writes local.toml (single-writer)
def _backup_restore(params):
    return {"message": mad_backup.do_restore(_targets())}


@method("backup.reset_local")        # FAST: unlinks local.toml (single-writer)
def _backup_reset_local(params):
    return {"message": mad_backup.reset_local()}


@method("backup.restore_router", slow=True)
def _backup_restore_router(params):
    return {"message": mad_backup.restore_router_backups(_targets())}


@method("backup.mad_code", slow=True)
def _backup_mad_code(params):
    dest = params.get("dest")
    dest_dir = _validate_dest(dest) if dest else None
    return {"message": mad_backup.backup_mad_code(dest_dir=dest_dir)}


@method("backup.get_dest")
def _backup_get_dest(params):
    """The destination the local-backup buttons will use: the remembered folder if
    still usable, else ~/deck-config-backups. `default` lets the UI show/return-to it."""
    return {"dest": _remembered_dest(), "default": DEFAULT_DEST}


@method("backup.set_dest")
def _backup_set_dest(params):
    """Remember a user-picked destination (validated) for the local-backup buttons."""
    dest = _validate_dest(params.get("dest") or "")
    try:
        DEST_FILE.write_text(dest + "\n", encoding="utf-8")
    except OSError as exc:
        raise RpcError("EIO", f"couldn't remember the destination: {exc}")
    return {"dest": dest}


@method("backup.get_format")
def _backup_get_format(params):
    """The config-archive format the full backup uses: 'zstd' (.tar.zst, the script's actual
    default), 'store' (.tar), 'mirror' (a browsable folder tree), or the legacy 'gzip' alias
    (deck-backup.sh emits .tar.zst for it too - a stored "gzip" choice is a display-only
    mismatch until a later fork build adds a zstd radio entry; see the plan's flagged decisions).
    Migrates the legacy .backup-compress choice on first read."""
    return {"format": _remembered_format()}


@method("backup.set_format")
def _backup_set_format(params):
    """Remember the config/saves-archive format for RUN FULL BACKUP: 'zstd' (.tar.zst), 'store'
    (.tar), 'mirror' (browsable folder), or the legacy 'gzip' alias (deck-backup.sh emits
    .tar.zst for it regardless - see backup.get_format's docstring)."""
    fmt = str(params.get("format", "gzip"))
    if fmt not in _FORMATS:
        raise RpcError("EINVAL", f"unknown backup format {fmt!r} (use: {', '.join(_FORMATS)})")
    try:
        FORMAT_FILE.write_text(fmt + "\n", encoding="utf-8")
    except OSError as exc:
        raise RpcError("EIO", f"couldn't remember the backup format: {exc}")
    return {"format": fmt}
