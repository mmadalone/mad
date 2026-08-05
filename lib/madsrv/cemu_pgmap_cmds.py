"""cemu_pgmap_docked / cemu_pgmap_handheld — the Wii U PER-GAME input-profile pickers.

One game can override any controller FAMILY's profile, per context, on top of the global map on the
Wii U tile's Input page. The launch binder resolves the title id from the rom and applies the
override through the normal seating path (lib/cemu_seat -> lib/cemu_profiles.resolve).

THIS IS NOT Cemu's own per-game pin. ``gameProfiles/<tid>.ini [Controller] controllerN = <name>``
(the "Cemu's own pin" page, lib/madsrv/cemu_pg_input_cmds) pins a DEVICE to a PORT and Cemu applies
it at boot AFTER and OVER whatever the binder wrote -- it bypasses seating entirely. That page
stays, deliberately, as the advanced escape hatch; this one feeds seating instead, and both pages
warn about the other so the conflict can never be hit blind.

Context placement follows the panel convention (the DOOR bakes the context): the Wii U tile's
per-game tree opens ``cemu_pgmap_docked``; the On-the-go Wii U tile opens ``cemu_pgmap_handheld``.

Store: ``[backends.cemu.pergame.<titleid>.<context>]`` in controller-policy.local.toml, ``{family:
stem}`` -- the same shape as the global map one level up, and the same file, so per-game input state
is backed up and hand-editable exactly like everything else Wii U. Sparse by construction: clearing
the last family prunes the context table, then the game, then ``pergame`` itself.

No EBUSY: this store is MAD's own and is applied at the game's NEXT launch, so Cemu may be running.
(Contrast cemu_pg_input_cmds, which DOES refuse while Cemu runs -- it writes a file Cemu rewrites
on exit.)
"""
from __future__ import annotations

import re

from .. import cemu_profiles, localpolicy, mad_config
from ..policy import LOCAL, load_merged
from . import cemu_games
from . import cemu_input_cmds as cin
from .rpc import RpcError, method

_TID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_GAMEPAD_FAMILY = "Steam Deck"
_GAMEPAD_TYPE = "Wii U GamePad"

_NOTE = ("Input profiles for this game only. A row left on 'Same as all games' uses your {ctx} map "
         "from {src}. Profiles are created in Cemu itself; the pick is applied at launch and "
         "reverted on exit.")
# Where the all-games map for each context actually lives. The handheld page used to point at "the
# Wii U tile's Input page", which is the DOCKED one (standalones_cmds row "cemu_input_docked"), so
# it named the wrong page to go and edit.
_SRC = {"docked": "the Wii U tile's Input page",
        "handheld": "On-the-go, Wii U, Input"}
_PIN_WARN = ("This game also has a Cemu per-game pin (Per-game -> Cemu's own pin) on Controller "
             "{ports}. Cemu applies that AFTER MAD, so those ports override everything below. ")


def _tid(params) -> str:
    t = (params.get("titleid") or "").strip().lower()
    if not _TID_RE.match(t):
        raise RpcError("EINVAL", f"bad game id {t!r}")
    return t


def _cfg() -> dict:
    be = (load_merged().get("backends") or {}).get("cemu")
    return be if isinstance(be, dict) else {}


def _slice(cfg: dict, tid: str, context: str) -> dict:
    s = cemu_profiles.pergame_slice(cfg, tid).get(context)
    return s if isinstance(s, dict) else {}


def _inherited(cfg: dict, pergame_all: dict, family: str, context: str) -> str | None:
    """What would apply to ``family`` if THIS row were cleared: the launch's own chain, minus this
    game's override for this family in this context.

    Not simply ``profile_for`` (the global-only entry point): with handheld_mirrors_docked on, the
    mirror tier consults this GAME's docked pick before the global docked one, so the label has to
    see the rest of the game's slice or it names a profile the launch will not use."""
    trimmed = {c: dict(v) for c, v in (pergame_all or {}).items() if isinstance(v, dict)}
    if context in trimmed:
        trimmed[context].pop(family, None)
    return cemu_profiles.resolve(trimmed, cfg, family, context)


def _options(cfg: dict, family: str, current: str | None, inherited: str | None):
    """``[inherit] + the profiles whose emulated <type> suits this family`` (+ the current pick,
    kept visible so a stale value can be seen and cleared). Slot 0 NAMES what it inherits, so the
    page never says a bare "(none)" when a global map entry is actually in force."""
    by_type = cin._profile_stems_by_type(cfg)
    # EXACTLY the gate the global Wii U Input page uses (cemu_input_cmds._opts). A looser test
    # ("anything that is not a GamePad", or "unknown type is fine") offered Wiimote/Classic-typed
    # profiles here that the global page refuses; cemu_seat then force-rewrites <type> to Pro
    # WITHOUT retranslating the button ids, so the pad plays with the wrong numbering and nothing
    # reports an error.
    want = _GAMEPAD_TYPE if family == _GAMEPAD_FAMILY else cin._PRO_TYPE
    stems = [s for s, t in by_type.items() if t == want]
    # Slot 0 reads like the other Wii U handheld pages' rest state, but says "all games" rather than
    # "docked": what this row falls back to is the map one level up (On-the-go > Wii U > Input for
    # handheld, the Wii U tile's Input page for docked), NOT the other context. Only with
    # handheld_mirrors_docked on does docked come into it, and even then the name is what matters.
    zero = f"Same as all games: {inherited}" if inherited else "Same as all games"
    opts = [zero] + sorted(stems)
    if current and current not in opts:
        opts.append(current)
    return opts, (opts.index(current) if current and current in opts else 0)


