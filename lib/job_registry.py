"""Persistent transfer-job registry - the single source of truth for every long
backup/restore transfer on this Deck (panel, game-end hook, CLI, auto-resume).

Why it exists: transfers used to live only as pipes inside the mad-backend daemon,
so closing the MAD panel killed them, and anything the daemon did not spawn (the
on-exit hook push, a CLI run) was invisible to the Transfers tile. Now every long
job is registered HERE as one <id>.json (state) + one <id>.out (raw output lines)
under $DECK_CLOUD_STATE_DIR/jobs/ (default ~/.config/deck-cloud/jobs/):

- deck-cloud.sh registers ITSELF at dispatch (_job_begin/_job_end), so hook + CLI
  runs appear with zero wrapper; the backend only passes DECK_CLOUD_JOB_ID when it
  spawns one so both sides agree on the id.
- the mad-backend spawns transfers DETACHED (new session, output to the .out file,
  no pipe) and only TAILS the .out - daemon teardown stops tailers, never jobs.
- the game-start/game-end hooks freeze/thaw running jobs by pgid (SIGSTOP/SIGCONT)
  when the BACKUP DURING GAMEPLAY toggle is off (absent gameplay.enabled file). A
  restore/fetch job (see protected_during_gameplay()) is frozen EITHER way: the
  toggle's name only promises to keep BACKUPS (uploads) running, and it overwrites
  live saves/config the running game may still hold open.

DEPENDENCY-FREE ON PURPOSE (stdlib only, no lib.* imports): bash callers run it as
a plain script (`python3 lib/job_registry.py <cmd> ...`), and it must import in
every context the engine runs in (hook, CLI, daemon, tests).

Concurrency: every read-modify-write takes an flock on jobs/.lock; JSON writes are
tmp+rename atomic. Pid reuse is guarded by the /proc starttime captured at begin()
(field 22 of /proc/<pid>/stat): a signal/state transition is only applied when the
live process's starttime still matches.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

TERMINAL = ("done", "failed")
LIVE = ("running", "paused")
# A job the user asked for while another was running: recorded and persisted like any other, but with
# NO process behind it yet. Deliberately NOT in LIVE, for two reasons that are easy to get wrong:
#   * _reap_locked fails any LIVE job whose pid is gone, and a queued job has no pid - it would be
#     marked failed within REAP_GRACE_S of being queued;
#   * the dispatcher asks "is anything running?" via live_jobs() before starting the queue head, so a
#     queued job counted as live would block its own start.
# It is not TERMINAL either, so _prune_locked keeps it. Ordering is queue_pos (lower runs first),
# assigned at enqueue and rewritten by reorder().
QUEUED = "queued"
KEEP_TERMINAL = 20            # newest terminal jobs kept (with their .out) after prune
GAMEPLAY_STALE_S = 12 * 3600  # a gameplay.active older than this is a crash leftover
# Reaping ignores records touched within this window: the process may be gone while its
# rc is still in flight (the engine's EXIT trap, or the backend spawner recording a fast
# child's exit) - reaping instantly would race that write and mislabel a clean finish as
# failed. A genuinely dead job is caught on the next pass; the tile lag is cosmetic.
REAP_GRACE_S = 5.0

# kind -> user-facing title (deck-cloud subcommands + the backend's own kinds).
TITLES = {
    "push-precious": "Backing up saves", "sync-library": "Syncing library",
    "push-games": "Backing up games", "push-bios": "Backing up BIOS",
    "push-esde": "Backing up ES-DE settings", "push-emucfg": "Backing up emulator config",
    "push-system": "Backing up system config",
    "push-controllers": "Backing up controller config",
    "restore-precious": "Restoring saves", "restore-library": "Restoring library",
    "fetch-games": "Downloading games", "fetch-bios": "Downloading BIOS",
    "fetch-esde": "Downloading ES-DE settings", "fetch-emucfg": "Downloading emulator config",
    "fetch-system": "Downloading system config",
    "fetch-controllers": "Downloading controller config",
    "backup.run_full": "Full local backup",
    "granular": "Copying files",
    "flatpak-lutris": "Installing Lutris",
}


def title_for(kind: str) -> str:
    return TITLES.get(kind, "Transfer")


def state_dir() -> Path:
    """Read the env at CALL time (tests + deck-cloud.sh parity)."""
    return Path(os.environ.get("DECK_CLOUD_STATE_DIR") or (Path.home() / ".config" / "deck-cloud"))


def jobs_dir() -> Path:
    return state_dir() / "jobs"


def out_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.out"


def _json_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def gameplay_marker() -> Path:
    return state_dir() / "gameplay.active"


def protected_during_gameplay(kind: str) -> bool:
    """Whether `kind` is a DOWNLOAD - the direction that can end up overwriting live
    saves/config - so it must never keep running against a game that may hold those
    files open, whatever the BACKUP DURING GAMEPLAY toggle says (that toggle's name
    only promises to keep BACKUPS, i.e. uploads, running). Not every covered kind
    writes live files itself: the six fetch-* kinds (fetch-games/bios/esde/emucfg/
    system/controllers) only pull into a staging dir their caller passes in, and
    restore-precious/restore-library only write live when the caller also passes
    --to-live. Freezing them anyway is what keeps a restore pipeline from finishing
    a fetch stage and rolling straight into the write stage mid-game. Prefix-based
    rather than a fixed set: every download kind in TITLES starts with one of these
    two prefixes, and any future download kind is covered automatically without
    another call site to remember. This is the one place cloud_cmds.py and
    job_registry.py both consult, so the three gameplay-freeze sites agree on what
    "protected" means."""
    return kind.startswith("restore-") or kind.startswith("fetch-")


class _Lock:
    """flock on jobs/.lock around every read-modify-write."""

    def __enter__(self):
        jobs_dir().mkdir(parents=True, exist_ok=True)
        self._fh = open(jobs_dir() / ".lock", "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
        return False


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_SEQ = 0
# In-process lock: new_id() is reached concurrently from the RPC thread pool, the
# granular stream threads and the auto-resume thread; a plain += is a read-modify-write
# that can hand two of them the same counter within one second (same timestamp + pid).
_SEQ_LOCK = threading.Lock()


def new_id() -> str:
    """Unique per call: timestamp + pid + an in-process counter. The counter matters -
    two begins in the SAME process and second (e.g. a granular op next to a full
    backup) would otherwise collide and silently overwrite each other's record."""
    global _SEQ
    with _SEQ_LOCK:
        _SEQ += 1
        seq = _SEQ
    return time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}-{seq}"


