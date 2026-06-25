"""bezels.* — install / status / enable / disable RetroArch bezel packs for the
MAD Bezel Project page. Thin RPC wrapper over lib/bezel_cfg.py (which owns the
file operations + House-rule-#5 safety). install/uninstall are slow (symlink +
per-game cfg writes / moves); list/status/enable/disable are fast.
"""
from __future__ import annotations

from .. import bezel_cfg, bezel_discover, proc_guard, staterev
from .rpc import RpcError, method
from .systems_cmds import console_art


def _require(key):
    if bezel_cfg._by_key(key) is None:
        raise RpcError("EINVAL", f"unknown bezel system {key!r}")


def _no_retroarch():
    """Refuse a bezel cfg mutation while RetroArch is running (it rewrites its config on
    exit and would clobber our write); the bezel_cfg docstring promise, now enforced."""
    if proc_guard.retroarch_running():
        raise RpcError("EBUSY", "RetroArch is running; close it first (it rewrites its "
                                "config on exit and would lose the bezel change).")


def _safe_name(name, what="name"):
    """Path-traversal guard for a client-supplied game/bezel name. These are single filename
    STEMS used as f"{name}.cfg" inside one dir, so a path SEPARATOR is the only escape vector;
    a bare ".." (e.g. an ellipsis ROM title like "...Iru!") is a normal filename and is allowed."""
    s = str(name)
    if "/" in s or "\\" in s:
        raise RpcError("EINVAL", f"invalid {what} {name!r}")
    return name


@method("bezels.list", slow=True, cache=("config", "bezels"))
def _list(params):
    # DYNAMIC tile set: only bezel systems whose member ES-DE systems are RetroArch +
    # have a gamelist with games (adds atomiswave/naomi, drops the unused Game Gear etc.).
    systems = bezel_discover.list_systems()
    for s in systems:                       # resolve each tile's console.png art
        art = console_art(s.get("art_system", ""))
        s["art"] = [art] if art else []
    return {"systems": systems}


@method("bezels.status")
def _status(params):
    _require(params["key"])
    return bezel_cfg.status(params["key"])


@method("bezels.install", slow=True)
def _install(params):
    _require(params["key"])
    _no_retroarch()
    try:
        out = bezel_cfg.install(params["key"])
    except FileNotFoundError as e:
        raise RpcError("ENOENT", str(e))
    staterev.bump("bezels")
    return out


@method("bezels.auto_assign", slow=True)
def _auto_assign(params):
    """Wire every DOWNLOADED-but-UNASSIGNED bezel pack in one pass: for each dynamically
    discovered system whose pack is present (repo_present), has 0 configured games, and is
    NOT widescreen-warned, run install(). One shared _TMP across the whole run (House Rule
    #5). Phase-1 scope = the trivial 'pack present, nothing wired yet' case (e.g. a freshly
    cloned pack); a partial/fuzzy re-assign of under-covered packs is a later, gated feature.

    WIDESCREEN_WARN systems (naomi etc.) force 4:3, so they're wired only via the per-system
    install path — where the page shows the widescreen badge — never this no-confirm bulk
    action. A failure on one pack is recorded and skipped, never aborts the batch, and the
    cache is invalidated for whatever DID wire (the bump is in `finally`)."""
    _no_retroarch()
    from pathlib import Path
    tmp_path = None
    assigned, errors = [], []
    try:
        for key in bezel_discover.discover_keys():
            st = bezel_cfg.status(key)
            if (not st.get("repo_present") or st.get("games", 0) > 0
                    or key in bezel_cfg.WIDESCREEN_WARN):
                continue
            try:
                res = bezel_cfg.install(key, tmp_holder=tmp_path)
            except Exception as e:            # noqa: BLE001 — one bad pack must not abort the batch
                errors.append({"system": key, "error": str(e)})
                continue
            if res.get("preserved_tmp"):
                tmp_path = Path(res["preserved_tmp"])
            if res.get("games", 0) > 0 or res.get("norm_games", 0) > 0:  # exact OR norm-equal
                row = bezel_cfg._by_key(key)
                assigned.append({"system": key, "label": row[1] if row else key,
                                 "games": res.get("games", 0),
                                 "norm_games": res.get("norm_games", 0),
                                 "links": res.get("links", 0),
                                 "skipped_widescreen": res.get("skipped_widescreen", 0)})
    finally:
        staterev.bump("bezels")               # refresh the cached tile list for whatever wired
    return {"assigned": assigned, "count": len(assigned), "errors": errors}


