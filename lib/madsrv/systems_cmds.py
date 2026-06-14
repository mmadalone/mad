"""systems.* + art.* methods — ready-to-render Systems-page data.

Ports the Tk Systems page's data composition (router-config-gui.py systems() /
_system_detail / _launcher_label / _resolve_category) so the C++ page only
renders. Art paths are resolved HERE (the backend owns the art lookup chain —
active theme's router-config/ → launchers art/ → ~/esde-build/art) and returned
as absolute paths for ImageComponent::setImage.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .. import es_systems
from ..esde_settings import active_theme_dir
from ..policy import load_merged
from ..retroarch_cfg import (core_dirs_for_system, get_system_option,
                             set_system_option)
from .preview_cmds import _esde_systems
from .rpc import method, RpcError

_LAUNCHERS = Path(__file__).resolve().parent.parent.parent

# ES-DE "systems" that are really tool launchers, not games (Tk systems() TOOL set).
TOOL_SYSTEMS = {"sinden", "steam", "desktop", "controllers", "sinden-tools"}

# Curated per-system RetroArch options surfaced as Systems-page toggles. Defined
# once in lib/ra_options.py (single source of truth) and shared with the Tk
# RetroArch page so the two surfaces never diverge.
from ..ra_options import RA_SYSTEM_OPTIONS, ra_options_for as _ra_options_for  # noqa: F401,E402


def _retroarch_running() -> bool:
    """RA reads these cfgs at startup and rewrites them on exit, so refuse to
    write while it's live."""
    try:
        return subprocess.run(["pgrep", "-x", "retroarch"],
                              capture_output=True).returncode == 0
    except Exception:
        return False

# Detail-page toggle defaults (mirror the Tk page): router_skip/require_* OFF.
TOGGLE_DEFAULTS = (("router_skip", False), ("require_dolphinbar", False),
                   ("require_sinden", False))


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
        if e.get("category"):
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
    """● marker: a DETAIL-PAGE toggle sits in a non-default position (exactly the
    detail page's own visibility — priority/pins overrides do NOT mark here)."""
    if any(bool(ent.get(k, d)) != d for k, d in TOGGLE_DEFAULTS):
        return True
    wf = _warn_flag(sysname, resolve_category(sysname, merged))
    return bool(wf and not ent.get(wf[0], True))


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


@method("esde.systems")
def _esde_systems_m(params):
    return {"systems": sorted(_esde_systems())}


@method("systems.list")
def _systems_list(params):
    """Tiles for the Systems page: ES-DE systems with games (tools excluded),
    each with its backend sublabel, ● state, and console art path. The sublabel
    uses the SAME truth as the detail page (resolve the policy backend through
    inherits; only claim "retroarch" when the active ES-DE command really is
    RetroArch, else name the real launcher) — the old `backend or "retroarch"`
    default mislabeled script-launched systems like mugen."""
    merged = load_merged()
    sysxml = es_systems.load_systems()
    rows = []
    for s in sorted(_esde_systems()):
        if s in TOOL_SYSTEMS:
            continue
        e = merged.get("systems", {}).get(s, {})
        if e.get("router_skip"):
            sub = "hands-off"
        else:
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


@method("systems.get", slow=True)
def _systems_get(params):
    """Detail-page data (slow: core_dirs_for_system walks the RA config tree).
    Mirrors the Tk _system_detail composition exactly."""
    sysname = params["system"]
    merged = load_merged()
    ent = merged.get("systems", {}).get(sysname, {})
    backend = es_systems._resolve_backend(merged, sysname)
    managed = bool(backend) or bool(core_dirs_for_system(sysname))
    if not backend:
        cmd = es_systems.default_command(sysname)
        if cmd and not es_systems.is_standalone(cmd):
            backend = "retroarch"
        else:
            backend = launcher_label(cmd)
    toggles = []
    if managed:
        toggles.append({"key": "router_skip",
                        "label": "Hands-off (router never touches this system)",
                        "value": bool(ent.get("router_skip", False))})
    for flag, lbl in (("require_dolphinbar", "Require a DolphinBar"),
                      ("require_sinden", "Require a Sinden gun")):
        if flag in ent or sysname == "wii":
            toggles.append({"key": flag, "label": lbl,
                            "value": bool(ent.get(flag, False))})
    wf = _warn_flag(sysname, resolve_category(sysname, merged))
    if wf:
        toggles.append({"key": wf[0], "label": wf[1],
                        "value": bool(ent.get(wf[0], True))})
    # RetroArch per-system option toggles (only for systems that have RA cores).
    ra_options = []
    if core_dirs_for_system(sysname):
        for o in _ra_options_for(sysname):
            ra_options.append({"id": o["id"], "label": o["label"],
                               "value": get_system_option(sysname, o["cfg_key"]) == o["on"]})
    return {"system": sysname, "backend_label": backend, "managed": managed,
            "art": console_art(sysname), "toggles": toggles,
            "ra_options": ra_options}


@method("systems.set_ra_option")   # fast: tiny read-modify-write + one pgrep, runs
                                   # inline on the stdin thread so config writes
                                   # serialize with model2.set / profiles.apply_slot
                                   # (avoids the lost-update + shared-temp-name race).
def _systems_set_ra_option(params):
    """Toggle a curated RetroArch option for a system: write/clear cfg_key in
    config/<Core>/<system>.cfg across all the system's cores. Refuses while
    RetroArch is running (it rewrites these on exit). Returns the re-read state."""
    sysname = params["system"]
    opt_id = params["id"]
    value = bool(params["value"])
    opt = next((o for o in _ra_options_for(sysname) if o["id"] == opt_id), None)
    if opt is None:
        raise RpcError("EINVAL", f"unknown RA option {opt_id!r} for {sysname!r}")
    if _retroarch_running():
        raise RpcError("EBUSY", "Close RetroArch first — it overwrites these on exit.")
    set_system_option(sysname, opt["cfg_key"], opt["on"] if value else None)
    return {"id": opt_id,
            "value": get_system_option(sysname, opt["cfg_key"]) == opt["on"]}


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
    for s in (n.replace(" ", "-"), n.replace(" ", "").replace("-", ""),
              (n.split()[0] if n.split() else n)):
        if s and s not in forms:
            forms.append(s)
    cand: list[str] = []
    for s in forms:
        cand += [f"icons/{s}.png", f"{s}.png"]
    if any(k in n for k in ("sinden", "lightgun", "gun")):
        cand += ["icons/sinden.png", "sinden.png", "icons/lightgun.png", "lightgun.png"]
    if fallback:
        cand += [f"icons/{fallback}.png", f"{fallback}.png"]
    return resolve_art(cand)