def starttime_of(pid: int):
    """/proc/<pid>/stat field 22 - the kernel's per-boot start tick, the pid-reuse
    guard. None when the process is gone/unreadable. Parses after the LAST ')' so a
    comm containing spaces/parens cannot shift the fields."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_bytes().decode("ascii", "replace")
        fields = raw.rsplit(")", 1)[1].split()
        return int(fields[19])          # field 22 overall; 20th after comm
    except (OSError, IndexError, ValueError):
        return None


def _write_json(job_id: str, data: dict) -> None:
    p = _json_path(job_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def _read_json(job_id: str):
    try:
        return json.loads(_json_path(job_id).read_text())
    except (OSError, ValueError):
        return None


def get(job_id: str):
    return _read_json(job_id)


def _alive(job: dict) -> bool:
    """The job's recorded process is still the SAME process (starttime match)."""
    pid = job.get("pid")
    st = starttime_of(pid) if pid else None
    return st is not None and st == job.get("starttime")


alive = _alive   # public alias: the backend's tail stream checks job liveness too


def begin(kind: str, pid: int, job_id: str = "", argv=None, source: str = "cli",
          detached: bool = True, title: str = "", token: str = "") -> str:
    """Register a job (engine self-registration or backend spawn). Returns the id.
    pgid/starttime are captured HERE from the live pid so bash callers only pass $$."""
    job_id = job_id or new_id()
    try:
        pgid = os.getpgid(int(pid))
    except OSError:
        pgid = int(pid)
    job = {"id": job_id, "kind": kind, "title": title or title_for(kind),
           "argv": list(argv or []), "pid": int(pid), "pgid": pgid,
           "starttime": starttime_of(pid), "state": "running", "paused_by": None,
           "rc": None, "detached": bool(detached), "source": source,
           "token": token or None, "created": _now(), "updated": _now()}
    with _Lock():
        _write_json(job_id, job)
        out_path(job_id).touch()
    return job_id


