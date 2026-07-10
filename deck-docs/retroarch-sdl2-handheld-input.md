# RetroArch handheld input on the Deck (sdl2 driver, Steam virtual pad) -- hard-won facts

Gathered 2026-07-10 building the on-the-go handheld RetroArch hotkey/pad feature
(`lib/ra_handheld_input.py`). All verified on-device on this Deck (RetroArch flatpak
`org.libretro.RetroArch`, stable). Sources at the bottom. READ THIS before touching RetroArch
handheld input again -- it cost a very long session to derive.

## 1. The Deck's built-in pad under ES-DE = the Steam VIRTUAL pad `28de:11ff`, sdl2 only
- Under gamescope, RetroArch's SDL sees the built-in controller as `28de:11ff` "Microsoft X-Box
  360 pad 0" (SDL GUID `030079f6de280000ff11000001000000`), a Steam-Input virtual gamepad on
  `js0`/`event10`. This pad is ONLY visible to SDL INSIDE the gamescope session (RetroArch). An
  SSH shell's SDL (`lib/devices.py sdl_devices`) sees only the RAW `28de:1205` "Steam Deck"
  (Steam Input off). But the 11ff EVDEV node (`event10`) IS readable from anywhere.
- The full, stable pad mapping works ONLY on the **sdl2 joypad driver**
  (`input_joypad_driver = "sdl2"`). Under **udev** the same virtual pad's BUTTON indices SHIFT
  between launches (a saved profile works once then breaks on relaunch; only the axes survive) --
  so udev is a dead end for this pad. The on-the-go rail flips `input_joypad_driver` to sdl2
  handheld, udev docked (X-Arcade reads raw).

## 2. RetroArch's sdl2 driver keys this pad by SDL GameController SEMANTIC indices (NOT raw evdev)
Verified from RetroArch's OWN "Set All Controls" capture. Do NOT assume raw evdev order.
- Buttons: `a=0 b=1 x=2 y=3` (RetroPad A/B/X/Y = SDL A/B/X/Y, no swap), `back/select=4 guide=5
  start=6 L3=7 R3=8 L1=9 R1=10`, d-pad `up=11 down=12 left=13 right=14`.
- Axes: `leftx=0 lefty=1 rightx=2 righty=3`, triggers `l2 = +4` (SDL TRIGGERLEFT), `r2 = +5`
  (TRIGGERRIGHT). (I wrongly assumed raw evdev order -- ABS_RX=3/ABS_RY=4/ABS_Z=2 -- and got the
  right stick + L2 wrong; the sticks are rightx=2/righty=3, L2=4.)

## 3. Manual `input_playerN_*` binds in retroarch.cfg OVERRIDE autoconfig profiles
The RetroArch docs say it plainly: "Manual bindings take precedence over autoconfig files." So a
stale/wrong `input_player1_*` set in retroarch.cfg WINS over any autoconfig profile (even an
exact vid:pid + name match). Consequences on this Deck:
- The global `input_player1_*` binds were leftover udev-era values (d-pad rotated, A/B + X/Y
  swapped, right stick unbound) and RetroArch used THEM, ignoring the sdl2 "Steam Virtual
  Gamepad" autoconfig. Adding/fixing an autoconfig profile did nothing.
- Correct fix = write the right `input_player1_*` binds DIRECTLY (what `ra_handheld_input`'s
  `_GAMEPAD` does, transient), OR clear the manual binds so autoconfig takes over. We do the
  former (transient global binds, restored docked).
- The two sdl2 profiles `Steam Virtual Gamepad.cfg` + `Steam Controller.cfg` BOTH claim vid:pid
  `10462:4607` (28de:11ff) but neither matches the pad's NAME "Microsoft X-Box 360 pad 0" -> a
  vid:pid tie that resolves to neither cleanly. Irrelevant once you set the manual binds.

## 4. "Menu dead but game works" = only Port-1's device controls the menu; the rig's Wii Nav grabs it
Input reached the game but the RetroArch MENU received nothing (no d-pad/touch/buttons). Cause:
by default ONLY the controller on Port 1's device index drives the menu, and the rig's virtual
`MAD Wii Nav` pad (`4d41:0001`, from the Wii-nav bridge) was taking that slot, bumping the Deck
pad off. Fixes (both work; we set the first, it's robust):
- `input_all_users_control_menu = "true"` -- ANY port can drive the menu.
- OR disable the Wii Nav bridge handheld / set Port 1 Device Index to the Deck pad.

## 5. config_save_on_exit=true is a footgun for a launch-hook that edits retroarch.cfg
With `config_save_on_exit = "true"` RetroArch REWRITES the whole retroarch.cfg on exit. That (a)
baked the stale binds in originally, and (b) can clobber what a game-start hook wrote / race a
still-open instance. Set `config_save_on_exit = "false"` -> retroarch.cfg is deterministic (only
our transient apply/restore + explicit user saves touch it). Trade-off: in-menu setting changes
need a manual Main Menu -> Configuration File -> Save Current Configuration.

## 6. Saving controller mappings on the flatpak
- `joypad_autoconfig_dir` defaults to `/app/share/libretro/autoconfig` (READ-ONLY in the
  flatpak) -> "Save Controller Profile" ERRORS. Point it at a writable dir
  (`~/.var/app/org.libretro.RetroArch/config/retroarch/autoconfig`, pre-populated) to save
  profiles. (We DON'T need this since we write manual binds directly.)
- "Save New Configuration" (Configuration File menu) writes a full snapshot to
  `config/<name>.cfg` (writable) even when profile-save fails -> a reliable way to CAPTURE the
  current binds. The user's ground-truth capture landed in `config/fbneo_libretro.cfg`.

## 7. The paddle-hotkey dead end (why we use gamepad combos, not the back paddles)
The Deck's back paddles are unmapped in Steam Input under ES-DE (emit nothing). Mapping them to
KEYBOARD keys works and ES-DE sees them, but Steam injects those keys SYNTHETICALLY -> RetroArch's
udev input_driver is blind to them (same as x11vnc/XTEST, see retroarch-vnc.sh). Reading them
needs input_driver=sdl2 or x11 -- both of which KILL the sdl2 joypad driver (the pad goes dead).
So you cannot have a stable pad AND synthetic-key paddle hotkeys at once. Resolution: keep the pad
on sdl2 and use gamepad COMBOS (a modifier button + a button); the hotkey buttons keep their
gameplay function when the modifier isn't held (`input_enable_hotkey` gates them).

Sources (verified 2026-07-10): docs.libretro.com/guides/controller-autoconfiguration/ ;
RetroArch #11549 (github.com/libretro/RetroArch/issues/11549, menu input tied to device index 1) ;
Steam Community "RetroArch Controller Issues Solution" (app 1118310) ; on-device SDL GameController
probe + evdev read of event10 + RetroArch's own Set-All-Controls capture (config/fbneo_libretro.cfg).
