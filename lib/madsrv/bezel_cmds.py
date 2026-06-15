"""bezels.* — install / status / enable / disable RetroArch bezel packs for the
MAD Bezel Project page. Thin RPC wrapper over lib/bezel_cfg.py (which owns the
file operations + House-rule-#5 safety). install/uninstall are slow (symlink +
per-game cfg writes / moves); list/status/enable/disable are fast.
"""
from __future__ import annotations

from .. import bezel_cfg, staterev
from .rpc import RpcError, method
from .systems_cmds import console_art


def _require(key):
    if bezel_cfg._by_key(key) is None:
        raise RpcError("EINVAL", f"unknown bezel system {key!r}")


@method("bezels.list", slow=True, cache=("config", "bezels"))
def _list(params):
    systems = bezel_cfg.list_systems()
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
    try:
        out = bezel_cfg.install(params["key"])
    except FileNotFoundError as e:
        raise RpcError("ENOENT", str(e))
    staterev.bump("bezels")
    return out


@method("bezels.uninstall", slow=True)
def _uninstall(params):
    _require(params["key"])
    out = bezel_cfg.uninstall(params["key"])
    staterev.bump("bezels")
    return out


@method("bezels.enable", slow=True)
def _enable(params):
    _require(params["key"])
    out = bezel_cfg.set_enabled(params["key"], True)
    staterev.bump("bezels")
    return out


@method("bezels.disable", slow=True)
def _disable(params):
    _require(params["key"])
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
    out = bezel_cfg.disable_game(params["key"], params["game"], bool(params.get("enabled", False)))
    staterev.bump("bezels")
    return out
