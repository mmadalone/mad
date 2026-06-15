"""pads.* — per-emulator "pads → players" device assignment (configure-once).

The MAD page (Standalones → Switch → Eden/Ryujinx → Controllers) lets the user
order the connected pads; on **Apply** we resolve the top-N connected pads by
that order to player slots and write the EMULATOR's own config device bindings,
PRESERVING any per-button remaps. The priority order is stored per emulator in
controller-policy.local.toml (`[standalone_pads].<emu>`). No router / launch-time
involvement — the write happens now (the user chose configure-once over dynamic
launch-time routing).

v1 keys pads by vid:pid class (the user's pads are distinct models); two pads of
the same model collapse to one entry. Re-apply after changing which pads are
connected (Ryujinx ids carry the live SDL index — see ryujinx_cfg).
"""
from __future__ import annotations

from .. import eden_cfg, localpolicy, proc_guard, staterev
from ..devices import sdl_devices
from ..policy import LOCAL
from . import ryujinx_cfg
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


def _emu(params) -> str:
    emu = params.get("emu", "")
    if emu not in _EMUS:
        raise RpcError("EINVAL", f"unknown emulator {emu!r}")
    return emu


def _real_pads():
    """Connected SDL joysticks that are real player pads, first device per vid:pid
    class (v1 keys pads by class), in SDL-index order."""
    out = []
    seen: set[str] = set()
    for d in sdl_devices():
        try:
            vid = int(d.vidpid.split(":")[0], 16)
        except (ValueError, IndexError):
            continue
        if vid in _EXCLUDE_VID or d.vidpid in _EXCLUDE_VIDPID or d.vidpid in seen:
            continue
        seen.add(d.vidpid)
        out.append(d)
    return out


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


def _ordered(emu: str, pads: list):
    """Connected pads sorted by the stored priority (unknown pads appended in
    SDL-index order)."""
    stored = _stored_order(emu)
    prio = {vp: i for i, vp in enumerate(stored)}
    return sorted(pads, key=lambda d: (prio.get(d.vidpid, len(stored)), d.index))


@method("pads.get", slow=True, cache=("devices", "config"))
def _pads_get(params):
    emu = _emu(params)
    cfg = _EMUS[emu]
    pads = _ordered(emu, _real_pads())
    rows = [{"id": d.vidpid, "label": d.name or d.vidpid, "vidpid": d.vidpid}
            for d in pads]
    run = proc_guard.emulator_running(emu)
    if run:
        note = f"Close {cfg['label']} first — it rewrites its config on exit."
    elif not rows:
        note = "No controllers connected."
    else:
        note = f"Top {cfg['players']} become Player 1/2 — reorder, then Apply."
    return {"emu": emu, "label": cfg["label"], "players": cfg["players"],
            "running": run, "note": note, "pads": rows}


@method("pads.set", slow=True)
def _pads_set(params):
    emu = _emu(params)
    cfg = _EMUS[emu]
    order = [str(x) for x in (params.get("order") or [])]
    if proc_guard.emulator_running(emu):
        raise RpcError("EBUSY", f"close {cfg['label']} first — it rewrites its "
                                "config on exit")
    pads = _real_pads()
    if not pads:
        raise RpcError("EINVAL", "no controllers connected")
    _store_order(emu, order)
    chosen = _ordered(emu, pads)[:cfg["players"]]
    try:
        if emu == "eden":
            eden_cfg.assign_devices(chosen)
        else:
            ryujinx_cfg.assign_devices(chosen)
    except (OSError, ValueError) as e:
        raise RpcError("ENOENT", str(e))
    staterev.bump("config")
    assigned = [{"player": i + 1, "label": d.name or d.vidpid, "vidpid": d.vidpid}
                for i, d in enumerate(chosen)]
    msg = "Set " + ", ".join(f"P{a['player']}={a['label']}" for a in assigned)
    return {"emu": emu, "assigned": assigned, "message": msg}
