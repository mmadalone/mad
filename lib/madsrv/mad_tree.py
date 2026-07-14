"""mad_tree — shared vocabulary + helpers for the Standalones/RetroArch/On-the-go
menu builders in standalones_cmds.py (and peers).

P2 scaffolding for the menu-uniformity work: this module holds the ONE ASCII page-title
separator, the ONE canonical label vocabulary, and a per-game menu-wrapper helper, so the
~13 per-emu builders stop hand-rolling those strings. It is deliberately a *pure-refactor*
layer — routing today's construction through these helpers changes no emitted byte (proven
by tests/test_menu_golden.py staying green with the goldens UNCHANGED). The canonical
row-reorder/relabel that actually *uses* the `L.*` vocabulary lands in a later phase.

Byte-identity note: tests/_menu_capture.serialize() emits sorted-keys JSON, so only the
`sections` list ORDER and each row's key/value SET matter — never dict construction order.
"""
from __future__ import annotations

# The single user-facing page-title separator (ASCII, welded here after P1). A page title
# reads "<Emulator> - <Section>", e.g. "PlayStation 2 - Graphics".
SEP = " - "


def title(label: str, leaf: str) -> str:
    """Compose a page title '<label> - <leaf>'. One separator, defined once, so it can
    never silently drift back to an em-dash."""
    return f"{label}{SEP}{leaf}"


class L:
    """Canonical menu-label vocabulary. Staged in P2 (substituted only where the emitted
    literal already matches, so no relabel ships); the actual canonical relabel that points
    divergent builders at these names is a later phase."""
    SYSTEM = "System"
    VIDEO = "Video"
    AUDIO = "Audio"
    INPUT = "Input"
    CONTROLLERS = "Controllers"
    INPUT_MAP = "Input mapping"
    DEVICE_VIS = "Device visibility"
    HOTKEYS = "Hotkeys"
    PERGAME = "Per-game"


def pergame_menu(label: str, games_ns: str, leaves: list,
                 *, row_label: str = L.PERGAME, suffix: str = "Per-game settings") -> dict:
    """The GAME-FIRST per-game menu row (kind:settings_pergame_menu): a single row that opens
    the media browser, then the picked title's pages. Dedupes the byte-identical wrappers the
    per-emu builders each hand-rolled. The `leaves` list is passed through VERBATIM — per-game
    subtrees are frozen; no helper here ever inspects or rewrites a leaf."""
    return {"label": row_label, "sublabel": "", "kind": "settings_pergame_menu",
            "arg": games_ns, "title": title(label, suffix), "sections": leaves}


def section_order(*, system=None, video=None, audio=None, inp=None,
                  extras=None, pergame=None) -> list:
    """Emit a builder's top-level sections in the canonical Switch-emu order:
    System, Video, Audio, Input, <extras...>, Per-game.

    PURE REORDERER. Each slot is an ALREADY-BUILT row (or None to omit it); `extras` is a
    list of already-built rows that sit between Input and Per-game. It NEVER constructs a
    group (doing so could trip _collapse_singletons' label-adoption in standalones_cmds and
    silently rename a row) and NEVER copies or mutates a row -- it returns the SAME row
    objects, only reordered. Present slots only; None vanishes. This is the one place the
    canonical row order lives, so a later reorder is a one-slot data change and every
    adopting builder is canonical by construction.
    """
    out = [r for r in (system, video, audio, inp) if r is not None]
    out.extend(extras or [])
    if pergame is not None:
        out.append(pergame)
    return out
