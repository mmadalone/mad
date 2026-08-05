"""cemuhh.games - the game browser behind On-the-go > Wii U > Per-game.

The handheld twin of ``cemu.games``. Same game list (every installed Wii U title), but the "custom"
badge and the summary read HANDHELD state only, and the hide list drops the leaves that do not apply
to a given title:

  packs         the game has no graphic packs at all
  packs_<cat>   that category has none for this game
  res           the game has no resolution pack that will be enabled on a handheld launch

``input`` is never hidden. Every game can take a handheld input override, and it doubles as the
guarantee that at least one leaf survives: a game that hid all of them would push an empty grid,
which the panel renders with the wrong empty state ("No standalone emulators found in ES-DE").

Why its own namespace rather than reusing an existing one: ``cemu.games`` badges DOCKED state, and
``cemures.games`` lists only titles that already have an enabled resolution pack, which would make
the packs and input leaves unreachable for every other game. Why its own module rather than a second
method in cemu_games: that module is on the launch path (titleid_for_rom) and already dodges an
import cycle with a late import; this one pulls in the whole pack + policy stack.
"""
from __future__ import annotations

from .. import cemu_hhpacks
from . import cemu_games, cemu_packs_cmds as cp
from .rpc import method


def _res_presets() -> dict:
    from ..policy import load_merged
    m = load_merged()
    systems = m.get("systems") if isinstance(m, dict) else None
    wiiu = systems.get("wiiu") if isinstance(systems, dict) else None
    hh = wiiu.get("handheld") if isinstance(wiiu, dict) else None
    rp = hh.get("res_presets") if isinstance(hh, dict) else None
    return rp if isinstance(rp, dict) else {}


@method("cemuhh.games", slow=True)
def _games(params):
    # One scan, one parse, one policy read for the whole response: each is ~75ms on a real library,
    # and the browser re-issues this on every back-press out of a leaf.
    entries = cp.read_entries()
    packs = cp._scan_packs()
    docked = cp.enabled_filenames(entries)
    store = cemu_hhpacks.load_all()
    presets = _res_presets()

    from . import cemu_pgmap_cmds as pgmap
    cfg = pgmap._cfg()

    cats: dict = {}
    for pk in packs:
        if pk["universal"]:
            continue                       # a global choice, not per-title (same rule as the pages)
        for t in pk["titleids"]:
            cats.setdefault(t, set()).add(cp._cat_of(pk))

    def _owner(tid):
        """The pack that will drive this title's resolution on a HANDHELD launch, or None. Resolved
        against the handheld-effective enabled set so a pack the user switched on or off for
        handheld is honoured here exactly as the rail will honour it."""
        eff = cemu_hhpacks.effective_enabled(entries, store.get(tid, {}), docked)
        return cp.resolution_titleids(entries=entries, packs=packs, enabled=eff).get(tid)

    owners: dict = {}

    def _owned(tid):
        if tid not in owners:
            owners[tid] = _owner(tid)
        return owners[tid]

    def _res_stored(tid):
        """The stored handheld resolution, but ONLY while a pack will actually drive it. With no
        effective owner the rail applies no resolution change and the Resolution leaf is hidden, so
        badging the value would promise something that will not happen and point at a page the user
        cannot open. The value survives in policy and comes back when the pack does."""
        return presets.get(tid) if _owned(tid) else None

    def _override(tid):
        return (bool(pgmap._slice(cfg, tid, "handheld")) or bool(_res_stored(tid))
                or tid in store)

    def _summary(tid):
        bits = []
        if pgmap._slice(cfg, tid, "handheld"):
            bits.append("input")
        if tid in store:
            bits.append("packs")
        # Badge on what is STORED, never on the automatic 720p cap: every game with a resolution
        # pack gets that by default, so badging it would mark the whole library as customised.
        stored = _res_stored(tid)
        if stored:
            bits.append("resolution: keep" if stored == cp.KEEP
                        else f"resolution: {cp._strip_default_tag(stored)}")
        return "Handheld: " + ", ".join(bits) if bits else ""

    def _hide(tid):
        hide = []
        present = cats.get(tid, set())
        if not present:
            hide.append("packs")
        else:
            hide += ["packs_" + cp.catkey(c) for c in cp.CATEGORIES if c not in present]
        if not _owned(tid):
            hide.append("res")
        return hide

    return {"games": cemu_games.listing(_override, _summary, _hide),
            "system": cemu_games._ESDE_SYSTEM}
