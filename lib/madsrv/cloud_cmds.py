"""Cloud (MEGA) backup page methods - thin RPC wrappers over deck-cloud.sh.

deck-cloud.sh is the single owner of every rclone call; this module only
exposes its subcommands to the MAD native panel:

- cloud.push / cloud.sync / cloud.restore_precious / cloud.restore_library ->
  start a DETACHED registered job and stream its output ({line} per line,
  {done, rc} at the end), same shape as backup.run_full. Only ONE cloud op
  runs at a time (in-daemon lock + the persistent job registry).
- transfers.list / attach / pause / resume / stop / cancel -> the multi-job
  view over lib/job_registry.py: EVERY transfer (panel, game-end hook, CLI,
  auto-resume) is a registered job with its own .out file, so the Transfers
  tile sees them all and they survive the panel closing.
- cloud.status / cloud.snapshots -> fast bounded calls
  (slow=True: they shell out, so run on the worker pool, never the stdin thread).
  (The three GLOBAL backup toggles live entirely in ES-DE's Other-settings menu now, which shells
  out to "deck-cloud.sh set-toggle" directly - there is no RPC method for them; cloud.set_toggle was
  retired audit 2026-08-12 phase 5 as dead code, never called by the panel.)

DETACHED-JOB MODEL (why there is no pipe): a transfer used to be a child of this
daemon with its output on a pipe - daemon teardown had to killpg it (or the dying
pipe would SIGPIPE rclone anyway), so closing the panel killed the transfer. Now
the child writes to the job's .out FILE and the daemon only TAILS that file
(_JobTailStream); teardown stops tailers, never jobs. deck-cloud.sh registers
itself (DECK_CLOUD_JOB_ID passes the id), deck-backup.sh is wrapped by a one-line
rc recorder, and the in-process granular ops register as detached:false.
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

from .. import backup_manifest, granular_backup   # cloud.push_game_assets: shared planner + manifest writer
from . import env_hygiene
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
        # ITEM-COUNT PROGRESS from deck-cloud.sh _push_set: "MAD_SET_PROGRESS done=N total=M name=X".
        # This WINS over rclone's byte stats for set pushes, and has to, because those stats cannot
        # describe this loop: a set already present on MEGA transfers zero bytes (copy is a per-file
        # check sweep, so totalBytes stays 0), and the loop runs one rclone per entry, so each blob
        # reports only its own sub-run and resets between entries. Counting plan entries is monotonic
        # and truthful in both cases. Returned as a progress dict with no display line: the bar and
        # the caption carry it, an extra log line per file would be noise.
        if s.startswith("MAD_SET_PROGRESS"):
            # Prefix matched WITHOUT the trailing space on purpose: a bare "MAD_SET_PROGRESS" with
            # no fields must be swallowed here, not fall through to the generic passthrough below
            # and print the raw marker at the user as if it were an engine log line.
            fields = {}
            for part in s[len("MAD_SET_PROGRESS"):].strip().split(" ", 2):
                k, _, v = part.partition("=")
                fields[k] = v
            try:
                done_n, total_n = int(fields.get("done", 0)), int(fields.get("total", 0))
            except ValueError:
                return None, None            # malformed: drop it, never show the raw marker
            if total_n <= 0:
                return None, None            # nothing to divide by: says nothing, shows nothing
            pct = int(round(done_n * 100.0 / total_n))
            name = fields.get("name", "")
            return {"overall_pct": max(0, min(100, pct)), "items_done": done_n,
                    "items_total": total_n, "item": name, "transfers": []}, (
                f"{pct}%  {done_n}/{total_n} files" + (f"  {name}" if name else ""))
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


# Every deck-cloud.sh job kind (mirror of the engine's _job_begin dispatch gate). Used to
# tell "a cloud op is live" from other registered jobs (full local backup, granular ops).
_CLOUD_KINDS = {"push-precious", "sync-library", "push-games", "push-bios", "push-esde",
                "push-emucfg", "push-system", "push-controllers", "restore-precious",
                "restore-library", "fetch-games", "fetch-bios", "fetch-esde",
                "fetch-emucfg", "fetch-system", "fetch-controllers"}


def _registry():
    from .. import job_registry
    return job_registry


def _freeze_if_gameplay(reg, job_id: str, kind: str) -> None:
    """Freeze a job that just started/dispatched WHILE a game is live, when either the
    BACKUP DURING GAMEPLAY toggle is off, or `kind` is a restore/fetch that the toggle's
    name never promised to keep running (see job_registry.protected_during_gameplay -
    it overwrites live saves/config the running game may hold open, so it is frozen
    EITHER WAY). Shared by _spawn_registered (a panel-launched op mid-game) and
    dispatch_queue (a queued op the dispatcher starts mid-game) so the two agree; the
    game-end hook thaws either one like any other gameplay-paused job, no extra
    plumbing needed on that side."""
    try:
        if reg.gameplay_marker().exists() \
                and (reg.protected_during_gameplay(kind)
                     or not (reg.state_dir() / "gameplay.enabled").exists()):
            reg.pause_job(job_id, by="gameplay")
    except OSError:
        pass


def _marker_matches_job(job) -> bool:
    """Whether the interrupted-transfer marker describes THIS job (so its clean finish
    may clear it). The marker is [<subcommand>, <args...>]; a job's argv is the same
    list. Compares the op AND the identifying first arg (a set token / ts), because
    two push-games runs for DIFFERENT sets must not clear each other's marker."""
    if not job or job.get("kind") not in _CLOUD_KINDS:
        return False
    m = _read_marker()
    if not m or m[0] != job.get("kind"):
        return False
    argv = list(job.get("argv") or [])
    if len(m) > 1 and len(argv) > 1:
        return m[1] == argv[1]
    return len(m) == len(argv) or len(m) == 1


