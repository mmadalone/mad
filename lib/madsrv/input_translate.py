"""
input_translate.py — turn a CAPTURED physical input (from capture_cmds: an evdev
button code) into the per-emulator binding token the standalone input-map pages
write.

The capture pipeline (capture_cmds._CaptureStream) reports a press as a raw evdev
button code in 0x130..0x13E (BTN_SOUTH..BTN_THUMBR — face / shoulder / trigger-
click / select / start / stick-click). The C++ input-map page is emulator-
agnostic: it forwards that raw code + kind, and the per-emulator backend
(pcsx2_input_cmds, eden_input_cmds, ryujinx_input_cmds, …) calls in here to format
it for its config.

Three vocabularies, one per emulator family:
  * SDL GameController SOURCE NAME — PCSX2 (`SDL-i/FaceSouth`), Dolphin SDL handler.
  * SDL JOYSTICK BUTTON INDEX — Eden/Yuzu (`button:N`). NOT `code-0x130`: it is the
    button's RANK among the pad's PRESENT buttons (BTN_C/BTN_Z are absent on modern
    pads), which is what SDL's joystick layer numbers. Verified against a live Eden
    Pro-Controller config (A=East=1, X=North=2 — i.e. North/West/shoulders are
    shifted down by the missing BTN_C/BTN_Z). The map below is the standard
    modern-pad rank; a pad that actually exposes BTN_C/BTN_Z would differ (rare —
    refine to a device-caps rank if it ever bites).
  * Ryujinx `GamepadButtonInputId` enum NAME — Ryujinx Config.json joycon values.

SCOPE (v1): the digital BUTTONS only. The d-pad (a hat — capture_cmds skips hats)
and the analog sticks (evdev axis rank ≠ SDL axis order) are shown read-only until
a later pass adds hat capture + SDL-axis correlation.
"""
from __future__ import annotations

# evdev BTN_* → SDL GameController "source" name (PCSX2 / Dolphin-SDL).
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

# evdev BTN_* → SDL joystick button INDEX (rank among present buttons, BTN_C 0x132
# and BTN_Z 0x135 absent on modern pads). Eden/Yuzu `button:N` uses this. NOT
# code-0x130 (that double-counts the missing C/Z).
_EVDEV_BTN_TO_INDEX = {
    0x130: 0,   # South
    0x131: 1,   # East
    0x133: 2,   # North   (skips BTN_C 0x132)
    0x134: 3,   # West
    0x136: 4,   # TL / L1  (skips BTN_Z 0x135)
    0x137: 5,   # TR / R1
    0x138: 6,   # TL2 / L2
    0x139: 7,   # TR2 / R2
    0x13A: 8,   # Select
    0x13B: 9,   # Start
    0x13C: 10,  # Guide
    0x13D: 11,  # ThumbL / L3
    0x13E: 12,  # ThumbR / R3
}

# evdev BTN_* → Ryujinx GamepadButtonInputId enum token (Config.json joycon value).
_EVDEV_BTN_TO_RYUJINX = {
    0x130: "A", 0x131: "B", 0x133: "Y", 0x134: "X",
    0x136: "LeftShoulder", 0x137: "RightShoulder",
    0x138: "LeftTrigger", 0x139: "RightTrigger",
    0x13A: "Minus", 0x13B: "Plus", 0x13C: "Guide",
    0x13D: "LeftStick", 0x13E: "RightStick",
}

# Human label for an SDL source name (for showing the current binding in a page).
_SDL_SOURCE_LABEL = {
    "FaceSouth": "A / ✕", "FaceEast": "B / ○", "FaceNorth": "Y / △",
    "FaceWest": "X / ▢", "LeftShoulder": "L1", "RightShoulder": "R1",
    "+LeftTrigger": "L2", "+RightTrigger": "R2", "Back": "Select",
    "Start": "Start", "Guide": "Guide", "LeftStick": "L3", "RightStick": "R3",
}
# SDL joystick index → source name (inverse of _EVDEV_BTN_TO_INDEX), for labelling
# a stored Eden `button:N`.
_INDEX_TO_SOURCE = {idx: _EVDEV_BTN_TO_SDL[code]
                    for code, idx in _EVDEV_BTN_TO_INDEX.items()}


def sdl_button_source(evdev_code: int) -> str | None:
    """SDL GameController source name for a captured evdev button code, or None
    if it's outside the cleanly-mappable digital-button set (PCSX2 / Dolphin)."""
    return _EVDEV_BTN_TO_SDL.get(evdev_code)


def sdl_button_index(evdev_code: int) -> int | None:
    """SDL joystick button index (modern-pad rank) for a captured code — Eden's
    `button:N`. None outside the mappable digital-button set."""
    return _EVDEV_BTN_TO_INDEX.get(evdev_code)


def ryujinx_button(evdev_code: int) -> str | None:
    """Ryujinx GamepadButtonInputId token for a captured code, or None if outside
    the mappable digital-button set."""
    return _EVDEV_BTN_TO_RYUJINX.get(evdev_code)


def sdl_source_label(source: str) -> str:
    """Friendly label for an SDL source name as found in a config. Falls back to
    the raw source string."""
    return _SDL_SOURCE_LABEL.get(source, source)


def sdl_index_label(idx: int) -> str:
    """Friendly label for a stored SDL joystick button index (Eden display)."""
    src = _INDEX_TO_SOURCE.get(idx)
    return sdl_source_label(src) if src else f"button {idx}"
