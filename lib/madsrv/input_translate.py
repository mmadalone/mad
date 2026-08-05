"""
input_translate.py — turn a CAPTURED physical input (from capture_cmds: an evdev
button code) into the per-emulator binding token the standalone input-map pages
write.

The capture pipeline (capture_cmds._CaptureStream) reports a press as a raw evdev
button code in 0x130..0x13E (BTN_SOUTH..BTN_THUMBR — face / shoulder / trigger-
click / select / start / stick-click). The C++ input-map page is emulator-
agnostic: it forwards that raw code + kind, and the per-emulator backend
(pcsx2x6_input_cmds, xemu_input_cmds, the hotkey pages, …) calls in here to format
it for its config.

Vocabularies, one per emulator family:
  * SDL GameController SOURCE NAME — PCSX2 (`SDL-i/FaceSouth`), Dolphin SDL handler.
  * SDL_GameControllerButton ENUM INDEX — xemu.
  * SDL GameController SOURCE TOKEN — RPCS3.

The Eden/Yuzu `button:N` (SDL joystick rank) and Ryujinx `GamepadButtonInputId`
vocabularies lived here too until 2026-08-04, when the Switch per-button editor pages
were removed (profiles are authored in those emulators now and MAD only picks them).
Their tables went with their only callers; the byte formats are still documented in
deck-docs/standalone-input-binding-formats.md.

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

# Human label for an SDL source name (for showing the current binding in a page).
_SDL_SOURCE_LABEL = {
    "FaceSouth": "A / ✕", "FaceEast": "B / ○", "FaceNorth": "Y / △",
    "FaceWest": "X / ▢", "LeftShoulder": "L1", "RightShoulder": "R1",
    "+LeftTrigger": "L2", "+RightTrigger": "R2", "Back": "Select",
    "Start": "Start", "Guide": "Guide", "LeftStick": "L3", "RightStick": "R3",
    "DPadUp": "D-pad Up", "DPadDown": "D-pad Down",
    "DPadLeft": "D-pad Left", "DPadRight": "D-pad Right",
    "+LeftX": "L-stick →", "-LeftX": "L-stick ←",
    "+LeftY": "L-stick ↓", "-LeftY": "L-stick ↑",
    "+RightX": "R-stick →", "-RightX": "R-stick ←",
    "+RightY": "R-stick ↓", "-RightY": "R-stick ↑",
}


def sdl_button_source(evdev_code: int) -> str | None:
    """SDL GameController source name for a captured evdev button code, or None
    if it's outside the cleanly-mappable digital-button set (PCSX2 / Dolphin)."""
    return _EVDEV_BTN_TO_SDL.get(evdev_code)


def sdl_source_label(source: str) -> str:
    """Friendly label for an SDL source name as found in a config. Falls back to
    the raw source string."""
    return _SDL_SOURCE_LABEL.get(source, source)


# ── xemu (Original Xbox) ────────────────────────────────────────────────────
# xemu's `[input] gamepad_mappings[].controller_mapping` maps each Xbox control
# NAME to an **SDL_GameControllerButton** ENUM INDEX (the int xemu feeds to
# SDL_GameControllerGetButton). This is the SDL *GameController* enum — DISTINCT
# from the SDL *joystick* button RANK (which is what the Yuzu forks' `button:N` uses;
# its table lived here until the Switch editors were removed 2026-08-04). Note the face
# crossing: evdev BTN_WEST(0x134)→X=2, BTN_NORTH(0x133)→Y=3.
_EVDEV_BTN_TO_SDL_GC = {
    0x130: 0,    # BTN_SOUTH  → A
    0x131: 1,    # BTN_EAST   → B
    0x134: 2,    # BTN_WEST   → X
    0x133: 3,    # BTN_NORTH  → Y
    0x13A: 4,    # BTN_SELECT → Back
    0x13C: 5,    # BTN_MODE   → Guide
    0x13B: 6,    # BTN_START  → Start
    0x13D: 7,    # BTN_THUMBL → LeftStick (L3)
    0x13E: 8,    # BTN_THUMBR → RightStick (R3)
    0x136: 9,    # BTN_TL     → LeftShoulder (L / LB)
    0x137: 10,   # BTN_TR     → RightShoulder (R / RB)
}
# SDL_GameControllerButton index → friendly label (d-pad 11..14 are shown read-
# only — they arrive as hats, which the button-capture path does not emit).
_SDL_GC_LABEL = {
    0: "A", 1: "B", 2: "X", 3: "Y", 4: "Back", 5: "Guide", 6: "Start",
    7: "L3", 8: "R3", 9: "L (LB)", 10: "R (RB)",
    11: "D-Up", 12: "D-Down", 13: "D-Left", 14: "D-Right",
}