class _JobTailStream(Stream):
    """Tails a registered job's .out file and emits the EXACT event shapes the old
    pipe stream produced ({progress} / {line} / terminal {done, rc[, failed]}), so
    GuiMadPageCloudProgress needs no protocol change. It NEVER signals the job:
    stopping/cleanup only stops the tailing - daemon teardown therefore leaves the
    detached transfer running, which is the whole point of the model."""

    POLL_S = 0.25
    ATTACH_TAIL_BYTES = 4096   # re-attach: skip old output, replay only the tail

    def __init__(self, job_id: str, proc=None, owns_lock: bool = False,
                 clears_marker: bool | None = None):
        super().__init__()
        self._job_id = job_id
        self._proc = proc            # the backend-spawned child (reaped here); None on attach
        self._owns_lock = owns_lock  # only _stream_op's tailer holds _RUN_ACTIVE
        self._failed = None          # MAD_SET_SUMMARY failed-count, rides the {done}
        self._items_mode = False     # seen a MAD_SET_PROGRESS: byte stats stop moving the bar
        # Clear the interrupted-transfer marker on a clean finish only when the marker
        # is THIS job's (op + args match). The marker is a SINGLE global file: clearing
        # it on any cloud job's rc-0 would eat an unrelated pending resume - e.g. a
        # game-end hook push-precious (which writes no marker of its own) finishing
        # while attached would wipe a pending push-games upload, silently abandoning it.
        # None = decide by matching the marker against the job record at finish time
        # (attach doesn't know the job up front); False = never (backup.run_full).
        self._clears_marker = clears_marker

    def _emit_line(self, line: str):
        if line.startswith("MAD_SET_SUMMARY "):
            # A per-set cloud push (games/BIOS/ES-DE/emucfg) reports "uploaded=N failed=M" on a
            # CLEAN publish (rc 0). Stash the failed count for {done} - do NOT show it as a line.
            try:
                self._failed = int(line.rsplit("failed=", 1)[1].split()[0])
            except (ValueError, IndexError):
                pass
            return
        prog, disp = _parse_progress(line)
        if prog is not None:
            # Once this job has reported ITEM counts, rclone's byte stats must never move the bar
            # again. They cannot describe a set push (one rclone per entry, and an already-synced
            # set transfers 0 bytes), so letting them through would drag a truthful 40% back to 0
            # between every entry - the exact flicker this replaced.
            if prog.get("items_total"):
                self._items_mode = True
            elif self._items_mode:
                prog = None
        if prog is not None:
            self.emit({"progress": prog})
        if disp:
            self.emit({"line": disp})

    def run(self):
        reg = _registry()
        rc = -1
        try:
            path = reg.out_path(self._job_id)
            pos = 0
            buf = b""
            if self._proc is None:
                # ATTACH to an already-running job. Replay only the LAST stats line, then
                # tail from EOF. Two reasons this is deliberately minimal: (1) hours of old
                # stats would flood the page, and (2) the RPC layer stashes only a handful
                # of events per token before the panel's callback is registered, so a burst
                # here could push the terminal {done} out of that buffer and leave the page
                # stuck "Reattaching…" forever. Seeking to a byte offset would also split a
                # line (and a UTF-8 char) - reading whole lines avoids emitting mojibake.
                last_prog = None
                try:
                    size = path.stat().st_size
                    with open(path, "rb") as fh:
                        if size > self.ATTACH_TAIL_BYTES:
                            fh.seek(size - self.ATTACH_TAIL_BYTES)
                            fh.readline()          # discard the partial first line
                        for raw in fh:
                            line = raw.decode("utf-8", "replace").rstrip()
                            if not line:
                                continue
                            if line.startswith("MAD_SET_SUMMARY "):
                                # No event, but the failed-file count must still reach
                                # {done} - a per-set push prints it once, before exiting 0.
                                try:
                                    self._failed = int(
                                        line.rsplit("failed=", 1)[1].split()[0])
                                except (ValueError, IndexError):
                                    pass
                                continue
                            prog, _disp = _parse_progress(line)
                            if prog is not None:
                                # Same precedence as the live path: once this job has reported item
                                # counts, a later byte-stats blob must not replace them as the
                                # replayed state, or re-attaching to a set push shows 0%.
                                if prog.get("items_total"):
                                    last_prog = prog
                                    self._items_mode = True
                                elif not self._items_mode:
                                    last_prog = prog
                        pos = fh.tell()
                except OSError:
                    pass
                if last_prog is not None:
                    self.emit({"progress": last_prog})   # ONE event: the current state
            missing_polls = 0
            dead_polls = 0
            while not self.stopped.is_set():
                # State FIRST, then read: anything written before the terminal-state
                # update is guaranteed to be drained in this same iteration.
                job = reg.get(self._job_id)
                terminal = job is not None and job.get("state") in ("done", "failed")
                try:
                    with open(path, "rb") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                except OSError:
                    chunk = b""
                if chunk:
                    pos += len(chunk)
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        line = raw.decode("utf-8", "replace").rstrip()
                        if line:
                            self._emit_line(line)
                if terminal:
                    rc = -1 if job.get("rc") is None else int(job["rc"])
                    break
                if job is None:
                    # Not registered (yet): the engine registers itself right after
                    # spawn - tolerate a brief gap while our child is alive, but a
                    # vanished/pruned job with no child ends the tail.
                    missing_polls += 1
                    if self._proc is None or self._proc.poll() is not None:
                        if missing_polls > 20:
                            break
                else:
                    missing_polls = 0
                    if self._proc is None and not reg.alive(job):
                        # ATTACH tail: nothing here owns the child, so a job killed
                        # without its EXIT trap (SIGKILL / OOM) would leave a live-looking
                        # record and freeze this page forever. Record the truth ourselves
                        # (the registry's reap grace covers the rc-in-flight window).
                        dead_polls += 1
                        if dead_polls > int(reg.REAP_GRACE_S / self.POLL_S) + 1:
                            reg.end(self._job_id, -1)
                    else:
                        dead_polls = 0
                if self._proc is not None and self._proc.poll() is not None \
                        and job is not None and job.get("state") in ("running", "paused"):
                    # The spawner's child exited (poll also reaps - no zombie) but the
                    # record is still live: the engine's EXIT trap runs BEFORE exit, so
                    # by the time poll() flips the trap already wrote the terminal state
                    # - reaching here means a hard kill or a script without the trap.
                    # Record the real exit code (the registry's reap grace keeps outside
                    # reaps from mislabeling this window as a -1 failure).
                    reg.end(self._job_id, self._proc.returncode
                            if self._proc.returncode is not None else -1)
                if not chunk:
                    time.sleep(self.POLL_S)
        finally:
            # done ALWAYS precedes closed so the page can clear its sticky; rc -1 =
            # did not finish cleanly (or the tail was stopped - the JOB may live on).
            done = {"done": True, "rc": rc}
            if self._failed:
                done["failed"] = self._failed
            self.emit(done)
            clears = self._clears_marker
            if clears is None:
                clears = _marker_matches_job(reg.get(self._job_id))
            if rc == 0 and clears:
                _clear_marker()   # only a CLEAN finish of the MARKER'S OWN op clears it
            with _ACTIVE_LOCK:
                if _ACTIVE["stream"] is self:
                    _ACTIVE.update(stream=None, op=None, title=None, paused=False)
            if self._owns_lock:
                _RUN_ACTIVE.release()

    def cleanup(self):
        pass   # stop tailing only - transfers.stop is how a JOB is stopped


