"""daphne.* methods — the Daphne/Hypseus section (MAD native-panel phase 3).

Ports lib/mad_daphne_page.py (the Tk mixin) 1:1 on top of the Tk-free
lib.hypinput. The daemon holds the EDITING BUFFER (an HypInput) exactly like
the Tk page held self._dp_hi: daphne.load (re)loads it for a scope, edits
mutate it in memory, daphne.save writes it (.bak conventions via hypinput).
Re-entering the page reloads from disk — unsaved edits are dropped, same as
the Tk page rebuild.

daphne.bind runs lib/hypseus_capture.py as a subprocess (SDL in-process with
the panel's daemon is the same segfault risk the Tk app avoided) and emits
input.lock around it: the cabinet press also reaches ES-DE via SDL and must
not navigate the panel.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import hypinput
from .rpc import RpcError, event, method

HERE = Path(__file__).resolve().parent.parent.parent     # launchers dir
DAPHNE_ROOT = Path.home() / "ROMs" / "daphne"
GAMELIST = Path.home() / "ES-DE" / "gamelists" / "daphne" / "gamelist.xml"

PRIMARY = ("COIN1", "START1", "BUTTON1", "BUTTON2", "BUTTON3")
P2 = ("COIN2", "START2")

# The editing buffer (the Tk self._dp_hi/_dp_scope/_dp_game/_dp_dirty). dirty is
# a real buffer-vs-disk compare: disk_text is the serialized on-disk baseline,
# snapshotted at every (re)load and at save (HypInput has no __eq__).
_state = {"hi": None, "scope": "global", "gamedir": "", "base": "", "disk_text": ""}


def _adv_actions() -> list:
    """ADVANCED minus Coin 2/Start 2, which have their own page section."""
    return [a for a in hypinput.ADVANCED if a not in P2]


def _row(action: str) -> dict:
    """One action row's display state — port of _dp_update_cell."""
    hi = _state["hi"]
    bval = hi.button_value(action)
    if action in hypinput.DIRECTIONS:
        ax = hi.axis_value(action)
        display = (hypinput.button_label(bval) if bval else
                   (f"axis {ax}" if ax else "— (unbound)"))
        warn = False
    else:
        # 0 = unreachable on the stick; only the PRIMARY P1 controls warn
        # (Coin2/Start2 default to unbound, which is normal).
        warn = bval == 0 and action in PRIMARY
        display = hypinput.button_label(bval)
    return {"action": action, "label": hypinput.ACTION_LABELS.get(action, action),
            "display": display, "warn": warn}


def _rows() -> dict:
    return {a: _row(a) for a in hypinput.ACTIONS}


def _game_names() -> dict:
    """basename -> display <name> from the daphne gamelist (read-only)."""
    out = {}
    try:
        for g in ET.parse(GAMELIST).getroot().findall("game"):
            stem = Path((g.findtext("path") or "").strip()).stem
            name = (g.findtext("name") or "").strip()
            if stem and name:
                out[stem] = name
    except Exception:
        pass
    return out


def _games() -> list:
    """Every Daphne/Singe game dir under ~/ROMs/daphne, with display names."""
    names = _game_names()
    out = []
    if DAPHNE_ROOT.is_dir():
        for p in sorted(DAPHNE_ROOT.iterdir()):
            if p.is_dir() and p.suffix in (".daphne", ".singe"):
                out.append({"gamedir": str(p), "base": p.stem,
                            "name": names.get(p.stem, p.stem)})
    out.sort(key=lambda g: g["name"].lower())
    return out


def _per_game() -> bool:
    return _state["scope"] == "game" and _state["base"]


def _dirty() -> bool:
    """True when the buffer differs from the on-disk baseline. HypInput has no
    __eq__, so compare its serialized text — a re-bind back to the original
    value (or clearing an already-unbound row) correctly reads clean."""
    hi = _state["hi"]
    return hi is not None and hi.text() != _state["disk_text"]


