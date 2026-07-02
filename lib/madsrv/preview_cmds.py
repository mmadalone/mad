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
from ..pad_labels import pad_label
from ..policy import load_merged
from ..routing import (load_policy, resolve_pins, resolve_policy, resolve_ports,
                       reserve_value, xarcade_port)
from ..standalone_preview import standalone_profile_preview
from .device_cmds import _devices_wiimotes, evdev_by_sdl_index, ser_device
from .rpc import method

_UNSET = object()   # "argument not provided" sentinel (None is a valid mouse-index value)


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


def _row_icon_name(row: dict) -> str:
    """Name to resolve a row's icon from. Normally the device hint in "icon"
    (else the label "text"). BUT a label naming the X-Arcade wins over the hint:
    Eden/Cemu rows carry a device hint of "Xbox 360" (the X-Arcade shares
    045e:02a1), so a user-named "X-Arcade P7" / "WiiU X-Arcade P6" profile would
    otherwise show the Xbox icon. The label is the reliable X-Arcade signal, so
    an "X-Arcade" label keeps the X-Arcade icon."""
    text = row.get("text") or ""
    if "x-arcade" in text.lower():
        return text
    return row.get("icon") or text


def _route_one(key: str, kind: str, merged: dict, policy: dict, xport: str,
               devs, sdl_devs, wm: int, sinden_idx=_UNSET) -> dict:
    ent = (merged.get("systems", {}).get(key)
           or merged.get("collections", {}).get(key) or {})
    be = ent.get("backend")
    if be == "pcsx2":
        # PS2 — PCSX2 binds by SDL *index* with no stable device identity, and the index in
        # PCSX2.ini is PCSX2's own emulog-calibrated numbering, which does NOT match MAD's live
        # SDL enumeration (resolving it always showed "no PlayStation pad"). Preview the router's
        # real would-bind pads instead: exactly what controller-router.py binds at launch — the
        # ordered, managed_players-capped list, honoring the pads->players page. quiet=True so this
        # read-only preview doesn't append phantom bind lines to router.log.
        from .. import switch_bind
        try:
            chosen = switch_bind._resolve_pads("pcsx2", quiet=True)
        except Exception:
            chosen = []
        if not chosen:
            return {"kind": "text", "text": "(no player pad connected)"}
        by_sdl = evdev_by_sdl_index(devs, sdl_devs)   # recover each SDL pad's USB port
        rows = []
        for i, d in enumerate(chosen):
            vid = int(d.vidpid.split(":")[0], 16) if getattr(d, "vidpid", "") else 0
            tw = by_sdl.get(d.index)                  # port lets pad_label name the X-Arcade
            port = dv.port_of(tw.phys) if tw is not None else ""
            rows.append({"slot": f"P{i + 1}",
                         "text": pad_label(vid, d.vidpid, d.name, port, xport)})
        return {"kind": "pads", "rows": rows}
    if be in ("cemu", "eden", "rpcs3"):
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
        # (be=="cemu" already returned at the top of _route_one, so no cemu branch here)
        # The "x-arcade" token (Backends X-Arcade tile) matches the X-Arcade's
        # 045e:02a1 at the SDL level; expand it so the pad still routes. (pad_label
        # names the X-Arcade by USB port below, regardless of whether this tile was chosen.)
        eff, _seen = [], set()
        for c in classes:
            v = "045e:02a1" if c in ("x-arcade", "xarcade") else c
            if v not in _seen:
                _seen.add(v)
                eff.append(v)
        prio = {c: i for i, c in enumerate(eff)}
        ps = sorted((d for d in sdl_devs if getattr(d, "vidpid", "") in prio),
                    key=lambda d: (prio[d.vidpid], d.index))
        if not ps:
            hh = bcfg.get("handheld_class") or bcfg.get("handheld_profile")
            return {"kind": "text",
                    "text": f"(no player pad → {('handheld: ' + str(hh)) if hh else 'unchanged'})"}
        by_sdl = evdev_by_sdl_index(devs, sdl_devs)
        rows = []
        for i, d in enumerate(ps[:4]):
            vid = int(d.vidpid.split(":")[0], 16) if getattr(d, "vidpid", "") else 0
            tw = by_sdl.get(d.index)
            # Always pass the real USB port so pad_label names the identified X-Arcade
            # "X-Arcade" (not "Xbox 360") in every section; a real Xbox 360 pad at a
            # different port still reads "Xbox 360" (pad_label only matches port == xport).
            port = dv.port_of(tw.phys) if tw is not None else ""
            rows.append({"slot": f"P{i + 1}",
                         "text": pad_label(vid, d.vidpid, d.name, port, xport)})
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
    # Mouse / lightgun visibility (Feature ④): surface the input_player*_mouse_index
    # the router will pin — so "which device drives the gun, or the RA red-button
    # hotkey" isn't a black box. Computed regardless of which menu pads are connected.
    extra = []
    if sys_entry.get("require_sinden"):
        # mouse-index lookups each open EVERY /dev/input/event* (~1s); _preview_all
        # computes them ONCE and passes them in, so the routes loop doesn't re-walk
        # per item (that per-route walk was the "Resolving routes…/backend timed out").
        p1, p2, smoothed = (dv.detect_sinden_mouse_indices(devs)
                            if sinden_idx is _UNSET else sinden_idx)
        src = "smoothed" if smoothed else "raw"
        if p1 is not None:
            extra.append({"slot": "Gun 1", "text": f"Sinden P1 — RA mouse {p1} ({src})"})
        if p2 is not None:
            extra.append({"slot": "Gun 2", "text": f"Sinden P2 — RA mouse {p2} ({src})"})
    if not port_devs:
        if extra:
            return {"kind": "pads", "rows": extra}
        return {"kind": "text", "text": "(no matching pad connected)"}
    rows = []
    for p in sorted(port_devs):
        d = port_devs[p]
        rows.append({"slot": f"P{p}",
                     "text": pad_label(d.vid, f"{d.vid:04x}:{d.pid:04x}", d.name,
                                       dv.port_of(d.phys), xport),
                     "pinned": p in pinned,
                     "reserve": reserve_value(d)})
    rows += extra
    return {"kind": "pads", "rows": rows}


