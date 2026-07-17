"""policy.* / splash.* methods — ports of the Tk GUI save-handlers.

Every write goes through lib.localpolicy.dump (atomic os.replace) and runs
SYNCHRONOUSLY on the stdin thread (fast file ops), so the response ack means
the file is on disk — the panel may close the page immediately after, and a
game launched right away sees the new value. Each write returns the fresh
merged view so the UI re-renders from truth, not from optimism.
"""
from __future__ import annotations

import tomllib

from .. import localpolicy, mad_config
from ..policy import LOCAL, POLICY, load_merged
from .rpc import RpcError, method


def _merged_result(extra: dict | None = None) -> dict:
    out = {"merged": load_merged()}
    if extra:
        out.update(extra)
    return out


@method("policy.merged")
def _policy_merged(params):
    return {"merged": load_merged()}


@method("policy.set_system_flag")
def _set_system_flag(params):
    """Port of App._set_sys: a value matching the system's BASE (non-local) state
    is a REVERT — drop the local key, and the whole entry once empty, so the
    Systems-page ● marker tracks REAL deviations only."""
    sysname, flag = params["system"], params["flag"]
    value = bool(params["value"])
    try:
        base_ent = tomllib.load(open(POLICY, "rb")).get("systems", {}).get(sysname, {})
    except Exception:
        base_ent = {}
    # display defaults (mirror the Systems detail page): warn_* default ON,
    # router_skip / require_* default OFF
    default = base_ent.get(flag, flag.startswith("warn_"))
    # Protective clamp: a system whose BASE policy ships router_skip = true is a
    # documented HANDS-OFF system (switch/openbor/wiiu/daphne) — the router must
    # never touch its input. Refuse to persist a router_skip = false override for
    # it (it would re-enable the active backend handler, e.g. eden_assign rewriting
    # hand-configured Switch input every launch). Forcing value back to True makes
    # it match `default`, so the existing revert branch below drops the key.
    if flag == "router_skip" and not value and base_ent.get("router_skip") is True:
        value = True
    data = localpolicy.load(LOCAL)
    sysd = data.setdefault("systems", {})
    ent = sysd.setdefault(sysname, {})
    if value == bool(default):
        ent.pop(flag, None)
        if not ent:
            sysd.pop(sysname, None)
    else:
        ent[flag] = value
    localpolicy.dump(LOCAL, data)
    return _merged_result()


_KIND_TABLE = {"system": "systems", "collection": "collections", "game": "games"}


def _scope_entry(data: dict, kind: str, name: str, *, create: bool):
    """Locate a scope's entry dict for the four-tier cascade (RetroArch-hub).
    Returns (entry, container, key): 'global' -> data['defaults'] directly
    (container/key None); others -> data[<table>][<name>]. `name` for a game is
    the '<system>:<rom>' key. create=False yields (None, ...) when absent; non-dict
    husks (hand edits) are reset when create=True. Prune via _prune()."""
    if kind == "global":
        if create and not isinstance(data.get("defaults"), dict):
            data["defaults"] = {}
        d = data.get("defaults")
        return (d if isinstance(d, dict) else None), None, None
    table = _KIND_TABLE.get(kind)
    if table is None:
        raise RpcError("EINVAL",
                       f"kind must be global|system|collection|game, got {kind!r}")
    if not name:
        raise RpcError("EINVAL", f"{kind} scope requires a name")
    if create:
        if not isinstance(data.get(table), dict):
            data[table] = {}
        if not isinstance(data[table].get(name), dict):
            data[table][name] = {}
        return data[table][name], data[table], name
    container = data.get(table)
    if not isinstance(container, dict):
        return None, None, name
    ent = container.get(name)
    return (ent if isinstance(ent, dict) else None), container, name


def _prune(data: dict, kind: str, entry: dict, container, key) -> None:
    """Drop a now-empty scope entry (and the global 'defaults' husk)."""
    if entry:
        return
    if kind == "global":
        data.pop("defaults", None)
    elif container is not None:
        container.pop(key, None)


