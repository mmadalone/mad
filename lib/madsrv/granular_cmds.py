"""granular.* - the read side of the granular Backup & Restore manager.

Two things the C++ Backup & Restore section needs, both READ-ONLY (no writes here; the backup +
restore STREAMS land in P1 with their rule-5 snapshot + ES-DE-running guard):

  granular.sources                -> the places you can browse FROM: the LIVE library (to pick what to
                                     back up) + every local backup that carries a manifest (to pick what
                                     to restore). Cloud sources arrive with the cloud backup+restore
                                     streams (P1).
  granular.browse {source,category[,system]}
                                  -> one drill level. No system -> the per-system/emulator TILES for a
                                     category; with a system -> the per-game/file rows. For source="live"
                                     the rows come from the on-disk library (es_gamelist + game_files);
                                     for a backup source they come straight from its mad-manifest.json.
  granular.categories             -> the category entry points (ROMs is the pilot; media/emu/bios/es-de
                                     land in later phases).

PILOT SCOPE: only the "roms" category has a LIVE provider wired here (per-system tiles -> per-game rows
with box art + a has_rom flag). A game whose gamelist entry points at a ROM that is no longer on disk
(a stale/region-swapped entry) comes back has_rom=false so the browser can grey it as "ROM missing" -
NOT silently dropped, NOT fuzzy-matched to a different file. The other categories return an empty live
list until their phase wires a provider; a backup source browses uniformly via the manifest.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .. import backup_manifest, es_gamelist, es_systems, game_files, granular_backup
from .rpc import RpcError, Stream, method
from .systems_cmds import TOOL_SYSTEMS, console_art

_GRAN_ACTIVE = threading.Lock()   # one granular backup/restore at a time (mirrors backup_cmds._RUN_ACTIVE)


def _ts() -> str:
    """A backup/snapshot timestamp: YYYYmmddTHHMMSS (matches backup_manifest + the backup dir name)."""
    return time.strftime("%Y%m%dT%H%M%S")

# Category entry points. `live` marks the ones a LIVE (on-disk) provider is wired for; a category
# without one can still be browsed from a backup source (via its manifest). Grows with phases 2-5.
CATEGORIES = [
    {"key": "roms", "label": "ROMs & games", "live": True},
]
_CATEGORY_KEYS = {c["key"] for c in CATEGORIES}

LIVE_SOURCE = "live"


# ---- live library enumeration (backup selection) ---------------------------

def _game_systems() -> list:
    """ES-DE systems that are real GAME systems (a visible-game gamelist, tool/picker systems excluded),
    sorted. This is the exact set the ROM browser offers - the enumeration behind the 100% figure."""
    from .preview_cmds import _esde_systems
    return sorted(s for s in _esde_systems() if s not in TOOL_SYSTEMS)


def _live_roms_systems() -> list:
    """Per-system TILE rows for the live ROM library: {key,label,art,count}. `count` is the number of
    games ES-DE shows for the system (present-or-missing); the item level flags which lack a ROM. The
    label is the SHORT name (the console art identifies the system); rows sort alphabetically by label."""
    rows = []
    for s in _game_systems():
        n = len(es_gamelist.visible_records(s))
        if not n:
            continue
        rows.append({"key": s, "label": es_systems.short_name(s),
                     "art": console_art(s), "count": n})
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def _live_roms_items(system: str) -> list:
    """Per-game rows for one live ROM system: {id,stem,name,art,has_rom,kind,size}. `has_rom` is false
    when the gamelist entry's ROM is absent on disk (stale/region-swapped) -> the browser greys it."""
    rows = []
    for stem_lower, rec in sorted(es_gamelist.visible_records(system).items(),
                                  key=lambda kv: kv[1].get("name", kv[0]).lower()):
        stem = rec.get("stem") or stem_lower
        paths = game_files.resolve_rom(system, stem)
        has_rom = bool(paths)
        kind = "folder" if (has_rom and os.path.isdir(paths[0])) else "file"
        # cheap size only for a single-file ROM; a folder ROM's size is deferred to backup time
        size = 0
        if has_rom and kind == "file":
            try:
                size = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
            except OSError:
                size = 0
        rows.append({"id": f"{system}:{stem}", "stem": stem,
                     "name": rec.get("name") or stem,
                     "art": game_files.resolve_boxart(system, stem).get("covers"),
                     "has_rom": has_rom, "kind": kind, "size": size})
    return rows


_LIVE_SYSTEMS = {"roms": _live_roms_systems}
_LIVE_ITEMS = {"roms": _live_roms_items}


# ---- sources ---------------------------------------------------------------