def _spawn_registered(argv: list, kind: str, source: str = "panel",
                      self_registering: bool = True, env: dict | None = None):
    """Spawn a script DETACHED (new session, stdout+stderr appended to the job's .out,
    no pipe) and register it. self_registering=True (deck-cloud.sh): the id rides
    DECK_CLOUD_JOB_ID and the engine's _job_begin/_job_end own the record's lifecycle;
    False (deck-backup.sh): a one-line bash wrapper records the exit rc instead. The
    backend PRE-registers in both cases so the Transfers tile and the tailer see the
    job from the first instant. Returns (job_id, proc)."""
    import shlex
    reg = _registry()
    job_id = reg.new_id()
    reg.jobs_dir().mkdir(parents=True, exist_ok=True)
    # Always strip the Steam overlay from the job env (see env_hygiene): with it the
    # job's .out fills with ld.so preload ERRORs + 'skipping destruction' fork noise.
    env = env_hygiene.clean_env(env)
    if self_registering:
        cmd = argv
        env["DECK_CLOUD_JOB_ID"] = job_id
        env["DECK_CLOUD_JOB_SOURCE"] = source
        rec_argv = argv[1:]
    else:
        reg_py = str(Path(__file__).resolve().parents[1] / "job_registry.py")
        cmd = ["bash", "-c",
               " ".join(shlex.quote(a) for a in argv)
               + f'; rc=$?; python3 {shlex.quote(reg_py)} end {shlex.quote(job_id)} "$rc"'
               + ' >/dev/null 2>&1; exit "$rc"']
        rec_argv = argv
    with open(reg.out_path(job_id), "ab") as outf:
        # stdin MUST be /dev/null - the daemon's stdin is the protocol pipe.
        proc = subprocess.Popen(cmd, stdout=outf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    reg.begin(kind, proc.pid, job_id=job_id, argv=list(rec_argv), source=source)
    # Started while a game is live: freeze it NOW when the toggle demands it (or the
    # kind always must - see _freeze_if_gameplay). The game-start hook only froze jobs
    # that already existed, so a transfer launched mid-game (the panel via the Steam
    # overlay) would otherwise run against the toggle's promise. The job cannot do
    # this itself (the registry refuses to signal its own process group); this
    # spawner is outside the job's new session, so it can.
    _freeze_if_gameplay(reg, job_id, kind)
    return job_id, proc


def _registry_busy():
    """Raise EBUSY if a DETACHED cloud job is live. The in-daemon lock is FRESH after a panel reopen but
    a detached job is not, so the registry is consulted too - with a distinct message for a gameplay-
    frozen job (it holds the engine's push.lock, so a new op would silently queue then die)."""
    for j in _registry().live_jobs():
        if j.get("kind") in _CLOUD_KINDS:
            if j.get("paused_by") == "gameplay":
                raise RpcError("EBUSY", "transfers are paused during gameplay - "
                                        "quit the game (or flip BACKUP DURING "
                                        "GAMEPLAY) and try again")
            raise RpcError("EBUSY", "a cloud backup/restore is already running")


def _busy_check():
    """Raise the same EBUSY _stream_op would, WITHOUT taking the lock - so an RPC can bail out before
    doing expensive work (network, plan-dir writes) that _stream_op would only reject at the end."""
    if not _RUN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a cloud backup/restore is already running")
    _RUN_ACTIVE.release()
    _registry_busy()


def _is_busy() -> bool:
    """Whether a cloud op is running/starting right now - the same two gates _stream_op applies, as a
    question rather than an exception. Used to decide run-now vs enqueue."""
    try:
        _busy_check()
        return False
    except RpcError:
        return True


def _merge_remote_manifest(merge_cmd: str, token: str, plandir: Path):
    """Fold the set's CURRENT remote manifest into the plan dir's, in place, right before uploading.

    This runs at DISPATCH, not when the job was created, and that timing is the whole point: the
    uploaded manifest REPLACES the remote one and is the only index of what the set holds, so two
    pushes to the same fixed set (e.g. 'games') queued back to back would otherwise both merge against
    the state they saw at enqueue - and the second would publish an index with no trace of the first's
    files. The bytes would survive on MEGA; the record of them would not.

    Fetch rc decides, as in the run-now path: 0 = merge; 3 = rclone "not found", the set does not exist
    yet, so a fresh manifest is correct; anything else is a transport failure and must ABORT rather
    than overwrite. Raises RpcError on that abort."""
    rc, out, err = _run([merge_cmd, token], timeout=60)
    if rc == 0:
        if out.strip():
            existing = backup_manifest.read_text(out)
            if backup_manifest.validate(existing):
                mf = backup_manifest.manifest_path(plandir)
                merged = backup_manifest.merge(existing, backup_manifest.read(mf))
                backup_manifest.write(merged, mf)
    elif rc != 3:
        raise RpcError("EFAIL",
                       f"could not read the existing cloud backup index for '{token}' "
                       f"(rclone exit {rc}) - not uploading, because replacing it would hide "
                       f"everything already backed up there. Check the connection and retry."
                       + (f" [{err.strip()}]" if err and err.strip() else ""))


def _enqueue_op(argv: list, merge_cmd: str = "", plan_dir: str = ""):
    """Record an op to run when the current one finishes. Returns {queued, position, title} - a
    DIFFERENT success shape from _stream_op's {stream}, because there is nothing to stream yet.

    Deliberately does NOT write the interrupted-transfer marker: there is only ONE marker file, so a
    queued job writing it would either clobber the running job's marker or, after a restart, make
    auto-resume fire the queued op directly and bypass the queue entirely. The marker is written when
    the job actually starts. The manifest merge is deferred for the same reason (see
    _merge_remote_manifest)."""
    reg = _registry()
    op = argv[1:]
    job_id = reg.enqueue(op[0], argv=op, source="panel", title=_op_title(op), plan_dir=plan_dir)
    if merge_cmd:
        reg.update(job_id, merge_cmd=merge_cmd)
    pos = next((i + 1 for i, j in enumerate(reg.queued_jobs()) if j["id"] == job_id), 1)
    return {"queued": job_id, "position": pos, "title": _op_title(op)}


# Children started by the dispatcher. Unlike the run-now path there is no tail stream holding the
# Popen, so nobody would wait() on them - and an unreaped child is a ZOMBIE whose /proc entry still
# carries its original starttime, so job_registry._alive() reports it as RUNNING. The engine normally
# records its own exit via its EXIT trap, but one that is SIGKILLed does not, and then the reaper is
# the only way out - and the reaper would believe the zombie. The queue would stall for good.
_DISPATCHED: list = []
_DISPATCHED_LOCK = threading.Lock()


def _reap_dispatched():
    """poll() each dispatched child, dropping the finished ones. Cheap, and the only thing standing
    between a killed engine and a permanently stuck queue."""
    with _DISPATCHED_LOCK:
        alive = []
        for proc in _DISPATCHED:
            try:
                if proc.poll() is None:
                    alive.append(proc)
            except Exception:
                pass
        _DISPATCHED[:] = alive


def dispatch_queue() -> str:
    """Start the head of the queue if nothing is running. Returns the started job id, or "".

    Holds _RUN_ACTIVE across the check AND the spawn, exactly as the run-now path does, so the two can
    never both decide the engine is free: deck-cloud.sh's push-precious and prune take the engine lock
    with `flock -n` and SILENTLY return 0 when they cannot get it, which auto-resume would then read as
    a clean finish. Strict serialisation here is what keeps that from happening.

    Called from the daemon's dispatcher thread; safe to call at any time."""
    reg = _registry()
    _reap_dispatched()                  # before deciding anything: a zombie child reads as alive
    if not _RUN_ACTIVE.acquire(blocking=False):
        return ""                       # a tailed op owns the engine
    try:
        for j in reg.live_jobs():       # a detached op from any source owns it too
            if j.get("kind") in _CLOUD_KINDS:
                return ""
        head = next(iter(reg.queued_jobs()), None)
        if head is None:
            return ""
        job_id = head["id"]
        argv = [str(ENGINE), *(head.get("argv") or [])]
        merge_cmd = head.get("merge_cmd")
        plandir = head.get("plan_dir")
        if merge_cmd and plandir:
            try:
                _merge_remote_manifest(merge_cmd, (head.get("argv") or ["", ""])[1], Path(plandir))
            except RpcError as exc:
                # Never silently drop the user's request: fail it visibly, with the reason in its .out
                # so the Transfers tile can show what went wrong.
                try:
                    reg.out_path(job_id).write_text(f"{exc}\n")   # RpcError carries its text as the arg
                except OSError:
                    pass
                reg.update(job_id, state="failed", rc=1, queue_pos=None)
                return ""
        env = dict(os.environ)
        env["DECK_CLOUD_JOB_ID"] = job_id
        env["DECK_CLOUD_JOB_SOURCE"] = head.get("source") or "panel"
        env = env_hygiene.clean_env(env)
        _write_marker(head.get("argv") or [])   # marker at DISPATCH, not at enqueue
        try:
            with open(reg.out_path(job_id), "ab") as outf:
                proc = subprocess.Popen(argv, stdout=outf, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        except OSError:
            _clear_marker()
            reg.update(job_id, state="failed", rc=1, queue_pos=None)
            return ""
        with _DISPATCHED_LOCK:
            _DISPATCHED.append(proc)
        if reg.start_queued(job_id, proc.pid) is None:
            # Cancelled while we were spawning: nothing tracks this process now, so kill it rather
            # than leave an untracked upload running against the user's wishes.
            _clear_marker()
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                pass
            return ""
        # A queued job dispatched mid-game got NO gameplay check at all before this -
        # neither toggle position froze it, unlike the run-now path above. Same rule,
        # same helper, so a restore/fetch head is frozen the instant it starts either way.
        _freeze_if_gameplay(reg, job_id, head.get("kind") or "")
        return job_id
    finally:
        _RUN_ACTIVE.release()


def _stream_op(argv: list, queue_if_busy: bool = False, merge_cmd: str = "", plan_dir: str = ""):
    """Start a cloud op NOW and return {stream}. With queue_if_busy, a busy engine enqueues instead of
    raising EBUSY and returns {queued, position, title} - the caller must handle both shapes.

    queue_if_busy defaults to False so every existing caller (and the eight test files that mock this
    function) keeps the old contract: one at a time, EBUSY otherwise."""
    if queue_if_busy and _is_busy():
        return _enqueue_op(argv, merge_cmd=merge_cmd, plan_dir=plan_dir)
    if not _RUN_ACTIVE.acquire(blocking=False):
        if queue_if_busy:
            return _enqueue_op(argv, merge_cmd=merge_cmd, plan_dir=plan_dir)
        raise RpcError("EBUSY", "a cloud backup/restore is already running")
    try:
        try:
            _registry_busy()
        except RpcError:
            # The DETACHED-job gate, and it used to raise even for a caller that asked to queue -
            # the same "answered busy a moment ago, busy now" shape as the caller-side race, just
            # narrower (a registry read rather than a MEGA round trip). A game-end push or the
            # timer registering right here would still have produced the refusal a queue-capable
            # caller explicitly asked to avoid. Release first: we hold the lock at this point.
            if not queue_if_busy:
                raise
            _RUN_ACTIVE.release()
            return _enqueue_op(argv, merge_cmd=merge_cmd, plan_dir=plan_dir)
        op = argv[1:]          # the deck-cloud.sh subcommand + args (after ENGINE)
        _write_marker(op)
        job_id, proc = _spawn_registered(argv, kind=op[0], source="panel")
        s = _JobTailStream(job_id, proc=proc, owns_lock=True)
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
    """Tier A: back up saves + configs now. Manual = force past any failure backoff.
    Queues behind a running transfer rather than being refused."""
    return _stream_op([str(ENGINE), "push-precious", "--force"], queue_if_busy=True)


@method("cloud.sync")
def _cloud_sync(params):
    """Tier B: sync the big library (ROMs/media/...) now (rclone copy). Queues when busy."""
    return _stream_op([str(ENGINE), "sync-library"], queue_if_busy=True)


def _persist_games_plan_and_stream(ts, manifest, plan, subcmd="push-games", plan_root="games-plan",
                                   remote_token=None, merge_cmd=None):
    """Persist a plan-dir (a NUL src\\0rel\\0 list + the manifest) under the daemon's state dir, then STREAM
    deck-cloud.sh <subcmd> over it. Shared by cloud.push_game_assets (subcmd=push-games) and
    cloud.push_bios (subcmd=push-bios) and cloud.push_esde: every push subcommand treats each `rel` as an
    OPAQUE remote path suffix. The plan-dir id is the caller's real `ts` (UNIQUE per call). `remote_token` is
    the SET name in the remote path (fixed "games"/"bios" for the non-versioned single set, or `ts` for a
    versioned esde snapshot). `merge_cmd` (cat-manifest / cat-bios-manifest) fetches the existing remote
    manifest and MERGES the current selection into it, so a cloud re-backup of a fixed set accumulates
    (mirrors the local fixed-set merge); idempotent on auto-resume (immutable content -> re-merge is a no-op).

    NEVER CLOBBER: the uploaded manifest REPLACES the remote one, and it is the only index of what the set
    holds - so a merge that silently no-ops on a transient failure would make every previously uploaded game
    invisible to restore (the bytes survive, the record does not). The fetch rc therefore decides: 0 = merge;
    3 = the set does not exist yet (first push / an interrupted push that never wrote a manifest) so a fresh
    manifest is CORRECT; anything else (1 not-connected/usage, 5 retries exhausted, ...) is a transport
    failure -> ABORT instead of overwriting. rc 3-vs-5 measured on the vendored rclone v1.74.4 (`cat` of a
    missing object -> 3, of an unreachable endpoint -> 5); rclone.org/docs "List of exit codes"."""
    token = remote_token or ts
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
    argv = [str(ENGINE), subcmd, token, str(plandir)]
    try:
        # BUSY -> QUEUE, and the remote-manifest merge goes WITH it rather than happening here: the
        # manifest to merge against is the one that will be on MEGA when this job actually runs, not
        # the one there now. Two pushes to the same fixed set queued back to back would otherwise both
        # merge against today's index and the second would publish one with no trace of the first.
        if _is_busy():
            return _enqueue_op(argv, merge_cmd=merge_cmd or "", plan_dir=str(plandir))
        if merge_cmd:
            _merge_remote_manifest(merge_cmd, token, plandir)
        # queue_if_busy on the RUN-NOW path too, because the busy question was answered ABOVE and the
        # answer can go stale before we get here: _merge_remote_manifest is a MEGA round trip that
        # takes seconds, and for an "All" push the plan build before it walks the whole library.
        # Anything that starts in that window turned an intended queue into a hard EBUSY, and the
        # except below then deleted the plan dir, throwing the work away too.
        # Observed 2026-08-13 as "cloud.push_game_assets_all -> EBUSY" in mad-backend.log with no
        # job created. Note the engine being busy BEFORE the press already queued correctly (the
        # _is_busy branch above, verified against the pre-fix code), so the refusal specifically
        # requires the other op to arrive AFTER that check - which is exactly what this closes.
        res = _stream_op(argv, queue_if_busy=True, merge_cmd=merge_cmd or "",
                         plan_dir=str(plandir))
        if merge_cmd and res.get("queued"):
            # UNDO the run-now merge, because this job is now going to be merged AGAIN at dispatch
            # and a double merge is NOT a no-op. Measured, after I first claimed the opposite:
            #   * backup_manifest.merge sets updated = incoming.created, so feeding it an already
            #     merged file stamps the set with its BIRTH date instead of this backup's. The
            #     panel's cloud-restore picker shows and SORTS on that date, so the set would
            #     display as ancient and sink to the bottom of the list.
            #   * if the set is DELETED while this job waits in the queue, the dispatch-time merge
            #     correctly finds nothing (rc 3, no remote) and this pre-merged file would be
            #     published as the set index - listing items whose bytes were just purged.
            #   * an item refreshed by the running op between the two merges regresses to the
            #     older copy, including its src, which is the restore anchor.
            # Rewriting the selection-only manifest puts the plan dir back in exactly the state
            # the early-enqueue branch above leaves it in, so both queue paths are identical and
            # the dispatch-time merge is the only merge. `manifest` is safe to reuse:
            # _merge_remote_manifest re-reads the file from disk and never aliases it.
            backup_manifest.write(manifest, backup_manifest.manifest_path(plandir))
        return res
    except Exception:
        # the stream never started (EBUSY / spawn failure), so the shell will never consume + clean the
        # plan dir - drop it here so a rejected start can't orphan it. (A STARTED stream cleans the dir on
        # a clean finish and deliberately keeps it on failure so cloud.resume_pending can replay it.)
        shutil.rmtree(plandir, ignore_errors=True)
        raise


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
    from . import granular_cmds
    p = params or {}
    # An item that OMITS keys means "everything this game has"; plan_game_assets would otherwise skip
    # it entirely and upload nothing for that game (see granular_cmds._default_asset_keys).
    items = granular_cmds._default_asset_keys(p.get("items") or [])
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
    push-games over a persisted plan-dir. Merges into the SAME fixed 'games' remote set as
    cloud.push_game_assets: on MEGA the games side is ONE undated accumulating set; only
    esde/emucfg sets (and Tier A precious-versions) are dated. (The LOCAL "All" backup stays a dated
    deck-granular-games-<ts> folder - local disk is where discrete snapshots live.)

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
    # Fixed 'games' set + merge, same as the cherry-pick pushes: on MEGA only esde/emucfg (and the
    # Tier A precious-versions) are DATED - everything games-side accumulates in the ONE merged set.
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          remote_token="games", merge_cmd="cat-manifest")


@method("cloud.push_bios", slow=True)
def _cloud_push_bios(params):
    """CLOUD parity of the local BIOS backup: upload the chosen BIOS files to MEGA. params
    {items:[{bucket, stem}]} - actually [{bucket, rel}], the exact shape the local granular.backup_bios takes
    (rel = 'bios/<path>'). Resolves the selection + builds the manifest via the SAME planner as the local
    BIOS backup (granular_backup.plan_bios), then STREAMS deck-cloud.sh push-bios over a persisted plan-dir.
    push-bios uploads to a SEPARATE remote base (the fixed bios/ set) so a BIOS set never cross-lists in the
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
    over a persisted plan-dir. push-esde uploads to a SEPARATE remote base (esde/<ts>) so an ES-DE
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
    plan-dir. push-system uploads to a SEPARATE remote base so a system-config set never cross-lists in the
    game/BIOS/ES-DE/emucfg cloud restore, and MERGES into the fixed 'system' set (system/) -
    one undated accumulating set, like games/bios.

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
                                          subcmd="push-system", plan_root="system-plan",
                                          remote_token="system", merge_cmd="cat-system-manifest")


@method("cloud.push_controllers", slow=True)
def _cloud_push_controllers(params):
    """CLOUD parity of the local controller-config backup: upload the chosen controller config to MEGA. params
    {items:[{group, rel}]}. Resolves + builds the manifest via the SAME planner (granular_backup.
    plan_controllers), then STREAMS deck-cloud.sh push-controllers over a persisted plan-dir, uploading to a
    SEPARATE remote base so a controller set never cross-lists in another category's cloud restore, MERGED
    into the fixed 'controllers' set (controllers/) - one undated accumulating set, like
    games/bios/system. slow=True; empty selection -> RpcError (releases the C++ mRunning guard); auto-resumable."""
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no controller config selected")
    ts = time.strftime("%Y%m%dT%H%M%S")
    manifest, plan = granular_backup.plan_controllers(items, ts)
    if not plan:
        raise RpcError("EINVAL", "nothing to back up in the selection (no controller config is present)")
    return _persist_games_plan_and_stream(ts, manifest, plan,
                                          subcmd="push-controllers", plan_root="controllers-plan",
                                          remote_token="controllers", merge_cmd="cat-controllers-manifest")


@method("cloud.push_emucfg", slow=True)
def _cloud_push_emucfg(params):
    """CLOUD parity of the local emulator-config backup: upload the chosen emulator config/data to MEGA.
    params {items:[{emulator, group, rel}]} - the exact shape local granular.backup_emucfg takes. Resolves +
    builds the manifest via the SAME planner as the local backup (granular_backup.plan_emucfg), then STREAMS
    deck-cloud.sh push-emucfg over a persisted plan-dir. push-emucfg uploads to a SEPARATE remote base
    (emucfg/<ts>) so an emulator-config set never cross-lists in the game/BIOS/ES-DE cloud restore.

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
    texture/mod/NAND/HDD folders) to MEGA (a dated emucfg/<ts> set, as the per-emulator cloud path).
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


