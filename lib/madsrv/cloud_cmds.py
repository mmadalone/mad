"""Cloud (MEGA) backup page methods - thin RPC wrappers over deck-cloud.sh.

deck-cloud.sh is the single owner of every rclone call; this module only
exposes its subcommands to the MAD native panel:

- cloud.push / cloud.sync / cloud.restore_precious / cloud.restore_library ->
  STREAM the engine's output lines ({line} per line, {done, rc} at the end),
  same shape as backup.run_full. Only ONE cloud stream op runs at a time.
- cloud.status / cloud.snapshots / cloud.set_toggle -> fast bounded calls
  (slow=True: they shell out, so run on the worker pool, never the stdin thread).

The stream plumbing mirrors lib/madsrv/backup_cmds.py deliberately (a child in its
own process group, a stop-watcher that killpg()s it) and is kept self-contained
here rather than importing that module's private classes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from .. import backup_manifest, granular_backup   # cloud.push_games: shared planner + manifest writer
from .rpc import RpcError, Stream, method, stop_stream

LAUNCHERS = Path(__file__).resolve().parents[2]
ENGINE = LAUNCHERS / "deck-cloud.sh"

_RUN_ACTIVE = threading.Lock()   # one streamed cloud op at a time


# ---- interrupted-transfer marker + auto-resume + the single live-op handle ----
# The marker records a USER-initiated transfer's op so it can be resumed after the MAD panel is
# reopened. Written on start; cleared ONLY on a CLEAN finish (rc==0) or an explicit cancel - a kill
# / app-close LEAVES it so auto-resume can pick it up. Uploads auto-resume; restores wait for a
# confirm (surfaced by cloud.active pending_restore). Paths read the env at CALL time so they honour
# DECK_CLOUD_STATE_DIR (tests + deck-cloud.sh parity). The hook/timer backups don't go through here,
# so they never leave a marker - they self-heal on their next run instead.
def _state_dir():
    return Path(os.environ.get("DECK_CLOUD_STATE_DIR") or (Path.home() / ".config" / "deck-cloud"))


def _marker_path():
    return _state_dir() / "in_progress"


def _write_marker(op):        # op = the deck-cloud.sh subcommand + args (list, after ENGINE)
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        _marker_path().write_text("\t".join(op) + "\n")
    except OSError:
        pass


def _clear_marker():
    try:
        _marker_path().unlink()
    except OSError:
        pass


def _read_marker():
    try:
        line = _marker_path().read_text().strip()
    except OSError:
        return None
    return line.split("\t") if line else None


def _op_title(op):
    c = op[0] if op else ""
    return {"push-precious": "Backing up saves", "sync-library": "Syncing library",
            "push-games": "Backing up games", "push-bios": "Backing up BIOS",
            "push-esde": "Backing up ES-DE settings", "push-emucfg": "Backing up emulator config",
            "push-system": "Backing up system config",
            "push-controllers": "Backing up controller config",
            "restore-precious": "Restoring saves", "restore-library": "Restoring library",
            }.get(c, "Cloud transfer")


def _is_restore(op):
    return bool(op) and op[0] in ("restore-precious", "restore-library")


def _autoresume_enabled():
    try:
        return (_state_dir() / "autoresume").read_text().strip() != "off"
    except OSError:
        return True   # default ON


# The single live streamed op (only one at a time; _RUN_ACTIVE guards it).
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {"stream": None, "op": None, "title": None, "paused": False}


def _human(n):
    """Bytes -> a short human string (e.g. 1.2G). Matches the C++ human() style."""
    n = float(n or 0)
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024.0 or unit == "T":
            return f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
        n /= 1024.0
    return "0B"


def _human_eta(secs):
    """rclone eta (seconds, or None) -> '5s' / '2m03s' / '1h04m'; '' if unknown."""
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _parse_progress(line):
    """One engine output line -> (progress_dict|None, display_line|None).

    rclone runs with --use-json-log, so its per-second stats arrive as a single-line JSON
    object carrying a `stats` block (bytes/totalBytes/speed/eta + a transferring[] array).
    We turn that into a structured {progress} the progress subpage renders as bars, plus a
    compact one-line summary for the footer. NON-JSON lines are the engine's own [cloud ...]
    logs and pass straight through as the display line (unchanged behaviour)."""
    s = line.strip()
    if not s.startswith("{"):
        # Drop the harmless Steam-overlay linker warning (a 32-bit LD_PRELOAD .so refused by a
        # 64-bit rclone). It reads as "error" but is noise, not a backup failure.
        if "ld.so:" in s or "LD_PRELOAD" in s:
            return None, None
        return None, s
    try:
        obj = json.loads(s)
    except ValueError:
        return None, s
    st = obj.get("stats")
    if not isinstance(st, dict):
        # A non-stats JSON log line (e.g. an rclone error) -> surface its message.
        msg = (obj.get("msg") or "").strip()
        return None, (msg or None)
    total = st.get("totalBytes") or 0
    done = st.get("bytes") or 0
    checks = st.get("checks") or 0
    total_checks = st.get("totalChecks") or 0
    # During an INCREMENTAL backup rclone spends a long time comparing already-uploaded files
    # (bytes still 0) before transferring the few new ones. Drive the overall bar off the check
    # progress in that phase so the panel isn't stuck at 0%.
    if total > 0:
        pct = int(round(done * 100.0 / total))
    elif total_checks > 0:
        pct = int(round(checks * 100.0 / total_checks))
    else:
        pct = 0
    transfers = []
    for t in st.get("transferring") or []:
        if not isinstance(t, dict):
            continue
        transfers.append({
            "name": t.get("name") or "",
            "pct": int(t.get("percentage") or 0),
            "bytes": int(t.get("bytes") or 0),
            "size": int(t.get("size") or 0),
            "speed": float(t.get("speed") or 0.0),
        })
    prog = {
        "overall_pct": pct,
        "bytes": int(done),
        "total": int(total),
        "checks": int(checks),
        "total_checks": int(total_checks),
        "speed": float(st.get("speed") or 0.0),
        "eta": st.get("eta"),
        "transfers": transfers,
    }
    if total > 0:
        summary = f"{pct}%  {_human(done)}/{_human(total)}  {_human(st.get('speed') or 0)}/s"
        eta = _human_eta(st.get("eta"))
        if eta:
            summary += f"  ETA {eta}"
    elif total_checks > 0:
        summary = f"Checking {checks}/{total_checks} files…"
    else:
        summary = "Working…"
    return prog, summary


def _run(args, timeout=90):
    """Run a fast, bounded engine subcommand. Returns (rc, stdout, stderr)."""
    p = subprocess.run([str(ENGINE), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


class _CloudStream(Stream):
    """Runs `deck-cloud.sh <cmd>` and streams its output lines. The engine is silent
    for long stretches (rclone legs), so a stopped-flag check inside the read
    loop alone never fires while blocked in readline. Each child runs in its OWN
    process group; a stop-watcher killpg()s it the moment stopped is set, and
    cleanup() (also killpg, idempotent) is the belt-and-braces on every other path."""

    def __init__(self, argv: list):
        super().__init__()
        self._argv = argv
        self._proc = None
        self._failed = None   # a per-set push (_push_set) reports its failed-file count via MAD_SET_SUMMARY

    def _spawn(self):
        # stdin MUST be /dev/null - the daemon's stdin is the protocol pipe.
        self._proc = subprocess.Popen(
            self._argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, start_new_session=True)
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
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def cleanup(self):
        self._kill_child()

    def pause(self):
        # Freeze the whole process group (bash + rclone). Must NOT touch self.stopped (that would
        # trip the stop-watcher and KILL it). SIGSTOP is instant; SIGCONT resumes exactly.
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGSTOP)
                return True
            except OSError:
                pass
        return False

    def resume(self):
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGCONT)
                return True
            except OSError:
                pass
        return False

    def run(self):
        rc = -1
        try:
            proc = self._spawn()
            for line in proc.stdout:
                if self.stopped.is_set():
                    break
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("MAD_SET_SUMMARY "):
                    # A per-set cloud push (games/BIOS/ES-DE/emucfg) reports "uploaded=N failed=M" on a
                    # CLEAN publish (rc 0). Stash the failed count for {done} - do NOT show it as a line.
                    try:
                        self._failed = int(line.rsplit("failed=", 1)[1].split()[0])
                    except (ValueError, IndexError):
                        pass
                    continue
                prog, disp = _parse_progress(line)
                if prog is not None:
                    self.emit({"progress": prog})
                if disp:
                    self.emit({"line": disp})
            if not self.stopped.is_set():
                rc = proc.wait()
        finally:
            # done ALWAYS precedes closed so the page can clear its sticky; rc -1 =
            # did not finish cleanly. `failed` (>0) rides along when a per-set push published its
            # manifest (rc 0) but some files did not upload - the UI warns instead of a clean success.
            done = {"done": True, "rc": rc}
            if self._failed:
                done["failed"] = self._failed
            self.emit(done)
            if rc == 0:
                _clear_marker()   # only a CLEAN finish clears it; a kill/stop leaves it resumable
            with _ACTIVE_LOCK:
                if _ACTIVE["stream"] is self:
                    _ACTIVE.update(stream=None, op=None, title=None, paused=False)
            _RUN_ACTIVE.release()


def _stream_op(argv: list):
    if not _RUN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a cloud backup/restore is already running")
    try:
        op = argv[1:]          # the deck-cloud.sh subcommand + args (after ENGINE)
        _write_marker(op)
        s = _CloudStream(argv)
        with _ACTIVE_LOCK:
            _ACTIVE.update(stream=s, op=op, title=_op_title(op), paused=False)
        return {"stream": s.start()}
    except Exception:
        with _ACTIVE_LOCK:
            _ACTIVE.update(stream=None, op=None, title=None, paused=False)
        _RUN_ACTIVE.release()   # start() never ran run()'s finally
        raise


# ---- streamed (long) operations ----
@method("cloud.push")
def _cloud_push(params):
    """Tier A: back up saves + configs now. Manual = force past any failure backoff."""
    return _stream_op([str(ENGINE), "push-precious", "--force"])


@method("cloud.sync")
def _cloud_sync(params):
    """Tier B: sync the big library (ROMs/media/...) now (rclone copy)."""
    return _stream_op([str(ENGINE), "sync-library"])


def _persist_games_plan_and_stream(ts, manifest, plan, subcmd="push-games", plan_root="games-plan",
                                   remote_token=None, merge_cmd=None):
    """Persist a plan-dir (a NUL src\\0rel\\0 list + the manifest) under the daemon's state dir, then STREAM
    deck-cloud.sh <subcmd> over it. Shared by cloud.push_games/push_game_assets (subcmd=push-games) and
    cloud.push_bios (subcmd=push-bios) and cloud.push_esde: every push subcommand treats each `rel` as an
    OPAQUE remote path suffix. The plan-dir id is the caller's real `ts` (UNIQUE per call). `remote_token` is
    the SET name in the remote path (fixed "games"/"bios" for the non-versioned single set, or `ts` for a
    versioned esde snapshot). `merge_cmd` (cat-manifest / cat-bios-manifest) fetches the existing remote
    manifest and MERGES the current selection into it, so a cloud re-backup of a fixed set accumulates
    (mirrors the local fixed-set merge); idempotent on auto-resume (immutable content -> re-merge is a no-op)."""
    token = remote_token or ts
    if merge_cmd:
        rc, out, _ = _run([merge_cmd, token], timeout=60)
        if rc == 0 and out.strip():
            existing = backup_manifest.read_text(out)
            if backup_manifest.validate(existing):
                manifest = backup_manifest.merge(existing, manifest)
    plandir = _state_dir() / plan_root / ts
    plandir.mkdir(parents=True, exist_ok=True)
    # The shell reads $pd/mad-manifest.json + $pd/plan; manifest_path(dir) yields that exact filename.
    backup_manifest.write(manifest, backup_manifest.manifest_path(plandir))
    # NUL-delimited src\0rel\0 records - survives ANY name (spaces / quotes / newline / unicode) that a
    # newline --files-from list could not express (deck-cloud.sh reads the pairs with read -r -d '').
    buf = bytearray()
    for entry in plan:
        buf += entry["src"].encode("utf-8") + b"\0" + entry["rel"].encode("utf-8") + b"\0"
    (plandir / "plan").write_bytes(bytes(buf))
    try:
        return _stream_op([str(ENGINE), subcmd, token, str(plandir)])
    except Exception:
        # the stream never started (EBUSY / spawn failure), so the shell will never consume + clean the
        # plan dir - drop it here so a rejected start can't orphan it. (A STARTED stream cleans the dir on
        # a clean finish and deliberately keeps it on failure so cloud.resume_pending can replay it.)
        shutil.rmtree(plandir, ignore_errors=True)
        raise


@method("cloud.push_games", slow=True)
def _cloud_push_games(params):
    """CLOUD parity of the Local per-game backup: upload the chosen games to MEGA. params
    {items:[{system, stem}]}. Resolves the selection + builds the manifest via the SAME planner as the
    local backup (granular_backup.plan_selection), so a cloud upload selects byte-identically (identical
    skips: ROM missing, or a resolver result outside the system's ROM dir), then STREAMS deck-cloud.sh
    push-games over a persisted plan-dir.

    slow=True: it does N x resolve_rom + manifest writes, and a non-slow method runs INLINE on the stdin
    thread (would freeze the UI). An empty/all-skipped selection raises RpcError so the C++ startCloudOp
    releases its synchronous mRunning guard (an empty {stream} would pin it forever). Auto-resumable: the
    op is NOT a restore, the plan-dir persists until a clean finish, and rclone copy is idempotent."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no games selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_selection(items, "roms", "ROMs & games", ts)
    if not plan:
        raise RpcError("EINVAL",
                       "no backable games in the selection (ROM missing, or not a plain ROM)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          remote_token="games", merge_cmd="cat-manifest")


@method("cloud.push_game_assets", slow=True)
def _cloud_push_game_assets(params):
    """CLOUD parity of the game-first per-asset backup ("Back up a game" -> MEGA). params
    {items:[{system, stem, keys:[asset-group-key,...]}]} - the exact shape the local granular.backup_assets
    takes. Resolves the ticked asset groups + builds the multi-category manifest via the SAME planner as
    the local game-first backup (granular_backup.plan_game_assets), so a cloud upload selects the exact
    files the game-first UI showed, then STREAMS deck-cloud.sh push-games over a persisted plan-dir (rel is
    opaque to the shell, so per-asset rels saves/.../media/.../roms/... upload unchanged).

    slow=True (N x resolve_game_assets + manifest writes). An empty/all-skipped selection raises RpcError
    so the C++ releases its synchronous mRunning guard. Auto-resumable (not a restore; plan-dir persists
    until a clean finish; rclone copy is idempotent). The manifest carries extra={game,asset} per item, so
    a later cloud browse regroups it by game (granular _manifest_game_assets) for a per-asset restore."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no games selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_game_assets(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no ticked assets are present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          remote_token="games", merge_cmd="cat-manifest")


@method("cloud.push_game_assets_all", slow=True)
def _cloud_push_game_assets_all(params):
    """CLOUD parity of the whole-system / all-systems "All" backup (granular.backup_all -> MEGA). params
    {scope:'system'|'all', system?}. Expands the live library to its full game list (the fixed ROM + saves +
    states + media allowlist) via the SAME _games_for_scope the local "All" uses, then STREAMS deck-cloud.sh
    push-games over a persisted plan-dir. Unlike cloud.push_game_assets (which merges into the fixed 'games'
    remote set), this uploads to a DATED remote set (game-backups/<ts>/) - a discrete bulk snapshot that
    never merges into the cherry-pick set (mirrors the local dated deck-granular-games-<ts>).

    slow=True (N x resolve_game_assets + manifest writes). An empty/all-skipped selection raises RpcError so
    the C++ releases its synchronous mRunning guard. Auto-resumable (not a restore; plan-dir persists until a
    clean finish; rclone copy is idempotent)."""
    from . import granular_cmds
    p = params or {}
    scope = p.get("scope")
    if scope not in ("system", "all"):
        raise RpcError("EINVAL", "scope must be 'system' or 'all'")
    system = p.get("system")
    if scope == "system" and not system:
        raise RpcError("EINVAL", "system is required for scope 'system'")
    games = granular_cmds._games_for_scope(scope, system)
    if not games:
        raise RpcError("EINVAL", "no games to back up")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_game_assets(games, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up (no ROMs/saves/states/media are present)")
    # remote_token=None -> the set name is `ts` (a DATED remote set), merge_cmd=None -> no merge: a bulk
    # snapshot is its own restore point, exactly like the local versioned deck-granular-games-<ts>.
    return _persist_games_plan_and_stream(ts, manifest, plan)


@method("cloud.push_bios", slow=True)
def _cloud_push_bios(params):
    """CLOUD parity of the local BIOS backup: upload the chosen BIOS files to MEGA. params
    {items:[{bucket, stem}]} - actually [{bucket, rel}], the exact shape the local granular.backup_bios takes
    (rel = 'bios/<path>'). Resolves the selection + builds the manifest via the SAME planner as the local
    BIOS backup (granular_backup.plan_bios), then STREAMS deck-cloud.sh push-bios over a persisted plan-dir.
    push-bios uploads to a SEPARATE remote base (bios-backups/<ts>) so a BIOS set never cross-lists in the
    per-game cloud restore.

    slow=True (N x path stat + manifest writes). An empty/all-skipped selection raises RpcError so the C++
    releases its synchronous mRunning guard. Auto-resumable (not a restore; plan-dir persists until a clean
    finish; rclone copy is idempotent)."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no BIOS files selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_bios(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no BIOS files are present)")
    return _persist_games_plan_and_stream(ts, manifest, plan, subcmd="push-bios", plan_root="bios-plan",
                                          remote_token="bios", merge_cmd="cat-bios-manifest")


@method("cloud.push_esde", slow=True)
def _cloud_push_esde(params):
    """CLOUD parity of the local ES-DE settings backup: upload the chosen settings files to MEGA. params
    {items:[{group, rel}]} - the exact shape local granular.backup_esde takes. Resolves + builds the manifest
    via the SAME planner as the local backup (granular_backup.plan_esde), then STREAMS deck-cloud.sh push-esde
    over a persisted plan-dir. push-esde uploads to a SEPARATE remote base (esde-backups/<ts>) so an ES-DE
    settings set never cross-lists in the game or BIOS cloud restore.

    slow=True. An empty/all-skipped selection raises RpcError so the C++ releases its synchronous mRunning
    guard. Auto-resumable (not a restore; plan-dir persists until a clean finish; rclone copy is idempotent)."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no ES-DE settings selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_esde(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no ES-DE settings are present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-esde", plan_root="esde-plan")


@method("cloud.push_system", slow=True)
def _cloud_push_system(params):
    """CLOUD parity of the local system-config backup: upload the chosen system config files to MEGA. params
    {items:[{group, rel}]} - the exact shape local granular.backup_system takes. Resolves + builds the manifest
    via the SAME planner (granular_backup.plan_system), then STREAMS deck-cloud.sh push-system over a persisted
    plan-dir. push-system uploads to a SEPARATE remote base (system-backups/<ts>) so a system-config set never
    cross-lists in the game/BIOS/ES-DE/emucfg cloud restore.

    slow=True. An empty/all-skipped selection raises RpcError so the C++ releases its mRunning guard. Auto-
    resumable (not a restore; plan-dir persists until a clean finish; rclone copy is idempotent)."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no system config selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_system(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no system config is present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-system", plan_root="system-plan")


