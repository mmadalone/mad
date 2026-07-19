"""mugen_hhres.* / mugen_hhres_pg.* - MUGEN handheld render-resolution knobs for the
On-the-go tile (general + per-game). Applied at launch by lib/mugen_res (aspect-preserving
config.ini GameWidth/Height downshift). The watt cap is the shared framework page
(onthego_mugen); this is only the mugen-specific resolution, which the framework's multiplier
rail cannot express (game aspects differ, so we scale each game's own resting size).

  general  -> [systems.mugen.handheld].res             (full|high|medium|low)
  per-game -> [backends.mugen.pergame.<folder>].hhres   (unset = inherit the all-games value)
Keyed by the game's config FOLDER (what mugen.sh has), mapped from the browser's titleid.
"""
from __future__ import annotations

from .. import localpolicy, mugen_res, staterev
from ..policy import LOCAL, load_merged
from . import mugen_cmds
from .rpc import RpcError, method

_OPTS = [("full", "Full (100%)"), ("high", "High (80%)"),
         ("medium", "Medium (65%)"), ("low", "Low (50%)")]
_TOK = [t for t, _ in _OPTS]
_LAB = [lb for _, lb in _OPTS]
_PG_TOK = ["inherit"] + _TOK
_PG_LAB = ["Same as all-games"] + _LAB
_NOTE = ("Lower the render resolution in HANDHELD to save battery: each game is scaled from its "
         "own resting size (aspect kept; docked always runs full). gamescope upscales to the screen.")


def _write(path_keys, key, value, *, remove=False):
    data = localpolicy.load(LOCAL)
    blk = data
    for k in path_keys:
        blk = blk.setdefault(k, {})
    if remove:
        blk.pop(key, None)
    else:
        blk[key] = value
    localpolicy.dump(LOCAL, data)


def _page(tokens, labels, cur, note):
    tok = str(cur or tokens[0]).strip().lower()
    idx = tokens.index(tok) if tok in tokens else 0
    return {"exists": True, "running": False, "note": note,
            "groups": [{"title": "Handheld resolution", "note": "", "settings": [
                {"key": "res", "label": "Render scale (handheld)", "type": "enum",
                 "value": idx, "options": labels, "picker": True}]}]}


def _folder(titleid: str) -> str:
    if not titleid:
        raise RpcError("EINVAL", "needs a titleid")
    return mugen_cmds._config_ini(titleid).parent.parent.name


def _pg_labels(titleid: str) -> list:
    """Per-game picker labels. Unlike the all-games scale (which spans games of different
    resting sizes, so only a percent is honest), a single game HAS a known resolution -- so
    label each step with the REAL pixels THIS game renders at handheld (full = its resting
    size, computed with the same math apply() writes). Falls back to the plain percentages
    when the game has no config yet / is unreadable."""
    try:
        dims = mugen_res.resting_dims(mugen_cmds._config_ini(titleid))
    except Exception:
        dims = None
    if not dims:
        return list(_PG_LAB)
    gw, gh = dims
    out = [_PG_LAB[0]]                              # "Same as all-games"
    for tok, base in zip(_TOK, _LAB):              # full/high/medium/low
        name = base.split(" (")[0]                 # "Full" / "High" / "Medium" / "Low"
        w, h = (gw, gh) if tok == "full" else mugen_res.scale_dims(gw, gh, mugen_res.PCT[tok])
        out.append(f"{name} ({w}x{h})")
    return out


@method("mugen_hhres.get", slow=True)
def _gen_get(params):
    hh = load_merged().get("systems", {}).get("mugen", {}).get("handheld", {})
    return _page(_TOK, _LAB, hh.get("res") if isinstance(hh, dict) else None,
                 "All games. " + _NOTE)


@method("mugen_hhres.set", slow=True)
def _gen_set(params):
    v = params.get("value")
    tok = _TOK[v] if isinstance(v, int) and 0 <= v < len(_TOK) else "full"
    _write(["systems", "mugen", "handheld"], "res", tok)
    staterev.bump("config")
    return {"key": "res", "value": v}


@method("mugen_hhres_pg.get", slow=True)
def _pg_get(params):
    titleid = params.get("titleid", "")
    folder = _folder(titleid)
    pg = load_merged().get("backends", {}).get("mugen", {}).get("pergame", {}).get(folder, {})
    cur = (pg.get("hhres") if isinstance(pg, dict) else None) or "inherit"
    return _page(_PG_TOK, _pg_labels(titleid), cur,
                 "This game only (overrides all-games). " + _NOTE)


@method("mugen_hhres_pg.set", slow=True)
def _pg_set(params):
    folder = _folder(params.get("titleid", ""))
    v = params.get("value")
    tok = _PG_TOK[v] if isinstance(v, int) and 0 <= v < len(_PG_TOK) else "inherit"
    if tok == "inherit":
        _write(["backends", "mugen", "pergame", folder], "hhres", None, remove=True)
    else:
        _write(["backends", "mugen", "pergame", folder], "hhres", tok)
    staterev.bump("config")
    return {"key": "res", "value": v}
