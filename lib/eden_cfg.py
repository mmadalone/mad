"""
Eden (Nintendo Switch, a Yuzu fork) controller-assignment backend.

Eden binds each player to a controller by an **SDL GUID + port**, embedded in
every per-button binding string inside `[Controls]` of `qt-config.ini`:

    player_0_button_a="button:1,guid:<GUID>,port:<k>,engine:sdl"
    ...

`guid` selects the device class; `port` (0,1,…) disambiguates identical pads.
Eden stores the **no-CRC SDL GUID form** (`0300 0000 <vidLE> 0000 <pidLE> 0000
0100 0000` — verified on this Deck), which differs from the CRC-bearing GUID
`sdl_devices()` returns, so we construct it from vid:pid here.

Button mappings are SDL-standard, so one device-agnostic template (the existing
`input/Deck P1 Pro Controller.ini`) is retargeted per player by swapping the
guid+port and prefixing each key with `player_N_`. Unused players are marked
disconnected. Eden rewrites qt-config.ini on exit (Qt QSettings), so we edit
while it's closed (ES-DE game-start) and keep a one-time backup. All paths /
classes come from `[backends.eden]`.

NOTE: the exact GUID Eden records can vary with its SDL build (bus/version
bytes). The no-CRC construction matches the observed on-disk format; if a live
Switch launch shows a pad not binding, capture Eden's recorded guid and adjust
`_eden_guid()` (one-line). The bindings are written deterministically regardless.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import inifile
from . import fsutil
from .devices import sdl_devices


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def _swap16(v: int) -> str:
    """0x054c -> '4c05' (little-endian 16-bit hex)."""
    return f"{v & 0xFF:02x}{(v >> 8) & 0xFF:02x}"


def _eden_guid(vidpid: str) -> str:
    """Eden's no-CRC SDL GUID for a 'vid:pid' class — USB-bus fallback used only
    when the device's real SDL GUID isn't available. Prefer `_eden_guid_sdl`."""
    vid, pid = (int(x, 16) for x in vidpid.split(":"))
    return "0300" + "0000" + _swap16(vid) + "0000" + _swap16(pid) + "0000" + "0100" + "0000"


def _eden_guid_sdl(sdl_guid: str) -> str:
    """Eden's GUID for a specific device = its REAL SDL GUID with the name-CRC
    (bytes 2-3) zeroed. This is what Eden itself records — crucially it preserves
    the real BUS byte (03 = USB, 05 = Bluetooth), so a Bluetooth pad like the Wii U
    Pro (`0500…`) matches, where the vid:pid form's hardcoded `0300…` does not.
    Returns '' on a malformed GUID so the caller can fall back to `_eden_guid`."""
    if not sdl_guid or len(sdl_guid) != 32:
        return ""
    return sdl_guid[:4] + "0000" + sdl_guid[8:]   # zero bytes 2-3 (the name-CRC)


def _retarget(value: str, guid: str, port: int) -> str:
    """Rewrite a binding string's guid:/port: to the chosen device."""
    if "guid:" not in value:
        return value   # keyboard / [empty] bindings left alone
    value = re.sub(r"guid:[0-9a-fA-F]+", f"guid:{guid}", value)
    value = re.sub(r"port:\d+", f"port:{port}", value)
    return value


def _template_bindings(profile: Path) -> dict[str, str]:
    """Parse `key=value` (non-`\\default`) lines from a profile's [Controls]."""
    out: dict[str, str] = {}
    body = inifile.section_body(profile.read_text(encoding="utf-8"), "Controls") or ""
    for ln in body.splitlines():
        if "=" not in ln or "\\default" in ln:
            continue
        k, v = ln.split("=", 1)
        k = k.strip()
        if k and k != "type":
            out[k] = v
    return out


