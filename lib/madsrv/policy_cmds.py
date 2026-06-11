"""policy.* / splash.* methods — ports of the Tk GUI save-handlers.

Every write goes through lib.localpolicy.dump (atomic os.replace) and runs
SYNCHRONOUSLY on the stdin thread (fast file ops), so the response ack means
the file is on disk — the panel may close the page immediately after, and a
game launched right away sees the new value. Each write returns the fresh
merged view so the UI re-renders from truth, not from optimism.
"""
from __future__ import annotations

import tomllib

from .. import localpolicy, mad_backup, mad_config
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


@method("policy.local")
def _policy_local(params):
    return {"local": localpolicy.load(LOCAL)}


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


def _table_for(kind: str) -> str:
    if kind not in ("system", "collection"):
        raise RpcError("EINVAL", f"kind must be system|collection, got {kind!r}")
    return "systems" if kind == "system" else "collections"


@method("policy.set_ports")
def _set_ports(params):
    """Port of the Priority page save(): ports = [order] * nports; a collection
    rule also carries require_sinden."""
    table = _table_for(params.get("kind", "system"))
    name = params["name"]
    order = [str(x) for x in params["order"]]
    nports = int(params.get("nports", 2))
    if not order or nports < 1 or nports > 16:
        raise RpcError("EINVAL", "order must be non-empty, 1 <= nports <= 16")
    data = localpolicy.load(LOCAL)
    entry = data.setdefault(table, {}).setdefault(name, {})
    entry["ports"] = [list(order) for _ in range(nports)]
    if table == "collections" and "require_sinden" in params:
        entry["require_sinden"] = bool(params["require_sinden"])
    localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.clear_ports")
def _clear_ports(params):
    """Port of App._priority_clear (drops the empty entry husk)."""
    table = _table_for(params.get("kind", "system"))
    name = params["name"]
    data = localpolicy.load(LOCAL)
    d = data.get(table, {})
    if name in d:
        d[name].pop("ports", None)
        if table == "collections":
            d[name].pop("require_sinden", None)
        if not d[name]:
            del d[name]
        localpolicy.dump(LOCAL, data)
    return _merged_result()


@method("policy.set_pins")
def _set_pins(params):
    """Port of the Players save(): scope None/"" = global [pins], else
    [systems.<scope>.pins]; an empty table deletes the key + empty husk."""
    scope = params.get("scope") or None
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
    if scope is None:
        if tbl:
            data["pins"] = tbl
        else:
            data.pop("pins", None)
    else:
        syst = data.setdefault("systems", {}).setdefault(scope, {})
        if tbl:
            syst["pins"] = tbl
        else:
            syst.pop("pins", None)
            if not syst:                 # don't leave an empty [systems.<scope>] table
                data["systems"].pop(scope, None)
    localpolicy.dump(LOCAL, data)
    return _merged_result({"saved": len(tbl)})


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
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(params["backend"], {})[params["key"]] = params["value"]
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


@method("policy.set_backend_template")
def _set_backend_template(params):
    """Port of App._set_template (cemu per-family profile)."""
    bname, cls, profile = params["backend"], params["cls"], params["profile"]
    merged = load_merged()
    tmpl = dict(merged.get("backends", {}).get(bname, {}).get("templates", {}))
    tmpl[cls] = profile
    data = localpolicy.load(LOCAL)
    data.setdefault("backends", {}).setdefault(bname, {})["templates"] = tmpl
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


@method("policy.reset_local")
def _reset_local(params):
    return _merged_result({"message": mad_backup.reset_local()})


@method("policy.gui_flags")
def _gui_flags(params):
    return mad_config.gui_flags()


@method("policy.set_gui_flag")
def _set_gui_flag(params):
    mad_config.set_gui_flag(params["key"], params["value"])
    return mad_config.gui_flags()


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
    standalone systems (auto-discovered from ES-DE, same as the Tk page), and
    the per-system overrides with button names resolved."""
    from .. import es_systems
    from .capture_cmds import btn_name
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
    return {"buttons": buttons, "names": [btn_name(b) for b in buttons],
            "hold_sec": float(qc.get("hold_sec", 1.0)),
            "eligible": eligible, "overrides": overrides}
