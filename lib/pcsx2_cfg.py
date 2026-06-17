"""
PCSX2 (PS2) controller-assignment backend for the controller-router.

PCSX2 binds each emulated pad to an SDL device by **index**, not GUID/name:
`PCSX2.ini` has `[Pad1]` with every button bound to `SDL-0/...`, `[Pad2]` to
`SDL-1/...`, etc. The button names are SDL-standard (`FaceSouth`,
`+LeftTrigger`, …), so the bind block is device-agnostic — only the `SDL-N/`
prefix selects which physical pad is that player.

So routing PS2 = find the PlayStation pads' real SDL indices (via
`devices.sdl_devices()`, the same SDL order PCSX2 walks moments later) and write
`[Pad1]`/`[Pad2]` to those `SDL-N`. This is robust even when Sinden guns / the
Steam Deck / other pads also occupy SDL slots (e.g. the Deck is usually SDL-0,
so the DualShock 4s land on SDL-1/2 — PCSX2's default `Pad1=SDL-0` would wrongly
be the Deck; the router fixes that).

Behaviour (all from `[backends.pcsx2]` in controller-policy.toml):
  * PlayStation pads (DualSense + DualShock 4, by vid:pid in `pad_classes`,
    priority order) → Pad1, Pad2 … up to `manage_pads`; extra slots -> None.
  * No PlayStation pad -> bind Pad1 to `handheld_class` (the Steam Deck) so the
    game is playable handheld; if that class isn't present (or it's ""), leave
    PCSX2.ini untouched.

Edits are section-targeted text replacements (the rest of PCSX2.ini is preserved
verbatim) with a one-time backup. PCSX2 is closed at ES-DE game-start (it
rewrites the ini on exit, so edits must happen while it's closed).
"""
from __future__ import annotations

import re
from pathlib import Path

from .devices import sdl_devices
from . import fsutil, inifile, pad_assign

_IDX = "@@IDX@@"   # placeholder for the SDL index in a bind template

# Canonical PCSX2 DualShock2 bind block (captured from a live EmuDeck PCSX2.ini),
# used when the existing [Pad1] has no usable bindings to clone. `@@IDX@@` is the
# SDL device index. The tuning header (AxisScale, deadzones) matches EmuDeck's.
_BAKED_DS2 = """Type = DualShock2
InvertL = 0
InvertR = 0
Deadzone = 0
AxisScale = 1.33
LargeMotorScale = 1
SmallMotorScale = 1
ButtonDeadzone = 0
PressureModifier = 0.5
Up = SDL-@@IDX@@/DPadUp
Right = SDL-@@IDX@@/DPadRight
Down = SDL-@@IDX@@/DPadDown
Left = SDL-@@IDX@@/DPadLeft
Triangle = SDL-@@IDX@@/FaceNorth
Circle = SDL-@@IDX@@/FaceEast
Cross = SDL-@@IDX@@/FaceSouth
Square = SDL-@@IDX@@/FaceWest
Select = SDL-@@IDX@@/Back
Start = SDL-@@IDX@@/Start
L1 = SDL-@@IDX@@/LeftShoulder
L2 = SDL-@@IDX@@/+LeftTrigger
R1 = SDL-@@IDX@@/RightShoulder
R2 = SDL-@@IDX@@/+RightTrigger
L3 = SDL-@@IDX@@/LeftStick
R3 = SDL-@@IDX@@/RightStick
LUp = SDL-@@IDX@@/-LeftY
LRight = SDL-@@IDX@@/+LeftX
LDown = SDL-@@IDX@@/+LeftY
LLeft = SDL-@@IDX@@/-LeftX
RUp = SDL-@@IDX@@/-RightY
RRight = SDL-@@IDX@@/+RightX
RDown = SDL-@@IDX@@/+RightY
RLeft = SDL-@@IDX@@/-RightX
LargeMotor = SDL-@@IDX@@/LargeMotor
SmallMotor = SDL-@@IDX@@/SmallMotor"""


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def _bind_template(text: str) -> str:
    """A DualShock2 bind block with the SDL index replaced by @@IDX@@. Clones the
    live [Pad1] (preserving any user tuning) if it's a usable DualShock2 block;
    otherwise the baked canonical block."""
    body = inifile.section_body(text, "Pad1")
    if body and "Type = DualShock2" in body and "SDL-" in body:
        return re.sub(r"SDL-\d+/", f"SDL-{_IDX}/", body)
    return _BAKED_DS2


def _pad_body(template: str, sdl_index: int) -> str:
    return template.replace(_IDX, str(sdl_index))


