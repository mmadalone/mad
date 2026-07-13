"""pads.* — per-emulator "pads → players" controller-TYPE PRIORITY editor.

The MAD page (Standalones → <emu> → Controllers) lets the user order controller
TYPES (DualSense, DualShock 4, Xbox 360, Wii U Pro, 8BitDo, …) into a priority
list — configurable even with nothing plugged in (the list is the known type
universe, connected ones flagged). It's stored per emulator in
controller-policy.local.toml (`[standalone_pads].<emu>`) as an ordered list of
vid:pid CLASSES. The order is APPLIED AT GAME LAUNCH by the ES-DE launch wrapper
(`controller-router switch-bind`), which resolves it against whatever pads are
connected (`_ordered` ranks them by class, same-class grouped by SDL index,
unranked classes appended = "the rest"), writes ONLY the emulator's input config
(players 1..N), and restores the input on exit — so the on-the-go (Steam-direct)
launch keeps its default and per-game SETTINGS are never touched. So this module
only READS pads + STORES the order; it does NOT write the emulator config (that
fragile, context-dependent step is the launch wrapper's job).

The resolver helpers (`_real_pads`/`_supported`/`_ordered`) are reused by the
launch wrapper. Ranking is class-level: two pads of the same model share a rank
and fall to SDL-index order between themselves.
"""
from __future__ import annotations

from .. import localpolicy, mad_config, proc_guard
from ..devices import sdl_devices, enumerate_devices, port_of
from ..policy import LOCAL
from .rpc import RpcError, method

# emulator key -> page facts. `players` = how many slots we manage.
_EMUS = {
    "eden":    {"label": "Eden",    "players": 8},
    "ryujinx": {"label": "Ryujinx", "players": 8},
    "citron":  {"label": "Citron",  "players": 8},
    "pcsx2":   {"label": "PCSX2",   "players": 2},   # default BIND count (NOT hw max 8): 2 = no multitap, the safe default for 1-player PS2. managed_players() reads policy first; this is only the fallback.
    "pcsx2x6": {"label": "Namco 246/256", "players": 2},   # System 246/256 games are 1-2 players
    "xemu":    {"label": "Xbox",    "players": 4},
    "rpcs3":   {"label": "RPCS3",   "players": 7},
}

# Not real player pads: Sinden guns (vid 16c0), the MAD wii-nav bridge (vid 4d41),
# and Steam's phantom virtual-gamepad pool (28de:11ff). Mirrors the selector
# filtering in routing/eden_cfg so the list shows only pads the user recognises.
_EXCLUDE_VID = {0x16C0, 0x4D41}
_EXCLUDE_VIDPID = {"28de:11ff"}

# Classes never offered as a selectable TYPE in the priority editor: the Steam Deck's built-in
# pad, in both the forms it can appear -- the raw 28de:1205 (only visible outside gamescope) and
# the Steam virtual 28de:11ff it surfaces as INSIDE Game Mode. The latter is the launch binder's
# automatic handheld fallback (bound only when no external pad is present, re-admitted by
# deck_virtual_pad -- see switch_bind._resolve_pads). Everything else in KNOWN_PADS is a type.
_EXCLUDE_TYPE = {"28de:1205", "28de:11ff"}

# Pads SDL's JOYSTICK layer enumerates but an emulator genuinely can't drive — shown as a
# "not supported" note instead of in the selectable pick list, per emulator. Currently EMPTY:
# the Wii U Pro Controller (057e:0330) was excluded for Ryujinx on OLDER builds (its SDL2
# gamepad layer couldn't drive it), but the current SDL3 Ryubing build DOES drive it
# (user-confirmed on-device), so it is no longer filtered. The mechanism stays for any future
# emulator/pad that truly can't be used.
_UNSUPPORTED: dict = {}


def _emu(params) -> str:
    emu = params.get("emu", "")
    if emu not in _EMUS:
        raise RpcError("EINVAL", f"unknown emulator {emu!r}")
    return emu


