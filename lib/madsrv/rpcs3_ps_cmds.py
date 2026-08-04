"""rpcs3ps.input_* — the PS-button chord page for the PlayStation 3 tile (RPCS3).

The ONE per-button mapping that survived the input-profile migration: the PS button can be a
CHORD (kind "chord" — the shared capture modal accumulates every simultaneously-held button
and returns them as `codes`), e.g. Select+Start, serialized as RPCS3's AND syntax
``Back&Start`` (input_translate.rpcs3_button per SDL name, joined with `&` — RPCS3's own
``pad::combo::to_string`` format). This lets a pad with no Home button open RPCS3's home
menu. Gameplay buttons are no longer mapped here — input PROFILES own the layout (authored
in RPCS3's Gamepads dialog, picked on the Input-profiles pages).

Storage: the context-keyed MAD override sidecar (rpcs3_cfg.load_overrides/save_overrides,
``.mad-input-overrides.yml``) — docked and handheld carry their own chord (the docked tile
door omits params["context"]; the On-the-go door sends "handheld"). The launch rail merges
ONLY the PS-button slice (rpcs3_cfg.ps_button_overrides) into the transient SDL profile, so
the chord layers over whatever profile was picked. Legacy gameplay keys living in an old
sidecar are preserved on disk (the buffered save replays onto a FRESH full store read —
never destroy user data) but are inert at launch.

Relocated from the retired per-button editor (rpcs3_input_cmds.py), trimmed to the chord
path; the helpers live HERE because this page is their only consumer and they raise
RpcError (madsrv-layer — lib/rpcs3_cfg must stay importable by the controller-router).
"""
from __future__ import annotations

try:
    import yaml
except ImportError:                    # PyYAML missing → cannot read the resting config
    yaml = None

from .. import handheld_input, rpcs3_cfg, rpcs3_profiles
from ..policy import load_merged
from .input_buffer import InputBuffer
from .input_translate import rpcs3_button, rpcs3_token_label
from .rpc import RpcError, method

_COMBO_KEYS = ("PS Button",)           # == rpcs3_cfg.PS_KEYS: the surviving mappable key(s)
_LABEL = {"PS Button": "PS"}


def _display(tok) -> str:
    """Friendly value for a stored token, combo-aware: `Back&Start` -> "Select + Start"
    (RPCS3 `&` = AND). Shows the primary combo (before any `,` OR-alternative). "—" if unset."""
    if not tok:
        return "—"
    primary = str(tok).split(",", 1)[0]
    if "&" in primary:
        return " + ".join(rpcs3_token_label(t) for t in primary.split("&") if t.strip())
    return rpcs3_token_label(tok)


def _combo_token(codes) -> str:
    """RPCS3 combo source token: the held buttons' SDL names joined by `&` (RPCS3's AND
    syntax, e.g. Select+Start -> `Back&Start`; matches `pad::combo::to_string`). A single held
    button -> a single token. Dedups a repeated button; raises EINVAL on empty/unmappable."""
    seen: set = set()
    toks: list = []
    for c in codes or []:
        try:
            code = int(c)
        except (TypeError, ValueError):
            raise RpcError("EINVAL", "invalid button in the combo")
        tok = rpcs3_button(code)
        if tok is None:
            raise RpcError("EINVAL", "one of those buttons can't be mapped — use a face, "
                                     "shoulder, trigger, stick-click, Select, Start or PS button")
        if tok not in seen:
            seen.add(tok)
            toks.append(tok)
    if not toks:
        raise RpcError("EINVAL", "hold the buttons together (e.g. Select + Start)")
    return "&".join(toks)


def _token_for(key: str, kind: str, value: str, codes=None) -> str:
    """The RPCS3 source token for one captured input. Only the PS-button chord survives the
    profile migration; anything else is not remappable here."""
    if key in _COMBO_KEYS and (kind == "chord" or codes):
        combo = list(codes) if codes else ([value] if str(value).strip() else [])
        return _combo_token(combo)
    raise RpcError("EINVAL", f"{key!r} is not a remappable RPCS3 input "
                             "(gameplay buttons are set by Input profiles)")


