"""backends.* / profiles.* / priority.* methods — the Backends and Priority
pages' data (MAD native-panel phase 2).

backends.describe is the schema-driven knob list: it mirrors the Tk
_backend_page composition (router-config-gui.py) knob for knob, in the same
order, so the C++ page only renders typed controls (bool / class_set / int /
slot_set / choice / slot_profiles) and never hardcodes a backend. All writes
go through the existing policy.set_backend_* RPCs; the per-slot profile apply
reuses lib.mad_backup.apply_slot_profile (active file only, named profiles
read-only).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import devices as dv
from .. import es_collections, es_systems, openbor_manifests, openbor_maps
from ..mad_backup import apply_slot_profile
from ..mad_config import (ADVANCED_KNOBS, CONFIG_PRESETS, KNOB_HELP, KNOWN_PADS,
                          PAD_SHORT, controller_families, list_profiles,
                          pad_class_candidates)
from ..policy import load_merged
from ..retroarch_cfg import core_dirs_for_system
from ..routing import (family_of, is_xarcade, load_policy, resolve_system,
                       xarcade_port)
from .preview_cmds import _esde_systems
from .rpc import RpcError, method
from .systems_cmds import (console_art, resolve_art, resolve_category,
                           _warn_flag)

# Each emulator backend → the console.png(s) of the system(s) it drives
# (verbatim from the Tk backends() page).
BE_SYS = {"cemu": ["wiiu"], "dolphin": ["gc", "wii"], "eden": ["switch"],
          "hypseus": ["daphne"], "openbor": ["openbor"], "pcsx2": ["ps2"],
          "rpcs3": ["ps3"], "supermodel": ["model3"], "xemu": ["xbox"],
          "xenia": ["xbox"], "flycast": ["dreamcast"]}


def _whitelist_empty(bcfg: dict) -> bool:
    """True when a backend USES the SDL-whitelist mechanism but has NEITHER
    pad_classes nor handheld_class populated (games get NO controllers).
    Mirrors App._whitelist_empty / the router's sdl-ignore guard."""
    uses = ("pad_classes" in bcfg) or ("handheld_class" in bcfg)
    return uses and not bcfg.get("pad_classes") and not bcfg.get("handheld_class")


@method("backends.list")
def _backends_list(params):
    """Rows for the Backends root page: every [backends.*] table whose system
    has games in ES-DE (all of them when gamelists are unavailable), with the
    Tk page's key summary and the ⚠ no-players state. Hidden names are
    reported for the dim footnote."""
    merged = load_merged()
    esde = _esde_systems()
    rows, hidden = [], []
    for bname in sorted(b for b, c in merged.get("backends", {}).items()
                        if isinstance(c, dict)):
        syslist = BE_SYS.get(bname, [bname])
        if esde and not any(s in esde for s in syslist):
            hidden.append(bname)
            continue
        bcfg = merged["backends"][bname]
        # Tile summary: knob names minus the config-location keys — paths are
        # detail-page info, not tile info (user request 2026-06-12).
        keys = [k for k in bcfg if k not in ADVANCED_KNOBS
                and k not in ("config_dir", "config_file")]
        rows.append({"name": bname,
                     "summary": ", ".join(keys[:4]) + ("…" if len(keys) > 4 else ""),
                     "no_players": _whitelist_empty(bcfg),
                     "art": [a for a in (console_art(s) for s in syslist) if a]})
    return {"backends": rows, "hidden": hidden}


def _class_set_knob(key: str, label: str, merged: dict, bcfg: dict,
                    bname: str = "") -> dict:
    current = set(bcfg.get(key, []))
    cands = pad_class_candidates(merged, *bcfg.get(key, []))
    if key == "pad_classes":
        # Always offer every known player-pad family (e.g. the Wii U Pro
        # Controller), not just vid:pids some backend already lists — otherwise a
        # pad like the Wii U Pro is impossible to pick. The Steam Deck's own pads
        # (28de:*) are handhelds, set via handheld_class, so they're excluded here.
        for c in PAD_SHORT:
            if not c.startswith("28de:") and c not in cands:
                cands.append(c)
        if bname == "openbor":
            # OpenBOR can only seat a pad the merger has a translation table for
            # (openbor_maps.CLASS_OF_VIDPID): the game sees canonical twins, and a
            # family we cannot translate produces no twin at all. Offering the
            # rest invited ticking a pad that then silently did not play — and,
            # worse, an untranslatable pad that IS listed makes the launch fall
            # back to raw pads. Anything already ticked stays offered, or it could
            # never be un-ticked.
            _ok = set(openbor_maps.CLASS_OF_VIDPID) | {"x-arcade", "xarcade"}
            cands = [c for c in cands if c in _ok or c in current]
    return {"key": key, "kind": "class_set", "label": label,
            "help": KNOB_HELP.get(key, ""),
            "candidates": [{"value": c, "label": PAD_SHORT.get(c, c),
                            "on": c in current} for c in cands]}