def managed_players(emu: str) -> int:
    """How many controller slots MAD binds for this emulator at launch: the SINGLE
    source of truth for the bind cap (switch_bind._resolve_pads) and the page's
    player count (_pads_get), replacing the old duplicated switch_bind._PLAYERS /
    _EMUS['players'] reads. Reads the backend's manage_pads / manage_players /
    manage_ports from the merged policy, so controller-policy.local.toml can override
    it (e.g. PS2 4-player co-op = "[backends.pcsx2] manage_pads = 4"), falling back to
    the per-emu default in _EMUS. NOTE: this is the BIND CAP, not the hardware max.
    PCSX2 still snapshots and nulls Pad1..8 regardless (switch_bind._PLAYERS), so an
    opt-in 4-player launch can never leak phantom pads into a later Steam-UI launch."""
    from ..policy import load_merged
    be = (load_merged().get("backends", {}) or {}).get(emu, {})
    if isinstance(be, dict):
        for key in ("manage_pads", "manage_players", "manage_ports"):
            v = be.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                return v
    cfg = _EMUS.get(emu)
    return cfg["players"] if cfg else 2


def _real_pads(pump: bool = True):
    """Connected SDL joysticks that are real player pads, in SDL-index order.
    Two UNITS of the same model (same vid:pid, differing only by SDL index) are
    kept as SEPARATE pads, so e.g. two Wii U Pro controllers both appear and can
    drive Player 1 + Player 2.

    pump defaults True (OWNER): both the launch wrapper (switch_bind._resolve_pads)
    AND pads.get use it, so they drive hotplug and wait out the daemon SDL warm-up
    (pads.get is slow=True, runs off the dispatch thread, so the bounded wait is fine —
    this is what makes pads appear on first open instead of only after toggling
    Hands-off). pump=False (non-blocking reader) is used elsewhere (e.g. preview)."""
    out = []
    for d in sdl_devices(pump=pump):
        try:
            vid = int(d.vidpid.split(":")[0], 16)
        except (ValueError, IndexError):
            continue
        if vid in _EXCLUDE_VID or d.vidpid in _EXCLUDE_VIDPID:
            continue
        out.append(d)
    return out


def deck_virtual_pad(pump: bool = False):
    """The Steam Deck's built-in pad as SDL sees it INSIDE Game Mode -- the Steam VIRTUAL pad
    28de:11ff ("Steam Deck Controller"). _real_pads drops the whole 11ff phantom pool (the real
    28de:1205 is only visible from an SSH shell OUTSIDE gamescope, never to a launched emulator),
    so this is how the launch binder re-admits exactly ONE Deck pad as the handheld fallback when
    no external pad is connected (switch_bind._resolve_pads). Prefers the unit whose SDL name is
    the real "Steam Deck Controller" over a numbered "X-Box 360 pad N" phantom. None if absent.
    pump=False by default: the caller (_resolve_pads) already pumped via _real_pads, so this reads
    the warm SDL cache rather than re-pumping."""
    cands = [d for d in sdl_devices(pump=pump) if d.vidpid == "28de:11ff"]
    if not cands:
        return None
    # Steam can expose a POOL of 11ff phantoms ("Microsoft X-Box 360 pad 0/1/2"); only one is the
    # live Deck. Prefer, in order: the SDL3-named "Steam Deck Controller"; else a pad Steam actually
    # assigned a player slot (player_index >= 0, i.e. hardware-backed, not a dead ghost); else the
    # lowest-index candidate.
    named = [d for d in cands if "steam deck" in (d.name or "").lower()]
    assigned = [d for d in cands if getattr(d, "player_index", -1) >= 0]
    return (named or assigned or cands)[0]


def _strip_rank(pad_id: str) -> str:
    """Class vid:pid from a stored priority entry, tolerating legacy '<vidpid>#N'
    per-instance ids written by the old (pre-type-priority) editor."""
    return pad_id.split("#", 1)[0]


def _hands_off(emu: str) -> bool:
    """True = MAD leaves this emulator's controller config alone (the emulator uses
    its own manually-set config); the launch wrapper skips bind+restore for it."""
    data = localpolicy.load(LOCAL)
    return bool((data.get("standalone_hands_off") or {}).get(emu, False))


