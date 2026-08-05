"""cemu_hhpacks_* - Wii U per-game graphic packs, HANDHELD ONLY.

The handheld twin of cemu_packs_cmds: the same six category sub-pages, the same one-group-per-pack
layout, but every row records what should be DIFFERENT on battery rather than editing Cemu's config.
Nothing here writes settings.xml. lib/cemu_res applies these at game-start and puts the docked state
back at game-end, so the docked pack setup is never touched from behind the On-the-go door.

Contract (INSTANT, unlike the docked twin's buffered X=Save):
  <ns>.get -> {exists:true, running, note, groups:[{title:<pack name>, note, settings:[...]}]}
  <ns>.set -> write one override straight to policy (lib/cemu_hhpacks) and bump config

Three differences from the docked page, all deliberate:
  * Every row rests on slot 0, "Same as docked (<value>)". Picking it CLEARS the override, so a pack
    the user only looked at stores nothing. That costs an index shift of 1 on the option rows.
  * The pack's Enabled row is a 3-slot enum, not a switch. A bool cannot express "same as docked"
    (the fork reads inherit only on number steppers), and without a third state every pack the user
    touched once would carry a permanent override with no gesture to clear it.
  * No EBUSY guard. This writes MAD's own policy, so it is safe with Cemu open; the write only
    reaches Cemu's file at the next handheld launch, when Cemu is closed.

The RESOLUTION option group is hidden here: a game's resolution is one option group on one pack,
owned by the On-the-go Resolution page. Ownership is matched on (filename, group) against the
HANDHELD-EFFECTIVE enabled set, not per-pack, so a game with two resolution-capable packs only hides
the group on the one that will actually be driving the render.
"""
from __future__ import annotations

from pathlib import Path

from .. import cemu_hhpacks
from . import cemu_packs_cmds as cp
from .rpc import RpcError, method

_SEP = cp._SEP                    # option-row key delimiter: <filename>\x1f<optgroup>
_ENABLED_KEY = "__enabled__"      # the pack's on/off row; never a real option-group name
_INHERIT = "Same as docked"

_NOTE = ("Handheld only, and it saves as you go. These override your docked graphic packs when a "
         "game launches on battery; your docked setup is put back when you quit. A row left on "
         "'Same as docked' changes nothing. Needs Wii U turned on in On-the-go Settings.")
_NOTE_EMPTY = "This game has no graphic packs in this category."
_NOTE_OFF = ("Off in your docked setup. Set Handheld to On to use it on battery only. ")
_NOTE_RES = ("This pack drives the game's resolution, which is set on the Resolution page so the "
             "two cannot disagree. ")


def _tid(params) -> str:
    """Lowercased, unlike the docked page's _tid: policy keys are lowercase everywhere."""
    t = (params.get("titleid") or "").strip().lower()
    if not cp._TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _ctx(tid: str) -> dict:
    """Everything a request needs, read ONCE. The pack scan and the settings.xml parse are ~75ms
    each on a real library, and a naive page would pay for both several times over."""
    entries = cp.read_entries()
    packs = cp._scan_packs()
    overrides = cemu_hhpacks.for_title(tid)
    docked = cp.enabled_filenames(entries)
    effective = cemu_hhpacks.effective_enabled(entries, overrides, docked)
    owner = cp.resolution_titleids(entries=entries, packs=packs, enabled=effective).get(tid)
    from .. import cemu_res
    return {"entries": entries, "packs": packs, "overrides": overrides, "docked": docked,
            "effective": effective, "owner": owner, "baseline": cemu_res.docked_baseline()}


def _docked_preset(ctx: dict, pk: dict, group: str) -> str | None:
    """The pack's DOCKED preset for one option group: what settings.xml holds, corrected by an
    outstanding marker (whose values are ours, not the user's), else None for "the pack default"."""
    slot = ctx["baseline"].get(cp._norm(pk["filename"]))
    if slot and group in slot["presets"]:
        return slot["presets"][group]
    e = cp._find(ctx["entries"], pk["filename"])
    return cp._entry_preset(e, group) if e is not None else None


def _docked_enabled(ctx: dict, pk: dict) -> bool:
    key = cp._norm(pk["filename"])
    slot = ctx["baseline"].get(key)
    if slot and slot["disabled"] is not None:
        return not slot["disabled"]                    # marker stores the previous `disabled` flag
    return key in ctx["docked"]


def _owner_group(ctx: dict, pk: dict) -> str | None:
    """The option group the Resolution page owns for THIS pack, or None. Matched on the resolved
    owner's filename, so a second resolution-capable pack on the same game keeps its group."""
    owner = ctx["owner"]
    if not owner or cp._norm(owner["filename"]) != cp._norm(pk["filename"]):
        return None
    return owner["group"]


def _pack_label(ctx: dict, pk: dict, siblings: list) -> str:
    """The group header. Two packs on one game can share a name (a "Resolution" and a
    "Resolution" under different folders is real, e.g. Twilight Princess HD), so a repeated name
    picks up its folder as the differentiator rather than showing twice identically."""
    name = cp._pack_label(pk)
    if sum(1 for s in siblings if cp._pack_label(s) == name) < 2:
        return name
    parent = Path(pk["filename"]).parent.name
    return f"{name} ({parent})" if parent else name


