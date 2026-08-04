"""Dolphin (Wii + GameCube) input-profile PICKERS — per style, per PLAYER, both contexts.

Profiles are AUTHORED in Dolphin's own UI (Profiles/Wiimote + Profiles/GCPad; the MAD
Button-mapping editors were phased out 2026-08-04); MAD only lists + picks them, and the
launch rails apply the picks transiently (lib/dolphin_wii_source for Wii,
lib/dolphin_gc_dock for GC). Context placement follows the panel convention (the DOOR bakes
the context): the Wii/GameCube tile opens the DOCKED namespaces, On-the-go the HANDHELD ones.

Every page carries PLAYER 1-4 rows (multi-seat 2026-08-04; Player 1 keeps the legacy key
name, players 2-4 suffix _p<n>). Each profile is bound to the pad it was authored on, so the
same profile on two rows would mirror one pad — set rejects it per page/game, and the rails
drop cross-source duplicates too. Wii semantics: an UNSET row turns that Wii Remote slot
OFF; a profile applies ONLY when NO DolphinBar is connected (a bar always routes real Wii
Remotes; Sinden-collection lightgun games always use the automatic gun rail). GC semantics:
an UNSET row leaves that port exactly as the normal setup has it (docked: Pads to players
still fills it; handheld: the resting config). With no per-game pick, the Wii launch decider
auto-detects the game's STYLE and uses that style's per-player defaults:
  CLASSIC  (GameTDB classiccontroller / force_cc)  -> docked: the CC pads rail (unchanged);
                                                      handheld: the "Classic games" seats
  SIDEWAYS (curated data/gametdb/sideways_ids.json) -> the "Sideways games" seats
  NUNCHUK  (GameTDB nunchuk control + the curated WiiWare overlay) -> the "Nunchuk games" seats
  OTHER    (pointer / unknown)                      -> per-game pick only (no global rows)
A Sinden* profile is pickable on Player 1 only (per-game lightgun outside the collection);
the launch apply reroutes it through the FULL sinden mode and ignores the other rows.

Namespaces (all data-only pages; policy-store writes, no EBUSY):
  dolphin_wii_dock_sideways  Wii tile   "Sideways games"  [backends.dolphin_wii].docked_profile_sideways[_pN]
  dolphin_wii_dock_nunchuk   Wii tile   "Nunchuk games"   [backends.dolphin_wii].docked_profile_nunchuk[_pN]
  dolphin_wii_hh_classic     On-the-go  "Classic games"   [backends.dolphin_wii].undocked_profile[_pN]
  dolphin_wii_hh_sideways    On-the-go  "Sideways games"  [backends.dolphin_wii].undocked_profile_sideways[_pN]
  dolphin_wii_hh_nunchuk     On-the-go  "Nunchuk games"   [backends.dolphin_wii].undocked_profile_nunchuk[_pN]
  dolphin_gc_hh_profiles     On-the-go  GC "Player profiles"  [backends.dolphin_gc].undocked_profile[_pN]
  dolphin_wii_pg_profiles    Wii tile per-game   [..dolphin_wii.pergame.<ID>].docked_profile[_pN]
  dolphin_gc_pg_profiles     GC tile per-game    [..dolphin_gc.pergame.<ID>].docked_profile[_pN]
  dolphin_gc_hh              On-the-go GC per-game [..dolphin_gc.pergame.<ID>].handheld_profile[_pN]
(The former dolphin_wii_dock / dolphin_wii_dock_hh grab-bag pages and the GC
dolphin_gc_dock_cmds "Dock / handheld" fold-in are RETIRED.)
"""
from __future__ import annotations

from .. import (dolphin_profiles, dolphin_wii_profiles, dolphin_wii_source,
                dolphin_wii_tdb, localpolicy)
from ..policy import LOCAL, load_merged
from . import dolphin_games
from . import dolphin_wii_hh_cmds as hh
from .rpc import RpcError, method

_NONE = "(none)"
_INHERIT = "(inherit global)"
_CC_DEFAULT_LABEL = f"(default: {dolphin_wii_profiles.HANDHELD_DEFAULT})"
_SEATS = (1, 2, 3, 4)
_WII_BE = "dolphin_wii"
_GC_BE = "dolphin_gc"
_DUP_MSG = ("that profile is already picked for another player here; each profile is "
            "bound to the one controller it was authored on in Dolphin")


def _seat_key(base: str, seat: int) -> str:
    """Player 1 keeps the legacy key name (live user data stays valid); 2-4 suffix _p<n>."""
    return base if seat == 1 else f"{base}_p{seat}"


def _be(backend: str) -> dict:
    be = (load_merged().get("backends") or {}).get(backend)
    return be if isinstance(be, dict) else {}


def _wii_profiles() -> list[str]:
    return dolphin_wii_profiles.list_profiles(None, include_sinden=True)


