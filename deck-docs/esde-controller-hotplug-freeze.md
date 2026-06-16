# ES-DE / SDL controller-hotplug freeze (DS4/DualSense over Bluetooth)

_Investigated 2026-06-16 (on-device repro by user; root cause from bundled SDL source + SDL docs)._

## Symptom
Connecting a Bluetooth DualShock4 / DualSense while ES-DE (and the MAD panel, which is
native C++ inside the same process) is open **freezes the whole ES-DE UI for ~5 s**.

## Root cause (CONFIRMED)
It is **ES-DE's own SDL on the main render thread**, NOT the MAD backend daemon.
- `es-core/src/InputManager.cpp:67` `SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER)` with **no joystick hints set**.
- On a BT hotplug, SDL's **HIDAPI PS4/PS5 driver** opens the pad and does a slow identity/feature-report
  read **over Bluetooth** (~5 s for the first read). This runs during `SDL_JoystickUpdate`/
  `SDL_GameControllerOpen` (`InputManager.cpp:675`) **on the main thread** → UI blocks.
- The MAD daemon (`lib/devices.py`) is a **separate process with its own SDL** — its owner/reader
  + `_SDL_CACHE` fix (committed) only addresses the daemon's "preview timed out on 2nd-DS4 connect",
  NOT this UI freeze. Different layer.

## What does NOT work
- `SDL_HINT_JOYSTICK_THREAD` is **Windows-only** in SDL2 (per the SDL2 wiki) → no effect on Linux.

## ON-DEVICE RESULT (2026-06-16) — Option A applied, PARTIAL, accepted as-is
With `HIDAPI_PS4/PS5=0` (below) the freeze is **noticeably shorter but NOT gone** — the slow
PS4 HIDAPI identity/feature-report read is eliminated, but a residual stutter remains. Most
likely the residual is SDL's **general HIDAPI device rescan** (`SDL_hidapi` polls/enumerates all
HID devices on the bus to decide which driver owns the new device) still running on the main
thread during `SDL_JoystickUpdate` on hotplug — disabling the PS4/PS5 *drivers* doesn't disable
the HIDAPI *rescan*. User accepted the reduced freeze as good enough (2026-06-16). If revisited:
**Option C** = `SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI, "0")` disables HIDAPI entirely (no rescan →
should remove the residual) but routes ALL pads through evdev, re-mapping every controller in
ES-DE — bigger blast radius, needs its own on-device pass. Not done.

## Fix (APPLIED — Option A, low-risk, scoped)
In `InputManager::init()` BEFORE `SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER)`:
```cpp
SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS4, "0");   // DS4 -> kernel evdev driver (fast open)
SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5, "0");   // DualSense too
```
Verified in the bundled SDL 2.32.10 (`external/SDL`): `SDL_hidapi_ps4.c:175` / `ps5.c:270` —
each driver's `IsEnabled()` is `GetHintBoolean(HIDAPI_PS4, GetHintBoolean(HIDAPI, default))`, so
setting the PS4/PS5 hint to "0" disables just that HIDAPI driver and SDL falls back to the Linux
evdev joystick driver (instant open, no BT feature read → no freeze). Hint defs:
`include/SDL_hints.h:969` (PS4) / `:1004` (PS5).

**Scope/tradeoffs:** only PlayStation pads route to evdev (other controllers keep HIDAPI). ES-DE then
sees the DS4 via its evdev GUID/mapping (acceptable: ES-DE menus are navigated with the Wiimote Pro /
Steam Deck pad here, and SDL's built-in gamecontrollerdb still maps the evdev DS4). The **router /
launch-time binding is unaffected** — `mad-switch-launch` / `switch_bind` enumerate via the MAD
daemon's separate SDL (HIDAPI still on there), so the DS4 launch GUID doesn't change.
Hints are per-process (`SDL_SetHint` is not an env var), so the daemon child is unaffected.

**Must be on-device tested** — the freeze is not reproducible headlessly (no display, and it needs a
real BT pad). Alternative if PS4/PS5-scoping is insufficient: `SDL_HINT_JOYSTICK_HIDAPI=0` (disables
HIDAPI for ALL controllers in ES-DE — broader, re-maps every pad).

## Sources
- SDL2 SDL_HINT_JOYSTICK_THREAD (Windows-only): https://wiki.libsdl.org/SDL2/SDL_HINT_JOYSTICK_THREAD
- SDL2 SDL_HINT_JOYSTICK_HIDAPI: https://wiki.libsdl.org/SDL2/SDL_HINT_JOYSTICK_HIDAPI
- RetroArch SDL2 BT controller slowdown/regression (prior art: prefer udev/evdev over sdl2-hidapi):
  https://github.com/libretro/RetroArch/issues/18535 ; https://docs.libretro.com/guides/input-controller-drivers/
- Bundled SDL 2.32.10 source: ~/esde-build/ES-DE/external/SDL/src/joystick/hidapi/SDL_hidapi_ps4.c:175, ps5.c:270
