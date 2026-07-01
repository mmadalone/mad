# PCSX2 (PS2) PCSX2.ini stored-value encodings (for a non-corrupting MAD settings editor)

Verified 2026-06-14 against PCSX2 **source** (GitHub `PCSX2/pcsx2` master) and the
**live config** this PCSX2 build wrote on this Steam Deck.

- Live config path: `~/.config/PCSX2/inis/PCSX2.ini`  (AppImage `~/Applications/pcsx2-Qt.AppImage`)
- Internal binary / process name (pgrep -x): **`pcsx2-qt`** (Exec= in `net.pcsx2.PCSX2.desktop`)
- Format: INI, `Key = Value` (spaces around `=`), `[Section]` and slash-namespaced
  sections like `[EmuCore/GS]`, `[EmuCore/Speedhacks]`. LF endings. Blank line(s)
  between sections.
- **Booleans = lowercase `true` / `false`** (e.g. `VsyncEnable = false`, `EnableFastBoot = true`).
  NOT `1/0`, NOT `True/False`. (Contrast: Dolphin uses `True/False`.)

## [EmuCore/GS] Renderer  — GSRendererType, SPARSE SIGNED int code (NOT a 0-based index)
Source: `enum class GSRendererType : s8` in `pcsx2/Config.h`:
```
Auto = -1, DX11 = 3, Null = 11, OGL = 12, SW = 13, VK = 14, DX12 = 15, Metal = 17
```
Stored as the integer code STRING. Live value `Renderer = 14` = **Vulkan (VK)**.
Default `Auto = -1`. Because codes are sparse/negative, this is a STRING-token enum
for our contract: write_mode="option", options_stored = the code strings
("-1","12","13","14","3","15"), index round-trips. NEVER write str(index).
Curated user-facing subset (Linux): Auto(-1), Vulkan(14), OpenGL(12), Software(13).
(DX11/DX12 are Windows-only; Metal is macOS-only; Null is debug — omit from the menu,
but get() must prepend the current code if it isn't in the curated list so it round-trips.)

## [Pad1..N] controller device prefix = "SDL-N" is PCSX2's SDL PLAYER INDEX (NOT the raw joystick index) -- CORRECTED 2026-06-30

PCSX2 binds each pad button as `SDL-{N}/{Button}` (e.g. `Cross = SDL-3/FaceSouth`). The
`N` is NOT the 0-based SDL joystick enumeration order. It is PCSX2's per-controller SDL
**player index**, derived at runtime:
- PCSX2 calls `SDL_GetGamepadPlayerIndex` / `SDL_GetJoystickPlayerIndex` for each opened
  device. If that returns -1 or collides with an already-used id, PCSX2 assigns the first
  free id from 0 (`GetFreePlayerId()`), logging "...player ID X, which is invalid or in
  use. Using ID Y instead." Source: `pcsx2/Input/SDLInputSource.cpp`
  (https://raw.githubusercontent.com/PCSX2/pcsx2/master/pcsx2/Input/SDLInputSource.cpp).
- The player id depends on connection order AND the controller's LED-based player index,
  so it is NOT stable across sessions. Official docs:
  https://pcsx2.net/docs/configuration/controllers/ . There is NO stable identifier
  (no GUID / name binding) in PCSX2; open feature request #11816
  (https://github.com/PCSX2/pcsx2/issues/11816).
- CRITICAL: **non-gamepad joysticks also consume an SDL-N in the same namespace and
  OFFSET the gamepads.** PCSX2's `SDL_EVENT_JOYSTICK_ADDED` handler opens any device where
  `SDL_IsGamepad()` is false (light guns, arcade sticks) as a raw joystick. So a
  `SDL_GAMECONTROLLER_IGNORE_DEVICES` blocklist (GameController layer only) does NOT hide
  such a device from PCSX2's joystick layer.

ON-DEVICE EVIDENCE (this rig, `~/.config/PCSX2/logs/emulog.txt`, 2026-06-30): with the full
rig connected, PCSX2 numbered SDL-1=SindenLightgun, SDL-2=DS4, SDL-3=DualSense, SDL-4=Deck,
SDL-5/6=X-Arcade(Xbox360), SDL-7=MAD Wii Nav. The Sinden gun (returned player id -1 -> got
id 1) shifted every gamepad up by one and there is NO SDL-0.

IMPACT ON OUR BINDER (`lib/pcsx2_cfg.py` `assign` / `assign_devices`, `encode_auto=lambda d,
rank: d.index`): it writes `SDL-<raw joystick index>`, which does NOT match PCSX2's player
index whenever any non-gamepad joystick (Sinden) or LED-player-index controller is present.
Result: Pad1 was bound to SDL-1 = the light gun, Pad2 to a nonexistent SDL-0 -> NO pad input
(only a game whose pad happened to align worked). The old docstring claim "robust even when
Sinden guns occupy SDL slots / the Deck is usually SDL-0" is WRONG and must be replaced.
FIX DIRECTION (needs on-device confirmation): make PCSX2 see only the bound pads and/or
match its player-index assignment; raw-joystick devices must be hidden at the joystick
layer, not just the GameController layer. RPCS3 is immune because it binds by device NAME.

## [EmuCore/GS] upscale_multiplier  — FLOAT (CORRECTED 2026-07-01: type is float, range up to 12x)
Source: `Pcsx2Config::GSOptions` `float UpscaleMultiplier` (default 1.0), key
`"upscale_multiplier"`, set via `setFloatSettingValue` (NOT SettingWidgetBinder). The combo offers
1.0..12.0 by default (up to GPU-max / 25.0 when `[EmuCore/GS] ExtendedUpscalingMultipliers=true`).
The build strips trailing zeros for WHOLE values, so the live file stores `upscale_multiplier = 3`
(bare integer, no `.0`). => This is the general PCSX2 FLOAT format: write whole values as the bare
int (`3`), fractional as-is (`1.5`); NEVER `3.0`/`3.000000` (byte mismatch, PCSX2 rewrites on exit).
The earlier "INTEGER ENUM 1..8" note was WRONG on both type and range. For the MAD menu, an enum of
curated whole steps (Native..8x/12x) that writes the bare-int token is fine and round-trips.

## [EmuCore/GS] VsyncEnable  — bool  (CORRECTED 2026-07-01: it is [EmuCore/GS], NOT [EmuCore])
Source: `Pcsx2Config.cpp:922,928` wraps section `EmuCore/GS` then `SettingsWrapBitBool(VsyncEnable)`;
widget bind `EmulationSettingsWidget.cpp:32` uses `("EmuCore/GS","VsyncEnable")`; live ini has
`VsyncEnable = false` under the `[EmuCore/GS]` header. The earlier `[EmuCore]` claim here was WRONG
(writing it to `[EmuCore]` makes a dead duplicate that never affects VSync). bool lowercase true/false.

## [EmuCore/GS] deinterlace_mode  — GSInterlaceMode, 0-based int == option index
Source: `enum class GSInterlaceMode : u8` (Config.h), key `"deinterlace_mode"`:
```
0 Automatic, 1 Off, 2 WeaveTFF, 3 WeaveBFF, 4 BobTFF, 5 BobBFF,
6 BlendTFF, 7 BlendBFF, 8 AdaptiveTFF, 9 AdaptiveBFF   (10 = Count, not selectable)
```
Live: `deinterlace_mode = 0` (Automatic). Stored int EQUALS the option index =>
this CAN use write_mode="index" (write str(idx)). Default Automatic=0.

## [EmuCore/GS] MaxAnisotropy  — RAW DEGREE, not an index
Source: `SettingsWrapBitfieldEx(MaxAnisotropy, "MaxAnisotropy")`. Stored value is the
anisotropy DEGREE: 0/1 = Off, 2, 4, 8, 16. Live: `MaxAnisotropy = 8` (8x).
=> integer-token enum (write_mode="option", tokens "0","2","4","8","16"); NOT str(index).
(The UI combo is Off / 2x / 4x / 8x / 16x; "Off" stored as 0 historically, modern
default 0. The current file has 8.)

## [EmuCore/Speedhacks] EECycleRate  — signed int, value == slider value, range -3..+3
Source: `MINIMUM_EE_CYCLE_RATE = -3`, `MAXIMUM_EE_CYCLE_RATE = 3`, default 0
(EmulationSettingsWidget.cpp). Stored int = slider value directly. Live: `EECycleRate = 0`.
Qt combobox labels (top→bottom, value -3..+3):
```
-3 "50% (Underclock)"
-2 "60% (Underclock)"
-1 "75% (Underclock)"
 0 "100% (Normal Speed)"
 1 "130% (Overclock)"
 2 "180% (Overclock)"
 3 "300% (Overclock)"
```
=> enum where stored token is the SIGNED INT ("-3".."3") => write_mode="option",
options_stored = ["-3","-2","-1","0","1","2","3"], index round-trips. NOT str(index)
(index 0 would wrongly mean "-3"; the stored value for the default is "0", at index 3).

## [EmuCore] EnableFastBoot  — bool, default true
Source: EmuCore `SettingsWrapBitBool(EnableFastBoot)`, default true. Live:
`EnableFastBoot = true`. bool lowercase.

## Multitap & pad→port/slot mapping  (for MAD's pads→players binder, ≥3 players)
Verified 2026-06-17 against PCSX2 **source** (`pcsx2/SIO/Pad/Pad.cpp` `LoadConfig`,
`pcsx2/SIO/Sio.cpp` `sioConvertPortAndSlotToPad`) + the live PCSX2.ini.
- `[Pad]` section holds the multitap toggles: `MultitapPort1` / `MultitapPort2`
  (lowercase `true`/`false`; live default both `false`). `.ini` keys are 1-based;
  the source enum is `MultitapPort0/1_Enabled` (0-based) — same thing.
- Config has a FLAT pad index 0-7 = sections `[Pad1]`..`[Pad8]`. `LoadConfig` forces
  `NotConnected` for index 2-4 unless `MultitapPort1`, and 5-7 unless `MultitapPort2`.
  So `[Pad1]`/`[Pad2]` (idx 0/1) are ALWAYS active (the two base console ports).
- `sioConvertPortAndSlotToPad(port, slot)`: `slot==0 → port`; else `port==0 → slot+1`;
  else `slot+4`. ⇒ exact (physical port → pad section) mapping:
  - **Port 1** (physical) slots A/B/C/D = pad idx 0/2/3/4 = **`Pad1, Pad3, Pad4, Pad5`**.
  - **Port 2** (physical) slots A/B/C/D = pad idx 1/5/6/7 = **`Pad2, Pad6, Pad7, Pad8`**.
- MAD's `pcsx2_cfg._slot_plan(n)` maps n priority pads PORT-1-FIRST: n≤2 → `[Pad1,Pad2]`
  multitap OFF (standard 2-controller layout); 3-4 → `[Pad1,Pad3,Pad4,Pad5]` +
  `MultitapPort1`; 5-8 → + `[Pad2,Pad6,Pad7,Pad8]` + both. So priority i → in-game
  player i+1. LIMITATION: PS2 multiplayer is game-dependent — this fits the common
  single-multitap (4p) / dual-multitap (8p) layouts, not games with an unusual port
  expectation. (Memory-card `Multitap*_Slot*_Enable` keys are SEPARATE — not touched.)
- Sources: https://github.com/PCSX2/pcsx2/blob/master/pcsx2/SIO/Pad/Pad.cpp ,
  https://github.com/PCSX2/pcsx2/blob/master/pcsx2/SIO/Sio.cpp

## SAFE write approach for this format
REUSE: `lib/inifile.py` `section_body()/set_section()` operate on WHOLE sections
(used by `lib/pcsx2_cfg.py` for the [PadN] router blocks) — too coarse for single
keys (would reserialize the section and could reorder/normalize). For the MAD
settings page, use the Dolphin module's BYTE-PRESERVING single-key approach
(`lib/madsrv/dolphin_cmds.py`): `_section_span()` to bound `[Section]`,
`_read_key()` / `_replace_key()` to rewrite ONLY that one value token via regex,
`_ensure_bak()` (one-time .bak), `_atomic_write()` (temp+replace), `newline=""`
to preserve LF. Only edit keys that ALREADY EXIST (never create). Refuse writes
while `pcsx2-qt` is running. Section names contain `/` — `re.escape` handles it
(the Dolphin regexes already use `re.escape(section)`).

Sources:
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2/Config.h (GSRendererType, GSInterlaceMode enums)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2/Pcsx2Config.cpp (LoadSave macros: Renderer, upscale_multiplier, deinterlace_mode, MaxAnisotropy, VsyncEnable, EnableFastBoot, EECycleRate)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2-qt/Settings/EmulationSettingsWidget.cpp (EECycleRate range -3..3, default 0, value==slider)
- https://github.com/PCSX2/pcsx2/blob/master/pcsx2-qt/Settings/EmulationSettingsWidget.ui (EE cycle rate % labels)

---

# FULL PCSX2 settings tree for the MAD expansion (verified 2026-07-01)

Verified against the PCSX2-Qt source at `/home/deck/pcsx2x6-src` (`Pcsx2Config.cpp` LoadSave;
`pcsx2-qt/Settings/{Emulation,Advanced,Audio,OSD,Graphics}SettingsWidget.{cpp,ui}`;
`Input/InputManager.cpp`; `Hotkeys.cpp`; `GS/GS.cpp`; `Patch.cpp`) — byte-identical to PCSX2/pcsx2
master for these files — AND the live `~/.config/PCSX2/inis/PCSX2.ini`. docs.pcsx2.net has NO
per-setting / hotkey reference, so source + live-ini IS the authority. All bools lowercase true/false.

## MAD layout (Miquel, 2026-07-01)
Each category = its own PS2-tile `settings` section = its own RPC namespace, rendered by the shipped
`GuiMadPageEmuSettings` (flat `groups[]`, one bold `header()` per group; renders int/float/enum
steppers + bool chips; supports BUFFERED Save/Cancel via `<ns>.save`/`<ns>.cancel` — Lindbergh model).
Namespaces: `pcsx2emu`=Emulation, `pcsx2gfx`=Graphics, `pcsx2osd`=On-Screen Display, `pcsx2aud`=Audio,
`pcsx2adv`=Advanced. Per-game (`pcsx2pg`) mirrors these (override = key present; inherit = key absent
/ index-0 "Inherit global"; bools 3-way) + a Patches category.

## Encoding contract (per item type)
- bool: lowercase `true`/`false`.
- combo (BindWidgetToIntSetting): store the 0-based COMBO INDEX as `str(idx)`.
- offset -1 combos: store `index-1` (Automatic = -1 at combo index 0). ONLY: TriFilter,
  ExclusiveFullscreenControl, OverrideTextureBarriers.
- name-token enum: store a STRING from a fixed table (NOT an index): AspectRatio,
  FMVAspectRatioSwitch, MaxAnisotropy, Audio Backend/SyncMode/ExpansionMode.
- Renderer: sparse signed int-code string (Auto=-1, OGL=12, SW=13, VK=14; Linux menu = those 4).
- float: whole values as BARE INT (`3`), fractional as-is (`0.5`); never trailing zeros. Fine floats
  (ExpandShift -1..1, etc.) exposed as SCALED-INT steppers mirroring PCSX2's own x10/x100 slider
  scaling (backend converts int<->float token) since the C++ float stepper shows one decimal.
- spinbox: raw numeric value (int).
- clamp modes (Advanced): a TRIPLE of bools; write all three atomically per control.
- speed scalars (Emulation): a FLOAT preset combo; `0.0` = Unlimited; per-game inherit = key absent.

## Key-case traps (use the PERSISTED spelling; the widget code has case bugs)
- `FramerateNTSC` / `FrameratePAL`   (widget wrongly passes FrameRateNTSC/PAL)
- `UserHacks_ForceEvenSpritePosition`  (widget wrongly passes ..._forceEvenSpritePosition)
- `OsdshowPatches`   (lowercase 's' — upstream typo, the real key)

## Version drift — installed AppImage (`~/Applications/pcsx2-Qt.AppImage`) LAGS master
RULE: reality-check every key against the LIVE ini; only OFFER a setting the installed binary honors
(present in live ini). create-absent is safe but INERT for keys the binary doesn't know. Known drift:
- Audio volume: installed writes `[SPU2/Output] OutputVolume`; master renamed it `StandardVolume`.
  Write `OutputVolume` for THIS build (write what the live ini has; ideally detect).
- Master-only keys ABSENT from the installed ini (don't rely on): HWAccurateAlphaTest, HWAA1, HWROV,
  HWROVBarriersVK, UserHacks_Limit24BitDepth, UserHacks_DrawBuffering, OsdMargin, OsdFontPath,
  OsdBoldText, OsdshowPatches, OsdShowTextureReplacements, OsdShowGPUDebug (Win-only),
  `[EmuCore] OsdWarnAboutUnsafeSettings`.
- SyncMode is now a 2-value enum (Disabled/TimeStretch); the old Async/None trio is gone.

## EMULATION  (ns `pcsx2emu`; `EmulationSettingsWidget`; global + per-game)
Speed Control `[Framerate]` (float scalars; preset combo of % ; Unlimited=`0.0`; per-game inherit=absent):
`NominalScalar` (Normal Speed) def `1.0` clamp 0.05-10; `TurboScalar` (Fast-Forward) def `2.0`;
`SlomoScalar` (Slow-Motion) def `0.5`.
System `[EmuCore/Speedhacks]` unless noted: `EECycleRate` signed-int -3..3 def 0 (labels above);
`EECycleSkip` int index==value 0..3 def 0 (Disabled/Mild/Moderate/Maximum); `vuThread` (MTVU) bool
def false; `[EmuCore] EnableThreadPinning` bool false; `[EmuCore] EnableCheats` bool false
(GLOBAL-ONLY here; per-game hides it -> Cheats tab); `[EmuCore] HostFs` bool false;
`[EmuCore] CdvdPrecache` bool false; `fastCDVD` bool false (PER-GAME-ONLY here).
Frame Pacing `[EmuCore/GS]`: **Optimal Frame Pacing = pseudo-toggle, NO own key** -> checked writes
`VsyncQueueSize=0`, unchecked restores `2`; `VsyncQueueSize` (Max Frame Latency) int 1..5 def 2;
`SyncToHostRefreshRate` bool false; `UseVSyncForTiming` bool false; `VsyncEnable` bool false;
`SkipDuplicateFrames` bool true.
Real-Time Clock `[EmuCore]` (PER-GAME-ONLY): `ManuallySetRealTimeClock` bool false; `RtcYear` int
stored as an OFFSET from 2000 (0-99; PCSX2 does `tm_year = RtcYear + 100`, def 0 == year 2000 — do
NOT store the absolute year); `RtcMonth`(1..12) `RtcDay`(1..31) `RtcHour`(0..23) `RtcMinute`(0..59)
`RtcSecond`(0..59) int stored raw; `UseSystemLocaleFormat` bool false (MASTER-ONLY, absent from the
installed build's ini -> omit).
Savestate `[EmuCore]`: `SavestateCompressionType` int idx 0 Uncompressed/1 Deflate/2 Zstandard def 2;
`SavestateCompressionRatio` int idx 0 Low/1 Medium/2 High/3 VeryHigh def 1; `BackupSavestate` bool true;
`SaveStateOnShutdown` bool false.
Game Settings `[EmuCore]`: `EnableGameFixes` bool true; `EnablePatches` bool true.
PINE `[EmuCore]`: `EnablePINE` bool false; `PINESlot` int def 28011.

## ADVANCED  (ns `pcsx2adv`; `AdvancedSettingsWidget`; global+per-game, gated on `[UI] ShowAdvancedSettings=true`)
Red "at your own risk" page. Rounding `[EmuCore/CPU]` (int idx == FPRoundMode 0 Nearest/1 Neg/2 Pos/
3 Chop): `FPU.Roundmode` def 3; `FPUDiv.Roundmode` def 0; `VU0.Roundmode`/`VU1.Roundmode`.
**Clamp modes = TRIPLE-bool, write all three atomically** (mirror `AdvancedSettingsWidget::setClampingMode`
/ `getClampingModeIndex`): EE `[EmuCore/CPU/Recompiler]` `fpuOverflow`/`fpuExtraOverflow`/`fpuFullMode`,
index 0 None(F,F,F) / 1 Normal(T,F,F)(def) / 2 Extra+PreserveSign(T,T,F) / 3 Full(T,T,T). VU uses
`vuOverflow`/`vuExtraOverflow`/`vuSignOverflow` and the VU combo has "Extra+PreserveSign" at index 3
(NOT 2) — CONFIRM the exact VU index/bit table from the widget at build time.
Recompiler `[EmuCore/CPU/Recompiler]`: `EnableEE`(true) `EnableEECache`(false) `EnableFastmem`(true)
`PauseOnTLBMiss`(false) `EnableVU0`(true) `EnableVU1`(true) `EnableIOP`(true) bool. Speedhacks
`[EmuCore/Speedhacks]`: `WaitLoop`(true) `IntcStat`(true) `vuFlagHack` `vu1Instant`(true) bool.
`[EmuCore/CPU] ExtraMemory` bool false. (UI-less keys `*Underflow`/`*.DenormalsAreZero` — do NOT touch.)

## GRAPHICS  (ns `pcsx2gfx`; `GraphicsSettingsWidget`; ONE group == one MAD subsection per emulator tab)
All `[EmuCore/GS]` unless noted. Tab order/groups: Renderer(header), Display, Rendering (Hardware),
Rendering (Software), Hardware Fixes, Upscaling Fixes, Texture Replacement, Post-Processing,
Media Capture, Advanced. Renderer-conditional gates apply (HW vs SW tab, HW-Fixes/Upscaling only when
`UserHacks` on, etc — mirror `updateRendererDependentOptions`). Exhaustive per-key list (types/defaults/
enum labels) is in `GraphicsSettingsWidget.cpp` + `Graphics*Tab.ui` + `Pcsx2Config.cpp` GSOptions::LoadSave;
the non-obvious ones:
- Renderer (sparse signed enum, above); Adapter (string GPU name — DEFER, needs free-text widget).
- Display: `AspectRatio` name-token {Stretch,'Auto 4:3/3:2','4:3','16:9','10:7'} def 'Auto 4:3/3:2';
  `FMVAspectRatioSwitch` name-token (+ 'Off') def 'Off'; `deinterlace_mode` int idx 0..9 (0 Automatic);
  `linear_present_mode` int idx 0 None/1 Smooth(def)/2 Sharp; `IntegerScaling`/`pcrtc_offsets`/
  `pcrtc_overscan`/`disable_interlace_offset` bool false, `pcrtc_antiblur` bool true; `StretchY` int
  def 100 (1..300); `CropLeft/Top/Right/Bottom` int 0 (0..1000); `FullscreenMode` string DEFER.
  GLOBAL-only (per-game -> migrated to Patches): `[EmuCore] EnableWideScreenPatches`,
  `[EmuCore] EnableNoInterlacingPatches` bool false.
- Rendering (HW): `upscale_multiplier` FLOAT (above); `filter` int idx 0..3 (Bilinear PS2=2 def);
  `TriFilter` OFFSET-1 idx0 Automatic(-1)/Off(0)/PS2(1)/Forced(2); `MaxAnisotropy` name-token
  {0,2,4,8,16}; `dithering_ps2` int idx 0 Off/1 Scaled/2 Unscaled(def)/3 Force32; `hw_mipmap` bool
  true; `accurate_blending_unit` int idx 0 Minimum/1 Basic(def)..5 Maximum; `UserHacks` (Manual HW
  Fixes) bool false PER-GAME-ONLY (reveals HW-Fixes/Upscaling tabs). (Master-only absent: HWAccurateAlphaTest,
  HWAA1, HWROV.)
- Rendering (SW): `filter` (shared key); `extrathreads` int def 2; `autoflush_sw` bool true; `mipmap`
  (distinct key from hw_mipmap) bool true.
- Hardware Fixes (all `[EmuCore/GS]`, def 0/false; UserHacks_*): CPUSpriteRenderBW int 0..10,
  CPUSpriteRenderLevel int idx 0..2, CPUCLUTRender idx 0..2, GPUTargetCLUTMode idx 0..2,
  SkipDraw_Start/SkipDraw_End int 0..10000, AutoFlushLevel idx 0..2, CPU_FB_Conversion bool,
  DisableDepthSupport bool, Disable_Safe_Features bool, DisableRenderFixes bool,
  preload_frame_with_gs_data bool, DisablePartialInvalidation bool, TextureInsideRt idx 0..2,
  ReadTCOnClose bool, EstimateTextureRegion bool, `paltex` bool (disables Anisotropic when on).
  (Master-only absent: UserHacks_Limit24BitDepth, UserHacks_DrawBuffering.)
- Upscaling Fixes (UserHacks_*): HalfPixelOffset idx 0..5, native_scaling idx 0..4,
  round_sprite_offset idx 0..2, BilinearHack idx 0..2, TCOffsetX/TCOffsetY int 0..1000,
  align_sprite_X bool, merge_pp_sprite bool, `UserHacks_ForceEvenSpritePosition` bool (CASE TRAP),
  NativePaletteDraw bool.
- Texture Replacement (bool): DumpReplaceableTextures, DumpReplaceableMipmaps, DumpTexturesWithFMVActive,
  LoadTextureReplacements, LoadTextureReplacementsAsync(true), PrecacheTextureReplacements;
  `[Folders] Textures` path DEFER (removed per-game).
- Post-Processing: `CASMode` int idx 0..2, `CASSharpness` int 0..100 def 50, `fxaa` bool, `ShadeBoost`
  bool (gates next 4), `ShadeBoost_Brightness/Contrast/Gamma/Saturation` int 1..100 def 50, `TVShader`
  int idx 0..7.
- Media Capture: `ScreenshotSize` idx 0..2, `ScreenshotFormat` idx 0 PNG/1 JPEG/2 WebP,
  `ScreenshotQuality` int 1..100 def 90, `CaptureContainer` string def 'mp4' (curated enum ok),
  VideoCaptureCodec/Format string DEFER, `EnableVideoCapture` bool true, `VideoCaptureBitrate` int
  100..200000 def 6000, `VideoCaptureAutoResolution` bool true, `VideoCaptureWidth`/`Height` int def
  640/480, `EnableAudioCapture` bool true, `AudioCaptureBitrate` int 16..2048 def 192,
  Enable*CaptureParameters bool + *Parameters string DEFER.
- Advanced (gated ShowAdvancedSettings): `texture_preloading` idx 0..2 (live/help def 2), `GSDumpCompression`
  idx 0..2 (Zstd def 2), `HWDownloadMode` int-enum GSHardwareDownloadMode 0..3 ONLY (0 Accurate/1
  Disable Readbacks/2 Unsynchronized/3 Disabled — there is NO "Force Full" mode) PER-GAME-ONLY, UseBlitSwapChain (Win) /UseDebugDevice/
  UseDebugBlend/DisableMailboxPresentation/ExtendedUpscalingMultipliers/DisableFramebufferFetch/
  DisableShaderCache/DisableVertexShaderExpand/HWSpinCPUForReadbacks/HWSpinGPUForReadbacks bool,
  `OverrideTextureBarriers` OFFSET-1, `ExclusiveFullscreenControl` OFFSET-1 (Win-only, omit on Linux),
  `FramerateNTSC` float def 59.94 (CASE TRAP), `FrameratePAL` float def 50.00 (CASE TRAP).

## ON-SCREEN DISPLAY  (ns `pcsx2osd`; `OSDSettingsWidget`; keys in `[EmuCore/GS]`, one in `[EmuCore]`)
`OsdScale` float def 100 (50..500); `OsdMargin` float def 10 (master-only, absent live);
`OsdMessagesPos` int enum OsdOverlayPos 0..9 (0 None/1 TopLeft/.../9 BottomRight) def 1;
`OsdPerformancePos` def 3. `OsdShow*` bool (all `[EmuCore/GS]`): OsdShowSpeed/FPS/VPS(false),
OsdShowResolution/GSStats/CPU/GPU(false), OsdShowIndicators(true), OsdShowSettings(false),
`OsdshowPatches`(false, CASE TRAP, master-only), OsdShowInputs(false), OsdShowFrameTimes(false),
OsdShowVersion(false), OsdShowHardwareInfo(false), OsdShowVideoCapture(true), OsdShowInputRec(true),
OsdShowTextureReplacements(false, master-only), OsdShowGPUDebug(Win-only, omit). `OsdBoldText` bool true
(master-only). `[EmuCore] OsdWarnAboutUnsafeSettings` bool true (master-only). `OsdFontPath` string DEFER.
NOTE keys that do NOT exist (do not implement): OsdShowMessages (disable via OsdMessagesPos=0),
OsdShowDeviceState, OsdShowVersionInfo, OsdShowPatchStatus, OsdShowGPUStats.

## AUDIO  (ns `pcsx2aud`; `AudioSettingsWidget`; all keys `[SPU2/Output]`)
Volume: `OutputVolume` (installed) / `StandardVolume` (master) int 0..200 def 100 (WRITE WHAT LIVE INI
HAS); `FastForwardVolume` int 0..200 def 100; `OutputMuted` bool false.
Backend/driver: `Backend` name-token {Null,Cubeb,SDL} def Cubeb (live SDL); `DriverName` string DEFER;
`DeviceName` string DEFER. Sync/latency: `SyncMode` name-token {Disabled,TimeStretch} def TimeStretch;
`BufferMS` int 15..500 def 50; `OutputLatencyMS` int 15..200 def 20; `OutputLatencyMinimal` bool false.
Expansion: `ExpansionMode` name-token {Disabled,StereoLFE,Quadraphonic,QuadraphonicLFE,Surround51,
Surround71} def Disabled. Expansion sub-dialog (when != Disabled; PCSX2 LOAD-CLAMPS every value at
AudioStream.cpp:810-819 — offer exactly these ranges or PCSX2 silently rewrites them): `ExpandBlockSize`
128..8192 def 2048, forced to a POWER OF TWO (bit_ceil) -> offer a pow2 enum (128/256/.../8192);
`ExpandCircularWrap` float 0..360 def 90 (raw slider, step 1); `ExpandShift` float -1..1 def 0 (x100
slider -> expose SCALED-INT -100..100); `ExpandDepth` float 0..5 def 1 (x10 slider, 0.1 step ok);
`ExpandFocus` float -1..1 def 0 (x100 -> scaled-int -100..100); `ExpandCenterImage` float 0..1 def 1
(x100 -> scaled-int 0..100); `ExpandFrontSeparation`/`ExpandRearSeparation` float 0..10 def 1 (x10, 0.1
step ok); `ExpandLowCutoff` int 0..100 def 40; `ExpandHighCutoff` int 0..100 def 90. (CORRECTED
2026-07-01: the earlier "LowCutoff/HighCutoff 0..255, CenterImage 0..2" was WRONG — those are the u8
storage widths, NOT the load-clamp; the actual clamps are 0..100 / 0..1.)
Note: the MAD C++ float stepper shows ONE decimal, so `FramerateNTSC`/`FrameratePAL` (0.01-precision,
def 59.94/50) are offered as curated rate PRESETS (enum), not a raw float stepper, so 59.94 stays exact. Stretch sub-dialog (when SyncMode==TimeStretch):
`StretchSequenceLengthMS` int def 30, `StretchSeekWindowMS` int def 20, `StretchOverlapMS` int def 10,
`StretchUseQuickSeek` bool false, `StretchUseAAFilter` bool false. (`[SPU2/Debug]` = skip.)

## PATCHES / CHEATS  (per-game only; ns `pcsx2pg` Patches category)
Source `Patch.cpp`: `PATCHES_CONFIG_SECTION="Patches"`, `CHEATS_CONFIG_SECTION="Cheats"`,
`PATCH_ENABLE_CONFIG_KEY="Enable"`, `PATCH_DISABLE_CONFIG_KEY="Disable"`. Per-game
`gamesettings/<SERIAL>_<CRC>.ini` stores one REPEATABLE `Enable = <Label>` line per enabled patch
group under `[Patches]` (and `[Cheats]` identically); `Disable = <Label>` force-off (pnach 2.0).
Available labels = the bracketed `[Label]` group headers inside pnach files named `<SERIAL>_<CRC>.pnach`
or `<CRC>.pnach`, found on disk first (`~/.config/PCSX2/patches/`, `~/.config/PCSX2/cheats/`) then in
the AppImage `usr/bin/resources/patches.zip` (cheats NOT bundled). `pcsx2_games.py` already resolves
serial/CRC + indexes patches.zip (WS marker only today -> generalize to enumerate ALL labels).
`pcsx2_pergame_cmds.py` already has `_patches_labels/_patches_add/_patches_remove` (extend to [Cheats]).
Global toggles `[EmuCore]` (belong to Emulation category): `EnablePatches`(true), `EnableCheats`(false),
`EnableWideScreenPatches`(false), `EnableNoInterlacingPatches`(false) — auto-enable groups named exactly
"Widescreen 16:9" / "No-Interlacing".

## HOTKEYS  (ns `pcsx2hk`; flat global `[Hotkeys]`; NO per-player prefix, NO per-game by default)
Value grammar (replicate `InputManager::ConvertInputBindingKeysToString`, `InputManager.cpp:391-414`):
one action -> one binding string; CHORD = tokens joined by `" & "` (space-amp-space, all held together);
token = `Keyboard/<QtKey>` (Control/Alt/Shift/Return/Space/Tab/Plus/Minus/Insert/F1..F12/letters) OR
`SDL-N/<Name>` (Back/Start/Guide/RightStick/LeftShoulder/RightShoulder/...) with `+`/`-` half-axis
(e.g. `SDL-0/+RightTrigger`) or `~` full-axis. Chords may MIX keyboard+pad. Live examples:
`SaveStateToSlot = Keyboard/F1`, `ZoomIn = Keyboard/Control & Keyboard/Plus`,
`OpenPauseMenu = SDL-0/Back & SDL-0/RightStick`, `HoldTurbo = SDL-0/Back & SDL-0/+RightTrigger`.
Action list (compiled-in `DEFINE_HOTKEY`, hardcode per build; PRESERVE unknown live keys like ZoomIn/ZoomOut):
- Navigation (`Hotkeys.cpp`): ToggleFullscreen, OpenPauseMenu, OpenAchievementsList, OpenLeaderboardsList.
- Speed: TogglePause, FrameAdvance, ToggleFrameLimit, ToggleTurbo, HoldTurbo, ToggleSlowMotion,
  IncreaseSpeed, DecreaseSpeed.
- System: ShutdownVM, ResetVM, ReloadPatches, SwapMemCards, InputRecToggleMode, ToggleMouseLock.
- Save States: PreviousSaveStateSlot, NextSaveStateSlot, SaveStateToSlot, LoadStateFromSlot,
  LoadBackupStateFromSlot, SaveStateAndSelectNextSlot, SelectNextSlotAndSaveState, + SaveStateToSlot1..10,
  LoadStateFromSlot1..10.
- Audio: Mute, IncreaseVolume, DecreaseVolume.
- Graphics (`GS/GS.cpp`): Screenshot, ToggleVideoCapture, GSDumpSingleFrame, GSDumpMultiFrame,
  ToggleSoftwareRendering, IncreaseUpscaleMultiplier, DecreaseUpscaleMultiplier, ToggleOSD,
  CycleAspectRatio, ToggleMipmapMode, CycleInterlaceMode, CycleTVShader, CycleBlendingAccuracy,
  ToggleTextureDumping, ToggleTextureReplacements, ReloadTextureReplacements.
MAD reuse: `capture_cmds` `combo` mode already accumulates simultaneously-held pad+kb+mouse (the chord
primitive; must capture 2+ regular keys together too, not just modifier+key); `input_translate.
usb_keyboard_source` already emits `Keyboard/<QtKey>`; pad codes map to SDL names but WITHOUT the
`SDL-N/` prefix (the router injects N for pad binds; hotkeys need the device-qualified token, and N =
PCSX2's UNSTABLE SDL player index — design risk, decide keyboard-only vs router-P1-N vs warn).
Writer: byte-preserving flat `[Hotkeys]` that CREATES missing keys + PRESERVES unknown ones (dolphin_cmds
`_section_span`/`_replace_key` + insert), NOT the pad `.mad-input-overrides.json` sidecar. Refuse while
`pcsx2-qt` runs. Closest prior art: `retroarch_cmds.py` "System hotkeys" group ("hotkey" kind).

Sources (all fetched/verified 2026-07-01): `/home/deck/pcsx2x6-src` (Pcsx2Config.cpp, the 5
*SettingsWidget.{cpp,ui}, Input/InputManager.cpp, Hotkeys.cpp, GS/GS.cpp, Patch.cpp), live
`~/.config/PCSX2/inis/PCSX2.ini`, and https://raw.githubusercontent.com/PCSX2/pcsx2/master (same files,
cross-checked). Exhaustive per-key detail lives in those source files (durable on disk).