def _choice_knob(key: str, label: str, value: str, options: list,
                 help_key: str | None = None) -> dict:
    """options = [(value, label)] — the Tk _select_page contract."""
    return {"key": key, "kind": "choice", "label": label, "value": value,
            "value_label": next((lb for v, lb in options if v == value), value or "none"),
            "help": KNOB_HELP.get(help_key or key, ""),
            "options": [{"value": v, "label": lb} for v, lb in options]}


@method("backends.describe")
def _backends_describe(params):
    """The typed, ORDERED knob list for one backend — a 1:1 mirror of the Tk
    _backend_page composition (same knobs, same order, same conditionals)."""
    bname = params["backend"]
    merged = load_merged()
    bcfg = merged.get("backends", {}).get(bname)
    if not isinstance(bcfg, dict):
        raise RpcError("EINVAL", f"unknown backend {bname!r}")

    knobs = []

    # Hidden for openbor: the merger replaces every real pad with canonical twins
    # and openbor.sh then whitelists ONLY those, so this knob's whitelist never
    # reaches the game and "expose only the top connected pad" describes nothing
    # that can happen. It stays in controller-policy.toml because the router still
    # reads it on the merger-failure fallback path — but a control that lies is
    # worse than no control, so it is not offered here.
    if "sdl_priority" in bcfg and bname != "openbor":
        # A single bool renders INLINE: [switch] label, on one row (no redundant green header).
        # toggle_label overrides the inline text; empty/omitted falls back to "label".
        knobs.append({"key": "sdl_priority", "kind": "bool",
                      "label": "Strict Player-1 priority",
                      "help": KNOB_HELP["sdl_priority"],
                      "value": bool(bcfg["sdl_priority"])})

    if "pad_classes" in bcfg:
        knobs.append(_class_set_knob("pad_classes", "Player pad families", merged,
                                     bcfg, bname))

    # int managers (hidden for cemu/eden — their 8-slot profile picker is the slot UI)
    for key, lo, hi in (("manage_players", 1, 4), ("manage_pads", 1, 4)):
        if key in bcfg and isinstance(bcfg[key], int) and bname not in ("cemu", "eden"):
            knobs.append({"key": key, "kind": "int", "label": key.replace("_", " "),
                          "help": KNOB_HELP.get(key, ""), "value": int(bcfg[key]),
                          "lo": lo, "hi": hi, "step": 1})

    if "manage_ports" in bcfg and bname not in ("cemu", "eden"):
        mp = bcfg["manage_ports"]
        if isinstance(mp, list):
            knobs.append({"key": "manage_ports", "kind": "slot_set",
                          "label": "Managed controller slots",
                          "help": KNOB_HELP["manage_ports_list"],
                          "slots": [{"slot": s, "label": f"C{s + 1}", "on": s in mp}
                                    for s in range(8)]})
        elif isinstance(mp, int):
            knobs.append({"key": "manage_ports", "kind": "int", "label": "managed ports",
                          "help": KNOB_HELP["manage_ports_int"], "value": mp,
                          "lo": 1, "hi": 4, "step": 1})

    if "real2_min_wiimotes" in bcfg:
        knobs.append({"key": "real2_min_wiimotes", "kind": "int",
                      "label": "2-remote threshold",
                      "help": KNOB_HELP["real2_min_wiimotes"],
                      "value": int(bcfg["real2_min_wiimotes"]), "lo": 1, "hi": 4,
                      "step": 1})

    for key in ("respect_user_config_classes", "keep_extra"):
        if key in bcfg:
            knobs.append(_class_set_knob(key, key.replace("_", " "), merged, bcfg))

    # X-Arcade warn flag: for a single-system GAMEPAD backend, surface its controller-policy warn
    # toggle here (right below "respect user config classes") so the emulator's grid Controllers tile
    # stays single-step (no redundant [Controllers, chip] step). It reads/WRITES the SYSTEM flag, not
    # the backend config -- the "__sysflag__<system>__<flag>" key routes the write to the system in
    # policy.set_backend_key. (Non-gamepad emus like pcsx2/rpcs3 never open this page, so it is inert
    # for them.)
    _wsys = BE_SYS.get(bname, [bname])
    if len(_wsys) == 1:
        _wf = _warn_flag(_wsys[0], resolve_category(_wsys[0], merged))
        if _wf:
            _wkey, _wlabel = _wf
            _went = merged.get("systems", {}).get(_wsys[0], {})
            _went = _went if isinstance(_went, dict) else {}
            knobs.append({"key": f"__sysflag__{_wsys[0]}__{_wkey}", "kind": "bool",
                          "label": _wlabel, "help": "",
                          "value": bool(_went.get(_wkey, _wkey.startswith("warn_")))})

    if "handheld_class" in bcfg:
        cur = bcfg.get("handheld_class", "")
        opts = [("", "none")] + [(k, KNOWN_PADS.get(k, k)) for k in KNOWN_PADS]
        knobs.append(_choice_knob("handheld_class", "Handheld / fallback pad", cur, opts))

    # cemu profile pickers (from .xml in config_dir)
    cfg_path = bcfg.get("config_dir") or bcfg.get("config_file") or ""
    for key in ("p1_gamepad_template", "handheld_profile"):
        if key in bcfg:
            profs = [p.stem for p in list_profiles(cfg_path, "*.xml")]
            opts = [("", "none")] + [(s, s) for s in profs]
            knobs.append(_choice_knob(key, key.replace("_", " "),
                                      bcfg.get(key, ""), opts))

    # Per-slot profile picker (cemu/eden): the active slot file gets YOUR named
    # profile; the live-input tester button is phase 4 (testers).
    if bname in ("cemu", "eden"):
        if bname == "cemu":
            pdir = os.path.expanduser(
                bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles"))
            profs = sorted(p.stem for p in list_profiles(pdir, "*.xml")
                           if not re.fullmatch(r"controller\d+", p.stem))
            slot_label, intro = "Controller", (
                "Pick which of your named profiles loads on each slot — MAD saves it "
                "and applies it to the active slot file the moment you choose (have "
                "the emulator closed). C1 = the Steam Deck GamePad.")
        else:
            pdir = os.path.expanduser("~/.config/eden/input")
            profs = sorted(p.stem for p in list_profiles(pdir, "*.ini"))
            slot_label, intro = "Player", (
                "Pick which of your named profiles loads on each player — applied to "
                "the active config the moment you choose (have the emulator closed).")
        sp = bcfg.get("slot_profiles", {})
        sp = dict(sp) if isinstance(sp, dict) else {}
        # Degrade on hand-edited TOML: a non-string slot value renders as
        # unset instead of failing the whole describe.
        slots = [{"slot": s,
                  "profile": sp[str(s)] if isinstance(sp.get(str(s)), str) else ""}
                 for s in range(8)]
        knobs.append({"key": "slot_profiles", "kind": "slot_profiles",
                      "label": "Per-slot profiles  (your profiles — MAD never edits them)",
                      "help": intro, "slot_label": slot_label, "profiles": profs,
                      "profiles_dir": pdir, "slots": slots})

    for key in ("config_dir", "config_file"):
        if key in bcfg:
            presets = list(CONFIG_PRESETS.get((bname, key), []))
            cur = bcfg.get(key, "")
            if cur and cur not in presets:
                presets = [cur] + presets
            opts = [(p, ("✓ " if Path(p).expanduser().exists() else "· ") + p)
                    for p in presets]
            knobs.append(_choice_knob(key, key.replace("_", " "), cur, opts))

    # OpenBOR recovery, pad-reachable. openbor_cfg seeds our default map ONCE and
    # then hands the cfg back to the engine — the game's own Options -> Controls
    # is the editor from there. So clearing the seed mark is the only road back
    # for a game whose controls were edited into a corner, and until now it was
    # CLI-only (`python3 -m lib.openbor_cfg reseed <game>`) on a rig whose owner
    # does not run CLIs.
    #
    # It rides HERE, inside the Controllers page, rather than as a sibling row on
    # the tile: the tile then keeps exactly ONE section and opens straight into
    # this page (GuiMadPageStandalones: secs.size()==1), which is the single-step
    # shape asked for. A `choice` knob IS the picker the standing rule wants
    # (whole label + A-press full list), and the pick routes through
    # policy.set_backend_key's magic-key path — the same trick __sysflag__ uses
    # above. No new page, no C++.
    if bname == "openbor":
        _seeded = set(openbor_maps.seeded_keys())
        _names = openbor_manifests.names()
        # ✓ = carries our map (a reset re-applies it next launch); · = does not
        # yet, so it gets it on its next launch anyway and picking it is a no-op.
        _opts = [(k, ("✓ " if k in _seeded else "· ") + _names.get(k, k))
                 for k in openbor_manifests.dir_keys()]
        if _opts:
            knobs.append(_choice_knob(
                "__openbor_reseed__",
                "Reset a game's controls to the MAD default", "", _opts))

    return {"backend": bname, "warn_empty": _whitelist_empty(bcfg), "knobs": knobs,
            "advanced": [k for k in ADVANCED_KNOBS if k in bcfg]}


@method("profiles.apply_slot")
def _profiles_apply_slot(params):
    """Apply a named profile to an emulator slot's ACTIVE file (cemu/eden) and
    persist the choice — lib.mad_backup.apply_slot_profile verbatim.

    Deliberately FAST (inline on the stdin thread) even though it copies a
    file: every local.toml writer must run on the single stdin thread — a
    worker-pool writer would race the inline policy.* read-modify-writes and
    silently lose updates. The copy is one small profile file (~ms)."""
    bname = params["backend"]
    slot = int(params["slot"])
    if bname not in ("cemu", "eden") or not 0 <= slot <= 7:
        raise RpcError("EINVAL", "backend must be cemu|eden, slot 0..7")
    profile = params.get("profile", "")
    if "/" in profile or "\\" in profile or ".." in profile:   # path-traversal guard
        raise RpcError("EINVAL", f"invalid profile name {profile!r}")
    message = apply_slot_profile(bname, slot, profile)
    return {"message": message, "merged": load_merged()}


# ── priority.* ──

def _p1(ent: dict) -> str:
    order = (ent.get("ports") or [[]])[0]
    return order[0] if order else "(empty)"


@method("priority.list", slow=True)
def _priority_list(params):
    """The Priority root page + both pickers in one response (slow:
    load_systems parses es_systems.xml). Mirrors the Tk priority() and
    _priority_picker composition: configured = RetroArch systems with ports /
    enabled collections with ports; available = gamelist-backed, unconfigured,
    non-standalone systems / enabled unconfigured collections."""
    merged = load_merged()
    sysxml = es_systems.load_systems()
    fallback_pad = resolve_art(["icons/controllers.png", "controllers.png"])
    fallback_gun = resolve_art(["icons/lightgun.png", "lightgun.png",
                                "icons/sinden.png", "sinden.png"])

    configured = sorted(
        s for s, ent in merged.get("systems", {}).items()
        if isinstance(ent, dict) and ent.get("ports")
        and not es_systems.is_standalone(es_systems.default_command(s, sysxml)))
    systems = [{"name": s, "p1": _p1(merged["systems"][s]), "art": console_art(s)}
               for s in configured]

    cfg_c = merged.get("collections", {})
    cols = []
    for c in es_collections.enabled_collections():
        ent = cfg_c.get(c)
        if not (isinstance(ent, dict) and ent.get("ports")):
            continue
        lightgun = bool(ent.get("require_sinden"))
        cols.append({"name": c, "p1": _p1(ent), "lightgun": lightgun,
                     "art": console_art(c) or (fallback_gun if lightgun
                                               else fallback_pad)})

    have = {s for s, ent in merged.get("systems", {}).items()
            if isinstance(ent, dict) and ent.get("ports")}
    avail_s = sorted(
        s for s in sysxml
        if es_systems._has_gamelist(s) and s not in have
        and not es_systems.is_standalone(es_systems.default_command(s, sysxml)))
    have_c = {c for c in cfg_c if isinstance(cfg_c.get(c), dict)
              and cfg_c[c].get("ports")}
    avail_c = [c for c in es_collections.enabled_collections() if c not in have_c]

    return {"systems": systems, "collections": cols,
            "available_systems": [{"name": s, "art": console_art(s)}
                                  for s in avail_s],
            "available_collections": [{"name": c,
                                       "art": console_art(c) or fallback_pad}
                                      for c in avail_c]}


def _effective_p1(ent: dict, fams: list) -> str:
    """The effective Player-1 family for a scopes-picker tile: the SAME order
    composition priority.get uses (existing ports[0] filtered to known
    families, then remaining known families appended) — so a scope with no
    `ports` rule still gets a sensible P1 (the top of the family list) instead
    of priority.list's bare "(empty)". "(default)" only when nothing resolves
    at all (no known families either — practically never, KNOWN_FAMILIES is
    always non-empty)."""
    existing = ent.get("ports") or []
    cur = list(existing[0]) if existing and existing[0] else []
    order = [f for f in cur if f in fams] + [f for f in fams if f not in cur]
    return order[0] if order else "(default)"


def _dict_ent(table: dict, name: str) -> dict:
    ent = table.get(name)
    return ent if isinstance(ent, dict) else {}


def present_ra_systems(sysxml: dict | None = None) -> list[str]:
    """Every RA system with an ES-DE gamelist AND a non-standalone active
    command, sorted — the enumeration predicate shared by racontrollers.scopes
    (Phase 2b) and ragame.systems (RetroArch hub Per-game, Phase 3), so the
    "which systems count as RetroArch" answer never diverges between the two
    all-present-systems pages."""
    if sysxml is None:
        sysxml = es_systems.load_systems()
    return sorted(
        s for s in sysxml
        if es_systems._has_gamelist(s)
        and not es_systems.is_standalone(es_systems.default_command(s, sysxml)))


@method("racontrollers.scopes", slow=True)
def _racontrollers_scopes(params):
    """Every PRESENT RetroArch system + collection (configured ∪ available —
    the union priority.list splits into two pickers) for the Controllers
    subpage list: name/p1/art per scope, same predicates and sort as
    priority.list (ES-DE gamelist, non-standalone active command, enabled
    collection), so a scope with no `ports` rule yet is still listed (with an
    effective P1 computed by _effective_p1, not just the order-configured
    ones priority.list's "configured" bucket returns)."""
    merged = load_merged()
    sysxml = es_systems.load_systems()
    fams = controller_families(merged)
    fallback_pad = resolve_art(["icons/controllers.png", "controllers.png"])
    fallback_gun = resolve_art(["icons/lightgun.png", "lightgun.png",
                                "icons/sinden.png", "sinden.png"])

    sysd = merged.get("systems", {})
    all_s = present_ra_systems(sysxml)
    systems = [{"name": s, "p1": _effective_p1(_dict_ent(sysd, s), fams),
                "art": console_art(s)} for s in all_s]

    cfg_c = merged.get("collections", {})
    cols = []
    for c in es_collections.enabled_collections():
        ent = _dict_ent(cfg_c, c)
        lightgun = bool(ent.get("require_sinden"))
        cols.append({"name": c, "p1": _effective_p1(ent, fams),
                     "lightgun": lightgun,
                     "art": console_art(c) or (fallback_gun if lightgun
                                               else fallback_pad)})

    return {"systems": systems, "collections": cols}


@method("priority.get")
def _priority_get(params):
    """Editor data for one system/collection/game: the order list composed the
    Tk way (existing order filtered to known families, remaining families
    appended), the existing nports (default 2), and require_sinden for
    collections.

    GAME scope (RetroArch-hub Phase 3, "<system>:<stem>" name) INHERITS its
    system's RESOLVED order as the base when the game entry has no `ports` of
    its own — mirroring routing.resolve_policy's per-game tier (~141-153): a
    game rule with no `ports` doesn't touch the system's; an explicit game
    `ports` REPLACES the system's wholesale, never merges. Carries neither
    `warn` (system-only) nor `require_sinden` (collection-only) — same shape
    otherwise. The WRITE side (policy.set_ports/clear_ports kind:"game")
    already accepts games via _KIND_TABLE (lib/madsrv/policy_cmds.py); only
    this read side needed extending."""
    kind = params.get("kind", "system")
    if kind not in ("system", "collection", "game"):
        raise RpcError("EINVAL", f"kind must be system|collection|game, got {kind!r}")
    name = params["name"]
    merged = load_merged()
    fams = controller_families(merged)

    if kind == "game":
        system, _, _stem = name.partition(":")
        own = merged.get("games", {}).get(name, {})
        own = own if isinstance(own, dict) else {}
        # A game with no `ports` of its own inherits its system's RESOLVED
        # order (follow the `inherits` chain, e.g. mame -> arcade), NOT the raw
        # unresolved [systems.<name>] entry (which may be inherits-only).
        resolved = resolve_system(merged, system) or {}
        ent = own if "ports" in own else resolved
        existing = ent.get("ports") or []
        cur = list(existing[0]) if existing and existing[0] else []
        order = [f for f in cur if f in fams]
        order += [f for f in fams if f not in order]
        return {"name": name, "kind": kind, "order": order,
                "nports": len(existing) if existing else 2,
                "configured": bool(own.get("ports"))}

    table = "systems" if kind == "system" else "collections"
    ent = merged.get(table, {}).get(name, {})
    existing = ent.get("ports") or []
    cur = list(existing[0]) if existing and existing[0] else []
    order = [f for f in cur if f in fams]
    order += [f for f in fams if f not in order]
    out = {"name": name, "kind": kind, "order": order,
           "nports": len(existing) if existing else 2,
           "configured": bool(existing),
           "require_sinden": bool(ent.get("require_sinden", False))}
    if kind == "system":
        toggles = []
        wf = _warn_flag(name, resolve_category(name, merged))
        if wf:
            key, label = wf
            warn = {"key": key, "label": label, "value": bool(ent.get(key, True))}
            toggles.append(warn)
            out["warn"] = warn   # back-compat: pre-toggles binaries read one "warn"
        # Hands-off (router_skip): RA systems listed here (racontrollers.scopes =
        # present_ra_systems) are never base-hands-off, so it is freely toggleable;
        # the base-hands-off clamp is enforced server-side (policy.set_scope_flag).
        toggles.append({"key": "router_skip",
                        "label": "Hands-off (leave input untouched)",
                        "value": bool(ent.get("router_skip", False))})
        out["toggles"] = toggles
        # Gate the per-system editor's "RetroArch options" button: only systems with
        # on-disk RA core dirs have per-system options to edit (rasys_<system>).
        out["ra_options_available"] = bool(core_dirs_for_system(name))
    return out


# ── racontrollers.* (RetroArch hub — Controllers section) ──

def _ra_toggles_for(scope: str, name: str, merged: dict) -> list:
    """Scope-specific toggles rendered on the Controllers editor. Always empty
    now: the X-Arcade presence warnings moved OFF the global root and onto
    each system's own editor (priority.get's "warn" field, RetroArch-hub
    Controllers restructure) — kept as a function (and racontrollers.get's
    "toggles" key kept, always []) for contract stability."""
    return []


@method("racontrollers.get", slow=True)
def _racontrollers_get(params):
    """Editor data for the RetroArch hub's Controllers section: the same
    order/nports/require_sinden composition as priority.get (global reads
    [defaults] instead of a systems/collections entry), plus the
    currently-connected controller families (for the picker to highlight
    what's actually plugged in). "toggles" is always [] now — see
    _ra_toggles_for."""
    scope = params.get("scope", "global")
    name = params.get("name", "")
    if scope not in ("global", "system", "collection"):
        raise RpcError("EINVAL", "scope must be global|system|collection")
    merged = load_merged()
    policy = load_policy()
    xport = xarcade_port(policy)
    if scope == "global":
        ent = merged.get("defaults", {})
    else:
        ent = merged.get({"system": "systems", "collection": "collections"}[scope],
                         {}).get(name, {})
    fams = controller_families(merged)
    existing = ent.get("ports")
    # ports may be per-port (list of family-lists) or a FLAT family list the router
    # also accepts for [defaults] (controller-router._setup); normalize to a family
    # list so the P1 order isn't read as characters of a string.
    first = existing[0] if existing else []
    cur = first if isinstance(first, list) else list(existing)
    order = [f for f in cur if f in fams] + [f for f in fams if f not in cur]
    nports = len(existing) if existing else 2
    conn = []
    for d in dv.joypads(dv.enumerate_devices()):
        fam = "X-Arcade" if is_xarcade(d, xport) else family_of(d)
        if fam is None and f"{d.vid:04x}:{d.pid:04x}" == "28de:1205":
            fam = "Steam Deck"          # family_of has no case for the Deck's own pad
        if fam and fam not in conn:
            conn.append(fam)
    return {"scope": scope, "name": name, "order": order, "nports": nports,
            "require_sinden": bool(ent.get("require_sinden")),
            "connected_families": conn,
            "toggles": _ra_toggles_for(scope, name, merged)}
