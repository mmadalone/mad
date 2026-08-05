"""eden_input_docked / eden_input_handheld / citron_input_docked / citron_input_handheld —
the Eden + Citron input-profile pickers, keyed by controller FAMILY.

Profiles are AUTHORED in the emulator's own Controls dialog (``~/.config/<be>/input/<stem>.ini``);
MAD only PICKS one per family per context, and the launch binder bakes the chosen block into that
pad's ``player_N_*`` slice transiently (lib/switch_bind -> lib/yuzu_profiles -> eden_cfg).

WHY FAMILY, NOT PLAYER: a Yuzu-fork profile embeds the pad's SDL guid in every binding value and its
button indices differ per pad model, and ``eden_cfg.assign_devices`` re-resolves each slot from the
pad that actually landed in it -- so a per-PLAYER pick would be overwritten before the game starts.
Keyed by family it works, and ``eden_cfg._retarget`` rewrites guid+port so one authored profile is
correct for the next pad of that family (``family_profiles.nth``: "DS 1" -> "DS 2").

Context placement follows the panel convention (the DOOR bakes the context): each Switch emulator
tile opens the DOCKED namespace, the On-the-go tile opens the HANDHELD twin. Handheld never inherits
docked unless the per-page "same as docked" toggle is on.

Store: ``[backends.<be>.profile_map.<context>]`` in controller-policy.local.toml, ``{family: stem}``
-- the same shape and the same reason as Cemu's. Deliberately NOT ``player_N_profile_name``, which
``eden_cfg`` blanks on every launch (the pick would read as forgotten next time the page opened).
No EBUSY: this page never touches the emulator's live config.
"""
from __future__ import annotations

from pathlib import Path

from .. import eden_cfg, localpolicy, mad_config, routing, yuzu_profiles
from ..policy import LOCAL, load_merged
from .rpc import RpcError, method

_UNSET = "(none — leave as it is)"
_BACKENDS = ("eden", "citron")

_AUTHOR_NOTE = ("Profiles are created in the emulator itself (Controls → Profile). The pick is "
                "applied at launch and reverted on exit; your controller assignment (Controllers) "
                "still decides who is Player 1.")


def _families() -> list[str]:
    # Exclude X-Arcade for the same reason Cemu's page does: routing.family_of classifies the cab as
    # "Xbox" and NEVER "X-Arcade" (only family_token_of splits it out), so an "X-Arcade" row would be
    # a dead assignment the binder can never apply. Assign the cab via the "Xbox" row instead.
    return [f for f in mad_config.KNOWN_FAMILIES if f != "X-Arcade"]


def _cfg(backend: str) -> dict:
    be = (load_merged().get("backends") or {}).get(backend)
    return be if isinstance(be, dict) else {}


def _idir(backend: str) -> Path:
    return yuzu_profiles.input_dir(backend, _cfg(backend))


def _family_of_stem(idir: Path, stem: str) -> str | None:
    """The controller family a profile was authored on, from the SDL guid its bindings embed.

    Used ONLY to filter each family row's options, so the DualSense row does not offer a Wii U Pro
    layout. The binder does not enforce this -- a hand-edited policy still applies whatever it
    names -- so an unreadable or guid-less profile returns None and stays offered everywhere."""
    try:
        binds = eden_cfg._clean_block(eden_cfg._template_bindings(idir / f"{stem}.ini"))
    except OSError:
        return None
    guid = eden_cfg._block_guid(binds)
    if not guid:
        return None
    vidpid = eden_cfg._guid_to_vidpid(guid)
    # routing.family_of splits some vid:pids by NAME (an 8BitDo pid outside _8BITDO_PRO_PIDS is
    # "8BitDo" or "8BitDo Pro" depending on the name; a Sony pid outside _DS4_PIDS is "DualShock 4"
    # or "DualSense"). The name is unrecoverable here -- eden_cfg zeroes the guid's name-CRC bytes
    # -- so guessing would file an SN30 Pro+ profile under the row the binder never consults, and
    # the only pick the user could make would be a no-op. Return None instead: _options already
    # offers an unclassifiable profile on EVERY row, which is the honest fallback.
    if routing.family_is_name_ambiguous(vidpid):
        return None
    return routing.family_of_vidpid(vidpid)


def _options(backend: str, family: str, current: str | None) -> tuple[list[str], int]:
    """``[unset] + the stems authored on THIS family`` (+ the current pick, always kept visible so a
    stale or hand-set value can be seen and cleared instead of silently reading as unset)."""
    idir = _idir(backend)
    stems = [s for s in yuzu_profiles.list_stems(idir)
             if _family_of_stem(idir, s) in (family, None)]
    opts = [_UNSET] + stems
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _map_for(backend: str, context: str) -> dict:
    """The stored ``{family: stem}`` slice for this context (husk-tolerant)."""
    pm = _cfg(backend).get("profile_map")
    slice_ = pm.get(context) if isinstance(pm, dict) else None
    return slice_ if isinstance(slice_, dict) else {}


