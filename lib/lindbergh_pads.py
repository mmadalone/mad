#!/usr/bin/env python3
"""Per-game per-pad controller profiles for Sega Lindbergh (non-lightgun games).

The lindbergh-loader binds each PLAYER_N control in a game's lindbergh.ini [EVDEV]
to a device by NAME (the loader tag = san(name)[+ "_<rank>"] for duplicate names, in
/dev/input string-sort order — see lindbergh_capture.loader_tags). That is per-device,
so a binding made for one pad does NOT work on another, and a missing pad means a dead
player. To make input SEAMLESS across whichever pad is connected, this module:

  - stores, per game, a control map FOR EACH pad (slot-agnostic) + a priority order,
    in a sidecar `<gamedir>/lindbergh-pads.json`;
  - at LAUNCH, resolves the connected pads against the priority into player slots and
    MATERIALIZES the game's lindbergh.ini [EVDEV] from the chosen pads' maps, after
    backing the ini up to `<ini>.mad-restore`; the game-end hook restores it.

So whatever pad is connected drives its slot with its own bindings — no reconfigure.
Opt-in per game: with no sidecar (or no configured pad connected) the ini is left
untouched, so games configured the classic per-PLAYER way are unaffected.

This module imports no madsrv RPC *handlers* (only cfgutil for ini I/O — which pulls
rpc's error types but starts nothing), so the launch hook can run it as a plain CLI:
`python3 -m lib.lindbergh_pads apply <gamedir>` / `restore <gamedir>`.
"""
from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path

from lib.lindbergh_capture import loader_tags
from lib.madsrv import cfgutil

_LAUNCHERS = Path(__file__).resolve().parent.parent
_PROFILES_PATH = _LAUNCHERS / "data" / "lindbergh-profiles.json"

# The DIGITAL JVS controls a player slot owns, WITHOUT the PLAYER_<n>_ prefix. A pad's stored
# map is keyed by these (slot-agnostic); materialize() prefixes the slot at launch. Analog channels
# are NOT here: they are stored slot-agnostically as ANALOG_<i> keys and mapped to the game's global
# ANALOGUE_<n> channels via the sidecar's "analog" fn->channel layout (see render_ini).
CONTROLS: list[str] = (
    [f"BUTTON_{i}" for i in range(1, 9)]
    + ["BUTTON_UP", "BUTTON_DOWN", "BUTTON_LEFT", "BUTTON_RIGHT",
       "BUTTON_START", "COIN", "BUTTON_SERVICE"]
)

DEFAULT_PLAYERS = 2          # non-lightgun JVS games are 1-2 players
RESTORE_SUFFIX = ".mad-restore"


# ── per-game ini location (replicates lindbergh_cmds._elf_of/_ini_of, kept self-
#    contained to avoid importing the madsrv RPC graph at launch) ────────────────
def _elf_of(gamedir: Path) -> Path | None:
    cmd = gamedir / f"{gamedir.name}.commands"
    try:
        line = cmd.read_text().strip()
    except OSError:
        return None
    if not line or line.startswith("-"):   # a -t test tile: not a real game
        return None
    elf = (gamedir / line).resolve()
    return elf if elf.is_file() else None


def ini_of(gamedir: Path) -> Path:
    elf = _elf_of(gamedir)
    return (elf.parent / "lindbergh.ini") if elf is not None else (gamedir / "elf" / "lindbergh.ini")


# ── sidecar I/O ────────────────────────────────────────────────────────────────
def sidecar_path(gamedir: Path) -> Path:
    return gamedir / "lindbergh-pads.json"