def _set_hands_off(emu: str, value: bool) -> None:
    data = localpolicy.load(LOCAL)
    ho = data.setdefault("standalone_hands_off", {})
    if value:
        ho[emu] = True
    else:
        ho.pop(emu, None)
        if not ho:
            data.pop("standalone_hands_off", None)
    localpolicy.dump(LOCAL, data)


def _stored_order(emu: str) -> list[str]:
    data = localpolicy.load(LOCAL)
    return [str(x) for x in ((data.get("standalone_pads") or {}).get(emu) or [])]


def _store_order(emu: str, order: list[str]) -> None:
    data = localpolicy.load(LOCAL)
    sp = data.setdefault("standalone_pads", {})
    if order:
        sp[emu] = list(order)
    else:
        sp.pop(emu, None)
        if not sp:
            data.pop("standalone_pads", None)
    localpolicy.dump(LOCAL, data)


def _type_priority(emu: str) -> dict:
    """vid:pid class -> rank from the stored per-emulator TYPE priority (first
    occurrence wins; legacy '#N' suffixes stripped). Lower rank = higher priority."""
    prio: dict = {}
    for i, pid in enumerate(_stored_order(emu)):
        prio.setdefault(_strip_rank(pid), i)
    return prio


def _ordered(emu: str, pads: list, allpads: list | None = None, *, order=None):
    """Connected pads sorted by the stored per-emulator TYPE (vid:pid class)
    priority. CONFIGURED classes keep their stored rank. UNRANKED classes ("the
    rest") fall back to the page's DISPLAY order (`_type_universe`: KNOWN_PADS family
    order, e.g. DualSense before DualShock 4) BEFORE SDL index, so the Player-1 pad
    the pads->players page SHOWS actually wins at launch even with nothing applied
    (fixes a low-SDL-index non-Deck pad stealing Player 1 when a class is unranked).
    Pads of the same class stay grouped, ordered by SDL index. `allpads` is accepted
    for call-site compatibility but unused (ranking is class-level).

    `order` (optional): a per-game TYPE-priority list (vid:pid classes) that OVERRIDES
    the stored global priority for this one resolution — used by the PCSX2 per-game
    pads page (see lib/switch_bind.py). The unranked-tail display-order logic is
    identical either way, so a per-game order composes exactly like the global one
    (configured classes first, the rest by display order)."""
    # isinstance guard: `order` comes from a hand-editable per-game store, so a corrupt non-list
    # value (int/bool/str) must fall back to the global priority, not crash the enumerate below or
    # (for a str) enumerate char-by-char into a garbage order. Mirrors the editor's isinstance guard.
    if order and isinstance(order, (list, tuple)):
        prio: dict = {}
        for i, pid in enumerate(order):
            prio.setdefault(_strip_rank(str(pid)), i)
    else:
        prio = _type_priority(emu)
    # Unranked classes sort AFTER every configured one: the base offset is STRICTLY
    # greater than every assigned rank (not len(prio): legacy '#N' ids make ranks
    # non-contiguous, so len(prio) could TIE a configured class and let an unconfigured
    # pad win Player 1).
    base = max(prio.values()) + 1 if prio else 0
    # The unranked tail follows the SAME display order the page shows (_type_universe),
    # not raw SDL index, so e.g. a DualSense beats a lower-index DualShock 4 for Player 1.
    universe = _type_universe(emu, list(dict.fromkeys(d.vidpid for d in pads)))
    disp = {vp: i for i, vp in enumerate(universe)}

    def rank(d):
        if d.vidpid in prio:
            return prio[d.vidpid]
        # unranked: keep the page's display order; a class absent from the universe
        # (shouldn't happen for a real pad) falls to the very end.
        return base + disp.get(d.vidpid, len(disp))

    return sorted(pads, key=lambda d: (rank(d), d.index))