def assign(cfg: dict, logger, devs=None, pins=None) -> int:
    """Apply the PS2 pad assignment. Returns 0 (launch always continues).

    `pins` ({player: evdev Device}) + `devs` (the evdev device list) let a GLOBAL
    device pin override the default in-SDL-order selection: a pinned pad takes its
    player's [PadN] via its live SDL index (re-resolved each launch)."""
    ini = _expand(cfg.get("config_file", "~/.config/PCSX2/inis/PCSX2.ini"))
    manage = int(cfg.get("manage_pads", 2))
    pad_classes: list[str] = list(cfg.get("pad_classes", []))
    handheld_class = cfg.get("handheld_class", "")

    if not ini.is_file():
        logger.warning(f"pcsx2: config file {ini} not found; skipping")
        return 0

    sdl = sdl_devices()
    if not sdl:
        logger.warning("pcsx2: SDL enumerated no joysticks; leaving PCSX2.ini")
        return 0

    logger.info("pcsx2: SDL order = "
                + ", ".join(f"SDL-{d.index}:{d.vidpid}" for d in sdl))

    text = ini.read_text(encoding="utf-8")
    template = _bind_template(text)

    # Slot -> SDL index via the shared pipeline. pcsx2's value IS the SDL index,
    # so collisions are plain value-membership (unit_count=1). Two historical
    # quirks are preserved by flags: an over-manage pin still suppresses the
    # handheld fallback (filter_pins_at_resolve=False), and two players pinned to
    # one pad keep only the higher slot (dedup_pins=True, the original loop).
    from .devices import sdl_index_of
    assigned = pad_assign.assign_slots(
        sdl, manage, pins, devs,
        pad_classes=pad_classes, handheld=handheld_class,
        encode_auto=lambda d, rank: d.index,
        encode_pin=lambda pdev, sdl_devs, evdevs: sdl_index_of(pdev, evdevs, sdl_devs),
        base_index=1, filter_pins_at_resolve=False, dedup_pins=True,
    )
    if assigned is None:
        logger.info("pcsx2: no PlayStation pad and no handheld device; "
                    "leaving PCSX2.ini untouched")
        return 0
    logger.info("pcsx2: pads -> "
                + (", ".join(f"Pad{k}=SDL-{i}" for k, i in sorted(assigned.items()))
                   or "(all disabled)"))

    # Back up once, then write Pad1..manage (assigned -> DualShock2, else None).
    if fsutil.ensure_pristine_backup(ini):
        logger.info(f"pcsx2: one-time backup -> {ini.name}.router-backup")

    for k in range(1, manage + 1):
        if k in assigned:
            text = inifile.set_section(text, f"Pad{k}", _pad_body(template, assigned[k]))
        else:
            text = inifile.set_section(text, f"Pad{k}", "Type = None")

    fsutil.atomic_write(ini, text)
    logger.info(f"pcsx2: wrote {ini}")
    return 0


def assign_devices(players, ini_path: str | None = None, manage: int = 2) -> dict:
    """Configure-once device pick (MAD Standalones 'pads → players'): bind the
    ordered ``players`` (a list of ``devices.SdlDevice`` in priority order) to
    ``[Pad1..N]`` of PCSX2.ini by each pad's live SDL index, and ``Type = None``
    for slots beyond the connected count. The Standalones launch wrapper calls
    this at game-start (and restores the prior ``[Pad*]`` on exit).

    Unlike ``assign()`` there is no policy ``pad_classes``/``pins``/handheld — the
    caller already chose the order, so this is the explicit-list writer. The
    DualShock2 bind block is cloned from the live ``[Pad1]`` (preserving user
    tuning) or the baked canonical block, exactly like ``assign()``. Raises
    FileNotFoundError if PCSX2.ini is missing (launch a PS2 game once)."""
    ini = _expand(ini_path or "~/.config/PCSX2/inis/PCSX2.ini")
    if not ini.is_file():
        raise FileNotFoundError("PCSX2.ini not found — launch a PS2 game once")
    text = ini.read_text(encoding="utf-8")
    template = _bind_template(text)
    slots = max(int(manage), len(players))
    fsutil.ensure_pristine_backup(ini)
    for k in range(1, slots + 1):
        if k - 1 < len(players):
            text = inifile.set_section(text, f"Pad{k}", _pad_body(template, players[k - 1].index))
        else:
            text = inifile.set_section(text, f"Pad{k}", "Type = None")
    fsutil.atomic_write(ini, text)
    return {"assigned": [(f"Pad{i + 1}", d.index) for i, d in enumerate(players)]}