def load(gamedir: Path) -> dict:
    """{"version","priority":[tag...],"pads":{tag:{control:codename}}} or {} if none/bad."""
    try:
        data = json.loads(sidecar_path(gamedir).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    data.setdefault("version", 1)
    data.setdefault("priority", [])
    data.setdefault("pads", {})
    if not isinstance(data["priority"], list) or not isinstance(data["pads"], dict):
        return {}
    if "analog" in data and not isinstance(data["analog"], list):
        return {}                            # corrupt analog -> clean no-op, not a launch-time crash
    if "single_player" in data:
        data["single_player"] = bool(data["single_player"])   # never let a non-bool flip the path
    return data


def save(gamedir: Path, data: dict) -> None:
    data = dict(data)
    data["version"] = 2          # v2 = may carry analog fn->channel layout; v1 was digital-only
    # Drop empty pad maps (a cleared pad shouldn't linger as a phantom slot). Keep the
    # priority order as given (deduped) even for not-yet-mapped/disconnected pads — the
    # user sets order then maps, and resolve() skips any unmapped/absent tag at launch.
    data["pads"] = {t: m for t, m in (data.get("pads") or {}).items() if m}
    seen, prio = set(), []
    for t in (data.get("priority") or []):
        if t not in seen:
            seen.add(t)
            prio.append(t)
    data["priority"] = prio
    cfgutil.atomic_write(sidecar_path(gamedir), json.dumps(data, indent=1, sort_keys=True) + "\n")


# ── resolution + materialization ────────────────────────────────────────────────
def resolve(priority: list[str], pads: dict, connected: set[str], nplayers: int) -> dict:
    """{slot: tag} — walk the priority order, assign each tag that is connected AND has a
    non-empty profile to the next free slot (1..nplayers). A tag with no profile or that
    isn't connected is skipped (the next priority pad takes the slot = seamless fallback)."""
    slots: dict[int, str] = {}
    slot = 1
    seen: set[str] = set()
    order = list(priority) + [t for t in pads if t not in priority]  # priority first, then any extras
    for tag in order:
        if slot > nplayers:
            break
        if tag in seen:
            continue
        seen.add(tag)
        if tag in connected and pads.get(tag):
            slots[slot] = tag
            slot += 1
    return slots


def render_ini(text: str, slots: dict, pads: dict, nplayers: int, analog: list | None = None,
               blank_unassigned: bool = True) -> str | None:
    """Write PLAYER_N_<control> tokens for each resolved slot's pad (and blank the controls
    of any unassigned slot), preserving everything else. Returns None if [EVDEV] is absent.

    `analog` (optional) is the per-game fn->channel layout [{fn,p1,p2}, ...] from the sidecar; when
    present AND a resolved pad actually mapped an analog function, the global ANALOGUE_<chan> channels
    are written per slot too (slot 1 -> p1 channel, slot 2 -> p2). This is opt-in so a digital-only
    config never disturbs a driving game's canonical wheel/pedals.

    `blank_unassigned`: when False, a WHOLE unassigned slot (no pad) is left at its canonical bindings
    instead of blanked. The launch path passes False for a legacy/unknown-shape sidecar (no
    single_player flag), so a pre-rework single-driver config does not blank the PLAYER_2 gear shifter
    before it has been re-opened (and re-classified) in MAD. A known 2-human game passes True."""
    if cfgutil._ini_span(text, "EVDEV") is None:
        return None
    out = text
    for n in range(1, nplayers + 1):
        tag = slots.get(n)
        m = pads.get(tag, {}) if tag else {}
        for ctrl in CONTROLS:
            key = f"PLAYER_{n}_{ctrl}"
            if tag and ctrl in m and m[ctrl]:
                val = f'"{tag}_{m[ctrl]}"'
            elif tag is None and not blank_unassigned:
                continue                          # unassigned slot, unknown shape -> keep canonical
            else:
                val = '""'                        # assigned-but-unmapped, or a known-2-human empty slot
            nt = cfgutil.ini_set_or_insert(out, "EVDEV", key, val)
            if nt is not None:
                out = nt
    analog = analog or []
    if analog and any(any(k.startswith("ANALOG_") for k in pads.get(slots.get(n)) or {})
                      for n in range(1, nplayers + 1)):
        for n in range(1, nplayers + 1):
            tag = slots.get(n)
            m = pads.get(tag, {}) if tag else {}
            for fn in analog:
                ch = fn.get(f"p{n}")
                if not ch:                          # this player has no such analog channel
                    continue
                code = m.get(fn.get("fn")) if tag else None
                if tag and code:
                    out = cfgutil.ini_set_or_insert(out, "EVDEV", f"ANALOGUE_{ch}", f'"{tag}_{code}"') or out
                    dz = f"ANALOGUE_DEADZONE_{ch}"  # add a neutral deadzone only if the channel lacks one
                    if cfgutil.ini_read(out, "EVDEV", dz) is None:
                        out = cfgutil.ini_set_or_insert(out, "EVDEV", dz, "0 0 0") or out
                elif not tag:                       # genuinely unassigned slot -> blank its channels
                    out = cfgutil.ini_set_or_insert(out, "EVDEV", f"ANALOGUE_{ch}", '""') or out
                # else: an assigned pad that just didn't map this analog function -> leave the channel
                #       at its canonical value (with the canonical-base render, that is the default
                #       binding); never blank a wheel because the slot's pad happens to lack it.
    return out


def render_ini_single(text: str, tag: str, pad_map: dict, analog: list | None = None) -> str | None:
    """SINGLE-human game: write the one driver's pad bindings DIRECTLY to their real PLAYER_<n>_<ctrl>
    and ANALOGUE_<chan> keys — both JVS slots belong to the one human, so the gear shifter on PLAYER_2
    is bound from the same pad (not collapsed onto PLAYER_1). Only keys the pad actually mapped are
    written; every other control is LEFT at its canonical value (no blanking of the second JVS slot).
    Returns None if [EVDEV] is absent."""
    if cfgutil._ini_span(text, "EVDEV") is None:
        return None
    out = text
    for key, code in (pad_map or {}).items():
        if not key.startswith("PLAYER_") or not code:   # digital keys are the real PLAYER_<n>_<ctrl>
            continue
        out = cfgutil.ini_set_or_insert(out, "EVDEV", key, f'"{tag}_{code}"') or out
    for fn in (analog or []):
        ch, code = fn.get("p1"), (pad_map or {}).get(fn.get("fn"))   # 1-human games use the p1 channels
        if ch and code:
            out = cfgutil.ini_set_or_insert(out, "EVDEV", f"ANALOGUE_{ch}", f'"{tag}_{code}"') or out
            dz = f"ANALOGUE_DEADZONE_{ch}"
            if cfgutil.ini_read(out, "EVDEV", dz) is None:
                out = cfgutil.ini_set_or_insert(out, "EVDEV", dz, "0 0 0") or out
    return out


def _priority_order(data: dict, pads: dict) -> list:
    return list(data.get("priority") or []) + [t for t in pads if t not in (data.get("priority") or [])]


def materialize(gamedir: Path, nplayers: int = DEFAULT_PLAYERS) -> dict:
    """Launch-time: generate the game's lindbergh.ini [EVDEV] from the sidecar + connected pads
    (after backing the ini up). No-op (ini untouched) when there is no sidecar, no [EVDEV], or none
    of the configured pads are connected. Returns a small status dict.

    The [EVDEV] is rendered from the CANONICAL bindings each launch (not the possibly-already-
    materialized live ini), so a missed restore (an ES-DE crash) never compounds and any control the
    user did not map keeps its canonical value. Two shapes: a single-human game (data['single_player'])
    materializes ONE pad onto its real PLAYER_<n> keys across both JVS slots (gear shifter included,
    nothing blanked); a multi-human game resolves connected pads -> slots, slot-agnostic, blanking
    unassigned slots."""
    data = load(gamedir)
    pads = data.get("pads") or {}
    if not pads:
        return {"applied": False, "reason": "no per-pad config"}
    ini = ini_of(gamedir)
    live = cfgutil.read_text(ini)
    if live is None:
        return {"applied": False, "reason": "no ini"}
    restore = ini.with_name(ini.name + RESTORE_SUFFIX)
    # Base the [EVDEV] render on the canonical bindings: if a backup already exists it holds the
    # canonical, so splice its [EVDEV] into the live text (preserving any non-EVDEV settings edits).
    if restore.exists():
        canon = cfgutil.read_text(restore)
        base = (_splice_evdev(live, canon) if canon is not None else None) or live
    else:
        base = live
    connected = {t["tag"] for t in loader_tags()}
    if data.get("single_player"):
        # one human spanning both JVS slots: the highest-priority CONNECTED mapped pad drives it all
        chosen = next((t for t in _priority_order(data, pads) if t in connected and pads.get(t)), None)
        if not chosen:
            return {"applied": False, "reason": "no configured pad connected"}
        new = render_ini_single(base, chosen, pads[chosen], data.get("analog"))
        slots = {1: chosen}
    else:
        slots = resolve(data.get("priority") or [], pads, connected, nplayers)
        if not slots:
            return {"applied": False, "reason": "no configured pad connected"}
        # Blank an unassigned slot only for a KNOWN 2-human game. A legacy sidecar (no single_player
        # flag, not yet re-opened in MAD) is shape-unknown, so leave its unassigned PLAYER_2 at
        # canonical — a single-driver game may keep its gear shifter there. New configs always carry
        # the flag (set by _pad_bind), and opening the MAD pads page heals + re-keys old ones.
        new = render_ini(base, slots, pads, nplayers, data.get("analog"),
                         blank_unassigned=(data.get("single_player") is False))
    if new is None:
        return {"applied": False, "reason": "no [EVDEV] section"}
    if not restore.exists():        # first materialize: the live ini IS the canonical -> back it up
        cfgutil.atomic_write(restore, live)
    if new != live:
        cfgutil.atomic_write(ini, new)
    return {"applied": True, "slots": {str(k): v for k, v in slots.items()}}


def _splice_evdev(dst: str, src: str) -> str | None:
    """`dst` with its [EVDEV] body replaced by `src`'s [EVDEV] body (None if either lacks it)."""
    d = cfgutil._ini_span(dst, "EVDEV")
    s = cfgutil._ini_span(src, "EVDEV")
    if d is None or s is None:
        return None
    return dst[:d[0]] + src[s[0]:s[1]] + dst[d[1]:]


def restore(gamedir: Path) -> bool:
    """Game-end: revert ONLY the [EVDEV] section to its pre-materialize canonical (from .mad-restore),
    preserving any OTHER edits made to the live ini meanwhile. materialize() only ever changes [EVDEV],
    so a missed restore (ES-DE death) leaving a stale .mad-restore must NOT clobber MAD Settings edits
    (region / resolution / crosshair) the user made to the live ini in between (rule #5)."""
    ini = ini_of(gamedir)
    bak = ini.with_name(ini.name + RESTORE_SUFFIX)
    canon = cfgutil.read_text(bak)
    if canon is None:
        return False
    live = cfgutil.read_text(ini)
    merged = _splice_evdev(live, canon) if live is not None else None
    cfgutil.atomic_write(ini, merged if merged is not None else canon)
    try:
        bak.unlink()
    except OSError:
        pass
    return True


# --- handheld auto-default (WS-D): a working Deck-pad map, injected only when undocked ---
# The Deck's built-in pad plays a non-lightgun Lindbergh game via Steam's virtual 28de:11ff pad (a
# full evdev gamepad; the raw 1205 node is buttonless lizard-mode). This ships a default control map
# so such a game is playable OUT OF THE BOX handheld, injected ONLY when handheld + non-lightgun + no
# pad is configured, on the SAME transient [EVDEV] rail materialize() uses (reverted on game-end, so
# the docked config is never touched). A user-configured pad (via the MAD pads page) always wins.
#
# Deck landmines (deck-docs/standalone-input-binding-formats.md): the 11ff L2/R2 are analog-only, so
# BUTTON_7/8 bind the BARE ABS_Z/ABS_RZ axis (never _MAX -> the stuck-button bug); the d-pad is a HAT
# (ABS_HAT0*_MIN/MAX). BTN_MODE/Guide is intentionally unused so the Steam overlay isn't triggered.
DEFAULT_DECK_MAP = {
    "BUTTON_1": "BTN_SOUTH", "BUTTON_2": "BTN_EAST", "BUTTON_3": "BTN_NORTH", "BUTTON_4": "BTN_WEST",
    "BUTTON_5": "BTN_TL", "BUTTON_6": "BTN_TR", "BUTTON_7": "ABS_Z", "BUTTON_8": "ABS_RZ",
    "BUTTON_UP": "ABS_HAT0Y_MIN", "BUTTON_DOWN": "ABS_HAT0Y_MAX",
    "BUTTON_LEFT": "ABS_HAT0X_MIN", "BUTTON_RIGHT": "ABS_HAT0X_MAX",
    "BUTTON_START": "BTN_START", "COIN": "BTN_SELECT", "BUTTON_SERVICE": "BTN_THUMBR",
}

_profiles_cache: dict | None = None
_crc_cache: dict = {}


def _region_crc(elf: Path) -> str | None:
    """The loader's per-rev game id (self-contained copy of lindbergh_cmds._region_crc, kept here so
    the launch CLI never imports the madsrv RPC graph): crc32 of 0x4000 bytes at
    program-header[2].p_offset + 10 (ELF32). Memoized per (path, mtime, size)."""
    import struct
    try:
        st = elf.stat()
    except OSError:
        return None
    key = (str(elf), st.st_mtime_ns, st.st_size)
    if key in _crc_cache:
        return _crc_cache[key]
    crc = None
    try:
        with open(elf, "rb") as f:
            hdr = f.read(0x34)
            if len(hdr) >= 0x34 and hdr[:4] == b"\x7fELF" and hdr[4] == 1:
                e_phoff = struct.unpack_from("<I", hdr, 0x1C)[0]
                e_phentsize = struct.unpack_from("<H", hdr, 0x2A)[0]
                e_phnum = struct.unpack_from("<H", hdr, 0x2C)[0]
                if e_phnum >= 3:
                    f.seek(e_phoff + 2 * e_phentsize + 4)
                    po = f.read(4)
                    if len(po) == 4:
                        f.seek(struct.unpack("<I", po)[0] + 10)
                        region = f.read(0x4000)
                        if len(region) == 0x4000:
                            crc = f"{zlib.crc32(region) & 0xFFFFFFFF:08x}"
    except (OSError, struct.error, IndexError):
        crc = None
    _crc_cache[key] = crc
    return crc


def _profiles() -> dict:
    global _profiles_cache
    if _profiles_cache is None:
        try:
            _profiles_cache = json.loads(_PROFILES_PATH.read_text())
        except Exception:
            _profiles_cache = {}
    return _profiles_cache


def is_gun_game(gamedir: Path) -> bool:
    """True if the game's profile marks it a lightgun title (skip the pad auto-default). Best-effort:
    an unknown/profile-less game returns False (treated as a pad game)."""
    try:
        elf = _elf_of(gamedir)
        if elf is None:
            return False
        crc = _region_crc(elf)
        return bool(crc and (_profiles().get(crc) or {}).get("gun"))
    except Exception:
        return False


def _handheld() -> bool:
    """The on-the-go handheld gate (same as switch_bind._launch_handheld, replicated so the launch
    CLI stays off the RPC graph): feature enabled AND the Deck is physically handheld."""
    try:
        from lib import deck_state, policy
        hh = policy.load_merged().get("handheld")
        if not (isinstance(hh, dict) and hh.get("enabled", False)):
            return False
        return deck_state.is_handheld(deck_state.resolve_force(hh))
    except Exception:
        return False


def _deck_pad_tag() -> str | None:
    """The loader tag of the Deck's connected Steam-virtual pad (28de:11ff), resolved DYNAMICALLY
    (its name/index isn't stable). loader_tags() enumerates it (unlike the router's joypads(), which
    drops the phantom); match it by the virtual-pad's evdev path. First 11ff = the Deck's primary."""
    try:
        from lib.devices import enumerate_devices
        virt = {d.path for d in enumerate_devices() if getattr(d, "is_steam_virtual", False)}
        if not virt:
            return None
        for t in loader_tags():
            if t.get("path") in virt:
                return t.get("tag")
    except Exception:
        return None
    return None


def materialize_handheld_default(gamedir: Path, nplayers: int = DEFAULT_PLAYERS) -> dict:
    """Launch-time fall-through (called by apply when materialize() found no usable configured pad):
    when HANDHELD + non-lightgun + the Deck pad is connected, render the shipped DEFAULT_DECK_MAP onto
    PLAYER_1 (P2 left canonical), backing the ini up to .mad-restore so the game-end hook reverts it.
    No-op docked / lightgun / no Deck pad / no [EVDEV]. Best-effort; never raises into the caller."""
    try:
        if not _handheld():
            return {"applied": False, "reason": "docked"}
        if is_gun_game(gamedir):
            return {"applied": False, "reason": "lightgun game"}
        tag = _deck_pad_tag()
        if not tag:
            return {"applied": False, "reason": "no Deck pad connected"}
        ini = ini_of(gamedir)
        live = cfgutil.read_text(ini)
        if live is None:
            return {"applied": False, "reason": "no ini"}
        restore = ini.with_name(ini.name + RESTORE_SUFFIX)
        base = live
        if restore.exists():
            canon = cfgutil.read_text(restore)
            base = (_splice_evdev(live, canon) if canon is not None else None) or live
        new = render_ini(base, {1: tag}, {tag: dict(DEFAULT_DECK_MAP)}, nplayers, blank_unassigned=False)
        if new is None:
            return {"applied": False, "reason": "no [EVDEV] section"}
        if not restore.exists():
            cfgutil.atomic_write(restore, live)
        if new != live:
            cfgutil.atomic_write(ini, new)
        return {"applied": True, "reason": "handheld default", "slots": {"1": tag}}
    except Exception as e:
        return {"applied": False, "reason": f"error {e!r}"}


# ── connected-pad listing (for the MAD pads page) ────────────────────────────────
def connected_pads() -> list[dict]:
    """[{tag,name,label,path}] for the JOYPAD-class devices currently connected, each with
    its loader tag. Labels are the friendly pad_labels names (KNOWN_PADS; the identified
    X-Arcade splits into "X-Arcade P1"/"P2" by USB interface). Pads that still share a
    label (e.g. two DualSense) get a port/rank hint so they're distinguishable in the
    picker — but NO dedup: two pads = two player rows. Labels are cosmetic; the loader
    matches by tag only. Daemon-only (UI RPCs) — the materialize/restore CLI path never
    calls this, so the heavier imports stay function-local."""
    from lib.devices import enumerate_devices, joypads, port_of
    from lib.pad_labels import device_label
    from lib.policy import load_merged
    from lib.routing import xarcade_port
    try:
        xport = xarcade_port(load_merged())
    except Exception:
        xport = ""                       # best-effort: unidentified -> class names only
    _devs = enumerate_devices()
    jp = {d.path: d for d in joypads(_devs)}
    # The Deck's built-in pad reaches the loader ONLY as the Steam-virtual 28de:11ff (joypads() drops
    # it as a phantom; the raw 1205 node is buttonless lizard-mode), so it would never appear here.
    # Re-admit the FIRST one -- the same pad materialize_handheld_default binds -- so the pads page can
    # configure the Deck for handheld play.
    _deck = sorted((d for d in _devs if getattr(d, "is_steam_virtual", False)), key=lambda d: d.path)
    _deck_path = _deck[0].path if _deck else None
    if _deck_path is not None:
        jp[_deck_path] = _deck[0]
    tags = loader_tags()
    # Friendly label per listed pad. Note the X-Arcade halves' P1/P2 follows the physical
    # side (bInterfaceNumber, same as Preview/RetroArch), NOT tag order — the base tag can
    # be the P2 half (event10 string-sorts before event6). Don't "fix" that reversal.
    friendly: dict[str, str] = {}
    labelcount: dict[str, int] = {}
    for t in tags:                       # count duplicates only among the joypads we'll list
        d = jp.get(t["path"])
        if d is None:
            continue
        friendly[t["path"]] = "Steam Deck" if t["path"] == _deck_path else device_label(d, xport)
        labelcount[friendly[t["path"]]] = labelcount.get(friendly[t["path"]], 0) + 1
    out, rank = [], {}
    for t in tags:
        d = jp.get(t["path"])
        if d is None:
            continue
        label = friendly[t["path"]]
        if labelcount[label] > 1:
            # Same-labeled pads (e.g. two DualSense; or X-Arcade halves whose interface
            # number was unreadable): distinguish by port when the ports differ, else
            # number them in enumeration order so the picker is unambiguous.
            rank[label] = rank.get(label, 0) + 1
            base = label
            port = port_of(d.phys) if getattr(d, "phys", "") else ""
            ports = {port_of(jp[x["path"]].phys) for x in tags
                     if x["path"] in jp and friendly.get(x["path"]) == base
                     and getattr(jp[x["path"]], "phys", "")}
            label = (f"{base} ({port})" if port and len(ports) > 1
                     else f"{base} #{rank[base]}")
        out.append({"tag": t["tag"], "name": t["name"], "label": label, "path": t["path"]})
    return out


def _main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: lindbergh_pads.py {apply|restore} <gamedir>", file=sys.stderr)
        return 2
    cmd, gamedir = argv[1], Path(argv[2])
    if cmd == "apply":
        res = materialize(gamedir)
        if not res.get("applied"):                 # no user-configured pad -> try the handheld default
            dflt = materialize_handheld_default(gamedir)
            if dflt.get("applied"):
                res = dflt
            elif restore(gamedir):
                # Neither applied (docked / no config). Heal a crash orphan at GAME-START too: an
                # ES-DE death leaves the Deck map in [EVDEV], and without this a docked relaunch would
                # run one session on the stale handheld map (game-end is the only other heal).
                res = {"applied": False, "reason": "healed crash orphan"}
        print(json.dumps(res))
        return 0
    if cmd == "restore":
        print(json.dumps({"restored": restore(gamedir)}))
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
