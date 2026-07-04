"""ryujinx_* — Ryujinx (Switch) GLOBAL settings, split into the Switch-emu menu-scheme pages.

Five instant-save pages mirroring Ryujinx's own Settings sidebar (curated to the useful subset):
  ryujinx_system / ryujinx_cpu / ryujinx_gfx / ryujinx_gfxadv / ryujinx_audio

Each renders a slice of ryujinx_cmds.GROUPS (the shared managed-key registry, filtered by the
group's "page" tag) over the single flat ~/.config/Ryujinx/Config.json. Reuses ryujinx_cmds' JSON
primitives (_json_read / _apply_key / set_global) so the global write path stays in ONE place and
the per-game engine keeps tracking the SAME managed-key set. String enums store the exact member
name; a bad token silently reverts to member 0 (see deck-docs/ryubing-config.md).

NOTE (no fork rebuild): these are kind:"settings" pages rendered by the generic
GuiMadPageEmuSettings, so onboarding is pure Python (this module + the _ryujinx_sections tree).
"""
from __future__ import annotations

from .. import proc_guard
from . import cfgutil, ryujinx_json
from . import ryujinx_cmds as rc
from .rpc import RpcError, method

# ns -> page title. Order = Ryujinx's own Settings sidebar order.
PAGES = {
    "ryujinx_system": "System",
    "ryujinx_cpu":    "CPU",
    "ryujinx_gfx":    "Graphics",
    "ryujinx_gfxadv": "Adv. Graphics",
    "ryujinx_audio":  "Audio",
}


def _page_groups(ns: str) -> list:
    """The GROUPS slice tagged for this page (the shared registry, filtered by 'page')."""
    return [g for g in rc.GROUPS if g.get("page") == ns]


def _register(ns: str, title: str) -> None:
    groups = _page_groups(ns)
    label = f"Ryujinx ({title})"

    @method(f"{ns}.get", slow=True, cache=("config",))
    def _g(params, groups=groups, label=label):
        # do_get offers only keys present in Config.json (version-safe), so a build without a
        # given key just omits that row rather than inventing it.
        return cfgutil.do_get(groups, ryujinx_json.CONFIG, rc._json_read,
                              proc=rc._PROC, label=label)

    @method(f"{ns}.set", slow=True)
    def _s(params, groups=groups, label=label):
        if proc_guard.emulator_running(rc._PROC):
            raise RpcError("EBUSY", f"{label} is running — close Ryujinx first "
                                    "(it rewrites its config on exit).")
        item = cfgutil.item_by_key(groups, params["key"])
        if item is None:
            raise RpcError("EINVAL", f"{params.get('key')!r} is not an editable setting")
        return rc.set_global(item, params)   # ryujinx_json.write auto-bumps staterev('config')


for _ns, _title in PAGES.items():
    _register(_ns, _title)
