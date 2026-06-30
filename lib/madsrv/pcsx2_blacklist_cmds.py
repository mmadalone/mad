"""pcsx2blacklist.* — per-emulator "device visibility" editor for PCSX2 (PS2).

PCSX2 numbers controllers by SDL PLAYER index, and non-gamepad joysticks (the Sinden
guns, the MAD Wii-Nav bridge) take a player index in the same namespace and shift the
real pads, so the binder lands Player 1 on the wrong device. Those non-pads are hidden
from PCSX2 at launch via `SDL_JOYSTICK_BLACKLIST_DEVICES` (see mad-standalone-launch.py),
after which the real gamepads keep clean player indices.

This module lets the user configure WHICH connected devices are hidden, from the
Standalones -> PS2 tile (the C++ "Device visibility" page).

SMART DEFAULT (no stored config): hide only the non-gamepad noise — vid in
`pads_cmds._EXCLUDE_VID` (Sinden 0x16C0 + Wii-Nav 0x4D41). EVERY real gamepad (X-Arcade,
Steam Deck, PlayStation pads, 8BitDo, ...) stays visible and selectable; the binder
writes each chosen pad's real player number, so an unselected gamepad is simply not bound
rather than hidden. The user's per-device overrides are stored in
controller-policy.local.toml `[standalone_blacklist].<emu>` as a list of vid:pid tokens:
a bare ``"vvvv:pppp"`` force-HIDES that class, a ``"~vvvv:pppp"`` force-SHOWS it (overriding
the default). The stored list only ever holds DEVIATIONS from the default, so a newly
plugged gun is hidden by default while the user's explicit choices persist.

The effective hidden set is APPLIED AT GAME LAUNCH by the ES-DE wrapper (it reads
`blacklist_env`); this module only READS devices + STORES the overrides.
"""
from __future__ import annotations

from .. import localpolicy
from ..devices import sdl_devices
from ..policy import LOCAL
from . import pads_cmds
from .rpc import RpcError, method

_SHOW = "~"     # prefix on a stored token = force-SHOW (override the default hide)


def _stored(emu: str) -> list[str]:
    data = localpolicy.load(LOCAL)
    return [str(x) for x in ((data.get("standalone_blacklist") or {}).get(emu) or [])]


def _store(emu: str, tokens: list[str]) -> None:
    data = localpolicy.load(LOCAL)
    bl = data.setdefault("standalone_blacklist", {})
    if tokens:
        bl[emu] = list(tokens)
    else:
        bl.pop(emu, None)               # prune empty per-emu entry
        if not bl:
            data.pop("standalone_blacklist", None)   # prune empty table
    localpolicy.dump(LOCAL, data)        # bumps staterev('config') -> page re-renders


def _default_hidden(vidpid: str) -> bool:
    """The smart default: the non-gamepad noise (Sinden guns, Wii-Nav) is hidden; every
    real gamepad is visible."""
    try:
        vid = int(vidpid.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return vid in pads_cmds._EXCLUDE_VID


def is_hidden(emu: str, vidpid: str, stored: list[str] | None = None) -> bool:
    """Effective hidden state for a vid:pid class: the user's force-show wins, else the
    user's force-hide, else the smart default (noise hidden)."""
    toks = _stored(emu) if stored is None else stored
    if _SHOW + vidpid in toks:
        return False
    if vidpid in toks:
        return True
    return _default_hidden(vidpid)


def hidden_vidpids(emu: str) -> list[str]:
    """The DISTINCT vid:pid classes hidden among CONNECTED devices (connected-only so we
    never blacklist a class that is not present). Enumerated via EVDEV, NOT SDL, so the
    launch wrapper can compute the blacklist BEFORE SDL is initialised — the
    SDL_JOYSTICK_BLACKLIST_DEVICES hint is only read at SDL's first init, so it must be set
    before our own first sdl_devices() call for our enumeration to match PCSX2's."""
    from ..devices import enumerate_devices, vidpid as _vidpid
    toks = _stored(emu)
    seen: set[str] = set()
    out: list[str] = []
    for d in enumerate_devices():
        vp = _vidpid(d)
        if not vp or vp in seen:
            continue
        seen.add(vp)
        if is_hidden(emu, vp, toks):
            out.append(vp)
    return out


def blacklist_env(emu: str) -> str:
    """The `SDL_JOYSTICK_BLACKLIST_DEVICES` value (`0xVID/0xPID,...`) for the launch
    wrapper; "" when nothing is hidden. Reuses sdl_filter._fmt for the exact format."""
    from ..sdl_filter import _fmt
    return _fmt(hidden_vidpids(emu))


def _emu(params) -> str:
    emu = params.get("emu", "")
    if emu not in pads_cmds._EMUS:
        raise RpcError("EINVAL", f"unknown emulator {emu!r}")
    return emu


@method("pcsx2blacklist.get", slow=True, cache=("devices", "config"))
def _get(params):
    """One row per CONNECTED joystick (incl. the guns/Wii-Nav), with its effective
    hidden state, for the Device-visibility toggle list."""
    emu = _emu(params)
    toks = _stored(emu)
    real = sdl_devices(pump=True)               # EVERY joystick PCSX2 sees (no _EXCLUDE filter)
    labels = pads_cmds._pad_labels(real)        # port-aware friendly names (X-Arcade, KNOWN_PADS)
    seen: set[str] = set()
    rows = []
    for d in real:
        vp = d.vidpid
        if vp in seen:
            continue
        seen.add(vp)
        rows.append({"id": vp, "vidpid": vp, "connected": True,
                     "hidden": is_hidden(emu, vp, toks),
                     "label": labels.get(d.index) or d.name or vp})
    note = ("Hidden devices are invisible to PCSX2 at launch. Light guns and the Wii-Nav "
            "bridge are hidden by default so your real pads number correctly; toggle any "
            "device on or off.")
    return {"emu": emu, "label": pads_cmds._EMUS[emu]["label"], "note": note, "devices": rows}


@method("pcsx2blacklist.set")
def _set(params):
    """Toggle one device hidden/visible. Stores only DEVIATIONS from the default, so the
    smart default keeps applying to devices the user never touched."""
    emu = _emu(params)
    vp = str(params.get("vidpid") or "")
    if ":" not in vp:
        raise RpcError("EINVAL", f"bad vidpid {vp!r}")
    hidden = bool(params.get("hidden"))
    toks = [t for t in _stored(emu) if t not in (vp, _SHOW + vp)]   # clear any prior override
    if hidden != _default_hidden(vp):              # only persist a real deviation
        toks.append(vp if hidden else _SHOW + vp)
    _store(emu, toks)
    return {"emu": emu, "vidpid": vp, "hidden": hidden,
            "message": f"Saved — applied when you launch {pads_cmds._EMUS[emu]['label']} from ES-DE."}
