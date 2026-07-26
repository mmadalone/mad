"""granular_backup - the copy/restore ENGINE for the granular Backup & Restore manager (pilot: ROMs).

Pure, Tk-free file logic wrapped by lib/madsrv/granular_cmds.py's streams. Progress is reported through
an emit(dict) callback and work is cooperatively cancellable via is_stopped().

RULE #5 IS ABSOLUTE: a restore NEVER deletes or overwrites a live file in place. Any existing target is
first MOVED ASIDE to a SAME-FILESYSTEM _TMP snapshot (an instant rename) with a RECOVERY.txt carrying the
exact rollback command, and only then is the restored copy written. The snapshot path is reported so it
can always be found and undone.

PILOT SCOPE (roms): a game's ROM file OR folder. Add-ons (texture packs / mods / cheats) and the other
categories layer on top later; the category meta table + emit/cancel plumbing here are built to be reused.
Restore reads from a granular backup FOLDER (the format granular.backup writes); archive/cloud restore is
a later pass.
"""
from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from . import backup_manifest, es_collections, es_systems, game_files, proc_guard

GRANULAR_PREFIX = "deck-granular-"          # a granular backup folder: <dest>/deck-granular-<ts>/
SNAPSHOT_PREFIX = "_TMP-granular-restore-"  # rule-5 pre-restore snapshot dir (same filesystem as target)

# Per-category restore mechanics. `needs_esde_stopped`: restoring these touches files ES-DE rewrites on
# exit (rule #3), so ES-DE must be closed first. `restart_scope`: what the user must restart for the
# restore to take effect (none | esde | emulator). ROMs need neither. Unknown categories default to the
# SAFE side (require ES-DE closed) so a future category can never silently skip the guard.
_CATEGORY_META = {
    "roms": {"needs_esde_stopped": False, "restart_scope": "none"},
    # ES-DE never writes saves/states (the emulator does, on its own launch), so restoring them does not
    # need ES-DE closed; the emulator reads the restored file on its next launch (no restart_scope).
    "saves": {"needs_esde_stopped": False, "restart_scope": "none"},
    "states": {"needs_esde_stopped": False, "restart_scope": "none"},
    # media files are only READ by ES-DE (never rewritten on exit like gamelist.xml), so a restore is safe
    # while it runs, but ES-DE caches media - a restart is needed to SEE the restored art.
    "media": {"needs_esde_stopped": False, "restart_scope": "esde"},
}
_DEFAULT_META = {"needs_esde_stopped": True, "restart_scope": "esde"}


def category_meta(category: str) -> dict:
    return _CATEGORY_META.get(category, _DEFAULT_META)


def _restore_root(category: str, rom_root):
    """(root, per_system) for a category's restore target.

    roms anchors PER-SYSTEM: each ~/ROMs/<system> is its own relocation symlink (e.g. ps2 -> internal), so
    the shipped code realpath's rom_root/<system> and contains the target there. saves/states/media anchor
    at a SINGLE root that may itself hold front-door symlinks deeper in (e.g. ~/Emulation/saves/retroarch/
    saves -> the RetroArch flatpak); for those the target is contained LEXICALLY under the root (the rel is
    validated free of ../abs/control, so the joined path cannot escape) and the copy FOLLOWS the front-door
    symlink - realpath-containment would wrongly reject the legit flatpak destination (the very trap the
    roms per-system anchor sidesteps). Returns (None, False) for an unknown category so restore refuses."""
    if category == "roms":
        return rom_root, True
    from . import esde_settings, mad_paths
    fns = {"saves": mad_paths.saves_root, "states": mad_paths.saves_root,
           "media": esde_settings.media_root}
    fn = fns.get(category)
    return (fn() if fn else None), False


class Cancelled(Exception):
    """Raised inside the engine when is_stopped() goes true mid-copy; the stream turns it into a clean
    stopped-without-done event. On a restore, any overwritten original is already safe in the snapshot."""


# ---- path safety (a restore may read a FOREIGN/corrupt manifest, e.g. from cloud) ------------------

def _safe_component(name: str) -> bool:
    """A single path component that cannot escape its parent: non-empty, not '.'/'..', no separator,
    not absolute. Guards the system/basename fields a restore keys off a possibly-untrusted manifest."""
    return bool(name) and name not in (".", "..") and "/" not in name \
        and "\\" not in name and "\x00" not in name and not os.path.isabs(name)