# ---- the multi-job transfer registry (transfers.*) --------------------------------

def _tail_progress(job_id: str):
    """Coarse (progress, summary) from the LAST stats line in a job's .out tail (~4 KB).
    (None, None) when the job has produced no stats yet."""
    reg = _registry()
    try:
        p = reg.out_path(job_id)
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > 4096:
                fh.seek(size - 4096)
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None, None
    # Item-count progress WINS over byte stats when both are present in the tail (see
    # _parse_progress). Scanning backwards, the newest MAD_SET_PROGRESS is authoritative, so take
    # the first one found and only fall back to a byte-stats line if the tail has none at all.
    fallback = (None, None)
    for line in reversed(lines):
        prog, summary = _parse_progress(line)
        if prog is None:
            continue
        if prog.get("items_total"):
            return prog, summary
        if fallback[0] is None:
            fallback = (prog, summary)
    return fallback


def _job_row(j: dict) -> dict:
    row = {"id": j.get("id"), "kind": j.get("kind") or "", "title": j.get("title") or "Transfer",
           "state": j.get("state") or "", "paused_by": j.get("paused_by") or "",
           "source": j.get("source") or "", "detached": bool(j.get("detached")),
           "started": j.get("created") or "", "rc": j.get("rc")}
    if j.get("state") in ("running", "paused"):
        prog, summary = _tail_progress(j["id"])
        row["pct"] = int(prog["overall_pct"]) if prog else 0
        row["summary"] = summary or ""
    elif j.get("state") == _registry().QUEUED:
        # A waiting job has no process and so no progress; its place in the queue is the useful
        # number instead. 1 = next to run.
        row["position"] = int(j.get("queue_pos") or 0)
    return row


