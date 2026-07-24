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
import threading
from pathlib import Path

from .. import mad_backup
from ..mad_config import backup_targets
from ..policy import load_merged
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


def _clean_env() -> dict:
    """A copy of the environment with Steam's Game Mode overlay stripped from LD_PRELOAD. Steam
    launches ES-DE (and thus this daemon) with LD_PRELOAD pointing at gameoverlayrenderer.so for BOTH
    arches; the 32-bit one can't load into our 64-bit tools (tar/du/...), so ld.so prints a harmless
    'object ... from LD_PRELOAD cannot be preloaded (wrong ELF class): ignored' ERROR for every spawn
    - pure noise that clutters the streamed backup output. Nothing here needs the Steam overlay."""
    env = dict(os.environ)
    pre = env.get("LD_PRELOAD", "")
    if "gameoverlayrenderer.so" in pre:
        kept = [p for p in pre.replace(":", " ").split() if "gameoverlayrenderer.so" not in p]
        if kept:
            env["LD_PRELOAD"] = " ".join(kept)
        else:
            env.pop("LD_PRELOAD", None)
    return env


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
            stdin=subprocess.DEVNULL, text=True, start_new_session=True, env=_clean_env())
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


class RunFullStream(_ScriptStream):
    """Runs deck-backup.sh --yes with the chosen include flags, streaming its
    output lines. Holds _RUN_ACTIVE for its whole life."""

    def __init__(self, argv: list):
        super().__init__()
        self._argv = argv

    def run(self):
        rc = -1
        try:
            proc = self._spawn(self._argv, merge_stderr=True)
            for line in proc.stdout:
                if self.stopped.is_set():
                    break
                line = line.rstrip()
                if line:
                    self.emit({"line": line})
            if not self.stopped.is_set():
                rc = proc.wait()
        finally:
            # done ALWAYS precedes closed (even on exceptions) so the page can
            # clear its "Backing up…" sticky; rc -1 = did not finish cleanly.
            self.emit({"done": True, "rc": rc})
            _RUN_ACTIVE.release()


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
        include = params.get("include") or {}
        argv = [str(SCRIPT), "--yes"]
        dest = params.get("dest")
        if dest:                                 # optional user-picked destination
            argv += ["--dest", _validate_dest(dest)]
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