def _store(tid: str, context: str, family: str, stem: str | None) -> None:
    """Set/clear one family's per-game override, pruning every table it empties."""
    data = localpolicy.load(LOCAL)
    be = data.setdefault("backends", {}).setdefault("cemu", {})
    pg = be.setdefault("pergame", {})
    game = pg.setdefault(tid, {})
    ctx = game.setdefault(context, {})
    if stem is None:
        ctx.pop(family, None)
    else:
        ctx[family] = stem
    for tbl, key, parent in ((ctx, context, game), (game, tid, pg), (pg, "pergame", be)):
        if isinstance(tbl, dict) and not tbl:
            parent.pop(key, None)
    if not be:
        data.get("backends", {}).pop("cemu", None)
    if not data.get("backends"):
        data.pop("backends", None)
    localpolicy.dump(LOCAL, data)          # atomic + staterev.bump("config")


def _register(ns: str, context: str, with_games: bool) -> None:
    ctx_label = "docked" if context == "docked" else "handheld"

    if with_games:
        @method(f"{ns}.games", slow=True)
        def _games(params, _ctx=context):
            cfg = _cfg()

            def _summary(tid):
                s = _slice(cfg, tid, _ctx)
                return ", ".join(f"{f}: {v}" for f, v in sorted(s.items())) if s else ""

            return {"games": cemu_games.listing(
                override_fn=lambda t: bool(_slice(cfg, t, _ctx)), summary_fn=_summary),
                "system": cemu_games._ESDE_SYSTEM}

    @method(f"{ns}.get", slow=True)
    def _get(params, _ctx=context):
        tid = _tid(params)
        cfg = _cfg()
        mine = _slice(cfg, tid, _ctx)
        rows = []
        pergame_all = cemu_profiles.pergame_slice(cfg, tid)
        for f in cin._families():
            cur = mine.get(f) if isinstance(mine.get(f), str) else None
            inherited = _inherited(cfg, pergame_all, f, _ctx)
            opts, val = _options(cfg, f, cur, inherited)
            rows.append({"key": f"family:{f}", "label": f, "type": "enum",
                         "options": opts, "value": val, "picker": True})
        note = _NOTE.format(ctx=ctx_label, src=_SRC[_ctx])
        pins = _native_pins(tid)
        if pins:
            note = _PIN_WARN.format(ports=", ".join(str(p) for p in pins)) + note
        return {"exists": True, "running": False, "note": note,
                "groups": [{"title": f"{ctx_label.capitalize()} map", "note": "",
                            "settings": rows}]}

    @method(f"{ns}.set", slow=True)
    def _set(params, _ctx=context):
        tid = _tid(params)
        key = str(params.get("key") or "")
        if not key.startswith("family:"):
            raise RpcError("EINVAL", f"{key!r} is not a profile setting")
        family = key.split(":", 1)[1]
        if family not in cin._families():
            raise RpcError("EINVAL", f"unknown controller family {family!r}")
        cfg = _cfg()
        mine = _slice(cfg, tid, _ctx)
        cur = mine.get(family) if isinstance(mine.get(family), str) else None
        opts, _v = _options(cfg, family, cur,
                            _inherited(cfg, cemu_profiles.pergame_slice(cfg, tid), family, _ctx))
        try:
            idx = int(float(params.get("value")))
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "bad profile index")
        if not (0 <= idx < len(opts)):
            raise RpcError("EINVAL", "profile index out of range")
        if idx != 0 and not (cin._config_dir(cfg) / f"{opts[idx]}.xml").is_file():
            raise RpcError("EINVAL",
                           "profile list changed on disk, reopen this page and pick again")
        _store(tid, _ctx, family, None if idx == 0 else opts[idx])
        return {"key": key, "value": idx}


def _native_pins(tid: str) -> list[int]:
    """The ports Cemu's own per-game ini pins for this title, for the page warning.

    Returned VERBATIM as written in the ini, which is 1-BASED (`controller1`..`controller8`, the same
    numbering cemu_pg_input_cmds labels its rows with). Do not add 1: doing so named the neighbouring
    port, so the user cleared the wrong row and the real pin kept overriding the seat."""
    try:
        text, _ = cemu_games.read_ini(cemu_games.pergame_path(tid))
        if not text:
            return []
        return sorted(int(m) for m in re.findall(r"^\s*controller(\d+)\s*=", text, re.M))
    except Exception:
        return []


_register("cemu_pgmap_docked", "docked", with_games=False)
# with_games=False: the game-first On-the-go menu owns the browser now (cemuhh.games),
# so this page is only ever opened with a title already picked. Two browsers would mean
# two answers to "which games are customised".
_register("cemu_pgmap_handheld", "handheld", with_games=False)
