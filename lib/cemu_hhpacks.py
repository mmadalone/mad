"""Handheld graphic-pack OVERRIDES for Cemu (Wii U): the store, shared by the pages and the rail.

Behind the On-the-go door everything is HANDHELD state, so these pages never touch Cemu's own
settings.xml. They record, in MAD's policy file, what should be DIFFERENT about a game's graphic
packs when it launches handheld; lib/cemu_res applies that at game-start and puts the docked state
back at game-end. A pack you never touch is stored not at all and behaves identically in both
contexts.

Store, sparse by construction (empty tables are pruned on the way out)::

    [systems.wiiu.handheld.packs.<titleid>."graphicPacks/.../rules.txt"]
    enabled = false                         # absent = same as docked
    [systems.wiiu.handheld.packs.<titleid>."graphicPacks/.../rules.txt".options]
    "TV Resolution" = "1280x720"            # absent group = same as docked
    "" = "Performance"                      # the UNNAMED option group is a real group, not a blank

Title ids are lowercase 16-hex; pack keys are ``cemu_packs_cmds._norm``'d, so the same pack always
lands on the same key whichever side wrote it. Both are quoted by ``localpolicy._key``. Never put
``cemu_packs_cmds._SEP`` (\\x1f) in a key: tomllib rejects it and ``localpolicy.load`` then returns
{} on the decode error, wiping every override in the file.

RESOLUTION stays out of here. A game's resolution is one option group on one pack, owned by the
On-the-go Resolution page and stored in ``[systems.wiiu.handheld.res_presets]``; these pages hide
that group so the same value can never be written from two places. The one interaction is
``effective_enabled``: if the handheld override turns the resolution owner OFF, the resolution
change is pointless and the rail skips it.
"""
from __future__ import annotations

import re

from .madsrv import cemu_packs_cmds as cp

_TID_RE = re.compile(r"^[0-9a-f]{16}$")
_PATH = ("systems", "wiiu", "handheld", "packs")


def _clean_tid(tid: str) -> str:
    t = (tid or "").strip().lower()
    return t if _TID_RE.match(t) else ""


def _slice(pol: dict, *keys) -> dict:
    """Walk a nested dict, returning {} the moment anything is missing or not a dict. Policy is
    user-editable, so a hand-typed scalar where a table belongs must degrade, never raise."""
    cur = pol
    for k in keys:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(k)
    return cur if isinstance(cur, dict) else {}


def load_all(pol: dict | None = None) -> dict:
    """{titleid: {pack filename: {"enabled": bool|None, "options": {group: preset}}}}, normalised.
    Malformed rows are dropped rather than raised on."""
    if pol is None:
        from .policy import load_merged
        pol = load_merged()
    raw = _slice(pol, *_PATH)
    out: dict = {}
    for tid, packs in raw.items():
        t = _clean_tid(tid)
        if not t or not isinstance(packs, dict):
            continue
        for filename, spec in packs.items():
            if not isinstance(spec, dict) or not isinstance(filename, str):
                continue
            enabled = spec.get("enabled")
            opts = {g: p for g, p in _slice(spec, "options").items()
                    if isinstance(g, str) and isinstance(p, str)}
            if not isinstance(enabled, bool) and not opts:
                continue                       # nothing usable in this row
            out.setdefault(t, {})[cp._norm(filename)] = {
                "enabled": enabled if isinstance(enabled, bool) else None,
                "options": opts}
    return out


def for_title(tid: str, pol: dict | None = None) -> dict:
    """{pack filename: {"enabled", "options"}} for one title, or {}."""
    return load_all(pol).get(_clean_tid(tid), {})


def _write(tid: str, filename: str, mutate) -> None:
    """Apply `mutate` to this pack's spec table, then prune every table the edit emptied so a
    cleared override leaves no trace. Serialised against other policy writers."""
    from . import localpolicy
    from .policy import LOCAL
    t, fn = _clean_tid(tid), cp._norm(filename)
    if not t or not fn:
        return

    def _mut(data):
        blk = data
        for k in _PATH:
            blk = blk.setdefault(k, {})
        title = blk.setdefault(t, {})
        spec = title.setdefault(fn, {})
        mutate(spec)
        if not spec.get("options"):
            spec.pop("options", None)
        if not spec:
            title.pop(fn, None)
        if not title:
            blk.pop(t, None)
        if not blk:
            # Prune the empty [packs] table and any parent it just emptied, so an all-cleared
            # store does not leave dead headers behind for the next hand-edit to puzzle over.
            node = data
            chain = []
            for k in _PATH:
                chain.append((node, k))
                node = node.get(k, {})
            for parent, key in reversed(chain):
                if isinstance(parent.get(key), dict) and not parent[key]:
                    parent.pop(key, None)

    localpolicy.read_modify_write(LOCAL, _mut)


def set_enabled(tid: str, filename: str, value: bool | None) -> None:
    """Force a pack on (True) or off (False) handheld; None clears back to "same as docked"."""
    def _m(spec):
        if value is None:
            spec.pop("enabled", None)
        else:
            spec["enabled"] = bool(value)
    _write(tid, filename, _m)


def set_option(tid: str, filename: str, group: str, preset: str | None) -> None:
    """Override one option group's preset handheld; None clears it back to "same as docked".
    `group` may legitimately be "" (the unnamed group)."""
    def _m(spec):
        opts = spec.setdefault("options", {})
        if preset is None:
            opts.pop(group, None)
        else:
            opts[group] = preset
    _write(tid, filename, _m)


def clear_title(tid: str) -> None:
    """Drop every handheld pack override for one title."""
    from . import localpolicy
    from .policy import LOCAL
    t = _clean_tid(tid)
    if not t:
        return

    def _mut(data):
        _slice(data, *_PATH).pop(t, None)

    localpolicy.read_modify_write(LOCAL, _mut)


def effective_enabled(entries: list, overrides: dict, docked_enabled: set | None = None) -> set:
    """The _norm'd filenames that will actually be ENABLED for this title on a handheld launch:
    the docked enabled set, minus the packs the override forces off, plus the ones it forces on.

    This is the set every consumer must resolve against -- which pack owns resolution, which option
    group the packs pages hide, and what the rail writes -- so that a pack the user switched off
    handheld cannot still be treated as the resolution owner (and vice versa)."""
    eff = set(cp.enabled_filenames(entries) if docked_enabled is None else docked_enabled)
    for filename, spec in (overrides or {}).items():
        on = spec.get("enabled")
        if on is True:
            eff.add(cp._norm(filename))
        elif on is False:
            eff.discard(cp._norm(filename))
    return eff
