# Standalone emulator controller-binding formats (for MAD input mapping)

Reference for the per-emulator input-map feature (`<emu>.input_get/.input_set`,
`GuiMadPageEmuInputMap`). How each standalone stores ONE controller button
binding, so we can write bindings programmatically. Gathered 2026-06-15 from
official docs + verified against live configs on this Deck. Each emulator already
has a router-side config module (`lib/<emu>_cfg.py`) that knows its format — reuse
it. **Key fact:** the controller-router rewrites these at launch (`*_cfg.assign()`)
but PCSX2-style writers clone the EXISTING button layout and change only the
device index, so a per-button remap PERSISTS — verify this per emulator before
shipping its writer.

## PCSX2 (PS2) — DONE (Phase 0)
- File: `~/.config/PCSX2/inis/PCSX2.ini`, INI, `[Pad1]`/`[Pad2]`.
- Binding: `Cross = SDL-2/FaceSouth` → `SDL-<idx>/<SDLsource>`. Sources:
  FaceSouth/East/West/North, LeftShoulder, RightShoulder, +LeftTrigger,
  +RightTrigger, Back, Start, Guide, LeftStick, RightStick, DPadUp/…, and axes
  `+LeftX`/`-LeftY`/etc. Router (`lib/pcsx2_cfg._bind_template`) abstracts the
  index → remaps persist. Verified live + router-gate.
- Src: github.com/PCSX2/pcsx2 (Qt input). Booleans lowercase.

## Eden (Switch, Yuzu fork) — DONE (Phase 1, run 42/43)
- File: `~/.config/eden/qt-config.ini`, Qt INI, `[Controls]`.
- Binding: `player_0_button_a="engine:sdl,port:0,guid:<32hex>,button:M"`.
- **`button:M` is the SDL joystick BUTTON RANK, not the evdev code-0x130.** SDL
  enumerates only the buttons the pad actually reports, so absent ones (BTN_C
  0x132, BTN_Z 0x135 on most pads) are skipped and the rank shifts. Mapping used
  by `input_translate.sdl_button_index(code)`: A 0x130→0, B 0x131→1, X 0x133→2,
  Y 0x134→3, L 0x136→4, R 0x137→5, ZL 0x138→6, ZR 0x139→7, Minus 0x13A→8,
  Plus 0x13B→9, Guide 0x13C→10, LStick 0x13D→11, RStick 0x13E→12.
  Axes: `axis:N`, with `axis_x/axis_y`, `invert_*`, `deadzone`.
- **GUID note (corrected 2026-06-15):** the live `guid:` is a **CRC-based SDL
  GUID** (NOT the bare vid:pid I first noted). MAD's per-button writer
  (`eden_input_cmds.input_set`) does NOT compute or touch the GUID — it
  **preserves the on-disk `guid:`/`port:`** and rewrites only the `button:M`
  token (`_BTN_RE = re.compile(r"button:(\d+)")`). So no GUID derivation is
  needed and the device identity stays whatever Eden already wrote.
- **Controller type + console mode (MAD selectors, 2026-06-15):**
  `player_N_type` (in `[Controls]`) = the **integer index** of Eden/yuzu
  `Settings::ControllerType` (verified from eden `src/common/settings_input.h`):
  `0 ProController, 1 DualJoyconDetached, 2 LeftJoycon, 3 RightJoycon, 4 Handheld,
  5 GameCube` (then Pokeball/NES/SNES/N64/SegaGenesis). `use_docked_mode`
  (1=docked,0=handheld) lives in **`[System]`**, NOT `[Controls]`. ⚠️ Each of these
  has a paired `<key>\default=true|false` line; Eden IGNORES the stored value while
  `\default` is true, so MAD's `eden.selector_set` writes BOTH `<key>=<v>` and flips
  `<key>\default=false`. (`eden_input_cmds._selector_set`.)
- Router gate: **N/A** — `[systems.switch] router_skip = true`
  (`controller-policy.toml`), so `eden_cfg.assign()` never runs for Switch and
  there is no launch-time clobber; the remap persists by construction. (The
  EBUSY guard `proc_guard.emulator_running("eden")` still blocks writes while
  Eden runs, since Eden rewrites the file itself on exit.)
- Existing router writer: `lib/eden_cfg.assign`. Qt paired lines
  (`key\default=…` + `key=…`) — match only `key=` (`cfgutil.ini_replace`).
- Src: github.com/yuzu-emu/yuzu (input parser). Verified live + headless.

## Ryujinx (Switch) — DONE (Phase 1, run 43)
- File: `~/.config/Ryujinx/Config.json`, **JSON** (cfgutil is INI-only → uses the
  `lib/madsrv/ryujinx_json.py` load/write helper; Ryujinx rewrites the file on
  exit, so a full parse→modify→reserialize round-trip is byte-safe).