@method("policy.set_ports")
def _set_ports(params):
    """Save controller-TYPE priority for a scope: ports = [order] * nports.
    kind in {global, system, collection, game} (RetroArch-hub four-tier); a
    collection also carries require_sinden."""
    kind = params.get("kind", "system")
    name = params.get("name", "")
    order = [str(x) for x in params["order"]]
    nports = int(params.get("nports", 2))
    if not order or nports < 1 or nports > 16:
        raise RpcError("EINVAL", "order must be non-empty, 1 <= nports <= 16")
    data = localpolicy.load(LOCAL)
    entry, _c, _k = _scope_entry(data, kind, name, create=True)
    entry["ports"] = [list(order) for _ in range(nports)]
    if kind == "collection" and "require_sinden" in params:
        entry["require_sinden"] = bool(params["require_sinden"])
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.clear_ports")
def _clear_ports(params):
    """Drop a scope's ports (+ require_sinden for a collection) and prune the husk."""
    kind = params.get("kind", "system")
    name = params.get("name", "")
    data = localpolicy.load(LOCAL)
    entry, container, key = _scope_entry(data, kind, name, create=False)
    if entry is not None:
        entry.pop("ports", None)
        if kind == "collection":
            entry.pop("require_sinden", None)
        _prune(data, kind, entry, container, key)
        localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_pins")
def _set_pins(params):
    """Save device->player pins for a scope. New callers pass kind in
    {global, system, collection, game} + name; legacy callers pass `scope`
    (None/"" = global, else a system name). Global pins live at top-level [pins]
    (resolve_pins reads that as the baseline); scoped pins live under the entry's
    `pins` key (picked up via resolve_policy -> eff_pins). Empty table deletes the
    key + empty husk."""
    kind = params.get("kind")
    if kind is None:                                  # legacy scope= shape
        scope = params.get("scope") or None
        kind, name = ("global", "") if scope is None else ("system", scope)
    else:
        name = params.get("name", "")
    pins = params.get("pins") or {}
    tbl = {}
    for k, v in pins.items():
        try:
            p = int(k)
        except (TypeError, ValueError):
            raise RpcError("EINVAL", f"pin key {k!r} is not a player number")
        if v:
            tbl[str(p)] = str(v)
    data = localpolicy.load(LOCAL)
    if kind == "global":
        if tbl:
            data["pins"] = tbl
        else:
            data.pop("pins", None)
    else:
        entry, container, key = _scope_entry(data, kind, name, create=bool(tbl))
        if tbl:
            entry["pins"] = tbl
        elif entry is not None:
            entry.pop("pins", None)
            _prune(data, kind, entry, container, key)
    localpolicy.dump(LOCAL, data)
    return _merged_result({"saved": len(tbl)})