def _type_universe(emu: str, connected_vps=()) -> list[str]:
    """Selectable controller-TYPE classes (vid:pid) for this emulator, in the stored
    priority order (configured classes first, the rest appended — mirrors the
    Priority page's family-order model). Universe = KNOWN_PADS player classes plus
    any currently-connected class, minus the handheld/virtual ones and the
    emulator's unsupported classes (so Eden lists Wii U Pro, Ryujinx doesn't)."""
    unsup = _UNSUPPORTED.get(emu, {})

    def ok(vp):
        return vp not in _EXCLUDE_TYPE and vp not in unsup

    known = [vp for vp in mad_config.KNOWN_PADS if ok(vp)]
    extra = [vp for vp in connected_vps if ok(vp) and vp not in known]
    allcls = known + extra
    order: list[str] = []
    seen: set[str] = set()
    for x in _stored_order(emu):        # configured classes first (legacy '#N' tolerated, deduped)
        c = _strip_rank(x)
        # Keep a stored class even if it's unknown AND currently disconnected — else the
        # next Apply (which rebuilds from the shown rows) would silently drop the user's
        # saved priority. Still drop excluded/unsupported classes (ok()).
        if c not in seen and ok(c):
            order.append(c)
            seen.add(c)
    order += [c for c in allcls if c not in seen]   # then "the rest"
    return order


def _supported(emu: str, pads: list):
    """Connected pads the emulator can actually use (drops known-unsupported
    classes like the Wii U Pro)."""
    unsup = _UNSUPPORTED.get(emu, {})
    return [d for d in pads if d.vidpid not in unsup]


def _handheld_class(emu: str) -> str:
    """The emulator's handheld fallback pad (the Steam Deck's built-in gamepad),
    from ``[backends.<emu>].handheld_class``. The launch binder uses it ONLY when no
    external pad is present (matches the old router). Empty → no fallback; the Deck is
    then just a normal pad."""
    from ..policy import load_merged
    be = (load_merged().get("backends", {}) or {}).get(emu, {})
    return be.get("handheld_class", "") if isinstance(be, dict) else ""


def _pad_labels(real) -> dict:
    """SDL-index -> port-aware friendly label (KNOWN_PADS / X-Arcade, not the raw
    SDL name). SDL pads carry no USB port, so recover it from the evdev twin via
    device_cmds.evdev_by_sdl_index — that's how the IDENTIFIED X-Arcade (a 045e at
    [hardware].xarcade_port) is told apart from a real Xbox 360 pad. Best-effort:
    falls back to the raw name if the evdev/policy side is unavailable."""
    from ..pad_labels import pad_label
    from .device_cmds import evdev_by_sdl_index
    from ..routing import xarcade_port
    from ..policy import load_merged
    try:
        by_sdl = evdev_by_sdl_index(enumerate_devices(), real)
        xport = xarcade_port(load_merged())
    except Exception:
        by_sdl, xport = {}, ""
    out = {}
    for d in real:
        ev = by_sdl.get(d.index)
        port = port_of(ev.phys) if ev is not None else ""
        try:
            vid = int(d.vidpid.split(":")[0], 16)
        except (ValueError, IndexError):
            vid = 0
        out[d.index] = pad_label(vid, d.vidpid, d.name, port, xport)
    return out


