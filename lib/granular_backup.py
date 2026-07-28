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

from . import backup_debris, backup_manifest, es_collections, es_systems, game_files, proc_guard

GRANULAR_PREFIX = "deck-granular-"          # a granular backup folder: <dest>/deck-granular-<ts>/
SNAPSHOT_PREFIX = "_TMP-granular-restore-"  # rule-5 pre-restore snapshot dir (same filesystem as target)

# Per-category restore mechanics. `needs_esde_stopped`: restoring these touches files ES-DE rewrites on
# exit (rule #3), so ES-DE must be closed first. `restart_scope`: what the user must restart for the
# restore to take effect (none | esde | emulator). ROMs need neither. Unknown categories default to the
# SAFE side (require ES-DE closed) so a future category can never silently skip the guard.
# `delivery`: "inplace" = restore writes the live target directly (rule-5 snapshot first); "stage" = restore
# CANNOT write live (ES-DE rewrites the target on exit, rule #3), so it stages the files to a next-boot tree
# the launch wrapper applies before ES-DE starts. All existing categories are inplace; only esde stages.
_CATEGORY_META = {
    "roms": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
    # ES-DE never writes saves/states (the emulator does, on its own launch), so restoring them does not
    # need ES-DE closed; the emulator reads the restored file on its next launch (no restart_scope).
    "saves": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
    "states": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
    # media files are only READ by ES-DE (never rewritten on exit like gamelist.xml), so a restore is safe
    # while it runs, but ES-DE caches media - a restart is needed to SEE the restored art.
    "media": {"needs_esde_stopped": False, "restart_scope": "esde", "delivery": "inplace"},
    # BIOS files are read by the EMULATOR at launch, never written by ES-DE, so a restore is safe while
    # ES-DE runs; the emulator picks up the restored file on its next launch (no restart needed).
    "bios": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
    # ES-DE SETTINGS: ES-DE REWRITES es_settings.xml + gamelists on exit (rule #3), and MAD *is* the running
    # ES-DE - so a live restore would be clobbered on quit. Restore STAGES to next boot (delivery="stage");
    # the wrapper applies it before ES-DE starts. Never needs ES-DE stopped (staging is a copy-aside).
    "esde": {"needs_esde_stopped": False, "restart_scope": "esde", "delivery": "stage"},
    # EMULATOR CONFIG + DATA: read by the EMULATOR at ITS next launch (never rewritten by ES-DE), so a
    # restore is safe while ES-DE runs and delivery is inplace (the rule-5 snapshot protects the overwrite).
    # It does NOT need ES-DE stopped - instead restore_selection refuses if the SPECIFIC emulator being
    # restored is live (the first per-emulator guard; the emulator would clobber its config on exit).
    # restart_scope "emulator": the emulator picks the restored config up on its next launch.
    "emucfg": {"needs_esde_stopped": False, "restart_scope": "emulator", "delivery": "inplace"},
    # SYSTEM config (control-panel calibration, lightgun cal, samba/backup prefs, EmuDeck settings): read by
    # the control panel / helpers, never rewritten by ES-DE, so a LIVE restore is safe while ES-DE runs (the
    # rule-5 snapshot protects the overwrite) - inplace, no restart, no stop. Like emucfg it anchors at $HOME
    # but is bounded by a TIGHT EXACT allowlist (system_map), not a broad emulator-dir set.
    "system": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
    # Controller config: the emulator controller configs + controller-policy.local.toml. Anchors at $HOME,
    # bounded by controllers_map's allowlist (the live target paths). Restores live under rule-5.
    "controllers": {"needs_esde_stopped": False, "restart_scope": "none", "delivery": "inplace"},
}
_DEFAULT_META = {"needs_esde_stopped": True, "restart_scope": "esde", "delivery": "inplace"}


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
           "media": esde_settings.media_root, "bios": mad_paths.bios_root,
           # esde settings live under ~/ES-DE (esde_settings.APPDATA); LEXICAL containment like the others
           # (the front-door downloaded_media symlink is excluded from the esde category anyway).
           "esde": lambda: esde_settings.APPDATA,
           # emulator config+data spans MANY roots (.config, .local/share, .var/app, Emulation, .mame,
           # .supermodel, Applications), so it anchors at $HOME. $HOME is broad, so restore is additionally
           # bounded by emu_map's allowlist (in _plan_restore_item) - $HOME alone is NOT the safety boundary.
           "emucfg": lambda: str(Path.home()),
           # system config also anchors at $HOME (its items span control-panel@storage, Lightgun, tools, etc.)
           # and is bounded by system_map's TIGHT EXACT allowlist in _plan_restore_item.
           "system": lambda: str(Path.home()),
           # controller config also anchors at $HOME (the emulator controller configs + policy override) and
           # is bounded by controllers_map's allowlist (derived from the live target set) in _plan_restore_item.
           "controllers": lambda: str(Path.home())}
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