@method("policy.set_scope_flag")
def _set_scope_flag(params):
    """Generalized set_system_flag across the four scopes. A value matching the
    BASE default is a REVERT (drop the local key + empty husk). Preserves the
    router_skip base-hands-off clamp (a base router_skip=true can't be flipped
    off). The RA Controllers page's toggles write here."""
    kind = params.get("kind", "system")
    name = params.get("name", "")
    flag = params["flag"]
    value = bool(params["value"])
    base_ent = {}
    if kind == "system" and name:
        try:
            base_ent = tomllib.load(open(POLICY, "rb")).get("systems", {}).get(name, {})
        except Exception:
            base_ent = {}
    default = base_ent.get(flag, flag.startswith("warn_"))
    if flag == "router_skip" and not value and base_ent.get("router_skip") is True:
        value = True
    data = localpolicy.load(LOCAL)
    entry, container, key = _scope_entry(data, kind, name, create=True)
    # Revert-to-default (drop the local key) ONLY for scopes whose inherited default
    # is reliably known here: system reads base policy; global is the hardcoded
    # default. For game/collection the true default is the RESOLVED system / inherits
    # value, which this method does not compute — so persist the explicit value
    # rather than silently drop a real override (RetroArch-hub review issue 2;
    # inherit-aware clear lands with the Phase 2 flag UI that will use these scopes).
    if kind in ("system", "global") and value == bool(default):
        entry.pop(flag, None)
        _prune(data, kind, entry, container, key)
    else:
        entry[flag] = value
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_quit_combo")
def _set_quit_combo(params):
    """Global ([quit_combo] buttons + hold_sec) or per-system override
    ([quit_combo.<system>] buttons only) — ports of gsave() / the detect grabs."""
    scope = params.get("scope") or None
    buttons = sorted(int(b) for b in params["buttons"])
    if not buttons:
        raise RpcError("EINVAL", "buttons must be non-empty")
    data = localpolicy.load(LOCAL)
    qc = data.setdefault("quit_combo", {})
    if scope is None:
        qc["buttons"] = buttons
        if "hold_sec" in params:
            qc["hold_sec"] = float(params["hold_sec"])
    else:
        qc.setdefault(scope, {})["buttons"] = buttons
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.clear_quit_combo")
def _clear_quit_combo(params):
    """Port of App._clear_sys (per-system override only)."""
    sysname = params["system"]
    data = localpolicy.load(LOCAL)
    if isinstance(data.get("quit_combo"), dict) and sysname in data["quit_combo"]:
        del data["quit_combo"][sysname]
        localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_backend_key")
def _set_backend_key(params):
    """Port of App._set_backend (scalar knob)."""
    key = params["key"]
    if key.startswith("__sysflag__"):
        # A controller-policy warn flag surfaced on a gamepad page (backends.describe): route to the
        # SYSTEM flag, not the backend config. Key format: __sysflag__<system>__<flag>. Reuse
        # _set_system_flag (the one source of truth for base-default revert + hands-off clamp).
        sysname, flag = key[len("__sysflag__"):].split("__", 1)
        _set_system_flag({"system": sysname, "flag": flag, "value": params["value"]})
        return _merged_result()
    if key == "__openbor_reseed__":
        # Not a config knob: the OpenBOR Controllers page's recovery picker (see
        # backends_cmds). Forgetting the seed mark makes the next launch put our
        # default map back — the only road back once openbor_cfg has handed the
        # cfg to the engine. Value = the manifest DIR key.
        #
        # The empty guard is LOAD-BEARING, twice over:
        #   1. openbor_maps.clear_seeded(None) forgets EVERY game, so a stray empty
        #      value must be a no-op, never a rig-wide wipe. (clear_seeded keys that
        #      branch on `is None`, not falsiness, so "" could not reach it anyway —
        #      belt and braces, deliberately.)
        #   2. Since 2026-07-17 the picker's FIRST row is "" ("Nothing selected") ON
        #      PURPOSE, so the cursor parks on a no-op instead of on the first game
        #      (an action knob's value is "", which matched no option, so the C++
        #      defaulted the cursor to row 0 and two A-presses reset that game with
        #      no confirmation). This guard is what makes that row inert, so it is
        #      now UI behaviour and not only defence. See backends_cmds.
        from .. import openbor_manifests, openbor_maps
        dir_key = str(params.get("value") or "")
        if not dir_key:
            return _merged_result()
        openbor_maps.clear_seeded(dir_key)
        # Name the outcome ourselves: this is an action, not a setting, so the
        # page's default "Saved openbor.__openbor_reseed__ = <game>" would leak
        # the magic key AND claim something untrue. Says what actually happens,
        # including WHEN — the write lands at that game's next launch, not now.
        # Leading verb, not "<name>: ...": several titles END in a colon of their
        # own ("Golden Axe: Genesis"), which made the punctuation read as noise.
        name = openbor_manifests.names().get(dir_key, dir_key)
        return _merged_result(
            {"flash": f"Reset {name} to MAD's default controls (applied at next launch)"})
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(params["backend"], {})[key] = params["value"]
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_backend_list_member")
def _set_backend_list_member(params):
    """Port of App._set_list_member (pad_classes / slot lists)."""
    bname, key = params["backend"], params["key"]
    member, present = params["member"], bool(params["present"])
    merged = load_merged()
    cur = list(merged.get("backends", {}).get(bname, {}).get(key, []))
    if present and member not in cur:
        cur.append(member)
    elif not present and member in cur:
        cur.remove(member)
    if params.get("is_int"):
        cur = sorted(set(cur))
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(bname, {})[key] = cur
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_hardware")
def _set_hardware(params):
    """[hardware].<key> = value (e.g. xarcade_port from press-to-identify)."""
    data = localpolicy.load(LOCAL)
    data.setdefault("hardware", {})[params["key"]] = params["value"]
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.clear_hardware")
def _clear_hardware(params):
    """Port of App._clear_xarcade generalized: drop a [hardware] key + empty husk."""
    data = localpolicy.load(LOCAL)
    if (data.get("hardware") or {}).pop(params["key"], None) is not None:
        if not data.get("hardware"):
            data.pop("hardware", None)
        localpolicy.dump(LOCAL, data)
    return _merged_result()


