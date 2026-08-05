"""ryujinx_input_docked / ryujinx_input_handheld — the Ryujinx input-profile pickers, per PLAYER.

Profiles are AUTHORED in Ryujinx's own Input dialog
(``~/.config/Ryujinx/profiles/controller/<stem>.json``); MAD only PICKS one per player per context,
and the launch binder bakes that profile's MAPPING subtree into the player's ``input_config`` entry
transiently (lib/switch_bind -> lib/ryujinx_profiles -> ryujinx_cfg), leaving the slot's device
identity to ``assign_devices``.

WHY PLAYER, NOT FAMILY (the opposite of Eden/Citron and Cemu): Ryujinx discards a profile's device
identity when it loads one -- ``InputViewModel.LoadProfile`` sets ``config.Id = currentDeviceId``
with ``LoadDevice()`` commented out, annotated "allows profiles to be applied to all controllers" --
so a profile is a portable BUTTON LAYOUT and "one profile = one player" is the honest key.

The picked profile also carries the CONTROLLER TYPE it was authored with, which is how JoyCon Pair
and single JoyCon stay reachable now that the per-button editor is gone. ``ryujinx_profiles``
clamps the one unsafe case (``Handheld`` is only stamped on the Handheld slot). Stick source,
invert and rotation ride along inside the profile's ``*_joycon_stick`` objects, which is why this
page needs no separate stick selectors.

Store: ``[backends.ryujinx.profile_map.<context>]`` in controller-policy.local.toml, keyed ``p1..p4``
(prefixed so it never reads as the legacy numeric ``slot_profiles`` tables). No EBUSY: this page
never touches Ryujinx's live config.
"""
from __future__ import annotations

from .. import localpolicy, ryujinx_profiles
from ..policy import LOCAL, load_merged
from .rpc import RpcError, method

_UNSET = "(none — leave as it is)"
_DUP_MSG = "that profile is already picked for another player"

_AUTHOR_NOTE = ("Profiles are created in Ryujinx itself (Input → Save Profile). The pick is applied "
                "at launch and reverted on exit, and it carries the controller type it was saved "
                "with. Your controller assignment (Controllers) still decides who is Player 1.")


def _cfg() -> dict:
    be = (load_merged().get("backends") or {}).get("ryujinx")
    return be if isinstance(be, dict) else {}


def _pdir():
    return ryujinx_profiles.profiles_dir(_cfg())


def _map_for(context: str) -> dict:
    pm = _cfg().get("profile_map")
    slice_ = pm.get(context) if isinstance(pm, dict) else None
    return slice_ if isinstance(slice_, dict) else {}


def _options(current: str | None) -> tuple[list[str], int]:
    """``[unset] + every authored profile`` (+ the current pick, always kept visible so a stale or
    hand-set value can be seen and cleared instead of silently reading as unset)."""
    opts = [_UNSET] + ryujinx_profiles.list_stems(_pdir())
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _register(ns: str, context: str) -> None:
    label = "Docked" if context == "docked" else "Handheld"

    @method(f"{ns}.get", slow=True)
    def _get(params, _ctx=context):
        assigned = _map_for(_ctx)
        docked = _map_for("docked")
        mirror = bool(_cfg().get("handheld_mirrors_docked"))
        rows = []
        for seat in ryujinx_profiles.SEATS:
            k = ryujinx_profiles.seat_key(seat)
            cur = assigned.get(k) if isinstance(assigned.get(k), str) else None
            opts, val = _options(cur)
            if _ctx == "handheld" and mirror and not cur and docked.get(k):
                # DISPLAY-only, exactly as the family pages do it: index 0 still means unset.
                opts = [f"(from docked: {docked[k]})"] + opts[1:]
            rows.append({"key": k, "label": f"Player {seat}", "type": "enum",
                         "options": opts, "value": val, "picker": True})
        groups = []
        note = ""
        if _ctx == "handheld":
            groups.append({"title": "Handheld input",
                           "note": ("Handheld here means playing on the Deck's own screen — it is "
                                    "unrelated to Ryujinx's own 'Handheld' controller type."),
                           "settings": [{"key": "handheld_mirrors_docked",
                                         "label": "Use my docked picks when a player is unset",
                                         "type": "bool", "value": mirror}]})
        groups.append({"title": f"{label} players", "note": note, "settings": rows})
        return {"exists": True, "running": False,
                "note": f"The {context} input profile for each player. {_AUTHOR_NOTE}",
                "groups": groups}

    @method(f"{ns}.set", slow=True)
    def _set(params, _ctx=context):
        key = str(params.get("key") or "")
        data = localpolicy.load(LOCAL)
        be = data.setdefault("backends", {}).setdefault("ryujinx", {})

        if key == "handheld_mirrors_docked":
            if _ctx != "handheld":
                raise RpcError("EINVAL", "the mirror toggle is a handheld-only setting")
            if _truthy(params.get("value")):
                be["handheld_mirrors_docked"] = True
            else:
                be.pop("handheld_mirrors_docked", None)
            _prune(data)
            localpolicy.dump(LOCAL, data)
            return {"key": key, "value": bool(be.get("handheld_mirrors_docked"))}

        seats = {ryujinx_profiles.seat_key(s): s for s in ryujinx_profiles.SEATS}
        if key not in seats:
            raise RpcError("EINVAL", f"{key!r} is not a player setting")
        assigned = _map_for(_ctx)
        cur = assigned.get(key) if isinstance(assigned.get(key), str) else None
        opts, _v = _options(cur)
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        if idx != 0 and ryujinx_profiles.profile_path(_pdir(), opts[idx]) is None:
            raise RpcError("EINVAL",
                           "profile list changed on disk — reopen this page and pick again")
        if idx != 0:
            # One profile per player. Two players sharing a layout is harmless to the bake, but it
            # is nearly always a mis-click, and ryujinx_cfg goes to some trouble (_drop_surplus) to
            # stop one pad driving two players -- so say so instead of silently accepting it.
            for other, stem in assigned.items():
                if other != key and stem == opts[idx]:
                    raise RpcError("EINVAL", _DUP_MSG)
        pm = be.setdefault("profile_map", {})
        slice_ = pm.setdefault(_ctx, {})
        if idx == 0:
            slice_.pop(key, None)
        else:
            slice_[key] = opts[idx]
        _prune(data)
        localpolicy.dump(LOCAL, data)
        return {"key": key, "value": idx}


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _prune(data: dict) -> None:
    """Drop emptied tables so clearing the last pick leaves no husk behind in local.toml."""
    be = data.get("backends", {}).get("ryujinx")
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
        data.get("backends", {}).pop("ryujinx", None)
    if not data.get("backends"):
        data.pop("backends", None)


_register("ryujinx_input_docked", "docked")
_register("ryujinx_input_handheld", "handheld")
