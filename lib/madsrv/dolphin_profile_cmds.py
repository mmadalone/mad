"""Dolphin (Wii + GameCube) docked/handheld input-profile pickers — global + per-game.

Profiles are AUTHORED in Dolphin's own UI (Profiles/Wiimote + Profiles/GCPad; the MAD
Button-mapping editors were phased out 2026-08-04); MAD only lists + picks them, and the
launch rails apply the pick transiently (lib/dolphin_wii_source for Wii,
lib/dolphin_gc_dock for GC). Context placement follows the panel convention (the DOOR bakes
the context): the Wii/GameCube tile opens the DOCKED namespaces, On-the-go the HANDHELD ones.

Wii semantics: a profile applies ONLY when NO DolphinBar is connected (a bar always routes
real Wii Remotes; Sinden-collection lightgun games always use the automatic gun rail). With
no explicit per-game pick, the launch decider auto-detects the game's STYLE and uses that
style's global default:
  CLASSIC  (GameTDB classiccontroller / force_cc)  -> the existing CC rails (unchanged)
  SIDEWAYS (curated data/gametdb/sideways_ids.json) -> "Sideways games" profile
  NUNCHUK  (GameTDB nunchuk control)                -> "Nunchuk games" profile
  OTHER    (pointer / unknown)                      -> "Other games" profile
A Sinden* profile is pickable (per-game lightgun outside the collection); the launch apply
reroutes it through the FULL sinden mode, never a bare body copy.

Namespaces (all data-only pages; policy-store writes, no EBUSY):
  dolphin_wii_dock      Wii tile   "Docked profiles"   [backends.dolphin_wii].docked_profile_*
  dolphin_wii_dock_hh   On-the-go  "Handheld profiles" [backends.dolphin_wii].undocked_profile*
  dolphin_wii_pg_profiles  Wii tile per-game  "Docked profile"  [..dolphin_wii.pergame.<ID>]
  dolphin_gc_pg_profiles   GC tile per-game   "Docked profile"  [..dolphin_gc.pergame.<ID>]
  dolphin_gc_hh            On-the-go GC per-game "Handheld profile" (same GC pergame table)
"""
from __future__ import annotations

from .. import dolphin_profiles, dolphin_wii_profiles, dolphin_wii_tdb, localpolicy
from ..policy import LOCAL, load_merged
from . import dolphin_games
from . import dolphin_wii_hh_cmds as hh
from .rpc import RpcError, method

_NONE = "(none)"
_INHERIT = "(inherit global)"
_CC_DEFAULT_LABEL = f"(default: {dolphin_wii_profiles.HANDHELD_DEFAULT})"


def _be(backend: str) -> dict:
    be = (load_merged().get("backends") or {}).get(backend)
    return be if isinstance(be, dict) else {}


def _wii_profiles() -> list[str]:
    return dolphin_wii_profiles.list_profiles(None, include_sinden=True)


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


def wii_style(tid: str) -> str:
    """The detected input style for the per-game page note + the launch ladder's labels."""
    if dolphin_wii_tdb.is_cc_capable(tid) or bool(hh._entry(tid).get("force_cc")):
        return "Classic controller"
    if dolphin_wii_tdb.is_sideways(tid):
        return "Sideways (curated)"
    if dolphin_wii_tdb.is_nunchuk(tid):
        return "Nunchuk (GameTDB)"
    return "Pointer/other"


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


# ── global style pages (Wii): docked on the tile, handheld on On-the-go ───────
_WII_BE = "dolphin_wii"
# (key, row label, options builder, zero-sentinel)
_DOCK_ROWS = [
    ("docked_profile_sideways", "Sideways games", _wii_profiles, _NONE),
    ("docked_profile_nunchuk", "Nunchuk games", _wii_profiles, _NONE),
    ("docked_profile_other", "Other games", _wii_profiles, _NONE),
]
_HH_ROWS = [
    # The Classic row keeps the CC rail's semantics: Classic profiles only, built-in default.
    ("undocked_profile", "Classic games", dolphin_wii_profiles.list_profiles, _CC_DEFAULT_LABEL),
    ("undocked_profile_sideways", "Sideways games", _wii_profiles, _NONE),
    ("undocked_profile_nunchuk", "Nunchuk games", _wii_profiles, _NONE),
    ("undocked_profile_other", "Other games", _wii_profiles, _NONE),
]


def _register_wii_dock(ns: str, rows, title: str, note: str) -> None:
    @method(f"{ns}.get", slow=True)
    def _get(params):
        be = _be(_WII_BE)
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
        opts, _cur = _options(row[3], row[2](), str(_be(_WII_BE).get(key, "") or ""))
        idx = _parse_idx(params, opts)
        if idx != 0:
            _require_on_disk(opts[idx], dolphin_wii_profiles.profile_body)
        _set_backend_key(_WII_BE, key, None if idx == 0 else opts[idx])
        return {"key": key, "value": idx}


