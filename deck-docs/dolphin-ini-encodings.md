# Dolphin INI stored-value encodings (for a non-corrupting settings editor)

Verified 2026-06-14 against the Dolphin **source code** on GitHub (master) and the
**live installed** Flatpak `org.DolphinEmu.dolphin-emu` build on this Steam Deck
(`Version 2603a`, commit `798fc13e...`). Files use `Key = Value`, LF endings,
booleans capitalized `True` / `False`.

Live config paths:
- `~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/GFX.ini`
- `~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/Dolphin.ini`

## GFX.ini

### [Settings] InternalResolution  (int, default 1)
- Source: `Config::GFX_EFB_SCALE` = `{GFX, "Settings", "InternalResolution"}` (GraphicsSettings.cpp).
- `EFB_SCALE_AUTO_INTEGRAL = 0` (VideoConfig.h). Stored value = the scale multiplier.
- 0 = Auto (multiple of 640x528), 1 = Native (1x, 640x528), 2 = 2x (720p-ish),
  3 = 3x, 4 = 4x ... N = Nx. **No hard-coded max** — UI caps at GFX_MAX_EFB_SCALE
  but the stored int is open-ended. Deck value `2` = 2x Native. NOT an enum index trick: value == scale, Auto is the special 0.

### [Settings] AspectRatio  (enum AspectMode:int, default 0 Auto)
- Source: `Config::GFX_ASPECT_RATIO` + `enum class AspectMode` (VideoConfig.h).
- 0=Auto, 1=ForceWide (16:9), 2=ForceStandard (4:3), 3=Stretch,
  4=Custom, 5=CustomStretch, 6=Raw (squared pixels).
- CAVEAT: user assumed only 0-3; values 4/5/6 exist in current builds.

### [Settings] MSAA  (u32, stored as 8-digit HEX, default 0x00000001)
- Source: `Config::GFX_MSAA` = `{GFX,"Settings","MSAA"}`, type u32.
- Value = MSAA sample COUNT, not an index. `MultisamplingEnabled() = iMultisamples > 1`.
- 0x00000001 = 1 sample = OFF/None. 0x00000002=2x, 0x00000004=4x, 0x00000008=8x.
- Written zero-padded lowercase-x hex (`0x%08x`).