def xemu_button_index(evdev_code: int) -> int | None:
    """SDL_GameControllerButton enum index for a captured evdev button code (for
    xemu's controller_mapping), or None outside the mappable digital-button set."""
    return _EVDEV_BTN_TO_SDL_GC.get(evdev_code)


def xemu_index_label(idx: int) -> str:
    """Friendly label for a stored SDL_GameControllerButton index (xemu display)."""
    return _SDL_GC_LABEL.get(idx, f"button {idx}")


# ── RPCS3 (PS3) ─────────────────────────────────────────────────────────────
# RPCS3's SDL pad handler stores each PS3 control NAME → an SDL GameController
# SOURCE TOKEN in `Player N Input → Config` (input_configs/global/Default.yml). The
# token vocabulary is the one already used by rpcs3_cfg._SDL_PLAYER: face =
# South/East/North/West, shoulders = LB/RB, triggers = LT/RT, stick-clicks = LS/RS,
# Select=Back, Start=Start, PS=Guide, d-pad = Up/Down/Left/Right. A remap sets
# Config[<ps3 key>] = <token of the physical button pressed> (device-agnostic, like
# PCSX2's source edit — so it survives the router re-pointing the Device per launch).
_EVDEV_BTN_TO_RPCS3 = {
    0x130: "South",   # BTN_SOUTH  (Cross)
    0x131: "East",    # BTN_EAST   (Circle)
    0x133: "North",   # BTN_NORTH  (Triangle)
    0x134: "West",    # BTN_WEST   (Square)
    0x136: "LB",      # BTN_TL     (L1)
    0x137: "RB",      # BTN_TR     (R1)
    0x138: "LT",      # BTN_TL2    (L2, full-pull digital click)
    0x139: "RT",      # BTN_TR2    (R2)
    0x13A: "Back",    # BTN_SELECT (Select)
    0x13B: "Start",   # BTN_START  (Start)
    0x13C: "Guide",   # BTN_MODE   (PS Button)
    0x13D: "LS",      # BTN_THUMBL (L3)
    0x13E: "RS",      # BTN_THUMBR (R3)
}
# RPCS3: d-pad directions store the literal direction token in Config.
_RPCS3_DPAD = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
# Friendly label for a stored RPCS3 source token (for showing the current binding).
_RPCS3_TOKEN_LABEL = {
    "South": "A / ✕", "East": "B / ○", "North": "Y / △", "West": "X / ▢",
    "LB": "L1", "RB": "R1", "LT": "L2", "RT": "R2", "LS": "L3", "RS": "R3",
    "Back": "Select", "Start": "Start", "Guide": "Guide",
    "Up": "D-pad Up", "Down": "D-pad Down", "Left": "D-pad Left", "Right": "D-pad Right",
    "LS X-": "L-stick Left", "LS X+": "L-stick Right", "LS Y+": "L-stick Up", "LS Y-": "L-stick Down",
    "RS X-": "R-stick Left", "RS X+": "R-stick Right", "RS Y+": "R-stick Up", "RS Y-": "R-stick Down",
}


def rpcs3_button(evdev_code: int) -> str | None:
    """RPCS3 SDL source token for a captured evdev button code, or None outside the
    mappable digital-button set."""
    return _EVDEV_BTN_TO_RPCS3.get(evdev_code)