def _reload_scope(scope: str, gamedir: str, base: str) -> None:
    """Load the on-disk map for an ALREADY-VALIDATED scope into the buffer and
    snapshot it as the clean baseline (disk_text). Shared by daphne.load and
    daphne.cancel; the traversal/existence guards live in _load."""
    if scope == "game":
        gd = Path(gamedir)
        if hypinput.has_per_game(gd, base):
            _state["hi"] = hypinput.load(hypinput.per_game_ini(gd, base))
        else:
            # Seed a new per-game map from the global one (Save creates it).
            _state["hi"] = hypinput.load()
        _state.update(scope="game", gamedir=str(gd), base=base)
    else:
        _state["hi"] = hypinput.load()
        _state.update(scope="global", gamedir="", base="")
    _state["disk_text"] = _state["hi"].text()


def _seek_get() -> bool:
    if _per_game():
        return hypinput.per_game_seek_instant(Path(_state["gamedir"]), _state["base"])
    return hypinput.global_seek_instant()


def _page_data() -> dict:
    hint = (hypinput.GAME_HINTS.get(_state["base"], "") if _per_game() else "")
    if _per_game():
        gamedir, base = Path(_state["gamedir"]), _state["base"]
        if hypinput.has_per_game(gamedir, base):
            caption = f"per-game map  ({base}.ini)"
        else:
            caption = f"new {base}.ini  (copied from global; Save creates it)"
        game_name = next((g["name"] for g in _games() if g["base"] == base), base)
    else:
        caption = "global map  (" + str(hypinput.GLOBAL_INI) + ")"
        game_name = ""
    return {"scope": _state["scope"], "base": _state["base"], "game_name": game_name,
            "caption": caption, "hint": hint, "buffered": True, "dirty": _dirty(),
            "seek_instant": _seek_get(),
            "sections": {"primary": list(PRIMARY), "p2": list(P2),
                         "directions": list(hypinput.DIRECTIONS),
                         "advanced": _adv_actions()},
            "rows": _rows(), "games": _games()}


@method("daphne.load", slow=True)
def _load(params):
    """(Re)load the editing buffer for a scope and return the full page data
    (slow: gamelist XML + per-game ini reads)."""
    scope = params.get("scope", "global")
    if scope == "game":
        gamedir = Path(params["gamedir"])
        base = params.get("base") or gamedir.stem
        try:                                  # path-traversal guard: keep writes under ~/ROMs/daphne
            gamedir.resolve().relative_to(DAPHNE_ROOT.resolve())
        except ValueError:
            raise RpcError("EINVAL", f"gamedir must be under {DAPHNE_ROOT}")
        if not gamedir.is_dir():
            raise RpcError("EINVAL", f"no such game dir: {gamedir}")
        _reload_scope("game", str(gamedir), base)
    elif scope == "global":
        _reload_scope("global", "", "")
    else:
        raise RpcError("EINVAL", f"scope must be global|game, got {scope!r}")
    return _page_data()


def _require_loaded() -> None:
    if _state["hi"] is None:
        raise RpcError("EINVAL", "daphne.load first")


@method("daphne.clear")
def _clear(params):
    _require_loaded()
    action = params["action"]
    _state["hi"].clear_button(action)
    return {"row": _row(action), "buffered": True, "dirty": _dirty(),
            "message": f"{hypinput.ACTION_LABELS.get(action, action)} unbound. "
                       "Press X to save."}


@method("daphne.reset_defaults")
def _reset_defaults(params):
    """Stock layout into the CURRENT scope's buffer; nothing written until Save."""
    _require_loaded()
    _state["hi"] = hypinput.load_default()
    target = (f"{_state['base']}.ini" if _per_game() else "the global hypinput.ini")
    return {"rows": _rows(), "buffered": True, "dirty": _dirty(),
            "message": f"Stock defaults loaded (nothing written yet) — press X to save "
                       f"them to {target}; Y cancels."}