- Bindings live in the `input_config` array, one object per player
  (`player_index: "Player1"`…`"Player8"`, `"Handheld"`). Buttons are nested in
  two objects: `right_joycon` (A/B/X/Y, R, ZR, Plus) and `left_joycon`
  (L, ZL, Minus, + d-pad). KEY = Switch button (`button_a`, `button_r`,
  `dpad_up`, …); **VALUE = a `GamepadInputId` enum TOKEN string**, e.g.
  `"A"`,`"B"`,`"X"`,`"Y"`,`"LeftShoulder"`,`"RightShoulder"`,`"LeftTrigger"`,
  `"RightTrigger"`,`"Minus"`,`"Plus"`,`"Guide"`,`"LeftStick"`,`"RightStick"`,
  `"DpadUp"`… (NOT an index). Mapping by `input_translate.ryujinx_button(code)`
  (evdev code → token; note the two north/west face codes map "crossed" per the
  Switch face layout): 0x130→"A", 0x131→"B", 0x133→"Y", 0x134→"X",
  L 0x136→"LeftShoulder", R 0x137→"RightShoulder",
  ZL 0x138→"LeftTrigger", ZR 0x139→"RightTrigger", Minus 0x13A→"Minus",
  Plus 0x13B→"Plus", Guide 0x13C→"Guide", LStick 0x13D→"LeftStick",
  RStick 0x13E→"RightStick". (d-pad / sticks read-only in v1 — capture skips
  hats/axes, same scope as PCSX2.)
- **Controller type (MAD "Type" selector, 2026-06-15):** each `input_config[]`
  entry has `controller_type` = the Ryujinx `ControllerType` enum **NAME string**
  (verified `ControllerType.cs`): `ProController, Handheld, JoyconPair, JoyconLeft,
  JoyconRight` (also Pokeball). MAD `ryujinx.selector_set` sets this; a missing
  player slot is created cloned from Player 1 with an unbound device id.
- v1 remaps **Player 1 only** (the first `input_config` entry whose
  `player_index=="Player1"`, else index 0); Handheld is left as-is.
- Router gate: **N/A** — Ryujinx is NOT router-managed (no `[backends.ryujinx]`,
  no `ryujinx_cfg.py`; Switch is `router_skip=true`), so nothing rewrites the
  config at launch → the remap persists. EBUSY guard
  `proc_guard.emulator_running("ryujinx")` blocks writes while it runs.
- Writers: `lib/madsrv/ryujinx_input_cmds.py` (input), `lib/madsrv/ryujinx_cmds.py`
  (Settings: top-level `graphics_backend`/`res_scale`/`aspect_ratio`/
  `anti_aliasing`/`scaling_filter`/`enable_vsync`/`backend_threading`).
  ⚠️ One-time `.router-backup` taken before MAD's first write.
- Src: github.com/Ryujinx/Ryujinx (`Common/Configuration`, `GamepadInputId`).
  Verified headless (real Config.json sha256 unchanged after a no-op test;
  see session notes). Verified live config layout on this Deck.

### Ryujinx device id (pads → players) — DONE (run 44)
- Each `input_config[]` player entry has an `id` (which physical pad drives it),
  `backend = "GamepadSDL2"`, `controller_type`, and `player_index`
  (`Player1`…`Player8`, `Handheld`). The MAD **Controllers → pads → players** page
  (configure-once; `pads.get`/`pads.set` → `lib/madsrv/ryujinx_cfg.assign_devices`)
  rewrites only `id` per player, **preserving** the `left_joycon`/`right_joycon`
  button maps.
- **id format (verified):** `"{sdl_index}-{guid}"` where `guid` is the **.NET
  `Guid.ToString()`** of the 16-byte SDL GUID — i.e. the first three fields are
  little-endian and the last eight bytes are emitted as-is. So from the SDL GUID
  hex `bytes`: `d1=LE(b0..3)`, `d2=LE(b4..5)`, `d3=LE(b6..7)`, then
  `b8b9-b10..b15`. Examples (both live):
  `28de:1205` SDL GUID `03000000de2800000512000000026800` → `0-00000003-28de-0000-0512-000000026800`;
  `28de:11ff` → `0-00000003-28de-0000-ff11-000001000000`.
  Implemented as `ryujinx_cfg.ryujinx_id(index, sdl_guid)`.
