"""
input_translate.py — turn a CAPTURED physical input (from capture_cmds: an evdev
button code, or an axis token) into the per-emulator binding token the standalone
input-map pages write.

The capture pipeline (capture_cmds._CaptureStream) reports a press as a raw evdev
button code in 0x130..0x13F (BTN_SOUTH..BTN_THUMBR — face/shoulder/trigger-click/
select/start/stick-click), or, in axis mode, a token "±N". The C++ input-map page
is emulator-agnostic: it forwards that raw value + kind, and the per-emulator
backend (pcsx2_input_cmds, …) calls in here to format it.

SCOPE (v1): the digital BUTTONS, which map cleanly + device-independently onto
SDL's GameController button names. The d-pad (a hat on most pads — capture_cmds
skips hats) and the analog sticks (whose evdev axis RANK does NOT match SDL's
axis ordering, so it can't be trusted without per-device SDL correlation) are
deliberately NOT translated here yet — pages show those binds read-only until a
later pass adds hat capture + SDL-axis correlation.

Shared by every emulator whose config references inputs by SDL GameController
name (PCSX2 `SDL-i/FaceSouth`, Dolphin `Buttons/A = ` + the SDL/evdev source).
Emulators that reference inputs differently (Eden = SDL button INDEX; RPCS3 =
SDL button name; Cemu = SDL internal code; Supermodel = JOYn_BUTTONm) get their
own small adapters in later phases — but they can all start from the SDL button
index this module also exposes.
"""
from __future__ import annotations

# evdev BTN_* (0x130..0x13E) → SDL GameController "source" name, the string the
# SDL-name emulators write. The four face buttons follow SDL's A/B/X/Y =
# South/East/West/North convention (evdev BTN_NORTH=0x133=top=Triangle/Y,
# BTN_WEST=0x134=left=Square/X). The triggers' digital click (BTN_TL2/TR2) maps
# to SDL's analog trigger source, which is what these emulators use for L2/R2.
_EVDEV_BTN_TO_SDL = {
    0x130: "FaceSouth",      # BTN_SOUTH  (A / Cross)
    0x131: "FaceEast",       # BTN_EAST   (B / Circle)
    0x133: "FaceNorth",      # BTN_NORTH  (Y / Triangle)
    0x134: "FaceWest",       # BTN_WEST   (X / Square)
    0x136: "LeftShoulder",   # BTN_TL     (L1)
    0x137: "RightShoulder",  # BTN_TR     (R1)
    0x138: "+LeftTrigger",   # BTN_TL2    (L2, full-pull digital click)
    0x139: "+RightTrigger",  # BTN_TR2    (R2)
    0x13A: "Back",           # BTN_SELECT (Select / Back)
    0x13B: "Start",          # BTN_START  (Start)
    0x13C: "Guide",          # BTN_MODE   (Guide / PS)
    0x13D: "LeftStick",      # BTN_THUMBL (L3)
    0x13E: "RightStick",     # BTN_THUMBR (R3)
}

# Human label for an SDL source name (for showing the current binding in a page).
_SDL_SOURCE_LABEL = {
    "FaceSouth": "A / ✕", "FaceEast": "B / ○", "FaceNorth": "Y / △",
    "FaceWest": "X / ▢", "LeftShoulder": "L1", "RightShoulder": "R1",
    "+LeftTrigger": "L2", "+RightTrigger": "R2", "Back": "Select",
    "Start": "Start", "Guide": "Guide", "LeftStick": "L3", "RightStick": "R3",
}


def sdl_button_source(evdev_code: int) -> str | None:
    """SDL GameController source name for a captured evdev button code, or None
    if it's outside the cleanly-mappable digital-button set."""
    return _EVDEV_BTN_TO_SDL.get(evdev_code)


def sdl_button_index(evdev_code: int) -> int | None:
    """SDL/udev joypad button INDEX (= code - 0x130), the numbering RetroArch and
    Eden use. None outside 0x130..0x13F."""
    return evdev_code - 0x130 if 0x130 <= evdev_code <= 0x13F else None


def sdl_source_label(source: str) -> str:
    """Friendly label for an SDL source name as found in a config (e.g. for the
    'currently: L1' display). Falls back to the raw source string."""
    return _SDL_SOURCE_LABEL.get(source, source)