def _resting() -> dict:
    """The RESTING input config as a dict (for player count + effective values), or {}.
    Ladder-aware: reads the file the next launch will actually target (per-title rungs
    don't apply here — this is the global page, serial=None)."""
    if yaml is None:
        return {}
    try:
        be = (load_merged().get("backends") or {}).get("rpcs3") or {}
        target = rpcs3_profiles.resting_target(be, None)
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _player_count(data: dict) -> int:
    """How many players to offer = configured (non-Null) `Player N Input` blocks,
    walked densely from 1 (the launch wrapper assigns pads to players 1..N in order, so
    a dense Player index == the launch slot the chord applies to). Min 1."""
    n = 0
    for k in range(1, 8):
        b = data.get(f"Player {k} Input")
        if isinstance(b, dict) and b.get("Handler") not in (None, "Null"):
            n = k
        else:
            break
    return max(n, 1)


def _profile_cfg(context, player: int) -> dict:
    """The picked GLOBAL profile's Config for `player` in `context`, or {} — the layer the
    launch injects between the MAD chord and the resting config (REVIEW FIX: the displayed
    effective value must mirror the launch merge, which since the profile migration reads
    the picked profile's blocks, compacted, before the resting layout). Per-game picks are
    not reflected here (this is the tile-global page). Best-effort: {} on any failure."""
    try:
        be = (load_merged().get("backends") or {}).get("rpcs3") or {}
        stem = rpcs3_profiles.global_profile(be, context)
        if not stem:
            return {}
        ppath = rpcs3_profiles.profile_path(rpcs3_profiles.profiles_dir(be), stem)
        if ppath is None:
            return {}
        pdata = yaml.safe_load(ppath.read_text(encoding="utf-8")) or {}
        blocks = rpcs3_cfg.sdl_player_blocks(pdata)     # the same compaction the launch does
        return blocks[player - 1][1]["Config"] if 0 <= player - 1 < len(blocks) else {}
    except Exception:
        return {}


def _resolve_player(params, count: int) -> int:
    raw = params.get("player")
    if raw in (None, ""):
        return 1                       # first load → Player 1
    try:
        i = int(raw)
    except (TypeError, ValueError):
        raise RpcError("EINVAL", f"invalid player {raw!r}")
    if not 1 <= i <= count:            # reject (don't silently misdirect to Player 1)
        raise RpcError("EINVAL", f"Player {i} isn't available (you have {count})")
    return i