- 🔴 **CRITICAL — Ryujinx ZEROES the SDL name-CRC (GUID bytes 2-3) (verified
  on-device 2026-06-15).** The live SDL GUID a real probe returns carries a
  name-CRC in bytes 2-3 (e.g. the DS4 `054c:09cc` → `03008fe54c050000cc09…`,
  CRC `8fe5`), but Ryujinx forms its id from the GUID with **bytes 2-3 = 0000**.
  So the DS4's working id is `0-00000003-054c-0000-cc09-000000006800` (note
  `00000003`, not `e58f0003`). The examples above hid this because they were
  reconstructed from config ids that were ALREADY CRC-zeroed — making the bare
  transform look correct against them while failing against any live-probed GUID.
  **`ryujinx_id` must `b[2]=b[3]=0` before the .NET transform** or the id never
  matches and Ryujinx reports "your current controller configuration is invalid."
  This is true for ANY SDL build (system OR Ryujinx's own bundled
  `~/Applications/publish/libSDL2.so`) — both compute the CRC; only Ryujinx
  suppresses it at runtime. Was the single root cause of a multi-round "invalid
  configuration" failure misdiagnosed as an index problem (the index was fine).
- ⚠️ **The index prefix MUST match** the device's live SDL index — Ryujinx
  `SDL2GamepadDriver.GetGamepad(id)` re-derives the id from the joystick at the
  parsed index and returns null on mismatch (so it is NOT GUID-only matching).
  We write the current `devices.sdl_devices()` index; if the connected set
  changes the index can shift → re-apply the order.
- **Now applied at LAUNCH, not configure-once (live 2026-06-15).** Because one
  Ryujinx config is shared by two contexts — docked via ES-DE (Steam Input OFF,
  raw DS4/Deck pads) vs on-the-go via Steam directly (Steam Input ON, virtual pad
  `28de:11ff`) — and Ryujinx has no fallback, a static binding can only ever serve
  one. So `es_systems.xml` wraps the Switch commands with `mad-switch-launch.py`,
  which binds the connected pads (in the MAD-stored priority) to the config the
  game reads at launch, then an ES-DE game-end hook restores the input to the
  resting on-the-go default on exit (SETTINGS preserved — input-only snapshot).
  The MAD pads page is now STORE-ONLY (`pads.set` just records the order). The
  index is computed in the launch session via Ryujinx's bundled SDL, so it matches
  what Ryujinx enumerates moments later. See `lib/switch_bind.py`.
- Src: Ryujinx `src/Ryujinx.Input.SDL2/SDL2GamepadDriver.cs`
  (`GenerateGamepadId` = `joystickIndex + "-" + guid`; `GetGamepad` index check).

### Ryujinx: NO fallback when a player's configured `id` pad is absent (verified 2026-06-15)
- **Verdict = (A): the player is left UNBOUND. There is NO auto-assign, NO
  "first available controller", NO fallback to the Steam/Deck virtual pad.**
  A single config bound to a specific pad will NOT switch to another pad when
  that pad is missing — the slot just gets no controller.
- Binding chain (mainline `7d158ac`, last commit 2024-09-30; canary 1.3.96 is
  based on this, same code):
  `NpadManager.ReloadConfiguration` (loops `_inputConfig` players) →
  `DriverConfigurationUpdate` → `NpadController.UpdateDriverConfiguration` →
  `_gamepad = GamepadDriver.GetGamepad(config.Id); return _gamepad != null;`.
  `SDL2GamepadDriver.GetGamepad(id)` does
  `GetJoystickIndexByGamepadId(id)` = `_gamepadsIds.IndexOf(id)` → **exact
  string match** of `"{index}-{guid}"` against the list of CURRENTLY-connected
  pads; not found ⇒ returns -1 ⇒ `GetGamepad` returns `null`. Back in
  `ReloadConfiguration`, `isValid==false` ⇒ `_controllers[index] = null` and
  the entry is dropped from `validInputs`. In `Update()` the loop guards
  `if (controller != null)` so a null slot emits a default (no-input) state.
  No code path substitutes another `id`.
- (B)/(E) answered: there is no "use first available" option anywhere.
  `GamepadsIds` (the connected list) is read ONLY by UI dropdowns
  (`InputViewModel`, GTK3 `ControllerWindow`) and the headless
  `--list-inputs`/`--input-id` arg — never to auto-pick a player's device.
  The config `id` (`InputConfig.Id`) is a fixed saved string.
- (D) HOTPLUG = YES, at runtime. `NpadManager` subscribes to
  `OnGamepadConnected`/`OnGamepadDisconnected`; each fires
  `ReloadConfiguration(_inputConfig,…)` which re-runs the SAME exact-id match
  against the new connected set. So a pad that comes back (same index+guid)
  re-binds; a pad whose id no longer matches stays unbound. Hotplug ≠ reassign.
- (F) Multi-pad pick = purely the saved per-player `id` exact match. SDL index
  order only matters because it is baked into the id prefix
  (`{index}-{guid}`); if connect order changes, the index shifts and the id no
  longer matches → that player goes unbound (must re-select in Input settings).
