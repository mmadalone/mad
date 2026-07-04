"""ryujinx_pg_*.* — Ryujinx (Switch) PER-GAME settings (System / CPU / Graphics / Adv. Graphics /
Audio), inherit-aware.

Unlike the Yuzu forks (Citron/Eden, ini inherit markers), Ryujinx has NO per-key inherit -- its
per-game games/<tid>/Config.json is a COMPLETE clone, so MAD tracks overrides in a sidecar pin-map
(Config.json.mad-pins) and REGENERATES the complete file (ryujinx_cmds). This module only slices the
shared GROUPS registry per page (by the group's "page" tag) and delegates to the ryujinx_cmds
pin-map engine (_pergame_get / _pergame_set). Instant save; refuses while Ryujinx runs (it rewrites
config on exit)."""
from __future__ import annotations

from .. import proc_guard
from . import cfgutil
from . import ryujinx_cmds as rc
from . import yuzu_pergame as yp
from .rpc import RpcError, method

# per-game page ns -> (title, GROUPS page-tag). Mirrors the global ryujinx_settings pages.
PG_PAGES = {
    "ryujinx_pg_system": ("System", "ryujinx_system"),
    "ryujinx_pg_cpu":    ("CPU", "ryujinx_cpu"),
    "ryujinx_pg_gfx":    ("Graphics", "ryujinx_gfx"),
    "ryujinx_pg_gfxadv": ("Adv. Graphics", "ryujinx_gfxadv"),
    "ryujinx_pg_audio":  ("Audio", "ryujinx_audio"),
}


def _page_groups(page: str) -> list:
    return [g for g in rc.GROUPS if g.get("page") == page]


def _register(ns: str, page: str) -> None:
    groups = _page_groups(page)

    @method(f"{ns}.get", slow=True)
    def _g(params, groups=groups):
        return rc._pergame_get(yp.tid(params), groups)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups):
        if proc_guard.emulator_running(rc._PROC):
            raise RpcError("EBUSY", "Ryujinx is running — close it first "
                                    "(it rewrites its config on exit).")
        item = cfgutil.item_by_key(groups, params["key"])
        if item is None:
            raise RpcError("EINVAL", f"{params.get('key')!r} is not an editable setting")
        return rc._pergame_set(item, params)


for _ns, (_title, _page) in PG_PAGES.items():
    _register(_ns, _page)