### [Settings] SSAA  (bool, default False)
- Separate flag. When True, the SAME MSAA sample count is used as SUPERSAMPLING
  instead of multisampling (the UI's AA dropdown merges them: "Nx MSAA" vs "Nx SSAA").
  So to express "4x SSAA": MSAA=0x00000004 + SSAA=True.

### [Hardware] VSync  (bool, default False)
- Source: `Config::GFX_VSYNC` = `{GFX,"Hardware","VSync"}`. Lives in [Hardware].

### MaxAnisotropy  — **VERSION-DEPENDENT, read the file, don't assume**
- **Installed 2603a build (this Deck):** `[Hardware] MaxAnisotropy`, plain 0-based int:
  0=1x, 1=2x, 2=4x, 3=8x, 4=16x. Deck value `3` = 8x.
- **Current master:** moved to `[Enhancements] MaxAnisotropy`, type
  `enum AnisotropicFilteringMode`: Default=-1, Force1x=0, Force2x=1, Force4x=2,
  Force8x=3, Force16x=4. When set to Default(-1) the key is DELETED from the ini.
- SAFE RULE: locate the existing key + its section in the file and preserve them;
  the index meaning (0=1x..4=16x) is the same in both, only -1/Default and the
  section header differ.

## Dolphin.ini

### [Core] GFXBackend  (string, no quotes)
- Source: `MAIN_GFX_BACKEND` = `{Main,"Core","GFXBackend"}`; value = backend CONFIG_NAME.
- Linux valid: `Vulkan`, `OGL` (OpenGL), `Software Renderer`, `Null`.
  (D3D11/D3D12 Windows-only; Metal macOS-only.) Deck = `Vulkan`.

### [DSP] Backend  (string, no quotes)
- Source: `MAIN_AUDIO_BACKEND` = `{Main,"DSP","Backend"}`; defines in MainSettings.h:
  - `Cubeb`  (BACKEND_CUBEB)
  - `ALSA`   (BACKEND_ALSA)
  - `Pulse`  (BACKEND_PULSEAUDIO)  <-- literally "Pulse", NOT "PulseAudio"
  - `OpenAL` (BACKEND_OPENAL)
  - `No Audio Output` (BACKEND_NULLSOUND)  <-- exact caps "No Audio Output"
  - `OpenSLES` (Android only)
  - `WASAPI (Exclusive Mode)` (Windows only)
- Linux typically offers: No Audio Output, Cubeb, ALSA, Pulse, OpenAL. Deck = `Cubeb`.

### [Core] CPUThread  (bool, default false; "dual core")
- Source: `MAIN_CPU_THREAD` = `{Main,"Core","CPUThread"}`. Deck = True.

### [Core] AudioStretch  (bool) + AudioStretchMaxLatency (int) — **legacy keys**
- The installed 2603a build writes `[Core] AudioStretch = False` and
  `AudioStretchMaxLatency = 80`.
- CAVEAT: current master RENAMED these to `[Core] AudioPreservePitch` (bool) and
  `[Core] AudioBufferSize` (int). For THIS Deck build use the AudioStretch* names;
  read the file to confirm which exist before writing.

### [Display] Fullscreen  (bool, default false)
- Source: `MAIN_FULLSCREEN` = `{Main,"Display","Fullscreen"}`. Deck = True.

## Sources
- github.com/dolphin-emu/dolphin Source/Core/VideoCommon/VideoConfig.h (AspectMode,
  AnisotropicFilteringMode, EFB_SCALE_AUTO_INTEGRAL, iMultisamples/bSSAA).
- .../Core/Core/Config/GraphicsSettings.cpp (GFX_* Config::Info section/key/default).
- .../Core/Core/Config/MainSettings.cpp + MainSettings.h (MAIN_* keys, BACKEND_* defines).
- .../Core/VideoBackends/{Vulkan,OGL,Software,Null}/VideoBackend.h (CONFIG_NAME).
- Live installed config files on this Deck (ground truth for 2603a layout).

## Additional keys verified 2026-07-08 (for the MAD Wii/GameCube settings tree)

Source: Dolphin `master` Source/Core/Core/Config/MainSettings.cpp (raw.githubusercontent.com,
fetched 2026-07-08) + the live config. All in `[Core]` unless noted. These are Config::Info
`{System::Main, "SECTION", "KEY"}` defaults:

### CREATE-in-section keys (Dolphin does not persist until changed; MAD creates them safely)
- `[Core] OverclockEnable`  bool, default False  (emulated-CPU clock override toggle)
- `[Core] Overclock`        float, default 1.0   (clock factor; 1.0 = 100%)
- `[DSP]  Volume`           int, default 100     (audio volume %)

### Present-key enums used by the tree
- `[Core] SIDevice0..3`  enum `SerialInterface::SIDevices` (NON-contiguous ints, so MAD writes
  the stored INT string via write_mode "option"): 0=None, 4=N64 Controller, 5=GC GBA(real link),
  6=GC Standard Controller (Deck default), 7=Keyboard, 8=Steering Wheel, 9=Dance Mat,
  10=DK Bongos, 11=AM Baseboard (Triforce), 12=GC Adapter for Wii U, 13=GC GBA (emulated).
- `[Settings] AspectRatio` enum `AspectMode` 0=Auto,1=ForceWide(16:9),2=ForceStandard(4:3),
  3=Stretch,4=Custom,5=CustomStretch,6=Raw. (matches the [Settings] section above.)
- `[Settings] ShaderCompilationMode` enum: 0=Synchronous, 1=Synchronous(Ubershaders),
  2=Asynchronous(Ubershaders), 3=Asynchronous(skip drawing).

### Anti-aliasing composite (MAD merges two keys into one "Anti-aliasing" enum)
- `[Settings] MSAA` (u32 hex sample-count, `0x%08x`) + `[Settings] SSAA` (bool). MAD offers:
  None=(MSAA 1,SSAA F), 2x/4x/8x MSAA=(2/4/8,F), 2x/4x/8x SSAA=(2/4/8,T).

### Keys deliberately NOT exposed (enum-order or byte risk on the older installed build)
- GC `SelectedLanguage` (int), memcard `SlotA/SlotB` (EXIDeviceType), `EnableCustomRTC`/
  `CustomRTCValue`, `RAMOverrideEnable`/`MEM1Size`/`MEM2Size`, `CPUCore`, `DPL2Quality` --
  present in source but left out to avoid guessing an enum order / raw byte value.
