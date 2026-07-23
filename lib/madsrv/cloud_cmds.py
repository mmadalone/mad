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
import signal
import subprocess
import threading
from pathlib import Path

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
                prog, disp = _parse_progress(line)
                if prog is not None:
                    self.emit({"progress": prog})
                if disp:
                    self.emit({"line": disp})
            if not self.stopped.is_set():
                rc = proc.wait()
        finally:
            # done ALWAYS precedes closed so the page can clear its sticky; rc -1 =
            # did not finish cleanly.
            self.emit({"done": True, "rc": rc})
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
