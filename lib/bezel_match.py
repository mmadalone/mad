"""bezel_match — name normalization + fuzzy ranking for bezel auto-assign (Phase 3).

`norm()` is the proven wire-bezels.py normalizer (drop region/dump/edition tags,
punctuation and articles) so a ROM filename and a Bezel-Project bezel name compare
region/edition-insensitively — e.g. "Cannon Fodder (1993)(Virgin)[!]" == bezel
"Cannon Fodder". It drives the two fuzzy tiers used by bezel_cfg:

  * norm_map() — a UNIQUE normalized-equal match auto-wires (silent, confident);
  * rank_candidates() — everything else is difflib-ranked and shown to the user to
    pick (never auto-wired). difflib is stdlib (SteamOS has no pip) and is already
    used for fuzzy name matching in skyscraper-apply.py.

NOTE: norm() is intentionally byte-identical to wire-bezels.py:norm — keep the two in
sync (or repoint wire-bezels at this module). Stdlib only.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_BRACKET_RE = re.compile(r"\(.*?\)|\[.*?\]")
_TAG_RE = re.compile(r"\b(english|translated|translation|hack|sample|proto|beta|unl|v\d[\d.]*)\b")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")
_ARTICLE_RE = re.compile(r"\b(the|a|of|and|in)\b")


def norm(s: str) -> str:
    """Normalize a ROM stem or bezel name for region/edition-insensitive comparison."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = _BRACKET_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = _NONALNUM_RE.sub(" ", s).strip()
    s = _ARTICLE_RE.sub(" ", s)
    return " ".join(s.split())


def norm_map(bezels) -> dict:
    """{norm(bezel): [bezel_stems]} — a normalized key can map to SEVERAL bezels (e.g. a
    CD32 and a floppy edition), so callers must treat a list of >1 as ambiguous and NOT
    auto-wire it. Empty-norm bezels are dropped (nothing useful to match on)."""
    out: dict[str, list] = {}
    for b in bezels:
        k = norm(b)
        if k:
            out.setdefault(k, []).append(b)
    return out


def normed(bezels):
    """Pre-normalize a bezel list ONCE so rank_candidates can be called per-ROM across a
    whole system without re-running norm() over every bezel each time."""
    return [(b, norm(b)) for b in bezels]


def rank_candidates(rom_stem: str, normed_bezels, n: int = 8, cutoff: float = 0.45):
    """Top-n (bezel, score) by difflib similarity of the normalized rom stem to each
    normalized bezel, best first (ties broken by name for determinism). `normed_bezels`
    is the output of normed(); quick_ratio() prefilters before the costlier ratio()."""
    target = norm(rom_stem)
    if not target:
        return []
    sm = difflib.SequenceMatcher()
    sm.set_seq2(target)          # build the target's index once; reuse for every bezel
    scored = []
    for bezel, bnorm in normed_bezels:
        if not bnorm:
            continue
        sm.set_seq1(bnorm)
        if sm.quick_ratio() < cutoff:    # cheap upper bound — skip obvious non-matches
            continue
        r = sm.ratio()
        if r >= cutoff:
            scored.append((bezel, r))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:n]