def _get_job_or_raise(params):
    job_id = (params or {}).get("id") or ""
    job = _registry().get(job_id)
    if not job:
        raise RpcError("ENOENT", f"no such transfer: {job_id!r}")
    return job_id, job


@method("transfers.list", slow=True)
def _transfers_list(params):
    """EVERY known transfer (panel / hook / CLI / auto-resume), newest first: live ones
    with a coarse pct+summary from their .out tail, plus the newest terminal ones.
    Also runs the registry housekeeping (reap stale, prune old, thaw crash-orphaned
    gameplay-frozen jobs) so the tile is always looking at the truth."""
    reg = _registry()
    reg.reconcile()
    return {"jobs": [_job_row(j) for j in reg.list_jobs()]}


@method("transfers.attach")
def _transfers_attach(params):
    """A live stream over one job's output (same event shapes as the run streams), for
    the progress page's focused job. Tailing only - detach/stop of the STREAM never
    touches the job."""
    job_id, job = _get_job_or_raise(params)
    s = _JobTailStream(job_id)
    return {"stream": s.start(), "title": job.get("title") or "Transfer",
            "state": job.get("state") or ""}


@method("transfers.pause")
def _transfers_pause(params):
    """Freeze one job (SIGSTOP its process group), starttime-verified. Registered
    in-process ops (detached=false, e.g. granular) are not pausable."""
    job_id, job = _get_job_or_raise(params)
    if not job.get("detached"):
        raise RpcError("EINVAL", "this transfer cannot be paused")
    job = _registry().pause_job(job_id) or job
    return {"id": job_id, "state": job.get("state"),
            "paused": job.get("state") == "paused"}


