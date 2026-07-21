# Cemu (Wii U) config formats - settings.xml, game profiles, graphic packs, input

Source-verified against cemu-project/Cemu `main` + the official wiki, fetched 2026-07-08, and the
live on-device install (Cemu 2.6 AppImage). Cached per house rule #2 so we do not re-derive it.
ASCII only (no em-dashes) per the docs rule.

## Paths on this Deck (NATIVE AppImage, XDG split - same as lib/cemu_cfg.py)
- CONFIG dir  `~/.config/Cemu`      -> settings.xml, controllerProfiles/, gameProfiles/
- DATA   dir  `~/.local/share/Cemu` -> graphicPacks/, title_list_cache.xml, keys.txt, log.txt
- settings.xml is LF; gameProfiles/*.ini are **CRLF** (Cemu writes them Windows-style); named
  controllerProfiles/*.xml are LF. (Flatpak Cemu would keep both under
  `~/.var/app/info.cemu.Cemu/data/Cemu` - not this device.)
- ROMs: `~/ROMs/wiiu` (symlink -> `/run/media/deck/1tbDeck/ROMs/wiiu`). ES-DE gamelist:
  `~/ES-DE/gamelists/wiiu/gamelist.xml`. Wii U rom filenames carry NO title-id tag.

## settings.xml (global) - single `<content>` root
Two C++ structs write tags as direct children of the SAME `<content>`: `CemuConfig` (emulation /
`<Graphic>` / `<Audio>`) and the child `wxCemuConfig` (fullscreen, language, check_update, ...). So
General-page keys use XML parent = `content`. `section` = the XML parent tag isolates non-unique
tags: `<api>` under BOTH `<Graphic>` and `<Audio>`; `<Position>`/`<TextScale>` under BOTH
`<Overlay>` and `<Notification>`. Bools are literal `true`/`false`; enums are the INTEGER ENUM CODE.

Enum codes (CemuConfig.h / IAudioAPI.h):
- GraphicAPI: OpenGL=0, Vulkan=1, Metal=2. Deck default Vulkan(1).
- AudioAPI: DirectSound=0, XAudio27=1, XAudio2=2, Cubeb=3. Linux = Cubeb(3) ONLY; PIN it (writing 0
  breaks audio). Stored as the code "3", NOT a 0-based index -> write_mode "option" stored=["3"].
- UpscalingFilter (Upscale AND Downscale): Linear=0, Bicubic=1, BicubicHermite=2, NearestNeighbor=3.
- FullscreenScaling: KeepAspectRatio=0, Stretch=1.
- ScreenPosition (Overlay/Notification Position): Disabled=0, TopLeft=1, TopCenter=2, TopRight=3,
  BottomLeft=4, BottomCenter=5, BottomRight=6.
- AudioChannels: Mono=0, Stereo=1, Surround=2. (PadChannels/InputChannels hardcoded by Cemu's GUI.)
- CafeConsoleLanguage: JA=0, EN=1, FR=2, DE=3, IT=4, ES=5, ZH=6, KO=7, NL=8, PT=9, RU=10, TW=11.
- VSync is a 0-based combo whose LABELS depend on api; Vulkan: 0=Off, 1=Double buffering,
  2=Triple buffering, 3=Match emulated display.

Do NOT expose (no safe generic control): `mlc_path`, `language` (wx language-id table),
`Graphic/device`+`vkDevice`+`mtlDevice` (GPU UUID strings), `Audio/*Device` (audio-device id
strings). NOT in settings.xml at all (commented out in Save): `cpu_mode`, `console_region`.

## gameProfiles/<titleId:016x>.ini (PER-GAME) - the real per-title override file
Filename = LOWERCASE 16-hex title id. Loaded by `gameProfile_load()` in CafeSystem.cpp. Every field
OPTIONAL -> ABSENT means "use Cemu's default" (for graphics_api that default is the global
settings.xml api; the CPU/shader keys have NO global equivalent, so absent = compiled default). So
our per-game page labels index 0 "Use default", NOT "Inherit global", and there is NO
`\use_global`/`\default` twin (unlike the Yuzu forks). CRLF - edit in LF, restore CRLF on write.

Sections / keys / enum codes (GameProfile.h + CemuConfig.h):
- `[General]` loadSharedLibraries (bool, optional), startWithPadView (bool)
- `[CPU]` cpuMode (enum, optional) CPUMode: SinglecoreInterpreter=0, SinglecoreRecompiler=1,
  DualcoreRecompiler=2 (legacy -> maps to Multicore on load; we curate it out), MulticoreRecompiler=3,
  Auto=4. threadQuantum (uint, default 45000, range 1000..536870912; GUI presets 20000/45000/60000/
  80000/100000).
- `[Graphics]` graphics_api (int, optional, -1=unset, 0=OpenGL, 1=Vulkan); accurateShaderMul (enum,
  default True) AccurateShaderMulOption: False=0, True=1 (the old "min" was removed);
  precompiledShaders (enum, optional, default Auto) PrecompiledShaderOption: Auto=0, Enable=1,
  Disable=2. (Serialized as the underlying int; a legacy string path also reads "true"/"false".)
- `[Audio]` disableAudio (bool)
- `[Controller]` controller1..controller8 (1-based) = a controllerProfiles/<name>.xml NAME (no
  extension) to load into that port FOR THIS GAME. Applied at launch by
  InputManager::apply_game_profile. This OVERRIDES Options>Input AND bypasses our launch-time router
  (lib/cemu_cfg.py), so our page defaults each port to "Use router / global" (no key).

## controllerProfiles/*.xml (INPUT - device-agnostic, kept as the router's job)
- 8 ports. Active files controller0..controller7.xml (0-based); named templates <name>.xml (same
  schema). `<type>`: "Wii U GamePad"=VPAD, "Wii U Pro Controller"=Pro, "Wii U Classic Controller
  Pro"=Classic, "Wiimote". `<api>`: SDLController / DSUController (serialized name of DSUClient) /
  Keyboard / XInput / GameCube. `<uuid>` SDL = "<index>_<sdl_guid>". `<mappings>` = emulated
  kButtonId (VPADController.h: A=1,B=2,X=3,Y=4,L=5,R=6,ZL=7,ZR=8,Plus=9,Minus=10,Up=11,Down=12,
  Left=13,Right=14,StickL=15,StickR=16,...) -> host code. Device-agnostic SDL GameController IDs
  (identical across pads) -> NO C1/C2 device-exact numbering; we do NOT build a per-button remapper.
  The router clones named templates into controllerN.xml at launch (lib/cemu_cfg.py).
- CRITICAL (uuid `<index>`): the number before the guid is the ORDINAL AMONG SAME-GUID pads (0 = the
  first connected pad of that guid, 1 = the second identical pad), NOT the global SDL enumeration
  index. Source: Cemu 2.6 `src/input/api/SDL/SDLControllerProvider.cpp` get_index() counts same-guid
  gamepads in SDL_GetGamepads() enumeration order and binds the guid_index-th; the enumerate loop
  assigns guid_index = running per-guid counter (verified 2026-07-21, github cemu-project/Cemu). So a
  lone Wii U Pro is ALWAYS "0_<guid>" even if it enumerates at SDL index 2 behind other pads. Writing
  the global index (e.g. "2_") makes Cemu hunt for a 3rd same-guid pad and bind NOTHING. This was the
  root cause of "external pads dead in a Wii U game" (fix f2000f0: lib/cemu_cfg._sdl_match returns the
  per-guid ordinal via class_index, not same[ci].index).
- Cemu allows exactly ONE "Wii U GamePad" (Controller 1). External players (Controller 2..5) must be
  "Wii U Pro Controller" (or Classic/Wiimote), never a 2nd GamePad, or the slot is invalid. A profile
  with a second `<controller>` block for another device (e.g. a "+ Steamdeck" co-source) binds BOTH
  devices to that one emulated controller; on an external player slot that lets the Deck (already
  Controller 1) shadow the player. cemu_seat seats external slots via repin_profile(external_slot=True)
  which drops non-family (Deck) blocks and forces Pro type. Per-launch seat diagnostics: router.log.

## Cemu controller MODEL + multiplayer (research 2026-07-21; sources at end)
- EMULATED TYPES (`EmulatedController.h`: `enum Type { VPAD, Pro, Classic, Wiimote }`): "Wii U GamePad"=VPAD,
  "Wii U Pro Controller"=Pro, "Wii U Classic Controller Pro"=Classic, "Wiimote". Wii U console model:
  EXACTLY ONE GamePad (VPAD ch0); up to 4 of Pro/Classic/Wiimote (WPAD/KPAD ch0-3). 8 config slots
  (controller0-7.xml) but a game only reads 1 GamePad + up to 4 WPAD/KPAD. Controller 1 = the GamePad slot.
- A GAME SEES CONTROLLERS BY TYPE (per player). Cemu emulates two Cafe OS input libs: `vpad.cpp` (VPAD)
  reads the GamePad ONLY; `padscore.cpp` (WPAD/KPAD) reads Wiimote/Pro/Classic for players 2-4 and reports
  the EXTENSION type. Consequences: a GamePad-only game never accepts P2-4; and SOME co-op games only accept
  Wii REMOTES for P2-4 and reject Pro Controllers (canonical: New Super Mario Bros U - extra players are
  Wiimotes, the GamePad is Boost Mode). Extra players must NOT be a 2nd GamePad. So the emulated TYPE per
  slot is load-bearing, and the router only ever emits "Wii U Pro Controller" for external slots (no Wiimote).
- CEMU MID-GAME REGISTRATION QUIRK (issue #645): non-Player-1 controllers ALREADY ENABLED when a game boots
  often do NOT register until you toggle them off->on in Cemu Options>Input WHILE the game runs (can also
  perturb P1). Strong match for "P1 works, P2 seated correctly on disk but dead in-game". This is a CEMU-side
  behavior, NOT a router seating bug, and it would hit the per-game [Controller] pin path too. Workaround =
  the manual mid-game toggle, or the game's own "press to join at character-select" step.
- #645 AUTO-CONNECT research (2026-07-21): NOT automatable by any config the router can write. There is NO
  settings.xml key, NO gameProfiles [Controller] flag, NO command-line option (full CLI in LaunchSettings.cpp
  is game/title/mlc/fullscreen/account/console-logging/interpreter/gdbstub only - nothing input-related), NO
  env var, and NO connected/active field in the controllerN.xml schema (`<type><api><uuid><display_name>
  <settings><mappings>` only). Cemu does NOT watch the controller files: InputManager::load() runs at startup
  (main.cpp), from the Input GUI, and via apply_game_profile() at launch - so a SECOND controllerN.xml write
  AFTER boot is a no-op. #645 is OPEN/unfixed (1 comment, no PR); corroborated by bugs.cemu.info #677 "PS5
  Controller not connected on startup". The GUI workarounds (disable/enable a port, load-profile, toggle
  emulated type) all do ONE thing internally: destroy + recreate the emulated WPADController, whose default
  m_status=ReportConnect makes padscore.cpp's TickFunction re-fire the game's WPAD connect callback
  (disconnect->connect) while the game is listening. Cemu exposes NO IPC/socket/scripting and NO
  reload/reconnect hotkey (hotkeys = fullscreen/screenshot/fast-forward/end-emulation/exit only), so the GUI
  toggle can NOT be scripted. The ONE GUI-free runtime nudge: padscore.cpp fires the same disconnect->connect
  when `was_home_button_down()` is true, so a synthetic emulated-HOME press on the affected pad AFTER the game
  starts re-registers that channel - the only automatable path, but game-dependent (some games open a HOME/pause
  menu) and needs on-device validation; must fire only on non-P1 channels and after the game polls. Mechanism is
  general to all WPAD types (Pro/Wiimote/Classic share padscore); WHICH games trip it is timing-dependent (MK8
  re-reads joins so it works at boot; Shovel Knight / Adventure Time latch the player set early and need a fresh
  connect). Sources: github Cemu#645, bugs.cemu.info#677, src/Cafe/OS/libs/padscore/padscore.cpp,
  src/input/emulated/WPADController.{h,cpp}, src/input/InputManager.cpp, src/gui/wxgui/input/{InputSettings2,
  HotkeySettings}.cpp, src/config/LaunchSettings.cpp.
- BIND-FAILURE causes (`SDLControllerProvider::get_index`): guid mismatch; wrong per-guid ordinal; wrong
  `<api>`; pad enabled after boot (#645); Steam Input virtualizing the guid. NOTE the DualSense guid depends
  on SDL HIDAPI: sig `68`=hidapi ON (`050057564c...6800`), sig `81`=hidapi OFF (`050057564c...8100`); the ES-DE
  Game Mode env does NOT force HIDAPI off, so hook and Cemu should agree (ruled out here).
- GAME Adventure Time ETDBIDK (000500001014e100): 4-player local co-op; Nintendo listing compatible
  controllers = GamePad, Wii Remote (+Nunchuk), Wii U Pro Controller. So Pro IS supported (Wiimote-for-P2 not
  strictly required); Cemu wiki: Playable.
- Sources: github EmulatedController.h + src/input; deepwiki cemu-project/Cemu/4-input-system (vpad/padscore);
  github Cemu#645; wiki.cemu.info + cemu.cfw.guide/controller-configuration; nintendo.com/en-gb game page.

## Graphic packs
- rules.txt `[Definition]`: titleIds (comma list of 16-hex, or `*` = universal/all games; matched by
  EXACT 64-bit equality, no wildcards except whole-list `*`), name, path ("Game/Category/Sub" tree),
  version (>=5 enables `condition` presets). `[Preset]` blocks: name (required), category (optional;
  groups presets into one dropdown), default (non-zero marks the category default), $vars.
- Packs live under DATA `graphicPacks/` (manual) and `graphicPacks/downloadedGraphicPacks/` (Cemu's
  "Download community graphic packs" updater; version marker `downloadedGraphicPacks/version.txt`).
  172 installed on this device.
- ENABLED state -> settings.xml `<GraphicPack>` with one `<Entry filename="<rules.txt relative to
  the DATA dir>" [disabled="true"]>` per pack; preset choices are `<Preset><category>C</category>
  <preset>P</preset></Preset>` children (category child omitted when empty; category written BEFORE
  preset, per CemuConfig.cpp Save). Presence of an `<Entry>` = ENABLED unless disabled="true"
  (disabled keeps preset choices). Match filenames by the tail from "graphicPacks/" (some installs
  prefix "../share/"). Never edit while Cemu runs (rewrites settings.xml on exit).

## title_list_cache.xml (rom -> title id)
DATA dir, well-formed single-root XML (ElementTree-safe). `<title titleId=.. app_type=.. version=..>
<region/><name/><path/></title>`. Base game app_type "80000000" / title-id prefix "00050000";
update "0005000e", DLC "0005000c". For per-game / pack matching use the BASE title. `.wua` bundles
base+update+DLC in one file. Only lists games Cemu has scanned (its Game Paths).

## Sources
- CemuConfig.h/.cpp, XMLConfig.h, IAudioAPI.h, GeneralSettings2.cpp:
  https://github.com/cemu-project/Cemu/tree/main/src/config , .../src/audio , .../src/gui/wxgui
- GameProfile.h/.cpp: https://github.com/cemu-project/Cemu/tree/main/src/Cafe/GameProfile
- InputManager / EmulatedController / InputAPI / VPADController:
  https://github.com/cemu-project/Cemu/tree/main/src/input
- GraphicPack2.cpp: https://github.com/cemu-project/Cemu/blob/main/src/Cafe/GraphicPack/GraphicPack2.cpp
- Wiki: https://wiki.cemu.info/wiki/Graphic_packs , .../Tutorial:Game_Profiles ,
  .../Tutorial:Configuring_Controllers
