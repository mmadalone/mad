"""backup_manifest - the browsable index + restore key for a MAD backup.

WHY: today a backup is either a browsable mirror FOLDER (config only) or an opaque
tar/tar.gz (whole config, or all ROMs, or all media). The granular Backup & Restore
manager needs to let the user drill CATEGORY -> SYSTEM/EMULATOR -> GAME/FILE and restore
ONE item, from a LOCAL backup or the MEGA CLOUD, WITHOUT unpacking a multi-GB archive or
listing a huge remote tree. A small JSON manifest written next to (or inside) every backup
solves both: it is the fast browse index AND the restore key (each entry records where the
file came from, so restore knows where it goes back).

STRUCTURE (schema 1)::

    {
      "schema": 1,
      "created": "20260724T101530",      # ES-DE-style local timestamp (caller may pass its own)
      "kind": "roms",                     # what produced this backup: config|roms|media|granular|...
      "host": "steamdeck",
      "categories": {
        "roms": {                         # category KEY (stable id used by the RPC + C++)
          "label": "ROMs & games",
          "systems": {
            "nes": {                      # system/emulator KEY
              "label": "Nintendo Entertainment System",
              "items": [ <item>, ... ]
            }
          }
        }
      }
    }

An <item> (see make_item) records, per backed-up thing:
  id      stable identity within its (category, system) - e.g. "nes:smb" or a file basename
  name    human label (a game <name>, or a friendly file name)
  src     ABSOLUTE source path it was backed up FROM (a realpath) - the restore anchor
  rel     path WITHIN the backup (archive member / folder-relative) - what to extract
  kind    "file" | "folder"
  size    bytes (int; 0 if unknown) - for browse tallies without touching the archive
  plus optional per-category metadata: stem, boxart, addons[], restart(none|esde|emulator).

The manifest is INFORMATION ONLY - it never itself restores anything; the restore layer reads
it and maps `src`/`rel` to real writes (honoring the ROM-root/symlink relocation and rule #5
snapshot). Category-specific restore mapping is the restore layer's job, not this module's.

Stdlib only; ASCII only. A tiny CLI (see __main__) lets shell (deck-backup.sh) build/read a
manifest without embedding JSON by hand.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = 1
MANIFEST_NAME = "mad-manifest.json"
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024   # a manifest is small; never read a file bigger than this

# restart scopes an item's restore may require (categories 3+5)
RESTART_SCOPES = ("none", "esde", "emulator")


def _now_stamp() -> str:
    """ES-DE-style local timestamp YYYYmmddTHHMMSS (matches gamelist <lastplayed> + backup dir names)."""
    return time.strftime("%Y%m%dT%H%M%S")


def new_manifest(kind: str, created: str | None = None, host: str = "steamdeck") -> dict:
    """A fresh, empty manifest. `created` defaults to now; pass the caller's own stamp
    (e.g. the backup directory's timestamp) to keep the manifest and its backup in lockstep."""
    return {
        "schema": SCHEMA,
        "created": created or _now_stamp(),
        "kind": str(kind),
        "host": host,
        "categories": {},
    }


def make_item(*, id: str, name: str, src: str, rel: str, kind: str = "file",
              size: int = 0, stem: str = "", boxart: bool = False,
              addons: list | None = None, restart: str = "none",
              extra: dict | None = None) -> dict:
    """Build one normalized manifest item. `src` is the absolute source path (a realpath);
    `rel` is the item's path inside the backup. `restart` must be one of RESTART_SCOPES.
    `extra` folds in any category-specific fields verbatim (kept forward-compatible)."""
    if restart not in RESTART_SCOPES:
        raise ValueError(f"restart must be one of {RESTART_SCOPES}, got {restart!r}")
    if kind not in ("file", "folder"):
        raise ValueError(f"kind must be 'file' or 'folder', got {kind!r}")
    item = {
        "id": str(id),
        "name": str(name),
        "src": str(src),
        "rel": str(rel),
        "kind": kind,
        "size": int(size),
    }
    if stem:
        item["stem"] = str(stem)
    if boxart:
        item["boxart"] = True
    if addons:
        item["addons"] = list(addons)
    if restart != "none":
        item["restart"] = restart
    if extra:
        for k, v in extra.items():
            item.setdefault(k, v)
    return item


def add_item(manifest: dict, *, category: str, category_label: str,
             system: str, system_label: str, item: dict) -> None:
    """Insert an item under (category -> system), creating the branches on first use.
    Idempotent-ish: a later item with the same id under the same (category, system)
    REPLACES the earlier one (so a rebuild does not duplicate rows)."""
    cats = manifest.setdefault("categories", {})
    cat = cats.setdefault(category, {"label": category_label, "systems": {}})
    if category_label and not cat.get("label"):
        cat["label"] = category_label
    sysd = cat.setdefault("systems", {})
    sysent = sysd.setdefault(system, {"label": system_label, "items": []})
    if system_label and not sysent.get("label"):
        sysent["label"] = system_label
    items = sysent.setdefault("items", [])
    iid = item.get("id")
    for i, existing in enumerate(items):
        if existing.get("id") == iid:
            items[i] = item
            return
    items.append(item)


# ---- persistence -----------------------------------------------------------

def manifest_path(backup: str | Path) -> Path:
    """Where the manifest lives for a backup target. Inside a FOLDER (mirror) -> folder/MANIFEST_NAME.
    For an ARCHIVE file (or a not-yet-created path) -> a SIDECAR next to it (<archive>.mad-manifest.json),
    so a cloud browse can `rclone cat` the sidecar without downloading the archive."""
    backup = Path(backup)
    if backup.is_dir():
        return backup / MANIFEST_NAME
    return backup.with_name(backup.name + "." + MANIFEST_NAME)


def write(manifest: dict, dest: str | Path) -> Path:
    """Atomically write `manifest` as JSON to `dest` (an explicit manifest file path).
    Use manifest_path() to derive the conventional location for a backup. Returns the path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    data = json.dumps(manifest, ensure_ascii=True, indent=1, sort_keys=False)
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def read_text(text: str) -> dict:
    """Parse manifest JSON text (e.g. the stdout of `rclone cat .../mad-manifest.json`).
    Returns {} on any parse error or a non-object / wrong-schema payload."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict) or "categories" not in obj:
        return {}
    return obj


def read(path: str | Path) -> dict:
    """Read a manifest given a manifest FILE, a backup FOLDER (looks inside), or an ARCHIVE
    path (looks for its sidecar). Returns {} when no readable manifest is found."""
    path = Path(path)
    cand: Path | None = None
    if path.is_file() and path.name == MANIFEST_NAME:
        cand = path
    elif path.is_dir():
        p = path / MANIFEST_NAME
        cand = p if p.is_file() else None
    else:
        # an archive path (existing or not): try its sidecar, else the raw path ONLY IF it is itself a
        # manifest file (name ends in .mad-manifest.json). NEVER blind-read an arbitrary path: the backup
        # dir also holds deck-backup.sh's multi-GB deck-roms-*.tar archives, and slurping one into memory
        # to fail json.loads would stall the daemon / OOM the Deck.
        side = path.with_name(path.name + "." + MANIFEST_NAME)
        if side.is_file():
            cand = side
        elif path.is_file() and path.name.endswith(MANIFEST_NAME):
            cand = path
    if cand is None:
        return {}
    try:
        # a real manifest is small (< a few MB even for thousands of games); cap the read so a truncated/
        # padded or wrongly-named file can never be slurped whole.
        if cand.stat().st_size > _MAX_MANIFEST_BYTES:
            return {}
        return read_text(cand.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


# ---- validation + accessors (browse/restore consume these) -----------------

def validate(manifest: dict) -> bool:
    """A manifest is usable iff it is a dict, carries the right schema, and has at least one
    item somewhere. An empty/foreign/corrupt manifest is REJECTED so a restore can never key
    off it (mirrors deck-restore.sh's non-empty-manifest guard)."""
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return False
    cats = manifest.get("categories")
    if not isinstance(cats, dict) or not cats:
        return False
    # A foreign/corrupt-but-still-JSON manifest can carry a non-dict category/system value
    # (e.g. {"categories":{"roms":null}}); guard every nested access so validate REJECTS it (returns
    # False) instead of raising - callers only guard the False case, not an exception.
    for cat in cats.values():
        if not isinstance(cat, dict):
            continue
        systems = cat.get("systems")
        if not isinstance(systems, dict):
            continue
        for sysent in systems.values():
            if _item_list(sysent):        # a non-empty list of dict items (same rule the accessors use)
                return True
    return False


# Every accessor below tolerates a non-dict/foreign nested shape (validate() may pass on the FIRST valid
# item while a sibling category/system is junk). These helpers keep a corrupt manifest from crashing the
# browse thread - a bad branch simply contributes nothing.

def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _item_list(sysent) -> list:
    items = _dict(sysent).get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _as_int(v) -> int:
    """Coerce a possibly-foreign size to an int, defaulting to 0 - a corrupt manifest's non-numeric
    'size' must contribute 0, not crash the browse (matches the _dict/_item_list tolerance contract)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _sum_size(items) -> int:
    return sum(_as_int(i.get("size", 0)) for i in items)


def categories(manifest: dict) -> list:
    """[{key, label, n_systems, n_items, size}] for the top-level category hub, sorted by key."""
    out = []
    for key in sorted(_dict(manifest.get("categories"))):
        cat = _dict(_dict(manifest.get("categories"))[key])
        sysd = _dict(cat.get("systems"))
        n_items = sum(len(_item_list(s)) for s in sysd.values())
        size = sum(_sum_size(_item_list(s)) for s in sysd.values())
        out.append({"key": key, "label": cat.get("label", key),
                    "n_systems": len(sysd), "n_items": n_items, "size": size})
    return out


def systems(manifest: dict, category: str) -> list:
    """[{key, label, n_items, size}] for a category's per-system/emulator tiles, sorted by key."""
    sysd = _dict(_dict(_dict(manifest.get("categories")).get(category)).get("systems"))
    out = []
    for key in sorted(sysd):
        items = _item_list(sysd[key])
        out.append({"key": key, "label": _dict(sysd[key]).get("label", key),
                    "n_items": len(items), "size": _sum_size(items)})
    return out


def items(manifest: dict, category: str, system: str) -> list:
    """The item list for one (category, system), verbatim (empty list if absent/corrupt)."""
    sysd = _dict(_dict(_dict(manifest.get("categories")).get(category)).get("systems"))
    return list(_item_list(sysd.get(system)))


def find_item(manifest: dict, category: str, system: str, item_id: str) -> dict | None:
    """The single item with `item_id` under (category, system), or None. The restore key lookup."""
    for it in items(manifest, category, system):
        if it.get("id") == item_id:
            return it
    return None


def item_game(item: dict, system: str) -> tuple:
    """Interpret a manifest item as (game_id, system, stem, asset_key), unifying the TWO granular schemas so
    the game-first restore/browse handles both:
      - a GAME-FIRST item (plan_game_assets) carries game='<sys>:<stem>' + asset (rom/saves/states/media);
      - a WHOLE-ROM item (the config/pilot backup, plan_selection) has NEITHER tag, but its id IS
        '<sys>:<stem>' and it is the game's ROM.
    Returns (game_id, system, stem, asset_key), or (None, system, None, None) when the item can't be keyed.
    `system` is the item's nesting system (authoritative), passed through so callers need not re-derive it."""
    g = item.get("game")
    if isinstance(g, str) and ":" in g:
        a = item.get("asset")
        return g, system, g.split(":", 1)[1], (a if isinstance(a, str) else "rom")
    iid = item.get("id")
    if isinstance(iid, str) and ":" in iid:
        return iid, system, (item.get("stem") or iid.split(":", 1)[1]), "rom"
    return None, system, None, None


# ---- tiny CLI so shell can build/read a manifest ---------------------------
# Usage:
#   python3 -m lib.backup_manifest read  <manifest|folder|archive>
#   python3 -m lib.backup_manifest validate <manifest|folder|archive>   (exit 0 = valid)
# The Python callers (granular_cmds, backup_manifest tests) use the API above directly; the
# CLI is the seam for deck-backup.sh (P0b) to emit/verify a manifest without hand-rolling JSON.
def _main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: backup_manifest {read|validate} <path>", flush=True)
        return 2
    cmd, path = argv[0], argv[1]
    m = read(path)
    if cmd == "read":
        print(json.dumps(m, ensure_ascii=True, indent=1))
        return 0 if m else 1
    if cmd == "validate":
        return 0 if validate(m) else 1
    print(f"unknown command: {cmd}", flush=True)
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
