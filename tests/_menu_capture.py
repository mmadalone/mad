"""
Hermetic capture of the MAD menu-tree JSON for the three surfaces (RetroArch,
Standalones, On-the-go). The SAME code drives golden CAPTURE
(tests/capture_menu_golden.py) and golden COMPARE (tests/test_menu_golden.py) —
that's the no-behaviour-change proof for the descriptor/projector refactor
(phases P1-P4): reshape the Python section builders, then re-run the compare;
an empty diff proves the emitted menus are byte-identical.

Mirrors the config-writer golden harness (tests/_harness.py + capture_golden.py
+ test_golden.py), applied to menu trees instead of config files.

Determinism (so the goldens are identical on the Deck and on a bare CI runner):
every curated emulator/system is forced present, machine-specific art paths are
replaced with fixed tokens, and the live controller-policy is pinned to
FIXTURE_POLICY so the per-system warn toggles are stable. Nothing here touches
the C++ side or any live config/gamelist — it only calls the Python section
builders and serializes the dicts they return.

FIXTURE_POLICY coupling: the per-system arcade/console category below mirrors
controller-policy.toml's canonical categories (which decide each system's
X-Arcade warn toggle). If a curated system's category changes there, update
FIXTURE_POLICY — or regenerate the goldens and review the git diff.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
from pathlib import Path

from lib import es_gamelist, es_systems, retroarch_cfg
from lib.madsrv import onthego_cmds as og
from lib.madsrv import policy_settings_cmds as psc
from lib.madsrv import retroarch_settings as rs
from lib.madsrv import standalones_cmds as sc
from lib.madsrv import systems_cmds

# ── curated system sets (DERIVED from the registries, never hand-listed) ──────
# Every standalone system carries a controller-policy warn flag; the union of the
# STANDALONES registry's `systems` is exactly the flag-bearing set (dolphin ->
# wii+gc, the two group tiles contribute switch/pcsx2x6). On-the-go adds a few
# console-only systems (psx/n64/…) that never reach the standalone tiles.
CURATED_FLAG_SYS = sorted({s for e in sc.STANDALONES for s in e["systems"]})
ALL_CURATED_SYSTEMS = sorted(set(CURATED_FLAG_SYS) | {t[0] for t in og._SYSTEMS})

# ── pinned controller-policy: each curated system's category (arcade|console) ──
# resolve_category() reads merged["systems"][s]["category"]; _warn_flag() maps
# arcade -> "warn_when_no_xarcade", console -> "warn_when_only_xarcade" (mugen /
# openbor are special-cased to the arcade warn regardless). With no override
# entries every warn_* toggle takes its ON default (_flag_default), so the tree
# is fully deterministic.
_ARCADE = {"model2", "model3", "daphne", "lindbergh", "pcsx2x6", "mugen", "openbor"}
FIXTURE_POLICY = {
    "systems": {
        s: {"category": ("arcade" if s in _ARCADE else "console")}
        for s in CURATED_FLAG_SYS
    }
}


def _fake_console_art(sysname: str) -> str:
    return f"{sysname}/console.png"


def _fake_resolve_art(rel_names):
    # rel_names is a lookup chain like ["icons/eden.png", "eden.png", …]; the real
    # resolve_art returns the first that exists (an absolute path) or None. Return
    # the first candidate verbatim so the token is present + machine-independent.
    return rel_names[0] if rel_names else None


@contextlib.contextmanager
def hermetic():
    """Force every curated emulator/system present, pin art + controller-policy,
    and mark RetroArch present, so the three menu trees serialize identically on
    the Deck and on a bare CI runner. Restores everything on exit."""
    saved: dict = {}
    tmp = tempfile.TemporaryDirectory()

    def patch(obj, attr, val):
        saved[(obj, attr)] = getattr(obj, attr)
        setattr(obj, attr, val)

    try:
        # 1. presence: every curated system has a visible gamelist (dict: code
        #    iterates keys; visible_records must be truthy).
        patch(es_systems, "load_systems", lambda: {s: [] for s in ALL_CURATED_SYSTEMS})
        patch(es_systems, "_has_gamelist", lambda s: True)
        patch(es_gamelist, "visible_records", lambda s: {"g": 1})
        # 2. installed-binary + device probes (Switch members glob binaries;
        #    pcsx2x6 Arcade Lightgun leaf + Retail member gate on GunCon2 probes).
        patch(sc, "_emu_installed", lambda emu: True)
        patch(sc, "_pcsx2x6_has_guncon2", lambda: True)
        patch(sc, "_pcsx2x6_has_guncon2_retail", lambda: True)
        # 3. art -> fixed tokens. Patch BOTH systems_cmds (on-the-go imports it at
        #    call time) AND the standalones module-level rebinds (imported at top).
        for mod in (systems_cmds, sc):
            patch(mod, "console_art", _fake_console_art)
            patch(mod, "resolve_art", _fake_resolve_art)
        # 4. controller-policy -> FIXTURE. Pins tile_flag_sections' membership
        #    (SYSFLAGS) and the toggle `value` (load_merged) — the only live-value
        #    leak in any tree.
        patch(psc, "load_merged", lambda: copy.deepcopy(FIXTURE_POLICY))
        patch(psc, "SYSFLAGS",
              {s: psc._flags_for(s, FIXTURE_POLICY)
               for s in CURATED_FLAG_SYS if psc._flags_for(s, FIXTURE_POLICY)})
        # 5. RetroArch present: _ra_hub_tiles() gates on RA_GLOBAL_CFG existing.
        ra_cfg = Path(tmp.name) / "retroarch.cfg"
        ra_cfg.write_text("", encoding="utf-8")
        patch(retroarch_cfg, "RA_GLOBAL_CFG", ra_cfg)
        yield
    finally:
        for (obj, attr), val in saved.items():
            setattr(obj, attr, val)
        tmp.cleanup()


def _normalize(tree):
    """Deep-copy + basename any ABSOLUTE `art` path (defensive; the stub already
    yields clean relative tokens, so this only fires if an unstubbed path leaks)."""
    def walk(x):
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k == "art" and isinstance(v, list):
                    out[k] = [os.path.basename(a) if isinstance(a, str) and os.path.isabs(a)
                              else a for a in v]
                else:
                    out[k] = walk(v)
            return out
        if isinstance(x, list):
            return [walk(i) for i in x]
        return x
    return walk(copy.deepcopy(tree))


def serialize(tree) -> str:
    """Stable pretty JSON: sorted keys -> order-independent; ensure_ascii=False ->
    the em-dash renders literally so a P1 ASCII-separator fix is a readable diff."""
    return json.dumps(tree, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def enumerate_cases():
    """Yield (case_id, normalized_tree) for every surface, built under hermetic().
    One case per top-level standalone tile (maps 1:1 to the section builders being
    collapsed), plus an index case (tile set + order) and the two single-tile
    surfaces."""
    with hermetic():
        yield ("retroarch", _normalize({"tiles": rs._ra_hub_tiles()}))
        yield ("onthego", _normalize(og._list({})))
        sa = sc._standalones_list({})
        yield ("standalones__index",
               _normalize([{"key": t["key"], "label": t["label"]} for t in sa["tiles"]]))
        for t in sa["tiles"]:
            yield (f"standalones__{t['key']}", _normalize(t))
