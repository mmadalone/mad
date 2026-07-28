"""Group the controller config into tickable GROUPS for granular backup/restore (P12b Build B).

The "Controllers" category = exactly what the router-config BACKUP op (mad_backup.do_backup) snapshots: every
emulator's ACTIVE controller config (mad_config.backup_targets - a config_dir or config_file per backend, plus
any backups.extra_configs) PLUS controller-policy.local.toml (policy.LOCAL - the GUI routing overrides). Each
target is under $HOME, so rel = 'controllers/<path relative to $HOME>' (the TRUE restore key); restore anchors
at $HOME with LEXICAL front-door containment (like emucfg/system), copying THROUGH any relocation symlink.

OVERLAP (accepted, user decision): these same files also ride in Emulator config (per-emulator input) + System
(device->player routing), and controller-policy.local.toml is in the launchers git repo. This is a DEDICATED
one-tile controller-config backup/restore, writing to the SAME shared destination as every other category.

RESTORE SAFETY: ALLOWED_PREFIXES is DERIVED at call time from the LIVE target set (each target's $HOME-relative
path) + the override file, so a restore can write ONLY those exact target paths, never elsewhere. The target
set is dynamic (it depends on which backends are configured), hence the runtime derivation.
"""
from __future__ import annotations

import os
from pathlib import Path

REL_PREFIX = "controllers/"

_GROUP_META = [
    {"key": "emulator-configs", "label": "Emulator controller configs",
     "explain": "Each emulator's active controller config - the files the router writes your pad mappings "
                "into (Cemu / Eden / PCSX2 / RPCS3 / xemu ...)."},
    {"key": "routing-overrides", "label": "Routing overrides",
     "explain": "controller-policy.local.toml - your MAD controller-routing overrides (which pad drives "
                "which player)."},
]
# key -> {label, explain, order}: a BACKUP source stores only the group KEY, so this shows the same nice label
# + explanation for a stored backup without touching disk.
GROUP_INFO = {g["key"]: {"label": g["label"], "explain": g["explain"], "order": i}
              for i, g in enumerate(_GROUP_META)}


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _rel_under_home(home: Path, p: Path) -> str | None:
    """$HOME-relative FRONT-DOOR path for p (NOT realpath'd), or None if p is not under $HOME."""
    try:
        rel = os.path.relpath(str(p), str(home))
    except ValueError:
        return None
    if rel == os.curdir or rel.startswith(os.pardir + os.sep) or rel == os.pardir:
        return None
    return rel


def _targets(home: Path) -> list:
    """[(rel_under_home, Path, is_dir)] for every backup target under $HOME (targets outside $HOME skipped)."""
    from . import mad_config
    from .policy import load_merged
    out: list = []
    try:
        tgs = mad_config.backup_targets(load_merged())
    except Exception:
        tgs = {}
    for _name, p in sorted(tgs.items()):
        p = Path(p)
        rel = _rel_under_home(home, p)
        if rel is not None:
            out.append((rel, p, p.is_dir()))
    return out


def _override(home: Path):
    """(rel_under_home, Path) for controller-policy.local.toml, or None if it is not under $HOME."""
    from . import policy
    lp = Path(policy.LOCAL)
    rel = _rel_under_home(home, lp)
    return (rel, lp) if rel is not None else None


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            total = 0
            for root, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        total += (Path(root) / f).stat().st_size
                    except OSError:
                        pass
            return total
    except OSError:
        pass
    return 0


def _allowed_prefixes(home=None) -> set:
    """The restore allowlist, DERIVED from the live target set + the override file: rel-after-'controllers/'
    must EQUAL or sit UNDER one of these exact paths. Computed at call time (the target set depends on the
    configured backends), so a forged/foreign manifest can never write outside the real controller targets."""
    h = Path(home) if home is not None else _home()
    out: set = set()
    for rel, _p, _isdir in _targets(h):
        out.add(rel.strip("/"))
    ov = _override(h)
    if ov:
        out.add(ov[0].strip("/"))
    return out


def rel_allowed(rel: str, home=None) -> bool:
    """True iff `rel` ('controllers/<path>') targets one of the live controller config paths. The restore path
    calls this so a forged manifest can never write outside them; `rel` must ALSO pass the generic traversal
    checks (done in _plan_restore_item) - this only bounds the destination."""
    if not (isinstance(rel, str) and rel.startswith(REL_PREFIX)):
        return False
    rem = rel[len(REL_PREFIX):].strip("/")
    if not rem or any(part in ("", ".", "..") for part in rem.split("/")):
        return False
    return any(rem == p or rem.startswith(p + "/") for p in _allowed_prefixes(home))


def list_groups(home=None) -> list:
    """The tickable CONTROLLER groups: [{key,label,explain,present,size,count,files:[{rel,name,size,kind}]}].
    Group 1 = every emulator's controller config target; group 2 = the routing-overrides file. An absent group
    has present=False."""
    h = Path(home) if home is not None else _home()
    cfg_files: list = []
    for rel, p, isdir in _targets(h):
        if not p.exists():
            continue
        cfg_files.append({"rel": REL_PREFIX + rel, "name": p.name,
                          "size": _size(p), "kind": "folder" if isdir else "file"})
    ov_files: list = []
    ov = _override(h)
    if ov and ov[1].is_file():
        ov_files.append({"rel": REL_PREFIX + ov[0], "name": ov[1].name,
                         "size": _size(ov[1]), "kind": "file"})
    out: list = []
    for meta, files in ((_GROUP_META[0], cfg_files), (_GROUP_META[1], ov_files)):
        out.append({"key": meta["key"], "label": meta["label"], "explain": meta["explain"],
                    "present": bool(files), "count": len(files),
                    "size": sum(f["size"] for f in files), "files": files})
    return out