@method("transfers.resume")
def _transfers_resume(params):
    """Thaw one job (SIGCONT). Works on user-paused AND gameplay-paused jobs."""
    job_id, job = _get_job_or_raise(params)
    job = _registry().resume_job(job_id) or job
    return {"id": job_id, "state": job.get("state"),
            "paused": job.get("state") == "paused"}


@method("transfers.stop", slow=True)
def _transfers_stop(params):
    """Halt one job (SIGCONT so a frozen group can die, SIGTERM, SIGKILL after 2 s) but
    KEEP the interrupted-transfer marker - a stopped upload stays resumable. An
    in-process job (detached=false) is stopped via its stream token instead."""
    job_id, job = _get_job_or_raise(params)
    if not job.get("detached"):
        tok = job.get("token")
        return {"id": job_id, "stopped": bool(tok and stop_stream(tok))}
    job = _registry().stop_job(job_id) or job
    return {"id": job_id, "stopped": job.get("state") in ("done", "failed")}


@method("transfers.cancel", slow=True)
def _transfers_cancel(params):
    """Halt AND forget one job: stop it, and when the interrupted-transfer marker is
    THIS op, clear it (+ drop its plan-dir) so auto-resume can never re-run it."""
    job_id, job = _get_job_or_raise(params)
    reg = _registry()
    if job.get("state") == reg.QUEUED:
        # It never started: drop the record and the plan dir it was holding. No signal (it has no
        # process group - see job_registry.signalable) and no marker to clear, because a queued job
        # deliberately never wrote one.
        rec = reg.dequeue(job_id)
        plan = (rec or {}).get("plan_dir")
        if plan and os.path.basename(os.path.dirname(plan)).endswith("-plan"):
            shutil.rmtree(plan, ignore_errors=True)
        return {"id": job_id, "cancelled": rec is not None, "was_queued": True}
    res = _transfers_stop({"id": job_id})
    if _marker_matches_job(job):
        m = _read_marker() or []
        _clear_marker()
        # Drop the PLAN DIR only for the push ops whose marker's 3rd field IS one
        # (push-games/bios/esde/...). For a restore marker that field is a snapshot id or
        # a user-supplied target directory - rmtree'ing that would delete live data.
        if job.get("kind") in _PUSH_CAT and len(m) > 2 and m[2] \
                and os.path.basename(os.path.dirname(m[2])).endswith("-plan"):
            shutil.rmtree(m[2], ignore_errors=True)
    return {"id": job_id, "cancelled": bool(res.get("stopped"))}