@method("pads.get", slow=True, cache=("devices", "config"))
def _pads_get(params):
    if params.get("emu") == "dolphin_gc":        # GameCube profile-priority page (delegated)
        from . import dolphin_gc_pads_cmds
        return dolphin_gc_pads_cmds._pads_get(params)
    if params.get("emu") == "dolphin_wii":       # Wii Classic Controller profile-priority page
        from . import dolphin_wii_pads_cmds
        return dolphin_wii_pads_cmds._pads_get(params)
    emu = _emu(params)
    cfg = _EMUS[emu]
    unsup = _UNSUPPORTED.get(emu, {})
    # pump=True: WAIT for the daemon's SDL warm-up so the connected ● flags are right
    # on the FIRST open (the reader path returned [] mid-warm, so pads only showed
    # after toggling Hands-off — which forced a re-fetch). pads.get is slow=True, so
    # the brief first-call wait is fine; later opens are cache-served (instant).
    real = _real_pads()
    connected = {d.vidpid for d in real}                 # membership for the ● flag
    connected_order = list(dict.fromkeys(d.vidpid for d in real))  # SDL-index order, deduped
    labels = _pad_labels(real)      # port-aware friendly names (X-Arcade, KNOWN_PADS)
    # The selectable controller TYPES (configurable even with nothing plugged in),
    # in stored priority order; connected ones flagged. id == the vid:pid class.
    # Pass the ORDERED connected list (not the set) so appended unknown classes have a
    # deterministic order across daemon restarts.
    universe = _type_universe(emu, connected_order)
    rows = []
    for vp in universe:
        known = mad_config.KNOWN_PADS.get(vp)
        name = mad_config.PAD_SHORT.get(vp) or known
        # A connected instance whose port-aware label differs from its class name is the
        # IDENTIFIED X-Arcade (a 045e:02a1 at [hardware].xarcade_port) — show "X-Arcade",
        # not the shared "Xbox 360" class name. (pad_label only renames the X-Arcade.)
        inst = next((labels.get(d.index) for d in real if d.vidpid == vp), None)
        if inst and inst != known:
            name = inst
        if not name:    # connected-but-unknown class — use its live friendly label
            name = inst or vp
        is_conn = vp in connected
        rows.append({"id": vp, "vidpid": vp, "connected": is_conn,
                     "label": name + ("  ●" if is_conn else "")})
    # Connected-but-unusable pads (shown as a note, NOT selectable): the per-emulator
    # _UNSUPPORTED classes, if any (currently none).
    unsupported = [{"label": labels.get(d.index) or d.name or d.vidpid, "vidpid": d.vidpid,
                    "reason": unsup[d.vidpid]}
                   for d in real if d.vidpid in unsup]
    run = proc_guard.emulator_running(emu)
    hands_off = _hands_off(emu)
    n = managed_players(emu)        # bind cap = page count (policy-driven; pcsx2 default 2)
    if run:
        note = f"Close {cfg['label']} first — it rewrites its config on exit."
    elif hands_off:
        note = f"Hands-off: {cfg['label']} uses its own controller config; MAD won't touch it."
    else:
        note = (("Set controller-TYPE priority — the top type becomes Player 1." if n == 1
                 else f"Set controller-TYPE priority — the top {n} present types become "
                      f"Players 1–{n} at launch.")
                + "  ● = connected now.")
    return {"emu": emu, "label": cfg["label"], "players": n,
            "running": run, "hands_off": hands_off, "note": note, "pads": rows,
            "unsupported": unsupported}


@method("pads.set")
def _pads_set(params):
    """Store the priority order for an emulator. The order is applied at game
    launch by the ES-DE wrapper (`controller-router switch-bind`) — we do NOT
    write the emulator config here (that would bind a raw pad and break the
    on-the-go default). `localpolicy.dump` bumps staterev('config') so the page
    re-renders from truth."""
    if params.get("emu") == "dolphin_gc":
        from . import dolphin_gc_pads_cmds
        return dolphin_gc_pads_cmds._pads_set(params)
    if params.get("emu") == "dolphin_wii":
        from . import dolphin_wii_pads_cmds
        return dolphin_wii_pads_cmds._pads_set(params)
    emu = _emu(params)
    order = [str(x) for x in (params.get("order") or [])]
    _store_order(emu, order)
    return {"emu": emu, "order": order,
            "message": f"Saved — applied when you launch {_EMUS[emu]['label']} from ES-DE."}


@method("pads.hands_off")
def _pads_hands_off(params):
    """Toggle whether MAD manages this emulator's controllers at launch. ON = the
    emulator uses its own config (the launch wrapper skips bind+restore); OFF = MAD
    applies the stored pads→players order at launch."""
    if params.get("emu") == "dolphin_gc":
        from . import dolphin_gc_pads_cmds
        return dolphin_gc_pads_cmds._pads_hands_off(params)
    if params.get("emu") == "dolphin_wii":
        from . import dolphin_wii_pads_cmds
        return dolphin_wii_pads_cmds._pads_hands_off(params)
    emu = _emu(params)
    value = bool(params.get("value"))
    _set_hands_off(emu, value)
    label = _EMUS[emu]["label"]
    return {"emu": emu, "hands_off": value,
            "message": (f"Hands-off ON — {label} will use its own controller config."
                        if value else
                        f"Hands-off OFF — MAD applies your order when you launch {label}.")}
