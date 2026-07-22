"""Ryujinx device assignment — which physical pad drives each player.

Sets each player's `id` in `Config.json` `input_config[]` while PRESERVING the per-button
`left_joycon`/`right_joycon` maps (so MAD per-button remaps survive — only the device changes), and
mirrors the same ids into `player_input_assignments[]` so the two device layers stay in lockstep.
Configure-once: written from MAD (launch wrapper re-applies, since SDL indices can shift).

Ryujinx matches a configured controller by `id = "{leading}-{guid}"`, where `guid` is the .NET Guid
string of the 16-byte SDL GUID (first three fields little-endian, last eight as-is) with the SDL
name-CRC (bytes 2-3) ZEROED. On the current SDL3 build the leading number is NOT the SDL enumeration
index (that was SDL2's meaning) — it is a PER-GUID DUPLICATE RANK: SDL3.GenerateGamepadId assigns 0
to the first device of each distinct CRC-zeroed GUID and increments only for identical-GUID
duplicates. So distinct-model pads all get 0 (the common multi-pad case); only true duplicates get
1,2,… Using the raw SDL index (the old code) made any non-first-enumerated / 2nd-model pad fail to
match. The `backend` token is preserved (both GamepadSDL2 and GamepadSDL3 are accepted and matching
is by id, so we never risk writing a token an older build cannot parse). See
deck-docs/ryubing-config.md.
"""
from __future__ import annotations

import copy
import re

from . import ryujinx_json


def _guid_string(sdl_guid: str) -> str:
    """The CRC-zeroed .NET Guid portion of a Ryujinx gamepad id (everything after the '<n>-')."""
    b = bytearray(bytes.fromhex(sdl_guid))
    if len(b) != 16:
        raise ValueError(f"SDL GUID must be 16 bytes, got {sdl_guid!r}")
    # Ryujinx ZEROES the SDL name-CRC (GUID bytes 2-3) when forming its gamepad id (both SDL2 and
    # SDL3 do this), so we must too — sdl_devices() returns the CRC-bearing GUID.
    b[2] = b[3] = 0
    d1 = int.from_bytes(b[0:4], "little")
    d2 = int.from_bytes(b[4:6], "little")
    d3 = int.from_bytes(b[6:8], "little")
    return f"{d1:08x}-{d2:04x}-{d3:04x}-{b[8:10].hex()}-{b[10:16].hex()}"


def ryujinx_id(leading: int, sdl_guid: str) -> str:
    """Ryujinx GamepadId: ``{leading}-{CRC-zeroed .NET Guid}``. `leading` is the per-GUID duplicate
    rank (SDL3), NOT the raw SDL enumeration index."""
    return f"{leading}-{_guid_string(sdl_guid)}"


def _rank_ids(players) -> dict:
    """id(device) -> its Ryujinx id, ranking same-GUID devices by ascending SDL index (SDL3 assigns
    the leading number as a per-GUID duplicate counter in connection order, which the SDL index
    approximates). Distinct models therefore all get rank 0; only identical-model duplicates get
    1,2,… (best-effort for the duplicate case, exact for the common distinct-model case)."""
    rank_of: dict = {}
    seen: dict = {}
    for d in sorted(players, key=lambda x: x.index):
        g = _guid_string(d.guid)
        r = seen.get(g, 0)
        rank_of[id(d)] = r
        seen[g] = r + 1
    return {id(d): ryujinx_id(rank_of[id(d)], d.guid) for d in players}


def _find(ics: list, pidx: str):
    return next((ic for ic in ics if ic.get("player_index") == pidx), None)


_PLAYER_NUM_RE = re.compile(r"^Player(\d+)$")


def _player_num(pidx) -> int | None:
    """The N in ``PlayerN`` (1-based), or ``None`` for ``Handheld`` / any non-numbered index."""
    m = _PLAYER_NUM_RE.match(pidx) if isinstance(pidx, str) else None
    return int(m.group(1)) if m else None


def _drop_surplus(entries: list, bound: int) -> tuple[list, bool]:
    """Return ``(kept, changed)`` dropping every ``PlayerN`` with ``N > bound``. ``Handheld`` and any
    non-numbered index (``_player_num`` -> None -> 0, never > bound>=1) are kept. Applied to
    input_config AND player_input_assignments INDEPENDENTLY -- each list is filtered on ITS OWN player
    numbers, never on the other's slot set, so a stale entry that exists in only one list is still
    neutralized (a PIA-only surplus slot cannot survive carrying a bound pad's id)."""
    kept = [e for e in entries if (_player_num(e.get("player_index")) or 0) <= bound]
    return kept, len(kept) != len(entries)