def _wii_profiles_no_sinden() -> list[str]:
    """Players 2-4: a Sinden profile is a whole-launch gun mode, meaningless as a seat."""
    return dolphin_wii_profiles.list_profiles(None, include_sinden=False)


def _cc_profiles() -> list[str]:
    return dolphin_wii_profiles.list_profiles()          # Classic-extension only, no Sinden


def _gc_profiles() -> list[str]:
    return dolphin_profiles.list_profiles()


def _options(zero_label: str, stems: list[str], current: str) -> tuple[list[str], int]:
    """``[zero_label] + stems`` (a stored-but-deleted stem stays VISIBLE, appended, so it can
    be seen + cleared instead of silently reading as unset) + the current row index."""
    opts = [zero_label] + list(stems)
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _set_backend_key(backend: str, key: str, value) -> None:
    """Set (truthy) or clear (None/"") one backend-level key in controller-policy.local.toml,
    pruning an emptied backend table. Atomic + staterev via localpolicy.dump."""
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault(backend, {})
    if value in (None, ""):
        be.pop(key, None)
    else:
        be[key] = value
    if not be:
        data.get("backends", {}).pop(backend, None)
    localpolicy.dump(LOCAL, data)


def _store_pergame(backend: str, tid: str, key: str, value) -> None:
    """Per-game policy-table write with the empty-table pruning convention
    (dolphin_wii_hh_cmds._store, generalized to any dolphin backend)."""
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault(backend, {})
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
        data.get("backends", {}).pop(backend, None)
    localpolicy.dump(LOCAL, data)


def _pergame_entry(backend: str, tid: str) -> dict:
    pg = _be(backend).get("pergame")
    e = pg.get(tid) if isinstance(pg, dict) else None
    return e if isinstance(e, dict) else {}


_STYLE_LABELS = {"cc": "Classic controller", "sideways": "Sideways (curated)",
                 "nunchuk": "Nunchuk (detected)", "other": "Pointer/other"}


def wii_style(tid: str) -> str:
    """The detected input style for the per-game page note — a LABEL over the launch
    decider's own ladder (dolphin_wii_source.style), so the two can never drift."""
    return _STYLE_LABELS[dolphin_wii_source.style(tid)]


def _parse_idx(params, opts) -> int:
    try:
        idx = int(float(params.get("value")))
    except (TypeError, ValueError):
        raise RpcError("EINVAL", "bad profile index")
    if not (0 <= idx < len(opts)):
        raise RpcError("EINVAL", "profile index out of range")
    return idx


def _require_on_disk(stem: str, body_fn) -> None:
    """The set-time index maps against a FRESH options list; the panel's page may predate a
    profile authored/deleted in Dolphin while MAD was open (no staterev bump). Requiring the
    resolved stem's file to exist kills the stored-deleted-stem variant; an insertion between
    get and set can still remap an index onto a neighboring REAL stem — a panel-wide enum
    limitation (reopen the page after authoring)."""
    if body_fn(stem) is None:
        raise RpcError("EINVAL", "profile list changed on disk — reopen this page and pick again")


# ── global per-style seat pages (the DOOR bakes the context) ──────────────────
def _seat_rows(base: str, p1_opts, rest_opts, p1_zero: str = _NONE) -> list[tuple]:
    """(key, row label, options builder, zero-sentinel) for players 1-4 of one page."""
    rows = [(_seat_key(base, 1), "Player 1", p1_opts, p1_zero)]
    rows += [(_seat_key(base, n), f"Player {n}", rest_opts, _NONE) for n in (2, 3, 4)]
    return rows