def _copy_path(src: str, dst: str, emit, is_stopped, skip_debris: bool = False) -> int:
    """Copy a file or a whole folder src -> dst, returning bytes copied. A folder is walked file by file
    so progress streams and cancellation can land BETWEEN files (never mid-file, so no torn file). Raises
    Cancelled if is_stopped() goes true between files.
    skip_debris (BACKUP callers only, NEVER restore): prune OS/VCS junk dirs + skip junk files so a backup
    stays clean; restore leaves it False so it reproduces a backup byte-faithfully."""
    src, dst = str(src), str(dst)
    if os.path.isdir(src):
        total = 0
        for root, dirs, files in os.walk(src):
            if skip_debris:
                dirs[:] = [d for d in dirs if not backup_debris.is_debris_dir(d)]
            rel = os.path.relpath(root, src)
            out = os.path.join(dst, rel) if rel != "." else dst
            os.makedirs(out, exist_ok=True)
            for name in files:
                if is_stopped():
                    raise Cancelled()
                if skip_debris and backup_debris.is_debris_file(name):
                    continue
                s = os.path.join(root, name)
                if os.path.islink(s) or os.path.isfile(s):
                    d = os.path.join(out, name)
                    shutil.copy2(s, d, follow_symlinks=False)
                    try:
                        total += os.path.getsize(d)
                    except OSError:
                        pass
        return total
    if skip_debris and backup_debris.is_debris_file(os.path.basename(src)):
        return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    try:
        return os.path.getsize(dst)
    except OSError:
        return 0


