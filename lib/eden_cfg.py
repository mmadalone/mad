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
    # errors="replace": a stray non-UTF-8 file in the input dir must not raise UnicodeDecodeError
    # (a ValueError, not caught by the callers' `except OSError`) out of the remap / harvest path.
    body = inifile.section_body(
        profile.read_text(encoding="utf-8", errors="replace"), "Controls") or ""
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


def _canonical_guid(tmpl_map: dict, by_guid: dict, connected_guid: str, vidpid: str) -> str:
    """The guid the emulator matches for this device, correcting the ONE case the raw connection guid
    gets wrong: a pad the emulator opens as an SDL GameController (a DualSense/DS4 in Eden) is recorded
    with the gamecontrollerdb USB-bus (03) guid regardless of the live transport, so a Bluetooth
    connection (bus 05) misses. Rule: only when the live pad is NOT already on the USB bus, look for a
    bus-03 guid for its vid:pid among the templates THEN the resting blocks -- if one exists, return it
    (searching for bus-03 specifically means a STALE bus-05 resting block left by an old binder can't
    shadow the canonical form). Otherwise return the live connected guid unchanged. That keeps every
    raw-joystick pad on its ACTUAL bus -- the Wii U Pro, a USB-connected pad, and ALL of Citron (which
    records the raw live bus, so it has no bus-03 form to find) -- so there is no regression where the
    emulator matches the live connection guid."""
    g = (connected_guid or "").lower()
    if not vidpid or g[:2] == "03":     # unknown pad, or already USB-bus -> nothing to canonicalize
        return g
    for m in (tmpl_map, by_guid):       # templates are authoritative; then a (maybe stale) resting block
        for k in m:
            if k[:2] == "03" and _guid_to_vidpid(k) == vidpid:
                return k
    return g


# ── per-device d-pad correctness (template lookup + launch self-heal) ────────────
# The d-pad's SDL button BASE is device-specific: on this Eden a DualSense/DS4 reports the
# d-pad as button:11..14 and the Wii U Pro as button:13..16 (both button-style, so hat-vs-button
# does NOT tell them apart here). The reliable per-device source is the input/*.ini TEMPLATE
# matched by vid:pid -- a connected pad's BUS byte (05=BT) differs from the template's (03=USB),
# so an exact-guid match usually misses. Shared by the remap page (dpad_index) and the launch
# self-heal (_heal_dpad).
_DPAD_DIR_KEY = {"up": "button_dup", "down": "button_ddown",
                 "left": "button_dleft", "right": "button_dright"}
_DPAD_OFFSET = {"up": 0, "down": 1, "left": 2, "right": 3}
_KEY_DIR = {v: k for k, v in _DPAD_DIR_KEY.items()}
_BTN_IDX_RE = re.compile(r"button:(\d+)")


def _match_template(input_dir: Path, guid: str) -> dict:
    """The device template block for `guid`: exact no-CRC guid match, else vid:pid match (the
    reliable path, since a connected pad's bus byte differs from the template's), else {}.
    Scans input_dir/*.ini in sorted order (deterministic)."""
    g = (guid or "").lower()
    vp = _guid_to_vidpid(g)
    vidpid_hit: dict = {}
    try:
        for tf in sorted(input_dir.glob("*.ini")):
            binds = _clean_block(_template_bindings(tf))
            bg = _block_guid(binds)
            if not bg:
                continue
            if bg == g:
                return binds
            if vp and not vidpid_hit and _guid_to_vidpid(bg) == vp:
                vidpid_hit = binds
    except OSError:
        pass
    return vidpid_hit


def template_dpad_index(input_dir: Path, guid: str, direction: str) -> int | None:
    """The correct `button:N` index for `direction` on the pad `guid`, from its device template
    (matched by exact-guid then vid:pid). None if no template matches or it isn't button-style."""
    key = _DPAD_DIR_KEY.get(direction)
    if not key:
        return None
    m = _BTN_IDX_RE.search(_match_template(input_dir, guid).get(key, ""))
    return int(m.group(1)) if m else None


def dpad_index(input_dir: Path, cur: str, direction: str, key: str) -> int | None:
    """Correct per-device d-pad button index for `direction`, for the pad whose binding string is
    `cur` (contains guid:G). Tier 1: the device's input template (authoritative, survives poison).
    Tier 2: derive the base from cur's OWN current index (base = M - offset(key)) so an
    untemplated-but-clean pad keeps its base instead of being stamped with the Wii U rank. None ->
    the caller uses the Wii U default (today's behavior)."""
    m = _GUID_RE.search(cur or "")
    if m:
        ti = template_dpad_index(input_dir, m.group(1), direction)
        if ti is not None:
            return ti
    bm = _BTN_IDX_RE.search(cur or "")
    if bm and key in _KEY_DIR and direction in _DPAD_OFFSET:
        return int(bm.group(1)) - _DPAD_OFFSET[_KEY_DIR[key]] + _DPAD_OFFSET[direction]
    return None


def _harvest_templates(input_dir: Path) -> dict:
    """{no_crc_guid: bindings} from the input/*.ini template profiles ONLY (the clean per-device
    layouts), for the launch self-heal. First-writer-wins per guid."""
    out: dict = {}
    try:
        for tf in sorted(input_dir.glob("*.ini")):
            binds = _clean_block(_template_bindings(tf))
            g = _block_guid(binds)
            if g and g not in out:
                out[g] = binds
    except OSError:
        pass
    return out


