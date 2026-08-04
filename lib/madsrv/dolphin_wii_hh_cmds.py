"""dolphin_wii_hh.* -- MAD On-the-go per-game HANDHELD page for Wii (Dolphin).

A `settings_pergame` browser (game picker -> ONE settings page per game, no submenu). It lists every
Wii game except lightgun-collection titles (useless without the guns handheld). Motion/pointer-only
games are NOT hidden any more (2026-08-04): the profile ladder (sideways/nunchuk/other styles +
per-game Handheld-profile picks on this very page) makes them handheld-playable.

Each game's page carries:
  - Handheld resolution: an enum over the Dolphin rungs handheld_res offers for Wii (Inherit = leave the
    per-system default). Stored [backends.dolphin_wii.pergame.<GameID>].hhres (a factor token); applied
    transiently at launch by handheld_res.apply, reverted on exit.
  - Input profiles: PLAYER 1-4 handheld seats (handheld_profile[_pN]; multi-seat 2026-08-04 —
    Player 1 is normally the Deck, 2-4 external pads). "(inherit global)" falls back per row to
    the per-style handheld pages (dolphin_wii_hh_classic/_sideways/_nunchuk).
  - Force Classic Controller: ONLY for games NOT auto-resolved as CC (auto-CC games get a note instead).
    Stored [backends.dolphin_wii.pergame.<GameID>].force_cc; the launch decider forces CC in any no-bar
    context (dolphin_wii_source.force_cc).

Pages:
  dolphin_wii_hh.games -> {games:[...filtered...], system:"wii", note}
  dolphin_wii_hh.get   -> {exists, running, note, groups:[ resolution (+ force-CC only if not auto-CC) ]}
  dolphin_wii_hh.set   -> persist the res token / force_cc to the per-game policy table
"""
from __future__ import annotations

import re

from .. import (dolphin_gameids as gids, dolphin_wii_source, dolphin_wii_tdb,
                es_gamelist, handheld_res, localpolicy, proc_guard)
from ..policy import LOCAL, load_merged
from . import dolphin_games
from .rpc import RpcError, method

_ID_RE = re.compile(r"^[A-Z0-9]{6}$")
_SYSTEM = "wii"
_PROFILE_KEYS = ("handheld_profile", "handheld_profile_p2",
                 "handheld_profile_p3", "handheld_profile_p4")


def _profile_opts(key: str, current: str) -> list[str]:
    """One seat row's fresh option list: '(inherit global)' + the profiles (Sinden only on
    Player 1 — a gun profile is a whole-launch mode, meaningless as a later seat), plus a
    stored-but-deleted stem appended so it stays visible + clearable."""
    from .. import dolphin_wii_profiles
    opts = ["(inherit global)"] + dolphin_wii_profiles.list_profiles(
        None, include_sinden=(key == "handheld_profile"))
    if current and current not in opts:
        opts.append(current)
    return opts


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip().upper()
    if not _ID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


# ── per-game policy store ([backends.dolphin_wii.pergame.<GameID>]) ───────────
def _pergame() -> dict:
    be = (load_merged().get("backends") or {}).get("dolphin_wii") or {}
    pg = be.get("pergame")
    return pg if isinstance(pg, dict) else {}


def _entry(tid: str) -> dict:
    e = _pergame().get(tid)
    return e if isinstance(e, dict) else {}


def _store(tid: str, key: str, value) -> None:
    """Set (value truthy) or clear (None/'') one per-game key in controller-policy.local.toml, pruning
    the game entry / pergame / dolphin_wii tables when they go empty. Atomic + staterev via dump."""
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault("dolphin_wii", {})
    pergame = be.setdefault("pergame", {})
    game = pergame.setdefault(tid, {})
    if value in (None, "", False):
        game.pop(key, None)
    else:
        game[key] = value
    if not game:
        pergame.pop(tid, None)
    if not pergame:
        be.pop("pergame", None)
    if not be:
        data.get("backends", {}).pop("dolphin_wii", None)
    localpolicy.dump(LOCAL, data)


# ── resolution choices (Inherit first, then the Wii Dolphin rungs) ────────────
def _res_choices():
    ch = handheld_res.resolution_choices(_SYSTEM)         # [...(token,label)..., ("inherit", "...")]
    rest = [(t, l) for t, l in ch if t != "inherit"]
    return [("inherit", "Inherit (per-system default)")] + rest


# ── pages ────────────────────────────────────────────────────────────────────
@method("dolphin_wii_hh.games", slow=True)
def _games(params):
    roms = dolphin_games._roms(_SYSTEM)
    names = es_gamelist.titles(_SYSTEM)                   # {stem.lower(): name}
    resolved = gids.gameids(roms)                         # {abspath: gameid|None}
    pergame = _pergame()
    label_map = dict(_res_choices())
    games, seen = [], set()
    for p in roms:
        if dolphin_wii_source._is_lightgun(str(p)):       # gun games: useless handheld -> drop
            continue
        gid = resolved.get(str(p))
        if not gid or gid in seen:
            continue
        # Motion/pointer-only games are NO LONGER hidden (2026-08-04 review fix): the profile
        # ladder makes them handheld-playable (sideways/nunchuk/other styles), and this page
        # now carries the per-game Handheld-profile pick — hiding them made the explicit
        # per-game rung unreachable for exactly the games the styles target.
        seen.add(gid)
        e = pergame.get(gid) if isinstance(pergame.get(gid), dict) else {}
        summ = []
        res = str(e.get("hhres") or "").strip().lower()
        if res and res != "inherit":
            summ.append(label_map.get(res, res))
        if e.get("force_cc"):
            summ.append("Force CC")
        games.append({"titleid": gid, "name": names.get(p.stem.lower()) or p.stem, "stem": p.stem,
                      "override": bool(e), "summary": "  ".join(summ)})
    games.sort(key=lambda g: g["name"].lower())
    note = ("" if games else
            "No Wii games to configure here. Lightgun-collection titles are hidden (no guns "
            "handheld). Add Wii ROMs and scrape them in ES-DE.")
    return {"games": games, "system": _SYSTEM, "note": note}