def _path_size(path: str, skip_debris: bool = False) -> int:
    """Byte size of a file or the recursive size of a folder (best-effort; unreadable parts count 0).
    skip_debris keeps a folder-kind manifest item's size honest with what the backup will actually copy
    (the enumerator/backup drop OS/VCS junk, so its size must not count it)."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path):
        if skip_debris:
            dirs[:] = [d for d in dirs if not backup_debris.is_debris_dir(d)]
        for name in files:
            if skip_debris and backup_debris.is_debris_file(name):
                continue
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
                kind=kind, size=_path_size(src, skip_debris=True), stem=stem, boxart=bool(art),
                extra={"art": art} if art else None))
        plan.append({"id": f"{system}:{stem}", "name": name, "system": system,
                     "stem": stem, "src": src, "rel": rel, "kind": kind})
    return manifest, plan


# Non-versioned buckets: their content is IMMUTABLE (ROMs / BIOS bytes never change), so a dated copy is
# pure clutter. They write a FIXED set dir (deck-granular-<bucket>) that MERGES on re-backup. Versioned
# buckets (esde, future saves) write a dated snapshot deck-granular-<bucket>-<ts>.
_FIXED_BUCKETS = {"games", "bios"}


def _backup_dir(dest_dir: str, bucket: str, ts: str, versioned: bool) -> Path:
    """The backup folder for a set: a FIXED deck-granular-<bucket> (non-versioned, merges) or a dated
    deck-granular-<bucket>-<ts> snapshot (versioned)."""
    name = f"{GRANULAR_PREFIX}{bucket}-{ts}" if versioned else f"{GRANULAR_PREFIX}{bucket}"
    return Path(dest_dir) / name


def _write_set_manifest(backupdir: Path, manifest: dict) -> None:
    """Persist a backup's manifest. A FIXED (non-versioned) set dir MERGES the current selection into any
    manifest already there (union by id/rel, keeping the set's original created) so re-backup accumulates;
    a dated snapshot writes fresh. Merge-vs-fresh is inferred from the dir name so every caller is uniform."""
    if backupdir.name in {GRANULAR_PREFIX + b for b in _FIXED_BUCKETS}:
        existing = backup_manifest.read(backupdir)
        if backup_manifest.validate(existing):
            manifest = backup_manifest.merge(existing, manifest)
    backup_manifest.write(manifest, backup_manifest.manifest_path(backupdir))


def backup_selection(items: list, dest_dir: str, category: str, category_label: str,
                     ts: str, emit, is_stopped) -> dict:
    """Back up the selected games (pilot: their ROM file/folder) into <dest_dir>/deck-granular-<ts>/ and
    write a mad-manifest.json. `items` = [{system, stem}]. Returns {path, copied, skipped}. A game whose
    ROM is absent (or is emulator data outside its ROM dir) is skipped (reported), never faked. Resolution +
    manifest-building are delegated to plan_selection so a local backup and a cloud upload select the exact
    same games from the exact same rules."""
    backupdir = _backup_dir(dest_dir, "games", ts, versioned=False)  # fixed set, merges on re-backup
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_selection(items, category, category_label, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
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


def _media_ticked(rel: str, keys) -> bool:
    """Whether a media file (backup rel 'media/<sys>/<subdir>/...') is selected by `keys` (the per-game
    ticked keys). The coarse 'media' key selects EVERY media file; a per-kind 'media.<kind>' key (P4 drill)
    selects only that kind. The kind is derived from the rel, so this works for pre-P4 backups too (their
    media items carry no per-kind tag). Shared by the backup plan + the restore/preview filter so both agree."""
    if "media" in keys:
        return True
    from . import es_gamelist
    kind = es_gamelist.media_kind_from_rel(rel)
    return bool(kind) and ("media." + kind) in keys


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
            if not grp["present"]:
                continue
            gkey = grp["key"]
            is_media = gkey == "media"
            if is_media:
                # media is DRILLABLE (P4): the coarse "media" key OR any per-kind "media.<kind>" selects it.
                if "media" not in keys and not any(k.startswith("media.") for k in keys):
                    continue
            elif gkey not in keys:
                continue
            cat = grp["category"]
            for f in grp["files"]:
                if is_media and not _media_ticked(f["rel"], keys):
                    continue  # a per-kind media selection: skip the media files not ticked
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


def backup_game_assets(games: list, dest_dir: str, ts: str, emit, is_stopped, versioned: bool = False) -> dict:
    """Back up a GAME-FIRST selection (each game's ticked asset groups) into a granular backup folder
    with a mad-manifest.json spanning every touched category. `games` = [{system, stem, keys:[...]}].
    Returns {path, copied, files}. Resolution + manifest-building delegate to plan_game_assets so backup
    copies exactly the planned files. A game/group with nothing present is reported and skipped.

    versioned: a normal per-game backup (False) accumulates into the merging fixed games set
    (deck-granular-games); a whole-system / all-systems 'All' snapshot (True) writes a DATED set
    (deck-granular-games-<ts>), a discrete restore point that never merges into the cherry-pick set."""
    backupdir = _backup_dir(dest_dir, "games", ts, versioned)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_game_assets(games, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up {entry['category']}: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


# ---- BIOS backup (P5): file-first, bucketed for display only ---------------

def plan_bios(items: list, ts: str, emit=None, is_stopped=None):
    """Resolve a BIOS selection to a (manifest, plan), WITHOUT copying. `items` = [{bucket, rel}] where
    rel = 'bios/<path relative to bios_root>' (the TRUE path) and bucket is the display grouping. Each src
    = bios_root/<path>; a rel that escapes bios_root or whose file is absent is skipped. Manifest items are
    category='bios', system=<bucket>, id=rel - so restore reuses restore_selection(category='bios')."""
    from . import mad_paths
    bios_root = os.path.realpath(str(mad_paths.bios_root()))
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    seen: set = set()
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        rel = it.get("rel")
        bucket = it.get("bucket") or "other"
        if not (isinstance(rel, str) and rel.startswith("bios/") and not os.path.isabs(rel)
                and not any(ord(c) < 0x20 for c in rel)
                and not any(p in ("", ".", "..") for p in rel.split("/"))):
            continue
        if rel in seen:
            continue
        src = os.path.normpath(os.path.join(bios_root, rel[len("bios/"):]))
        # LEXICAL containment (the rel is validated free of ../abs/control above, so src stays under
        # bios_root); do NOT realpath - a legit front-door symlink subdir (ryujinx/keys -> the emulator
        # store) resolves OUTSIDE bios_root and realpath-containment would wrongly reject it, like saves.
        if not (src == bios_root or src.startswith(bios_root + os.sep)) or not os.path.isfile(src):
            if emit is not None:
                emit({"line": f"skip (missing): {rel}"})
            continue
        seen.add(rel)
        name = os.path.basename(rel)
        backup_manifest.add_item(
            manifest, category="bios", category_label="BIOS", system=bucket, system_label=bucket,
            item=backup_manifest.make_item(id=rel, name=name, src=src, rel=rel, kind="file",
                                           size=_path_size(src, skip_debris=True)))
        plan.append({"id": rel, "name": name, "system": bucket, "src": src, "rel": rel, "kind": "file"})
    return manifest, plan


# ---- ES-DE settings backup (P6): grouped, file-first; restore is STAGED (delivery="stage") -----------

def plan_esde(items: list, ts: str, emit=None, is_stopped=None):
    """Resolve an ES-DE settings selection to a (manifest, plan), WITHOUT copying. `items` = [{group, rel}]
    where rel = 'esde/<path relative to ~/ES-DE>' (the TRUE path) and group is the display grouping
    (settings/input/custom_systems/collections/gamelists). Structurally identical to plan_bios; kept
    separate so the shipped BIOS path is untouched. Manifest items are category='esde', system=<group>,
    id=rel - so restore reuses restore_selection(category='esde') (which routes to the STAGED delivery)."""
    from . import esde_settings
    esde_root = os.path.realpath(str(esde_settings.APPDATA))
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    seen: set = set()
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        rel = it.get("rel")
        group = it.get("group") or it.get("bucket") or "other"
        if not (isinstance(rel, str) and rel.startswith("esde/") and not os.path.isabs(rel)
                and not any(ord(c) < 0x20 for c in rel)
                and not any(p in ("", ".", "..") for p in rel.split("/"))):
            continue
        if rel in seen:
            continue
        src = os.path.normpath(os.path.join(esde_root, rel[len("esde/"):]))
        # LEXICAL containment (the rel is validated free of ../abs/control above, so src stays under
        # esde_root); the excluded dirs (downloaded_media symlink etc.) never enter the plan anyway.
        if not (src == esde_root or src.startswith(esde_root + os.sep)) or not os.path.isfile(src):
            if emit is not None:
                emit({"line": f"skip (missing): {rel}"})
            continue
        seen.add(rel)
        name = it.get("name") or os.path.basename(rel)
        backup_manifest.add_item(
            manifest, category="esde", category_label="ES-DE settings", system=group, system_label=group,
            item=backup_manifest.make_item(id=rel, name=name, src=src, rel=rel, kind="file",
                                           size=_path_size(src, skip_debris=True)))
        plan.append({"id": rel, "name": name, "system": group, "src": src, "rel": rel, "kind": "file"})
    return manifest, plan


def backup_esde(items: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up the selected ES-DE settings files into <dest>/deck-granular-<ts>/esde/... + a mad-manifest.json.
    `items` = [{group, rel}]. Returns {path, copied, files}. Delegates resolution to plan_esde."""
    backupdir = _backup_dir(dest_dir, "esde", ts, versioned=True)  # dated snapshots (settings change)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_esde(items, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


def backup_bios(items: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up the selected BIOS files into <dest>/deck-granular-<ts>/bios/... + a mad-manifest.json.
    `items` = [{bucket, rel}]. Returns {path, copied, files}. Delegates resolution to plan_bios."""
    backupdir = _backup_dir(dest_dir, "bios", ts, versioned=False)  # fixed set, merges on re-backup
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_bios(items, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


# ---- Emulator config+data backup (P7): per-emulator, grouped; multi-root under $HOME ------------------

def plan_emucfg(items: list, ts: str, emit=None, is_stopped=None):
    """Resolve an emulator-config selection to a (manifest, plan), WITHOUT copying. `items` =
    [{emulator, group, rel}] where rel = 'emucfg/<path relative to $HOME, front-door side>' (the TRUE path),
    emulator is the tile (proc_guard-backed) and group is the display grouping. Manifest items are
    category='emucfg', system=<emulator>, extra={'group':<group>}, id=rel - so restore reuses
    restore_selection(category='emucfg') and a browse regroups by emulator then group. src = $HOME/<rel-after
    -prefix>; a rel outside the emulator dirs (emu_map allowlist), escaping $HOME, or whose file/folder is
    absent is skipped. An item may be a FILE or a whole FOLDER (kind derived from src)."""
    from . import emu_map
    home = os.path.normpath(str(Path.home()))
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    seen: set = set()
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        rel = it.get("rel")
        emulator = it.get("emulator") or it.get("system") or "other"
        group = it.get("group") or "other"
        # Same traversal checks as plan_esde PLUS the emu_map allowlist (rel must live in a known emu dir).
        if not (isinstance(rel, str) and rel.startswith("emucfg/") and not os.path.isabs(rel)
                and not any(ord(c) < 0x20 for c in rel)
                and not any(p in ("", ".", "..") for p in rel.split("/"))
                and emu_map.rel_allowed(rel)):
            continue
        if rel in seen:
            continue
        src = os.path.normpath(os.path.join(home, rel[len("emucfg/"):]))
        # LEXICAL containment under $HOME (the rel is validated free of ../abs/control above + inside the
        # allowlist). Accept a FILE or a FOLDER (huge opt-in groups are a single folder row).
        if not (src == home or src.startswith(home + os.sep)) or not os.path.exists(src):
            if emit is not None:
                emit({"line": f"skip (missing): {rel}"})
            continue
        seen.add(rel)
        name = it.get("name") or os.path.basename(rel.rstrip("/"))
        kind = "folder" if os.path.isdir(src) else "file"
        backup_manifest.add_item(
            manifest, category="emucfg", category_label="Emulator config & data",
            system=emulator, system_label=emu_map.label_for(emulator),
            item=backup_manifest.make_item(id=rel, name=name, src=src, rel=rel, kind=kind,
                                           size=_path_size(src, skip_debris=True), extra={"group": group}))
        plan.append({"id": rel, "name": name, "system": emulator, "src": src, "rel": rel, "kind": kind})
    return manifest, plan


def backup_emucfg(items: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up the selected emulator config/data into <dest>/deck-granular-emucfg-<ts>/... + a
    mad-manifest.json. `items` = [{emulator, group, rel}]. Returns {path, copied, files}. Dated snapshots
    (config changes over time). Delegates resolution to plan_emucfg."""
    backupdir = _backup_dir(dest_dir, "emucfg", ts, versioned=True)  # dated snapshots (config changes)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_emucfg(items, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


def plan_system(items: list, ts: str, emit=None, is_stopped=None):
    """Resolve a SYSTEM config selection to a (manifest, plan), WITHOUT copying. `items` = [{group, rel}]
    where rel = 'system/<path relative to $HOME, front-door side>' (the TRUE path) and group is the display
    grouping. Manifest items are category='system', system=<group>, id=rel - so restore reuses
    restore_selection(category='system'). Same shape as plan_emucfg but bounded by system_map's TIGHT EXACT
    allowlist (only the curated config files/dir, never all of $HOME or ~/Emulation/tools). src = $HOME/<rel
    -after-prefix>; a rel outside the allowlist, escaping $HOME, or whose file is absent is skipped."""
    from . import system_map
    home = os.path.normpath(str(Path.home()))
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    seen: set = set()
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        rel = it.get("rel")
        group = it.get("group") or it.get("system") or "other"
        if not (isinstance(rel, str) and rel.startswith("system/") and not os.path.isabs(rel)
                and not any(ord(c) < 0x20 for c in rel)
                and not any(p in ("", ".", "..") for p in rel.split("/"))
                and system_map.rel_allowed(rel)):
            continue
        if rel in seen:
            continue
        src = os.path.normpath(os.path.join(home, rel[len("system/"):]))
        if not (src == home or src.startswith(home + os.sep)) or not os.path.isfile(src):
            if emit is not None:
                emit({"line": f"skip (missing): {rel}"})
            continue
        seen.add(rel)
        name = it.get("name") or os.path.basename(rel)
        backup_manifest.add_item(
            manifest, category="system", category_label="System config",
            system=group, system_label=group,
            item=backup_manifest.make_item(id=rel, name=name, src=src, rel=rel, kind="file",
                                           size=_path_size(src, skip_debris=True), extra={"group": group}))
        plan.append({"id": rel, "name": name, "system": group, "src": src, "rel": rel, "kind": "file"})
    return manifest, plan


def backup_system(items: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up the selected SYSTEM config into <dest>/deck-granular-system-<ts>/... + a mad-manifest.json.
    `items` = [{group, rel}]. Returns {path, copied, files}. Dated snapshots (config changes over time)."""
    backupdir = _backup_dir(dest_dir, "system", ts, versioned=True)  # dated snapshots
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_system(items, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
    else:
        try:
            backupdir.rmdir()
        except OSError:
            pass
    return {"path": str(backupdir), "copied": copied, "files": len(plan)}


def plan_controllers(items: list, ts: str, emit=None, is_stopped=None):
    """Resolve a CONTROLLER config selection to a (manifest, plan), WITHOUT copying. `items` = [{group, rel}]
    where rel = 'controllers/<path relative to $HOME, front-door side>'. Manifest items are
    category='controllers', system=<group>, id=rel - so restore reuses restore_selection(category=
    'controllers'). Bounded by controllers_map's allowlist (the live controller target paths). A target is a
    FILE or a DIR (e.g. Cemu controllerProfiles); a rel outside the allowlist, escaping $HOME, or whose src is
    absent is skipped."""
    from . import controllers_map
    home = os.path.normpath(str(Path.home()))
    manifest = backup_manifest.new_manifest("granular", created=ts)
    plan: list = []
    seen: set = set()
    for it in items:
        if is_stopped is not None and is_stopped():
            raise Cancelled()
        rel = it.get("rel")
        group = it.get("group") or it.get("system") or "other"
        if not (isinstance(rel, str) and rel.startswith("controllers/") and not os.path.isabs(rel)
                and not any(ord(c) < 0x20 for c in rel)
                and not any(p in ("", ".", "..") for p in rel.split("/"))
                and controllers_map.rel_allowed(rel)):
            continue
        if rel in seen:
            continue
        src = os.path.normpath(os.path.join(home, rel[len("controllers/"):]))
        if not (src == home or src.startswith(home + os.sep)):
            continue
        isdir = os.path.isdir(src)
        if not (isdir or os.path.isfile(src)):
            if emit is not None:
                emit({"line": f"skip (missing): {rel}"})
            continue
        seen.add(rel)
        name = it.get("name") or os.path.basename(rel)
        kind = "folder" if isdir else "file"
        backup_manifest.add_item(
            manifest, category="controllers", category_label="Controller config",
            system=group, system_label=group,
            item=backup_manifest.make_item(id=rel, name=name, src=src, rel=rel, kind=kind,
                                           size=_path_size(src, skip_debris=True), extra={"group": group}))
        plan.append({"id": rel, "name": name, "system": group, "src": src, "rel": rel, "kind": kind})
    return manifest, plan


def backup_controllers(items: list, dest_dir: str, ts: str, emit, is_stopped) -> dict:
    """Back up the selected CONTROLLER config into <dest>/deck-granular-controllers-<ts>/... + a
    mad-manifest.json. `items` = [{group, rel}]. Dated snapshots. Returns {path, copied, files}."""
    backupdir = _backup_dir(dest_dir, "controllers", ts, versioned=True)
    backupdir.mkdir(parents=True, exist_ok=True)
    manifest, plan = plan_controllers(items, ts, emit, is_stopped)
    copied = 0
    for entry in plan:
        if is_stopped():
            raise Cancelled()
        emit({"line": f"backing up: {entry['name']}"})
        _copy_path(entry["src"], str(backupdir / entry["rel"]), emit, is_stopped, skip_debris=True)
        copied += 1
        emit({"item_done": entry["id"], "copied": copied})
    if copied:
        _write_set_manifest(backupdir, manifest)
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
        # saves/states/media/esde/emucfg: LEXICAL containment under the category root. DON'T realpath the
        # target - a front-door symlink inside the root (e.g. retroarch/saves -> flatpak, Cemu saves ->
        # mlc01) resolves OUTSIDE the root and realpath-containment would falsely reject it; the
        # ''/'.'/'..'/abs rejections above already prove the joined path stays under the root, and
        # _copy_path follows the symlink when it writes.
        rel_rem = rel[len(prefix):]
        root_s = os.path.normpath(str(root))
        target = os.path.normpath(os.path.join(root_s, rel_rem))
        if target != root_s and not target.startswith(root_s + os.sep):
            return {"ok": False, "reason": "target_escapes_root", "id": item_id, "name": name}
        snap_rel = rel   # unique per file (category/.../name); the snapshot subpath name only
        root_used = root_s
        if category == "emucfg":
            # emucfg anchors at $HOME (its files span many roots), which is BROAD - a forged manifest rel
            # like "emucfg/.ssh/id_rsa" is a valid $HOME path. Bound the restore to the emulator dirs
            # emu_map knows (allowlist derived from the emulator table, so it can't drift).
            from . import emu_map
            if not emu_map.rel_allowed(rel):
                return {"ok": False, "reason": "outside_emu_roots", "id": item_id, "name": name}
            # rule-5's snapshot dir is derived from root_used. $HOME's PARENT (/home) is not user-writable,
            # so anchoring the snapshot at $HOME would fail mkdir on every REPLACE and silently skip it
            # (defeating rule #5 exactly when it matters). Anchor the snapshot at the target's OWN directory
            # instead: it exists on a REPLACE, is writable, and is on the same filesystem as the target.
            root_used = os.path.dirname(target)
        elif category == "system":
            # system also anchors at $HOME (broad) - bound the restore to the EXACT config files system_map
            # knows (control-panel dir + the specific files), never all of ~/Emulation/tools or $HOME. Same
            # $HOME-parent-unwritable snapshot fix as emucfg: anchor the snapshot at the target's own dir.
            from . import system_map
            if not system_map.rel_allowed(rel):
                return {"ok": False, "reason": "outside_system_roots", "id": item_id, "name": name}
            root_used = os.path.dirname(target)
        elif category == "controllers":
            # controllers anchors at $HOME (broad) - bound the restore to the EXACT live controller targets
            # controllers_map knows (the emulator configs + policy override), never elsewhere. Same
            # $HOME-parent-unwritable snapshot fix: anchor the rule-5 snapshot at the target's own dir.
            from . import controllers_map
            if not controllers_map.rel_allowed(rel):
                return {"ok": False, "reason": "outside_controller_roots", "id": item_id, "name": name}
            root_used = os.path.dirname(target)
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
            "restart_scope": meta["restart_scope"], "deferred": meta.get("delivery") == "stage"}


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
            "restart_scope": meta["restart_scope"], "deferred": meta.get("delivery") == "stage"}


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


# ---- STAGED restore (P6, delivery="stage"): never write live; apply at next ES-DE start (rule #3) -----
# ES-DE rewrites es_settings.xml + gamelists on exit, and MAD *is* the running ES-DE, so a live restore is
# clobbered on quit. Instead we copy the backed-up files into a $HOME-mirrored _staged-apply tree and arm
# the single-shot marker ~/.config/deck-cloud/pending-restore-apply; the EXISTING apply-staged-restore.sh
# (run by the ES-DE launch wrapper BEFORE ES-DE starts, and by the RESTART re-exec) lays them onto live
# $HOME with its OWN rule-5 move-aside. This reuses the shipped cloud-restore staging machinery verbatim.

def _pending_marker_path() -> Path:
    """The single-shot next-boot marker apply-staged-restore.sh reads (same path deck-cloud.sh writes)."""
    state = Path(os.environ.get("DECK_CLOUD_STATE_DIR") or (Path.home() / ".config" / "deck-cloud"))
    return state / "pending-restore-apply"


def _staged_apply_root(ts: str) -> Path:
    """The $HOME-mirrored staged tree apply-staged-restore.sh applies at next boot. If a tree is ALREADY
    armed (the marker points at a live _staged-apply dir), reuse it so two restores before a reboot
    ACCUMULATE rather than clobber the single marker; else make a fresh same-fs _TMP tree."""
    marker = _pending_marker_path()
    try:
        first = marker.read_text().splitlines()[0].strip() if marker.exists() else ""
    except (OSError, IndexError):
        first = ""
    if first and first.endswith("_staged-apply") and os.path.isdir(first):
        return Path(first)
    root = Path.home() / "Downloads" / "_TMP" / f"mad-esde-restore-{ts}" / "_staged-apply"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _arm_marker(staged_root: Path) -> None:
    marker = _pending_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(staged_root) + "\n")


def _wrapper_has_apply_hook() -> bool:
    """Is the ES-DE launch wrapper wired to run apply-staged-restore.sh at boot? A staged restore only
    applies if it is. Reads ONLY a small text wrapper (skips the ~125MB real AppImage). True when unknown
    so it never nags without cause."""
    try:
        w = Path.home() / "Applications" / "ES-DE.AppImage"
        if not w.is_file() or w.stat().st_size > 1_000_000:
            return True
        return "apply-staged-restore.sh" in w.read_text(errors="replace")
    except OSError:
        return True


def _restore_staged(m: dict, items: list, category: str, source_dir: Path, rom_root, ts: str,
                    emit, is_stopped, meta: dict) -> dict:
    """STAGED delivery (rule #3): copy each backed-up file into the $HOME-mirrored _staged-apply tree and arm
    the marker; the launch wrapper applies it (with ITS OWN rule-5) at the next ES-DE start / RESTART.
    NOTHING under the live target is touched now. Same _plan_restore_item validation as an in-place restore
    (unsafe/foreign items rejected). Returns the usual summary + deferred:True + staged:<root>."""
    staged_root = _staged_apply_root(ts)
    home = str(Path.home())
    staged = replaced = skipped = 0
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
        # Mirror the LOGICAL live target under the staged root, keyed relative to $HOME, so the applier
        # writes it back to exactly $HOME/<rel>. p["target"] is the lexical ~/ES-DE/<path> (not realpath'd),
        # which is what the applier lays down. Any target outside $HOME can't be staged -> skip safely.
        home_rel = os.path.relpath(p["target"], home)
        if home_rel.startswith(".."):
            emit({"line": f"skip (outside home): {p['name']}"})
            skipped += 1
            continue
        dst = os.path.join(str(staged_root), home_rel)
        if p["exists"]:
            replaced += 1
        emit({"line": f"staging: {p['name']}"})
        _copy_path(str(p["backup_file"]), dst, emit, is_stopped)
        staged += 1
        emit({"item_done": p["id"], "restored": staged})
    if staged:
        _arm_marker(staged_root)
        if not _wrapper_has_apply_hook():
            emit({"line": "WARNING: the ES-DE launch wrapper is missing the staged-restore hook - run "
                          "deck-post-update.sh --wrapper so these settings apply on restart."})
    return {"restored": staged, "replaced": replaced, "skipped": skipped, "orphaned": [],
            "snapshot": None, "snapshots": [], "restart_scope": meta["restart_scope"],
            "deferred": True, "staged": str(staged_root) if staged else None}


def restore_selection(source: str, items: list, category: str, ts: str, emit, is_stopped) -> dict:
    """Restore the selected items from a granular backup FOLDER back to the live library (SINGLE category).
    Rule #5: any existing target is snapshotted aside FIRST. `items` = [{system, id|stem}]. Returns
    {restored, replaced, skipped, orphaned, snapshot, snapshots, restart_scope}. A STAGED category
    (delivery="stage", e.g. esde) instead stages to next boot (never writes live, rule #3). Raises
    ValueError on an invalid/foreign manifest and RuntimeError when the category needs ES-DE closed but it
    is running."""
    m, source_dir, rom_root, meta = _open_source(source, category)
    if meta["needs_esde_stopped"] and proc_guard.esde_running():
        raise RuntimeError("close ES-DE before restoring this category")
    if category == "emucfg":
        # The FIRST per-emulator guard: restoring an emulator's config/saves while THAT emulator is live
        # would be clobbered when it exits (same rule #3 rationale as ES-DE). Refuse per emulator in the set.
        from . import emu_map
        for sysrow in backup_manifest.systems(m, "emucfg"):
            be = emu_map.backend_for(sysrow.get("key"))
            if proc_guard.emulator_running(be):
                raise RuntimeError(f"close {sysrow.get('label') or sysrow.get('key')} "
                                   "before restoring its config")
    if meta.get("delivery") == "stage":
        return _restore_staged(m, items, category, source_dir, rom_root, ts, emit, is_stopped, meta)
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
                if keys:  # a ticked-keys filter; a whole-ROM item's asset is "rom"
                    if asset == "media":
                        # media is per-kind selectable (P4): the kind comes from the item's rel, so "media"
                        # (all) OR the exact "media.<kind>" key keeps it; anything else drops it.
                        if not _media_ticked(item.get("rel") or "", keys):
                            continue
                    elif asset not in keys:
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


def _preview_game_assets(m: dict, source_dir, rom_root, games: list, check_backup_file: bool) -> dict:
    """Shared game-first preview: classify the selected games' backed-up assets into replace / fresh / skip.
    `check_backup_file` is True for a LOCAL source (files on disk) and False for a CLOUD source (files on
    MEGA - classify from the manifest + live target alone). A live target shared by two selections is
    counted once (matching restore's done_targets)."""
    triples = _manifest_items_for_games(m, games)
    replace, fresh, skip = [], [], []
    seen: set = set()
    restart = "none"
    for cat, system, item_id in triples:
        try:
            p = _plan_restore_item(m, cat, {"system": system, "id": item_id}, source_dir, rom_root,
                                   check_backup_file=check_backup_file)
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


def restore_preview_game_assets(source: str, games: list) -> dict:
    """READ-ONLY game-first preview over a LOCAL backup folder: classify into replace / fresh / skip so the
    UI can warn before overwriting. Same shape as restore_preview."""
    m, source_dir, rom_root, _ = _open_source(source, "roms")
    return _preview_game_assets(m, source_dir, rom_root, games, check_backup_file=True)


def restore_preview_game_assets_manifest(m: dict, games: list) -> dict:
    """READ-ONLY CLOUD game-first preview: classify from a MANIFEST DICT (the backup files are on MEGA), so
    the UI can warn before a cloud restore downloads + overwrites. Same shape as restore_preview_game_assets."""
    if not backup_manifest.validate(m):
        raise ValueError("no valid backup manifest for this cloud backup")
    return _preview_game_assets(m, Path("/__cloud__"), es_collections.rom_root(), games,
                                check_backup_file=False)