def _template_for(tmpl_map: dict, guid: str) -> dict:
    """The template block for a device: exact no-CRC guid, else a vid:pid variant, else {}."""
    g = (guid or "").lower()
    if g in tmpl_map:
        return tmpl_map[g]
    vp = _guid_to_vidpid(g)
    if vp:
        for k, binds in tmpl_map.items():
            if _guid_to_vidpid(k) == vp:
                return binds
    return {}


def _dpad_index_set(binds: dict):
    """The set of button:N ints across the 4 d-pad keys, or None if the d-pad isn't button-style
    (a hat / axis / missing key) -> such a block is never 'foreign' and is left alone."""
    idxs = set()
    for key in _DPAD_DIR_KEY.values():
        v = binds.get(key, "")
        if "button:" not in v:
            return None
        m = _BTN_IDX_RE.search(v)
        if m:
            idxs.add(int(m.group(1)))
    return idxs or None


def _all_button_set(binds: dict) -> set:
    """Every button:N index across ALL keys of a block = the device's button RANGE (from its
    template). Lets us tell a poisoned d-pad (references a button the device LACKS) apart from a
    legit d-pad direction cross-mapped to a face/shoulder button the device HAS."""
    s: set = set()
    for v in binds.values():
        m = _BTN_IDX_RE.search(v)
        if m:
            s.add(int(m.group(1)))
    return s


def _dpad_foreign(block: dict, tmpl: dict) -> bool:
    """True iff the block's d-pad references a button the device does NOT have -- its d-pad index set
    is not a subset of the DEVICE's FULL button range (every button:N the template maps, not just the
    4 d-pad keys). That is the signature of a foreign base a buggy remap stamped on (the Wii U's
    button:13..16 on a DS that has no button:15). A legit remap -- even cross-mapping a d-pad direction
    to a face/shoulder button the device has -- stays a subset and is never flagged; a hat/axis d-pad
    (index set None) is never foreign."""
    bs = _dpad_index_set(block)
    if not bs:
        return False
    dev = _all_button_set(tmpl)
    return bool(dev and not bs <= dev)


def _heal_dpad(block: dict, tmpl: dict) -> dict:
    """If the block's d-pad is structurally FOREIGN to the device template (a buggy remap stamped a
    base the pad lacks, e.g. button:13..16 on a DS whose real d-pad is 11..14), replace the 4 d-pad
    key VALUES with the template's (retargeted to the device's guid/port by the caller). Else return
    the block unchanged -- no-op when there is no template or the d-pad is a hat / in-range remap."""
    if not _dpad_foreign(block, tmpl):
        return block
    healed = dict(block)
    for key in _DPAD_DIR_KEY.values():
        if key in tmpl:
            healed[key] = tmpl[key]
    return healed


def _fallback_template(input_dir: Path) -> dict:
    """A real block for the last-ditch `tmpl` fallback, replacing the historical (now nonexistent)
    'Deck P1 Pro Controller.ini' default. Prefers a handheld/Deck (28de:1205) template, else a
    Deck/GamePad/Steamdeck-named one, else the first *.ini; {} only if the dir is empty."""
    def score(tf: Path) -> int:
        binds = _clean_block(_template_bindings(tf))
        if _guid_to_vidpid(_block_guid(binds)) == "28de:1205":
            return 0
        n = tf.name.lower()
        return 1 if ("deck" in n or "gamepad" in n or "steamdeck" in n) else 2
    try:
        files = sorted(input_dir.glob("*.ini"))
    except OSError:
        return {}
    for tf in sorted(files, key=score):
        binds = _clean_block(_template_bindings(tf))
        if binds:
            return binds
    return {}


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
    if not tmpl:                       # the historical default file is gone -> a real fallback block
        tmpl = _fallback_template(template.parent)
    text = ini.read_text(encoding="utf-8")
    body = inifile.section_body(text, "Controls") or ""
    # Give each pad the block that MATCHES its guid (correct hat/button/axis structure),
    # not the slot's leftover tokens -- fixes a hat-d-pad pad (DS/DS4) that lands on a slot
    # last held by a button-d-pad pad (Wii U Pro) getting a dead `button:13` d-pad.
    by_guid = _harvest_guid_bindings(body, template.parent)
    tmpl_map = _harvest_templates(template.parent)   # clean per-device layouts, for the d-pad self-heal

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
                # Slot already holds THIS device -> keep its (maybe user-customised) binds. A wrong
                # d-pad STRUCTURE a buggy remap left here (a foreign base) is self-healed below via
                # _heal_dpad against the device template; a legit in-range remap is preserved.
                src = own
            else:                  # a DIFFERENT device (or empty) -> the block matching this pad
                src = _resolve_block(by_guid, guid, vidpid, own or tmpl)
            # Self-heal a poisoned d-pad: if src references buttons this device does not have (a
            # foreign base a buggy remap stamped on, e.g. button:13..16 on a DS whose d-pad is
            # 11..14), restore the 4 d-pad keys from the device template. An in-range remap stays a
            # subset of the template and is left untouched.
            src = _heal_dpad(src, _template_for(tmpl_map, guid))
            # Write the guid the emulator matches against, not the raw connection. Eden canonicalizes a
            # DualSense/DS4 to a bus-03 GameController guid even over Bluetooth; the raw bus-05 guid
            # misses. This only overrides that specific case (bus-03 form exists + live pad on another
            # bus) and otherwise keeps the live guid -- so the Wii U Pro and ALL of Citron (raw live
            # bus) are untouched, and a stale bus-05 resting can't shadow the canonical bus-03.
            tgt = _canonical_guid(tmpl_map, by_guid, guid, vidpid)
            ov = {k: _retarget(v, tgt, port) for k, v in src.items()}
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
