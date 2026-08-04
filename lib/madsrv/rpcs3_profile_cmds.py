"""rpcs3prof.* / rpcs3profhh.* / rpcs3profpg.* / rpcs3profpghh.* — the PS3 docked/handheld
input-profile pickers (global + per-game), replacing the per-button Mappings editors.

Profiles are AUTHORED in RPCS3's own UI (Configuration → Gamepads: save the config file
under a new name → ``~/.config/rpcs3/input_configs/global/<stem>.yml``; reachable from the
ES-DE System row without leaving Game mode); MAD only PICKS
one per context and the launch binder copies its usable ``Player N Input`` blocks into the
resting target transiently (lib/switch_bind.py -> rpcs3_cfg.apply_profile_players), so the
pick composes with per-launch pad seating and the PS-button chord. CRITICAL: RPCS3's
``--input-config`` CLI is NEVER used (it hard-requires --no-gui and bypasses the file the
binder seats devices into).

Context placement follows the panel convention (the DOOR bakes the context): the PS3 tile
opens the DOCKED namespaces (``rpcs3prof`` global, ``rpcs3profpg`` per-game); the On-the-go
tile opens the HANDHELD twins (``rpcs3profhh``, ``rpcs3profpghh``). Handheld never inherits
docked.

Divergence from the PCSX2 twin (lib/madsrv/pcsx2_profile_cmds): RPCS3's profiles live in the
SAME directory as the resting config, so the resting stem is NOT listed as a pick — it IS
the first option, shown by NAME (e.g. "Default": the config RPCS3 itself has active; picking
it changes nothing, unlike PCSX2's unnamed "(none — current layout)" resting ini); and there
is no ports/Player-2 group (RPCS3 has no USB-port selectors).

Stores (no EBUSY anywhere — these pages never touch RPCS3's live config):
  global    [backends.rpcs3].profile_docked / .profile_handheld   (controller-policy.local.toml)
  per-game  entry["profiles"] = {"docked": <stem>, "handheld": <stem>}  (sparse) in
            ~/Emulation/storage/rpcs3/pergame-input.json
            (shared with lib/madsrv/rpcs3_pergame_input_cmds, which owns the store file).
"""
from __future__ import annotations

from .. import localpolicy, rpcs3_profiles, staterev
from ..policy import LOCAL, load_merged
from . import rpcs3_games
from . import rpcs3_pergame_input_cmds as pgin
from .rpc import RpcError, method

_AUTHOR_NOTE = ("Profiles are created in RPCS3 itself (Configuration → Gamepads — save the "
                "config file under a new name). The pick is applied at launch and reverted on "
                "exit; your controller assignment (Pads to players) still decides who is "
                "Player 1. Players saved with RPCS3's SDL handler apply fully; native-handler "
                "players (DualSense/DualShock) keep the standard layout.")


def _cfg() -> dict:
    be = (load_merged().get("backends") or {}).get("rpcs3")
    return be if isinstance(be, dict) else {}


def _resting_info(serial: str | None = None) -> tuple[str, bool]:
    """``(stem, per_title)`` of the config the launch will rest on for this page's scope
    (the SAME ladder the launch targets: per-title custom when `serial` is given and has
    one, else the active pointer, else Default). The stem names the picker's first option
    (no fuzzy '(none)'); ``per_title`` flags a game resting on its own
    ``input_configs/<SERIAL>/Default.yml`` — for those, NO global stem is an identity
    (the stem exclusion is dropped) and the label must not pretend a global name rests."""
    cfg = _cfg()
    target = rpcs3_profiles.resting_target(cfg, serial)
    return target.stem, target.parent != rpcs3_profiles.profiles_dir(cfg)


def _options(zero_label: str, current: str | None,
             exclude: str | None) -> tuple[list[str], int]:
    """``[zero_label] + stems`` minus ``exclude`` (the resting stem — picking it would be
    an identity copy; None for a per-title-config game, where every global profile is a
    real change). A stored-but-deleted stem stays VISIBLE, appended, so it can be seen and
    cleared instead of silently reading as unset. The caller resolves the resting info ONCE
    per request and threads it here — label and exclusion must come from the same read, or
    a pointer switch mid-request could render a duplicated row / store the zero label."""
    opts = [zero_label] + [
        s for s in rpcs3_profiles.list_stems(rpcs3_profiles.profiles_dir(_cfg()))
        if s != exclude]
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _ctx_key(context: str) -> str:
    return "profile_handheld" if context == "handheld" else "profile_docked"


