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

from .. import (backup_manifest, bios_map, emu_map, es_gamelist, es_systems, esde_map, game_files,
                granular_backup)
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
    {"key": "bios", "label": "BIOS", "live": True},
    {"key": "esde", "label": "ES-DE settings", "live": True},
    {"key": "emucfg", "label": "Emulator config & data", "live": True},
    {"key": "system", "label": "System", "live": True},
    {"key": "controllers", "label": "Controller config", "live": True},
]
_CATEGORY_KEYS = {c["key"] for c in CATEGORIES}

LIVE_SOURCE = "live"

# Sizing budget for one granular.game_assets live resolve: safely under the panel's
# 12 s call timeout, so a huge wine prefix yields a partial size + "still counting"
# note instead of "Couldn't read this game".
_ASSET_SIZING_BUDGET_S = 9.0


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
    # Valve Steam: the NON-STEAM shortcut games only (dynamic - each launcher's own rungameid decodes
    # to a shortcut appid; Steam-proper titles reinstall from Steam and stay out). steam is in
    # TOOL_SYSTEMS so _game_systems() above never yields it - this tile is its only entry, and
    # scope=all (_games_for_scope -> _game_systems) stays steam-free by the same fact.
    try:
        from .. import steam_shortcuts
        ns_count = len(steam_shortcuts.nonsteam_games())
    except Exception:
        ns_count = 0
    if ns_count:
        rows.append({"key": "steam", "label": es_systems.short_name("steam"),
                     "art": console_art("steam"), "count": ns_count})
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def _live_steam_items() -> list:
    """Per-game rows for the Valve Steam tile: ONLY the non-Steam shortcuts. has_rom=False here means a
    DEAD shortcut (the launcher .sh survives but Steam no longer has the shortcut) - the browser's
    existing red missing treatment; its surviving prefix stays rescuable from the asset page (backup
    mode opens assets regardless of `present`). Size = the launcher .sh only: browsing must stay cheap,
    the multi-GB prefix sizes show on the per-game asset page."""
    from .. import steam_shortcuts
    rows = []
    for stem, g in sorted(steam_shortcuts.nonsteam_games().items(),
                          key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        try:
            size = os.path.getsize(g["sh"])
        except OSError:
            size = 0
        rows.append({"id": f"steam:{stem}", "stem": stem, "name": g.get("name") or stem,
                     "art": game_files.cover_path("steam", stem),
                     "has_rom": bool(g.get("alive")), "kind": "file", "size": size})
    return rows


def _live_roms_items(system: str) -> list:
    """Per-game rows for one live ROM system: {id,stem,name,art,has_rom,kind,size}. `has_rom` is false
    when the gamelist entry's ROM is absent on disk (stale/region-swapped) -> the browser greys it."""
    if system == "steam":
        return _live_steam_items()
    rows = []
    for stem_lower, rec in sorted(es_gamelist.visible_records(system).items(),
                                  key=lambda kv: kv[1].get("name", kv[0]).lower()):
        stem = rec.get("stem") or stem_lower
        paths = game_files.resolve_rom(system, stem)
        has_rom = bool(paths)
        kind = "folder" if (has_rom and os.path.isdir(paths[0])) else "file"
        # Size for the browse list, kept CHEAP so a huge system can't time the RPC out:
        #  - launcher/manifest/index ROM (few games) -> expand to the real payload (openbor/mugen/namco/
        #    segacd/cannonball show their true game size instead of the pointer byte-count);
        #  - a plain single-file ROM -> just its own size (no re-resolve, no walk);
        #  - a folder-per-game ROM (ps3/daphne/lindbergh) -> deferred to 0 here (os.walk-ing every game
        #    would re-introduce the browse timeout); its true size still shows on the per-game asset page.
        size = 0
        if has_rom and kind == "file":
            try:
                if os.path.splitext(paths[0])[1].lower() in game_files.LAUNCHER_EXTS:
                    size = sum(granular_backup._path_size(it["path"])
                               for it in game_files.resolve_rom_files(system, stem))
                else:
                    size = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
            except OSError:
                size = 0
        rows.append({"id": f"{system}:{stem}", "stem": stem,
                     "name": rec.get("name") or stem,
                     "art": game_files.cover_path(system, stem),   # covers-only: fast for huge systems
                     "has_rom": has_rom, "kind": kind, "size": size})
    return rows


_LIVE_SYSTEMS = {"roms": _live_roms_systems}
_LIVE_ITEMS = {"roms": _live_roms_items}


# The FIXED asset allowlist a whole-system / all-systems "All" backup expands to. New per-game asset kinds
# (P13: textures / console-save / cheats) stay PER-GAME by design and are DELIBERATELY excluded here, so an
# "All" never silently balloons once those land - it always means ROM + saves + states + media, nothing more.
# lutriscfg rides along: a tiny per-game YAML, and the ONE asset a fresh-Deck recovery
# needs first (it names the wine prefix). Non-steam systems simply have no such group.
_ALL_ASSET_KEYS = ("rom", "media", "saves", "states", "lutriscfg")


def _games_for_scope(scope: str, system: str | None) -> list:
    """Expand an "All" backup to its game list: every live game (system, stem), each ticked with the fixed
    asset allowlist _ALL_ASSET_KEYS. scope='system' -> the one system; scope='all' -> every game system
    (alphabetical). Feeds plan_game_assets/backup_game_assets, which drop the absent groups per game, so this
    is a single resolve pass with no double-enumeration. A game whose ROM is missing is NOT dropped here
    (it is logged + skipped by the planner) so a bulk backup never silently truncates."""
    if scope == "system" and system == "steam":
        # The Valve Steam tile's "All": the NON-STEAM games only (matching the tile's count, never all
        # 80+ launchers). With _ALL_ASSET_KEYS this means launcher + saves + media exactly - the heavy
        # prefix/gamedir keys are per-game only by design (_STEAM_HEAVY_KEYS), so an "All" here cannot
        # balloon into 25 GB of prefixes.
        from .. import steam_shortcuts
        return [{"system": "steam", "stem": stem, "keys": list(_ALL_ASSET_KEYS)}
                for stem in sorted(steam_shortcuts.nonsteam_games())]
    systems = [system] if scope == "system" else _game_systems()
    games = []
    for s in systems:
        if not s:
            continue
        for stem_lower, rec in es_gamelist.visible_records(s).items():
            games.append({"system": s, "stem": rec.get("stem") or stem_lower,
                          "keys": list(_ALL_ASSET_KEYS)})
    if scope != "system":
        # scope=all promises EVERY game on this Deck (that is what the confirm says, and
        # the Valve Steam tile sits in the same grid with its own count), so the non-Steam
        # shortcut games join it with the same fixed allowlist: launcher + media + saves.
        # The heavy prefix/gamedir keys stay per-game, so an "All" still cannot balloon.
        try:
            from .. import steam_shortcuts
            games.extend({"system": "steam", "stem": stem, "keys": list(_ALL_ASSET_KEYS)}
                         for stem in sorted(steam_shortcuts.nonsteam_games()))
        except Exception:
            pass   # a Steam-side hiccup must never truncate an all-systems backup
    return games


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


def _scan_backup_sources(roots: list, category: str = "roms") -> list:
    """Every local backup under `roots` (DIRECT CHILDREN only) that carries a readable, valid manifest,
    newest first. A backup is a mirror FOLDER (mad-manifest.json inside) or an archive with a
    <archive>.mad-manifest.json sidecar. Shared by granular.sources (remembered+default dest) and
    granular.sources_under (a user-browsed folder). `category` gates + counts: "roms" counts distinct GAMES
    and skips a game-less backup (the game-first list); any other category counts that category's ITEMS and
    lists a backup that HAS them (so a BIOS-only / ES-DE-settings-only local backup is now findable)."""
    out = []
    for root in roots:
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
            # normalize `created` to a string: sources_under scans ARBITRARY, untrusted folders (SD
            # card, USB, manifests copied from another host), where a foreign/hand-edited manifest may
            # carry a non-string created (int/null). A mixed-type sort would TypeError and make the whole
            # folder un-browsable - matching backup_manifest's _as_int/_dict corrupt-tolerance, coerce here.
            # Count for the chosen category: roms -> distinct GAMES (a game-less backup is skipped, since the
            # game-first browser would land on an empty list); any other category -> that category's item
            # count (a backup that has NONE of it is skipped, so only relevant backups are offered).
            if category == "roms":
                count = len(_manifest_games(m))
            else:
                count = sum(s["n_items"] for s in backup_manifest.systems(m, category))
            if count == 0:
                continue
            out.append({"id": str(p), "kind": "local", "label": p.name,
                        "created": str(m.get("updated") or m.get("created") or ""), "count": count})
    out.sort(key=lambda s: s.get("created", ""), reverse=True)
    return out


def _local_backup_sources(category: str = "roms") -> list:
    """Every local backup in the remembered + default dest that carries a valid manifest, newest first."""
    return _scan_backup_sources(_local_backup_roots(), category)


@method("granular.categories")
def _granular_categories(params):
    """The category entry points for the Backup & Restore hub."""
    return {"categories": [{"key": c["key"], "label": c["label"]} for c in CATEGORIES]}


@method("granular.sources")
def _granular_sources(params):
    """LOCAL browse sources (fast, no network): the live library (backup selection) + local backups with a
    manifest (restore selection). Cloud restore sources come from granular.cloud_sources (slow)."""
    category = (params or {}).get("category") or "roms"
    sources = [{"id": LIVE_SOURCE, "kind": "live",
                "label": "Current library (to back up)", "created": ""}]
    sources.extend(_local_backup_sources(category))
    return {"sources": sources}


@method("granular.sources_under", slow=True)
def _granular_sources_under(params):
    """The LOCAL RESTORE SOURCE BROWSER: scan ONE user-chosen folder (from the C++ folder picker) for
    local backups that carry a manifest, using the SAME detection as granular.sources (direct children
    only, backup_manifest's 64MB read cap guards a stray large file). slow=True: an arbitrary folder may
    hold many entries, so it runs off the stdin dispatch thread. Returns {path, sources:[...]}; the C++
    merges these into its local-source list (de-duped by id = the backup's absolute path)."""
    raw = (params or {}).get("path", "")
    if not isinstance(raw, str) or not raw.strip():
        raise RpcError("EINVAL", "a folder path is required")
    category = (params or {}).get("category") or "roms"
    root = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(root):
        raise RpcError("EINVAL", "not a folder: " + raw)
    return {"path": root, "sources": _scan_backup_sources([Path(root)], category)}


# ---- cloud restore sources / manifest (per-game restore FROM MEGA) ----------

def _safe_ts(ts: str) -> bool:
    """A granular timestamp token YYYYmmddTHHMMSS - safe to interpolate into a remote path + a shell arg."""
    return bool(ts) and len(ts) == 15 and ts[8] == "T" and ts[:8].isdigit() and ts[9:].isdigit()


def _safe_settoken(t: str) -> bool:
    """A cloud SET token safe to interpolate into a remote path + shell arg: either a 15-char timestamp
    (versioned esde snapshots) or one of the FIXED non-versioned set names. The fixed names are a compile-time
    allowlist of exact literals (never user input), so shell-injection safety is preserved."""
    return _safe_ts(t) or t in ("games", "bios")


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


# ---- Manage backups: list every deletable set + PERMANENT LOCAL delete ----------------------
_MANAGE_CAT_LABEL = {"games": "Games", "bios": "BIOS", "esde": "ES-DE settings",
                     "emucfg": "Emulator config", "system": "System", "controllers": "Controller config"}
# Never rmtree one of these even if a backup DEST somehow resolves onto it (belt-and-suspenders; the
# manifest-validation gate below is the real protector - a primary dir carries no mad-manifest.json).
_MANAGE_PROTECTED_DIRS = ("~", "~/ROMs", "~/OpenBor", "~/Emulation", "~/ES-DE",
                          "~/.config", "~/.local", "~/.var", "~/Downloads")


def _manage_category(m: dict) -> str:
    """Classify a backup manifest into ONE Manage-backups category key. A game-first backup carries
    roms/media/saves/states -> 'games'; the single-category backups map by their own category. Falls back to
    'games' for an unrecognized manifest (still listable + deletable)."""
    keys = {c["key"] for c in backup_manifest.categories(m)}
    if keys & {"roms", "media", "saves", "states"}:
        return "games"
    for k in ("bios", "esde", "emucfg", "system", "controllers"):
        if k in keys:
            return k
    return "games"


def _manage_count(m: dict, cat: str) -> int:
    if cat == "games":
        return len(_manifest_games(m))
    return sum(c["n_items"] for c in backup_manifest.categories(m) if c["key"] == cat)


def _manage_size(m: dict) -> int:
    """Total bytes the backup holds, summed from the manifest across all its categories."""
    return sum(int(c.get("size", 0) or 0) for c in backup_manifest.categories(m))


@method("manage.list", slow=True)
def _manage_list(params):
    """Every deletable BACKUP SET for the Manage backups page, local + cloud, across the 5 granular categories.
    LOCAL: each validated backup folder/archive under the backup roots (direct children), once, classified by
    category, with a manifest-summed size. CLOUD: each set in each category's SEPARATE remote base (list-<cat>).
    slow=True (scans the dest folders + up to 5 network list calls). Returns {connected, local:[...], cloud:[...]};
    a local row's id = its absolute path, a cloud row carries {category, token} for cloud.delete_set."""
    local = []
    for root in _local_backup_roots():
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name.endswith("." + backup_manifest.MANIFEST_NAME):
                continue
            m = backup_manifest.read(p)
            if not backup_manifest.validate(m):
                continue
            cat = _manage_category(m)
            local.append({"scope": "local", "category": cat, "label": _MANAGE_CAT_LABEL[cat],
                          "id": str(p), "name": p.name,
                          "created": str(m.get("updated") or m.get("created") or ""),
                          "count": _manage_count(m, cat), "size": _manage_size(m)})
    local.sort(key=lambda s: s.get("created", ""), reverse=True)
    cloud = []
    connected = True
    for cat in ("games", "bios", "esde", "emucfg", "system", "controllers"):
        rc, out = _cloud_run(["list-" + cat], timeout=180)
        if rc != 0:
            connected = False
            continue
        for line in out.splitlines():
            if "\t" not in line:
                continue
            parts = line.split("\t")
            ts = parts[0].strip()
            if not _safe_settoken(ts):
                continue
            count = parts[1].strip() if len(parts) > 1 else ""
            created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
            cloud.append({"scope": "cloud", "category": cat, "label": _MANAGE_CAT_LABEL[cat],
                          "id": f"cloud:{ts}", "token": ts, "name": ts, "created": created,
                          "count": int(count) if count.isdigit() else 0, "size": 0})
    cloud.sort(key=lambda s: (s.get("category", ""), s.get("created", "")), reverse=True)
    return {"connected": connected, "local": local, "cloud": cloud}


@method("manage.delete_local", slow=True)
def _manage_delete_local(params):
    """PERMANENTLY delete ONE local backup (Manage backups). params {path}. A deliberate user override of rule
    #5 for a BACKUP copy - primary data is never touched. GUARDS: the realpath must be a DIRECT CHILD of a known
    local backup root, must NOT be a root (or a protected primary dir) itself, and must CURRENTLY carry a valid
    granular manifest (a folder with mad-manifest.json, or an archive with a sidecar) - so only a real backup is
    removed. Takes _GRAN_ACTIVE so it can't race a running backup/restore. slow=True (rmtree may take a bit)."""
    import shutil
    raw = (params or {}).get("path", "")
    if not isinstance(raw, str) or not raw.strip():
        raise RpcError("EINVAL", "a backup path is required")
    target = os.path.realpath(os.path.expanduser(raw))
    roots = [str(r) for r in _local_backup_roots()]
    if os.path.dirname(target) not in roots:
        raise RpcError("EINVAL", "not inside a backup folder")
    protected = {os.path.realpath(os.path.expanduser(d)) for d in _MANAGE_PROTECTED_DIRS}
    if target in roots or target in protected:
        raise RpcError("EINVAL", "refusing to delete a system folder")
    if not backup_manifest.validate(backup_manifest.read(Path(target))):
        raise RpcError("EINVAL", "not a recognizable backup (no manifest)")
    if not _GRAN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a backup or restore is in progress")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:                                    # an archive backup: the file + its sidecar manifest
            os.remove(target)
            side = target + "." + backup_manifest.MANIFEST_NAME
            if os.path.exists(side):
                os.remove(side)
    finally:
        _GRAN_ACTIVE.release()
    return {"deleted": True, "path": target}


@method("manage.delete_all", slow=True)
def _manage_delete_all(params):
    """Bulk PERMANENT delete: every backup set of ONE category, or ALL categories (category "all" or omitted).
    Enumerates via manage.list and removes each set through the SAME guarded single-delete paths (local:
    containment + manifest gate + _GRAN_ACTIVE; cloud: token validate + marker clear + purge), so a bulk
    delete is exactly N guarded single deletes - a per-set failure is counted, never fatal. Returns
    {deleted, failed, errors}. slow=True. A deliberate user override of rule #5 for BACKUP copies - primary
    data is never touched."""
    from . import cloud_cmds
    cat = (params or {}).get("category") or "all"
    if cat != "all" and cat not in _MANAGE_CAT_LABEL:
        raise RpcError("EINVAL", f"unknown backup category: {cat!r}")
    listing = _manage_list({})
    # a category's cloud enumeration may have failed (network) -> its sets are absent from listing["cloud"],
    # so a bulk delete that reports "success" could leave those behind. Surface it so the UI can caveat.
    connected = bool(listing.get("connected", True))
    deleted = failed = 0
    errors = []
    # A per-set failure must be COUNTED, never fatal (the bulk contract): catch OSError too (rmtree/remove on a
    # busy/permission-locked file on a Samba/exFAT/USB dest) alongside the guard RpcErrors, else it escapes as
    # EINTERNAL after some sets are already gone.
    for row in listing["local"]:
        if cat != "all" and row["category"] != cat:
            continue
        try:
            _manage_delete_local({"path": row["id"]})
            deleted += 1
        except (RpcError, OSError) as e:
            failed += 1
            errors.append(f"{row['name']}: {e}")
    for row in listing["cloud"]:
        if cat != "all" and row["category"] != cat:
            continue
        try:
            cloud_cmds._cloud_delete_set({"category": row["category"], "token": row["token"]})
            deleted += 1
        except (RpcError, OSError) as e:
            failed += 1
            errors.append(f"{row['category']}/{row['token']}: {e}")
    return {"deleted": deleted, "failed": failed, "connected": connected, "errors": errors[:20]}


def _cloud_manifest(ts: str, bios: bool = False, esde: bool = False, emucfg: bool = False,
                    system: bool = False, controllers: bool = False) -> dict:
    """The granular manifest of a cloud backup set (deck-cloud.sh cat-manifest / cat-bios-manifest /
    cat-esde-manifest / cat-emucfg-manifest / cat-system-manifest / cat-controllers-manifest). Each category
    lives in a SEPARATE remote base, so the flags select which base's manifest to read. Raises RpcError."""
    if not _safe_settoken(ts):
        raise RpcError("EINVAL", f"bad cloud backup id: {ts!r}")
    cmd = ("cat-controllers-manifest" if controllers else "cat-system-manifest" if system
           else "cat-emucfg-manifest" if emucfg
           else "cat-esde-manifest" if esde else "cat-bios-manifest" if bios else "cat-manifest")
    rc, out = _cloud_run([cmd, ts], timeout=60)
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
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        # 3rd field = the manifest's updated/created date (a fixed "games"/"bios" set shows a real date, not
        # its token); fall back to the token for a legacy set that predates the 3-field emit.
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


@method("bios.cloud_sources", slow=True)
def _bios_cloud_sources(params):
    """Cloud BIOS-backup sets available to restore FROM MEGA (deck-cloud.sh list-bios - a SEPARATE remote
    base from the per-game sets, so the two restore lists never cross). slow=True: shells out + hits the
    network. {connected, sources:[{id:"cloud:<ts>", kind:"cloud", created:<ts>, count}]} where count = the
    BIOS FILE count (a BIOS set has no game tags, so list-bios falls back to the item count)."""
    rc, out = _cloud_run(["list-bios"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        # 3rd field = the manifest's updated/created date (a fixed "games"/"bios" set shows a real date, not
        # its token); fall back to the token for a legacy set that predates the 3-field emit.
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


@method("esde.cloud_sources", slow=True)
def _esde_cloud_sources(params):
    """Cloud ES-DE-settings-backup sets available to restore FROM MEGA (deck-cloud.sh list-esde - a SEPARATE
    remote base from games/BIOS, so the restore lists never cross). {connected, sources:[{id:"cloud:<ts>",
    kind:"cloud", created:<ts>, count}]} where count = the ES-DE settings FILE count."""
    rc, out = _cloud_run(["list-esde"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        # 3rd field = the manifest's updated/created date (a fixed "games"/"bios" set shows a real date, not
        # its token); fall back to the token for a legacy set that predates the 3-field emit.
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


@method("emucfg.cloud_sources", slow=True)
def _emucfg_cloud_sources(params):
    """Cloud emulator-config sets available to restore FROM MEGA (deck-cloud.sh list-emucfg - a SEPARATE
    remote base from games/BIOS/ES-DE, so the restore lists never cross). {connected, sources:[{id:"cloud:
    <ts>", kind:"cloud", created:<ts>, count}]} where count = the emulator-config FILE count."""
    rc, out = _cloud_run(["list-emucfg"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
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


def _manifest_games(m: dict) -> dict:
    """{game_id: {system, stem, name, art}} across ALL categories - one row per distinct game. Unifies both
    granular schemas (backup_manifest.item_game), so a game whose ONLY backed-up asset is a save/state/media
    still appears, and a whole-ROM backup lists its games. `art` prefers a roms item's boxart when present."""
    games: dict = {}
    for cat in backup_manifest.categories(m):
        ck = cat["key"]
        for sysrow in backup_manifest.systems(m, ck):
            sk = sysrow["key"]
            for it in backup_manifest.items(m, ck, sk):
                gid, gsys, stem, _asset = backup_manifest.item_game(it, sk)
                if gid is None:
                    continue
                g = games.get(gid)
                if g is None:
                    g = {"system": gsys, "stem": stem, "name": it.get("name") or stem, "art": ""}
                    games[gid] = g
                if not g["art"] and it.get("art"):
                    g["art"] = it.get("art")
    return games


def _manifest_games_browse(m: dict, source: str, category: str, system: str | None) -> dict:
    """GAME-FIRST browse over a manifest (LOCAL folder OR cloud set): a UNIFIED game view (union of EVERY
    category via _manifest_games) so a game whose only backed-up asset is a save/state/media is reachable -
    not just games that have a ROM - AND a whole-ROM backup still lists its games. Systems tiles count
    distinct games; a system's rows are one per game {id,stem,name,art}. Shared by local + cloud restore."""
    games = _manifest_games(m)
    if system is None:
        counts: dict = {}
        for g in games.values():
            counts[g["system"]] = counts.get(g["system"], 0) + 1
        rows = [{"key": sk, "label": es_systems.short_name(sk), "art": console_art(sk), "count": n}
                for sk, n in counts.items()]
        rows.sort(key=lambda r: r["label"].lower())
        return {"level": "systems", "source": source, "category": category, "systems": rows}
    rows = [{"id": gid, "stem": g["stem"], "name": g["name"], "art": g["art"]}
            for gid, g in games.items() if g["system"] == system]
    rows.sort(key=lambda r: (r["name"] or r["stem"]).lower())
    return {"level": "items", "source": source, "category": category, "system": system, "items": rows}


def _browse_manifest(source: str, category: str, system: str | None) -> dict:
    m = backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return _manifest_games_browse(m, source, category, system)


def _browse_cloud(source: str, category: str, system: str | None) -> dict:
    """Browse a cloud game-backup set via its manifest (deck-cloud.sh cat-manifest) - no ROM download to
    browse; the SAME unified game-first view as a local backup, so a save/media-only game is reachable."""
    m = _cloud_manifest(_cloud_source_ts(source))
    return _manifest_games_browse(m, source, category, system)


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


# ---- game-first per-game assets (P2) ---------------------------------------

_ASSET_LABELS = {"rom": "ROM", "media": "Media", "saves": "Save", "states": "Save state",
                 "cheats": "Cheats", "settings": "Game settings", "textures": "Textures",
                 "console-save": "Console save", "shader": "Shader cache", "mods": "Mods & DLC",
                 "graphicpacks": "Graphic packs",
                 # steam (non-Steam shortcuts) - the live side labels come from
                 # game_files._steam_game_assets; these cover the manifest-side regroup.
                 "prefix": "Proton prefix", "gamedir": "Game folder", "note": "Note",
                 "prefix-proton": "Old Proton prefix", "lutriscfg": "Lutris config"}


def _mint(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _manifest_game_assets(m: dict, game_id: str) -> list:
    """Group a backup manifest's items for ONE game (item extra.game == '<system>:<stem>') by asset kind,
    for the game-first restore tick list. Returns [{key,label,category,present,size,count}], preserving
    first-seen order. Empty for an invalid manifest or a game with no items."""
    if not backup_manifest.validate(m):
        return []
    buckets: dict = {}
    order: list = []
    for cat in backup_manifest.categories(m):
        ckey = cat["key"]
        for sysrow in backup_manifest.systems(m, ckey):
            sk = sysrow["key"]
            for it in backup_manifest.items(m, ckey, sk):
                # item_game unifies both schemas: a game-first item's game/asset tags, OR a whole-ROM item
                # (no tags) whose id is '<sys>:<stem>' surfaced as the "rom" asset.
                gid, _sys, _stem, akey = backup_manifest.item_game(it, sk)
                if gid != game_id:
                    continue
                b = buckets.get(akey)
                if b is None:
                    label = _ASSET_LABELS.get(akey, akey)
                    if game_id.startswith("steam:"):
                        # steam reuses the generic keys with different MEANINGS: "rom" is
                        # the launcher .sh, "saves" are the Proton-prefix save folders.
                        if akey == "rom":
                            label = "Launcher"
                        elif akey == "saves":
                            label = "Game saves (Proton prefix)"
                    b = {"key": akey, "label": label, "category": ckey,
                         "present": True, "size": 0, "count": 0}
                    buckets[akey] = b
                    order.append(akey)
                b["size"] += _mint(it.get("size"))
                b["count"] += 1
    return [buckets[k] for k in order]


@method("steam.lutris_status")
def _steam_lutris_status(params):
    """Whether Lutris itself is present - the restore flow offers a one-tap install when
    a Lutris game's data is being restored onto a Deck without it."""
    from .. import lutris_games
    return {"installed": lutris_games.installed()}


@method("steam.install_lutris")
def _steam_install_lutris(params):
    """Install Lutris (flatpak, --user so it survives SteamOS updates) as a DETACHED
    registered job - it shows in the Transfers tile and streams like any transfer.
    Only ever called from the restore flow's explicit confirm (never silent)."""
    from . import cloud_cmds
    from .. import job_registry
    # In-flight guard: a second INSTALL press (or a re-fired restore preview) must
    # attach to the RUNNING install, never spawn a second concurrent flatpak install.
    for j in job_registry.live_jobs():
        if j.get("kind") == "flatpak-lutris":
            s = cloud_cmds._JobTailStream(j["id"], clears_marker=False)
            return {"stream": s.start(), "job": j["id"], "already": True}
    job_id, proc = cloud_cmds._spawn_registered(
        ["flatpak", "install", "--user", "-y", "--noninteractive",
         "flathub", "net.lutris.Lutris"],
        kind="flatpak-lutris", source="panel", self_registering=False)
    s = cloud_cmds._JobTailStream(job_id, proc=proc, clears_marker=False)
    return {"stream": s.start(), "job": job_id}


@method("granular.game_assets", slow=True)
def _granular_game_assets(params):
    """The tickable per-game ASSET groups for game-first backup/restore. params: {source, system, game}
    (game = the stem, or an id '<system>:<stem>'). source="live" -> the assets the game has on disk;
    a backup source (a local path or 'cloud:<ts>') -> the assets that backup holds for this game (grouped
    from its manifest). Returns {system, game, assets:[{key,label,category,present,size,count}]}. slow=True:
    a live resolve globs saves/states/media; a cloud source cats its manifest over the network."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    system = p.get("system")
    game = p.get("game") or ""
    stem = game.split(":", 1)[1] if ":" in game else game
    if not system or not stem:
        raise RpcError("EINVAL", "system and game are required")
    if source == LIVE_SOURCE:
        groups = game_files.resolve_game_assets(
            system, stem, deadline=time.monotonic() + _ASSET_SIZING_BUDGET_S)
        assets = [{"key": g["key"], "label": g["label"], "category": g["category"],
                   "present": g["present"], "size": g["size"], "count": len(g["files"]),
                   "detail": g.get("detail", ""),
                   "size_partial": g.get("size_partial", False)}
                  for g in groups]
        return {"system": system, "game": stem, "assets": assets}
    # (a steam game's heavy groups - full prefix + game folder - are sized here too; see
    # game_files._steam_game_assets, whose sizes come from the shared _path_size walk)
    try:
        m = _cloud_manifest(_cloud_source_ts(source)) if source.startswith("cloud:") \
            else backup_manifest.read(source)
    except ValueError as exc:
        raise RpcError("ENOENT", str(exc))
    return {"system": system, "game": stem, "assets": _manifest_game_assets(m, f"{system}:{stem}")}


def _manifest_game_media_kinds(m: dict, game_id: str) -> list:
    """The media KINDS a backup holds for ONE game (the P4 drill over a backup/cloud source): walk that
    game's media items and group them by the kind derived from each item's rel. Returns
    [{key,label,present,size,count}] in the stable media_kinds() order (only kinds actually present)."""
    if not backup_manifest.validate(m):
        return []
    agg: dict = {}   # kind -> [size, count]
    for cat in backup_manifest.categories(m):
        ckey = cat["key"]
        for sysrow in backup_manifest.systems(m, ckey):
            sk = sysrow["key"]
            for it in backup_manifest.items(m, ckey, sk):
                gid, _sys, _stem, akey = backup_manifest.item_game(it, sk)
                if gid != game_id or akey != "media":
                    continue
                kind = es_gamelist.media_kind_from_rel(it.get("rel") or "")
                if not kind:
                    continue
                a = agg.setdefault(kind, [0, 0])
                a[0] += _mint(it.get("size"))
                a[1] += 1
    return [{"key": k, "label": es_gamelist.media_kind_label(k), "present": True,
             "size": agg[k][0], "count": agg[k][1]}
            for k in es_gamelist.media_kinds() if k in agg]


@method("granular.game_media", slow=True)
def _granular_game_media(params):
    """The tickable MEDIA KINDS under a game's coarse 'media' asset (P4 per-kind DRILL). params:
    {source, system, game}. source='live' -> the media kinds the game has on disk; a backup source (a local
    path or 'cloud:<ts>') -> the kinds that backup holds. Returns {system, game,
    kinds:[{key,label,present,size,count}]}. The UI ticks these into per-kind 'media.<kind>' backup keys."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    system = p.get("system")
    game = p.get("game") or ""
    stem = game.split(":", 1)[1] if ":" in game else game
    if not system or not stem:
        raise RpcError("EINVAL", "system and game are required")
    if source == LIVE_SOURCE:
        return {"system": system, "game": stem,
                "kinds": game_files.resolve_media_kinds(system, stem)}
    try:
        m = _cloud_manifest(_cloud_source_ts(source)) if source.startswith("cloud:") \
            else backup_manifest.read(source)
    except ValueError as exc:
        raise RpcError("ENOENT", str(exc))
    return {"system": system, "game": stem,
            "kinds": _manifest_game_media_kinds(m, f"{system}:{stem}")}


# ---- backup + restore STREAMS (the write paths) ----------------------------

class _GranularStream(Stream):
    """Runs one granular engine call (backup or restore) on the stream thread, forwarding its progress
    via emit and its cancel signal via is_stopped. Always emits a terminal {done} (rc 0 ok, -1 cancel/
    error) so the page can clear its 'working...' state, and releases _GRAN_ACTIVE in finally.

    Registers in the transfer-job registry as detached:false (it runs IN this daemon - a
    panel close still ends it, honestly reported by the next reap) so the Transfers tile
    sees granular ops like every other transfer; human lines mirror to the job's .out."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn                      # fn(emit, is_stopped) -> summary dict

    def run(self):
        from .. import job_registry
        job_id, out = None, None
        try:
            job_id = job_registry.begin("granular", os.getpid(), detached=False,
                                        source="panel", title="Copying files",
                                        token=self.token)
        except Exception:
            job_id = None                  # registry trouble must never block the op
        try:
            if job_id is not None:
                out = open(job_registry.out_path(job_id), "ab")
        except OSError:
            out = None                     # keep the REGISTERED job (it is end()ed below)

        def emit(ev):
            if out is not None and isinstance(ev, dict) and ev.get("line"):
                try:
                    out.write((str(ev["line"]) + "\n").encode("utf-8", "replace"))
                    out.flush()
                except (OSError, ValueError):
                    pass
            self.emit(ev)

        rc = -1
        try:
            summary = self._fn(emit, lambda: self.stopped.is_set())
            rc = 0
            emit({"done": True, "rc": 0, **summary})
        except granular_backup.Cancelled:
            emit({"done": True, "rc": -1, "stopped": True})
        except Exception as exc:           # ValueError (bad manifest) / RuntimeError (ES-DE up) / OSError
            emit({"done": True, "rc": -1, "error": str(exc)})
        finally:
            if job_id is not None:
                try:
                    job_registry.end(job_id, rc)
                except Exception:
                    pass
            if out is not None:
                out.close()
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


@method("granular.backup_assets")
def _granular_backup_assets(params):
    """GAME-FIRST backup: each game's ticked asset groups (ROM/save/state/media/...) across categories,
    into ONE granular backup folder + a multi-category manifest. params: {items:[{system, stem, keys:[
    asset-group-key,...]}], dest?}. dest defaults to the remembered backup destination. Streams progress;
    {done, path, copied, files} at the end."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no games selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_game_assets(items, dest, ts, emit, stopped))


@method("granular.backup_all", slow=True)
def _granular_backup_all(params):
    """Whole-system / all-systems "All" backup: back up EVERY game's ROM + saves + states + media (the fixed
    _ALL_ASSET_KEYS allowlist) into ONE granular set. params {scope:'system'|'all', system?, dest?}. Streams;
    {done, path, copied, files}. Writes a DATED set (deck-granular-games-<ts>) - a discrete bulk-snapshot
    restore point, NOT the merging fixed games set, so a "back up everything" is never confused with the
    cherry-pick set. A ROM-missing / nothing-present game is logged + skipped (never silently dropped)."""
    from . import backup_cmds
    p = params or {}
    scope = p.get("scope")
    if scope not in ("system", "all"):
        raise RpcError("EINVAL", "scope must be 'system' or 'all'")
    system = p.get("system")
    if scope == "system" and not system:
        raise RpcError("EINVAL", "system is required for scope 'system'")
    games = _games_for_scope(scope, system)
    if not games:
        raise RpcError("EINVAL", "no games to back up")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_game_assets(
            games, dest, ts, emit, stopped, versioned=True))


# ---- category "All" enumerators + backup (BIOS / emulator config) ------------
# "All" for a file-first category = every item the live view holds. Emulator-config "All" is EVERYTHING
# (every group of every emulator, INCLUDING the giant texture/mod/NAND/HDD folder rows) - the size WARNING
# lives in the C++ confirm (granular.backup_all_size), the backend just backs up whatever it enumerates.

def _all_bios_items() -> list:
    """[{bucket, rel}] for EVERY bios file across every bucket (the shape granular.backup_bios/plan_bios take)."""
    return [{"bucket": b["key"], "rel": f["rel"]}
            for b in bios_map.list_buckets() for f in b["files"]]


def _all_emucfg_items() -> list:
    """[{emulator, group, rel}] for EVERY group of every emulator - ALL groups incl. the giant folder rows
    (the shape granular.backup_emucfg/plan_emucfg take)."""
    return [{"emulator": e["key"], "group": g["key"], "rel": f["rel"]}
            for e in emu_map.list_emulators()
            for g in emu_map.list_files(e["key"]) for f in g["files"]]


@method("granular.backup_all_size", slow=True)
def _granular_backup_all_size(params):
    """Total bytes + file count an "All" backup of a file-first category would copy - so the C++ confirm can
    warn HONESTLY before pulling data (emucfg totals include the giant texture/mod dirs, which the tile fetch
    deliberately omits for speed). params {category:'bios'|'emucfg'}. slow=True: emucfg walks the big folders
    (bounded + cached in emu_map)."""
    p = params or {}
    category = p.get("category")
    if category == "bios":
        items = [(b, f) for b in bios_map.list_buckets() for f in b["files"]]
        total = sum(f.get("size", 0) for _b, f in items)
        return {"size": total, "count": len(items)}
    if category == "emucfg":
        total = 0
        count = 0
        for e in emu_map.list_emulators():
            for g in emu_map.list_files(e["key"]):
                for f in g["files"]:
                    total += f.get("size", 0)
                    count += 1
        return {"size": total, "count": count}
    raise RpcError("EINVAL", f"no 'All' size for category: {category!r}")


@method("granular.backup_bios_all", slow=True)
def _granular_backup_bios_all(params):
    """Back up EVERY bios file (all buckets) into the fixed bios set. params {dest?}. Streams {done, path,
    copied, files}. slow=True: it enumerates the whole bios tree before the stream starts."""
    from . import backup_cmds
    p = params or {}
    items = _all_bios_items()
    if not items:
        raise RpcError("EINVAL", "no BIOS files to back up")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_bios(items, dest, ts, emit, stopped))


@method("granular.backup_emucfg_all", slow=True)
def _granular_backup_emucfg_all(params):
    """Back up EVERY emulator's config/data - ALL groups incl. the giant texture/mod/NAND/HDD folders. params
    {dest?}. Streams {done, path, copied, files}. slow=True: it walks every emulator dir before the stream."""
    from . import backup_cmds
    p = params or {}
    items = _all_emucfg_items()
    if not items:
        raise RpcError("EINVAL", "no emulator config to back up")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_emucfg(items, dest, ts, emit, stopped))


# ---- BIOS (P5): per-system bucket tiles -> files; restore reuses granular.restore(category="bios") ----

def _bios_art(key: str) -> str:
    """The console.png a BIOS bucket tile shows (reusing the theme's per-system art via an art-key remap),
    or "" so the C++ falls back to the generic BIOS icon."""
    return console_art(bios_map.art_key(key)) or ""


def _bios_live_buckets() -> list:
    """LIVE per-system BIOS buckets (bios_map): [{key,label,count,size,art}] (files fetched separately)."""
    return [{"key": b["key"], "label": b["label"], "count": b["count"], "size": b["size"],
             "art": _bios_art(b["key"])}
            for b in bios_map.list_buckets()]


@method("bios.systems", slow=True)
def _bios_systems(params):
    """The per-system BIOS bucket TILES. params {source}. source="live" -> bios_map buckets; a backup source
    -> the buckets that backup holds (manifest 'bios' systems). Returns {source, systems:[{key,label,count,
    size}]}. slow=True: the live scan walks the whole bios tree; a cloud/manifest source may cat over net."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "systems": _bios_live_buckets()}
    m = _cloud_manifest(_cloud_source_ts(source), bios=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    rows = [{"key": s["key"], "label": bios_map.label_for_key(s["key"]), "count": s["n_items"],
             "size": s["size"], "art": _bios_art(s["key"])} for s in backup_manifest.systems(m, "bios")]
    rows.sort(key=bios_map.order_key)  # A->Z by label, "Other" last (same as the live view)
    return {"source": source, "systems": rows}


@method("bios.files", slow=True)
def _bios_files(params):
    """The BIOS files in ONE bucket, tickable. params {source, bucket}. Returns {source, bucket, files:[
    {rel, name, size}]}. rel = 'bios/<path>' (the true restore key). Live -> bios_map; a backup -> manifest."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    bucket = p.get("bucket")
    if not bucket:
        raise RpcError("EINVAL", "bucket is required")
    if source == LIVE_SOURCE:
        b = next((x for x in bios_map.list_buckets() if x["key"] == bucket), None)
        files = [{"rel": f["rel"], "name": os.path.basename(f["rel"]), "size": f["size"]}
                 for f in (b["files"] if b else [])]
        return {"source": source, "bucket": bucket, "files": files}
    m = _cloud_manifest(_cloud_source_ts(source), bios=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    files = [{"rel": it.get("id"), "name": it.get("name") or os.path.basename(it.get("id") or ""),
              "size": it.get("size", 0)} for it in backup_manifest.items(m, "bios", bucket)]
    files.sort(key=lambda f: (f["name"] or "").lower())  # A->Z within the bucket (like the live view)
    return {"source": source, "bucket": bucket, "files": files}


@method("granular.backup_bios")
def _granular_backup_bios(params):
    """Back up the selected BIOS files. params {items:[{bucket, rel}], dest?}. dest defaults to the
    remembered destination. Streams; {done, path, copied, files}."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no BIOS files selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_bios(items, dest, ts, emit, stopped))


# ---- ES-DE settings (P6): 5 tickable GROUPS (+ per-system gamelist drill); restore is STAGED -----------
# Restore reuses granular.restore(category="esde"): esde needs_esde_stopped=False so the RPC never EBUSYs,
# and granular_backup.restore_selection routes the "esde" category to its STAGED delivery (next-boot apply).

def _esde_manifest_groups(m: dict) -> list:
    """The GROUPS a backup source holds: [{key,label,explain,present,count,size,files:[{rel,name,size}]}]
    from the manifest's 'esde' systems (system=<group>), in the curated display order + with nice labels."""
    rows = []
    for s in backup_manifest.systems(m, "esde"):
        gk = s["key"]
        info = esde_map.GROUP_INFO.get(gk, {"label": gk, "explain": "", "order": 99})
        files = [{"rel": it.get("id"), "name": it.get("name") or os.path.basename(it.get("id") or ""),
                  "size": it.get("size", 0)} for it in backup_manifest.items(m, "esde", gk)]
        rows.append({"key": gk, "label": info["label"], "explain": info["explain"],
                     "present": bool(files), "count": len(files),
                     "size": sum(f["size"] for f in files), "files": files, "order": info["order"]})
    rows.sort(key=lambda r: r["order"])
    for r in rows:
        r.pop("order", None)
    return rows


def _esde_source_manifest(source: str) -> dict:
    """Read the manifest of an esde backup source (a local folder, or 'cloud:<ts>' from the esde MEGA base)."""
    m = _cloud_manifest(_cloud_source_ts(source), esde=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return m


@method("esde.groups", slow=True)
def _esde_groups(params):
    """The tickable ES-DE settings GROUPS. params {source}. source="live" -> esde_map.list_groups (scans
    ~/ES-DE); a backup source (a local path or 'cloud:<ts>') -> the groups that backup holds. Returns
    {source, groups:[{key,label,explain,present,count,size,files:[{rel,name,size}]}]}."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "groups": esde_map.list_groups()}
    return {"source": source, "groups": _esde_manifest_groups(_esde_source_manifest(source))}


@method("esde.gamelist_systems", slow=True)
def _esde_gamelist_systems(params):
    """The per-system drill for the "Game favorites & metadata" group. params {source}. Returns
    {source, systems:[{system,label,rel,size}]}. Live -> esde_map; a backup -> the gamelists group's items."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "systems": esde_map.list_gamelist_systems()}
    m = _esde_source_manifest(source)
    rows = []
    for it in backup_manifest.items(m, "esde", "gamelists"):
        rel = it.get("id") or ""
        parts = rel.split("/")           # esde/gamelists/<system>/gamelist.xml
        sysk = parts[2] if len(parts) >= 4 and parts[1] == "gamelists" else (it.get("name") or rel)
        rows.append({"system": sysk, "label": es_systems.short_name(sysk), "rel": rel,
                     "size": it.get("size", 0)})
    rows.sort(key=lambda r: (r["label"] or r["system"]).lower())
    return {"source": source, "systems": rows}


@method("granular.backup_esde")
def _granular_backup_esde(params):
    """Back up the selected ES-DE settings files. params {items:[{group, rel}], dest?}. dest defaults to the
    remembered destination. Streams; {done, path, copied, files}."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no ES-DE settings selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_esde(items, dest, ts, emit, stopped))


# ---- System config (P12a): tickable GROUPS of irreplaceable system config; LIVE restore + rule-5 ------
# Restore reuses granular.restore(category="system"): system needs_esde_stopped=False (never EBUSYs) and
# restore_selection writes live under the TIGHT system_map allowlist (a rule-5 snapshot protects the overwrite).

def _system_manifest_groups(m: dict) -> list:
    """The GROUPS a backup source holds: [{key,label,explain,present,count,size,files:[{rel,name,size}]}]
    from the manifest's 'system' systems (system=<group>), in the curated display order + with nice labels."""
    from .. import system_map
    rows = []
    for s in backup_manifest.systems(m, "system"):
        gk = s["key"]
        info = system_map.GROUP_INFO.get(gk, {"label": gk, "explain": "", "order": 99})
        files = [{"rel": it.get("id"), "name": it.get("name") or os.path.basename(it.get("id") or ""),
                  "size": it.get("size", 0)} for it in backup_manifest.items(m, "system", gk)]
        rows.append({"key": gk, "label": info["label"], "explain": info["explain"],
                     "present": bool(files), "count": len(files),
                     "size": sum(f["size"] for f in files), "files": files, "order": info["order"]})
    rows.sort(key=lambda r: r["order"])
    for r in rows:
        r.pop("order", None)
    return rows


def _system_source_manifest(source: str) -> dict:
    """Read the manifest of a system backup source (a local folder, or 'cloud:<ts>' from the system MEGA base)."""
    m = _cloud_manifest(_cloud_source_ts(source), system=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return m


@method("system.groups", slow=True)
def _system_groups(params):
    """The tickable SYSTEM config GROUPS. params {source}. source="live" -> system_map.list_groups (scans the
    curated config paths); a backup source (a local path or 'cloud:<ts>') -> the groups that backup holds.
    Returns {source, groups:[{key,label,explain,present,count,size,files:[{rel,name,size}]}]}."""
    from .. import system_map
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "groups": system_map.list_groups()}
    return {"source": source, "groups": _system_manifest_groups(_system_source_manifest(source))}


@method("granular.backup_system")
def _granular_backup_system(params):
    """Back up the selected SYSTEM config files. params {items:[{group, rel}], dest?}. dest defaults to the
    remembered destination. Streams; {done, path, copied, files}."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no system config selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_system(items, dest, ts, emit, stopped))


@method("system.cloud_sources", slow=True)
def _system_cloud_sources(params):
    """Cloud SYSTEM-config backup sets available to restore FROM MEGA (deck-cloud.sh list-system - a SEPARATE
    remote base, so the restore lists never cross). slow=True (network). {connected, sources:[{id:"cloud:
    <ts>", kind:"cloud", created:<ts>, count}]} where count = the system-config FILE count."""
    rc, out = _cloud_run(["list-system"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


# ---- Controller config (P12b Build B): tickable GROUPS; restore reuses granular.restore(category=------
# "controllers"). Same shape/pattern as System; controllers needs_esde_stopped=False (never EBUSYs), restore
# writes live under controllers_map's allowlist with a rule-5 snapshot. Overlaps emucfg/System by design.

def _controllers_manifest_groups(m: dict) -> list:
    """Regroup a controller-config backup manifest into the display GROUPS (via controllers_map.GROUP_INFO),
    so a stored backup browses the same as the live view without touching disk."""
    from .. import controllers_map
    groups: dict = {}
    for s in backup_manifest.systems(m, "controllers"):
        gk = s["key"]
        info = controllers_map.GROUP_INFO.get(gk, {"label": gk, "explain": "", "order": 99})
        files = [{"rel": it.get("id"), "name": it.get("name"), "size": it.get("size", 0)}
                 for it in backup_manifest.items(m, "controllers", gk)]
        groups[gk] = {"key": gk, "label": info["label"], "explain": info["explain"], "order": info["order"],
                      "present": bool(files), "count": len(files),
                      "size": sum(f["size"] for f in files), "files": files}
    return [groups[k] for k in sorted(groups, key=lambda k: groups[k]["order"])]


def _controllers_source_manifest(source: str) -> dict:
    """Read the manifest of a controllers backup source (a local folder, or 'cloud:<ts>' from the MEGA base)."""
    m = _cloud_manifest(_cloud_source_ts(source), controllers=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return m


@method("controllers.groups", slow=True)
def _controllers_groups(params):
    """The tickable CONTROLLER config GROUPS. params {source}. source="live" -> controllers_map.list_groups; a
    backup source (a local path or 'cloud:<ts>') -> the groups that backup holds. Returns {source, groups}."""
    from .. import controllers_map
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "groups": controllers_map.list_groups()}
    return {"source": source, "groups": _controllers_manifest_groups(_controllers_source_manifest(source))}


@method("granular.backup_controllers")
def _granular_backup_controllers(params):
    """Back up the selected CONTROLLER config. params {items:[{group, rel}], dest?}. dest defaults to the
    remembered destination. Streams; {done, path, copied, files}."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no controller config selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_controllers(items, dest, ts, emit, stopped))


@method("controllers.cloud_sources", slow=True)
def _controllers_cloud_sources(params):
    """Cloud CONTROLLER-config backup sets available to restore FROM MEGA (deck-cloud.sh list-controllers - a
    SEPARATE remote base). slow=True (network). {connected, sources:[{id:"cloud:<ts>", kind:"cloud", created,
    count}]}."""
    rc, out = _cloud_run(["list-controllers"], timeout=180)
    if rc != 0:
        return {"connected": False, "sources": []}
    sources = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        ts = parts[0].strip()
        count = parts[1].strip() if len(parts) > 1 else ""
        created = parts[2].strip() if len(parts) > 2 and parts[2].strip() else ts
        if not _safe_settoken(ts):
            continue
        sources.append({"id": f"cloud:{ts}", "kind": "cloud", "created": created,
                        "count": int(count) if count.isdigit() else 0})
    return {"connected": True, "sources": sources}


# ---- Emulator config+data (P7): per-EMULATOR tiles -> tickable GROUPS; restore reuses -----------------
# granular.restore(category="emucfg"): emucfg needs_esde_stopped=False, but restore_selection refuses per
# emulator if THAT emulator is live (the first per-emulator guard); _granular_restore mirrors it (clean EBUSY).

def _emu_art(art_key: str) -> str:
    """The icon an emulator tile shows: a representative system's console.png where the art_key IS an ES-DE
    system; else a router-config icon named <art_key>.png (e.g. RetroArch is not a console but has
    retroarch.png); else "" so the C++ falls back to the generic emulator-config icon."""
    if not art_key:
        return ""
    art = console_art(art_key)
    if art:
        return art
    from .systems_cmds import resolve_art
    return resolve_art([f"icons/{art_key}.png", f"{art_key}.png"]) or ""


def _emu_live_tiles() -> list:
    """LIVE per-emulator TILES (emu_map): [{key,label,count,size,art}] for present emulators (files fetched
    separately; count/size cover the default-on groups only so the tile screen stays fast)."""
    return [{"key": e["key"], "label": e["label"], "count": e["count"], "size": e["size"],
             "art": _emu_art(e.get("art_key", ""))}
            for e in emu_map.list_emulators()]


def _emu_manifest_tiles(m: dict) -> list:
    """The emulator TILES a backup source holds: manifest 'emucfg' systems (system=<emulator>)."""
    rows = [{"key": s["key"], "label": emu_map.label_for(s["key"]), "count": s["n_items"],
             "size": s["size"], "art": _emu_art(emu_map.art_key_for(s["key"]))}
            for s in backup_manifest.systems(m, "emucfg")]
    rows.sort(key=lambda r: r["label"].lower())
    return rows


@method("emucfg.systems", slow=True)
def _emucfg_systems(params):
    """The per-EMULATOR TILES. params {source}. source="live" -> emu_map present emulators; a backup source
    -> the emulators that backup holds. Returns {source, systems:[{key,label,count,size,art}]}. slow=True:
    the live scan stats the emulators' config/save dirs; a cloud source cats its manifest over the net."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    if source == LIVE_SOURCE:
        return {"source": source, "systems": _emu_live_tiles()}
    m = _cloud_manifest(_cloud_source_ts(source), emucfg=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return {"source": source, "systems": _emu_manifest_tiles(m)}


def _emu_manifest_groups(m: dict, emulator: str) -> list:
    """ONE emulator's tickable GROUPS from a backup source: regroup its 'emucfg' items by extra.group
    (folded to item['group']), with the curated labels/explains/order from GROUP_INFO."""
    by_group: dict = {}
    order: list = []
    for it in backup_manifest.items(m, "emucfg", emulator):
        gk = it.get("group") or "other"
        if gk not in by_group:
            by_group[gk] = []
            order.append(gk)
        by_group[gk].append({"rel": it.get("id"), "size": it.get("size", 0), "kind": it.get("kind", "file"),
                             "name": it.get("name") or os.path.basename(it.get("id") or "")})
    rows = []
    for gk in order:
        files = by_group[gk]
        info = emu_map.GROUP_INFO.get(gk, {"label": gk, "explain": "", "order": 99})
        rows.append({"key": gk, "label": info["label"], "explain": info["explain"],
                     "default_ticked": True, "present": True, "count": len(files),
                     "size": sum(f["size"] for f in files), "files": files, "order": info["order"]})
    rows.sort(key=lambda r: r["order"])
    for r in rows:
        r.pop("order", None)
    return rows


@method("emucfg.files", slow=True)
def _emucfg_files(params):
    """ONE emulator's tickable GROUPS. params {source, emulator}. Live -> emu_map.list_files (each group's
    default_ticked drives the pre-tick; the huge opt-in groups are a single folder row); a backup source ->
    the groups that backup holds. Returns {source, emulator, groups:[{key,label,explain,default_ticked,
    present,count,size,files:[{rel,name,size,kind}]}]}."""
    p = params or {}
    source = p.get("source") or LIVE_SOURCE
    emulator = p.get("emulator")
    if not emulator:
        raise RpcError("EINVAL", "emulator is required")
    if source == LIVE_SOURCE:
        return {"source": source, "emulator": emulator, "groups": emu_map.list_files(emulator)}
    m = _cloud_manifest(_cloud_source_ts(source), emucfg=True) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    return {"source": source, "emulator": emulator, "groups": _emu_manifest_groups(m, emulator)}


@method("granular.backup_emucfg")
def _granular_backup_emucfg(params):
    """Back up the selected emulator config/data. params {items:[{emulator, group, rel}], dest?}. dest
    defaults to the remembered destination. Streams; {done, path, copied, files}."""
    from . import backup_cmds
    p = params or {}
    items = p.get("items") or []
    if not items:
        raise RpcError("EINVAL", "no emulator config selected")
    dest = backup_cmds._validate_dest(p["dest"]) if p.get("dest") else backup_cmds._remembered_dest()
    ts = _ts()
    return _start_granular(
        lambda emit, stopped: granular_backup.backup_emucfg(items, dest, ts, emit, stopped))


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
                _cloud_manifest(_cloud_source_ts(source), bios=(category == "bios"),
                                esde=(category == "esde"), emucfg=(category == "emucfg"),
                                system=(category == "system"), controllers=(category == "controllers")),
                p.get("items") or [], category)
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
    if category == "emucfg":
        # Fast-fail per-emulator guard (a clean EBUSY before the stream starts): refuse if any emulator being
        # restored is live (it would clobber its config on exit). Key on the OWNING emulator of each item's
        # rel (id == rel for an emucfg item) - correct for a CATEGORY backup (system == emulator) AND a
        # game-first backup reached here (system == ES-DE, 1:many for switch, so backend_for(system) would
        # misfire). The engine (restore_selection) re-checks against the manifest rel; this is the fast path.
        from .. import proc_guard
        seen = set()
        for it in items:
            # owner from the item's rel (id == rel for emucfg) - correct for a game-first backup whose system
            # is the ES-DE system; fall back to backend_for(system) for a category backup. Owner precedence.
            be = emu_map.owner_backend_for_rel(it.get("id") or "") or emu_map.backend_for(it.get("system") or "")
            if be and be not in seen:
                seen.add(be)
                if proc_guard.emulator_running(be):
                    raise RpcError("EBUSY", f"close {emu_map.label_for(be)} before restoring its config")
    ts = _ts()
    if source.startswith("cloud:"):
        return _start_granular(
            lambda emit, stopped: _cloud_restore(source, items, category, ts, emit, stopped))
    return _start_granular(
        lambda emit, stopped: granular_backup.restore_selection(
            source, items, category, ts, emit, stopped))


@method("granular.restore_assets_preview", slow=True)
def _granular_restore_assets_preview(params):
    """READ-ONLY game-first restore preview: which of the selected games' backed-up assets already exist
    live (a restore overwrites them, snapshotting aside first). params {source, games:[{system, stem,
    keys}]}. Returns {replace, fresh, skip, restart_scope}. Local sources only for now (a cloud game-first
    restore is a later slice; the whole-ROM cloud restore still uses granular.restore_preview)."""
    p = params or {}
    source = p.get("source")
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    try:
        if source.startswith("cloud:"):
            return granular_backup.restore_preview_game_assets_manifest(
                _cloud_manifest(_cloud_source_ts(source)), p.get("games") or [])
        return granular_backup.restore_preview_game_assets(source, p.get("games") or [])
    except ValueError as exc:
        raise RpcError("ENOENT", str(exc))


@method("granular.restore_assets")
def _granular_restore_assets(params):
    """Game-first RESTORE: restore one or more games' ticked asset groups (rom/saves/states/media) from a
    backup source back to their live locations across categories. params {source, games:[{system, stem,
    keys}]}. Rule #5 per item. Streams; {done, restored, replaced, skipped, orphaned, snapshot, snapshots,
    restart_scope} at the end. Local sources only for now (cloud game-first restore is a later slice)."""
    p = params or {}
    source = p.get("source")
    games = p.get("games") or []
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    if not games:
        raise RpcError("EINVAL", "no games selected")
    ts = _ts()
    if source.startswith("cloud:"):
        return _start_granular(
            lambda emit, stopped: _cloud_restore_assets(source, games, ts, emit, stopped))
    return _start_granular(
        lambda emit, stopped: granular_backup.restore_game_assets(source, games, ts, emit, stopped))


def _all_games_in_source(source: str, system: str | None) -> list:
    """The full [{system, stem, keys:[]}] restore list for a backup source - every distinct game the backup
    holds, empty keys = ALL of that game's backed-up assets. Optionally filtered to one system. Reads the
    manifest (a local folder OR a cloud 'cloud:<ts>' set). Raises ENOENT on an invalid/missing manifest."""
    m = _cloud_manifest(_cloud_source_ts(source)) if source.startswith("cloud:") \
        else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    games = []
    for g in _manifest_games(m).values():
        if system and g.get("system") != system:
            continue
        games.append({"system": g["system"], "stem": g["stem"], "keys": []})
    return games


def _all_items_in_source(source: str, category: str) -> list:
    """Every [{system, id}] a backup holds for a FILE-FIRST category (bios/emucfg) - a whole-category restore.
    system = the bucket (bios) / emulator (emucfg); id = the item rel. Reads the manifest (local OR cloud).
    Raises ENOENT on an invalid/missing manifest."""
    m = _cloud_manifest(_cloud_source_ts(source), bios=(category == "bios"), esde=(category == "esde"),
                        emucfg=(category == "emucfg"), system=(category == "system"),
                        controllers=(category == "controllers")) \
        if source.startswith("cloud:") else backup_manifest.read(source)
    if not backup_manifest.validate(m):
        raise RpcError("ENOENT", f"no valid backup manifest for source: {source}")
    items = []
    for sysrow in backup_manifest.systems(m, category):
        sk = sysrow["key"]
        for it in backup_manifest.items(m, category, sk):
            items.append({"system": sk, "id": it.get("id")})
    return items


@method("granular.restore_all_preview", slow=True)
def _granular_restore_all_preview(params):
    """READ-ONLY preview for a whole-system / whole-category "All" restore: how many of the backup's items
    already exist live (each is snapshotted aside first on restore). params {source, category?, system?}.
    category 'roms' (default) = the game-first path (system omitted = every game; system set = that system);
    'bios'/'emucfg' = every manifest item of that category. Delegates to the reviewed per-item preview, so the
    replace/fresh/skip classification is identical to a hand-picked restore."""
    p = params or {}
    source = p.get("source")
    category = p.get("category") or "roms"
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    if category == "roms":
        games = _all_games_in_source(source, p.get("system"))
        return _granular_restore_assets_preview({"source": source, "games": games})
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    items = _all_items_in_source(source, category)
    return _granular_restore_preview({"source": source, "category": category, "items": items})


@method("granular.restore_all", slow=True)
def _granular_restore_all(params):
    """Whole-system / whole-category "All" RESTORE: restore EVERYTHING a backup holds for a category. params
    {source, category?, system?}. category 'roms' (default) = every game (or one system's games), all of each
    game's backed-up assets; 'bios'/'emucfg' = every manifest item of that category. Rule #5 per item. Streams
    the same terminal as the per-item restore - it enumerates the full set from the manifest, then delegates to
    the reviewed restore path (local OR cloud; the emucfg per-emulator running guard is enforced there)."""
    p = params or {}
    source = p.get("source")
    category = p.get("category") or "roms"
    if not source or source == LIVE_SOURCE:
        raise RpcError("EINVAL", "restore needs a backup source (not the live library)")
    if category == "roms":
        games = _all_games_in_source(source, p.get("system"))
        if not games:
            raise RpcError("EINVAL", "this backup has no games to restore")
        return _granular_restore_assets({"source": source, "games": games})
    if category not in _CATEGORY_KEYS:
        raise RpcError("EINVAL", f"unknown category: {category!r}")
    items = _all_items_in_source(source, category)
    if not items:
        raise RpcError("EINVAL", f"this backup has no {category} to restore")
    return _granular_restore({"source": source, "category": category, "items": items})


def _cloud_restore(source: str, items: list, category: str, ts: str, emit, is_stopped) -> dict:
    """Cloud restore (runs inside the granular stream): DOWNLOAD the selected games (+ the manifest) from a
    cloud set to a temp staging dir (streamed via deck-cloud.sh fetch-games), then restore_selection(staging)
    with the EXISTING, reviewed engine (rule-5 snapshot + replace + orphaned all reused). Staging is always
    cleaned up. Cancellable during the download (terminates fetch-games) and inside restore_selection."""
    import shutil
    import tempfile
    # BIOS / ES-DE / emulator config / system config each live in a SEPARATE remote base (cat-*-manifest /
    # fetch-*).
    bios = category == "bios"
    esde = category == "esde"
    emucfg = category == "emucfg"
    system = category == "system"
    controllers = category == "controllers"
    cts = _cloud_source_ts(source)
    manifest = _cloud_manifest(cts, bios=bios, esde=esde, emucfg=emucfg, system=system,
                               controllers=controllers)  # cat over net
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
        if _stream_fetch(cts, staging, planpath, emit, is_stopped, bios=bios, esde=esde, emucfg=emucfg,
                         system=system, controllers=controllers) != 0:
            raise RuntimeError("could not download from MEGA (see the cloud log)")
        # Restore from the downloaded staging folder with the UNCHANGED local engine. For esde,
        # restore_selection routes to the STAGED delivery (next-boot apply, rule #3).
        return granular_backup.restore_selection(str(staging), items, category, ts, emit, is_stopped)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.unlink(planpath)
        except OSError:
            pass


def _cloud_restore_assets(source: str, games: list, ts: str, emit, is_stopped) -> dict:
    """CLOUD game-first restore: DOWNLOAD the ticked assets of the selected games (+ manifest) from a cloud
    set to a temp staging dir (fetch-games), then restore_game_assets(staging) with the reviewed engine
    (rule-5, multi-category). The download set == the restore set: EVERY rel is gated through the same
    _plan_restore_item validator the restore uses, so a forged/corrupt cloud manifest cannot make the
    download escape the staging dir. Staging is always cleaned; cancellable during the download + restore."""
    import shutil
    import tempfile
    cts = _cloud_source_ts(source)
    manifest = _cloud_manifest(cts)
    rom_root = granular_backup.es_collections.rom_root()
    triples = granular_backup._manifest_items_for_games(manifest, games)
    plan = bytearray()
    for cat, system, item_id in triples:
        p = granular_backup._plan_restore_item(manifest, cat, {"system": system, "id": item_id},
                                               Path("/__cloud__"), rom_root, check_backup_file=False)
        if not p.get("ok"):
            continue
        rel = p["item"].get("rel")
        if not isinstance(rel, str) or not rel:
            continue
        kind = str(p["item"].get("kind") or "file")
        plan += rel.encode("utf-8") + b"\0" + kind.encode("utf-8") + b"\0"
    if not plan:
        return {"restored": 0, "replaced": 0, "skipped": 0, "orphaned": [], "snapshot": None,
                "snapshots": [], "restart_scope": "none"}
    staging = Path(tempfile.mkdtemp(prefix="mad-cloud-restore-"))
    planfd, planpath = tempfile.mkstemp(prefix="mad-cloud-plan-")
    try:
        with os.fdopen(planfd, "wb") as fh:
            fh.write(bytes(plan))
        emit({"line": "Downloading from MEGA..."})
        if _stream_fetch(cts, staging, planpath, emit, is_stopped) != 0:
            raise RuntimeError("could not download the games from MEGA (see the cloud log)")
        return granular_backup.restore_game_assets(str(staging), games, ts, emit, is_stopped)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.unlink(planpath)
        except OSError:
            pass


def _stream_fetch(ts: str, staging: Path, planpath: str, emit, is_stopped, bios: bool = False,
                  esde: bool = False, emucfg: bool = False, system: bool = False,
                  controllers: bool = False) -> int:
    """Run deck-cloud.sh fetch-games (or fetch-bios / fetch-esde / fetch-emucfg / fetch-system /
    fetch-controllers for the SEPARATE bases), forwarding each per-item line via emit; return its rc. The read
    is INTERRUPTIBLE - a select() poll re-checks is_stopped every 0.5s - so a cancel / daemon teardown during a
    long single-item rclone copy (which emits nothing to the pipe for the whole copy) promptly terminates the
    child and lets the callers clean the staging dir. Always closes the pipe + reaps the child (no leaked FD /
    zombie). Binary reads via os.read so a select-ready partial line never blocks readline()."""
    import select
    from . import cloud_cmds
    fetch = ("fetch-controllers" if controllers else "fetch-system" if system else "fetch-emucfg" if emucfg
             else "fetch-esde" if esde else "fetch-bios" if bios else "fetch-games")
    # start_new_session: the engine self-registers this fetch as a transfer job and records
    # os.getpgid(its pid). WITHOUT a new session that pgid is the DAEMON's group - which is
    # ES-DE's own group (MadBackend forks python3 with no setsid) - so a transfers.pause/stop
    # on the job would SIGSTOP/SIGTERM the whole frontend. Its own session makes the recorded
    # pgid controllable, and the interrupt path below (terminate + the select poll) is
    # unaffected: terminate() targets the direct child, not the group.
    proc = subprocess.Popen([str(cloud_cmds.ENGINE), fetch, ts, str(staging), planpath],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
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
