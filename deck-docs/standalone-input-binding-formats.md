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

See [[../FIX-PLAN.md]]-style plan in the session plan file; capture pipeline =
`capture_cmds.py` + `GuiMadCaptureModal`; translator = `lib/madsrv/input_translate.py`.
