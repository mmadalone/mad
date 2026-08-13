"""Resolve a Ryujinx (Switch) per-PLAYER x context input profile from policy.

WHY PER-PLAYER HERE AND PER-FAMILY EVERYWHERE ELSE. Ryujinx's named profiles
(``~/.config/Ryujinx/profiles/controller/<stem>.json``, authored in its own Input dialog) are FULL
``InputConfig`` objects, but Ryujinx itself throws the device identity away when it loads one --
``InputViewModel.LoadProfile`` sets ``config.Id = currentDeviceId`` and leaves ``LoadDevice()``
commented out, annotated "allows profiles to be applied to all controllers". A profile is therefore
a portable BUTTON LAYOUT, not a device pin, so "one profile = one player" is the honest key:

    [backends.ryujinx.profile_map.<docked|handheld>]
    p1 = "WiiU Pro 1"                 # value = profiles/controller/<stem>.json

That is the opposite of Eden/Citron (lib/yuzu_profiles), whose bindings embed the pad guid and whose
button indices differ per pad model, and of Cemu, which decides slot occupancy at launch.

The mapping subtree is BAKED into the player's ``input_config`` entry at launch, preserving the
slot's ``id`` / ``backend`` / ``player_index`` -- ``ryujinx_cfg.assign_devices`` still owns the
device seating, and rewrites only ``id``, so injecting then seating composes cleanly.
``player_N_profile_name`` is NOT written: it is provenance metadata Ryujinx does not resolve at boot.

Leaf module: imports only json/pathlib + lib.family_profiles (for the map lookup and the opt-in
handheld mirror), so the launch hot path stays cheap.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import family_profiles

# The MAPPING subtree of an InputConfig -- everything that describes HOW the pad plays, and nothing
# that describes WHICH pad it is. Moved verbatim from ryujinx_input_cmds._PROFILE_MAP_KEYS, whose
# page is being replaced by the picker. Note left_joycon_stick / right_joycon_stick are whole
# objects carrying joystick source, invert_stick_x/y, rotate90_cw and stick_button -- which is why
# the old page's six separate stick selectors are subsumed by picking a profile.
#
# "version" is deliberately NOT in this list. A saved profile file is a frozen snapshot (authored in
# Ryujinx's Input dialog on whatever schema version was current that day); the live Config.json slot
# carries whatever version Ryujinx itself last wrote, and Ryujinx migrates that number forward when it
# bumps its per-InputConfig schema. If "version" were baked in here, every launch would overwrite the
# live (possibly already-migrated) slot's version with the profile file's frozen one -- silently
# re-arming Ryujinx's input migration against already-migrated data on the NEXT launch after an
# upgrade, every single time, because Ryujinx rewrites Config.json on exit so the stale number just
# round-trips forever. This class of bug already bit once for real (the SDL2 -> SDL3 meaning change
# on a different field). deck-docs/ryubing-config.md's "Named input profiles" section (line ~192,
# not 192-196 -- the mapping-subtree paragraph runs to about line 209) documents this same exclusion;
# before phase 5 of the 2026-08-12 audit it instead listed "version" as an optional bracketed
# "[+ version]", which is the line this code stopped following, not something Ryujinx was ever
# source-verified to require a profile to supply.
MAP_KEYS = ("left_joycon_stick", "right_joycon_stick", "deadzone_left", "deadzone_right",
            "range_left", "range_right", "trigger_threshold", "motion", "rumble", "led",
            "left_joycon", "right_joycon")

SEATS = (1, 2, 3, 4)                  # the players MAD seats; widen here if ever needed
HANDHELD_SLOT = "Handheld"            # Ryujinx's own handheld-mode entry (NOT the Deck's screen)
SUFFIX = ".json"

_DEFAULT_CONFIG = "~/.config/Ryujinx/Config.json"


def seat_key(seat: int) -> str:
    """The policy key for a seat: 1 -> "p1". Prefixed so it never reads as the legacy numeric
    slot_profiles tables."""
    return f"p{int(seat)}"


def player_index(seat: int) -> str:
    """The Config.json player_index for a seat: 1 -> "Player1"."""
    return f"Player{int(seat)}"


def profiles_dir(be_cfg: dict | None = None) -> Path:
    """``profiles/controller`` beside Ryujinx's Config.json, so a redirected/portable tree works."""
    cfg = _DEFAULT_CONFIG
    if isinstance(be_cfg, dict):
        v = be_cfg.get("config_file")
        if isinstance(v, str) and v.strip():
            cfg = v.strip()
    return Path(cfg).expanduser().parent / "profiles" / "controller"


