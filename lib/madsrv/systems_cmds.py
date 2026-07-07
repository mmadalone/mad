"""systems.list + art.* helpers.

The old Systems page is gone; systems.list survives as the system enumeration the
Device-pins and Quit-combo pages consume, and this module still owns the shared art
lookup chain (active theme's router-config/, then launchers art/, then
~/esde-build/art), resolved to absolute paths for ImageComponent::setImage and
imported by several other MAD pages (bezel / backends / standalones / preview /
tester).
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .. import es_systems
from ..esde_settings import active_theme_dir
from ..policy import load_merged
from .preview_cmds import _esde_systems
from .rpc import method

_LAUNCHERS = Path(__file__).resolve().parent.parent.parent

# ES-DE "systems" that are really tool launchers, not games (Tk systems() TOOL set).
TOOL_SYSTEMS = {"sinden", "steam", "desktop", "controllers", "sinden-tools"}


# Detail-page ● markers: require_* default OFF. router_skip is an INTERNAL flag now
# (RetroArch-hub plan), NOT a user toggle, so it is excluded here — it must not drive
# the tile's ● "configured" marker for base-hands-off systems.
TOGGLE_DEFAULTS = (("require_dolphinbar", False), ("require_sinden", False))


def art_dirs() -> list[Path]:
    """The MAD art lookup chain: active theme's router-config/ (themeable) →
    launchers repo art/ → the esde-build/art drop dir."""
    dirs = []
    td = active_theme_dir()
    if td:
        dirs.append(Path(td) / "router-config")
    dirs.append(_LAUNCHERS / "art")
    dirs.append(Path.home() / "esde-build" / "art")
    return dirs


def resolve_art(rel_names: list[str]) -> str | None:
    """First existing file among rel_names across the art-dir chain. A
    "console:<system>" candidate resolves via the active theme's per-system
    console.png instead (the Tk sidebar's Daphne fallback)."""
    for nm in rel_names:
        if nm.startswith("console:"):
            hit = console_art(nm[len("console:"):])
            if hit:
                return hit
            continue
        for base in art_dirs():
            p = base / nm
            if p.is_file():
                return str(p)
    return None


def console_art(sysname: str) -> str | None:
    """The active ES-DE theme's per-system console.png (case-tolerant)."""
    td = active_theme_dir()
    if not td:
        return None
    for cand in (Path(td) / sysname / "console.png",
                 Path(td) / sysname.lower() / "console.png"):
        if cand.is_file():
            return str(cand)
    return None


def resolve_category(sysname: str, merged: dict) -> str | None:
    """Walk the policy `inherits` chain to find a system's category."""
    sysd = merged.get("systems", {})
    s, seen = sysname, set()
    while s and s not in seen:
        seen.add(s)
        e = sysd.get(s, {})
        e = e if isinstance(e, dict) else {}   # a hand-edited non-table entry
        if e.get("category"):                  # (str/list) must not raise here
            return e["category"]
        s = e.get("inherits")
    return None


def _warn_flag(sysname: str, cat: str | None) -> tuple[str, str] | None:
    """The ONE relevant X-Arcade warn toggle for this system (mugen/openbor count
    as arcade), or None. Default ON; toggling writes an explicit override."""
    if sysname in ("mugen", "openbor") or cat == "arcade":
        return ("warn_when_no_xarcade", "Warn when the X-Arcade is NOT present")
    if cat == "console":
        return ("warn_when_only_xarcade", "Warn when only the X-Arcade is present")
    return None


def _configured(sysname: str, ent: dict, merged: dict) -> bool:
    """● marker: a require_* DETAIL-PAGE toggle is ENABLED (non-default). The
    X-Arcade presence WARNINGS default ON and are a minor preference, so silencing
    one must NOT light the ● — it wrongly read arcade systems that legitimately
    disable it (cannonball/lindbergh) as "configured". router_skip is internal and
    already excluded from TOGGLE_DEFAULTS. Priority/pins overrides don't mark here."""
    return any(bool(ent.get(k, d)) != d for k, d in TOGGLE_DEFAULTS)


def launcher_label(cmd: str) -> str:
    """A human-readable launcher name for a no-policy-backend system, derived
    from its active ES-DE <command> (port of App._launcher_label)."""
    if not cmd:
        return "none — system uses its own launcher"
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    display = None
    # mad-standalone-launch.py / mad-switch-launch.py wrap the real command; their
    # 2nd token is the emulator key (pcsx2x6, eden, …) — the meaningful label. The
    # rest (controller-router-wrap.sh, /usr/bin/env VAR=…, the AppImage) is plumbing.
    if toks and Path(toks[0]).name in ("mad-standalone-launch.py",
                                       "mad-switch-launch.py") and len(toks) >= 2:
        return toks[1]
    if toks and Path(toks[0]).name == "controller-router-wrap.sh" and "--" in toks:
        cut = toks.index("--")
        if cut >= 1 and "%" not in toks[cut - 1]:
            display = toks[cut - 1]              # the wrap's launch-screen name arg
        toks = toks[cut + 1:]                    # the REAL command after the wrap
    for t in toks:
        if "/" in t and "%" not in t:
            return Path(t).name                  # rpcs3.sh / an AppImage / mugen.sh …
    for t in toks:                               # %EMULATOR_HYPSEUS-SINGE% → "hypseus-singe"
        if t.startswith("%EMULATOR_") and t.endswith("%") and "OS-SHELL" not in t:
            return t[10:-1].lower()
    if display:
        return display                           # e.g. "M.U.G.E.N Game Engine"
    return "per-game script (OS shell)"          # %EMULATOR_OS-SHELL% %ROM% (steam, desktop)


@method("systems.list")
def _systems_list(params):
    """ES-DE systems with games (tools excluded), each with a backend sublabel,
    dot state, and console art path. Consumed by the Device-pins + Quit-combo
    pages as the system enumeration. The sublabel resolves the policy backend
    through inherits, only claiming "retroarch" when the active ES-DE command
    really is RetroArch, else naming the real launcher (so script-launched systems
    like mugen are not mislabeled)."""
    merged = load_merged()
    sysxml = es_systems.load_systems()
    rows = []
    for s in sorted(_esde_systems()):
        if s in TOOL_SYSTEMS:
            continue
        e = merged.get("systems", {}).get(s, {})
        # No "hands-off" subtitle: router_skip is an internal flag now (RetroArch-hub
        # plan). Show the real backend/emulator so the tile is informative and never
        # misleading (the real hands-off control lives on the Standalones page).
        sub = es_systems._resolve_backend(merged, s)
        if not sub:
            cmd = es_systems.default_command(s, sysxml)
            if cmd and not es_systems.is_standalone(cmd):
                sub = "retroarch"
            else:
                sub = launcher_label(cmd)
        rows.append({"name": s, "sub": sub,
                     "configured": _configured(s, e, merged),
                     "art": console_art(s)})
    return {"systems": rows}


@method("art.resolve")
def _art_resolve(params):
    """{names: {logical: [rel-candidates...]}} → {paths: {logical: abs|null}}.
    Also accepts {names: [rel...]} → {path: abs|null} for a single lookup."""
    names = params.get("names")
    if isinstance(names, list):
        return {"path": resolve_art(names)}
    out = {}
    for logical, cands in (names or {}).items():
        out[logical] = resolve_art(list(cands))
    return {"paths": out}


# Devices whose themed icon can't be derived from the label text get an explicit
# stem here: the Steam Input virtual pad labels "Steam Deck (SI)", and the "(SI)"
# suffix breaks the flatten match (→ "steamdeck(si)", no asset) so it fell back to
# the generic gamepad. (28de:1205 "Steam Deck" already resolves via its label.)
_VIDPID_ICON = {"28de:11ff": "steamdeck"}


def device_icon_path(name: str, vidpid: str = "",
                     fallback: str = "genericgamepad") -> str | None:
    """Resolved path of a device-specific themeable icon — port of the Tk
    App._device_icon candidate forms: vidpid files (<vid>-<pid>.png /
    <vid>_<pid>.png, so a NEW pad's icon works by dropping an asset named by
    USB id), then hyphenated / flattened / first-word name forms, gun keywords
    map to the sinden/lightgun art, generic fallback LAST."""
    n = (name or "").lower()
    forms: list[str] = []
    if vidpid:
        forms += [vidpid.replace(":", "-"), vidpid.replace(":", "_")]
        override = _VIDPID_ICON.get(vidpid.strip().lower())
        if override:
            forms.insert(0, override)   # explicit stem wins over the (broken) label forms
    for s in (n.replace(" ", "-"), n.replace(" ", "").replace("-", ""),
              (n.split()[0] if n.split() else n),
              # hyphen-stripped first word: "X-Arcade P1" → "xarcade" matches xarcade.png
              # (the " P1"/"P2" suffix added for labeling must not lose the device icon).
              (n.split()[0].replace("-", "") if n.split() else n)):
        if s and s not in forms:
            forms.append(s)
    cand: list[str] = []
    for s in forms:
        cand += [f"icons/{s}.png", f"{s}.png"]
    # An "X-Arcade" label in ANY position ("X-Arcade", "X-Arcade P1", "WiiU X-Arcade P6")
    # maps to the X-Arcade icon, ahead of the first-word / vid:pid forms — the X-Arcade
    # shares 045e:02a1 with a generic Xbox 360, so the NAME is the reliable signal.
    if "x-arcade" in n or "xarcade" in n:
        cand = ["icons/xarcade.png", "xarcade.png"] + cand
    if any(k in n for k in ("sinden", "lightgun", "gun")):
        cand += ["icons/sinden.png", "sinden.png", "icons/lightgun.png", "lightgun.png"]
    if fallback:
        cand += [f"icons/{fallback}.png", f"{fallback}.png"]
    return resolve_art(cand)