def _within(child: str, parent: str) -> bool:
    """True iff realpath(child) is parent itself or lives under it - the containment check that keeps a
    restore write inside the ROM root and a backup read inside the backup folder."""
    child = os.path.realpath(child)
    parent = os.path.realpath(parent).rstrip("/")
    return child == parent or child.startswith(parent + os.sep)


# ---- copy primitives -------------------------------------------------------

def _copy_path(src: str, dst: str, emit, is_stopped) -> int:
    """Copy a file or a whole folder src -> dst, returning bytes copied. A folder is walked file by file
    so progress streams and cancellation can land BETWEEN files (never mid-file, so no torn file). Raises
    Cancelled if is_stopped() goes true between files."""
    src, dst = str(src), str(dst)
    if os.path.isdir(src):
        total = 0
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            out = os.path.join(dst, rel) if rel != "." else dst
            os.makedirs(out, exist_ok=True)
            for name in files:
                if is_stopped():
                    raise Cancelled()
                s = os.path.join(root, name)
                if os.path.islink(s) or os.path.isfile(s):
                    d = os.path.join(out, name)
                    shutil.copy2(s, d, follow_symlinks=False)
                    try:
                        total += os.path.getsize(d)
                    except OSError:
                        pass
        return total
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    try:
        return os.path.getsize(dst)
    except OSError:
        return 0


