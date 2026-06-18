"""
xemu (Original Xbox) controller-assignment backend for the controller-router.

xemu binds each console port to a controller by its **SDL GUID**, in the
`[input.bindings]` table of its TOML config:

    [input.bindings]
    port1 = '03000000...'   # SDL GUID of the Player 1 pad
    port2 = '4c050000...'   # Player 2, etc. (absent key = port unbound)

GUID is the device CLASS (vendor/product/crc), so two identical pads share one
GUID — xemu then fills the ports in order from the matching devices (so two PS4
controllers → port1 + port2 works; ordering between identical pads falls to
power-on order, the usual caveat). Distinct models pin cleanly.

This backend picks the connected PlayStation pads (PS4 treated like DualSense,
by vid:pid in `pad_classes`) via `devices.sdl_devices()` and writes port1..N to
their GUIDs; no pad → bind port1 to `handheld_class` (Steam Deck) or leave the
file untouched. xemu rewrites xemu.toml on exit, so we edit while it's closed
(guaranteed at ES-DE game-start) and keep a one-time backup. Everything comes
from `[backends.xemu]` in controller-policy.toml.
"""
from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from . import inifile
from . import fsutil
from . import pad_assign
from .devices import sdl_devices

_PORT_RE = re.compile(r"\s*port\d+\s*=")


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the Xbox pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` let a GLOBAL pin set a port's pad.
    NOTE: xemu binds by SDL GUID (device CLASS), so a pin selects the port's pad
    MODEL — two identical-model pads can't be told apart here (xemu fills them in
    SDL order). Cross-model pins (e.g. P1=8BitDo, P2=DualSense) ARE honored."""
    path = _expand(cfg.get(
        "config_file", "~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml"))
    manage = int(cfg.get("manage_ports", 4))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld = cfg.get("handheld_class", "")

    if not path.is_file():
        logger.warning(f"xemu: config file {path} not found; skipping")
        return 0

    sdl = sdl_devices()
    if not sdl:
        logger.warning("xemu: SDL enumerated no joysticks; leaving xemu.toml")
        return 0

    # Slot (1-based port) -> SDL GUID via the shared pipeline. xemu binds by class
    # GUID, so the collision rule is class-with-spare-count: a pinned class drops a
    # colliding auto port ONLY when no spare physical unit of that class remains —
    # so two identical pads on two ports survive, but a single pad pinned to one
    # port can no longer phantom-duplicate onto another (the fix for latent bug
    # "D"). unit_count(guid) = how many physical pads of that class are present.
    from collections import Counter
    from .devices import vidpid as _vp
    units = Counter(d.guid for d in sdl)
    assigned = pad_assign.assign_slots(
        sdl, manage, pins, devs,
        pad_classes=pad_classes, handheld=handheld,
        encode_auto=lambda d, rank: d.guid,
        encode_pin=lambda pdev, sdl_devs, evdevs: next(
            (s.guid for s in sdl_devs if s.vidpid == _vp(pdev)), None),
        unit_count=lambda g: units[g],
        base_index=1,
    )
    if assigned is None:
        logger.info("xemu: no PlayStation pad and no handheld device; "
                    "leaving xemu.toml untouched")
        return 0
    logger.info("xemu: ports -> "
                + (", ".join(f"port{k}={g[:12]}…" for k, g in sorted(assigned.items()))
                   or "(none)"))

    text = path.read_text(encoding="utf-8")
    # Preserve any non-port keys already in [input.bindings]; replace the portN set.
    body = inifile.section_body(text, "input.bindings") or ""
    keep = [ln for ln in body.splitlines() if ln.strip() and not _PORT_RE.match(ln)]
    new_lines = keep + [f"port{k} = '{g}'" for k, g in sorted(assigned.items())]

    backup = path.with_name(path.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(path, backup)
        logger.info(f"xemu: one-time backup -> {backup.name}")

    text = inifile.set_section(text, "input.bindings", "\n".join(new_lines))
    fsutil.atomic_write(path, text)
    logger.info(f"xemu: wrote {path}")
    return 0


def assign_devices(players, config_path: str | None = None, manage: int = 4) -> dict:
    """Configure-once device pick (MAD Standalones 'pads → players'): bind the ordered
    ``players`` (a list of ``devices.SdlDevice`` in priority order) to
    ``[input.bindings] portN`` of xemu.toml by each pad's SDL GUID; ports beyond the
    connected count are left unbound (key absent). The Standalones launch wrapper calls
    this at game-start (and restores the prior ``[input.bindings]`` on exit).

    Unlike ``assign()`` there is no policy ``pad_classes``/``pins``/handheld — the caller
    already chose the order. Non-port keys (keyboard etc.) in ``[input.bindings]`` are
    preserved. Raises FileNotFoundError if xemu.toml is missing."""
    path = _expand(config_path or "~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml")
    if not path.is_file():
        raise FileNotFoundError("xemu.toml not found — launch an Xbox game once")
    slots = min(len(players), int(manage))
    text = path.read_text(encoding="utf-8")
    body = inifile.section_body(text, "input.bindings") or ""
    keep = [ln for ln in body.splitlines() if ln.strip() and not _PORT_RE.match(ln)]
    new_lines = keep + [f"port{k + 1} = '{players[k].guid}'" for k in range(slots)]

    backup = path.with_name(path.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(path, backup)

    text = inifile.set_section(text, "input.bindings", "\n".join(new_lines))
    fsutil.atomic_write(path, text)
    return {"assigned": [(f"port{i + 1}", d.guid) for i, d in enumerate(players[:slots])]}


# ── Per-button remap: [input] gamepad_mappings (xemu >= v0.8.133) ────────────
# xemu stores per-pad button/axis remaps in the `[input]` section as
# `gamepad_mappings`, a TOML array-of-tables keyed by `gamepad_id` (the SDL GUID
# = the same string as portN). We edit ONLY the matching pad's entry, re-emit the
# array, and replace the `[input]` section body via inifile.set_section — so
# sibling pad entries, other `[input]` scalars (gamecontrollerdb_path, …) and
# every other section ([input.bindings], [display], …) stay byte-for-byte intact.
# Note: section_body("input") stops at the next `[` (= [input.bindings]), so the
# body it returns is valid standalone TOML and never contains the port bindings.
_GP_KEY = "gamepad_mappings"


def _toml_scalar(v) -> str:
    """Serialize a Python value back to xemu-style TOML (literal single-quoted
    strings, lowercase bools, inline tables)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return "'" + v + "'"            # TOML literal string; xemu values are quote-free
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_scalar(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    raise TypeError(f"xemu_cfg: cannot serialize {type(v).__name__}")


def emit_gamepad_mappings(entries: list) -> str:
    """The `gamepad_mappings = [ … ]` assignment as a multi-line inline-table array
    (one {…} per line), matching xemu's own emission style."""
    lines = [f"{_GP_KEY} = ["]
    for e in entries:
        inner = ", ".join(f"{k} = {_toml_scalar(v)}" for k, v in e.items())
        lines.append(f"    {{ {inner} }},")
    lines.append("    ]")
    return "\n".join(lines)


def _emit_input_body(parsed: dict) -> str:
    """Re-emit the `[input]` section body from a parsed dict, preserving key order.
    Sub-tables never appear here (they are their own [input.x] sections); skip any
    defensively rather than corrupt the file."""
    out = []
    for k, v in parsed.items():
        if k == _GP_KEY:
            out.append(emit_gamepad_mappings(v if isinstance(v, list) else []))
        elif isinstance(v, dict):
            continue
        else:
            out.append(f"{k} = {_toml_scalar(v)}")
    return "\n".join(out)


def read_gamepad_mappings(text: str) -> list:
    """The `[input] gamepad_mappings` array as a list of per-pad dicts, or [].
    Reads xemu's inline-array form (the [input] body) and falls back to a whole-
    file parse so the block-of-tables form ([[input.gamepad_mappings]]) is also
    tolerated on read."""
    body = inifile.section_body(text, "input") or ""
    if body.strip():
        try:
            val = tomllib.loads(body).get(_GP_KEY)
            if isinstance(val, list):
                return val
        except tomllib.TOMLDecodeError:
            pass
    try:
        val = tomllib.loads(text).get("input", {}).get(_GP_KEY)
        return val if isinstance(val, list) else []
    except (tomllib.TOMLDecodeError, AttributeError):
        return []


def set_controller_mappings(text: str, gamepad_id: str, updates: dict) -> str:
    """Set multiple `controller_mapping` key→value pairs (int axis/button indices
    AND bool invert flags) on the gamepad_mappings entry whose `gamepad_id ==
    gamepad_id` (seeding it if absent), returning the new file text. Sibling pad
    entries, other `[input]` keys and all other sections are preserved byte-for-byte.
    Value TYPES are kept (int → `N`, bool → `true`/`false`)."""
    body = inifile.section_body(text, "input") or ""
    try:
        parsed = tomllib.loads(body) if body.strip() else {}
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"xemu.toml [input] is not valid TOML: {e}") from e
    entries = parsed.get(_GP_KEY)
    if not isinstance(entries, list):
        entries = []
    target = next((e for e in entries
                   if isinstance(e, dict) and e.get("gamepad_id") == gamepad_id), None)
    if target is None:
        target = {"gamepad_id": gamepad_id}
        entries.append(target)
    cm = target.get("controller_mapping")
    if not isinstance(cm, dict):
        cm = {}
        target["controller_mapping"] = cm
    cm.update(updates)
    parsed[_GP_KEY] = entries
    return inifile.set_section(text, "input", _emit_input_body(parsed))


def set_controller_mapping(text: str, gamepad_id: str, xbox_key: str,
                           sdl_index: int) -> str:
    """Set ONE `controller_mapping.<xbox_key> = <int>` (button / d-pad index)."""
    return set_controller_mappings(text, gamepad_id, {xbox_key: int(sdl_index)})