def _register_style_page(ns: str, backend: str, rows, title: str, note: str,
                         body_fn, implicit: dict | None = None) -> None:
    """One global seat page: an enum picker row per player. set validates against a fresh
    list, requires the picked stem on disk, and rejects a stem already stored on ANOTHER
    row of this page (mirroring one pad across two players is never meaningful).
    `implicit` maps a row key to the stem its EMPTY value resolves to at launch (the
    handheld-Classic Player 1 default) so the duplicate check covers unset-but-defaulted
    rows too — otherwise the pick would validate here and then be dup-dropped at launch."""
    @method(f"{ns}.get", slow=True)
    def _get(params):
        be = _be(backend)
        settings = []
        for key, label, stems_fn, zero in rows:
            opts, val = _options(zero, stems_fn(), str(be.get(key, "") or ""))
            settings.append({"key": key, "label": label, "type": "enum",
                             "options": opts, "value": val, "picker": True})
        return {"exists": True, "running": False, "note": note,
                "groups": [{"title": title, "note": "", "settings": settings}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        key = params.get("key")
        row = next((r for r in rows if r[0] == key), None)
        if row is None:
            raise RpcError("EINVAL", f"{key!r} is not a profile setting")
        be = _be(backend)
        opts, _cur = _options(row[3], row[2](), str(be.get(key, "") or ""))
        idx = _parse_idx(params, opts)
        if idx != 0:
            _require_on_disk(opts[idx], body_fn)
            for k2, _lbl, _o, _z in rows:
                if k2 == key:
                    continue
                stored = str(be.get(k2, "") or "") or (implicit or {}).get(k2, "")
                if stored == opts[idx]:
                    raise RpcError("EINVAL", _DUP_MSG)
        _set_backend_key(backend, key, None if idx == 0 else opts[idx])
        return {"key": key, "value": idx}


_WII_SEAT_NOTE = ("Each player row seats one Dolphin profile into that Wii Remote slot at "
                  "launch; an unset row keeps the slot off. Used only when NO DolphinBar is "
                  "connected (a bar always routes real Wii Remotes; Sinden-collection games "
                  "always use the gun rail). Profiles are created in Dolphin itself (desktop "
                  "mode) and each is bound to the controller it was authored on, so pick a "
                  "different profile per player. A per-game pick wins over these rows. "
                  "Applied at launch, reverted on exit.")

_register_style_page(
    "dolphin_wii_dock_sideways", _WII_BE,
    _seat_rows("docked_profile_sideways", _wii_profiles, _wii_profiles_no_sinden),
    "Players",
    "Sideways-Wiimote games (curated list), docked. " + _WII_SEAT_NOTE
    + " A Sinden profile on Player 1 turns the whole launch into a lightgun launch and the "
      "other rows are ignored. Classic-capable games use the Classic controller order page "
      "instead.", lambda s: dolphin_wii_profiles.profile_body(s))
_register_style_page(
    "dolphin_wii_dock_nunchuk", _WII_BE,
    _seat_rows("docked_profile_nunchuk", _wii_profiles, _wii_profiles_no_sinden),
    "Players",
    "Wiimote-plus-Nunchuk games (GameTDB plus the curated WiiWare list), docked. "
    + _WII_SEAT_NOTE
    + " A Sinden profile on Player 1 turns the whole launch into a lightgun launch and the "
      "other rows are ignored.", lambda s: dolphin_wii_profiles.profile_body(s))
_register_style_page(
    "dolphin_wii_hh_classic", _WII_BE,
    _seat_rows("undocked_profile", _cc_profiles, _cc_profiles, _CC_DEFAULT_LABEL),
    "Players",
    "Classic-capable games, handheld. Player 1 is the Deck itself and keeps the built-in "
    "Deck Classic-Controller default unless you pick another profile; players 2-4 seat "
    "external pads next to the Deck. An unset row keeps that Wii Remote slot off. Profiles "
    "are created in Dolphin itself (desktop mode); each is bound to the controller it was "
    "authored on. A per-game pick (On-the-go, Wii, Per-game) wins over these rows.",
    lambda s: dolphin_wii_profiles.profile_body(s),
    implicit={"undocked_profile": dolphin_wii_profiles.HANDHELD_DEFAULT})
_register_style_page(
    "dolphin_wii_hh_sideways", _WII_BE,
    _seat_rows("undocked_profile_sideways", _wii_profiles, _wii_profiles_no_sinden),
    "Players",
    "Sideways-Wiimote games (curated list), handheld. Player 1 is normally the Deck; "
    "players 2-4 seat external pads next to the Deck. " + _WII_SEAT_NOTE,
    lambda s: dolphin_wii_profiles.profile_body(s))
_register_style_page(
    "dolphin_wii_hh_nunchuk", _WII_BE,
    _seat_rows("undocked_profile_nunchuk", _wii_profiles, _wii_profiles_no_sinden),
    "Players",
    "Wiimote-plus-Nunchuk games (GameTDB plus the curated WiiWare list), handheld. Player 1 "
    "is normally the Deck; players 2-4 seat external pads next to the Deck. " + _WII_SEAT_NOTE,
    lambda s: dolphin_wii_profiles.profile_body(s))
_register_style_page(
    "dolphin_gc_hh_profiles", _GC_BE,
    _seat_rows("undocked_profile", _gc_profiles, _gc_profiles),
    "Players",
    "Handheld GameCube player seats. Each row loads one Dolphin GCPad profile into that "
    "port at a handheld launch; '(none)' leaves the port exactly as your normal setup has "
    "it. Player 1 is normally the Deck; players 2-4 seat external pads next to the Deck. "
    "Profiles are created in Dolphin itself (desktop mode); each is bound to the controller "
    "it was authored on. A per-game pick wins over these rows. Applied at launch, reverted "
    "on exit.", lambda s: dolphin_profiles.profile_body(s))


# ── per-game seat pages ───────────────────────────────────────────────────────
def _register_pergame(ns: str, backend: str, rows, note_fn, body_fn) -> None:
    """One per-game seat page: an enum picker row per player against the game's policy
    entry ('(inherit global)' falls back per row). Same fresh-list validation, on-disk
    requirement, and same-game duplicate rejection as the global pages."""
    @method(f"{ns}.get", slow=True)
    def _get(params):
        tid = hh._tid(params)
        entry = _pergame_entry(backend, tid)
        settings = []
        for key, label, stems_fn in rows:
            opts, val = _options(_INHERIT, stems_fn(), str(entry.get(key, "") or ""))
            settings.append({"key": key, "label": label, "type": "enum",
                             "options": opts, "value": val, "picker": True})
        return {"exists": True, "running": False, "note": note_fn(tid),
                "groups": [{"title": "Input profiles", "note": "", "settings": settings}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        tid = hh._tid(params)
        key = params.get("key")
        row = next((r for r in rows if r[0] == key), None)
        if row is None:
            raise RpcError("EINVAL", f"{key!r} is not a profile setting")
        entry = _pergame_entry(backend, tid)
        opts, _cur = _options(_INHERIT, row[2](), str(entry.get(key, "") or ""))
        idx = _parse_idx(params, opts)
        if idx != 0:
            _require_on_disk(opts[idx], body_fn)
            for k2, _lbl, _o in rows:
                if k2 != key and str(entry.get(k2, "") or "") == opts[idx]:
                    raise RpcError("EINVAL", _DUP_MSG)
        _store_pergame(backend, tid, key, None if idx == 0 else opts[idx])
        return {"key": key, "value": idx}


def _pergame_seat_rows(base: str, p1_opts, rest_opts) -> list[tuple]:
    rows = [(_seat_key(base, 1), "Player 1", p1_opts)]
    rows += [(_seat_key(base, n), f"Player {n}", rest_opts) for n in (2, 3, 4)]
    return rows


def _wii_pg_note(tid: str) -> str:
    note = (f"Detected style: {wii_style(tid)}. Player rows seat THIS game's docked players "
            "1 to 4, used only with no DolphinBar; '(inherit global)' falls back per row to "
            "the style pages under Input, Wii. Each profile is bound to the controller it "
            "was authored on, so pick a different profile per player. A Sinden profile on "
            "Player 1 gives this game the full lightgun setup and the other rows are "
            "ignored (Sinden-collection games already get it automatically). For a "
            "Classic-capable game these rows apply only when Player 1 is set here; "
            "otherwise the Classic controller order page seats the players. Applied at "
            "launch, reverted on exit.")
    if dolphin_wii_source.style(tid) == "other":
        if dolphin_wii_tdb.has_input_data(tid):
            note += (" GameTDB lists only pointer or motion input for this game, so no "
                     "style default applies; picks here are how pads get seated.")
        else:
            note += (" This game is not in GameTDB at all, so its style cannot be "
                     "detected; pick profiles here, or curate its id into the sideways or "
                     "nunchuk list.")
    return note


def _gc_pg_note(_tid: str) -> str:
    return ("Docked GameCube player seats for THIS game. Each row loads one profile into "
            "that port; '(inherit global)' keeps the normal setup for that port (the Pads "
            "to players order still fills it). Each profile is bound to the controller it "
            "was authored on. Applied at launch, reverted on exit. Profiles are created in "
            "Dolphin itself (desktop mode).")


def _gc_hh_note(_tid: str) -> str:
    return ("Handheld GameCube player seats for THIS game. Each row loads one profile into "
            "that port at a handheld launch; '(inherit global)' falls back per row to the "
            "Player profiles page, and an unset seat leaves the port as your normal setup "
            "has it. Applied at launch, reverted on exit. Profiles are created in Dolphin "
            "itself (desktop mode).")


_register_pergame("dolphin_wii_pg_profiles", _WII_BE,
                  _pergame_seat_rows("docked_profile", _wii_profiles, _wii_profiles_no_sinden),
                  _wii_pg_note, lambda s: dolphin_wii_profiles.profile_body(s))
_register_pergame("dolphin_gc_pg_profiles", _GC_BE,
                  _pergame_seat_rows("docked_profile", _gc_profiles, _gc_profiles),
                  _gc_pg_note, lambda s: dolphin_profiles.profile_body(s))
_register_pergame("dolphin_gc_hh", _GC_BE,
                  _pergame_seat_rows("handheld_profile", _gc_profiles, _gc_profiles),
                  _gc_hh_note, lambda s: dolphin_profiles.profile_body(s))


@method("dolphin_gc_hh.games", slow=True)
def _gc_hh_games(params):
    # The On-the-go GC per-game browser: every GC game (same listing as the tile's per-game
    # menu; the badge there means a GameSettings override, which is fine — the page itself
    # shows the current profile picks).
    return dolphin_games._listing("gc")