def _local_backup_roots() -> list:
    """Directories to scan for local backups: the user's chosen backup destination + the default,
    de-duplicated. A backup is a mirror FOLDER (mad-manifest.json inside) or an archive with a
    sidecar (<archive>.mad-manifest.json)."""
    from . import backup_cmds
    roots, seen = [], set()
    for d in (backup_cmds._remembered_dest(), backup_cmds.DEFAULT_DEST):
        rp = os.path.realpath(os.path.expanduser(d))
        if rp not in seen and os.path.isdir(rp):
            seen.add(rp)
            roots.append(Path(rp))
    return roots


def _local_backup_sources() -> list:
    """Every local backup that carries a readable, valid manifest, newest first."""
    out = []
    for root in _local_backup_roots():
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            # a manifest lives inside a folder, or as an <archive>.mad-manifest.json sidecar; skip the
            # sidecar files themselves (they are found via their backup, not on their own).
            if p.name.endswith("." + backup_manifest.MANIFEST_NAME):
                continue
            m = backup_manifest.read(p)
            if not backup_manifest.validate(m):
                continue
            out.append({"id": str(p), "kind": "local", "label": p.name,
                        "created": m.get("created", ""),
                        "count": sum(c.get("n_items", 0) for c in backup_manifest.categories(m))})
    out.sort(key=lambda s: s.get("created", ""), reverse=True)
    return out


@method("granular.categories")
def _granular_categories(params):
    """The category entry points for the Backup & Restore hub."""
    return {"categories": [{"key": c["key"], "label": c["label"]} for c in CATEGORIES]}


@method("granular.sources")
def _granular_sources(params):
    """LOCAL browse sources (fast, no network): the live library (backup selection) + local backups with a
    manifest (restore selection). Cloud restore sources come from granular.cloud_sources (slow)."""
    sources = [{"id": LIVE_SOURCE, "kind": "live",
                "label": "Current library (to back up)", "created": ""}]
    sources.extend(_local_backup_sources())
    return {"sources": sources}


# ---- cloud restore sources / manifest (per-game restore FROM MEGA) ----------

def _safe_ts(ts: str) -> bool:
    """A granular timestamp token YYYYmmddTHHMMSS - safe to interpolate into a remote path + a shell arg."""
    return bool(ts) and len(ts) == 15 and ts[8] == "T" and ts[:8].isdigit() and ts[9:].isdigit()


def _cloud_source_ts(source: str) -> str:
    return source.split(":", 1)[1] if source.startswith("cloud:") else ""