def rpcs3_dpad(token: str) -> str | None:
    """RPCS3 d-pad source token ('Up'/…) from a capture hat token 'h<N><dir>', else None."""
    d = _hat_direction(token)
    return _RPCS3_DPAD.get(d) if d else None


def rpcs3_token_label(token: str) -> str:
    """Friendly label for a stored RPCS3 Config source token (falls back to the raw token)."""
    return _RPCS3_TOKEN_LABEL.get(token, token)


# ── Dolphin (GameCube) ──────────────────────────────────────────────────────
# GCPadNew.ini binds each [GCPadN] control to a token in the vocabulary of THAT
# slot's `Device` backend: evdev/… uses libevdev names (EAST/SOUTH/TL/THUMBL…),
# SDL/… uses SDL_Gamepad semantic names (Button S/E/N/W, Shoulder L/R, Back, …).
# Only the DIGITAL buttons are remapped here (the sticks/d-pad axis tokens are
# device-specific — a capture cannot synthesize it); both button vocabularies below are
# device-agnostic fixed tables, verified against the live GCPadNew.ini on this Deck
# + Dolphin source (SDLGamepad.cpp / evdev.cpp), 2026-07-08.
_EVDEV_BTN_TO_DOLPHIN_EVDEV = {
    0x130: "SOUTH", 0x131: "EAST", 0x133: "NORTH", 0x134: "WEST",
    0x136: "TL", 0x137: "TR", 0x138: "TL2", 0x139: "TR2",
    0x13A: "SELECT", 0x13B: "START", 0x13C: "MODE", 0x13D: "THUMBL", 0x13E: "THUMBR",
}
_EVDEV_BTN_TO_DOLPHIN_SDL = {
    0x130: "Button S", 0x131: "Button E", 0x133: "Button N", 0x134: "Button W",
    0x136: "Shoulder L", 0x137: "Shoulder R", 0x138: "Trigger L", 0x139: "Trigger R",
    0x13A: "Back", 0x13B: "Start", 0x13C: "Guide", 0x13D: "Thumb L", 0x13E: "Thumb R",
}


def dolphin_gc_button(evdev_code: int, sdl: bool) -> str | None:
    """Dolphin GCPadNew.ini token for a captured evdev button code, in the SDL or
    evdev vocabulary (per the pad slot's Device backend). None outside the mappable
    digital-button set (face / shoulders / triggers-click / select / start / guide /
    stick-clicks)."""
    table = _EVDEV_BTN_TO_DOLPHIN_SDL if sdl else _EVDEV_BTN_TO_DOLPHIN_EVDEV
    return table.get(evdev_code)


def dolphin_gc_axis_token(sign: str, rank: int, trigger: bool) -> str:
    """Dolphin GC stick/trigger token in the LEGACY `Axis N` form -- source-verified to resolve on
    BOTH the evdev and SDL backends (Dolphin's SDL backend always adds legacy `Axis N` inputs
    alongside the recognized names). Sticks: `Axis <rank><sign>` (raw sign, + = toward max, no
    odd-axis flip). Analog triggers: `Full Axis <rank>+` (whole-travel, full-pull). `rank` = the
    captured axis rank among the device's non-hat ABS axes (== Dolphin's axis index for sticks/
    triggers, since hats rank last)."""
    return f"Full Axis {rank}+" if trigger else f"Axis {rank}{sign}"


# ── D-pad (hat) remap — capture_cmds returns a single hat direction as a token
# "h<N><dir>" (e.g. "h0up"); each emulator stores the d-pad differently, so map the
# DIRECTION (hat number ignored — the user is binding "this Xbox/Switch/PS2 d-pad
# direction" to whatever they pressed) to that emulator's d-pad token. ──────────
def _hat_direction(token: str) -> str | None:
    """'up'/'down'/'left'/'right' from a capture hat token 'h<N><dir>', else None."""
    if not token or token[0] != "h":
        return None
    for d in ("up", "down", "left", "right"):
        if token.endswith(d) and token[1:-len(d)].isdigit():
            return d
    return None