- Community confirmation (consistent with the source, not a separate fallback):
  users report "Ryujinx no longer recognizes the Steam Deck's gamepad" /
  "can't detect controller" after a reconnect/SteamOS update, and the fix is
  always to MANUALLY re-select the pad in Input settings (Load→Save) — never an
  automatic recovery. EmuDeck issue #1438. The 3rd-party "RyujinxLauncher"
  exists precisely to re-map ids when a BT pad connects in a different order —
  a gap that exists *because* Ryujinx has no native auto-remap.
- Implication for MAD: our `ryujinx_cfg.assign_devices` configure-once contract
  is correct — if the connected set changes we MUST re-apply the id order;
  there is no "Player 1 = whatever is connected" we can rely on. To make Player1
  always work we'd have to (re)write its `id` to the live first pad's
  `"{index}-{guid}"` at launch (router-style), since Ryujinx won't do it.
- Srcs (all read on the cloned mainline mirror git.ngni.us→git.ngram.ca
  /mirrors/Ryujinx @ 7d158ac):
  `src/Ryujinx.Input/HLE/NpadManager.cs` (ReloadConfiguration L119-171,
  DriverConfigurationUpdate L96-117, hotplug handlers L70-93, Update L211-244);
  `src/Ryujinx.Input/HLE/NpadController.cs` (UpdateDriverConfiguration L226-238);
  `src/Ryujinx.Input.SDL2/SDL2GamepadDriver.cs` (GetGamepad L156-173,
  GetJoystickIndexByGamepadId L75-81, GenerateGamepadId L48-73);
  `src/Ryujinx.Common/Configuration/Hid/InputConfig.cs` (Id L22).
  Community: github.com/dragoonDorise/EmuDeck/issues/1438; docs.ryujinx.app
  setup guide (no fallback documented).
- **Eden device pick** (same MAD page) reuses `eden_cfg.assign_devices` →
  `_eden_guid(vidpid)` + `_retarget` on the LIVE `[Controls]` `player_N_*` lines
  (only `guid:`/`port:` change; every `button:M` preserved). Router gate N/A
  (Switch router_skip); EBUSY while the emulator runs.

## RPCS3 (PS3) — clean, Phase 1
- File: `~/.config/rpcs3/input_configs/global/Default.yml` (EmuDeck path:
  `~/.config/EmuDeck/backend/configs/rpcs3/…`), YAML, `Player N Input:`.
- Binding: under `Config:` → `Cross: A`, `L1: TL`, `L2: LZ+`, d-pad `Hat0 X-`,
  sticks `LX+`/`RY-`. `Handler:` = `Evdev`|`SDL`, `Device:` = NAME+rank
  (`"PS4 Controller 1"`). Existing writer: `lib/rpcs3_cfg.assign`.
- Src: github.com/RPCS3/rpcs3. Verified live.

## Dolphin (GC/Wii) — clean-ish, Phase 1
- File: `~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/GCPadNew.ini`
  (+ `WiimoteNew.ini`), INI, `[GCPad1]`.
- Binding: `Device = evdev/1/<name>` (or `SDL/0/<name>`) + `Buttons/A = EAST`
  (evdev names: EAST/SOUTH/NORTH/WEST/START/SELECT) or backtick `` `Button E` ``
  (SDL). D-pad `D-Pad/Up = `, triggers `Triggers/L = `. Pipe `|` = OR.
  NOTE: existing `dolphin_cmds` only does Wii via `dolphin-wii-mode.sh`; GameCube
  pad writer is NEW.
- Src: github.com/dolphin-emu/dolphin. Verified live.

## Cemu (Wii U) — HARDER, Phase 2
- File: `~/.config/Cemu/controllerProfiles/controller{0-3}.xml`, XML.
- Binding: `<mappings><entry><mapping>25</mapping><button>40</button></entry>` —
  `<mapping>` = emulated-button code, `<button>` = SDL internal input code; both
  poorly documented (reverse-engineer from live + SDL enums). `<uuid>` =
  `<sdl_index>_<GUID>`, `<api>SDLController</api>`. Existing writer:
  `lib/cemu_cfg.assign` uses vid:pid TEMPLATES → per-button edits the template.
- Src: github.com/cemu-project/Cemu (sparse docs — CONFIRM codes before shipping).

## Supermodel (Model 3) — text but token map needed, Phase 2
- File: `~/.supermodel/Config/Supermodel.ini`, INI, `[ Global ]` (LAST section).
- Binding: `InputStart1 = "KEY_1,JOY1_BUTTON8"` — tokens `JOYn_BUTTONm`,
  `JOYn_YAXIS_NEG`, `JOYn_POV1_UP` (n = joystick number, m = button number).
  Comma list = OR. Booleans int 1/0. Confirm the router doesn't manage it +
  build the evdev→`JOYn_BUTTONm` token map.