def _path_size(path: str) -> int:
    """Byte size of a file or the recursive size of a folder (best-effort; unreadable parts count 0)."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ---- rule-5 snapshot -------------------------------------------------------

def _samefs_snap_root(rom_root_real: str, ts: str) -> Path:
    """A snapshot dir SANDWICHED next to the real ROM directory - i.e. on the SAME filesystem as the
    targets, so moving a live file aside is an instant rename (deck-restore.sh's `$RT/_TMP-restore-...`
    for the SD case; the internal drive for internal ROMs). Deriving it from the (realpath'd) ROM ROOT -
    never a fixed global like ~/Downloads - keeps it same-fs on any device AND lets a test with a
    sandbox ROM root snapshot inside that sandbox. Even if the parent turns out cross-device, shutil.move
    still preserves the original (copy+unlink), so rule #5 holds; only the instant-rename speed is lost."""
    base = Path(rom_root_real).parent
    snap = base / (SNAPSHOT_PREFIX + ts)
    snap.mkdir(parents=True, exist_ok=True)
    rec = snap / "RECOVERY.txt"
    if not rec.exists():
        rec.write_text(
            "Pre-restore snapshot taken by the MAD granular restore, BEFORE overwriting.\n"
            "Each live file/folder that a restore replaced was MOVED here (fully recoverable).\n"
            "To roll one back: remove the restored copy at its original path, then move the saved\n"
            "copy below back to that path. Delete this folder once you are happy.\n\n",
            encoding="utf-8")
    return snap


def _snapshot_aside(target_real: str, snap_root: Path, relname: str, emit) -> str:
    """Move an existing target out of the way into snap_root/relname (instant rename when same-fs; a
    cross-device copy+unlink otherwise - the data is preserved either way). Appends the exact rollback
    command to RECOVERY.txt. Returns the saved path."""
    dest = snap_root / relname
    dest.parent.mkdir(parents=True, exist_ok=True)
    # RULE #5: NEVER clobber an existing snapshot - a prior snapshot at this path already holds a genuine
    # original, so pick a unique sibling instead of letting shutil.move overwrite it (belt-and-braces to
    # restore_selection's per-target dedupe; together they guarantee the true original is never destroyed).
    if os.path.lexists(str(dest)):
        i = 1
        while os.path.lexists(str(snap_root / f"{relname}.{i}")):
            i += 1
        dest = snap_root / f"{relname}.{i}"
    shutil.move(target_real, str(dest))
    with (snap_root / "RECOVERY.txt").open("a", encoding="utf-8") as fh:
        # shell-quote EVERY interpolated path (incl. the header) so an unusual/crafted ROM name - a
        # space, quote, $, or even an embedded newline - can neither break the rollback command nor
        # inject spurious lines into the recovery notes.
        fh.write(f'# {shlex.quote(relname)}\n'
                 f'rm -rf {shlex.quote(target_real)} && '
                 f'mv {shlex.quote(str(dest))} {shlex.quote(target_real)}\n\n')
    emit({"line": f"saved existing {relname} -> {snap_root}"})
    return str(dest)


# ---- backup ----------------------------------------------------------------

def plan_selection(items: list, category: str, category_label: str, ts: str, emit=None,
                   is_stopped=None):
    """Resolve the selected games to a backup PLAN + a manifest, WITHOUT copying anything.

    Returns (manifest, plan) where plan = [{id, name, system, stem, src, rel, kind}] - one entry per game
    that resolves to a real ROM living under its own system dir. Applies the SAME skip rules as the copy in
    backup_selection (malformed item; ROM missing; a resolver result OUTSIDE realpath(rom_root/<system>),
    e.g. an rpcs3 dev_hdd0 install), so a LOCAL backup and a CLOUD upload select byte-identically off the one
    planner. Writes nothing to disk. `emit` (optional) receives a {"line": ...} for each game skipped with a
    reason; when None (the pre-stream cloud path, before its stream exists) the skips are silent.
    `is_stopped` (optional) makes the resolve/size phase cancellable per item - the local backup passes it
    through (folder-ROM sizing can walk a large tree); the pre-stream cloud caller leaves it None."""
    def _say(msg):
        if emit is not None:
            emit(msg)
    manifest = backup_manifest.new_manifest("granular", created=ts)
    rom_root = es_collections.rom_root()
    plan: list = []
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        system = it.get("system")
        stem = it.get("stem") or (it.get("id", "").split(":", 1)[1] if ":" in it.get("id", "") else "")
        if not system or not stem or not _safe_component(system):
            continue
        paths = game_files.resolve_rom(system, stem)
        rec = es_gamelist_record(system, stem)
        name = rec.get("name") or stem
        if not paths:
            _say({"line": f"skip (ROM missing): {name}"})
            continue
        src = os.path.realpath(paths[0])
        sysdir = os.path.realpath(str(rom_root / system))
        # Only a ROM that actually lives UNDER its system's ROM dir (following the per-system relocation
        # symlink, e.g. ~/ROMs/ps2 -> internal) can round-trip through the ROMs category. A resolver result
        # OUTSIDE it - e.g. an rpcs3 PSN title installed under ~/.config/rpcs3/dev_hdd0 - is emulator DATA,
        # not a plain ROM; the emulator-config category owns that. Skip it rather than mis-restore it.
        if not _within(src, sysdir):
            _say({"line": f"skip (not a plain ROM under {system}/): {name}"})
            continue
        rel_rom = os.path.relpath(src, sysdir)     # basename, or subdir/basename - the sub-path is PRESERVED
        kind = "folder" if os.path.isdir(src) else "file"
        rel = f"roms/{system}/{rel_rom}"
        art = game_files.resolve_boxart(system, stem).get("covers")
        backup_manifest.add_item(
            manifest, category=category, category_label=category_label,
            system=system, system_label=es_systems.fullname(system),
            item=backup_manifest.make_item(
                id=f"{system}:{stem}", name=name, src=src, rel=rel,
                kind=kind, size=_path_size(src), stem=stem, boxart=bool(art),
                extra={"art": art} if art else None))
        plan.append({"id": f"{system}:{stem}", "name": name, "system": system,
                     "stem": stem, "src": src, "rel": rel, "kind": kind})
    return manifest, plan


def backup_selection(items: list, dest_dir: str, category: str, category_label: str,
                     ts: str, emit, is_stopped) -> dict:
    """Back up the selected games (pilot: their ROM file/folder) into <dest_dir>/deck-granular-<ts>/ and
    write a mad-manifest.json. `items` = [{system, stem}]. Returns {path, copied, skipped}. A game whose
    ROM is absent (or is emulator data outside its ROM dir) is skipped (reported), never faked. Resolution +
    manifest-building are delegated to plan_selection so a local backup and a cloud upload select the exact
    same games from the exact same rules."""
    backupdir = Path(dest_dir) / (GRANULAR_PREFIX + ts)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_selection(items, category, category_label, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        backup_manifest.write(manifest, backup_manifest.manifest_path(backupdir))
    else:
        # nothing landed -> don't leave an empty, manifest-less folder masquerading as a backup
        try:
            backupdir.rmdir()
        except OSError:
            pass
    # every input item was either planned (and copied) or dropped by plan_selection's skip rules.
    return {"path": str(backupdir), "copied": copied, "skipped": len(items) - copied}


def es_gamelist_record(system: str, stem: str) -> dict:
    """Thin indirection over es_gamelist.record so tests can monkeypatch names without importing it."""
    from . import es_gamelist
    return es_gamelist.record(system, stem)


# ---- game-first backup (P2): one game's ticked assets across categories -----

_CATEGORY_LABELS = {"roms": "ROMs & games", "media": "Downloaded media",
                    "saves": "Saves", "states": "Save states", "cheats": "Cheats"}


def plan_game_assets(games: list, ts: str, emit=None, is_stopped=None):
    """Resolve a GAME-FIRST selection to a backup PLAN + a multi-category manifest, WITHOUT copying.
    `games` = [{system, stem, keys:[asset-group-key,...]}] - the ticked asset groups per game (keys are
    the group keys game_files.resolve_game_assets returns: rom/media/saves/states/...). Returns
    (manifest, plan) where plan = [{id, name, system, category, src, rel, kind}], one entry per concrete
    live file across every ticked+present group. Reuses game_files.resolve_game_assets so the backup
    selects EXACTLY the files the game-first UI showed. Each manifest item carries extra={"game","asset"}
    so a later browse/restore can regroup a backup's items by game. Writes nothing to disk."""
    def _say(msg):
        if emit is not None:
            emit(msg)
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    try:
        systems = es_systems.load_systems()
    except Exception:
        systems = None
    for g in games:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        system = g.get("system")
        gid = g.get("id", "")
        stem = g.get("stem") or (gid.split(":", 1)[1] if ":" in gid else "")
        keys = set(g.get("keys") or [])
        if not system or not stem or not _safe_component(system) or not keys:
            continue
        name = es_gamelist_record(system, stem).get("name") or stem
        groups = game_files.resolve_game_assets(system, stem, systems)
        planned_here = 0
        for grp in groups:
            if grp["key"] not in keys or not grp["present"]:
                continue
            cat = grp["category"]
            for f in grp["files"]:
                # id = the backup-relative path (unique per file); browse/restore key off it. The
                # game+asset back-link lets a game-first restore regroup a backup's items by game.
                backup_manifest.add_item(
                    manifest, category=cat, category_label=_CATEGORY_LABELS.get(cat, cat),
                    system=system, system_label=es_systems.fullname(system),
                    item=backup_manifest.make_item(
                        id=f["rel"], name=name, src=f["src"], rel=f["rel"],
                        kind=f["kind"], size=f.get("size", 0), stem=stem,
                        extra={"game": f"{system}:{stem}", "asset": grp["key"]}))
                plan.append({"id": f["rel"], "name": name, "system": system, "category": cat,
                             "src": f["src"], "rel": f["rel"], "kind": f["kind"]})
                planned_here += 1
        if not planned_here:
            _say({"line": f"skip (nothing to back up): {name}"})
    return manifest, plan


def backup_game_assets(games: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up a GAME-FIRST selection (each game's ticked asset groups) into <dest>/deck-granular-<ts>/
    with a mad-manifest.json spanning every touched category. `games` = [{system, stem, keys:[...]}].
    Returns {path, copied, files}. Resolution + manifest-building delegate to plan_game_assets so backup
    copies exactly the planned files. A game/group with nothing present is reported and skipped."""
    backupdir = Path(dest_dir) / (GRANULAR_PREFIX + ts)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_game_assets(games, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up {entry['category']}: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        backup_manifest.write(manifest, backup_manifest.manifest_path(backupdir))
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


# ---- restore (rule #5) -----------------------------------------------------
# One SHARED planner resolves a selected item to its manifest entry, in-backup file and live target, and
# says whether that target already EXISTS. Both the pre-restore PREVIEW (which drives the C++ "these will
# be replaced" warning) and the actual restore call it, so the warning can never disagree with the write.

def _open_source(source: str, category: str):
    """Validate + open a restore source. Returns (manifest, source_dir, rom_root, meta). Raises
    ValueError on a bad/foreign manifest or a non-folder source (archive/cloud restore is a later pass)."""
    m = backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise ValueError("no valid backup manifest for this source")
    source_dir = Path(source)
    if not source_dir.is_dir():
        raise ValueError("granular restore needs a backup folder (archive/cloud restore is later)")
    return m, source_dir, es_collections.rom_root(), category_meta(category)


def _plan_restore_item(m: dict, category: str, it: dict, source_dir: Path, rom_root,
                       check_backup_file: bool = True) -> dict:
    """Resolve one selection entry to {ok, reason, id, name, item, backup_file, target, exists}. `ok` is
    False (with a reason) when the item is not in the manifest, its path is unsafe (foreign/corrupt
    manifest), its backup file is missing, or its target would escape the ROM root - and such items are
    never written or counted. When ok, `exists` says the live target is already there (a REPLACE).
    `check_backup_file` is False for the CLOUD preview, whose backup files live on MEGA (not on disk yet),
    so it classifies replace/fresh from the manifest + the live target alone."""
    system = it.get("system")
    item_id = it.get("id") or (f"{system}:{it['stem']}" if it.get("stem") else None)
    item = backup_manifest.find_item(m, category, system, item_id) if (system and item_id) else None
    if not item:
        return {"ok": False, "reason": "not_in_manifest", "id": item_id, "name": item_id}
    name = item.get("name", item_id)
    rel = item.get("rel")
    prefix = f"{category}/"
    # A foreign/corrupt manifest may omit or forge rel/system; validate BEFORE building any path. Reject
    # ANY control char (a newline could inject RECOVERY.txt lines), any absolute rel, and any ''/'.'/'..'
    # component (traversal), and require the category's rel-prefix. These string checks alone guarantee the
    # lexical join below cannot escape the root; the copy is then free to follow a legit front-door symlink.
    if not (isinstance(rel, str) and rel and rel.startswith(prefix)
            and not any(ord(c) < 0x20 for c in rel) and not os.path.isabs(rel)
            and not any(part in ("", ".", "..") for part in rel.split("/"))):
        return {"ok": False, "reason": "unsafe_path", "id": item_id, "name": name}
    backup_file = source_dir / rel
    if not _within(str(backup_file), str(source_dir)):
        return {"ok": False, "reason": "unsafe_path", "id": item_id, "name": name}
    if check_backup_file and not backup_file.exists():
        return {"ok": False, "reason": "missing_in_backup", "id": item_id, "name": name}

    root, per_system = _restore_root(category, rom_root)
    if root is None:
        return {"ok": False, "reason": "unknown_category", "id": item_id, "name": name}
    if per_system:
        # roms: rebuild UNDER the per-system relocation symlink (realpath'd) from the backup-relative
        # sub-path, and anchor containment to realpath(rom_root/<system>) - NOT realpath(rom_root). That is
        # what makes symlinked systems (ps2/ps3/switch/gba/openbor, whose dir points off the ~/ROMs volume)
        # restorable, while _within still blocks any '..'/symlink escape.
        if not (system and _safe_component(system) and rel.startswith(f"roms/{system}/")):
            return {"ok": False, "reason": "unsafe_path", "id": item_id, "name": name}
        rel_rom = rel[len(f"roms/{system}/"):]
        sysdir = os.path.realpath(str(root / system))
        target = os.path.realpath(os.path.join(sysdir, rel_rom))
        if not _within(target, sysdir):
            return {"ok": False, "reason": "target_escapes_root", "id": item_id, "name": name}
        snap_rel = os.path.join(system, rel_rom)
        root_used = os.path.realpath(str(root))  # the ROM root: snapshot lands OUTSIDE the ~/ROMs scan tree
    else:
        # saves/states/media: LEXICAL containment under the category root. DON'T realpath the target - a
        # front-door symlink inside the root (e.g. retroarch/saves -> flatpak) resolves OUTSIDE the root and
        # realpath-containment would falsely reject it; the ''/'.'/'..'/abs rejections above already prove
        # the joined path stays under the root, and _copy_path follows the symlink when it writes.
        rel_rem = rel[len(prefix):]
        root_s = os.path.normpath(str(root))
        target = os.path.normpath(os.path.join(root_s, rel_rem))
        if target != root_s and not target.startswith(root_s + os.sep):
            return {"ok": False, "reason": "target_escapes_root", "id": item_id, "name": name}
        snap_rel = rel   # unique per file (category/.../name); the snapshot subpath name only
        root_used = root_s
    return {"ok": True, "reason": "", "id": item_id, "name": name, "item": item,
            "backup_file": backup_file, "target": target, "rel": snap_rel, "root": root_used,
            "exists": os.path.lexists(target)}


def restore_preview(source: str, items: list, category: str) -> dict:
    """READ-ONLY: classify a restore selection WITHOUT touching disk, so the UI can warn before writing.
    Returns {replace:[{id,name}], fresh:[{id,name}], skip:[{id,name,reason}], restart_scope}. `replace`
    is the set whose live files already exist and will be overwritten (a recoverable snapshot is kept)."""
    m, source_dir, rom_root, meta = _open_source(source, category)
    replace, fresh, skip = [], [], []
    for it in items:
        try:
            p = _plan_restore_item(m, category, it, source_dir, rom_root)
        except Exception:
            iid = it.get("id") or it.get("system")
            skip.append({"id": iid, "name": iid, "reason": "corrupt_item"})
            continue
        row = {"id": p["id"], "name": p["name"]}
        if not p["ok"]:
            skip.append({**row, "reason": p["reason"]})
        elif p["exists"]:
            replace.append(row)
        else:
            fresh.append(row)
    return {"replace": replace, "fresh": fresh, "skip": skip,
            "restart_scope": meta["restart_scope"]}


def restore_preview_manifest(m: dict, items: list, category: str) -> dict:
    """READ-ONLY CLOUD preview: classify a restore selection straight from a MANIFEST DICT (the backup
    files are on MEGA, not on disk yet), so the UI can warn before a cloud restore downloads + overwrites.
    Same shape as restore_preview. `replace` = games whose live target already exists (a recoverable
    snapshot is taken first). No download, no disk writes."""
    if not backup_manifest.validate(m):
        raise ValueError("no valid backup manifest for this cloud backup")
    meta = category_meta(category)
    rom_root = es_collections.rom_root()
    # a placeholder source dir: there are no local backup files to check (they live on MEGA), so
    # check_backup_file=False skips the on-disk existence test while the target/containment checks stand.
    dummy = Path("/__cloud__")
    replace, fresh, skip = [], [], []
    for it in items:
        try:
            p = _plan_restore_item(m, category, it, dummy, rom_root, check_backup_file=False)
        except Exception:
            iid = it.get("id") or it.get("system")
            skip.append({"id": iid, "name": iid, "reason": "corrupt_item"})
            continue
        row = {"id": p["id"], "name": p["name"]}
        if not p["ok"]:
            skip.append({**row, "reason": p["reason"]})
        elif p["exists"]:
            replace.append(row)
        else:
            fresh.append(row)
    return {"replace": replace, "fresh": fresh, "skip": skip,
            "restart_scope": meta["restart_scope"]}


_RESTART_ORDER = {"none": 0, "emulator": 1, "esde": 2}


def _stricter_restart(a: str, b: str) -> str:
    """The more-demanding of two restart scopes (none < emulator < esde)."""
    return a if _RESTART_ORDER.get(a, 0) >= _RESTART_ORDER.get(b, 0) else b


def _snap_root_for(root_used: str, ts: str, snap_roots: dict, emit) -> Path:
    """The rule-5 snapshot dir for a target under `root_used` (a category's realpath'd root). Placed next to
    the realpath'd root, on the SAME filesystem (so OUTSIDE any tree ES-DE/emulators scan) for an instant
    move-aside. Keyed + deduped by the DERIVED snapshot PATH (two category roots that share a parent - e.g.
    ~/ROMs and downloaded_media both on the SD card - resolve to the one snapshot dir, emitted once)."""
    sr = _samefs_snap_root(os.path.realpath(str(root_used)), ts)   # idempotent: mkdir + one-shot RECOVERY.txt
    key = str(sr)
    if key not in snap_roots:
        snap_roots[key] = sr
        emit({"snapshot": str(sr)})
    return sr


def _restore_one(p: dict, ts: str, snap_roots: dict, done_targets: set, orphaned: list, emit,
                 is_stopped) -> str:
    """Restore ONE planned item (a successful _plan_restore_item result) under rule #5: an existing target
    is snapshotted aside FIRST (never overwritten in place). Returns 'restored' (fresh target), 'replaced'
    (an existing target was moved aside then written), 'skipped' (an alias already done this run, or a
    FRESH-target copy error where nothing is at risk), or 'orphaned' (the original was moved aside but the
    copy then failed - the live slot needs a RECOVERY.txt rollback). On an orphan it appends {id,name,
    snapshot} to `orphaned` itself (it holds the snapshot dir in scope) and emits the orphan event; the
    caller only tallies counts + emits item_done."""
    target = p["target"]
    if target in done_targets:   # two entries aliasing one target: restore once, never re-snapshot
        emit({"line": f"skip (already restored this run): {p['name']}"})
        return "skipped"
    did_replace = False          # set once the live original has been moved to the snapshot
    sr: Path | None = None       # the snapshot dir this item's original was moved into (if any)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if p["exists"]:
            sr = _snap_root_for(p["root"], ts, snap_roots, emit)
            _snapshot_aside(target, sr, p["rel"], emit)   # RULE #5: raises BEFORE the copy runs
            did_replace = True
        emit({"line": f"restoring: {p['name']}"})
        _copy_path(str(p["backup_file"]), target, emit, is_stopped)
    except Cancelled:
        raise                    # cancellation is not a per-item skip
    except Exception as exc:
        if did_replace:
            # RULE #5 "report it so it can be found and undone": the original was moved aside but the copy
            # failed, so the live slot is now empty/partial. Surface it DISTINCTLY (not a benign skip).
            orphaned.append({"id": p["id"], "name": p["name"], "snapshot": str(sr) if sr else None})
            emit({"orphaned": p["id"], "name": p["name"],
                  "snapshot": str(sr) if sr else None, "error": str(exc)})
            return "orphaned"
        emit({"line": f"skip (restore error): {p['name']}: {exc}"})   # fresh target - nothing at risk
        return "skipped"
    done_targets.add(target)
    return "replaced" if did_replace else "restored"


def _finish_restore(restored: int, replaced: int, skipped: int, orphaned: list, snap_roots: dict,
                    restart_scope: str) -> dict:
    """Assemble the terminal summary. `snapshot` is the FIRST snapshot dir (back-compat) and `snapshots`
    lists all of them (a game-first restore can span filesystems -> one snapshot dir per category root)."""
    roots = [str(v) for v in snap_roots.values()]
    return {"restored": restored, "replaced": replaced, "skipped": skipped, "orphaned": orphaned,
            "snapshot": roots[0] if roots else None, "snapshots": roots,
            "restart_scope": restart_scope}


def restore_selection(source: str, items: list, category: str, ts: str, emit, is_stopped) -> dict:
    """Restore the selected items from a granular backup FOLDER back to the live library (SINGLE category).
    Rule #5: any existing target is snapshotted aside FIRST. `items` = [{system, id|stem}]. Returns
    {restored, replaced, skipped, orphaned, snapshot, snapshots, restart_scope}. Raises ValueError on an
    invalid/foreign manifest and RuntimeError when the category needs ES-DE closed but it is running."""
    m, source_dir, rom_root, meta = _open_source(source, category)
    if meta["needs_esde_stopped"] and proc_guard.esde_running():
        raise RuntimeError("close ES-DE before restoring this category")
    snap_roots: dict = {}
    restored = replaced = skipped = 0
    orphaned: list = []
    done_targets: set = set()
    for it in items:
        if is_stopped():
            raise Cancelled()
        try:
            p = _plan_restore_item(m, category, it, source_dir, rom_root)
        except Exception:
            emit({"line": f"skip (corrupt item): {it.get('id') or it.get('system')}"})
            skipped += 1
            continue
        if not p["ok"]:
            emit({"line": f"skip ({p['reason']}): {p['name']}"})
            skipped += 1
            continue
        st = _restore_one(p, ts, snap_roots, done_targets, orphaned, emit, is_stopped)
        if st in ("restored", "replaced"):
            restored += 1
            if st == "replaced":
                replaced += 1
            emit({"item_done": p["id"], "restored": restored})
        elif st != "orphaned":   # orphaned was recorded by _restore_one; anything else is a skip
            skipped += 1
    return _finish_restore(restored, replaced, skipped, orphaned, snap_roots, meta["restart_scope"])


# ---- game-first restore (P3): one or more games' ticked asset groups across categories --------------

def _manifest_items_for_games(m: dict, games: list) -> list:
    """[(category, system, item_id)] the backup holds for the selected games + ticked asset keys. Reads
    each item's category + its extra.game ('<sys>:<stem>') / extra.asset (the group key) tags that
    plan_game_assets stamped. A ticked key with no backed-up item simply yields nothing (a silent skip)."""
    want: dict = {}
    for g in games:
        system = g.get("system")
        gid = g.get("id", "")
        stem = g.get("stem") or (gid.split(":", 1)[1] if ":" in gid else "")
        if not system or not stem:
            continue
        want[f"{system}:{stem}"] = set(g.get("keys") or [])
    out: list = []
    for cat in backup_manifest.categories(m):        # categories()/systems() return [{key,label,...}]
        ck = cat.get("key")
        for sysrow in backup_manifest.systems(m, ck):
            sk = sysrow.get("key")
            for item in backup_manifest.items(m, ck, sk):
                # item_game unifies both schemas: a game-first item keys off extra.game/asset; a whole-ROM
                # item (RUN FULL BACKUP / the pilot) has neither but its id IS '<sys>:<stem>' + asset "rom".
                gid, _sys, _stem, asset = backup_manifest.item_game(item, sk)
                keys = want.get(gid) if isinstance(gid, str) else None
                if keys is None:
                    continue
                if keys and asset not in keys:  # a ticked-keys filter; a whole-ROM item's asset is "rom"
                    continue
                out.append((ck, sk, item.get("id")))
    return out


def restore_game_assets(source: str, games: list, ts: str, emit, is_stopped) -> dict:
    """Game-first RESTORE: restore the ticked asset groups of one or more games from a backup FOLDER back
    to their live locations ACROSS categories (rom/saves/states/media). `games` = [{system, stem, keys}].
    Rule #5 per item (a per-category snapshot dir). Refuses (RuntimeError) if ANY involved category needs
    ES-DE closed while it is running. Returns the same summary shape as restore_selection."""
    m, source_dir, rom_root, _ = _open_source(source, "roms")   # meta is computed PER category below
    triples = _manifest_items_for_games(m, games)
    involved = {cat for cat, _, _ in triples}
    if any(category_meta(c)["needs_esde_stopped"] for c in involved) and proc_guard.esde_running():
        raise RuntimeError("close ES-DE before restoring these items")
    snap_roots: dict = {}
    restored = replaced = skipped = 0
    orphaned: list = []
    done_targets: set = set()
    restart = "none"
    for cat, system, item_id in triples:
        if is_stopped():
            raise Cancelled()
        try:
            p = _plan_restore_item(m, cat, {"system": system, "id": item_id}, source_dir, rom_root)
        except Exception:
            emit({"line": f"skip (corrupt item): {item_id}"})
            skipped += 1
            continue
        if not p["ok"]:
            emit({"line": f"skip ({p['reason']}): {p['name']}"})
            skipped += 1
            continue
        st = _restore_one(p, ts, snap_roots, done_targets, orphaned, emit, is_stopped)
        if st in ("restored", "replaced"):
            restored += 1
            if st == "replaced":
                replaced += 1
            emit({"item_done": p["id"], "restored": restored})
            restart = _stricter_restart(restart, category_meta(cat)["restart_scope"])
        elif st != "orphaned":   # orphaned was recorded by _restore_one; anything else is a skip
            skipped += 1
    return _finish_restore(restored, replaced, skipped, orphaned, snap_roots, restart)


def restore_preview_game_assets(source: str, games: list) -> dict:
    """READ-ONLY game-first preview: classify the selected games' backed-up assets into replace / fresh /
    skip so the UI can warn before overwriting. Same shape as restore_preview."""
    m, source_dir, rom_root, _ = _open_source(source, "roms")
    triples = _manifest_items_for_games(m, games)
    replace, fresh, skip = [], [], []
    seen: set = set()   # a live target SHARED by two selections (e.g. a flat RA save under two same-stem
    restart = "none"    # games) is restored once - count it once here too, matching restore's done_targets
    for cat, system, item_id in triples:
        try:
            p = _plan_restore_item(m, cat, {"system": system, "id": item_id}, source_dir, rom_root)
        except Exception:
            skip.append({"id": item_id, "name": item_id, "reason": "corrupt_item"})
            continue
        row = {"id": p["id"], "name": p["name"]}
        if not p["ok"]:
            skip.append({**row, "reason": p["reason"]})
            continue
        if p["target"] in seen:
            continue
        seen.add(p["target"])
        restart = _stricter_restart(restart, category_meta(cat)["restart_scope"])
        (replace if p["exists"] else fresh).append(row)
    return {"replace": replace, "fresh": fresh, "skip": skip, "restart_scope": restart}