def update(job_id: str, **fields):
    with _Lock():
        job = _read_json(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated"] = _now()
        _write_json(job_id, job)
        return job


def end(job_id: str, rc: int):
    return update(job_id, state=("done" if int(rc) == 0 else "failed"),
                  rc=int(rc), paused_by=None)


def _all_jobs() -> list:
    d = jobs_dir()
    if not d.is_dir():
        return []
    jobs = []
    for p in sorted(d.glob("*.json")):
        try:
            jobs.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    # id as the tiebreak: created has second granularity, and ids embed a timestamp +
    # counter, so same-second jobs still order newest-first deterministically.
    jobs.sort(key=lambda j: (j.get("created") or "", j.get("id") or ""), reverse=True)
    return jobs


def _reap_locked() -> list:
    """Mark LIVE jobs whose process is gone (or whose pid was reused) as failed.
    Caller holds the lock. Returns the refreshed list, newest first. Records touched
    within REAP_GRACE_S are left alone (see the constant's rationale)."""
    jobs = _all_jobs()
    for j in jobs:
        if j.get("state") in LIVE and not _alive(j):
            try:
                if time.time() - _json_path(j["id"]).stat().st_mtime < REAP_GRACE_S:
                    continue
            except OSError:
                pass
            j.update(state="failed", rc=(j.get("rc") if j.get("rc") is not None else -1),
                     paused_by=None, updated=_now())
            _write_json(j["id"], j)
    return jobs


def _prune_locked(jobs: list, keep: int = KEEP_TERMINAL) -> list:
    terminal = [j for j in jobs if j.get("state") in TERMINAL]
    for j in terminal[keep:]:
        for p in (_json_path(j["id"]), out_path(j["id"])):
            try:
                p.unlink()
            except OSError:
                pass
    return [j for j in jobs if j.get("state") not in TERMINAL] + terminal[:keep]


def list_jobs(reap: bool = True, prune: bool = True) -> list:
    """Every known job, newest first; stale live entries reaped, old terminal ones
    (beyond the newest KEEP_TERMINAL) pruned with their .out files."""
    with _Lock():
        jobs = _reap_locked() if reap else _all_jobs()
        if prune:
            jobs = _prune_locked(jobs)
    return jobs


def live_jobs() -> list:
    return [j for j in list_jobs(prune=False) if j.get("state") in LIVE]


def queued_jobs() -> list:
    """The waiting queue, in dispatch order (queue_pos, then creation). Never includes a job that has
    already started - a dispatched job leaves QUEUED for 'running' in the same locked transition."""
    q = [j for j in list_jobs(reap=False, prune=False) if j.get("state") == QUEUED]
    q.sort(key=lambda j: (j.get("queue_pos", 0), j.get("created") or "", j.get("id") or ""))
    return q


def enqueue(kind: str, argv=None, source: str = "panel", title: str = "", plan_dir: str = "") -> str:
    """Record a job to run LATER (no process yet). Returns the id. `plan_dir`, when given, is the
    persisted plan the dispatcher will hand the engine - it is stored so cancelling a still-queued job
    can delete it, which the caller cannot do once it has handed the plan over."""
    job_id = new_id()
    with _Lock():
        tail = [j.get("queue_pos", 0) for j in _all_jobs() if j.get("state") == QUEUED]
        job = {"id": job_id, "kind": kind, "title": title or title_for(kind),
               "argv": list(argv or []), "pid": None, "pgid": None, "starttime": None,
               "state": QUEUED, "paused_by": None, "rc": None, "detached": True,
               "source": source, "token": None, "plan_dir": plan_dir or None,
               "queue_pos": (max(tail) + 1) if tail else 1,
               "created": _now(), "updated": _now()}
        _write_json(job_id, job)
        out_path(job_id).touch()
    return job_id


def dequeue(job_id: str) -> dict | None:
    """Drop a job that has NOT started (the user cancelled a waiting entry). Returns the removed record
    so the caller can clean up its plan dir, or None if the id is unknown or already running - a started
    job must be stopped, never silently forgotten."""
    with _Lock():
        job = _read_json(job_id)
        if job is None or job.get("state") != QUEUED:
            return None
        for p in (_json_path(job_id), out_path(job_id)):
            try:
                p.unlink()
            except OSError:
                pass
        return job


def reorder(job_id: str, direction: int) -> bool:
    """Move a QUEUED job one place earlier (direction<0) or later (>0) by swapping queue_pos with its
    neighbour. Returns False when the id is not queued or is already at that end. Only waiting jobs
    reorder - the running one is not part of the queue."""
    with _Lock():
        q = [j for j in _all_jobs() if j.get("state") == QUEUED]
        q.sort(key=lambda j: (j.get("queue_pos", 0), j.get("created") or "", j.get("id") or ""))
        idx = next((i for i, j in enumerate(q) if j.get("id") == job_id), -1)
        other = idx + (1 if direction > 0 else -1)
        if idx < 0 or other < 0 or other >= len(q):
            return False
        # Swap POSITIONS rather than list order, so the pair keeps a total order even if some other
        # writer added an entry between the read and the write.
        q[idx]["queue_pos"], q[other]["queue_pos"] = q[other].get("queue_pos", 0), q[idx].get("queue_pos", 0)
        for j in (q[idx], q[other]):
            j["updated"] = _now()
            _write_json(j["id"], j)
        return True


def start_queued(job_id: str, pid: int) -> dict | None:
    """Atomically turn the queue head into a running job once its process exists. Returns the updated
    record, or None if it is no longer queued (cancelled while the dispatcher was spawning) - the
    caller must then kill the process it just started, because nothing else will track it."""
    with _Lock():
        job = _read_json(job_id)
        if job is None or job.get("state") != QUEUED:
            return None
        try:
            pgid = os.getpgid(int(pid))
        except OSError:
            pgid = int(pid)
        job.update(state="running", pid=int(pid), pgid=pgid, starttime=starttime_of(pid),
                   queue_pos=None, updated=_now())
        _write_json(job_id, job)
        return job


def signalable(job: dict) -> bool:
    """Whether this job's process GROUP may be signalled from here.

    THE load-bearing safety invariant: never signal our OWN process group. A job that
    shares it is not a separate transfer we can control - it is code running inside the
    caller's own tree (an in-daemon granular op; or a child spawned without
    start_new_session, whose group is the mad-backend daemon's == ES-DE's own group,
    since MadBackend forks python3 with no setsid). SIGSTOP/SIGTERM there freezes or
    kills ES-DE itself. Holds in every context that signals: the daemon and the ES-DE
    game hooks both run inside ES-DE's group, while a genuinely detached job always has
    a group of its own.

    A QUEUED job has no process yet, so pgid is None. That must be refused OUTRIGHT and
    before anything else: `int(None or 0)` is 0, and killpg(0, sig) is defined as "signal
    the CALLER's process group" - i.e. the one case this whole function exists to prevent,
    reached by the one job kind that has no group at all. Cancel a queued job with
    dequeue(), never a signal."""
    if job.get("state") == QUEUED or job.get("pgid") is None or job.get("pid") is None:
        return False
    try:
        if int(job.get("pgid")) == os.getpgrp():
            return False
    except (TypeError, ValueError, OSError):
        return False
    return bool(job.get("detached"))


def _signal_job(job: dict, sig) -> bool:
    """killpg the job's group, starttime-verified, never our own group (see signalable).
    False when it is gone or must not be signalled."""
    if not _alive(job) or not signalable(job):
        return False
    try:
        os.killpg(int(job["pgid"]), sig)
        return True
    except OSError:
        return False


def pause_job(job_id: str, by: str = "user") -> dict | None:
    with _Lock():
        job = _read_json(job_id)
        if not job or job.get("state") != "running" or not signalable(job):
            return job
        if _signal_job(job, signal.SIGSTOP):
            job.update(state="paused", paused_by=by, updated=_now())
            _write_json(job_id, job)
        return job


def resume_job(job_id: str) -> dict | None:
    with _Lock():
        job = _read_json(job_id)
        if not job or job.get("state") != "paused":
            return job
        if _signal_job(job, signal.SIGCONT):
            job.update(state="running", paused_by=None, updated=_now())
            _write_json(job_id, job)
        return job


def stop_job(job_id: str, grace: float = 2.0) -> dict | None:
    """SIGCONT (a frozen group must be able to die), SIGTERM, then SIGKILL after
    `grace`. The engine's own EXIT trap records the final rc; if the group dies
    without it, the next reap marks the job failed.

    Refuses a job whose group is not ours to signal (see signalable) - the SAME guard
    pause_job applies, with a stronger signal at stake: an in-daemon job's group is
    ES-DE's, so an unguarded SIGTERM here would kill the frontend. Such a job is
    stopped through its stream token instead (transfers.stop does that routing)."""
    with _Lock():
        job = _read_json(job_id)
        if not job or job.get("state") not in LIVE:
            return job
        if not signalable(job):
            return job
        _signal_job(job, signal.SIGCONT)
        _signal_job(job, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        with _Lock():
            job = _read_json(job_id)
        if not job or job.get("state") in TERMINAL or not _alive(job):
            break
        time.sleep(0.1)
    with _Lock():
        job = _read_json(job_id)
        if job and job.get("state") in LIVE:
            if _alive(job):
                _signal_job(job, signal.SIGKILL)
            job.update(state="failed", rc=143, paused_by=None, updated=_now())
            _write_json(job_id, job)
    return job


def _freeze_for_gameplay_locked(j: dict) -> bool:
    """SIGSTOP one RUNNING job and persist state='paused'/paused_by='gameplay'.
    Caller holds the lock and passes a freshly-read record. Shared by
    pause_all_gameplay() (toggle OFF: every running job) and deprioritize_running()
    (toggle ON: restore/fetch jobs only - see protected_during_gameplay). Factored so
    both freeze paths write the EXACT same shape, which matters because
    resume_gameplay() thaws by matching paused_by == 'gameplay' alone."""
    if j.get("state") == "running" and signalable(j) and _signal_job(j, signal.SIGSTOP):
        j.update(state="paused", paused_by="gameplay", updated=_now())
        _write_json(j["id"], j)
        return True
    return False


def pause_all_gameplay() -> int:
    """Game-start, toggle OFF: freeze every RUNNING detached job (paused_by=
    'gameplay'). USER-paused jobs are left exactly as they are. Returns the count."""
    n = 0
    with _Lock():
        for j in _reap_locked():
            if _freeze_for_gameplay_locked(j):
                n += 1
    return n


def resume_gameplay() -> int:
    """Game-end: thaw EXACTLY the gameplay-paused jobs. A user-paused job stays
    paused - the user chose that. Returns the count."""
    n = 0
    with _Lock():
        for j in _reap_locked():
            if j.get("state") == "paused" and j.get("paused_by") == "gameplay" \
                    and _signal_job(j, signal.SIGCONT):
                j.update(state="running", paused_by=None, updated=_now())
                _write_json(j["id"], j)
                n += 1
    return n


def deprioritize_running() -> int:
    """Game-start, toggle ON: keep UPLOADS running but imperceptible - ionice idle +
    nice 19 per running group. renice is one-way for non-root (the job stays
    background priority after the game): accepted trade-off, jobs are background
    work anyway.

    A restore/fetch job is never merely deprioritized here, toggle or no: it
    OVERWRITES live saves/config the running game may hold open, and the toggle's
    name only promises to keep backups (uploads) running - see
    protected_during_gameplay(). Such a job takes the SAME freeze path
    pause_all_gameplay() uses instead. The thaw side needs no matching change:
    resume_gameplay() already thaws anything with paused_by == 'gameplay', frozen
    here or there alike.

    Returns the count of jobs TOUCHED (deprioritized + frozen) - i.e. how many
    running transfers this call changed something about, not "how many are safe"."""
    import subprocess
    n = 0
    for j in live_jobs():
        # signalable: never renice/ionice/signal OUR OWN group - a non-detached job's
        # pgid is ES-DE's, and renice is one-way for non-root, so a hit would
        # permanently idle the frontend and everything it launches.
        if j.get("state") != "running" or not _alive(j) or not signalable(j):
            continue
        if protected_during_gameplay(j.get("kind", "")):
            with _Lock():
                fresh = _read_json(j["id"])   # re-read: state may have moved since live_jobs()
                if fresh and _freeze_for_gameplay_locked(fresh):
                    n += 1
            continue
        pgid = str(int(j["pgid"]))
        subprocess.run(["ionice", "-c3", "-P", pgid], capture_output=True)
        subprocess.run(["renice", "-n", "19", "-g", pgid], capture_output=True)
        n += 1
    return n


def reconcile() -> int:
    """Crash safety: thaw gameplay-paused jobs when NO game is live - the marker is
    absent (the game-end hook removed it, or never ran) or stale (>12 h = a crashed
    session left it behind). Called at backend start, from transfers.list, and by
    the engine at _job_begin (a new run implies the last game ended in practice)."""
    marker = gameplay_marker()
    try:
        age = time.time() - marker.stat().st_mtime
        if age <= GAMEPLAY_STALE_S:
            return 0
    except OSError:
        pass          # absent marker: thaw
    else:
        try:
            marker.unlink()   # stale marker: remove, then thaw
        except OSError:
            pass
    return resume_gameplay()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: job_registry.py <begin|end|update|list|reap|pause|resume|stop|"
              "pause-all-gameplay|resume-gameplay|deprioritize-running|reconcile> ...",
              file=sys.stderr)
        return 2
    cmd = args.pop(0)
    if cmd == "begin":
        opts = {"kind": "", "pid": os.getppid(), "job_id": "", "argv": [],
                "source": "cli", "title": ""}
        while args:
            a = args.pop(0)
            if a == "--kind":
                opts["kind"] = args.pop(0)
            elif a == "--pid":
                opts["pid"] = int(args.pop(0))
            elif a == "--id":
                opts["job_id"] = args.pop(0)
            elif a == "--source":
                opts["source"] = args.pop(0)
            elif a == "--title":
                opts["title"] = args.pop(0)
            elif a == "--argv":
                opts["argv"] = args
                args = []
        print(begin(opts["kind"], opts["pid"], job_id=opts["job_id"],
                    argv=opts["argv"], source=opts["source"], title=opts["title"]))
        return 0
    if cmd == "end":
        end(args[0], int(args[1] if len(args) > 1 else -1))
        return 0
    if cmd == "list":
        print(json.dumps(list_jobs(), indent=1))
        return 0
    if cmd == "reap":
        list_jobs()
        return 0
    if cmd == "pause":
        pause_job(args[0])
        return 0
    if cmd == "resume":
        resume_job(args[0])
        return 0
    if cmd == "stop":
        stop_job(args[0])
        return 0
    if cmd == "pause-all-gameplay":
        print(f"paused {pause_all_gameplay()} job(s) for gameplay")
        return 0
    if cmd == "resume-gameplay":
        print(f"resumed {resume_gameplay()} job(s) after gameplay")
        return 0
    if cmd == "deprioritize-running":
        print(f"deprioritized/froze {deprioritize_running()} job(s) for gameplay "
              "(uploads deprioritized, restores/fetches frozen)")
        return 0
    if cmd == "reconcile":
        print(f"reconciled {reconcile()} job(s)")
        return 0
    print(f"job_registry.py: unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