# ── splash ──

@method("splash.get")
def _splash_get(params):
    return {"splash": mad_config.splash_cfg(),
            "modes": mad_config.SPLASH_MODES,
            "fits": mad_config.SPLASH_FITS,
            "picker_cap": mad_config.SPLASH_PICKER_CAP}


@method("splash.set")
def _splash_set(params):
    mad_config.set_splash(params["key"], params["value"])
    return {"splash": mad_config.splash_cfg()}


@method("splash.images")
def _splash_images(params):
    return {"images": mad_config.list_splash_images()}


@method("splash.toggle_image")
def _splash_toggle_image(params):
    mad_config.toggle_splash_image(params["name"], bool(params["on"]))
    return {"splash": mad_config.splash_cfg()}


# ── quit combo (page data; writes go through policy.set_quit_combo) ──

@method("quitcombo.get")
def _quitcombo_get(params):
    """Page data for the Quit-combo page: global combo + hold, the eligible
    standalone systems (auto-discovered from ES-DE, same as the Tk page), the
    per-system overrides, and the enabled custom collections + their per-collection
    overrides (a collection combo overrides the system/per-game combo — stored under
    scope "collection-<name>"). All button lists have their names resolved."""
    from .. import es_systems, es_collections
    from .capture_cmds import btn_name
    from .systems_cmds import console_art
    merged = load_merged()
    qc = merged.get("quit_combo", {})
    buttons = [int(b) for b in qc.get("buttons", [314, 315])]
    eligible = list(es_systems.quit_combo_systems(merged))
    overrides = {}
    for s in eligible:
        ent = qc.get(s)
        if isinstance(ent, dict) and "buttons" in ent:
            bs = [int(b) for b in ent["buttons"]]
            overrides[s] = {"buttons": bs, "names": [btn_name(b) for b in bs]}
    # Enabled custom collections (name + member count for the picker) and any
    # per-collection combo already set (scope "collection-<name>"), for the grid.
    collections = []
    collection_overrides = {}
    for cname in es_collections.enabled_collections():
        collections.append({"name": cname, "count": len(es_collections.members(cname)),
                            "art": console_art(cname) or ""})
        ent = qc.get(f"collection-{cname}")
        if isinstance(ent, dict) and "buttons" in ent:
            bs = [int(b) for b in ent["buttons"]]
            collection_overrides[cname] = {"buttons": bs, "names": [btn_name(b) for b in bs]}
    return {"buttons": buttons, "names": [btn_name(b) for b in buttons],
            "hold_sec": float(qc.get("hold_sec", 1.0)),
            "eligible": eligible, "overrides": overrides,
            "collections": collections, "collection_overrides": collection_overrides}
