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
from . import pad_assign
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

    # Slot (0-based player) -> (vidpid, port-within-class) via the shared
    # pipeline. eden disambiguates identical pads by an explicit port, so a
    # collision is value-membership (unit_count=1) over the full (vidpid, port)
    # tuple; pins are 0-based players, hence base_index=0.
    from .devices import vidpid as _vp, class_index as _ci
    assigned = pad_assign.assign_slots(
        sdl, manage, pins, devs,
        pad_classes=pad_classes, handheld=handheld,
        encode_auto=lambda d, rank: (d.vidpid, rank),
        encode_pin=lambda pdev, sdl_devs, evdevs: (_vp(pdev), _ci(evdevs, pdev)),
        rank_key=lambda d: d.vidpid, base_index=0,
    )
    if assigned is None:
        logger.info("eden: no PlayStation pad and no handheld; leaving qt-config.ini")
        return 0
    logger.info("eden: players -> " + (", ".join(
        f"P{n+1}={vp}#{pt}" for n, (vp, pt) in sorted(assigned.items())) or "(none)"))

    tmpl = _clean_block(_template_bindings(template))
    text = ini.read_text(encoding="utf-8")
    body = inifile.section_body(text, "Controls") or ""
    by_guid = _harvest_guid_bindings(body, template.parent)   # match each pad's own structure

    for n in range(manage):
        if n in assigned:
            vidpid, port = assigned[n]
            own = _live_player_bindings(body, n)
            own_guid = _block_guid(own)
            if own and _guid_to_vidpid(own_guid) == vidpid:
                src, guid = own, own_guid          # keep this slot's own binds + its real-bus guid
            else:
                src = _resolve_block(by_guid, _eden_guid(vidpid), vidpid, own or tmpl)
                bg = _block_guid(src)
                # prefer the resolved block's own guid (real bus byte) over the USB-bus
                # reconstruction, so a Bluetooth pad (05..) is not stamped with a 03.. guid Eden
                # would fail to match. Fall back to the reconstruction only for the template.
                guid = bg if bg and _guid_to_vidpid(bg) == vidpid else _eden_guid(vidpid)
            ov = {k: _retarget(v, guid, port) for k, v in src.items()}
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


# ── per-device binding structure (correct hat/button/axis per pad) ──────────────
# A binding's TOKEN STRUCTURE is device-specific: the Wii U Pro adapter reports the
# d-pad + ZL/ZR as plain SDL buttons (button:13..16, button:6/7); a DualSense / DS4 /
# Deck / Xbox reports the d-pad as a HAT (hat:0,direction:up) and ZL/ZR as analog AXES
# (axis:4/5,threshold). `_retarget` only swaps guid/port, so reusing a slot's leftover
# tokens for a DIFFERENT device breaks its d-pad + triggers (a DS has no button:13). We
# therefore give each pad the block that MATCHES its guid, sourced from the resting
# config + the input/*.ini template profiles.
_GUID_RE = re.compile(r"guid:([0-9a-fA-F]+)")
_META_KEYS = ("connected", "type", "profile_name")
_MAX_SCAN_SLOTS = 16       # resting player_0..player_N to harvest (8 pads + keyboard slots)


def _block_guid(binds: dict) -> str:
    """The no-CRC SDL guid (lowercased) a binding block belongs to, or '' if none (a
    keyboard / empty block)."""
    for v in binds.values():
        m = _GUID_RE.search(v)
        if m:
            return m.group(1).lower()
    return ""


def _guid_to_vidpid(g: str) -> str:
    """'vid:pid' decoded from a no-CRC SDL guid (vid at bytes 4-5, pid at 8-9, each
    little-endian), or '' if too short. Bus/version bytes are ignored, so a USB (03..)
    and a Bluetooth (05..) guid for the same model resolve to one vid:pid."""
    if len(g) < 20:
        return ""
    try:
        vid = int(g[10:12] + g[8:10], 16)
        pid = int(g[18:20] + g[16:18], 16)
    except ValueError:
        return ""
    return f"{vid:04x}:{pid:04x}"


def _clean_block(binds: dict) -> dict:
    """A binding block with the device/meta keys dropped (they are set per-slot)."""
    return {k: v for k, v in binds.items() if k not in _META_KEYS}


def _harvest_guid_bindings(body: str, input_dir: Path) -> dict:
    """{no_crc_guid: {key: value}} of CORRECT per-device binding blocks, so each pad can be
    given the layout that MATCHES it regardless of which slot it lands in. First-writer-wins
    per guid: the resting [Controls] player blocks (the ground truth configured in the
    emulator), then every input/*.ini template profile. Guid-less (keyboard) blocks skipped."""
    out: dict = {}
    for n in range(_MAX_SCAN_SLOTS):
        binds = _clean_block(_live_player_bindings(body, n))
        g = _block_guid(binds)
        if g and g not in out:
            out[g] = binds
    try:
        for tf in sorted(input_dir.glob("*.ini")):
            binds = _clean_block(_template_bindings(tf))
            g = _block_guid(binds)
            if g and g not in out:
                out[g] = binds
    except OSError:
        pass
    return out


def _modern_default(by_guid: dict):
    """A hat+axis prototype for a pad with no known-correct block = the first harvested
    block whose d-pad is a HAT (DualSense/DS4/Deck/Xbox-style). NEVER the Wii U Pro button
    layout (its button_dup is `button:13`, not `hat:`). None if no hat-style block exists."""
    for binds in by_guid.values():
        if "hat:" in binds.get("button_dup", ""):
            return binds
    return None


def _resolve_block(by_guid: dict, guid: str, vidpid: str, fallback):
    """The correct binding block for a device: exact no-CRC guid match, else a vid:pid match
    (a USB vs BT guid variant of the same model), else the modern hat+axis default, else
    `fallback` (the caller's live-slot / template block, i.e. today's behavior). The block's
    guid/port are retargeted to the device by the caller."""
    g = (guid or "").lower()
    if g in by_guid:
        return by_guid[g]
    if vidpid:
        for k, binds in by_guid.items():
            if _guid_to_vidpid(k) == vidpid:
                return binds
    return _modern_default(by_guid) or fallback


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
    tmpl = _clean_block(_template_bindings(template)) if template.is_file() else {}
    text = ini.read_text(encoding="utf-8")
    body = inifile.section_body(text, "Controls") or ""
    # Give each pad the block that MATCHES its guid (correct hat/button/axis structure),
    # not the slot's leftover tokens -- fixes a hat-d-pad pad (DS/DS4) that lands on a slot
    # last held by a button-d-pad pad (Wii U Pro) getting a dead `button:13` d-pad.
    by_guid = _harvest_guid_bindings(body, template.parent)

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
            own = _live_player_bindings(body, n)
            if own and _block_guid(own) == guid.lower():
                # Slot already holds THIS device -> keep its (maybe user-customised) binds. CAVEAT: a
                # block a PRE-FIX buggy launch left with this guid but a wrong STRUCTURE (dead
                # button:13 on a hat pad) is preserved, not self-healed -> reconfigure that pad once
                # in the emulator's own GUI (rewrites a clean block) if its d-pad stays dead.
                src = own
            else:                  # a DIFFERENT device (or empty) -> the block matching this pad
                src = _resolve_block(by_guid, guid, vidpid, own or tmpl)
            ov = {k: _retarget(v, guid, port) for k, v in src.items()}
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
