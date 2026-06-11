"""preview.* methods — the would-route preview, now running the router's REAL
resolution pipeline (lib.routing) for RetroArch systems/collections.

This intentionally ends the old Tk-GUI divergence: _preview_route re-implemented
port resolution over SDL devices with class-token heuristics and IGNORED pins,
the fallback rescue, and X-Arcade port identity. The daemon previews exactly
what controller-router.py will do at launch (read-only — nothing is written).
Standalone hands-off backends keep their config-file preview
(lib.standalone_preview), and dolphin keeps the DolphinBar status text.
"""
from __future__ import annotations

from .. import devices as dv
from .. import es_collections, es_systems
from ..mad_config import backend_systems
from ..policy import load_merged
from ..routing import (load_policy, resolve_pins, resolve_policy, resolve_ports,
                       reserve_value, xarcade_port)
from ..standalone_preview import standalone_profile_preview
from .device_cmds import _devices_wiimotes, pad_label, ser_device
from .rpc import method


def _esde_systems() -> set:
    """Systems with a gamelist.xml (same signal ES-DE uses to hide empty ones)."""
    from ..esde_settings import APPDATA
    gl = APPDATA / "gamelists"
    if not gl.is_dir():
        return set()
    return {d.name for d in gl.iterdir()
            if d.is_dir() and (d / "gamelist.xml").is_file()}


def _items(merged: dict) -> list[dict]:
    """The routed-things list, mirroring the Tk Preview page composition:
    standalone-backend systems (with games) + Priority-configured RetroArch
    systems + configured collections."""
    esde = _esde_systems()
    sysxml = es_systems.load_systems()
    items, seen = [], set()
    for sysname in backend_systems(merged):
        if esde and sysname not in esde:          # configured but no games (xbox, model3…)
            continue
        if sysname not in seen:
            seen.add(sysname)
            items.append({"key": sysname, "label": sysname, "art": sysname,
                          "kind": "system"})
    for s in sorted(merged.get("systems", {})):
        ent = merged["systems"][s]
        if not (isinstance(ent, dict) and ent.get("ports")) or s in seen:
            continue
        if es_systems.is_standalone(es_systems.default_command(s, sysxml)):
            continue                              # standalone ones came from backend_systems
        seen.add(s)
        items.append({"key": s, "label": s, "art": s, "kind": "system"})
    cfg_c = merged.get("collections", {})
    for c in es_collections.enabled_collections():
        if isinstance(cfg_c.get(c), dict) and cfg_c[c].get("ports") and c not in seen:
            seen.add(c)
            items.append({"key": c, "label": f"▣ {c}", "art": None,
                          "kind": "collection"})
    return items


def _rows(pads) -> list[dict]:
    """(slot, text[, icon]) tuples → row dicts."""
    out = []
    for t in pads:
        row = {"slot": t[0], "text": t[1]}
        if len(t) > 2:
            row["icon"] = t[2]
        out.append(row)
    return out