@method("dolphin_wii_hh.get", slow=True)
def _get(params):
    tid = _tid(params)
    running = proc_guard.emulator_running("dolphin")
    cc = dolphin_wii_tdb.is_cc_capable(tid)
    e = _entry(tid)

    choices = _res_choices()
    tokens = [t for t, _ in choices]
    labels = [l for _, l in choices]
    stored = str(e.get("hhres") or "inherit").strip().lower()
    snapped = handheld_res.snap_token(_SYSTEM, stored) if stored != "inherit" else "inherit"
    val = tokens.index(snapped) if snapped in tokens else 0
    groups = [{"title": "Handheld resolution", "note": "", "settings": [
        {"key": "res", "label": "Handheld resolution", "type": "enum",
         "options": labels, "value": val, "picker": True}]}]

    # Per-game HANDHELD input profiles, players 1-4 (the docked twin lives on the Wii tile's
    # per-game menu — the door bakes the context). "(inherit global)" falls back per row to
    # the per-style handheld pages. Applied only with no DolphinBar; transient.
    prof_settings = []
    for n, key in enumerate(_PROFILE_KEYS, start=1):
        cur = str(e.get(key) or "")
        opts = _profile_opts(key, cur)
        prof_settings.append({"key": key, "label": f"Player {n}", "type": "enum",
                              "options": opts,
                              "value": opts.index(cur) if cur in opts and cur else 0,
                              "picker": True})
    groups.append({"title": "Input profiles", "note": "", "settings": prof_settings})

    cc = dolphin_wii_tdb.is_cc_capable(tid)
    if cc:
        note = ("This game already supports a Classic Controller (auto-detected via GameTDB), so it "
                "plays with a gamepad handheld -- no need to force it. Set a handheld resolution above "
                "if you like; it reverts to your docked setting on exit.")
    else:
        groups.append({"title": "Classic Controller", "note": "", "settings": [
            {"key": "force_cc", "label": "Force Classic Controller", "type": "enum",
             "options": ["Off", "Force Classic Controller"], "value": 1 if e.get("force_cc") else 0}]})
        # The wording gates on the DETECTED STYLE first (like the docked per-game note):
        # a curated-overlay game (WiiWare nunchuk) IS detected even though GameTDB itself
        # has no record of it — has_input_data alone would wrongly call it unknown.
        style_tok = dolphin_wii_source.style(tid)
        if style_tok in ("sideways", "nunchuk"):
            label = {"sideways": "Sideways", "nunchuk": "Nunchuk"}[style_tok]
            note = (f"Detected style: {label}. The {label} games handheld seats apply at "
                    "launch; the Player rows above override them for this game. If it also "
                    "supports a Classic Controller, force it below.")
        elif dolphin_wii_tdb.has_input_data(tid):
            note = ("GameTDB lists no Classic Controller for this game. If it really supports one, "
                    "force it below to drive it with a gamepad (handheld and docked-without-a-bar). "
                    "Forcing will NOT help a pure Wii-Remote motion / pointer game; the Player rows "
                    "above are the way to seat pads for those.")
        else:
            note = ("This game is not in GameTDB, so nothing about its controls can be detected. "
                    "If it supports a Classic Controller, force it below; otherwise seat pads with "
                    "the Player rows above, or curate its id into the sideways or nunchuk list.")

    return {"exists": True, "running": running, "note": note, "groups": groups}


@method("dolphin_wii_hh.set", slow=True)
def _set(params):
    tid = _tid(params)
    key = params.get("key")
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad option index")
    if key == "res":
        choices = _res_choices()
        if not (0 <= idx < len(choices)):
            raise RpcError("EINVAL", "option index out of range")
        token = choices[idx][0]
        _store(tid, "hhres", None if token == "inherit" else token)
    elif key == "force_cc":
        _store(tid, "force_cc", True if idx >= 1 else None)
    elif key in _PROFILE_KEYS:
        from .. import dolphin_wii_profiles
        entry = _entry(tid)
        opts = _profile_opts(key, str(entry.get(key) or ""))   # mirror _get's fresh list
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "option index out of range")
        if idx != 0:
            # Set-time index maps a FRESH list; require the stem's file to exist so a profile
            # deleted in Dolphin between get and set can't be silently stored (see
            # dolphin_profile_cmds._require_on_disk for the residual insertion-race note).
            if dolphin_wii_profiles.profile_body(opts[idx]) is None:
                raise RpcError("EINVAL",
                               "profile list changed on disk — reopen this page and pick again")
            for k2 in _PROFILE_KEYS:                     # one pad cannot drive two players
                if k2 != key and str(entry.get(k2) or "") == opts[idx]:
                    raise RpcError("EINVAL",
                                   "that profile is already picked for another player of this "
                                   "game; each profile is bound to the one controller it was "
                                   "authored on in Dolphin")
        _store(tid, key, None if idx == 0 else opts[idx])
    else:
        raise RpcError("EINVAL", f"unknown key {key!r}")
    return {"key": key, "value": idx}
