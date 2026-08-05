"""Resolve a Cemu (Wii U) family x context input profile name from policy.

The family x context map lives in ``[backends.cemu.profile_map.<context>]`` as
``{family: "<profile stem>"}`` (see controller-policy.toml). This leaf answers the one
question the launch binder (lib/cemu_seat) and the MAD editor ask: "which native
``controllerProfiles/<stem>.xml`` is assigned to this controller FAMILY in this launch
context?" An unset / blank / absent entry returns ``None`` = leave that slot's resting
file untouched (never cleared).

PER-GAME (2026-08-04): a title may override any family in either context via
``[backends.cemu.pergame.<titleid>.<context>]``, the same ``{family: stem}`` shape. The launch
binder resolves the title id from the rom (cemu_games.titleid_for_rom) and hands the slice in;
an unknown title, or no per-game table, degrades to the global map. This deliberately does NOT
write Cemu's native ``gameProfiles/<tid>.ini [Controller]`` pin, which BYPASSES seating entirely
(see lib/madsrv/cemu_pg_input_cmds) -- the per-game pick feeds cemu_seat instead.

Family keys are the canonical ``routing.family_of`` names (DualSense, DualShock 4,
Wii Remote Pro, Steam Deck, 8BitDo, 8BitDo Pro, Xbox). Context is "docked" | "handheld".

The map lookup, the opt-in handheld mirror and the nth-same-family-pad derivation live in
lib/family_profiles (shared with the Yuzu-fork Switch emulators, which key their profiles the
same way and for the same reason). This module adds only the Cemu flavour: the ``.xml`` suffix
and the per-game tier.

Leaf module: imports only lib.handheld_input + lib.family_profiles, so the launch
hot path and hook-side CLIs stay cheap.
"""
from __future__ import annotations

from . import family_profiles, handheld_input

SUFFIX = ".xml"                       # Cemu's native controllerProfiles/<stem>.xml


def _lookup(cemu_cfg: dict, family: str, ctx: str) -> str | None:
    """The stem assigned to ``family`` in the ``ctx`` slice of profile_map, or ``None``. Husk-tolerant
    (a non-dict profile_map / slice, or a non-string value, degrades to ``None`` on the launch path).
    Kept as a name because callers and tests reference it."""
    return family_profiles.lookup(cemu_cfg, family, ctx)


def _pergame_lookup(pergame, family: str, ctx: str) -> str | None:
    """``pergame[ctx][family]``, husk-tolerant in exactly the same way as the global lookup."""
    if not isinstance(pergame, dict):
        return None
    slice_ = pergame.get(ctx)
    if not isinstance(slice_, dict):
        return None
    name = slice_.get(family)
    if not isinstance(name, str):
        return None
    return name.strip() or None


def pergame_slice(cemu_cfg: dict, titleid: str | None) -> dict:
    """``[backends.cemu.pergame.<titleid>]`` as a ``{context: {family: stem}}`` dict, or ``{}`` when
    the title id is absent / unknown / husked. Title ids are stored lowercase 16-hex, matching
    cemu_games.pergame_path and the res_presets store."""
    if not titleid or not isinstance(cemu_cfg, dict):
        return {}
    pg = cemu_cfg.get("pergame")
    if not isinstance(pg, dict):
        return {}
    entry = pg.get(str(titleid).strip().lower())
    return entry if isinstance(entry, dict) else {}


def seat_key(seat: int) -> str:
    """The policy key for an external player: 1 -> "p1". Same shape, and the same table, as
    lib/ryujinx_profiles.seat_key -- family_profiles.lookup is key-agnostic, so a player row and a
    family row live side by side in ``profile_map.<context>`` without a second store.

    The number is the EXTERNAL player, matching ``[systems.wiiu].ports`` and the Device pins table,
    so "Player 2" means the same pad on both pages. The Deck's own GamePad seat is not a player: it
    stays on the "Steam Deck" family row, which is also what keeps the GamePad-vs-Pro profile gate
    working without a slot-aware rule on the page."""
    return f"p{int(seat)}"