def _register(ns: str, backend: str, context: str) -> None:
    label = "Docked" if context == "docked" else "Handheld"

    @method(f"{ns}.get", slow=True)
    def _get(params, _be=backend, _ctx=context):
        cfg = _cfg(_be)
        assigned = _map_for(_be, _ctx)
        docked = _map_for(_be, "docked")
        mirror = bool(cfg.get("handheld_mirrors_docked"))
        rows = []
        for f in _families():
            cur = assigned.get(f) if isinstance(assigned.get(f), str) else None
            opts, val = _options(_be, f, cur)
            if _ctx == "handheld" and mirror and not cur and docked.get(f):
                # DISPLAY-only: index 0 still means unset, and .set reads a fresh list, so the
                # indices are unchanged. Picking a real profile still overrides the inherited one.
                opts = [f"(from docked: {docked[f]})"] + opts[1:]
            rows.append({"key": f"family:{f}", "label": f, "type": "enum",
                         "options": opts, "value": val, "picker": True})
        settings = []
        note = ""
        if _ctx == "handheld":
            settings.append({"key": "handheld_mirrors_docked",
                             "label": "Use my docked map when a handheld family is unset",
                             "type": "bool", "value": mirror})
            note = "Handheld here means playing on the Deck's own screen."
        groups = []
        if settings:
            groups.append({"title": "Handheld input", "note": note, "settings": settings})
        groups.append({"title": f"{label} map", "note": "", "settings": rows})
        return {"exists": True, "running": False,
                "note": f"The {context} input profile for each controller. {_AUTHOR_NOTE}",
                "groups": groups}

    @method(f"{ns}.set", slow=True)
    def _set(params, _be=backend, _ctx=context):
        key = str(params.get("key") or "")
        data = localpolicy.load(LOCAL)
        be = data.setdefault("backends", {}).setdefault(_be, {})

        if key == "handheld_mirrors_docked":
            if _ctx != "handheld":
                raise RpcError("EINVAL", "the mirror toggle is a handheld-only setting")
            if _truthy(params.get("value")):
                be["handheld_mirrors_docked"] = True
            else:
                be.pop("handheld_mirrors_docked", None)
            _prune(data, _be)
            localpolicy.dump(LOCAL, data)         # atomic + staterev.bump("config")
            return {"key": key, "value": bool(be.get("handheld_mirrors_docked"))}

        if not key.startswith("family:"):
            raise RpcError("EINVAL", f"{key!r} is not a profile setting")
        family = key.split(":", 1)[1]
        if family not in _families():
            raise RpcError("EINVAL", f"unknown controller family {family!r}")
        assigned = _map_for(_be, _ctx)
        cur = assigned.get(family) if isinstance(assigned.get(family), str) else None
        opts, _v = _options(_be, family, cur)
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        # The index maps against a FRESH options list; the panel's page may be older (profiles are
        # authored in the emulator while MAD is open, and a new .ini bumps no staterev). Requiring
        # the resolved stem to exist kills the worst variant (storing a deleted stem); an insertion
        # between get and set can still remap onto a neighbouring REAL stem -- a panel-wide enum
        # limitation, bounded by the page note telling the user to reopen after authoring.
        if idx != 0 and not (_idir(_be) / f"{opts[idx]}.ini").is_file():
            raise RpcError("EINVAL",
                           "profile list changed on disk — reopen this page and pick again")
        pm = be.setdefault("profile_map", {})
        slice_ = pm.setdefault(_ctx, {})
        if idx == 0:
            slice_.pop(family, None)
        else:
            slice_[family] = opts[idx]
        _prune(data, _be)
        localpolicy.dump(LOCAL, data)
        return {"key": key, "value": idx}


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _prune(data: dict, backend: str) -> None:
    """Drop emptied tables so clearing the last pick leaves no husk behind in local.toml."""
    be = data.get("backends", {}).get(backend)
    if not isinstance(be, dict):
        return
    pm = be.get("profile_map")
    if isinstance(pm, dict):
        for ctx in ("docked", "handheld"):
            if isinstance(pm.get(ctx), dict) and not pm[ctx]:
                pm.pop(ctx)
        if not pm:
            be.pop("profile_map", None)
    if not be:
        data.get("backends", {}).pop(backend, None)
    if not data.get("backends"):
        data.pop("backends", None)


for _be in _BACKENDS:
    _register(f"{_be}_input_docked", _be, "docked")
    _register(f"{_be}_input_handheld", _be, "handheld")