def _route_one(key: str, kind: str, merged: dict, policy: dict, xport: str,
               devs, sdl_devs, wm: int) -> dict:
    ent = (merged.get("systems", {}).get(key)
           or merged.get("collections", {}).get(key) or {})
    be = ent.get("backend")
    if be in ("cemu", "eden", "rpcs3", "pcsx2"):
        k, data = standalone_profile_preview(be, merged, sdl_devs)
        return ({"kind": "text", "text": data} if k == "text"
                else {"kind": "pads", "rows": _rows(data)})
    if be == "dolphin":
        if not dv.dolphinbar_present():
            return {"kind": "text", "text": "⚠ no DolphinBar connected"}
        if not dv._dolphinbar_slot_nodes():
            return {"kind": "text",
                    "text": "⚠ DolphinBar connected but exposing 0 slots — re-plug its USB"}
        return {"kind": "text", "text": f"DolphinBar: {wm} Wiimote{'s' if wm > 1 else ''}"}
    if be and be != "retroarch":
        # standalone backend → vid:pid pad_classes over the SDL view (what the
        # emulator itself will see through the SDL whitelist)
        bcfg = merged.get("backends", {}).get(be or "", {})
        classes = list(bcfg.get("pad_classes", []))
        if be == "cemu":
            classes = list(bcfg.get("templates", {}).keys())
        prio = {c: i for i, c in enumerate(classes)}
        ps = sorted((d for d in sdl_devs if getattr(d, "vidpid", "") in prio),
                    key=lambda d: (prio[d.vidpid], d.index))
        if not ps:
            hh = bcfg.get("handheld_class") or bcfg.get("handheld_profile")
            return {"kind": "text",
                    "text": f"(no player pad → {('handheld: ' + str(hh)) if hh else 'unchanged'})"}
        rows = []
        for i, d in enumerate(ps[:4]):
            vid = int(d.vidpid.split(":")[0], 16) if getattr(d, "vidpid", "") else 0
            rows.append({"slot": f"P{i + 1}",
                         "text": pad_label(vid, d.vidpid, d.name, "", xport)})
        return {"kind": "pads", "rows": rows}
    # RetroArch system OR collection → the router's REAL pipeline, read-only
    sys_entry = (resolve_policy(policy, key, None) if kind == "system"
                 else resolve_policy(policy, "", key)) or ent
    ports = sys_entry.get("ports") or []
    if not ports:
        return {"kind": "text", "text": "(not configured)"}
    eff_pins = {**policy.get("pins", {}), **sys_entry.get("pins", {})}
    pinned, claimed = resolve_pins(eff_pins, devs)
    port_devs = resolve_ports(ports, devs, preassigned=pinned,
                              preclaimed=claimed, xport=xport)
    if not port_devs:
        return {"kind": "text", "text": "(no matching pad connected)"}
    rows = []
    for p in sorted(port_devs):
        d = port_devs[p]
        rows.append({"slot": f"P{p}",
                     "text": pad_label(d.vid, f"{d.vid:04x}:{d.pid:04x}", d.name,
                                       dv.port_of(d.phys), xport),
                     "pinned": p in pinned,
                     "reserve": reserve_value(d)})
    return {"kind": "pads", "rows": rows}


@method("preview.route", slow=True)
def _preview_route(params):
    key = params["key"]
    kind = params.get("kind", "system")
    merged = load_merged()
    policy = load_policy()
    xport = xarcade_port(policy)
    devs = dv.enumerate_devices()
    sdl_devs = dv.sdl_devices()
    wm = _devices_wiimotes({}).get("count", 0)
    return {"route": _route_one(key, kind, merged, policy, xport, devs, sdl_devs, wm)}


@method("preview.all", slow=True)
def _preview_all(params):
    """One response feeding the whole Preview page: connected controllers (SDL
    order, evdev-joined: label/battery/port), DolphinBar status, X-Arcade port,
    and the would-route result for every routed system/collection."""
    merged = load_merged()
    policy = load_policy()
    xport = xarcade_port(policy)
    devs = dv.enumerate_devices()
    sdl_devs = dv.sdl_devices()
    wii = _devices_wiimotes({"force": bool(params.get("force"))})
    wm = wii.get("count", 0)

    # controllers: SDL order with the evdev twin's identity merged in
    by_sdl = {}
    for d in dv.joypads(devs):
        try:
            idx = dv.sdl_index_of(d, devs, sdl_devs)
        except Exception:
            idx = None
        if idx is not None and idx not in by_sdl:
            by_sdl[idx] = d
    controllers = []
    for s in sdl_devs:
        ent = {"index": s.index, "name": s.name,
               "vidpid": getattr(s, "vidpid", ""), "guid": getattr(s, "guid", "")}
        tw = by_sdl.get(s.index)
        if tw is not None:
            ent["evdev"] = ser_device(tw, xport)
            ent["label"] = ent["evdev"]["label"]
            if "battery" in ent["evdev"]:
                ent["battery"] = ent["evdev"]["battery"]
        else:
            vid = int(ent["vidpid"].split(":")[0], 16) if ent["vidpid"] else 0
            ent["label"] = pad_label(vid, ent["vidpid"], s.name, "", xport)
        controllers.append(ent)

    routes = []
    for it in _items(merged):
        r = dict(it)
        r["route"] = _route_one(it["key"], it["kind"], merged, policy, xport,
                                devs, sdl_devs, wm)
        routes.append(r)
    return {"xport": xport, "controllers": controllers, "wiimotes": wii,
            "routes": routes}