def _sync_pia(data: dict, assigned_ids) -> None:
    """Keep player_input_assignments in lockstep with input_config so the two device layers agree.
    input_config[].id is authoritative while enable_dynamic_input_swap is false, but a STALE
    assignment id would bypass MAD if dynamic swap is ever toggled — so mirror each bound player's id
    here too. Upsert ONLY when the config already uses PIA (don't introduce the structure to a config
    that lacks it). `assigned_ids` = [(player_index, ryujinx_id), …] for the bound players (Handheld
    is excluded — it has no PIA entry)."""
    pias = data.get("player_input_assignments")
    if not isinstance(pias, list):
        return
    by_player = {p.get("player_index"): p for p in pias if isinstance(p, dict)}
    for pidx, rid in assigned_ids:
        entry = by_player.get(pidx)
        if entry is None:
            entry = {"player_index": pidx}
            pias.append(entry)
            by_player[pidx] = entry
        entry["enable_dynamic_input_swap"] = False
        entry["devices"] = [{"type": "Controller", "id": rid, "profile_name": None}]


def assign_devices(players, config_path=None) -> dict:
    """Assign ``players[0]`` → Player 1 (and Handheld), ``players[1]`` → Player 2, … by rewriting each
    entry's ``id`` (per-GUID rank) while leaving its joycon button maps + backend + every non-input
    setting untouched. ``players`` is a list of ``devices.SdlDevice`` (needs ``.index`` + ``.guid``).
    A missing Player-N entry is created by cloning Player 1 (same button layout + backend). Raises
    ValueError if there is no Player 1 entry to base on. ``config_path`` targets a specific config
    (e.g. a per-game ``games/<titleid>/Config.json``); defaults to global. player_input_assignments
    is kept in lockstep with the written ids.

    SURPLUS SLOTS: any ``Player N`` beyond the pads we bind is DROPPED (input_config + PIA), so a
    leftover higher slot cannot keep an id that collides with a bound pad and make one physical pad
    drive two players. Transient -- the launch wrapper snapshots the full resting config first and
    reverts it on game-end, so a richer multi-player config returns on exit."""
    if not players:
        raise ValueError("no controller to assign")
    data = ryujinx_json.load(config_path)
    ics = data.get("input_config")
    if not isinstance(ics, list):
        ics = []
        data["input_config"] = ics
    p1 = _find(ics, "Player1")
    if p1 is None:
        raise ValueError("Ryujinx has no Player 1 controller yet — open Ryujinx "
                         "once, add a controller, then set the order here")

    ids = _rank_ids(players)                  # SDL3 per-GUID-rank ids
    assigned: list[tuple[str, object]] = []
    p1["id"] = ids[id(players[0])]            # backend preserved (both SDL2/SDL3 route by id)
    assigned.append(("Player1", players[0]))
    hh = _find(ics, "Handheld")               # handheld mode follows P1's pad (no PIA entry)
    if hh is not None:
        hh["id"] = ids[id(players[0])]

    for n in range(1, len(players)):
        pidx = f"Player{n + 1}"
        entry = _find(ics, pidx)
        if entry is None:                     # clone P1's layout + backend for a new player slot
            entry = copy.deepcopy(p1)
            entry["player_index"] = pidx
            ics.append(entry)
        entry["id"] = ids[id(players[n])]
        assigned.append((pidx, players[n]))

    # Drop SURPLUS player slots -- any PlayerN beyond the pads we just bound. Ryujinx matches a
    # controller purely by id and has NO per-slot "connected" flag (unlike Eden/Citron, which stamp
    # connected=false on every unbound slot), so a leftover higher slot that still carries a bound
    # pad's id makes ONE physical pad drive TWO players (a phantom player). A player with no entry
    # simply has no controller (a fresh Ryujinx config is Player1-only), so removing the surplus
    # entries is the idiomatic, collision-proof neutralization. Handheld + Player1..len(players) are
    # kept. input_config AND player_input_assignments are each filtered on their OWN player numbers
    # (never on the other's slot set), so an asymmetric config where PIA holds a PlayerN input_config
    # lacks cannot leave a stale bound id on that PIA slot.
    #   TRANSIENT: bind() snapshots the full resting input_config (+ PIA) BEFORE this call and the
    # game-end restore reverts it, so a richer multi-player config returns on exit.
    #   TRADE-OFF vs Eden/Citron's connected=false: deleting (not just disabling) the entry is lossier
    # under the rare double fault of a crash (game-end restore never runs) PLUS a torn/corrupt sidecar
    # -- then the deleted higher slots are gone until re-configured, recoverable only from the one-time
    # .router-backup (ryujinx_json.write). Accepted because Ryujinx offers no disable-in-place flag.
    bound = len(players)
    kept_ic, ic_changed = _drop_surplus(ics, bound)
    if ic_changed:
        data["input_config"] = kept_ic
    pias = data.get("player_input_assignments")
    if isinstance(pias, list):
        kept_pia, pia_changed = _drop_surplus(pias, bound)
        if pia_changed:
            data["player_input_assignments"] = kept_pia

    _sync_pia(data, [(pidx, ids[id(d)]) for pidx, d in assigned])
    ryujinx_json.write(data, config_path)
    return {"assigned": [(pi, d.vidpid) for pi, d in assigned]}