@method("bezels.uninstall", slow=True)
def _uninstall(params):
    _require(params["key"])
    _no_retroarch()
    out = bezel_cfg.uninstall(params["key"])
    staterev.bump("bezels")
    return out


@method("bezels.enable", slow=True)
def _enable(params):
    _require(params["key"])
    _no_retroarch()
    out = bezel_cfg.set_enabled(params["key"], True)
    staterev.bump("bezels")
    return out


@method("bezels.disable", slow=True)
def _disable(params):
    _require(params["key"])
    _no_retroarch()
    out = bezel_cfg.set_enabled(params["key"], False)
    staterev.bump("bezels")
    return out


@method("bezels.games")
def _games(params):
    _require(params["key"])
    return {"games": bezel_cfg.list_games(params["key"])}


@method("bezels.disable_game", slow=True)
def _disable_game(params):
    _require(params["key"])
    _no_retroarch()
    _safe_name(params["game"], "game")
    out = bezel_cfg.disable_game(params["key"], params["game"], bool(params.get("enabled", False)))
    staterev.bump("bezels")
    return out


# ── assign / reassign an existing bezel to a same-system game ──────────────────

@method("bezels.available")
def _available(params):
    """The SOURCE list for the reassign picker: bezels installed for this system."""
    _require(params["key"])
    return {"bezels": bezel_cfg.list_available_bezels(params["key"])}


@method("bezels.roms", slow=True)   # a rom-dir scan can be slow on big systems
def _roms(params):
    """The TARGET list for the reassign picker: every ROM of this system, with the
    bezel each currently points at (assigned) + whether it has a 1:1-named bezel."""
    _require(params["key"])
    return {"roms": bezel_cfg.list_roms(params["key"])}


@method("bezels.assign", slow=True)
def _assign(params):
    """Point a target game at an existing same-system bezel (assign or reassign)."""
    _require(params["key"])
    _no_retroarch()
    _safe_name(params["target"], "target")
    _safe_name(params["source"], "source")
    try:
        out = bezel_cfg.assign_bezel(params["key"], params["target"], params["source"])
    except FileNotFoundError as e:
        raise RpcError("ENOENT", str(e))
    staterev.bump("bezels")
    return out


# ── Phase-3 fuzzy: confident normalized-equal auto-wire + interactive review + prune ──

@method("bezels.fuzzy_review", slow=True)
def _fuzzy_review(params):
    """Open the per-system fuzzy review: FIRST auto-wire the confident normalized-equal
    matches (silent), THEN return the still-unmatched ROMs as a work list. Shape:
    {auto: N, skipped_widescreen: M, tmp: <path|null>, roms: [{game, title}]}. Each ROM's
    ranked candidate bezels are fetched LAZILY per-ROM via bezels.fuzzy_candidates (ranking
    every ROM up front is too slow on big packs)."""
    _require(params["key"])
    _no_retroarch()
    try:
        auto = bezel_cfg.auto_match(params["key"])
    except Exception as e:                      # noqa: BLE001 — surface as an RPC error, don't crash
        raise RpcError("EIO", str(e))
    roms = bezel_cfg.fuzzy_unmatched(params["key"])
    staterev.bump("bezels")
    return {"auto": auto.get("norm_games", 0),
            "skipped_widescreen": auto.get("skipped_widescreen", 0),
            "tmp": auto.get("preserved_tmp"), "roms": roms}


@method("bezels.fuzzy_candidates", slow=True)
def _fuzzy_candidates(params):
    """Ranked candidate bezels for ONE unmatched rom — the interactive picker fetches these
    lazily as the user walks the review list (ranking all ROMs up front is too slow). An
    optional `query` (the Y/refine search) ranks against typed text instead of the rom name."""
    _require(params["key"])
    return {"candidates": bezel_cfg.fuzzy_candidates(params["key"], params["game"],
                                                     query=params.get("query", ""))}


@method("bezels.prune_unowned", slow=True)
def _prune_unowned(params):
    """Move MAD/bezelproject sentinel per-game cfgs for games the user doesn't own to _TMP
    (recoverable, rule #5). Cfgs for owned games are untouched."""
    _require(params["key"])
    _no_retroarch()
    out = bezel_cfg.prune_unowned(params["key"])
    staterev.bump("bezels")
    return out