def _do_get(category: str, params: dict) -> dict:
    tid = _tid(params)
    ctx = _ctx(tid)
    mine = ctx["overrides"]
    siblings = cp._packs_for(tid, category)
    groups = []
    for pk in siblings:
        spec = mine.get(cp._norm(pk["filename"]), {})
        on_docked = _docked_enabled(ctx, pk)
        forced = spec.get("enabled")
        rows = [{"key": pk["filename"] + _SEP + _ENABLED_KEY, "label": "Handheld", "type": "enum",
                 "options": [f"{_INHERIT} ({'on' if on_docked else 'off'})", "On", "Off"],
                 "value": 0 if forced is None else (1 if forced else 2)}]
        owner_group = _owner_group(ctx, pk)
        for og in cp._optgroups(pk):
            if og == owner_group:
                continue                     # the Resolution page owns this one
            names = pk["options"][og]
            if not names:
                continue
            rest = _docked_preset(ctx, pk, og) or pk["defaults"].get(og) or names[0]
            stored = (spec.get("options") or {}).get(og)
            rows.append({
                "key": pk["filename"] + _SEP + og,
                "label": og or "Preset",
                "type": "enum",
                "options": [f"{_INHERIT} ({cp._strip_default_tag(rest)})"]
                           + [cp._strip_default_tag(n) for n in names],
                "value": names.index(stored) + 1 if stored in names else 0})
        note = ""
        if not on_docked and forced is not True:
            note += _NOTE_OFF
        if owner_group is not None:
            note += _NOTE_RES
        groups.append({"title": _pack_label(ctx, pk, siblings), "note": note.strip(),
                       "settings": rows})
    # running is deliberately False, unlike the docked twin. That flag renders "the emulator is
    # running, close it before changing these (it rewrites its config on exit and would undo your
    # changes)", which is true of a page that writes settings.xml and false of this one: nothing
    # here reaches Cemu's files until the next handheld launch, when Cemu is closed.
    return {"exists": True, "running": False,
            "note": _NOTE if groups else _NOTE_EMPTY, "groups": groups}


def _do_set(category: str, params: dict) -> dict:
    tid = _tid(params)
    key = params.get("key", "")
    filename, sep, optgroup = key.partition(_SEP)
    if not filename or not sep:
        raise RpcError("EINVAL", f"bad row key {key!r}")
    pk = next((p for p in cp._scan_packs() if p["filename"] == filename), None)
    if pk is None:
        raise RpcError("EINVAL", "graphic pack no longer exists")
    # This writes a store the launch rail obeys, so refuse a row key that does not belong to the
    # game whose page sent it. Universal packs are a global choice and are never listed per-game.
    if pk["universal"] or tid not in pk["titleids"] or cp._cat_of(pk) != category:
        raise RpcError("EINVAL", "that graphic pack is not on this game's page")
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")

    if optgroup == _ENABLED_KEY:
        if not 0 <= idx <= 2:
            raise RpcError("EINVAL", "option index out of range")
        cemu_hhpacks.set_enabled(tid, filename, None if idx == 0 else (idx == 1))
        return {"key": key, "value": idx}

    names = pk["options"].get(optgroup, [])
    if not 0 <= idx <= len(names):
        raise RpcError("EINVAL", "option index out of range")
    # GET hides the owner's resolution group, but hiding a row is not the same as refusing a write.
    # Ownership can flip between two of a game's packs (force one off, another on), so a row that
    # was legitimately editable a moment ago can become the owned one; a value stored then is
    # invisible afterwards and nothing prunes it. Refuse it here, and clear anything already stored,
    # so the Resolution page stays the single writer of that group.
    if _owner_group(_ctx(tid), pk) == optgroup:
        cemu_hhpacks.set_option(tid, filename, optgroup, None)
        raise RpcError("EINVAL", "this pack's resolution is set on the Resolution page")
    # Slot 0 is the synthetic "same as docked" entry, so a real preset sits at index-1. Store the
    # RAW name, never the display one: _strip_default_tag drops a "(Default)" suffix the pack author
    # baked into the name, and the rail matches what Cemu actually writes.
    cemu_hhpacks.set_option(tid, filename, optgroup, None if idx == 0 else names[idx - 1])
    return {"key": key, "value": idx}


def _register(category: str) -> None:
    ns = f"cemu_hhpacks_{cp.catkey(category)}"

    # No cache=: pack data lives on the filesystem, which no staterev key covers, so a pack added or
    # enabled in Cemu must show up on the next open.
    @method(f"{ns}.get", slow=True)
    def _g(params, category=category):
        return _do_get(category, params)

    @method(f"{ns}.set", slow=True)
    def _s(params, category=category):
        return _do_set(category, params)


for _cat in cp.CATEGORIES:
    _register(_cat)
