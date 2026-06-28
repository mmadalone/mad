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
from pathlib import Path

from lib.lindbergh_capture import loader_tags
from lib.madsrv import cfgutil

# The JVS controls a player slot owns, WITHOUT the PLAYER_<n>_ prefix. A pad's stored
# map is keyed by these (slot-agnostic); materialize() prefixes the slot at launch.
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
    return data


def save(gamedir: Path, data: dict) -> None:
    data = dict(data)
    data.setdefault("version", 1)
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


def render_ini(text: str, slots: dict, pads: dict, nplayers: int) -> str | None:
    """Write PLAYER_N_<control> tokens for each resolved slot's pad (and blank the controls
    of any unassigned slot), preserving everything else. Returns None if [EVDEV] is absent."""
    if cfgutil._ini_span(text, "EVDEV") is None:
        return None
    out = text
    for n in range(1, nplayers + 1):
        tag = slots.get(n)
        m = pads.get(tag, {}) if tag else {}
        for ctrl in CONTROLS:
            key = f"PLAYER_{n}_{ctrl}"
            val = f'"{tag}_{m[ctrl]}"' if (tag and ctrl in m and m[ctrl]) else '""'
            nt = cfgutil.ini_set_or_insert(out, "EVDEV", key, val)
            if nt is not None:
                out = nt
    return out


def materialize(gamedir: Path, nplayers: int = DEFAULT_PLAYERS) -> dict:
    """Launch-time: generate the game's lindbergh.ini [EVDEV] from the sidecar + connected
    pads (after backing the ini up). No-op (ini untouched) when there is no sidecar, no
    [EVDEV], or none of the configured pads are connected. Returns a small status dict."""
    data = load(gamedir)
    pads = data.get("pads") or {}
    if not pads:
        return {"applied": False, "reason": "no per-pad config"}
    ini = ini_of(gamedir)
    text = cfgutil.read_text(ini)
    if text is None:
        return {"applied": False, "reason": "no ini"}
    connected = {t["tag"] for t in loader_tags()}
    slots = resolve(data.get("priority") or [], pads, connected, nplayers)
    if not slots:
        return {"applied": False, "reason": "no configured pad connected"}
    new = render_ini(text, slots, pads, nplayers)
    if new is None:
        return {"applied": False, "reason": "no [EVDEV] section"}
    restore = ini.with_name(ini.name + RESTORE_SUFFIX)
    if not restore.exists():        # preserve the canonical ini across a missed restore
        cfgutil.atomic_write(restore, text)
    if new != text:
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


# ── connected-pad listing (for the MAD pads page) ────────────────────────────────
def connected_pads() -> list[dict]:
    """[{tag,name,label,path}] for the JOYPAD-class devices currently connected, each with
    its loader tag. Duplicate-named pads (e.g. two X-Arcade ports) get a port/rank hint so
    they're distinguishable in the picker."""
    from lib.devices import enumerate_devices, joypads, port_of
    jp = {d.path: d for d in joypads(enumerate_devices())}
    tags = loader_tags()
    namecount: dict[str, int] = {}
    for t in tags:                       # count duplicates only among the joypads we'll list
        if t["path"] in jp:
            namecount[t["name"]] = namecount.get(t["name"], 0) + 1
    out, rank = [], {}
    for t in tags:
        d = jp.get(t["path"])
        if d is None:
            continue
        label = t["name"]
        if namecount[t["name"]] > 1:
            # Same-named pads (e.g. the X-Arcade's two ports): a shared USB phys can't tell
            # them apart, so number them in enumeration order so the picker is unambiguous.
            rank[t["name"]] = rank.get(t["name"], 0) + 1
            port = port_of(d.phys) if getattr(d, "phys", "") else ""
            ports = {port_of(jp[x["path"]].phys) for x in tags
                     if x["name"] == t["name"] and x["path"] in jp and getattr(jp[x["path"]], "phys", "")}
            label = (f"{t['name']} ({port})" if port and len(ports) > 1
                     else f"{t['name']} #{rank[t['name']]}")
        out.append({"tag": t["tag"], "name": t["name"], "label": label, "path": t["path"]})
    return out


def _main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: lindbergh_pads.py {apply|restore} <gamedir>", file=sys.stderr)
        return 2
    cmd, gamedir = argv[1], Path(argv[2])
    if cmd == "apply":
        print(json.dumps(materialize(gamedir)))
        return 0
    if cmd == "restore":
        print(json.dumps({"restored": restore(gamedir)}))
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