@method("cloud.push_controllers", slow=True)
def _cloud_push_controllers(params):
    """CLOUD parity of the local controller-config backup: upload the chosen controller config to MEGA. params
    {items:[{group, rel}]}. Resolves + builds the manifest via the SAME planner (granular_backup.
    plan_controllers), then STREAMS deck-cloud.sh push-controllers over a persisted plan-dir, uploading to a
    SEPARATE remote base (controllers-backups/<ts>) so a controller set never cross-lists in another category's
    cloud restore. slow=True; empty selection -> RpcError (releases the C++ mRunning guard); auto-resumable."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no controller config selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_controllers(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no controller config is present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-controllers", plan_root="controllers-plan")


@method("cloud.push_emucfg", slow=True)
def _cloud_push_emucfg(params):
    """CLOUD parity of the local emulator-config backup: upload the chosen emulator config/data to MEGA.
    params {items:[{emulator, group, rel}]} - the exact shape local granular.backup_emucfg takes. Resolves +
    builds the manifest via the SAME planner as the local backup (granular_backup.plan_emucfg), then STREAMS
    deck-cloud.sh push-emucfg over a persisted plan-dir. push-emucfg uploads to a SEPARATE remote base
    (emucfg-backups/<ts>) so an emulator-config set never cross-lists in the game/BIOS/ES-DE cloud restore.

    slow=True. An empty/all-skipped selection raises RpcError so the C++ releases its synchronous mRunning
    guard. Auto-resumable (not a restore; plan-dir persists until a clean finish; rclone copy is idempotent)."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no emulator config selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_emucfg(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no emulator config is present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-emucfg", plan_root="emucfg-plan")