@method("transfers.reorder", slow=True)
def _transfers_reorder(params):
    """Move a WAITING transfer one place up or down the queue. params {id, direction:'up'|'down'}.

    Only queued jobs reorder - the running one is not part of the queue, and a job that has already
    started cannot be un-started. Returns the queue as the UI should now draw it."""
    p = params or {}
    job_id, job = _get_job_or_raise(p)
    reg = _registry()
    direction = (p.get("direction") or "").lower()
    if direction not in ("up", "down"):
        raise RpcError("EINVAL", "direction must be 'up' or 'down'")
    if job.get("state") != reg.QUEUED:
        raise RpcError("EINVAL", "only a waiting transfer can be reordered")
    moved = reg.reorder(job_id, -1 if direction == "up" else 1)
    return {"id": job_id, "moved": bool(moved),
            "queue": [{"id": j["id"], "title": j.get("title") or "Transfer"}
                      for j in reg.queued_jobs()]}


# ---- legacy single-op controls (one release of panel/backend skew insurance): act on
#      the NEWEST live cloud job via the registry, exactly what transfers.* would do ----

def _newest_live_cloud_job():
    for j in _registry().live_jobs():
        if j.get("kind") in _CLOUD_KINDS:
            return j
    return None


@method("cloud.pause")
def _cloud_pause(params):
    """LEGACY: freeze the newest live cloud transfer. New panels use transfers.pause."""
    j = _newest_live_cloud_job()
    if j is not None:
        j = _registry().pause_job(j["id"]) or j
    paused = bool(j and j.get("state") == "paused")
    with _ACTIVE_LOCK:
        _ACTIVE["paused"] = paused
    return {"paused": paused}