# xemu: controller_mapping dpad_* = SDL_GameControllerButton index (FIXED 11..14).
_XEMU_DPAD = {"up": 11, "down": 12, "left": 13, "right": 14}
# PCSX2: SDL source name (FIXED).
_PCSX2_DPAD = {"up": "DPadUp", "down": "DPadDown", "left": "DPadLeft", "right": "DPadRight"}
# Ryujinx: GamepadButtonInputId enum NAME (FIXED).
# Eden: SDL JOYSTICK button index of the d-pad. CAVEAT: this is the modern-pad rank
# (verified live = 13..16); a pad that exposes the d-pad at a different button rank
# would differ — same class of caveat as the SDL joystick button rank generally.


def xemu_hat_dpad_index(token: str) -> int | None:
    d = _hat_direction(token)
    return _XEMU_DPAD.get(d) if d else None


def pcsx2_dpad_source(token: str) -> str | None:
    d = _hat_direction(token)
    return _PCSX2_DPAD.get(d) if d else None


# ── Analog sticks + triggers (Phase 2) ──────────────────────────────────────
# The "axisname" capture mode emits a CANONICAL token "{sign}{canonical}", e.g.
# "+left_x", "-right_y", "+trigger_left" (rank-independent; see capture_cmds).
# Each emulator maps the canonical name to its own storage.
_CANONICAL_AXES = ("left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right")
# Natural deflection sign (evdev: + is right / down / full-pull). A directed-push
# capture ("push right" / "push down" / "pull") derives invert = sign != natural.
_CANONICAL_NATURAL_SIGN = {a: "+" for a in _CANONICAL_AXES}
# xemu: SDL_GameControllerAxis enum index.
_CANONICAL_TO_SDL_GC = {
    "left_x": 0, "left_y": 1, "right_x": 2, "right_y": 3,
    "trigger_left": 4, "trigger_right": 5,
}
# PCSX2: SDL axis source NAME (the sign is prepended from the capture).
_CANONICAL_TO_PCSX2_AXIS = {
    "left_x": "LeftX", "left_y": "LeftY", "right_x": "RightX", "right_y": "RightY",
    "trigger_left": "LeftTrigger", "trigger_right": "RightTrigger",
}
# RPCS3: SDL stick source "<LS|RS> <axis><sign>", e.g. "LS Y+". RPCS3's Y is UP-positive (its
# default binds Left Stick Up = "LS Y+"), i.e. INVERTED vs the evdev/canonical natural sign
# (+ = down); X matches (right = X+). Sticks only - RPCS3 triggers are digital L2/R2 buttons.
_CANONICAL_TO_RPCS3_STICK = {"left_x": "LS X", "left_y": "LS Y", "right_x": "RS X", "right_y": "RS Y"}


def parse_axis_token(token: str):
    """('+', 'left_x') from an 'axisname' token ('+left_x', or '+left_x@3' with the
    raw axis rank appended), or None if malformed / not a known canonical axis."""
    if not token or token[0] not in ("+", "-"):
        return None
    sign, rest = token[0], token[1:]
    canonical = rest.split("@", 1)[0]
    if canonical not in _CANONICAL_AXES:
        return None
    return sign, canonical


def canonical_is_trigger(canonical: str) -> bool:
    return canonical in ("trigger_left", "trigger_right")


def xemu_axis_index(canonical: str) -> int | None:
    """SDL_GameControllerAxis index (0..5) for a canonical axis, or None."""
    return _CANONICAL_TO_SDL_GC.get(canonical)


def axis_invert(sign: str, canonical: str) -> bool:
    """Whether to set the emulator's invert flag, from the captured sign vs the
    axis's natural direction (the directed-push UX)."""
    return sign != _CANONICAL_NATURAL_SIGN.get(canonical, "+")


def pcsx2_axis_source(sign: str, canonical: str) -> str | None:
    """PCSX2 SDL source 'signAxisName' for a captured stick/trigger, e.g.
    ('-','left_y') → '-LeftY'. Triggers are full-pull (always '+'). None if unknown."""
    name = _CANONICAL_TO_PCSX2_AXIS.get(canonical)
    if not name:
        return None
    if canonical_is_trigger(canonical):
        sign = "+"
    return f"{sign}{name}"


