"""preview.* methods — the would-route preview, now running the router's REAL
resolution pipeline (lib.routing) for RetroArch systems/collections.

This intentionally ends the old Tk-GUI divergence: _preview_route re-implemented
port resolution over SDL devices with class-token heuristics and IGNORED pins,
the fallback rescue, and X-Arcade port identity. The daemon previews exactly
what controller-router.py will do at launch (read-only — nothing is written).
Standalone backends preview the LIVE launch decision the same way: the exact
launch-path planners, called read-only (switch_bind._resolve_pads for
pcsx2/eden/rpcs3, cemu_seat._seat_plan / cemu_input_dock._gate for cemu,
dolphin_gc_dock.plan for gc). Only a HANDS-OFF backend keeps the stored
config-file preview (lib.standalone_preview) — hands-off means the emulator's
own config IS the launch truth. dolphin keeps the DolphinBar status text.
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


def _handheld() -> bool:
    """True when the on-the-go feature is enabled AND the Deck is physically handheld.

    Reuses switch_bind._launch_handheld -- the SAME gate the standalone launch path applies -- so
    the preview cannot disagree with the launch about which context it is in. (The predicate itself
    is duplicated across ~16 modules; unifying that is its own job. Do not add a 17th copy here.)
    Fail-safe: any error -> False (docked), so a detection glitch can never invent a handheld claim.
    """
    try:
        from .. import switch_bind
        return switch_bind._launch_handheld()
    except Exception:
        return False


def _ra_joypad_driver(policy: dict, handheld: bool) -> str:
    """The joypad driver the NEXT RetroArch launch will use, from the one shared decision
    (retroarch_cfg.planned_joypad_driver) the router acts on. It decides what a bind NUMBER means
    (udev = per-device evdev ranks; sdl2 = SDL GameController semantic indices), so it is the single
    most load-bearing fact about a RetroArch route and the page should say it out loud."""
    try:
        from .. import retroarch_cfg
        return retroarch_cfg.planned_joypad_driver(policy, handheld)
    except Exception:
        return ""


def _handheld_pad_label(hh: str, xport: str) -> str:
    """Name a backend's handheld_class ("28de:1205") or handheld_profile ("Steamdeck") for display.
    A vid:pid goes through pad_label like every other pad; anything else is already a profile name."""
    parts = hh.split(":")
    if len(parts) == 2 and all(len(p) == 4 for p in parts):
        try:
            return pad_label(int(parts[0], 16), hh, "", "", xport)
        except Exception:
            return hh
    return hh


def _esde_systems() -> set:
    """Systems ES-DE actually shows: a gamelist with >=1 VISIBLE game.

    The file's mere existence is NOT the signal — an emptied `<gameList/>` stub
    (e.g. an xbox system whose ROMs are gone but a leftover gamelist.xml remains)
    is hidden by ES-DE yet still had a file, so it leaked into the would-route
    preview. Mirror es_gamelist.visible_records (which drops `<hidden>` games and
    honours ES-DE's 'Show hidden games' setting) — the same visible-game gate
    quit_combo_systems uses to drop those stubs."""
    from ..esde_settings import APPDATA
    from .. import es_gamelist
    gl = APPDATA / "gamelists"
    if not gl.is_dir():
        return set()
    return {d.name for d in gl.iterdir()
            if d.is_dir() and (d / "gamelist.xml").is_file()
            and es_gamelist.visible_records(d.name)}


def _items(merged: dict) -> list[dict]:
    """The routed-things list, mirroring the Tk Preview page composition:
    standalone-backend systems (with games) + Priority-configured RetroArch
    systems + configured collections.

    NO `art` field: art is resolved from `key` by the caller, for EVERY item.
    It used to carry one — set to the system name for a system and to None for a
    collection — and the caller then gated the lookup on its truthiness, so
    console_art() was never called for a collection and every collection rendered
    text-only. The field was a value and a boolean at once; that dual role WAS the
    bug (the "▣ " label prefix was the placeholder standing in for the missing
    icon). `lightgun` picks the gun fallback, mirroring priority.list.
    """
    esde = _esde_systems()
    sysxml = es_systems.load_systems()
    items, seen = [], set()
    for sysname in backend_systems(merged):
        if esde and sysname not in esde:          # configured but no games (xbox, model3…)
            continue
        if sysname not in seen:
            seen.add(sysname)
            items.append({"key": sysname, "label": sysname, "kind": "system"})
    for s in sorted(merged.get("systems", {})):
        ent = merged["systems"][s]
        if not (isinstance(ent, dict) and ent.get("ports")) or s in seen:
            continue
        if esde and s not in esde:                # ports configured but no games (naomi2…)
            continue
        if es_systems.is_standalone(es_systems.default_command(s, sysxml)):
            continue                              # standalone ones came from backend_systems
        seen.add(s)
        items.append({"key": s, "label": s, "kind": "system"})
    cfg_c = merged.get("collections", {})
    for c in es_collections.enabled_collections():
        ent = cfg_c.get(c)
        if isinstance(ent, dict) and ent.get("ports") and c not in seen:
            seen.add(c)
            items.append({"key": c, "label": c, "kind": "collection",
                          "lightgun": bool(ent.get("require_sinden"))})
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


def _bound_pad_rows(be: str, devs, sdl_devs, xport: str) -> list[dict]:
    """The router's REAL would-bind pads for a switch_bind-managed backend
    (pcsx2/eden/rpcs3): exactly what controller-router.py binds at launch — the
    ordered, managed_players-capped list, honoring the pads->players page and the
    handheld Deck fallback (no external pad + handheld → _resolve_pads re-admits
    the Deck virtual pad, so the preview shows the Deck as P1, same as the launch).
    quiet=True so this read-only preview doesn't append phantom bind lines to
    router.log. Each row carries the pad's vidpid so the icon resolver keys on the
    device identity (28de:11ff → the Steam Deck icon), not just the label text."""
    from .. import switch_bind
    try:
        chosen = switch_bind._resolve_pads(be, quiet=True)
    except Exception:
        chosen = []
    by_sdl = evdev_by_sdl_index(devs, sdl_devs)   # recover each SDL pad's USB port
    rows = []
    for i, d in enumerate(chosen):
        vid = int(d.vidpid.split(":")[0], 16) if getattr(d, "vidpid", "") else 0
        tw = by_sdl.get(d.index)                  # port lets pad_label name the X-Arcade
        port = dv.port_of(tw.phys) if tw is not None else ""
        rows.append({"slot": f"P{i + 1}",
                     "text": pad_label(vid, d.vidpid, d.name, port, xport),
                     "vidpid": getattr(d, "vidpid", "")})
    return rows


def _route_one(key: str, kind: str, merged: dict, policy: dict, xport: str,
               devs, sdl_devs, wm: int, sinden_idx=_UNSET) -> dict:
    ent = (merged.get("systems", {}).get(key)
           or merged.get("collections", {}).get(key) or {})
    be = ent.get("backend")
    if be == "pcsx2":
        # PS2 — PCSX2 binds by SDL *index* with no stable device identity, and the index in
        # PCSX2.ini is PCSX2's own emulog-calibrated numbering, which does NOT match MAD's live
        # SDL enumeration (resolving it always showed "no PlayStation pad"). Preview the router's
        # real would-bind pads instead (shared _bound_pad_rows).
        rows = _bound_pad_rows("pcsx2", devs, sdl_devs, xport)
        if not rows:
            return {"kind": "text", "text": "(no player pad connected)"}
        return {"kind": "pads", "rows": rows}
    if be in ("eden", "rpcs3"):
        # LIVE TRUTH: mirror switch_bind.bind() gate-for-gate instead of parsing the
        # emulator's stored config. The stored file shows the LAST bind's seats (e.g. a
        # long-disconnected DualSense) — bind() rewrites it from the LIVE pads at every
        # launch, so parsing it previews the past, not the next launch. Gates in bind()'s
        # order: hands-off keeps the stored-config preview (the emu's own config IS the
        # launch truth then); a missing config file is left untouched at launch; otherwise
        # the router's would-bind pads (handheld Deck fallback included).
        from . import pads_cmds
        from .. import switch_bind
        if pads_cmds._hands_off(be):
            k, data = standalone_profile_preview(be, merged, sdl_devs)
            return ({"kind": "text", "text": data} if k == "text"
                    else {"kind": "pads", "rows": _rows(data)})
        try:
            target_ok = switch_bind._target(be, "").is_file()
        except Exception:
            target_ok = False
        if not target_ok:
            return {"kind": "text", "text": "(no config file → input left untouched)"}
        rows = _bound_pad_rows(be, devs, sdl_devs, xport)
        if not rows:
            return {"kind": "text", "text": "(no player pad → input left untouched)"}
        return {"kind": "pads", "rows": rows}
    if be == "cemu":
        # LIVE TRUTH: mirror the game-start hook's decision (cemu_seat.apply) READ-ONLY
        # instead of parsing controller{0..7}.xml. Those are the RESTING profiles — they
        # show seats from games past (a disconnected pad stays in the file) while the hook
        # re-seats every launch from the live pads. _seat_plan is the hook's own pure
        # planner; apply() is never called here (it writes).
        from pathlib import Path as _Path

        from .. import cemu_seat, handheld_input
        bcfg = cemu_seat._cemu_cfg(merged)
        if cemu_seat._dget(bcfg, "seating_enabled", False):
            try:
                context = handheld_input.context()
                cfg_dir = cemu_seat._config_dir(bcfg)
                plan, _owned = cemu_seat._seat_plan(merged, bcfg, context, devs)
                # apply() SKIPS a planned slot whose profile file is unreadable, and does
                # nothing at all when the config dir is missing. Mirror both gates or the
                # page would promise a seat the launch provably will not make.
                if not cfg_dir.is_dir():
                    return {"kind": "text",
                            "text": f"(Cemu config dir {cfg_dir} not found -> no seat)"}
                plan = [p for p in plan
                        if (cfg_dir / f"{p[1]}.xml").is_file()]
            except Exception:
                return {"kind": "text", "text": "(cemu seating unavailable)"}
            if not plan:
                # Exactly what apply() reports: no profile assigned for this context's
                # families → every slot stays resting. Configured-but-disconnected pads
                # deliberately do NOT show here (live truth only).
                return {"kind": "text",
                        "text": f"(nothing assigned for {context} → Cemu resting input untouched)"}
            rows = []
            # Sort by SLOT only: the tuples carry a devices.Device (no ordering defined),
            # so sorting whole tuples raises TypeError on a (slot, stem) tie and would
            # take down the entire preview.all response, not just this row.
            for slot0, stem, dev, _gp in sorted(plan, key=lambda t: t[0]):
                row = {"slot": f"C{slot0 + 1}", "text": stem}
                if dev is None:
                    row["vidpid"] = "28de:11ff"       # the Deck GamePad slot
                else:
                    row["vidpid"] = f"{dev.vid:04x}:{dev.pid:04x}"
                    row["icon"] = pad_label(dev.vid, row["vidpid"], dev.name,
                                            dv.port_of(dev.phys), xport)
                rows.append(row)
            return {"kind": "pads", "rows": rows}
        # Seating disabled = the legacy single-slot handheld swap (cemu_input_dock).
        # _gate is its pure decision half: a profile Path means the swap WILL happen.
        from .. import cemu_input_dock
        try:
            prof, why = cemu_input_dock._gate(bcfg)
        except Exception:
            prof, why = None, "gate unavailable"
        if prof is not None:
            return {"kind": "pads",
                    "rows": [{"slot": "C1", "text": prof.stem, "vidpid": "28de:11ff"}]}
        return {"kind": "text", "text": f"(Cemu resting input untouched → {why})"}
    if be == "dolphin_gc":
        # GameCube routes by Dolphin PROFILE, not by pad family, and it is dock-aware. Ask the
        # router for its decision instead of re-deriving one: dolphin_gc_dock.plan() is the same
        # call dolphin_gc_dock.apply() acts on at launch. Read-only, writes nothing.
        # This branch MUST sit above the generic `be and be != "retroarch"` fallthrough below:
        # that one resolves pads from backends[be]["pad_classes"], a key dolphin_gc does not have,
        # so gc matched nothing and rendered "(no player pad -> unchanged)" for every fleet. Do NOT
        # "fix" that by giving dolphin_gc a pad_classes list — it would produce an answer, and the
        # answer would be wrong: the real router matches profiles to pads by vid:pid resolved from
        # each profile's Device name and honors hands-off, which a flat vid:pid list cannot express.
        from .. import dolphin_gc_dock
        try:
            p = dolphin_gc_dock.plan()
        except Exception:
            return {"kind": "text", "text": "(gc routing unavailable)"}
        if not p["assign"]:
            return {"kind": "text", "text": f"({p['mode']} -> {p['note']})"}
        # Row text = the profile NAME (the user-facing answer: which layout lands on P1).
        # Row icon = a pad_label device HINT, exactly like the cemu/eden rows -- NOT Dolphin's raw
        # Device string. Dolphin's Device names are SDL/evdev names ("PS4 Controller",
        # "Nintendo Wii Remote Pro Controller") and device_icon_path resolves art from the LABEL
        # vocabulary, so those fell through to genericgamepad; "DualSense Wireless Controller"
        # only worked by luck (its first word happens to match dualsense.png). Resolving the
        # profile's Device -> vid:pid -> pad_label gives the names the art is actually keyed on
        # (054c:09cc -> "DualShock 4" -> dualshock.png). _row_icon_name still lets an "X-Arcade"
        # profile NAME beat this hint, which is right: 045e:02a1 is shared with a real Xbox pad.
        from .. import dolphin_gc_pads, dolphin_profiles
        try:
            _pool, name_to_vp = dolphin_gc_pads._connected_index()
        except Exception:
            name_to_vp = {}
        rows = []
        handheld_mode = p.get("mode") == "handheld"
        for port, name in p["assign"]:
            row = {"slot": f"P{port}", "text": name}
            if handheld_mode:
                # Handheld: the seat IS the Deck's built-in pad. The profile's Device
                # string never matches the live evdev name inside Game Mode (the pad only
                # surfaces as the Steam virtual 28de:11ff), so the name-join below always
                # missed and the row rendered iconless — assert the identity instead.
                row["vidpid"] = "28de:11ff"
            else:
                vp = name_to_vp.get(dolphin_profiles.profile_device(name) or "")
                if vp:
                    row["vidpid"] = vp
                    try:
                        row["icon"] = pad_label(int(vp.split(":")[0], 16), vp, "", "", xport)
                    except Exception:
                        pass
            rows.append(row)
        return {"kind": "pads", "rows": rows}
    if be == "dolphin":
        if not dv.dolphinbar_present():
            # No bar: the profile ladder covers emulated play — render the decider's own
            # rom-less plan (dolphin_wii_source.plan, same do-not-re-derive contract as gc).
            try:
                from lib import dolphin_wii_source
                p = dolphin_wii_source.plan()
                extras = [f"{k} → {v}" for k, v in p["styles"].items() if v]
                txt = (f"no DolphinBar — {p['mode']}: Classic → {p['cc']}"
                       + (("; " + "; ".join(extras)) if extras else ""))
                return {"kind": "text", "text": "⚠ " + txt}
            except Exception:
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
            # This used to assert "handheld: <raw vid:pid>" whenever NO pad matched -- with no dock
            # gate at all, so DOCKED it claimed a handheld fallback that was not going to happen
            # (live: xbox -> "(no player pad -> handheld: 28de:1205)" while is_docked() was True),
            # and it printed a bare vid:pid though pad_label was already imported. Gate on the real
            # context and name the pad.
            hh = bcfg.get("handheld_class") or bcfg.get("handheld_profile")
            if hh and _handheld():
                # HANDHELD: the fallback IS the seat, so render it as the P1 pad row it
                # effectively is (with the pad icon), not an explanatory text line - the
                # old "(no player pad -> handheld: ...)" read as noise next to real pad
                # rows (user request 2026-07-30). A profile-name fallback still seats
                # the Deck's built-in pad, hence the 28de:11ff identity for the icon.
                hhs = str(hh)
                parts = hhs.split(":")
                vp = hhs if len(parts) == 2 and all(len(x) == 4 for x in parts) else "28de:11ff"
                return {"kind": "pads",
                        "rows": [{"slot": "P1", "text": _handheld_pad_label(hhs, xport),
                                  "vidpid": vp}]}
            return {"kind": "text", "text": "(no player pad → unchanged)"}
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
                         "text": pad_label(vid, d.vidpid, d.name, port, xport),
                         "vidpid": getattr(d, "vidpid", "")})
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
        if _handheld():
            # The Deck pad IS Player 1 here, so say exactly that as a pad row. Mechanism
            # (why resolve_ports returned {}): the Steam-virtual pad 28de:11ff is the only
            # form the Deck's controls take in Game Mode and resolve_ports excludes it, so
            # the router writes NO reservation and RetroArch seats the Deck through its own
            # enumeration - the OUTCOME is P1 = Deck every time, and the old explanatory
            # text line read as noise next to real pad rows (user request 2026-07-30; the
            # driver/not-reserved internals stay visible in router.log).
            return {"kind": "pads",
                    "rows": [{"slot": "P1",
                              "text": pad_label(0x28DE, "28de:11ff", "Steam Deck", "", xport),
                              "vidpid": "28de:11ff"}]}
        return {"kind": "text", "text": "(no matching pad connected)"}
    rows = []
    for p in sorted(port_devs):
        d = port_devs[p]
        rows.append({"slot": f"P{p}",
                     "text": pad_label(d.vid, f"{d.vid:04x}:{d.pid:04x}", d.name,
                                       dv.port_of(d.phys), xport),
                     "vidpid": f"{d.vid:04x}:{d.pid:04x}",
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
        if getattr(s, "vidpid", "").startswith("4d41:"):   # any MAD-made uinput pad
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

    from .systems_cmds import console_art, device_icon_path, resolve_art
    # Art for EVERY item, systems and collections alike, with the same fallback chain
    # priority.list uses. Resolved unconditionally: the old `if it.get("art")` gate was a
    # truthiness test on a field _items set to None for collections, so console_art() was
    # never called for one. A fallback alone would not have fixed it (the gate short-circuits
    # first), and the gate alone would work here only by luck — every collection happens to
    # have a theme dir today, and console_art tries just <name> and <name>.lower(), so a
    # future name with no matching dir needs the fallback. Systems get it too: one whose
    # theme dir lacks console.png rendered text-only before, by the same mechanism.
    fallback_pad = resolve_art(["icons/controllers.png", "controllers.png"])
    fallback_gun = resolve_art(["icons/lightgun.png", "lightgun.png",
                                "icons/sinden.png", "sinden.png"])
    # The Sinden mouse-index lookup opens every /dev/input/event* (~1s). Compute it ONCE
    # here and pass into _route_one so N routes don't trigger N walks (that per-route walk
    # made preview.all exceed the RPC timeout once a mouse device was bound).
    sinden_idx = dv.detect_sinden_mouse_indices(devs)
    routes = []
    for it in _items(merged):
        r = dict(it)
        r["art"] = (console_art(it["key"])
                    or (fallback_gun if it.get("lightgun") else fallback_pad))
        r["route"] = _route_one(it["key"], it["kind"], merged, policy, xport,
                                devs, sdl_devs, wm, sinden_idx)
        for row in r["route"].get("rows", []) or []:
            # vidpid (when the branch had the device in hand) lets _VIDPID_ICON and
            # per-pid themed assets fire; the name alone missed the Steam Deck icon
            # entirely (28de:11ff labels as "Steam Deck (SI)", which matches no asset).
            row["icon_path"] = device_icon_path(_row_icon_name(row), row.get("vidpid", ""))
        routes.append(r)
    wii["icon"] = device_icon_path("dolphinbar", fallback="")
    # Context the whole page is answering FOR. Without these the payload was byte-for-byte identical
    # docked vs handheld (proven: the same 43 routes under MAD_FORCE_CONTEXT=handheld), i.e. the page
    # silently reported one context while the Deck was in the other. `driver` is the planned one, not
    # the resting cfg value -- see retroarch_cfg.planned_joypad_driver.
    handheld = _handheld()
    return {"xport": xport, "controllers": controllers, "wiimotes": wii,
            "routes": routes, "handheld": handheld,
            "joypad_driver": _ra_joypad_driver(policy, handheld)}
