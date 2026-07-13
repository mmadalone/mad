"""Per-game HANDHELD RetroArch input remap (transient, WS-I).

WS-C's ra_handheld_input remaps the Deck pad GLOBALLY (all RA games) when handheld. This adds a
PER-GAME override: a user-configured input remap for one game that applies ONLY when handheld and is
reverted when docked. RetroArch's native per-game remap (a .rmp file, edited by the permanent
ragamein.* editor) is NOT dock-aware -- RA auto-loads it every launch -- so a handheld-only per-game
remap needs a transient rail, exactly like ra_handheld_input but scoped per game:

  store   [.mad-ra-handheld-pergame.json] = {"<system>:<stem>": {rmp-key: value, ...}}  (the user's map,
          edited via the ragamehh.* MAD editor; this is NOT the live .rmp)
  apply   at handheld game-start (before RetroArch launches): SNAPSHOT the resting .rmp for the
          launching core to a sidecar, WRITE the handheld map into that core's .rmp, enable remaps.
  restore at game-end / dock: put the snapshotted resting .rmp back (empty snapshot -> remove the .rmp).

Crash-safe: sweep the orphan first at game-start AND game-end (a crash can never leave a later DOCKED
game running the handheld remap). Own sidecar. Every error degrades to "leave the .rmp alone; the
launch continues". Wired from controller-router._ra_on_the_go / ._cleanup, after the global rail.
Coexists with the permanent ragamein .rmp: a game with a permanent remap has it snapshotted + restored
(handheld overrides it transiently); a game with none has the handheld .rmp removed on exit.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import mad_paths, retroarch_cfg, retroarch_rmp as rmp
from .policy import load_merged

STORE = mad_paths.storage("controller-router", ".mad-ra-handheld-pergame.json")
_SIDECAR = mad_paths.storage("controller-router", ".mad-ra-handheld-pergame-restore")


def titleid(system: str, stem: str) -> str:
    return f"{system}:{stem}"


# --- the user's per-game handheld remap store (edited by ragamehh.*) --------------------------------
def _load_store() -> dict:
    try:
        if STORE.is_file():
            d = json.loads(STORE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def get_pergame(tid: str) -> dict:
    """The user's handheld remap for a game ("<system>:<stem>"), or {}."""
    v = _load_store().get(tid)
    return dict(v) if isinstance(v, dict) else {}


def set_pergame(tid: str, mapping: dict) -> None:
    """Persist (or clear, on empty) the handheld remap for a game."""
    store = _load_store()
    if mapping:
        store[tid] = {k: str(v) for k, v in mapping.items()}
    else:
        store.pop(tid, None)
    _write(STORE, json.dumps(store, indent=2, sort_keys=True))


# --- the transient rail (snapshot / apply / restore) ------------------------------------------------
def _handheld() -> bool:
    """The on-the-go handheld gate (feature enabled AND physically handheld). Best-effort -> False."""
    try:
        from . import deck_state
        pol = load_merged()
        hh = pol.get("handheld") if isinstance(pol, dict) else None
        if not (isinstance(hh, dict) and hh.get("enabled", False)):
            return False
        return deck_state.is_handheld(deck_state.resolve_force(hh))
    except Exception:
        return False


def restore() -> None:
    """Put the snapshotted resting .rmp back (an empty snapshot removes the .rmp), then drop the
    sidecar. No-op when no sidecar. Kept on a write failure so the next sweep retries."""
    try:
        if not _SIDECAR.is_file():
            return
        d = json.loads(_SIDECAR.read_text(encoding="utf-8"))
        system, stem, core = d.get("system"), d.get("stem"), d.get("core")
        resting = d.get("resting") if isinstance(d.get("resting"), dict) else {}
        if not (system and stem):
            _SIDECAR.unlink(missing_ok=True)
            return
        rmp.set_game_remap(system, stem, resting, only_core=core or None)
        _SIDECAR.unlink(missing_ok=True)
    except Exception:
        pass                                              # keep the sidecar; a later sweep retries


def apply(system: str, stem: str) -> None:
    """Swap the launching game's .rmp to its handheld remap when handheld. No-op unless: on-the-go
    enabled + HANDHELD, the game has a stored handheld remap, and it launches on a real RA core.
    Writes an atomic sidecar (the resting .rmp) BEFORE mutating."""
    restore()                                             # heal any crash orphan first
    try:
        if _SIDECAR.exists():                             # a leftover we couldn't clear -> don't stack
            return
        if not _handheld():
            return
        mapping = get_pergame(titleid(system, stem))
        # Handheld is single-pad: never write a Player-2 or port remap to the live .rmp. The editor
        # already scopes saves to Player 1, but a legacy store from the pre-P1-only editor could
        # otherwise re-arm the "Player 1 port" foot-gun (a port != Port 1 = silent no-input).
        mapping = {k: v for k, v in mapping.items()
                   if "player2" not in k and "_p2" not in k and not k.startswith("input_remap_port_")}
        if not mapping:
            return                                        # no handheld remap for this game
        core = retroarch_cfg.launched_core(system, stem)
        if not core:
            return                                        # standalone / no core -> not an RA remap
        resting = rmp.get_game_remap(system, stem, only_core=core)   # snapshot THIS core's resting .rmp
        _write(_SIDECAR, json.dumps({"system": system, "stem": stem, "core": core, "resting": resting}))
        rmp.set_game_remap(system, stem, mapping, only_core=core)
        retroarch_cfg.ensure_pergame_enabled(["remaps"])
    except Exception:
        pass                                              # best-effort; never block the launch
