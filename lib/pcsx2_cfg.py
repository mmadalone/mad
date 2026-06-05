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
import shutil
from pathlib import Path

from .devices import sdl_devices

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


def _section_body(text: str, name: str) -> str | None:
    """Return the body (lines after the header, no trailing blanks) of [name]."""
    m = re.search(rf"(?ms)^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text)
    return m.group(1).rstrip("\n") if m else None


def _set_section(text: str, name: str, body: str) -> str:
    """Replace (or append) the [name] section with `body`, preserving the rest
    of the file. One trailing blank line separates sections."""
    block = f"[{name}]\n{body}\n\n"
    pat = re.compile(rf"(?ms)^\[{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(lambda _m: block, text, count=1)
    if not text.endswith("\n"):
        text += "\n"
    return text + block


def _bind_template(text: str) -> str:
    """A DualShock2 bind block with the SDL index replaced by @@IDX@@. Clones the
    live [Pad1] (preserving any user tuning) if it's a usable DualShock2 block;
    otherwise the baked canonical block."""
    body = _section_body(text, "Pad1")
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

    # PlayStation pads in priority order (pad_classes), then SDL index.
    prio = {c: i for i, c in enumerate(pad_classes)}
    ps = sorted((d for d in sdl if d.vidpid in prio),
                key=lambda d: (prio[d.vidpid], d.index))
    logger.info("pcsx2: SDL order = "
                + ", ".join(f"SDL-{d.index}:{d.vidpid}" for d in sdl))

    text = ini.read_text(encoding="utf-8")
    template = _bind_template(text)

    # Global device pins → live SDL index (override the in-order pick below).
    pinned_idx: dict[int, int] = {}   # pad slot (1-based) -> sdl index (from a pin)
    if pins and devs:
        from .devices import sdl_index_of
        for port, dev in pins.items():
            si = sdl_index_of(dev, devs, sdl)
            if si is not None:
                pinned_idx[port] = si

    # Decide each managed pad's SDL index (None => disable that pad).
    assigned: dict[int, int] = {}     # pad slot (1-based) -> sdl index
    if ps:
        for k in range(1, manage + 1):
            if k - 1 < len(ps):
                assigned[k] = ps[k - 1].index
        logger.info("pcsx2: PlayStation pads -> "
                    + ", ".join(f"Pad{k}=SDL-{i}" for k, i in assigned.items()))
    elif not pinned_idx:
        # Handheld fallback: bind Pad1 to the Steam Deck if present.
        deck = next((d for d in sdl if d.vidpid == handheld_class), None)
        if not handheld_class or deck is None:
            logger.info("pcsx2: no PlayStation pad and no handheld device; "
                        "leaving PCSX2.ini untouched")
            return 0
        assigned[1] = deck.index
        logger.info(f"pcsx2: no PlayStation pad -> Pad1=SDL-{deck.index} (handheld)")

    # Apply pins last: a pinned pad takes its slot's SDL index (freeing that index
    # from any other slot first) — so e.g. a specific DualShock4 lands on Pad1.
    for port, si in sorted(pinned_idx.items()):
        if port > manage:
            continue
        for k in [k for k, v in assigned.items() if v == si and k != port]:
            del assigned[k]
        assigned[port] = si
    if pinned_idx:
        logger.info("pcsx2: pins -> "
                    + ", ".join(f"Pad{k}=SDL-{i}" for k, i in sorted(pinned_idx.items())))

    # Back up once, then write Pad1..manage (assigned -> DualShock2, else None).
    backup = ini.with_name(ini.name + ".router-backup")
    if not backup.exists():
        shutil.copy2(ini, backup)
        logger.info(f"pcsx2: one-time backup -> {backup.name}")

    for k in range(1, manage + 1):
        if k in assigned:
            text = _set_section(text, f"Pad{k}", _pad_body(template, assigned[k]))
        else:
            text = _set_section(text, f"Pad{k}", "Type = None")

    ini.write_text(text, encoding="utf-8")
    logger.info(f"pcsx2: wrote {ini}")
    return 0
