#!/bin/sh
# Wrap the native Linux Supermodel (flatpak) to:
#   - filter SDL2's joystick enumeration down to ONLY the DualSense, so the
#     PS5 controller becomes JOY1 in Supermodel.ini bindings (otherwise the
#     Steam Deck virtual controller / Xbox 360 wireless receiver / Sinden
#     joystick interfaces would shadow it)
#   - enable HIDAPI native PS5 driver for better DualSense support
#
# X-Arcade isn't a joystick to SDL (it presents as a keyboard), so it works
# alongside the player pads via the KEY_* bindings already in Supermodel.ini.
#
# The keep-list is now DYNAMIC: the controller-router computes which connected
# player pads to keep visible (config-driven [backends.supermodel] pad_classes,
# PS4 treated like DualSense; Deck as handheld fallback). Falls back to the old
# hardcoded DualSense filter if the router call yields nothing.
#
# Forwards every argument to the flatpak.
EXCEPT="$(/home/deck/Emulation/tools/launchers/controller-router.py sdl-ignore model3 2>/dev/null)"
[ -z "$EXCEPT" ] && EXCEPT="0x054c/0x0ce6"
exec /usr/bin/flatpak run \
    --env=SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="$EXCEPT" \
    --env=SDL_JOYSTICK_HIDAPI_PS5=1 \
    com.supermodel3.Supermodel "$@"