- Src: github.com/trzy/Supermodel + bundled README §10. (Agent first thought
  GUI-only; the INI DOES take Input* bindings — verify on-device.)

## Model 2 (m2emu) — NOT FEASIBLE
- Bindings in `~/Emulation/roms/model2/CFG/<game>.input` = BINARY m2emu blob;
  `EMULATOR.INI [Input]` only has `XInput=1`/`RawDevP*` (no per-button keys).
  Delegates to Windows XInput. MAD cannot remap — surface a "uses the standard
  Xbox layout; remap in the emulator's menu" note instead.
- Src: ElSemi/Nebula README (2014).

## xemu (Original Xbox) — FEASIBLE as of xemu v0.8.133 (researched 2026-06-18)
xemu v0.8.133 (released 2026-01-20; feature merged 2025-12-25, PR #1516, fixes issue #136)
added FILE-CONFIGURABLE per-button + per-axis + keyboard remapping to `xemu.toml`.
Before v0.8.133 mappings were hardcoded / GUI-less → not file-remappable; **version-gate any writer**.
- Installed on THIS Deck: **0.8.136** (flatpak `app.xemu.xemu`, dated 2026-06-14) → feature present.
  Live `xemu.toml` already carries `[input] gamepad_mappings = [ {gamepad_id='…'}, … ]` (one entry
  per pad, GUIDs == the port1/port2 GUIDs) — format confirmed on-device.
- Config file: `~/.var/app/app.xemu.xemu/data/xemu/xemu/xemu.toml`. xemu writes only NON-DEFAULT keys
  (delta TOML via toml++). On load it strcmp-looks-up `gamepad_id`; a pre-seeded entry with the right
  GUID is found and used as-is, so an external writer need NOT have xemu create the entry first.
- THREE input surfaces under the `input` table:
  1. Port→device (ALREADY managed by `lib/xemu_cfg.py`): `input.bindings.portN = '<SDL GUID>'`
     + sibling `portN_driver` ('usb-xbox-gamepad' / DRIVER_S).
  2. Per-controller button/axis remap (NEW — the per-button surface MAD lacked):
     `input.gamepad_mappings` = TOML array-of-tables, one per physical pad, keyed by
     `gamepad_id` = SDL joystick GUID (SAME string as portN). Per entry:
       • `gamepad_id` (string GUID)
       • `enable_rumble` (bool, default true; replaces deprecated global `allow_vibration`)
       • `controller_mapping` sub-table: each Xbox control → an **SDL_GameControllerButton enum INDEX**
         (a=0 b=1 x=2 y=3 back=4 guide=5 start=6 lstick_btn=7 rstick_btn=8 lshoulder=9 rshoulder=10
          dpad_up=11 dpad_down=12 dpad_left=13 dpad_right=14); axes → **SDL_GameControllerAxis index**
         (axis_left_x=0 left_y=1 right_x=2 right_y=3 trigger_left=4 trigger_right=5);
         plus `invert_axis_left_x/left_y/right_x/right_y` bools. VALUES ARE ENUM INDICES, not names
         → writer needs the a→0/b→1/… lookup table.
  3. Keyboard→Xbox remap (separate): `input.keyboard_controller_scancode_map` = flat table, each
     Xbox button → SDL scancode int (a=4 b=5 x=27 y=28 start=40 back=42 …). NB: distinct from the
     xemu.app/docs/keyboard page (that is the USB-keyboard-PERIPHERAL monitor command, unrelated).
  - Also: `input.gamecontrollerdb_path` (optional custom SDL DB), `input.background_input_capture` (bool).
- Live file uses INLINE-table array form, not `[[input.gamepad_mappings]]` block form — equivalent TOML,
  but a writer must round-trip whatever xemu emits and key off `gamepad_id` (never clobber sibling pads).
  `xemu_cfg.py` today edits only `portN=` lines via regex/inifile — a real input-map writer needs proper
  TOML array-of-tables handling (the inifile shim is insufficient).
- In-app GUI = Settings → Input → "Input Mapping" (writes same keys); "Reset controls to default" =
  xemu_settings_reset_controller_mapping / reset_keyboard_mapping.
- MAD build path: mirror PCSX2/Eden — add 'xemu' to `_INPUT_MAP_EMUS` (standalones_cmds.py:66) + new
  `lib/madsrv/xemu_input_cmds.py` implementing `xemu.input_get`/`input_set` against
  `gamepad_mappings.controller_mapping` (GUID-keyed) with a >=0.8.133 version gate (else a
  "remap in xemu's Settings → Input" note). Capture caveat: xemu triggers are SDL axes — same
  axis-capture gap as the RetroArch L2/R2 item, so reuse the shared fix.
- Src (verified 2026-06-18): config_spec.yml @ tag v0.8.133
  https://github.com/xemu-project/xemu/blob/master/config_spec.yml ; commit aed1fa4ac6 ;
  PR https://github.com/xemu-project/xemu/pull/1516 ; behavior from ui/xemu-input.c, ui/xemu-settings.cc.

See [[../FIX-PLAN.md]]-style plan in the session plan file; capture pipeline =
`capture_cmds.py` + `GuiMadCaptureModal`; translator = `lib/madsrv/input_translate.py`.

## X-Arcade joystick = BTN_TRIGGER_HAPPY buttons → RA rank 11-14, SDL d-pad (verified 2026-06-19)

The X-Arcade Tankstick (045e:02a1, Xbox-360 mode, two byte-identical USB interfaces P1/P2) reports
its arcade STICK as four DIGITAL buttons `BTN_TRIGGER_HAPPY1..4` (evdev **0x2c0-0x2c3**), NOT as
the `ABS_HAT0X/Y` it ALSO exposes — that hat is DEAD/phantom (present in caps, but the stick fires
the HAPPY buttons). Face buttons = 0x130-0x13e (skips 0x132/0x135/0x138/0x139).

Two correct readings of the SAME stick, both confirmed on-device (empirical monitor matched 10/10):
- **RetroArch** reads it as numbered BUTTONS via its udev driver's keybit scan, which assigns
  indices over FOUR ranges in THIS order (verbatim from libretro `udev_joypad.c` `udev_add_pad`):
  `KEY_UP..KEY_DOWN`, `BTN_MISC(0x100)..KEY_MAX(0x2ff)`, `0..KEY_UP`, `KEY_DOWN+1..BTN_MISC`. The
  HAPPY buttons fall in range 2, after the 0x130-0x13e face buttons, so: HAPPY1→11, HAPPY2→12,
  HAPPY3→13, HAPPY4→14 (this is what `capture_cmds._btn_index_map` replicates). Ground truth = the
  loaded autoconfig
  `…/autoconfig/udev/Xbox_360_Wireless_Receiver.cfg`: `input_left_btn=11 right=12 up=13 down=14`
  (⇒ HAPPY1=left, HAPPY2=right, HAPPY3=up, HAPPY4=down). `capture_cmds._btn_index_map` already ranks
  them 11-14 via the BTN_MISC..KEY_MAX loop.
- **SDL standalones** (xemu/PCSX2/RPCS3/Ryujinx via the SDL GameController API) read it as a D-PAD
  via gamecontrollerdb GUID `030000005e040000a102000000010000`:
  `dpleft:b11, dpright:b12, dpup:b13, dpdown:b14` — direction order matches RA exactly.
  **EXCEPTION — Eden** uses the raw SDL JOYSTICK rank, not the GameController d-pad; its fixed
  `_EDEN_DPAD` (up:13 down:14 left:15 right:16) mis-binds the X-Arcade left/right (really 11/12), so
  MAD **refuses** an X-Arcade d-pad bind on Eden (guarded in `GuiMadPageEmuInputMap`) rather than
  silently mis-bind. (Deferred 2026-06-19 — X-Arcade-on-Switch is a rare combo.)

MAD capture (`capture_cmds.py`) therefore: widens `_on_button` to accept 0x2c0-0x2c3; suppresses
the dead ABS_HAT on any device exposing HAPPY (`_has_happy`, capability-keyed → order/co-fire
independent); and DUAL-EMITS a lone HAPPY press as BOTH `btn_indices` (the RA index) AND
`bind_token="h<dir>"` (the hat token the SDL standalones bind on a `kind=="hat"` row). RA hotkeys:
`retroarch_cmds._input_set_hotkey` takes an `index` (udev rank) honoured ONLY in the joypad branch,
AFTER the mouse branch (so the X-Arcade red-button mouse hotkey is never mis-routed).

CAVEAT — kernel ≥ 6.17 (SDL #14324): a future SteamOS jump to xpad on ≥6.17 flips the stick to a
REAL ABS hat; then `_has_happy` is empty and the existing `_on_hat`→`h0up` path takes over
(forward-compatible). Do NOT hard-code 11-14 in any writer — keep the rank computed. Deck kernel
today = 6.11.11-valve.

Sources (verified 2026-06-19): the loaded RA autoconfig on this box; SDL gamecontrollerdb
(github.com/mdqinc/SDL_GameControllerDB) GUID 030000005e040000a102000000010000; libretro udev
joypad button enumeration (github.com/libretro/RetroArch → input/drivers_joypad/udev_joypad.c);
SDL #14324 (github.com/libsdl-org/SDL/issues/14324).

## Sega Lindbergh loader [EVDEV] tokens: bind the BARE axis token, NEVER `_MAX`/`_MIN` (verified 2026-06-27)

lindbergh-loader (INPUT_MODE=2) binds each `lindbergh.ini [EVDEV]` key to a token built as
`<normalised-device-name>_<codename>`, where `normaliseName()` uppercases and turns
` / ( ) , = -` into `_` (so "Xbox 360 Wireless Receiver" becomes `XBOX_360_WIRELESS_RECEIVER`,
P2 dedup `..._2`). For an analog axis the loader auto-creates three companion inputs per axis:
the BARE token (e.g. `..._ABS_Z`) plus `..._ABS_Z_MAX` and `..._ABS_Z_MIN`.

KEY BUG (confirmed by reading the loader source, byte-identical to the v2.1.4 AppImage we run):
binding a digital button to the `_MAX` token STICKS the button on. The `_MAX`
(`ANALOGUE_TO_DIGITAL_MAX`) release path only calls `setSwitch(...)` while the axis value is at or
above the axis midpoint:

```c
// evdevInput.c:1366-1382  (maxEnabled set at :1609)
if (event.value >= ((absMin + absMax) / 2))           // gate
    if (maxEnabled) setSwitch(maxPlayer, maxChannel, scaled > 0.8 ? 1 : 0);
```

An X-Arcade trigger (LT=`ABS_Z`, RT=`ABS_RZ`; live: rest 0, max 255, midpoint 127) snaps 255->0 on
release; value 0 is below the midpoint so the block is skipped, `setSwitch(0)` never fires, button
stuck. The `_MIN` path (`:1348-1364`) is the mirror and equally broken (holds a min-resting axis
pressed at rest). No EV_SYN/periodic/initial reset clears it.

FIX: bind the BARE axis token (`..._ABS_Z`, no suffix). A bare token maps to `NO_SPECIAL_FUNCTION`,
sets `.enabled=1` and (because the JVS target name `PLAYER_x_BUTTON_n` lacks "ANALOGUE")
`isAnalogue=0` (`:1594-1604`), and runs the UNGATED digital path on every event
(`:1276 + 1341-1344`): `setSwitch(player, channel, scaled < 0.8 ? 0 : 1)`. Same press point
(scaled>=0.8, value>=204) as a correctly-working `_MAX`, but it releases cleanly at rest. No daemon
or virtual device needed. Note: the loader exposes a stable NAMED token only for the positive
(toward-max) direction; the negative direction is reachable only via an unstable path-based tech
name, so a genuinely min-resting (inverted) trigger as a digital button is unsupported. X-Arcade
LT/RT rest at 0 (press toward max), so the bare token is exactly right for all four (P1/P2 x LT/RT).

A loader UPGRADE is NOT a fix: v2.1.4 is the latest release and master's `evdevInput.c` is
byte-identical (frozen since 2026-01-27); no GitHub issue/PR addresses this. The documented
`ANALOGUE_DEADZONE` knob does not apply (it only affects the `isAnalogue==1` analogue-output path).

MAD implementation: `lib/lindbergh_capture.py` emits the bare token for an analog axis driven to its
MAX extreme in button mode; `lib/madsrv/lindbergh_cmds.py` `_migrate_stuck_triggers` strips a stale
`_MAX` suffix from `[EVDEV]` digital-button bindings on page load (self-heals existing inis, Save to
apply). Upstream-canonical fix would move the `_MAX`/`_MIN` digital `setSwitch` out of the midpoint
guard, but we run the prebuilt AppImage (no build).

Source: github.com/lindbergh-loader/lindbergh-loader `src/lindbergh/evdevInput.c` (master == v2.1.4),
read 2026-06-27.

### Lindbergh D-pad (controller hat) = `_MIN`/`_MAX`, NOT bare (2026-06-28)

A controller D-pad usually reports as a HAT axis `ABS_HAT0X/0Y` (codes 0x10-0x17), range -1..1, rest 0.
Unlike a trigger, a hat rests at its MIDPOINT, so the loader's `_MIN`/`_MAX` digital tokens release
correctly (the midpoint gate at evdevInput.c:1348-1382 clears at rest 0) and are the RIGHT token; the
bare token is asymmetric for a hat (the bare path fires only at +1). So a hat D-pad binds by DIRECTION:
`..._ABS_HAT0X_MIN` = left, `_MAX` = right; `..._ABS_HAT0Y_MIN` = up, `_MAX` = down. Capture in
`lib/lindbergh_capture.py` `_read` emits `_MIN` at value -1 and `_MAX` at +1 for any ABS_HAT axis (the
trigger move-from-rest guard never fires for a hat's +-1 range, so without this branch a D-pad bound
nothing). A pad whose D-pad is `BTN_DPAD_*` (EV_KEY) is captured by the normal key path. NOTE: the
trigger `_MAX`->bare migration (`_migrate_stuck_triggers`) EXCLUDES `ABS_HAT*` so it never rewrites a
legit hat binding. Added 2026-06-28.

### Per-game Lindbergh quit combo (reuses the hold-to-quit machinery, 2026-06-28)

Lindbergh quit combos are PER GAME because lightgun and non-lightgun games use different peripherals
(a Sinden gun is a MOUSE and cannot press the default pad combo BTN_SELECT+START = 314,315, so gun games
had no working quit). Stored as a flat scope key `[quit_combo.lindbergh-<titleid>]` in
`controller-policy.local.toml` (titleid = the game dir stem, e.g. `vf5`; bare-key-safe with a hyphen).
This reuses the EXISTING machinery with no changes to the quit-combo functions: the MAD Lindbergh input
page captures via the same `GuiMadCaptureModal` "combo" mode (which already accepts mouse buttons
0x110-0x114 for the gun) and writes via the existing `policy.set_quit_combo` (scope=`lindbergh-<titleid>`)
/ `policy.clear_quit_combo` (system=`lindbergh-<titleid>`). At launch, `hooks/game-start/quit-combo-watcher.sh`
passes `--system lindbergh-<titleid>` so `_read_quit_combo` selects that game's combo, while `--quit-cmd`
stays the real lindbergh quit. `_read_quit_combo` LAYERS a hyphenated key: per-game
`[quit_combo.lindbergh-<titleid>]` overrides the system-wide `[quit_combo.lindbergh]` overrides the
global default (so the global Quit-combo page's lindbergh tile sets the all-games default and an existing
`[quit_combo.lindbergh]` is honoured, not orphaned; the want_kbd keyboard-watch is derived from the
resolved combo). The watcher already watches
mouse nodes (`_all_input_event_nodes` includes `is_mouse`), so a gun (mouse-button) combo fires at
runtime. The combo is written IMMEDIATELY on capture (like the global quit-combo page), separate from the
Lindbergh page's buffered ini SAVE/CANCEL.

### Per-game per-pad "pads -> players" + seamless fallback (non-lightgun, 2026-06-28)

Because the loader binds [EVDEV] by device NAME, a binding made for one pad does NOT work on another, so
a missing controller would mean a dead player. To make input seamless regardless of which pad is plugged
in, non-lightgun games get a per-game per-pad system (`lib/lindbergh_pads.py`):
- Each candidate pad's control map is captured ONCE, slot-agnostic, and stored (with a priority order) in
  a sidecar `<game>/lindbergh-pads.json`: `{version, priority:[tag...], pads:{tag:{control:codename}}}`.
  `tag` = the loader tag (`lindbergh_capture.loader_tags()` = `san(name)[+ "_<rank>"]`, the same dedup the
  loader uses); `control` = a JVS control WITHOUT the player prefix (BUTTON_1..8, BUTTON_UP/DOWN/LEFT/RIGHT,
  BUTTON_START, COIN, BUTTON_SERVICE).
- At LAUNCH a game-start hook (`hooks/game-start/lindbergh-pads-apply.sh` -> `python3 -m lib.lindbergh_pads
  apply <gamedir>`) MATERIALIZES the ini: resolve the connected pads by priority into player slots, write
  `PLAYER_N_<control> = "<tag>_<codename>"` for each, blank unassigned slots. The connected pads fill the
  slots in priority order, so an absent top-priority pad just hands its slot to the next one (no reconfig).
- Backup/restore: materialize backs the ini up to `<ini>.mad-restore` (only if absent, so a missed restore
  keeps the canonical); the game-end hook (`lindbergh-pads-restore.sh`) reverts ONLY the [EVDEV] section
  (`_splice_evdev`) so MAD Settings edits (region/resolution/crosshair) made meanwhile are never clobbered.
- Opt-in per game: with no sidecar (or no configured pad connected) the ini is left untouched, so games
  bound the classic per-PLAYER way are unaffected. Identical pads (two X-Arcade ports) are best-effort:
  their `_2` rank is enumeration-order, so a reboot/replug can swap them (surfaced in the page).
- RPCs (dedicated, NOT the shared pads page): `lindbergh.pads_get` / `pads_set_order` (priority) +
  `lindbergh.pad_load` / `pad_bind` / `pad_clear` (per-pad control map). UI: GuiMadPageLindberghPads
  (priority + "Make Player 1") + GuiMadPageLindberghPadMap (per-pad capture), reached via the game picker
  in `target="pads"` mode (Standalones -> Sega Lindbergh -> Controllers).
