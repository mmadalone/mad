"""Handheld-only Deck input for Daphne / Hypseus (WS-D).

Hypseus reads ONE shared hypinput.ini for every launch. The Deck's built-in pad presents a DIFFERENT
raw SDL_Joystick button order than the docked X-Arcade -- SDL GameController order: coin = Select =
btn4 (value 5), start = Start = btn6 (value 7) -- vs the X-Arcade's Select = btn6 / Start = btn7. So
the shipped X-Arcade map leaves coin + start DEAD on the Deck (only the stick/directions work).

This keeps a SEPARATE Deck map (hypinput.deck.ini) and, when HANDHELD, transiently swaps it in for the
shared hypinput.ini, restored on game-end so the docked map is never touched. The MAD editor
(onthego_cmds ns daphne_handheld) edits DECK_INI. Button order confirmed on-device (deck-sdl-buttons
capture): A/B/X/Y = 1-4, Select = 5, Start = 7, L3 = 8, R3 = 9, L1 = 10, R1 = 11; directions ride the
left stick (axes 0/1), same as the shipped template, so only coin/start need re-valuing by default.

The swap replaces the GLOBAL map, so a game that uses a per-game -keymapfile override still reads that
(niche: such a game keeps its X-Arcade-shaped per-game map handheld); the common global-map case works.
"""
from __future__ import annotations

from pathlib import Path

from . import hypinput

DECK_INI = hypinput.GLOBAL_INI.with_name("hypinput.deck.ini")
_RAIL = hypinput.GLOBAL_INI.with_name(hypinput.GLOBAL_INI.name + ".docked-rail")
# The Deck-correct default = the shipped classic layout with ONLY coin/start re-valued to the Deck's
# SDL buttons (A/B/X/Y = 1-4 and the left-stick directions are already correct on the Deck pad).
_DECK_OVERRIDES = {"COIN1": 5, "START1": 7}


def deck_default_text() -> str:
    """The shipped hypinput template re-valued for the Deck pad (coin -> Select, start -> Start), with
    a Deck-correct button banner (the shared template's banner lists the X-Arcade layout)."""
    hi = hypinput.parse(hypinput.DEFAULT_TEMPLATE)
    for action, val in _DECK_OVERRIDES.items():
        hi.set_button(action, val)
    return hi.text().replace(
        "# X-Arcade buttons:  A=1 B=2 X=3 Y=4  L1=5 R1=6  Select=7 Start=8  L3=10 R3=11\n"
        "# (the Steam Deck's SDL joystick order DIFFERS -- see lib/daphne_input.py hypinput.deck.ini)",
        "# Steam Deck buttons:  A=1 B=2 X=3 Y=4  Select=5 Start=7  L3=8 R3=9  L1=10 R1=11")


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def ensure_deck_ini() -> Path:
    """Seed DECK_INI from the Deck default if it doesn't exist yet (best-effort)."""
    if not DECK_INI.is_file():
        try:
            DECK_INI.parent.mkdir(parents=True, exist_ok=True)
            _write(DECK_INI, deck_default_text())
        except Exception:
            pass
    return DECK_INI


def load_deck():
    """The Deck map's HypInput model (seeded from the Deck default if absent)."""
    ensure_deck_ini()
    return hypinput.load(DECK_INI)


def save_deck(hi) -> None:
    _write(DECK_INI, hi.text())


def _handheld() -> bool:
    """The on-the-go handheld gate (feature enabled AND physically handheld). Best-effort -> False."""
    try:
        from . import deck_state, policy
        hh = policy.load_merged().get("handheld")
        if not (isinstance(hh, dict) and hh.get("enabled", False)):
            return False
        return deck_state.is_handheld(deck_state.resolve_force(hh))
    except Exception:
        return False


def _valid_map(text) -> bool:
    """A sane hypinput.ini: non-empty with the [KEYBOARD]...END envelope. Guards the swap against
    ever backing up or promoting a torn/empty file over the intact docked map."""
    return bool(text) and "[KEYBOARD]" in text and "END" in text


def sweep() -> None:
    """Restore the docked hypinput.ini from the rail backup (revert a handheld swap). Runs at
    game-start AND game-end so a crash orphan can never leave the docked map replaced. A torn/empty
    rail is DROPPED, never promoted -- a torn rail means the atomic backup never completed, so the
    live GLOBAL_INI is still the intact docked map."""
    try:
        if not _RAIL.exists():
            return
        text = _RAIL.read_text()
        if _valid_map(text):
            _write(hypinput.GLOBAL_INI, text)     # atomic restore of the docked map
        _RAIL.unlink()                            # drop the rail (restored, or corrupt -> discard)
    except Exception:
        pass


def apply() -> None:
    """When handheld, swap the Deck map in for the shared hypinput.ini (the docked map is backed up to
    the rail first, ATOMICALLY). No-op docked / no or broken map. Best-effort; never blocks a launch."""
    sweep()                                   # heal any orphan from a prior crash first
    try:
        if not _handheld():
            return
        ensure_deck_ini()
        if not (DECK_INI.is_file() and hypinput.GLOBAL_INI.is_file()):
            return
        docked, deck = hypinput.GLOBAL_INI.read_text(), DECK_INI.read_text()
        if not (_valid_map(docked) and _valid_map(deck)):   # never back up / swap a broken map
            return
        _write(_RAIL, docked)                     # ATOMIC backup of the docked map (tmp + replace)
        _write(hypinput.GLOBAL_INI, deck)         # ATOMIC swap to the Deck map
    except Exception:
        pass
