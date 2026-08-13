# Prior art for "controller router + control panel" + gamepad-navigable config UI
Researched 2026-06-04. We already have a bespoke router (controller-router.py + controller-policy.toml + tkinter router-config-gui.py). Question = what to REUSE for UI/nav/theming + consolidation, NOT a rewrite.

## Routing / remap daemons (concept reference, GPL = no code copy into anything we'd ever ship closed)
- evsieve (KarsMulder, GPL-3.0, Rust). Low-level evdev->uinput mapper with "domain" tagging to SPLIT/route events from N input devices to N virtual devices. Closest conceptual match to our routing core. CLI-only, no GUI. https://github.com/KarsMulder/evsieve
- sc-controller (Ryochan7 fork, GPL-2.0, Python 95% + C). Daemon+GUI split, uinput virtual pads (x360 emu), PER-APPLICATION auto profile switching (active-window). GTK3 GUI is MOUSE-driven, NOT gamepad-navigable. ARCHIVED Jan 2024. Best architecture reference for daemon/GUI split + profile autoswitch. https://github.com/Ryochan7/sc-controller
- input-remapper (sezanzeb, GPL-3.0, Python). Client-server daemon, per-DEVICE presets, AUTOLOAD preset on device hotplug, macros, evdev->uinput. GTK GUI mouse-driven. Good reference for per-device preset autoload model. https://github.com/sezanzeb/input-remapper
- AntiMicroX (GPL-3.0, C++/Qt). Pad->kbd/mouse mapper. Qt GUI mouse-driven. Less relevant (we don't need pad->kbd).

## Gamepad-navigable / 10-foot UI toolkits
- Dear ImGui (ocornut, MIT). Has built-in gamepad nav: ImGuiConfigFlags_NavEnableGamepad, SDL2/SDL3 backend feeds controller. MIT = freely cherry-pickable. C++ (cimgui/pyimgui bindings exist but heavier). Strong candidate for a NEW SDL overlay GUI if we leave tkinter; gives D-pad nav + theming for free. https://github.com/ocornut/imgui (issues #787 nav, #6559 multi-pad)
- pygame-gui (MIT, Python). JSON-themeable widgets on pygame; UIManager handles focus. BUT focus nav is keyboard/mouse-centric; D-pad nav must be hand-wired (feed pad -> Tab/arrow). Pip dependency. Plausible if staying Python and want themed widgets.
- tkinter (what we have): no native gamepad nav. Must poll SDL/evdev and synth focus moves. Keeps zero-pip footprint (stdlib). Verdict: KEEP for now; reuse don't rewrite.

## Decky Loader (could the control panel BE a Decky plugin?)
- decky-loader: GPL-2.0. Injects React/TS components into the Steam UI; optional Python backend, 2-way TS<->Python. Runs in BOTH Game Mode (Quick Access Menu) and Desktop. Needs installer .desktop, ports 1337/8080, CEF. https://github.com/SteamDeckHomebrew/decky-loader
- @decky/ui (decky-frontend-lib): LGPL-2.1. React components mirroring Steam's own UI; gamepad-navigable BECAUSE Steam UI is. NOT a standalone toolkit - only usable inside the Steam/CEF React runtime. https://github.com/SteamDeckHomebrew/decky-frontend-lib
- Verdict: a Decky plugin gets gamepad nav + native Deck look "for free" and lives in the Steam overlay - but that's the WRONG host for us. Our panel lives in/around ES-DE (our patched fork), not the Steam overlay. Toolchain (Node/pnpm/React/TS) is a full rewrite away from Python. CONCEPT-ONLY unless we re-home the panel into Game Mode.
- Existing controller Decky plugins: ControllerTools (jfernandez, battery/connection only), per-game Steam Input on/off (RomM Sync), Controller Layout Editor (Steam native). None do multi-physical-pad routing. No direct prior art there.

## Per-game / multi-pad assignment prior art (emulator frontends)
- Batocera: manual joystick player assignment by VID/PID, or USB PORT LOCATION when VID/PID collide (two identical encoders). Per-game pad order. THIS is the cleanest model to mirror in our policy.toml (already VID/PID/port based). https://wiki.batocera.org/configure_a_controller , automatic_controller_layouts
- ES-DE: input config is per-GUID (es_input.xml); two identical pads share one config (a limitation we already route around). --force-input-config. No per-game pad assignment.
- X-Arcade: XINPUT mode (Tankstick 2025 fw) -> shows as Xbox Controller 1/2.

## gamescope/Game-Mode gotchas for a custom GUI (verified pain points)
- Non-Steam apps don't gain FOCUS under gamepadui+gamescope unless Steam launched with -steamos3 -steamdeck (steam-for-linux #8513). -steamdeck globally enables Steam Input -> one pad seen as two.
- XWayland keyboard/focus quirks under gamescope (gamescope #876/#1460/#1902); --backend sdl sometimes fixes input. Relevant: a raw tkinter window in Game Mode is fragile; Desktop Mode is safer for our panel, or go Decky to live inside Steam's own focus model.