_register_wii_dock(
    "dolphin_wii_dock", _DOCK_ROWS, "Docked profiles (no DolphinBar)",
    "Docked defaults per game STYLE, used only when NO DolphinBar is connected (a bar always "
    "routes real Wii Remotes; Sinden-collection games always use the gun rail). Classic-capable "
    "games use the Classic controller order page. The style is auto-detected (GameTDB + a curated "
    "sideways list); a per-game pick (Per-game → Wii games → Input profiles) wins over these. "
    "A profile seats Player 1 only; '(none)' keeps today's behavior. Profiles are created in "
    "Dolphin itself (desktop mode).")
_register_wii_dock(
    "dolphin_wii_dock_hh", _HH_ROWS, "Handheld profiles",
    "Handheld defaults per game STYLE (no DolphinBar handheld play). Classic games keep the "
    "built-in Deck Classic-Controller default unless you pick another Classic profile. The style "
    "is auto-detected; a per-game pick (On-the-go → Wii → Per-game) wins over these. Profiles "
    "are created in Dolphin itself (desktop mode).")


# ── per-game pickers ──────────────────────────────────────────────────────────
def _register_pergame(ns: str, backend: str, key: str, row_label: str,
                      stems_fn, note_fn, body_fn) -> None:
    @method(f"{ns}.get", slow=True)
    def _get(params):
        tid = hh._tid(params)
        cur = str(_pergame_entry(backend, tid).get(key, "") or "")
        opts, val = _options(_INHERIT, stems_fn(), cur)
        return {"exists": True, "running": False, "note": note_fn(tid),
                "groups": [{"title": "Input profile", "note": "", "settings": [
                    {"key": key, "label": row_label, "type": "enum",
                     "options": opts, "value": val, "picker": True}]}]}

    @method(f"{ns}.set", slow=True)
    def _set(params):
        tid = hh._tid(params)
        if params.get("key") != key:
            raise RpcError("EINVAL", f"{params.get('key')!r} is not a profile setting")
        cur = str(_pergame_entry(backend, tid).get(key, "") or "")
        opts, _cur = _options(_INHERIT, stems_fn(), cur)
        idx = _parse_idx(params, opts)
        if idx != 0:
            _require_on_disk(opts[idx], body_fn)
        _store_pergame(backend, tid, key, None if idx == 0 else opts[idx])
        return {"key": key, "value": idx}


def _wii_pg_note(tid: str) -> str:
    return (f"Detected style: {wii_style(tid)}. The docked profile for THIS game, used only "
            "with no DolphinBar ('(inherit global)' falls back to the style default on "
            "Input → Wii → Docked profiles). Seats Player 1 only — leave inherited for "
            "multi-pad Classic play. A Sinden profile gives this game the full lightgun setup; "
            "Sinden-collection games already get it automatically. Applied at launch, reverted "
            "on exit.")


def _gc_pg_note(_tid: str) -> str:
    return ("The docked GameCube profile for THIS game ('(inherit)' keeps the Pads-to-players "
            "order). Loads into Port 1 only; ports 2-4 keep your normal setup. Applied at "
            "launch, reverted on exit. Profiles are created in Dolphin itself (desktop mode).")


def _gc_hh_note(_tid: str) -> str:
    return ("The handheld GameCube profile for THIS game ('(inherit global)' uses the "
            "Dock / handheld undocked profile). Loaded into Port 1 at a handheld launch, "
            "reverted on exit. Profiles are created in Dolphin itself (desktop mode).")


_register_pergame("dolphin_wii_pg_profiles", "dolphin_wii", "docked_profile",
                  "Docked profile", _wii_profiles, _wii_pg_note,
                  dolphin_wii_profiles.profile_body)
_register_pergame("dolphin_gc_pg_profiles", "dolphin_gc", "docked_profile",
                  "Docked profile", _gc_profiles, _gc_pg_note,
                  dolphin_profiles.profile_body)
_register_pergame("dolphin_gc_hh", "dolphin_gc", "handheld_profile",
                  "Handheld profile", _gc_profiles, _gc_hh_note,
                  dolphin_profiles.profile_body)


@method("dolphin_gc_hh.games", slow=True)
def _gc_hh_games(params):
    # The On-the-go GC per-game browser: every GC game (same listing as the tile's per-game
    # menu; the badge there means a GameSettings override, which is fine — the page itself
    # shows the current profile pick).
    return dolphin_games._listing("gc")