@method("cloud.push_bios_all", slow=True)
def _cloud_push_bios_all(params):
    """CLOUD "All" BIOS backup: upload EVERY bios file to MEGA (the same fixed "bios" remote set as the
    per-bucket cloud backup). Enumerates all buckets via the SAME helper the local granular.backup_bios_all
    uses, so cloud + local select identically. slow=True; auto-resumable (rclone copy idempotent)."""
    from . import granular_cmds
    items = granular_cmds._all_bios_items()
    if not items:
        raise RpcError("EINVAL", "no BIOS files to back up")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_bios(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up (no BIOS files are present)")
    return _persist_games_plan_and_stream(ts, manifest, plan, subcmd="push-bios", plan_root="bios-plan",
                                          remote_token="bios", merge_cmd="cat-bios-manifest")


@method("cloud.push_emucfg_all", slow=True)
def _cloud_push_emucfg_all(params):
    """CLOUD "All" emulator-config backup: upload EVERY emulator's config/data (ALL groups incl. the giant
    texture/mod/NAND/HDD folders) to MEGA (a dated emucfg-backups/<ts> set, as the per-emulator cloud path).
    Enumerates via the SAME helper the local granular.backup_emucfg_all uses. slow=True; auto-resumable."""
    from . import granular_cmds
    items = granular_cmds._all_emucfg_items()
    if not items:
        raise RpcError("EINVAL", "no emulator config to back up")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_emucfg(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up (no emulator config is present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-emucfg", plan_root="emucfg-plan")


@method("cloud.restore_precious")
def _cloud_restore_precious(params):
    """Restore the precious set. Default = into a scratch dir (never blind-overwrites). With
    to_live it restores OVER the live saves + configs (overwrites -> _TMP; running tooling
    excluded so a restore can't revert the code/app)."""
    argv = [str(ENGINE), "restore-precious"]
    if params.get("to_live"):
        argv.append("--to-live")
    argv.append(params.get("snapshot") or "latest")
    if params.get("target"):
        argv.append(str(params["target"]))
    return _stream_op(argv)


@method("cloud.restore_library")
def _cloud_restore_library(params):
    """Restore a big-library category. Default = into a STAGING dir the user copies back from.
    If to_live is set, restore to the REAL location and recreate the symlink front-door
    (e.g. ~/ROMs -> SD), rule #5-protected. An explicit target dir may be passed."""
    cat = params.get("category")
    if not cat:
        raise RpcError("EINVAL", "category is required")
    argv = [str(ENGINE), "restore-library", str(cat)]
    if params.get("to_live"):
        argv.append("--to-live")
    if params.get("target"):
        argv.append(str(params["target"]))
    return _stream_op(argv)


# ---- transfer controls: pause / resume / stop / cancel + reattach + resume-pending ----
@method("cloud.pause")
def _cloud_pause(params):
    """Freeze the live transfer (SIGSTOP the process group). Returns the resulting paused state."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
    if s is not None and s.pause():
        with _ACTIVE_LOCK:
            _ACTIVE["paused"] = True
    with _ACTIVE_LOCK:
        return {"paused": _ACTIVE["paused"]}


@method("cloud.resume")
def _cloud_resume(params):
    """Unfreeze the live transfer (SIGCONT). Returns the resulting paused state."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
    if s is not None and s.resume():
        with _ACTIVE_LOCK:
            _ACTIVE["paused"] = False
    with _ACTIVE_LOCK:
        return {"paused": _ACTIVE["paused"]}


@method("cloud.stop")
def _cloud_stop(params):
    """Halt the live transfer but KEEP the marker (resumable). SIGCONT first so a paused group can die."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
    if s is None:
        return {"stopped": False}
    s.resume()
    return {"stopped": bool(s.token and stop_stream(s.token))}


@method("cloud.cancel")
def _cloud_cancel(params):
    """Halt AND forget: clear the marker so auto-resume won't re-run it (works whether or not live)."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
    _clear_marker()
    if s is None:
        return {"cancelled": False}
    s.resume()
    return {"cancelled": bool(s.token and stop_stream(s.token))}


@method("cloud.active")
def _cloud_active(params):
    """Reattach info for the panel: a live op's token to adopt, or an interrupted transfer waiting
    (pending; a restore needs a confirm before it re-runs)."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
        title = _ACTIVE["title"]
        paused = _ACTIVE["paused"]
    if s is not None:
        return {"running": True, "token": s.token, "title": title or "Cloud transfer",
                "paused": paused, "pending": False, "pending_restore": False}
    m = _read_marker()
    if m:
        return {"running": False, "pending": True, "op": m[0], "title": _op_title(m),
                "pending_restore": _is_restore(m)}
    return {"running": False, "pending": False, "pending_restore": False}


@method("cloud.resume_pending")
def _cloud_resume_pending(params):
    """Re-launch an interrupted transfer from the marker (the modal's Resume, or a manual resume)."""
    m = _read_marker()
    if not m:
        raise RpcError("ENONE", "no interrupted transfer to resume")
    return _stream_op([str(ENGINE), *m])


# ---- Manage backups: PERMANENT delete of a cloud set ----
# category -> the deck-cloud.sh purge subcommand. And push subcommand -> the category it writes, so a
# live/interrupted upload of the set being deleted can be matched + stopped (push-games writes BOTH the fixed
# "games" set [push_games/push_game_assets] and a dated games "All" set [push_game_assets_all]; likewise bios).
_PURGE_SUBCMD = {"games": "purge-games", "bios": "purge-bios", "esde": "purge-esde",
                 "emucfg": "purge-emucfg", "system": "purge-system", "controllers": "purge-controllers"}
_PUSH_CAT = {"push-games": "games", "push-bios": "bios", "push-esde": "esde",
             "push-emucfg": "emucfg", "push-system": "system", "push-controllers": "controllers"}


def _delete_safe_token(t):
    """A cloud set token safe to purge: a 15-char YYYYmmddTHHMMSS OR the fixed names games/bios. Mirror of
    granular_cmds._safe_settoken, inlined to avoid an import cycle; a destructive op re-validates before it
    shells out (the shell _purge_set validates a THIRD time - defense in depth)."""
    return bool(t) and (t in ("games", "bios") or
                        (len(t) == 15 and t[8] == "T" and t[:8].isdigit() and t[9:].isdigit()))


@method("cloud.delete_set", slow=True)
def _cloud_delete_set(params):
    """PERMANENTLY delete ONE cloud backup set on MEGA (Manage backups). params {category, token}: category in
    games/bios/esde/emucfg/system; token is the set id ("games"/"bios" fixed, or a 15-char ts). A deliberate
    user override of rule #5 for a BACKUP copy - primary data is never touched. Before purging: if the LIVE or
    INTERRUPTED op targets THIS set, stop it + drop its plan-dir + clear the marker, so mad-backend's
    auto-resume can't re-upload the very set we just deleted. slow=True (shells out + hits the network)."""
    p = params or {}
    category = p.get("category") or ""
    token = p.get("token") or ""
    if category not in _PURGE_SUBCMD:
        raise RpcError("EINVAL", f"unknown backup category: {category!r}")
    if not _delete_safe_token(token):
        raise RpcError("EINVAL", f"bad cloud backup id: {token!r}")
    # 1. If the LIVE op is uploading THIS set, stop it (resume a paused group first so it can die).
    with _ACTIVE_LOCK:
        op = _ACTIVE["op"]
        s = _ACTIVE["stream"]
    if op and _PUSH_CAT.get(op[0]) == category and len(op) > 1 and op[1] == token and s is not None:
        s.resume()
        if s.token:
            stop_stream(s.token)
    # 2. Drop the interrupted-transfer marker + its plan-dir if it targets THIS set (clearing the marker is
    #    the essential step: auto-resume replays from the marker, so a cleared marker can't re-upload).
    m = _read_marker()
    if m and _PUSH_CAT.get(m[0]) == category and len(m) > 1 and m[1] == token:
        _clear_marker()
        if len(m) > 2 and m[2]:
            shutil.rmtree(m[2], ignore_errors=True)
    # 3. Purge the remote folder (idempotent: the shell treats an already-gone set as success).
    rc, out, err = _run([_PURGE_SUBCMD[category], token], timeout=180)
    if rc != 0:
        raise RpcError("EIO", (err or out or "cloud delete failed").strip()[:200] or "cloud delete failed")
    return {"deleted": True, "category": category, "token": token}


# ---- fast bounded operations ----
@method("cloud.status", slow=True)
def _cloud_status(params):
    """Connection + toggle state for the page header (no network hit)."""
    rc, out, _ = _run(["status"], timeout=30)
    st = {}
    for line in out.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            st[k] = v
    for b in ("connected", "timer_active", "onexit_enabled", "autoresume_enabled"):
        st[b] = st.get(b) == "1"
    return st


def _fmt_version(v):
    """'20260723-071500' -> '2026-07-23 07:15:00'; pass through otherwise."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$", v)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else v


@method("cloud.snapshots", slow=True)
def _cloud_snapshots(params):
    """List rollback points (version folders under precious-versions), newest first."""
    rc, out, err = _run(["snapshots"], timeout=120)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "cannot list versions")
    versions = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return {"snapshots": [{"id": v, "time": _fmt_version(v)} for v in versions]}


@method("cloud.servers", slow=True)
def _cloud_servers(params):
    """List the selectable MEGA S4 servers (id/label/endpoint/region) + which is current."""
    rc, out, err = _run(["list-servers"], timeout=30)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "cannot list servers")
    servers, current = [], None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        sid, label, endpoint, region, cur = parts
        is_cur = cur.strip() == "1"
        servers.append({"id": sid, "label": label, "endpoint": endpoint,
                        "region": region, "current": is_cur})
        if is_cur:
            current = sid
    return {"servers": servers, "current": current}


@method("cloud.set_server", slow=True)
def _cloud_set_server(params):
    """Switch the active MEGA S4 server. {server:<id>}. Saves the choice + probes reachability
    (so the returned message says whether the picked server is reachable right now)."""
    sid = params.get("server")
    if not sid:
        raise RpcError("EINVAL", "server id is required")
    rc, out, err = _run(["set-server", str(sid)], timeout=90)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "could not set server")
    return {"message": (out or err).strip()}


@method("cloud.categories", slow=True)
def _cloud_categories(params):
    """What the cloud backs up, split by tier: Tier A = 'Back up now' + auto (saves+configs);
    Tier B = 'Sync library' (ROMs/media/...). Each {key,label,on}."""
    rc, out, err = _run(["list-categories"], timeout=30)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "cannot list categories")
    tier_a, tier_b = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        tier, key, label, on = parts
        entry = {"key": key, "label": label, "on": on.strip() == "1"}
        (tier_a if tier == "A" else tier_b).append(entry)
    return {"tierA": tier_a, "tierB": tier_b}


@method("cloud.set_category", slow=True)
def _cloud_set_category(params):
    """Flip a backup category on/off. {key, value:on|off}. push-precious / sync-library and
    the headless auto-backups honor the saved selection."""
    key = params.get("key")
    val = params.get("value")
    if not key or val not in ("on", "off"):
        raise RpcError("EINVAL", "key is required and value must be on|off")
    rc, out, err = _run(["set-category", str(key), val], timeout=15)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "could not set category")
    return {"message": (out or err).strip()}


@method("cloud.sizes", slow=True)
def _cloud_sizes(params):
    """The REAL post-filter upload size per Tier-A category (esde/emu/saves/bios), so the panel
    chips reflect what the cloud actually sends (Tier B syncs wholesale and keeps backup.sizes).
    Slow: it runs rclone size walks (~10-12s) - the C++ fetches it async and shows
    '(calculating...)' until it lands. Returns {sizes: {key: bytes}}."""
    rc, out, err = _run(["cloud-sizes"], timeout=180)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "cannot compute cloud sizes")
    sizes = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        key, val = line.split("\t", 1)
        val = val.strip()
        if val.isdigit():
            sizes[key] = int(val)
    return {"sizes": sizes}


@method("cloud.set_toggle", slow=True)
def _cloud_set_toggle(params):
    """which=onexit|timer, value=on|off. onexit = the game-exit backup; timer = during-play."""
    which = params.get("which")
    val = params.get("value")
    if which not in ("onexit", "timer") or val not in ("on", "off"):
        raise RpcError("EINVAL", "which must be onexit|timer and value on|off")
    rc, out, err = _run(["set-toggle", which, val], timeout=30)
    if rc != 0:
        raise RpcError("EFAIL", (err or out).strip() or "toggle failed")
    return {"message": (out or err).strip()}
