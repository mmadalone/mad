"""pads.* — per-emulator "pads → players" PRIORITY editor.

The MAD page (Standalones → Switch → Eden/Ryujinx → Controllers) lets the user
order the connected pads into a priority list, stored per emulator in
controller-policy.local.toml (`[standalone_pads].<emu>`). The order is APPLIED AT
GAME LAUNCH by the ES-DE launch wrapper (`controller-router switch-bind`), which
resolves it against whatever pads are connected, writes ONLY the emulator's input
config (players 1..N), and restores the input on exit — so the on-the-go
(Steam-direct) launch keeps its default and per-game SETTINGS are never touched.
So this module only READS pads + STORES the order; it does NOT write the emulator
config (that fragile, context-dependent step is the launch wrapper's job).

The resolver helpers (`_real_pads`/`_supported`/`_ordered`) are reused by the
launch wrapper. v1 keys pads by vid:pid class (the user's pads are distinct
models); two pads of the same model collapse to one entry.
"""
from __future__ import annotations

from .. import localpolicy, proc_guard
from ..devices import sdl_devices
from ..policy import LOCAL
from .rpc import RpcError, method

# emulator key -> page facts. `players` = how many slots we manage.
_EMUS = {
    "eden":    {"label": "Eden",    "players": 2},
    "ryujinx": {"label": "Ryujinx", "players": 2},
}

# Not real player pads: Sinden guns (vid 16c0), the MAD wii-nav bridge (vid 4d41),
# and Steam's phantom virtual-gamepad pool (28de:11ff). Mirrors the selector
# filtering in routing/eden_cfg so the list shows only pads the user recognises.
_EXCLUDE_VID = {0x16C0, 0x4D41}
_EXCLUDE_VIDPID = {"28de:11ff"}

# Pads SDL's JOYSTICK layer enumerates but the emulator can't actually use —
# listing them as selectable would mislead, so they're shown as a "not supported"
# note instead of in the pick list. The Wii U Pro Controller (057e:0330) is
# Ryujinx-specific: verified on-device (Desktop Mode, no Steam Input) that Ryujinx
# sees the DS4 but NOT the Wii U Pro. EDEN, by contrast, DOES drive the Wii U Pro
# (user-confirmed from experience), so it is NOT unsupported there.
_UNSUPPORTED = {
    "ryujinx": {"057e:0330": "Wii U Pro controllers aren't supported by Ryujinx"},
}


def _emu(params) -> str:
    emu = params.get("emu", "")
    if emu not in _EMUS:
        raise RpcError("EINVAL", f"unknown emulator {emu!r}")
    return emu


def _real_pads(pump: bool = True):
    """Connected SDL joysticks that are real player pads, in SDL-index order.
    Two UNITS of the same model (same vid:pid, differing only by SDL index) are
    kept as SEPARATE pads, so e.g. two Wii U Pro controllers both appear and can
    drive Player 1 + Player 2.

    pump defaults True (OWNER) so the launch wrapper (switch_bind._resolve_pads)
    still drives hotplug and sees freshly-plugged pads; the deadline-bound
    pads.get RPC passes pump=False (reader mode — never block on the pumper)."""
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


def _pad_identity(d, pads) -> str:
    """Stable per-instance id used for priority storage. The lowest-SDL-index unit
    of a vid:pid keeps the bare vid:pid (back-compat with saved orders); each extra
    identical unit gets '<vidpid>#<rank>' (rank 2,3,… by index)."""
    rank = sorted(p.index for p in pads if p.vidpid == d.vidpid).index(d.index)
    return d.vidpid if rank == 0 else f"{d.vidpid}#{rank + 1}"


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


def _ordered(emu: str, pads: list, allpads: list | None = None):
    """Connected pads sorted by the stored priority (unknown pads appended in
    SDL-index order). `allpads` (default = `pads`) is the full set used to compute
    each pad's per-instance identity, so numbering is stable under filtering."""
    allpads = allpads if allpads is not None else pads
    stored = _stored_order(emu)
    prio = {pid: i for i, pid in enumerate(stored)}
    return sorted(pads, key=lambda d: (prio.get(_pad_identity(d, allpads), len(stored)), d.index))


def _supported(emu: str, pads: list):
    """Connected pads the emulator can actually use (drops known-unsupported
    classes like the Wii U Pro)."""
    unsup = _UNSUPPORTED.get(emu, {})
    return [d for d in pads if d.vidpid not in unsup]


@method("pads.get", slow=True, cache=("devices", "config"))
def _pads_get(params):
    emu = _emu(params)
    cfg = _EMUS[emu]
    unsup = _UNSUPPORTED.get(emu, {})
    real = _real_pads(pump=False)   # deadline-bound reader: never block on the pumper
    all_pads = _ordered(emu, real, real)
    pads = [d for d in all_pads if d.vidpid not in unsup]
    # Detected-but-unusable pads (shown as a note, NOT selectable) — e.g. the
    # Wii U Pro under Ryujinx, which SDL's gamepad layer can't drive.
    unsupported = [{"label": d.name or d.vidpid, "vidpid": d.vidpid,
                    "reason": unsup[d.vidpid]}
                   for d in all_pads if d.vidpid in unsup]
    rows = [{"id": _pad_identity(d, real), "label": d.name or d.vidpid, "vidpid": d.vidpid}
            for d in pads]
    run = proc_guard.emulator_running(emu)
    hands_off = _hands_off(emu)
    if run:
        note = f"Close {cfg['label']} first — it rewrites its config on exit."
    elif hands_off:
        note = f"Hands-off: {cfg['label']} uses its own controller config; MAD won't touch it."
    elif not rows and not unsupported:
        note = "No controllers connected."
    elif not rows:
        note = "No usable controllers connected."
    else:
        note = f"Top {cfg['players']} become Player 1/2 — reorder, then Apply."
    return {"emu": emu, "label": cfg["label"], "players": cfg["players"],
            "running": run, "hands_off": hands_off, "note": note, "pads": rows,
            "unsupported": unsupported}


@method("pads.set")
def _pads_set(params):
    """Store the priority order for an emulator. The order is applied at game
    launch by the ES-DE wrapper (`controller-router switch-bind`) — we do NOT
    write the emulator config here (that would bind a raw pad and break the
    on-the-go default). `localpolicy.dump` bumps staterev('config') so the page
    re-renders from truth."""
    emu = _emu(params)
    order = [str(x) for x in (params.get("order") or [])]
    _store_order(emu, order)
    return {"emu": emu, "order": order,
            "message": "Saved — applied when you launch a Switch game from ES-DE."}


@method("pads.hands_off")
def _pads_hands_off(params):
    """Toggle whether MAD manages this emulator's controllers at launch. ON = the
    emulator uses its own config (the launch wrapper skips bind+restore); OFF = MAD
    applies the stored pads→players order at launch."""
    emu = _emu(params)
    value = bool(params.get("value"))
    _set_hands_off(emu, value)
    label = _EMUS[emu]["label"]
    return {"emu": emu, "hands_off": value,
            "message": (f"Hands-off ON — {label} will use its own controller config."
                        if value else
                        f"Hands-off OFF — MAD applies your order when you launch {label}.")}