def _cloud_run(args: list, timeout: int = 120):
    """Run a bounded deck-cloud.sh subcommand (the rclone owner) for the cloud restore browse. Returns
    (rc, stdout); a spawn failure / timeout is (1, "")."""
    from . import cloud_cmds
    try:
        p = subprocess.run([str(cloud_cmds.ENGINE), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return p.returncode, (p.stdout or "")


def _cloud_manifest(ts: str) -> dict:
    """The granular manifest of a cloud game-backup set (deck-cloud.sh cat-manifest). Raises RpcError."""
    if not _safe_ts(ts):
        raise RpcError("EINVAL", f"bad cloud backup id: {ts!r}")
    rc, out = _cloud_run(["cat-manifest", ts], timeout=60)
    if rc != 0 or not out.strip():
        raise RpcError("ENOENT", f"cannot read cloud backup {ts}")
    m = backup_manifest.read_text(out)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"cloud backup {ts} has an invalid manifest")
    return m


@method("granular.cloud_sources", slow=True)
def _granular_cloud_sources(params):
    """Cloud game-backup sets available to restore FROM MEGA (deck-cloud.sh list-games). slow=True: it
    shells out + hits the network, so the C++ fetches it async and shows 'Looking on MEGA...' until it
    lands. {connected, sources:[{id:"cloud:<ts>", kind:"cloud", created:<ts>, count}]}."""
    rc, out = _cloud_run(["list-games"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}   # not connected / no bucket -> the UI shows a hint
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        ts, count = line.split("\t", 1)
        ts, count = ts.strip(), count.strip()
        if not _safe_ts(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": ts,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


# ---- browse ----------------------------------------------------------------

def _browse_live(category: str, system: str | None) -> dict:
    if system is None:
        fn = _LIVE_SYSTEMS.get(category)
        systems = fn() if fn else []          # a category with no live provider yet -> empty
        return {"level": "systems", "source": LIVE_SOURCE, "category": category,
                "systems": systems}
    fn = _LIVE_ITEMS.get(category)
    items = fn(system) if fn else []
    return {"level": "items", "source": LIVE_SOURCE, "category": category,
            "system": system, "items": items}


def _manifest_systems_rows(m: dict, category: str) -> list:
    """Per-system TILE rows for a manifest source (local backup or cloud): SHORT label + console art +
    count, sorted alphabetically by the visible label. Shared by the local and cloud restore browse."""
    rows = [{"key": s["key"], "label": es_systems.short_name(s["key"]),
             "art": console_art(s["key"]), "count": s["n_items"]}
            for s in backup_manifest.systems(m, category)]
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def _browse_manifest(source: str, category: str, system: str | None) -> dict:
    m = backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return _manifest_browse_result(m, source, category, system)


def _browse_cloud(source: str, category: str, system: str | None) -> dict:
    """Browse a cloud game-backup set via its manifest (deck-cloud.sh cat-manifest) - no ROM download to
    browse; identical shape to a local manifest browse."""
    m = _cloud_manifest(_cloud_source_ts(source))
    return _manifest_browse_result(m, source, category, system)


def _manifest_browse_result(m: dict, source: str, category: str, system: str | None) -> dict:
    if system is None:
        return {"level": "systems", "source": source, "category": category,
                "systems": _manifest_systems_rows(m, category)}
    return {"level": "items", "source": source, "category": category, "system": system,
            "items": backup_manifest.items(m, category, system)}


@method("granular.browse", slow=True)
def _granular_browse(params):
    """One drill level of a source. params: {source, category, system?}. source="live" browses the on-disk
    library; "cloud:<ts>" browses a MEGA backup via its manifest; any other source is a local backup path.
    No system -> per-system tiles; with a system -> per-game/file rows. slow=True: a cloud/live browse can
    shell out / walk gamelists, so it runs off the stdin thread."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    category = p.get("category")
    system = p.get("system") or None
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    if source == LIVE_SOURCE:
        return _browse_live(category, system)
    if source.startswith("cloud:"):
        return _browse_cloud(source, category, system)
    return _browse_manifest(source, category, system)


# ---- backup + restore STREAMS (the write paths) ----------------------------

class _GranularStream(Stream):
    """Runs one granular engine call (backup or restore) on the stream thread, forwarding its progress
    via emit and its cancel signal via is_stopped. Always emits a terminal {done} (rc 0 ok, -1 cancel/
    error) so the page can clear its 'working...' state, and releases _GRAN_ACTIVE in finally."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn                      # fn(emit, is_stopped) -> summary dict

    def run(self):
        try:
            summary = self._fn(self.emit, lambda: self.stopped.is_set())
            self.emit({"done": True, "rc": 0, **summary})
        except granular_backup.Cancelled:
            self.emit({"done": True, "rc": -1, "stopped": True})
        except Exception as exc:           # ValueError (bad manifest) / RuntimeError (ES-DE up) / OSError
            self.emit({"done": True, "rc": -1, "error": str(exc)})
        finally:
            _GRAN_ACTIVE.release()


def _start_granular(fn):
    """Acquire the single-op lock and start a _GranularStream, releasing the lock if the start fails
    before run()'s finally can (mirrors backup_cmds._backup_run_full)."""
    if not _GRAN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a granular backup or restore is already running")
    try:
        return {"stream": _GranularStream(fn).start()}
    except Exception:
        _GRAN_ACTIVE.release()
        raise


@method("granular.backup")
def _granular_backup(params):
    """Back up the selected games to a granular backup folder. params: {category, items:[{system,stem}],
    dest?}. dest defaults to the remembered backup destination. Streams progress; {done, path, copied,
    skipped} at the end."""
    from . import backup_cmds
    p = params or {}
    category = p.get("category")
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no items selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    label = next((c["label"] for c in CATEGORIES if c["key"] == category), category)
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_selection(
            items, dest, category, label, ts, emit, stopped))


@method("granular.restore_preview", slow=True)
def _granular_restore_preview(params):
    """READ-ONLY preview of a restore selection so the page can WARN before overwriting. params:
    {source, category, items}. Returns {replace, fresh, skip, restart_scope} - `replace` are the games
    already present live that a restore would overwrite (a recoverable snapshot is taken first). slow=True:
    a cloud source cats its manifest over the network. A cloud preview classifies from the manifest alone
    (the files are still on MEGA), so no download happens here."""
    p = params or {}
    source = p.get("source")
    category = p.get("category")
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    try:
        if source.startswith("cloud:"):
            return granular_backup.restore_preview_manifest(
                _cloud_manifest(_cloud_source_ts(source)), p.get("items") or [], category)
        return granular_backup.restore_preview(source, p.get("items") or [], category)
    except ValueError as exc:
        raise RpcError("ENOENT", str(exc))


@method("granular.restore")
def _granular_restore(params):
    """Restore the selected items from a backup source back to the live library. params: {source,
    category, items:[{system, id|stem}]}. Rule #5: existing targets are snapshotted aside first. Streams
    progress; {done, restored, skipped, snapshot, restart_scope} at the end. Refuses (EBUSY) a category
    that needs ES-DE closed while ES-DE is running."""
    p = params or {}
    source = p.get("source")
    category = p.get("category")
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no items selected")
    # guard here too (not only in the engine) so the refusal is a clean RPC error, not a stream event
    meta = granular_backup.category_meta(category)
    if meta["needs_esde_stopped"]:
        from .. import proc_guard
        if proc_guard.esde_running():
            raise RpcError("EBUSY", "close ES-DE before restoring this category")
    ts = _ts()
    if source.startswith("cloud:"):
        return _start_granular(
            lambda emit, stopped: _cloud_restore(source, items, category, ts, emit, stopped))
    return _start_granular(
        lambda emit, stopped: granular_backup.restore_selection(
            source, items, category, ts, emit, stopped))


def _cloud_restore(source: str, items: list, category: str, ts: str, emit, is_stopped) -> dict:
    """Cloud restore (runs inside the granular stream): DOWNLOAD the selected games (+ the manifest) from a
    cloud set to a temp staging dir (streamed via deck-cloud.sh fetch-games), then restore_selection(staging)
    with the EXISTING, reviewed engine (rule-5 snapshot + replace + orphaned all reused). Staging is always
    cleaned up. Cancellable during the download (terminates fetch-games) and inside restore_selection."""
    import shutil
    import tempfile
    cts = _cloud_source_ts(source)
    manifest = _cloud_manifest(cts)   # cat over the network, inside the stream thread (RPC stays fast)
    rom_root = granular_backup.es_collections.rom_root()
    # Build the NUL fetch plan (rel\0kind\0), gating EVERY rel through the SAME validator the restore uses
    # (_plan_restore_item: roms/<system>/ prefix + _safe_component + control-char reject + _within), so a
    # forged/corrupt cloud manifest cannot make the DOWNLOAD escape the staging dir. (The restore-time guard
    # would also reject it, but only after the bytes had already been written.) The download set is thus
    # exactly the restore set.
    plan = bytearray()
    for it in items:
        p = granular_backup._plan_restore_item(manifest, category, it, Path("/__cloud__"),
                                               rom_root, check_backup_file=False)
        if not p.get("ok"):
            continue
        rel = p["item"].get("rel")
        if not isinstance(rel, str) or not rel:
            continue
        kind = str(p["item"].get("kind") or "file")
        plan += rel.encode("utf-8") + b"\0" + kind.encode("utf-8") + b"\0"
    if not plan:
        return {"restored": 0, "replaced": 0, "skipped": len(items), "orphaned": [], "snapshot": None,
                "restart_scope": granular_backup.category_meta(category)["restart_scope"]}
    staging = Path(tempfile.mkdtemp(prefix="mad-cloud-restore-"))
    planfd, planpath = tempfile.mkstemp(prefix="mad-cloud-plan-")
    try:
        with os.fdopen(planfd, "wb") as fh:
            fh.write(bytes(plan))
        emit({"line": "Downloading from MEGA..."})
        if _stream_fetch(cts, staging, planpath, emit, is_stopped) != 0:
            raise RuntimeError("could not download the games from MEGA (see the cloud log)")
        # Restore from the downloaded staging folder with the UNCHANGED local engine.
        return granular_backup.restore_selection(str(staging), items, category, ts, emit, is_stopped)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.unlink(planpath)
        except OSError:
            pass


def _stream_fetch(ts: str, staging: Path, planpath: str, emit, is_stopped) -> int:
    """Run deck-cloud.sh fetch-games, forwarding each per-game line via emit; return its rc. The read is
    INTERRUPTIBLE - a select() poll re-checks is_stopped every 0.5s - so a cancel / daemon teardown during a
    long single-game rclone copy (which emits nothing to the pipe for the whole copy) promptly terminates the
    child and lets the callers clean the staging dir. Always closes the pipe + reaps the child (no leaked FD /
    zombie). Binary reads via os.read so a select-ready partial line never blocks readline()."""
    import select
    from . import cloud_cmds
    proc = subprocess.Popen([str(cloud_cmds.ENGINE), "fetch-games", ts, str(staging), planpath],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    buf = b""
    try:
        while True:
            if is_stopped():
                proc.terminate()
                raise granular_backup.Cancelled()
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break                                     # child gone + no pending output
                continue
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break                                         # EOF
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    emit({"line": text})
        tail = buf.decode("utf-8", "replace").rstrip()        # any trailing partial line
        if tail:
            emit({"line": tail})
        return proc.wait()
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()                                    # reap after SIGKILL (no zombie)