# ── global pickers (rpcs3prof = docked door, rpcs3profhh = handheld door) ─────
def _register_global(ns: str, context: str, row_label: str) -> None:
    key = _ctx_key(context)

    @method(f"{ns}.get", slow=True)
    def _get(params):
        resting, _pt = _resting_info()
        opts, val = _options(resting, rpcs3_profiles.global_profile(_cfg(), context),
                             exclude=resting)
        note = (f"The {context} input profile for every PS3 game (a game can override it "
                f"under Per-game). '{resting}' is the config RPCS3 itself has active — "
                f"picking it leaves everything as it is. {_AUTHOR_NOTE}")
        return {"exists": True, "running": False, "note": note,
                "groups": [{"title": "Input profiles", "note": "", "settings": [
                    {"key": key, "label": row_label, "type": "enum",
                     "options": opts, "value": val, "picker": True}]}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        if params.get("key") != key:
            raise RpcError("EINVAL", f"{params.get('key')!r} is not a profile setting")
        resting, _pt = _resting_info()
        opts, _cur = _options(resting, rpcs3_profiles.global_profile(_cfg(), context),
                              exclude=resting)
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        # The index maps against a FRESH options list; the panel's page may be older
        # (profiles are authored in RPCS3 while MAD is open, and a new .yml bumps no
        # staterev). Requiring the resolved stem to exist on disk kills the worst
        # variant (storing a deleted stem); an insertion between get and set can still
        # remap an index onto a neighboring REAL stem — a panel-wide enum limitation,
        # bounded by this page note telling the user to reopen after authoring.
        if idx != 0 and rpcs3_profiles.profile_path(
                rpcs3_profiles.profiles_dir(_cfg()), opts[idx]) is None:
            raise RpcError("EINVAL",
                           "profile list changed on disk — reopen this page and pick again")
        data = localpolicy.load(LOCAL)
        be = data.setdefault("backends", {}).setdefault("rpcs3", {})
        if idx == 0:
            be.pop(key, None)
            if not be:
                data.get("backends", {}).pop("rpcs3", None)
        else:
            be[key] = opts[idx]
        localpolicy.dump(LOCAL, data)          # atomic + staterev.bump("config")
        return {"key": key, "value": idx}


_register_global("rpcs3prof", "docked", "Docked profile")
_register_global("rpcs3profhh", "handheld", "Handheld profile")


# ── per-game pickers (rpcs3profpg = docked, rpcs3profpghh = handheld) ─────────
def _games_payload():
    store = pgin._load()

    def _ovr(serial):
        e = store.get(serial)
        return pgin._has_input_override(pgin._normalize_entry(e)) if isinstance(e, dict) else False

    out = []
    for g in rpcs3_games.games():
        override = _ovr(g["key"])
        out.append({"titleid": g["key"], "name": g["name"],
                    "stem": rpcs3_games.stem_of(g["path"]),
                    "override": override, "summary": "Custom input" if override else ""})
    return {"games": out, "system": "ps3"}


def _inherit_label(context: str, resting: str, per_title: bool) -> str:
    """Always NAMES the inherited outcome (no fuzzy bare '(inherit global)'): the
    tile-level pick if one is set; else, for a game resting on its own per-title RPCS3
    config, that fact (a global stem would misname what actually rests); else the config
    RPCS3 itself rests on."""
    g = rpcs3_profiles.global_profile(_cfg(), context)
    if g:
        return f"(inherit global: {g})"
    if per_title:
        return "(inherit: this game's own RPCS3 config)"
    return f"(inherit global: {resting})"


def _entry(serial: str) -> dict:
    e = pgin._load().get(serial)
    return pgin._normalize_entry(e) if isinstance(e, dict) else {}


def _store_pergame(serial: str, mutate) -> None:
    """Load a FRESH store under the shared lock, apply ``mutate(entry)``, prune, save.
    Normalizing on the way in migrates a legacy per-button entry losslessly (its binds
    fold under "binds", preserved inert)."""
    with pgin._LOCK:
        data = pgin._load()
        entry = data.get(serial)
        entry = pgin._normalize_entry(entry) if isinstance(entry, dict) else {}
        mutate(entry)
        if pgin._is_empty(entry):
            data.pop(serial, None)
        else:
            data[serial] = entry
        pgin._save(data)
    staterev.bump("config")


def _set_profile(entry: dict, context: str, stem: str | None) -> None:
    profiles = entry.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        entry.pop("profiles", None)
    if stem:
        profiles[context] = stem
    else:
        profiles.pop(context, None)
    if profiles:
        entry["profiles"] = profiles
    else:
        entry.pop("profiles", None)


def _register_pergame(ns: str, context: str) -> None:
    row_label = "Docked profile" if context == "docked" else "Handheld profile"

    @method(f"{ns}.games", slow=True)
    def _games(params):
        return _games_payload()

    @method(f"{ns}.get", slow=True)
    def _get(params):
        serial = pgin._titleid(params)
        e = _entry(serial)
        resting, per_title = _resting_info(serial)
        opts, val = _options(_inherit_label(context, resting, per_title),
                             rpcs3_profiles.pergame_profile(e, context),
                             exclude=None if per_title else resting)
        note = (f"The {context} input profile for THIS game only — the first option makes "
                f"no per-game pick (it names what the game falls back to). Applied at "
                f"launch, reverted on exit. {_AUTHOR_NOTE}")
        return {"exists": True, "running": False, "note": note,
                "groups": [{"title": "Input profile", "note": "", "settings": [
                    {"key": "profile", "label": row_label, "type": "enum",
                     "options": opts, "value": val, "picker": True}]}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        serial = pgin._titleid(params)
        if params.get("key") != "profile":
            raise RpcError("EINVAL", f"unknown key {params.get('key')!r}")
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad option index")
        e = _entry(serial)
        resting, per_title = _resting_info(serial)
        opts, _cur = _options(_inherit_label(context, resting, per_title),
                              rpcs3_profiles.pergame_profile(e, context),
                              exclude=None if per_title else resting)
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        stem = None if idx == 0 else opts[idx]
        if stem is not None and rpcs3_profiles.profile_path(
                rpcs3_profiles.profiles_dir(_cfg()), stem) is None:
            raise RpcError("EINVAL",
                           "profile list changed on disk — reopen this page and pick again")
        _store_pergame(serial, lambda entry: _set_profile(entry, context, stem))
        return {"key": "profile", "value": idx}


_register_pergame("rpcs3profpg", "docked")
_register_pergame("rpcs3profpghh", "handheld")