@method("rpcs3ps.input_get", slow=True)   # buffered: NO cache=("config",) — the buffer is truth
def _input_get(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot read RPCS3 input config")
    data = _resting()
    count = _player_count(data)
    players = [{"id": str(n), "label": f"Player {n}"} for n in range(1, count + 1)]
    player = _resolve_player(params, count)
    ctx = _ctx(params)
    ovp = _buf.get(ctx).get(player, {})            # buffer-over-disk: reflects staged edits
    # Effective value mirrors what the launch merge binds in-game: the MAD chord wins, else
    # the PICKED profile's PS binding (the launch injects the profile before seating), else
    # the resting SDL Config's PS Button (preserved by _player_block), else the template.
    rblock = data.get(f"Player {player} Input")
    resting_cfg = (rblock.get("Config") if isinstance(rblock, dict)
                   and rblock.get("Handler") == "SDL"
                   and isinstance(rblock.get("Config"), dict) else None) or {}
    tok = (ovp.get("PS Button") or _profile_cfg(ctx[0], player).get("PS Button")
           or resting_cfg.get("PS Button")
           or rpcs3_cfg._SDL_PLAYER["Config"].get("PS Button"))
    groups = [{"title": "PS button", "binds": [
        {"id": "PS Button", "label": "PS", "kind": "chord",
         "value": _display(tok), "capturable": True}]}]
    note = (f"Hold the buttons together (e.g. Select + Start) to open RPCS3's home menu with "
            f"a chord — for Player {player}, applied when you launch a PS3 game from ES-DE "
            "over the picked input profile. Gameplay buttons are set by Input profiles.")
    return {"running": False, "note": note, "groups": groups,
            "players": players, "player": str(player),
            "buffered": True, "dirty": _buf.dirty}


@method("rpcs3ps.input_set", slow=True)
def _input_set(params):
    if yaml is None:
        raise RpcError("EINVAL", "PyYAML not available — cannot save RPCS3 input overrides")
    key = params.get("id", "")
    kind = params.get("kind", "chord")
    # Resolve/validate the target player up front (needs the resting player count), then STAGE
    # the edit — _apply computes + validates the token and raises EINVAL on a bad capture. No
    # disk write here; the chord reaches the sidecar only on rpcs3ps.input_save.
    count = _player_count(_resting())
    player = _resolve_player(params, count)
    edit = {"player": player, "id": key, "kind": kind,
            "value": str(params.get("value", "")), "codes": params.get("codes")}
    _buf.set(_ctx(params), edit)
    disp = _display(_buf.working.get(player, {}).get(key))
    return {"id": key, "value": disp, "dirty": _buf.dirty,
            "message": f"{_LABEL.get(key, key)} → {disp}"}


# ---------------------------------------------------------------------------
# Buffered plumbing (X=Save / Y=Cancel). Edits stage in the module-level InputBuffer and
# only reach the MAD override sidecar on rpcs3ps.input_save; input_cancel drops them.
# ctx = (context,) — "docked" | "handheld": the On-the-go door sets params["context"] =
# "handheld"; the docked tile door omits it -> docked. The flush replays onto a FRESH FULL
# sidecar read (rpcs3_cfg.load_overrides), so legacy gameplay keys in an old sidecar ride
# through saves untouched. Deliberately NO running-guard: RPCS3 can be edited while running
# (applies next launch).
# ---------------------------------------------------------------------------
def _ctx(params) -> tuple:
    """Buffer identity = the docked/handheld slice this page targets (from params["context"],
    default docked). Switching context reloads a separate working copy."""
    return (handheld_input.normalize(params.get("context", "docked")),)


def _apply(overrides: dict, edit: dict) -> dict:
    """Apply one staged edit to the overrides dict, returning it. Pure (no disk write, no
    bump). Replayed verbatim by the buffer's flush onto a FRESH sidecar read, so a foreign
    override to a different player/key survives."""
    player = edit["player"]
    key = edit["id"]
    token = _token_for(key, edit["kind"], str(edit.get("value", "")), edit.get("codes"))
    overrides.setdefault(player, {})[key] = token
    return overrides


def _load(ctx: tuple) -> dict:
    return rpcs3_cfg.load_overrides(context=ctx[0] if ctx else "docked")


def _apply_edit(overrides: dict, edit: dict):
    return _apply(overrides, edit), edit


def _flush(ctx: tuple, disk: dict, edits: list) -> dict:
    context = ctx[0] if ctx else "docked"
    overrides = rpcs3_cfg.load_overrides(context=context)   # replay onto FRESH sidecar
    for edit in edits:
        overrides = _apply(overrides, edit)
    rpcs3_cfg.save_overrides(overrides, context=context)
    return overrides


_buf = InputBuffer(load=_load, apply_edit=_apply_edit, flush=_flush)


@method("rpcs3ps.input_save", slow=True)
def _input_save(params):
    return {"saved": _buf.save(_ctx(params)), "dirty": _buf.dirty}


@method("rpcs3ps.input_cancel", slow=True)
def _input_cancel(params):
    _buf.cancel(_ctx(params))
    return {"cancelled": True, "dirty": _buf.dirty}