@method("daphne.bind", slow=True)
def _bind(params):
    """Capture ONE X-Arcade press via the hypseus_capture.py subprocess and
    apply it to the buffer (port of _dp_bind_press/_dp_bind_done). input.lock
    brackets the capture: the press also reaches ES-DE via SDL."""
    _require_loaded()
    action = params["action"]
    label = hypinput.ACTION_LABELS.get(action, action)
    is_dir = action in hypinput.DIRECTIONS
    argv = [sys.executable, str(HERE / "lib" / "hypseus_capture.py"), "--timeout", "10"]
    if not is_dir:
        argv += ["--no-axis", "--no-hat"]  # Buttons/coin/start are digital only.

    event("input.lock", {"locked": True})
    res = {"error": "timeout"}
    proc = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        out, _ = proc.communicate(timeout=14)
        if proc.returncode == 0 and out.strip():
            res = json.loads(out.strip())
        elif proc.returncode == 4:
            res = {"error": "no_xarcade"}
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        event("input.lock", {"locked": False})

    changed = []
    warn = False
    if res.get("error") == "no_xarcade":
        message = "X-Arcade not detected — Identify it first on the Preview page."
        warn = True
    elif res.get("error"):
        message = f"Cancelled — no button pressed for {label}."
    else:
        kind = res.get("kind")
        if kind == "button":
            _state["hi"].set_button(action, int(res["value"]))
            if is_dir:
                _state["hi"].set_axis(action, None)  # Digital direction.
            changed = [action]
            message = f"{label} → {res.get('name', res['value'])}.  Press X to save."
        elif kind == "axis" and is_dir:
            _state["hi"].set_axis(action, res["value"])
            _state["hi"].set_button(action, 0)  # Analog steering.
            changed = [action]
            message = f"{label} → axis {res['value']}.  Press X to save."
        elif kind == "hat" and is_dir:
            value = int(res["value"])
            if value > 0:  # Hat on the P2/P3 stick → enable via KEY_UP.
                _state["hi"].set_button("UP", value)
                changed = ["UP"]
                message = (f"D-pad hat (P{value // 100 + 1}) enabled for all "
                           "directions. Verify on-screen.")
            else:
                # Primary-stick (js0) hat encodes to 0, which collides with "unbound", so it
                # can't be written to hypinput.dat. Hypseus is meant to read the primary
                # joystick's hat for all 4 directions automatically — so NO row change here is
                # expected. Say so clearly so the press obviously registered.
                message = ("✓ D-pad DETECTED — it's the primary stick's HAT. Hypseus drives "
                           "all 4 directions from it automatically, so there's nothing to "
                           "bind (that's why no row changed). Test the directions in Daphne — "
                           "if they don't move, tell me and I'll fix the hat handling.")
        else:
            want = "a stick direction" if is_dir else "a BUTTON"
            message = f"That was a {kind} — bind {want} for {label}."
            warn = True
    return {"message": message, "warn": warn,
            "rows": {a: _row(a) for a in changed}, "buffered": True, "dirty": _dirty()}


@method("daphne.save")
def _save(params):
    _require_loaded()
    if _per_game():
        gamedir, base = Path(_state["gamedir"]), _state["base"]
        hypinput.write_per_game(gamedir, base, _state["hi"])
        _state["disk_text"] = _state["hi"].text()
        return {"buffered": True, "dirty": False,
                "message": f"Saved {base}.ini and linked it in {base}.commands. "
                           "Applies to this game on its next launch."}
    hypinput.write_global(_state["hi"])
    _state["disk_text"] = _state["hi"].text()
    return {"buffered": True, "dirty": False,
            "message": "Saved hypinput.ini (backup: hypinput.ini.bak). "
                       "Applies to every Daphne game on the next launch."}


@method("daphne.cancel", slow=True)
def _cancel(params):
    """Discard unsaved edits: reload the current scope's map from disk (buffered
    editor Y=Cancel / Back-discard). Same effect as re-entering the page — the
    scope/gamedir/base were already validated by daphne.load."""
    _require_loaded()
    _reload_scope(_state["scope"], _state["gamedir"], _state["base"])
    return _page_data()


@method("daphne.seek_set")
def _seek_set(params):
    on = bool(params["on"])
    if _per_game():
        hypinput.set_per_game_seek(Path(_state["gamedir"]), _state["base"], on)
        target = _state["base"]
    else:
        hypinput.set_global_seek(on)
        target = "all laserdisc games"
    return {"message": f"Instant transitions {'ON' if on else 'off'} for {target}. "
                       "Applies on the next launch.", "seek_instant": _seek_get()}


@method("daphne.build_index")
def _build_index(params):
    """Launch singe-indexer.sh on-screen (detached) — 'all' or a game folder."""
    from .sinden_cmds import _detached
    arg = params.get("arg", "all")
    _detached([HERE / "singe-indexer.sh", arg], "singe-indexer")
    return {"message": "⚙ Seek-index builder launched — Hypseus runs on-screen, then "
                       "returns here. (log: control-panel/singe-indexer.log)"}
