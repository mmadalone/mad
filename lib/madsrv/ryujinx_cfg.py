"""Ryujinx device assignment — which physical pad drives each player.

Sets each player's `id` in `Config.json` `input_config[]` while PRESERVING the
per-button `left_joycon`/`right_joycon` maps (so MAD per-button remaps survive —
only the device changes). Configure-once: written now from MAD, no router /
launch-time involvement.

Ryujinx matches a configured controller by `id = "{sdl_index}-{guid}"`, where
`guid` is the **.NET `Guid` string** of the 16-byte SDL GUID (first three fields
little-endian, last eight bytes as-is). Its `SDL2GamepadDriver.GetGamepad(id)`
re-derives the id from the joystick AT the parsed index and returns null on
mismatch — so the id MUST carry the device's *current* SDL index. Verified
against live ids (e.g. `28de:1205` SDL GUID `03000000de28000005120000…` →
`0-00000003-28de-0000-0512-000000026800`). If the connected set changes the SDL
index can shift, so the order must be re-applied (the configure-once contract).
"""
from __future__ import annotations

import copy

from . import ryujinx_json


def ryujinx_id(index: int, sdl_guid: str) -> str:
    """Ryujinx GamepadId for a device: ``{index}-{.NET Guid of the SDL GUID}``."""
    b = bytearray(bytes.fromhex(sdl_guid))
    if len(b) != 16:
        raise ValueError(f"SDL GUID must be 16 bytes, got {sdl_guid!r}")
    # Ryujinx ZEROES the SDL name-CRC (GUID bytes 2-3) when forming its gamepad id,
    # so we must too — `sdl_devices()` returns the CRC-bearing GUID, and keeping the
    # CRC makes the id never match what Ryujinx enumerates. Verified against a
    # Ryujinx-written DS4 id (054c:09cc -> 0-00000003-054c-0000-cc09-...).
    b[2] = b[3] = 0
    d1 = int.from_bytes(b[0:4], "little")
    d2 = int.from_bytes(b[4:6], "little")
    d3 = int.from_bytes(b[6:8], "little")
    return f"{index}-{d1:08x}-{d2:04x}-{d3:04x}-{b[8:10].hex()}-{b[10:16].hex()}"


def _find(ics: list, pidx: str):
    return next((ic for ic in ics if ic.get("player_index") == pidx), None)


def assign_devices(players, config_path=None) -> dict:
    """Assign ``players[0]`` → Player 1 (and Handheld), ``players[1]`` → Player 2,
    … by rewriting each entry's ``id`` (and ``backend``). ``players`` is a list of
    ``devices.SdlDevice`` (needs ``.index`` + ``.guid``). Every entry's joycon
    button maps — and every non-input setting — are left untouched. A missing
    Player 2 entry is created by cloning Player 1 (same button layout). Raises
    ValueError if there is no Player 1 entry to base on (the user must add a
    controller in Ryujinx once first). ``config_path`` targets a specific config
    file (e.g. a per-game ``games/<titleid>/Config.json``); defaults to global."""
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

    assigned: list[tuple[str, object]] = []
    rid = ryujinx_id(players[0].index, players[0].guid)
    p1["id"] = rid
    p1["backend"] = "GamepadSDL2"
    assigned.append(("Player1", players[0]))
    hh = _find(ics, "Handheld")          # handheld mode follows P1's pad
    if hh is not None:
        hh["id"] = rid
        hh["backend"] = "GamepadSDL2"

    for n in range(1, len(players)):
        pidx = f"Player{n + 1}"
        entry = _find(ics, pidx)
        if entry is None:                # clone P1's layout for a new player slot
            entry = copy.deepcopy(p1)
            entry["player_index"] = pidx
            ics.append(entry)
        entry["id"] = ryujinx_id(players[n].index, players[n].guid)
        entry["backend"] = "GamepadSDL2"
        assigned.append((pidx, players[n]))

    ryujinx_json.write(data, config_path)
    return {"assigned": [(pi, d.vidpid) for pi, d in assigned]}