def list_stems(pdir) -> list[str]:
    """Sorted valid stems of ``<pdir>/*.json``; ``[]`` when absent/unreadable."""
    return family_profiles.list_stems(pdir, SUFFIX)


def profile_path(pdir, stem) -> Path | None:
    """``<pdir>/<stem>.json`` when ``stem`` is safe and the file exists, else ``None``."""
    if not family_profiles.valid_stem(stem):
        return None
    try:
        p = Path(pdir).expanduser() / f"{stem.strip()}{SUFFIX}"
        return p if p.is_file() else None
    except OSError:
        return None


def profile_for(be_cfg: dict, seat: int, context: str) -> str | None:
    """The profile stem picked for ``seat`` in ``context``, or ``None`` when unset. Honours the
    opt-in ``handheld_mirrors_docked`` flag exactly as the family backends do."""
    return family_profiles.resolve(be_cfg, seat_key(seat), context)


def seat_mapping(path, pidx: str) -> dict | None:
    """The bakeable mapping subtree of the profile at ``path`` for the slot ``pidx``, or ``None``
    when the file is missing/unreadable/not a profile or carries no mapping at all.

    Never copies ``id`` / ``backend`` / ``player_index`` / ``name`` -- the slot keeps its own device
    identity, which is what makes a profile portable across pads.

    CONTROLLER TYPE, clamped BOTH WAYS: a profile records the controller type its author chose in
    Ryujinx's GUI, and baking it is what keeps JoyCon Pair / single JoyCon reachable once the
    per-button editor is gone. But the type and the slot must agree:
      * ``Handheld`` is only meaningful on the Handheld slot -- on a PlayerN entry it yields a slot
        Ryujinx does not expect.
      * Equally, a NON-Handheld type must never land ON the Handheld slot. ``seat_mappings`` mirrors
        Player 1's mapping onto that slot (it follows P1's pad), and every profile a user normally
        authors is ``ProController`` -- so a one-directional clamp silently rewrote the Handheld
        entry's type on the feature's MAIN path. Ryujinx keys its "drop the Handheld config when
        docked" branch on that type, so the entry survived docking and one physical pad drove both
        Player 1 and the handheld npad; single-player titles then fail Npad validation and loop on
        the "invalid controller configuration" dialog.
    Hence: keep the type only when its Handheld-ness matches the slot's."""
    try:
        prof = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prof, dict):
        return None
    out = {k: prof[k] for k in MAP_KEYS if k in prof}
    ct = prof.get("controller_type")
    if isinstance(ct, str) and ct and (ct == HANDHELD_SLOT) == (pidx == HANDHELD_SLOT):
        out["controller_type"] = ct
    return out or None


def seat_mappings(be_cfg: dict, context: str, count: int | None, pdir) -> dict:
    """``{player_index: mapping}`` for ``ryujinx_cfg.assign_devices(profiles=...)``.

    Only seats that will actually be occupied are resolved (``count`` = the number of pads the
    binder resolved), so an unpicked or absent seat is left exactly as it rests.

    The Handheld slot mirrors Player 1's mapping, because ``ryujinx_cfg.assign_devices`` points that
    slot at Player 1's physical pad. Mirroring means the two agree whichever entry Ryujinx actually
    consults in handheld console mode, so this is correct without depending on that answer."""
    out: dict = {}
    limit = len(SEATS) if count is None else max(0, min(int(count), len(SEATS)))
    p1_path = None
    for seat in SEATS[:limit]:
        stem = profile_for(be_cfg, seat, context)
        if not stem:
            continue
        path = profile_path(pdir, stem)
        if path is None:
            continue                             # stale pick -> leave that seat resting
        pidx = player_index(seat)
        m = seat_mapping(path, pidx)
        if m:
            out[pidx] = m
            if seat == 1:
                p1_path = path
    if p1_path is not None:
        hm = seat_mapping(p1_path, HANDHELD_SLOT)
        if hm:
            out[HANDHELD_SLOT] = hm
    return out