def rpcs3_axis_source(sign: str, canonical: str) -> str | None:
    """RPCS3 SDL stick source ('LS Y+', 'RS X-') for a captured stick push, or None for a
    non-stick axis (RPCS3 triggers are digital L2/R2 buttons, not remapped here). RPCS3's Y
    axis is up-positive, so it is inverted vs the captured evdev sign; X is unchanged."""
    base = _CANONICAL_TO_RPCS3_STICK.get(canonical)
    if not base:
        return None
    if base.endswith("Y"):
        sign = "-" if sign == "+" else "+"
    return f"{base}{sign}"


_SDL_GC_AXIS_LABEL = {0: "L-stick X", 1: "L-stick Y", 2: "R-stick X", 3: "R-stick Y",
                      4: "L trigger", 5: "R trigger"}


def xemu_axis_label(idx: int) -> str:
    """Friendly label for a stored xemu SDL_GameControllerAxis index (display)."""
    return _SDL_GC_AXIS_LABEL.get(idx, f"axis {idx}")


# ── pcsx2x6 USB devices (Light Gun / HID Mouse) ─────────────────────────────
# The "pointer" capture (capture_cmds._on_pointer) reports either a MOUSE button
# ({kind:"mouse", mbtn:1..5}) or a KEYBOARD key ({kind:"key", key:<RA name>}). The
# C++ input page forwards these as kind="gun" + gun_kind + value. PCSX2's [USB*]
# guncon2_*/hidmouse_* keys store an InputManager SOURCE string:
#   mouse button -> "Pointer-<idx>/LeftButton|RightButton|MiddleButton"  (idx = port-1)
#   keyboard key -> "Keyboard/<QtKey>"
_MBTN_TO_USB = {1: "LeftButton", 2: "RightButton", 3: "MiddleButton"}
# RetroArch capture key name -> PCSX2 Qt key token (letters/digits handled in code).
_RA_TO_QT_KEY = {
    "enter": "Return", "space": "Space", "escape": "Escape",
    "backspace": "Backspace", "tab": "Tab",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "shift": "Shift", "rshift": "Shift", "ctrl": "Control", "alt": "Alt",
    "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End",
    "pageup": "PageUp", "pagedown": "PageDown", "minus": "Minus",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}


def usb_mouse_button_source(mbtn: int, pointer_idx: int) -> str | None:
    """'Pointer-<idx>/LeftButton' etc for a captured mouse button (mbtn 1..3) on the
    given pointer index (USB port 1 -> 0, port 2 -> 1). None for an unmappable button."""
    name = _MBTN_TO_USB.get(int(mbtn))
    return f"Pointer-{pointer_idx}/{name}" if name else None


def usb_keyboard_source(ra_key: str) -> str | None:
    """'Keyboard/<QtKey>' for a captured RetroArch key name (capture_cmds._RA_KEYMAP),
    or None if unmappable. Letters -> uppercase, numN -> N."""
    if ra_key in _RA_TO_QT_KEY:
        return f"Keyboard/{_RA_TO_QT_KEY[ra_key]}"
    if ra_key.startswith("num") and ra_key[3:].isdigit():
        return f"Keyboard/{ra_key[3:]}"
    if len(ra_key) == 1 and ra_key.isalpha():
        return f"Keyboard/{ra_key.upper()}"
    return None


_USB_MOUSE_LABEL = {"LeftButton": "Mouse Left", "RightButton": "Mouse Right",
                    "MiddleButton": "Mouse Middle"}


def usb_source_label(source: str) -> str:
    """Friendly label for a stored [USB*] source ('Pointer-0/LeftButton' -> 'Mouse
    Left', 'Keyboard/Return' -> 'Return', 'Pointer-0' -> 'Pointer 0')."""
    if source.startswith("Pointer-"):
        if "/" in source:
            btn = source.split("/", 1)[1]
            return _USB_MOUSE_LABEL.get(btn, btn)
        return "Pointer " + source[len("Pointer-"):]      # the aim device itself
    if source.startswith("Keyboard/"):
        return source.split("/", 1)[1]
    return source
