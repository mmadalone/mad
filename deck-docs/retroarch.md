# RetroArch — cached facts (Steam Deck / EmuDeck flatpak)

Config root in use (flatpak): `~/.var/app/org.libretro.RetroArch/config/retroarch/`
- `retroarch.cfg` = global config. `config/<CoreName>/` = override dir. `joypad_autoconfig_dir = /app/share/libretro/autoconfig` (bundled, upstream profiles). `input_joypad_driver = "udev"`.

## Per-player `input_playerN_analog_dpad_mode` (analog stick → d-pad)
Source: RetroArch input config + on-box confirmation, 2026-06-05. Ref: https://docs.libretro.com/guides/retroarch-controller-configuration/
- Values: `"0"` = none (stick stays analog-only) · `"1"` = **Left Analog → d-pad** · `"2"` = Right Analog → d-pad · `"3"/"4"` = forced variants (also remap d-pad→analog).
- **Per-player**, independent per port. Mode `"1"` is ADDITIVE: the stick drives the d-pad AND analog still passes to the core (so it's safe even on analog systems — confirmed by P1 being global `"1"` here with no N64/PSX regressions).
- D-pad-only cores (FBA/FBNeo, arcade, NES…) read the RetroPad **d-pad**, NOT analog — so a player's left stick is DEAD in those games unless that player's `analog_dpad_mode = "1"`. This is per-player, NOT pad/autoconfig-related (any pad on a `"0"` port fails). The DualSense udev autoconfig is correct (`input_l_x_plus_axis="+0"`, `input_l_y_plus_axis="+1"`).
- This box's default `retroarch.cfg`: P1=`"1"`, P2–P16=`"0"` → only P1's stick worked in arcade. Fix for #41 (FBA): per-core override (below).

## Override file precedence / cascade
Source: https://docs.libretro.com/guides/overrides/ + on-box empirical confirmation, 2026-06-05.
- Auto-loaded in order, each LAYERED on top of the previous (they CASCADE, not replace):
  1. global `retroarch.cfg`
  2. per-core: `config/<CoreName>/<CoreName>.cfg`  (e.g. `config/FinalBurn Neo/FinalBurn Neo.cfg`) — applies to ALL content of that core
  3. per-content-directory: `config/<CoreName>/<contentDirName>.cfg`  (e.g. `config/FinalBurn Neo/fba.cfg` for ROMs in `~/ROMs/fba/`)
  4. per-game: `config/<CoreName>/<romBasename>.cfg`  ← the controller-router writes its reserved_device/joypad_index here per launch
  5. remaps `config/remaps/<CoreName>/<rom>.rmp` (highest)
- **Empirical proof of cascade on this box:** `config/FinalBurn Neo/fba.cfg` (content-dir, sets lightgun device type) coexists with the router's per-game overrides and BOTH apply (FBA lightgun works). So a per-core override + the router's per-game override compose with no collision as long as they set different keys.
- **`<CoreName>` for FBNeo = `FinalBurn Neo`** (the dir + the override filename). FB Alpha 2012 = `FB Alpha 2012`. (The router maps `fba` system → these core dirs.)
- Per-game exceptions via the RA UI: Quick Menu → Overrides → Save Game Overrides (writes layer 4, wins over the core override).

## Controller-priority family tokens (how the router picks a pad per port)
Source: our code (lib/routing.py, lib/mad_config.py), 2026-06-29.
- The MAD "Controller Priority" list (per system / per collection) reorders controller FAMILIES, not individual pads. A port `ports` token (e.g. "DualSense", "Xbox") matches a connected pad two ways: the pad's evdev/SDL name contains the token (case-insensitive substring) OR the pad's family equals the token, classified by vendor:product id in `routing.family_of`.
- Why the family-id path matters: a DualShock 4 enumerates with the GENERIC name "Wireless Controller" (vid 054c:09cc), which contains no family word. Before the family-id match, a lone DS4 was never picked by any token and the X-Arcade grabbed P1+P2 instead (on-device repro 2026-06-29, racing collection). Now the "DualShock 4" token matches it by id.
- "DualSense" and "DualShock 4" are SEPARATE families (both vendor 054c). DS4 pids {05c4, 09cc, 0ba0} to "DualShock 4"; DS5 pids {0ce6, 0df2} and any other 054c default to "DualSense". Order the two rows to set which is P1 vs P2 (cascade: P1 takes the higher family, P2 falls through to the next). The reserve value is per-device vid:pid (`reserve_value`), so RetroArch binds the two distinct pids to distinct ports.
- LIMITATION: this separates by MODEL only. Two pads of the SAME model (two DS4s, two DS5s) share one vid:pid and cannot be ordered this way; use a device PIN (Players page) for deterministic per-physical-unit assignment. Pins resolve BEFORE the family priority.
- Standalone emulators (PCSX2/xemu/rpcs3/eden/cemu) still group both Sony pads by vid:pid `pad_classes` (they bind by class, not family name); the split is the RetroArch priority path only.

## Mouse-button SYSTEM HOTKEYS (`input_*_mbtn`) — confirmed working at runtime
Source: on-box test, 2026-06-18 (X-Arcade red button = `BTN_MIDDLE`/274 = RA mbtn `"3"`). Ref: https://docs.libretro.com/guides/retroarch-controller-configuration/
- RA hotkey keys have an `_mbtn` variant (`input_exit_emulator_mbtn`, `input_menu_toggle_mbtn`, `input_enable_hotkey_mbtn`, …). They DO fire at runtime — a mouse button can drive a *system hotkey*, not just the lightgun trigger.
- **BUT** RA polls hotkeys on **port 0 (player 1) only** → a mouse-button hotkey reads the device at `input_player1_mouse_index`. The red button worked ONLY after P1's mouse index pointed at the X-Arcade trackball; P2/P3 mouse indices are NOT consulted for hotkeys.
- mbtn numbering: `1`=left(`BTN_LEFT`/0x110), `2`=right(0x111), `3`=middle(0x112), `4`=side, `5`=extra.
- RA has **no native mouse-by-identity** pin — unlike joypads (`input_playerN_reserved_device` = vid:pid), mice take ONLY a numeric `input_playerN_mouse_index`. That index is RA's udev mouse-enumeration order (BTN_LEFT + ABS_X/REL_X devices, sorted by sysfs path) and shifts on replug → re-derive it at launch from the stable vid:pid (cf. `lib/devices.py:_ra_mouse_order` / `detect_sinden_mouse_indices`).
- **udev JOYPAD button index** (the `input_<name>_btn` numeric value): NOT `code-0x130`. `udev_add_pad` (libretro `input/drivers_joypad/udev_joypad.c`) assigns `button_bind[i]=buttons++` scanning keybits in FOUR ranges **in this order**: (1) `KEY_UP..KEY_DOWN`, (2) `BTN_MISC(0x100)..KEY_MAX`, (3) `0..KEY_UP`, (4) `KEY_DOWN+1..BTN_MISC`. So the index = a device's **rank among ALL present keybits in that scan order**, not just the 0x130 face range. Consequences: a pad presenting buttons below 0x130 (e.g. the **Steam Deck pad**: `BTN_THUMB` 0x121/0x122, `BTN_BASE` 0x126 — loop 2 visits them before 0x130) shifts every face-button index up; the X-Arcade (no sub-0x130 buttons, skips 0x132/0x135) has `BTN_NORTH` 0x133 = index **2** (matches its autoconfig `input_y_btn="2"`). `BTN_DPAD` (0x220-0x223) sorts AFTER 0x130 in loop 2. Replicate the full order to compute a correct rebind index (`lib/madsrv/capture_cmds.py:_btn_index_map`). Source: libretro master `udev_joypad.c` `udev_add_pad`, verified 2026-06-18. https://github.com/libretro/RetroArch/blob/master/input/drivers_joypad/udev_joypad.c
- `config_save_on_exit` is OFF on this box → RA does NOT persist in-menu config changes on exit (file edits stick; menu edits don't).