def _controllers_evdev(devs, xport):
    """Connected-controllers list from EVDEV only (no SDL) — the fast first
    Preview render. Same per-row shape as _preview_all's controllers but with
    index = -1 (SDL order unknown until preview.all): label/battery/port/icon,
    Steam-virtual pads collapsed to one row. The wii-nav bridge is filtered by
    dv.joypads() (it only shows in the SDL-ordered list), so it isn't here — it
    appears when preview.all lands."""
    from .systems_cmds import device_icon_path
    out = []
    seen_virtual = False
    for d in dv.joypads(devs):
        vidpid = f"{d.vid:04x}:{d.pid:04x}"
        if vidpid == "28de:11ff":
            if seen_virtual:
                continue
            seen_virtual = True
        ev = ser_device(d, xport)
        ent = {"index": -1, "name": d.name, "vidpid": vidpid, "guid": "",
               "evdev": ev, "label": ev["label"],
               "icon": device_icon_path(ev["label"], vidpid)}
        if "battery" in ev:
            ent["battery"] = ev["battery"]
        out.append(ent)
    return out


@method("preview.devices", cache=("config", "devices"))
def _preview_devices(params):
    """FAST connected-controllers list (evdev only, no SDL init) for the first
    Preview render — the SDL-ordered list + per-system routes follow via the
    slow preview.all. Returns in ms; cached on the config/device revisions."""
    policy = load_policy()
    xport = xarcade_port(policy)
    devs = dv.enumerate_devices()
    return {"xport": xport, "controllers": _controllers_evdev(devs, xport)}


@method("preview.all", slow=True, cache=("config", "devices"))
def _preview_all(params):
    """One response feeding the whole Preview page: connected controllers (SDL
    order, evdev-joined: label/battery/port), DolphinBar status, X-Arcade port,
    and the would-route result for every routed system/collection."""
    merged = load_merged()
    policy = load_policy()
    xport = xarcade_port(policy)
    devs = dv.enumerate_devices()
    # slow=True page AND the DEFAULT landing page (opened the instant the backend
    # starts) → pump=True waits out the cold SDL warm-up so the connected list is
    # real on first open. pump=False raced the ~6s warm-up and returned the empty
    # _SDL_CACHE → "(none detected)" overwrote the evdev flash. Mirrors pads.get.
    sdl_devs = dv.sdl_devices(pump=True)
    wii = _devices_wiimotes({"force": bool(params.get("force"))})
    wm = wii.get("count", 0)

    # controllers: SDL order with the evdev twin's identity merged in
    by_sdl = evdev_by_sdl_index(devs, sdl_devs)
    controllers = []
    seen_virtual = False
    for s in sdl_devs:
        # MAD's own virtual nav bridge (4d41:0001 "MAD Wii Nav") is created the
        # whole time the panel is open (wii-nav-bridge.py), so it's ALWAYS an SDL
        # joystick — but it's not a controller the user connected. Listing it as
        # "Wii Remote (nav)" read as "a wiimote is connected" even with no
        # DolphinBar. Skip it (matches the evdev-only preview.devices, which
        # joypads() already filters it from); real Wii Remotes are reported by
        # the DolphinBar status line below.
        if getattr(s, "vidpid", "") == "4d41:0001" or s.name == "MAD Wii Nav":
            continue
        # Collapse ALL Steam-virtual pads (28de:11ff) to ONE row — switching a
        # controller's mode can spawn extra 11ff ghosts (same fix as the Tk
        # Preview; the backend is now the single source of truth for it).
        if getattr(s, "vidpid", "") == "28de:11ff":
            if seen_virtual:
                continue
            seen_virtual = True
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
        from .systems_cmds import device_icon_path
        ent["icon"] = device_icon_path(ent["label"], ent["vidpid"])
        controllers.append(ent)

    from .systems_cmds import console_art, device_icon_path
    # The Sinden mouse-index lookup opens every /dev/input/event* (~1s). Compute it ONCE
    # here and pass into _route_one so N routes don't trigger N walks (that per-route walk
    # made preview.all exceed the RPC timeout once a mouse device was bound).
    sinden_idx = dv.detect_sinden_mouse_indices(devs)
    routes = []
    for it in _items(merged):
        r = dict(it)
        r["art"] = console_art(it["key"]) if it.get("art") else None
        r["route"] = _route_one(it["key"], it["kind"], merged, policy, xport,
                                devs, sdl_devs, wm, sinden_idx)
        for row in r["route"].get("rows", []) or []:
            row["icon_path"] = device_icon_path(_row_icon_name(row))
        routes.append(r)
    wii["icon"] = device_icon_path("dolphinbar", fallback="")
    return {"xport": xport, "controllers": controllers, "wiimotes": wii,
            "routes": routes}
