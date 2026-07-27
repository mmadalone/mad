"""Group ES-DE settings files into tickable GROUPS for granular backup/restore (P6).

Curated, NOT a filename classifier: ES-DE settings are a small fixed set under ~/ES-DE, so each GROUP is a
hand-listed set of files with a plain-English explanation for the UI. rel = 'esde/<path relative to
~/ES-DE>' (the TRUE restore key). The "Game favorites & metadata" group is per-system (one gamelist.xml per
system) and drills via list_gamelist_systems().

DELIBERATELY EXCLUDED (regenerable / large / not user settings): themes/, resources/, scrapers/,
downloaded_media (a symlink), logs/, splashscreens/, screensavers/, usermanuals/, controllers/, and any
*.bak* debris. Restoring those would be pointless or huge; ES-DE regenerates them.
"""
from __future__ import annotations

import os
from pathlib import Path

# Order = display order. Each group lists its files under ~/ES-DE. `gamelists` is special (per-system).
_GROUPS = [
    {"key": "settings", "label": "Main settings", "files": ["settings/es_settings.xml"],
     "explain": "All ES-DE options: theme, scraper, media folder and every UI toggle. "
                "Applied the next time ES-DE starts."},
    {"key": "input", "label": "Controller input", "files": ["settings/es_input.xml"],
     "explain": "Your controller button mappings inside ES-DE (one config per pad)."},
    {"key": "custom_systems", "label": "Custom systems",
     "files": ["custom_systems/es_systems.xml", "custom_systems/es_systems_sorting.xml",
               "custom_systems/es_find_rules.xml"],
     "explain": "Systems you added or customised, their sort order, and the emulator find-rules."},
    {"key": "collections", "label": "Collections", "glob": ("collections", "custom-*.cfg"),
     "explain": "Your custom game collections (each file is one collection's game list)."},
    {"key": "gamelists", "label": "Game favorites & metadata", "gamelists": True,
     "explain": "Favorites, play counts, hidden flags and ratings - per system. "
                "Drill in to pick individual systems."},
]


# key -> {label, explain, order}: so a BACKUP source (which only stores the group KEY) can show the same
# nice label + explanation as the live view, without reading ~/ES-DE.
GROUP_INFO = {g["key"]: {"label": g["label"], "explain": g["explain"], "order": i}
              for i, g in enumerate(_GROUPS)}


def _root(esde_root=None) -> Path:
    if esde_root is not None:
        return Path(esde_root)
    from . import esde_settings
    return Path(esde_settings.APPDATA)


def _row(root: Path, rel_under: str) -> dict | None:
    """A tickable file row for one path under ~/ES-DE, or None if absent. rel is the true restore key."""
    p = root / rel_under
    if not p.is_file():
        return None
    return {"rel": f"esde/{rel_under}", "name": os.path.basename(rel_under), "size": p.stat().st_size}


def _group_files(root: Path, g: dict) -> list:
    files: list = []
    if g.get("gamelists"):
        gdir = root / "gamelists"
        if gdir.is_dir():
            for sysd in sorted(p for p in gdir.iterdir() if p.is_dir()):
                gl = sysd / "gamelist.xml"
                if gl.is_file():
                    files.append({"rel": f"esde/gamelists/{sysd.name}/gamelist.xml",
                                  "name": f"{sysd.name}/gamelist.xml", "size": gl.stat().st_size})
    elif g.get("glob"):
        subdir, pat = g["glob"]
        d = root / subdir
        if d.is_dir():
            for f in sorted(d.glob(pat)):
                if f.is_file():
                    files.append({"rel": f"esde/{subdir}/{f.name}", "name": f.name, "size": f.stat().st_size})
    else:
        for rel_under in g["files"]:
            r = _row(root, rel_under)
            if r:
                files.append(r)
    return files


def list_groups(esde_root=None) -> list:
    """The 5 ES-DE settings GROUPS: [{key,label,explain,present,count,size,files:[{rel,name,size}]}].
    `present` is False for a group whose files are all absent (nothing to back up)."""
    root = _root(esde_root)
    out = []
    for g in _GROUPS:
        files = _group_files(root, g)
        out.append({"key": g["key"], "label": g["label"], "explain": g["explain"],
                    "present": bool(files), "count": len(files),
                    "size": sum(f["size"] for f in files), "files": files})
    return out


def list_gamelist_systems(esde_root=None) -> list:
    """The per-system drill for the "Game favorites & metadata" group: one row per system that has a
    gamelist.xml. [{system,label,rel,size}], sorted by the short display label."""
    root = _root(esde_root)
    from . import es_systems
    gdir = root / "gamelists"
    rows = []
    if gdir.is_dir():
        for sysd in sorted(p for p in gdir.iterdir() if p.is_dir()):
            gl = sysd / "gamelist.xml"
            if gl.is_file():
                rows.append({"system": sysd.name, "label": es_systems.short_name(sysd.name),
                             "rel": f"esde/gamelists/{sysd.name}/gamelist.xml", "size": gl.stat().st_size})
    rows.sort(key=lambda r: (r["label"] or r["system"]).lower())
    return rows