@method("cloud.resume")
def _cloud_resume(params):
    """LEGACY: thaw the newest live cloud transfer. New panels use transfers.resume."""
    j = _newest_live_cloud_job()
    if j is not None:
        j = _registry().resume_job(j["id"]) or j
    paused = bool(j and j.get("state") == "paused")
    with _ACTIVE_LOCK:
        _ACTIVE["paused"] = paused
    return {"paused": paused}


@method("cloud.stop", slow=True)
def _cloud_stop(params):
    """LEGACY: halt the newest live cloud transfer, KEEP the marker (resumable)."""
    j = _newest_live_cloud_job()
    if j is None:
        return {"stopped": False}
    j = _registry().stop_job(j["id"]) or j
    return {"stopped": j.get("state") in ("done", "failed")}


@method("cloud.cancel", slow=True)
def _cloud_cancel(params):
    """LEGACY: halt the newest live cloud transfer AND forget it. Delegates to
    transfers.cancel so the marker/plan-dir handling is the guarded one (a marker that
    belongs to a DIFFERENT op must survive). With nothing live it still clears the
    marker: that is this method's other job - the resume modal's DISCARD."""
    j = _newest_live_cloud_job()
    if j is None:
        _clear_marker()
        return {"cancelled": False}
    out = _transfers_cancel({"id": j["id"]})
    return {"cancelled": bool(out.get("cancelled"))}


@method("cloud.active")
def _cloud_active(params):
    """Reattach info for the panel. `running` is the REGISTRY's truth (a detached job
    survives this daemon, so the in-daemon stream handle alone would lie after a panel
    reopen); `token` is only set when THIS daemon session started the op - otherwise
    adopt via transfers.attach with `job`. `pending` = an interrupted transfer marker
    with NO live job behind it (a restore waits for the confirm modal)."""
    with _ACTIVE_LOCK:
        s = _ACTIVE["stream"]
        title = _ACTIVE["title"]
    j = _newest_live_cloud_job()
    if j is not None:
        return {"running": True, "token": (s.token if s is not None else None),
                "job": j.get("id"), "title": j.get("title") or title or "Cloud transfer",
                "paused": j.get("state") == "paused",
                "pending": False, "pending_restore": False}
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
# "games" set [push_game_assets] and a dated games "All" set [push_game_assets_all]; likewise bios).
_PURGE_SUBCMD = {"games": "purge-games", "bios": "purge-bios", "esde": "purge-esde",
                 "emucfg": "purge-emucfg", "system": "purge-system", "controllers": "purge-controllers"}
_PUSH_CAT = {"push-games": "games", "push-bios": "bios", "push-esde": "esde",
             "push-emucfg": "emucfg", "push-system": "system", "push-controllers": "controllers"}


def _delete_safe_token(t):
    """A cloud set token safe to purge: a 15-char YYYYmmddTHHMMSS OR one of the fixed undated set names
    games/bios/system/controllers. Mirror of granular_cmds._safe_settoken, inlined to avoid an import cycle;
    a destructive op re-validates before it shells out (the shell _purge_set validates a THIRD time - defense
    in depth). All THREE copies must list the same fixed names, or a set becomes listable-but-undeletable."""
    return bool(t) and (t in ("games", "bios", "system", "controllers") or
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
    # 1. If a LIVE job is uploading THIS set, stop it. The registry knows every one
    #    (incl. a detached job from a previous panel session); stop_job SIGCONTs a
    #    frozen group first so it can die.
    for j in _registry().live_jobs():
        argv = list(j.get("argv") or [])
        if _PUSH_CAT.get(j.get("kind")) == category and len(argv) > 1 and argv[1] == token:
            _registry().stop_job(j["id"])
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


@method("cloud.connect_setup", slow=True)
def _cloud_connect_setup(params):
    """Finish the MEGA connection AFTER the user placed the S4 keys file (the panel's
    CONNECT TO MEGA dialog): runs the idempotent deck-cloud-setup.sh - it validates the
    keys, writes the rclone remote stanza (env_auth: the secret never lands in
    rclone.conf), probes the bucket and saves the server. Never prompts; keys are only
    ever READ from the credentials file, never passed through this RPC."""
    setup = LAUNCHERS / "deck-cloud-setup.sh"
    if not setup.is_file():
        raise RpcError("ENOENT", "deck-cloud-setup.sh not found")
    try:
        p = subprocess.run(["bash", str(setup)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=180)
    except subprocess.TimeoutExpired:
        return {"connected": False, "message": "Setup timed out - check the network."}
    rc, _out, _err = _run(["is-connected"], timeout=60)
    lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    return {"connected": rc == 0,
            "message": lines[-1] if lines else ("ok" if rc == 0 else "setup failed")}


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
    for b in ("connected", "onexit_enabled", "autoresume_enabled", "gameplay_enabled"):
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