def _resolve_keyed(pergame, cemu_cfg: dict, keys: list, ctx: str):
    """``(stem, winning key)`` for the first hit, or ``(None, None)``.

    SCOPE-MAJOR, then key-major within a scope: everything a title says beats everything the global
    map says, and inside one scope a PLAYER row beats the pad-TYPE row. Scope has to be the outer
    axis because "per-game beats all-games" is the promise every per-game page in MAD makes -- with
    the axes the other way round, a global player row would quietly override a pick the user made
    for one game specifically.

    The opt-in ``handheld_mirrors_docked`` tier runs only for a HANDHELD launch whose handheld chain
    missed entirely, and repeats the same two scopes against DOCKED. Without the flag, handheld never
    inherits docked at all."""
    def _scan(where):
        for k in keys:
            name = (_pergame_lookup(pergame, k, where) if _scan.pg
                    else family_profiles.lookup(cemu_cfg, k, where))
            if name:
                return name, k
        return None, None

    for where in (ctx, "docked") if _mirrors(cemu_cfg, ctx) else (ctx,):
        for _scan.pg in (True, False):
            name, key = _scan(where)
            if name:
                return name, key
    return None, None


def _mirrors(cemu_cfg: dict, ctx: str) -> bool:
    return (ctx == "handheld" and isinstance(cemu_cfg, dict)
            and bool(cemu_cfg.get(family_profiles.MIRROR_KEY)))


def _keys(family: str | None, seat: int | None) -> list:
    """The keys to try, best first: the player row, then the pad-type row."""
    return ([seat_key(seat)] if seat else []) + ([family] if family else [])


def resolve(pergame, cemu_cfg: dict, family: str | None, context: str,
            seat: int | None = None) -> str | None:
    """The stem to apply for this launch, or None to leave the slot's resting file untouched.

    The pad-TYPE tier is the fallback for a player row left unset, which is what makes adopting
    players gradual: an untouched Deck resolves exactly as it did before players existed."""
    return _resolve_keyed(pergame, cemu_cfg, _keys(family, seat),
                          handheld_input.normalize(context))[0]


def resolve_nth(pergame, cemu_cfg: dict, family: str | None, context: str,
                ordinal: int, cfg_dir, seat: int | None = None) -> str | None:
    """``resolve`` for the ``ordinal``-th connected pad of ``family`` (0-based).

    The "<base> 2" bump exists so two pads of ONE TYPE get distinct device-bound profiles, so it
    applies only when the pad-TYPE row won. A player pick already names one seat, and bumping it
    would silently load "DualSense 2" for a Player 2 row that plainly says "DualSense 1"."""
    name, key = _resolve_keyed(pergame, cemu_cfg, _keys(family, seat),
                               handheld_input.normalize(context))
    if name is None or (seat and key == seat_key(seat)):
        return name
    return family_profiles.nth(name, ordinal, cfg_dir, SUFFIX)


def profile_for(cemu_cfg: dict, family: str | None, context: str) -> str | None:
    """The native profile stem assigned to ``family`` in ``context``
    ("docked"|"handheld"), or ``None`` when unset / blank / absent.

    ``cemu_cfg`` is the merged ``[backends.cemu]`` table. Tolerates a hand-edited
    husk (a non-dict profile_map / context slice, or a non-string value) by
    degrading to ``None`` rather than raising on the launch path.

    HANDHELD MIRROR: when ``[backends.cemu].handheld_mirrors_docked`` is set AND a
    HANDHELD family has no handheld entry, fall back to that family's DOCKED entry
    (opt-in "same as docked"). Default off = today's stock fallback, so with the
    flag absent this is byte-identical and the seating path is unchanged.

    The GLOBAL-only entry point (no per-game tier) -- callers that have a title in scope use
    ``resolve``. Kept so the MAD pages and the Preview, which are system-level, stay unchanged.
    """
    return resolve(None, cemu_cfg, family, context)


def profile_for_nth(cemu_cfg: dict, family: str | None, context: str,
                    ordinal: int, cfg_dir) -> str | None:
    """The profile for the ``ordinal``-th connected pad of ``family`` (0-based), so two same-family
    pads use DISTINCT device-bound profiles instead of both reusing the first.

    The map holds ONE stem per family = the FIRST pad's profile (e.g. "DualSense 1"). For the
    ordinal-th pad we auto-derive "<base> <n+ordinal>" by bumping the trailing number
    ("DualSense 1" -> "DualSense 2" for the 2nd DualSense, "WiiU Pro 1" -> "WiiU Pro 2", ...). Falls
    back to the base profile when ordinal 0, when the base has no trailing number, or when the derived
    file does not exist -- so a user who only has one profile per family, or non-numbered names, keeps
    today's behaviour. ``cfg_dir`` is the controllerProfiles dir (the caller passes it to keep this a
    leaf module). GLOBAL-only; the per-game twin is ``resolve_nth``."""
    return resolve_nth(None, cemu_cfg, family, context, ordinal, cfg_dir)