def _apply_player(body: str, n: int, overrides: dict[str, str]) -> str:
    """Set every `player_{n}_<key>` (and its `\\default=false`) in the [Controls]
    body text, replacing existing lines or appending."""
    lines = body.splitlines()
    want = {f"player_{n}_{k}": v for k, v in overrides.items()}
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln else ""
        base = key[:-len("\\default")] if key.endswith("\\default") else key
        if base in want:
            if key.endswith("\\default"):
                out.append(f"{base}\\default=false")
            else:
                out.append(f"{base}={want[base]}")
                seen.add(base)
            continue
        out.append(ln)
    for key, val in want.items():
        if key not in seen:
            out.append(f"{key}\\default=false")
            out.append(f"{key}={val}")
    return "\n".join(out)


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the Switch pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` let a GLOBAL pin set a player's
    (guid, port-within-class) to the pinned pad. (Switch is usually router_skip,
    so this rarely runs — included for parity.)"""
    ini = _expand(cfg.get("config_file", "~/.config/eden/qt-config.ini"))
    template = _expand(cfg.get("template_profile",
                               "~/.config/eden/input/Deck P1 Pro Controller.ini"))
    manage = int(cfg.get("manage_players", 2))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld = cfg.get("handheld_class", "")

    if not ini.is_file():
        logger.warning(f"eden: config {ini} not found; skipping")
        return 0
    if not template.is_file():
        logger.warning(f"eden: template profile {template} not found; skipping")
        return 0

    sdl = sdl_devices()
    prio = {c: i for i, c in enumerate(pad_classes)}
    ps = sorted((d for d in sdl if d.vidpid in prio),
                key=lambda d: (prio[d.vidpid], d.index))

    # Global pins -> (vidpid, port-within-class) for the pinned pad (eden players
    # are 0-based; the port is the pad's rank among same-class devices).
    pinned: dict[int, tuple[str, int]] = {}
    if pins and devs:
        from .devices import vidpid as _vp, class_index as _ci
        for player, pdev in pins.items():
            n = player - 1
            if 0 <= n < manage:
                pinned[n] = (_vp(pdev), _ci(devs, pdev))

    # player (0-based) -> (vidpid, port-within-class)
    assigned: dict[int, tuple[str, int]] = {}
    if ps:
        seen_class: dict[str, int] = {}
        for k in range(manage):
            if k < len(ps):
                d = ps[k]
                port = seen_class.get(d.vidpid, 0)
                seen_class[d.vidpid] = port + 1
                assigned[k] = (d.vidpid, port)
        logger.info("eden: players -> " + ", ".join(
            f"P{k+1}={vp}#{pt}" for k, (vp, pt) in assigned.items()))
    elif not pinned:
        deck = next((d for d in sdl if d.vidpid == handheld), None)
        if not handheld or deck is None:
            logger.info("eden: no PlayStation pad and no handheld; leaving qt-config.ini")
            return 0
        assigned[0] = (deck.vidpid, 0)
        logger.info(f"eden: no PlayStation pad -> P1={deck.vidpid} (handheld)")

    # Pins win on their players; drop in-order assignments that collide.
    for n, vp in sorted(pinned.items()):
        assigned[n] = vp
    pinned_vals = set(pinned.values())
    for n in [m for m in assigned if m not in pinned and assigned[m] in pinned_vals]:
        del assigned[n]
    if pinned:
        logger.info("eden: pins -> " + ", ".join(
            f"P{n+1}={vp}#{pt}" for n, (vp, pt) in sorted(pinned.items())))

    tmpl = _template_bindings(template)
    text = ini.read_text(encoding="utf-8")
    body = inifile.section_body(text, "Controls") or ""

    for n in range(manage):
        if n in assigned:
            vidpid, port = assigned[n]
            guid = _eden_guid(vidpid)
            ov = {k: _retarget(v, guid, port) for k, v in tmpl.items()}
            ov["connected"] = "true"
            ov["type"] = "0"
            ov["profile_name"] = ""
            body = _apply_player(body, n, ov)
        else:
            body = _apply_player(body, n, {"connected": "false"})

    if fsutil.ensure_pristine_backup(ini):
        logger.info(f"eden: one-time backup -> {ini.name}.router-backup")

    text = inifile.set_section(text, "Controls", body)
    fsutil.atomic_write(ini, text)
    logger.info(f"eden: wrote {ini}")
    return 0


def _live_player_bindings(body: str, n: int) -> dict[str, str]:
    """Existing `player_{n}_<key>=value` (non-`\\default`) bindings from a
    [Controls] body, keyed WITHOUT the `player_n_` prefix — so an explicit device
    pick can retarget the user's LIVE remap (preserving each `button:M`) rather
    than overwrite it from the template. Drops the device/meta keys."""
    pref = f"player_{n}_"
    out: dict[str, str] = {}
    for ln in body.splitlines():
        if "=" not in ln or "\\default" in ln:
            continue
        k, v = ln.split("=", 1)
        k = k.strip()
        if k.startswith(pref):
            sub = k[len(pref):]
            if sub not in ("connected", "type", "profile_name"):
                out[sub] = v
    return out


def assign_devices(players, ini_path: str = "~/.config/eden/qt-config.ini",
                   template_path: str = "~/.config/eden/input/Deck P1 Pro Controller.ini",
                   manage: int = 2) -> dict:
    """Configure-once device pick (MAD 'pads → players'): set each
    `player_{N}` in qt-config.ini's [Controls] to ``players[N]`` (its no-CRC SDL
    `guid` + port-within-class), PRESERVING that player's existing per-button
    bindings (only `guid:`/`port:` are retargeted via `_retarget`). A player with
    no live bindings falls back to the device-agnostic template; players beyond
    the connected count are marked disconnected. ``players`` is a list of
    ``devices.SdlDevice``. Raises FileNotFoundError if the config is missing."""
    ini = _expand(ini_path)
    if not ini.is_file():
        raise FileNotFoundError("Eden config not found — launch an Eden game once")
    template = _expand(template_path)
    tmpl = _template_bindings(template) if template.is_file() else {}
    text = ini.read_text(encoding="utf-8")
    body = inifile.section_body(text, "Controls") or ""

    seen: dict[str, int] = {}
    assigned: list[tuple[object, str, int]] = []
    for d in players:
        port = seen.get(d.vidpid, 0)
        seen[d.vidpid] = port + 1
        assigned.append((d, d.vidpid, port))

    slots = max(manage, len(assigned))
    for n in range(slots):
        if n < len(assigned):
            _d, vidpid, port = assigned[n]
            guid = _eden_guid_sdl(getattr(_d, "guid", "")) or _eden_guid(vidpid)
            base = _live_player_bindings(body, n) or tmpl
            ov = {k: _retarget(v, guid, port) for k, v in base.items()}
            ov["connected"] = "true"
            ov["type"] = "0"
            ov["profile_name"] = ""
            body = _apply_player(body, n, ov)
        else:
            body = _apply_player(body, n, {"connected": "false"})

    fsutil.ensure_pristine_backup(ini)
    text = inifile.set_section(text, "Controls", body)
    fsutil.atomic_write(ini, text)
    return {"assigned": [(f"P{i + 1}", d.vidpid) for i, (d, _vp, _pt) in enumerate(assigned)]}
