# Ikemen GO (MUGEN-compatible engine) config reference

Engine: Ikemen GO, the open-source MUGEN-compatible fighting engine.
Repo: https://github.com/ikemen-engine/Ikemen-GO
Scope of this note: what the OFFICIAL source/schema says about resolution (video),
input (keyboard/joystick), config file format across versions, and per-game overrides.
All facts below verified against the repository source, not reverse-engineered.
Date captured: 2026-07-19.

See also: deck-docs/mugen.md for how the Deck and the MAD tile actually run this engine (paths,
launch command, input wiring). This file is the upstream engine reference only.

IMPORTANT correction to the common assumption: for Ikemen GO, JSON is the OLDER/stable
format and INI is the NEWER (master/nightly 2025 rewrite). It is NOT "new=json, old=ini".
See "Config file format across versions" below. A self-contained bundled game that ships
`save/config.ini` is running a RECENT nightly/master build, not an old one. Verify the
build's version if this matters.

--------------------------------------------------------------------------------
## 1. Two config eras (source of truth)

A) STABLE / released (through the latest tag v0.99.0 and earlier): `save/config.json`
   - Embedded default: `src/resources/defaultConfig.json`
   - Loaded in `src/main.go` -> setupConfig(): `cfgPath := "save/config.json"`
   - FLAT JSON (all keys at top level; no nested [Video]/[Input] sections).
   Sources:
   - https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/v0.99.0/src/main.go (2026-07-19)
   - https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/v0.99.0/src/resources/defaultConfig.json (2026-07-19)

B) CURRENT master / "nightly" release (2025 rewrite): `save/config.ini`
   - Embedded default: `src/resources/defaultConfig.ini`
   - Config struct + loader now in `src/config.go` (go-ini), filename in `src/main.go`:
     `configPath := "save/config.ini"`
   - Sectioned INI: [Video], [Sound], [Input], [Keys_P1..], [Joystick_P1..], etc.
   Sources:
   - https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/config.go (2026-07-19)
   - https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/resources/defaultConfig.ini (2026-07-19)
   - https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/main.go (2026-07-19)

Latest GitHub release tag = "nightly"; latest SemVer tag = v0.99.0.

Per-game / per-build config isolation: CONFIRMED. The path is RELATIVE ("save/config.json"
or "save/config.ini") to the engine's working directory. Each self-contained Ikemen build
(own folder, own `save/`) keeps its OWN config file. Copying a build = copying its config.

--------------------------------------------------------------------------------
## 2. Video / resolution keys

### JSON era (v0.99.0, flat keys) - exact key names from defaultConfig.json
  GameWidth              640      ; internal RENDER resolution width
  GameHeight             480      ; internal RENDER resolution height
  FullscreenWidth        -1       ; exclusive-fullscreen screen width, -1 = desktop native
  FullscreenHeight       -1       ; exclusive-fullscreen screen height, -1 = desktop native
  FullscreenRefreshRate  60
  Fullscreen             false    ; bool -> exclusive fullscreen
  Borderless             false    ; bool
  MSAA                   false    ; NOTE: BOOL in the JSON era
  VRetrace               1        ; this is the VSync knob in the JSON era (NOT named "VSync")
  Framerate              60
  WindowCentered         true
  Players                4
  Motif                  "data/system.def"   ; the active motif/screenpack
  (there is NO WindowWidth/WindowHeight, NO RenderMode, NO WindowScaleMode in v0.99 JSON;
   windowed mode uses GameWidth/GameHeight as the window size.)

### INI era (master, [Video] section) - exact keys from defaultConfig.ini
  RenderMode        = OpenGL 3.3   ; NEW. one of: "OpenGL ES 3.2" (Android), "OpenGL 3.3"
                                   ;   (desktop default), "Vulkan 1.3" (desktop, experimental)
  GameWidth         = 1280         ; internal RENDER resolution width (default bumped to 720p)
  GameHeight        = 720          ; internal RENDER resolution height
  WindowWidth       = 0            ; windowed: window size. exclusive fullscreen: screen size.
  WindowHeight      = 0            ;   0 = fall back to GameWidth/GameHeight
  Fullscreen        = 0            ; 1 = exclusive fullscreen, 0 = windowed
  Borderless        = 0            ; borderless fullscreen (ignored if Fullscreen = 0)
  VSync             = 1            ; renamed from VRetrace; sync frame rate to refresh
  MSAA              = 0            ; NOTE: now INT (powers of 2, 2..32), NOT bool
  Framerate         = 60           ; render target FPS (does not change game logic speed)
  WindowCentered    = 1
  WindowScaleMode   = 1            ; NEW. framebuffer scale filter: 0 nearest, 1 bilinear
  FightAspectWidth  = -1           ; match aspect: 0,0 = resolution AR; -1,-1 = stage AR; w,h = custom
  FightAspectHeight = -1
  KeepAspect        = 1            ; 0 = stretch to window, 1 = keep aspect
  RGBSpriteBilinearFilter = 1
  EnableModel / EnableModelShadow / RendererDebugMode / ImageSuballoc* = 3D + memory knobs
  (there is NO FullscreenWidth/FullscreenHeight/FullscreenRefreshRate in the INI era;
   WindowWidth/WindowHeight double as the exclusive-fullscreen screen size.)

### How they interact (render res vs coordinate space) - IMPORTANT
  - GameWidth x GameHeight = the RENDER resolution (the internal framebuffer the engine
    renders the whole game at). This is what to change for sharpness/perf.
  - localcoord is NOT a config.json key. It is a per-asset AUTHORING coordinate space
    declared in .def files: system.def [Info] localcoord, a character's .cns localcoord,
    a stage .def localcoord. The engine SCALES each asset's localcoord to GameWidth/Height.
    So GameWidth is the render space; localcoord is the authoring space. Do not conflate them.
  - Window/Fullscreen size: JSON era -> windowed uses GameWidth/Height, exclusive fullscreen
    uses FullscreenWidth/Height (-1 = desktop). INI era -> WindowWidth/Height is the window
    size (windowed) or the screen size (exclusive fullscreen), 0 = use GameWidth/Height.
    Either way the GameWidth framebuffer is scaled to the on-screen surface (filter =
    WindowScaleMode in the INI era).
  - Fullscreen (bool/1) = exclusive fullscreen; Borderless = borderless-window fullscreen,
    ignored unless Fullscreen is set. VSync/VRetrace = tear prevention (adds input lag).

Source for the struct (INI era, src/config.go, Video struct + ini tags):
  https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/config.go (2026-07-19)

--------------------------------------------------------------------------------
## 3. Input config (keyboard + joystick)

### JSON era (v0.99.0): KeyConfig / JoystickConfig arrays
  "KeyConfig":      [ {..P1..}, {..P2..}, {..P3..}, {..P4..} ]   ; keyboard, one per player
  "JoystickConfig": [ {..P1..}, {..P2..}, {..P3..}, {..P4..} ]   ; pads, one per player
  Each entry:
    "Joystick": <int>     ; -1 = keyboard entry; 0,1,2,3.. = physical joystick INDEX
    "Buttons": [ 14 tokens ]  ; order = up,down,left,right,a,b,c,x,y,z,start,d,w,menu
  Keyboard tokens are GLFW key names ("UP","DOWN","LEFT","RIGHT","z","x","c","a","s",
    "d","RETURN","q","w","Not used").
  Joystick tokens in the JSON era are RAW numeric button/axis IDs as strings; a negative
    value means an axis direction (e.g. "-12","-10"). These raw IDs differ Windows vs Linux.
  Player count = "Players" (default 4). Number of config slots matches.
  CRITICAL: the JSON era has NO GUID field. A joystick is bound PURELY by numeric index.

### INI era (master): [Keys_Pn] and [Joystick_Pn] sections
  Struct KeysProperties (src/config.go) fields per player slot:
    Joystick int   (-1 keyboard, else physical index)
    GUID     string    <-- NEW in the INI era
    up,down,left,right,a,b,c,x,y,z,start,d,w,menu   (named string bindings)
    RumbleOn bool
  Sections: [Keys_P1..] (keyboard) and [Joystick_P1..] (pads); regex-mapped
    "^(?i)Keys_P[0-9]+$" and "^(?i)Joystick_P[0-9]+$" -> any number of players.
  Joystick tokens are now SDL/gamepad NAMES not raw numbers, e.g. DP_U/DP_D/DP_L/DP_R,
    A,B,X,Y, LB,RB,LT,RT, START,BACK. Default [Joystick_P1] GUID is empty.

### Is a pad bound by INDEX or by GUID? (the key question)
  - JSON era (v0.99 and earlier): INDEX ONLY. No GUID stored. No per-controller identity.
  - INI era (master/nightly): a GUID string IS stored per player slot, AND the engine can
    auto-remap slots by GUID at startup so a specific physical pad reclaims its slot
    regardless of enumeration order. Logic in src/system.go: it reads
    input.GetJoystickGUID(joyS), and if the present pad's GUID matches a DIFFERENT config
    slot's stored GUID it swaps them and sets sys.inputRemap[...].
    Lua helper getJoystickGUID(index) exposes the GUID to screenpack scripts.

  *** STEAM DECK / LINUX CAVEAT (verified, important) ***
  The GUID-based auto-remap block in src/system.go is GATED on `runtime.GOOS == "darwin"`
  (macOS only). On Linux (the Steam Deck) that automatic reindex-by-GUID does NOT run in
  current master: the GUID is stored/loaded and usable by the config editor, but at launch
  pads are still taken by numeric index. So even on the INI build, Ikemen does NOT natively
  guarantee "pad family X -> player slot N" on the Deck. External ordering (the controller
  router pinning device enumeration) is still required for stable per-family assignment.
  Sources:
    https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/system.go (~L890-916) (2026-07-19)
    https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/master/src/config.go (KeysProperties, L21-39) (2026-07-19)

--------------------------------------------------------------------------------
## 4. Are .ini and .json key names the same? NO.

The INI rewrite renamed/retyped/restructured keys. Do not assume a 1:1 mapping.
  - VRetrace (json)         -> VSync (ini)
  - MSAA bool (json)        -> MSAA int, powers of 2 (ini)
  - FullscreenWidth/Height + FullscreenRefreshRate (json)  -> removed; WindowWidth/Height (ini)
  - RenderMode, WindowScaleMode, FightAspect*, KeepAspect  -> NEW in ini, absent in json
  - Flat top-level keys (json)  -> sectioned [Video]/[Sound]/[Input]/... (ini)
  - Input: KeyConfig/JoystickConfig ARRAYS with "Joystick"+"Buttons"[] (json)
           -> [Keys_Pn]/[Joystick_Pn] SECTIONS with named up..menu keys + GUID + RumbleOn (ini)
  - Joystick button tokens: raw numeric IDs (json) -> SDL gamepad names DP_U/A/LB/RT (ini)
  Tooling that edits Ikemen configs must branch on which file exists (config.json vs config.ini).

--------------------------------------------------------------------------------
## 5. Per-game / override mechanisms (native, command-line)

From `-h`/`-?` help text in src/main.go (present in both eras):
  -config <path>       Override the config file path (JSON era: replaces save/config.json;
                       INI era: replaces save/config.ini). Lets one binary use many configs.
  -r <path>            Load motif/screenpack <path>, e.g. -r motifdir or -r motifdir/system.def
  -lifebar <path>      Load a specific lifebar .def
  -storyboard <path>   Load a storyboard .def
  -s <stagename>       Load a specific stage
  -p<n> <name> / -p<n>.ai/.color/.power/.life   Quick-VS player setup
  -tmode1/-tmode2, -time, -rounds                Match setup
  -nojoy               Disable joysticks
  -nomusic / -nosound  Disable audio
  -windowed            Force windowed (overrides Fullscreen)
  -togglelifebars, -maxpowermode, -ailevel, -speed, -stresstest, -speedtest   Debug
The persistent per-build override, though, is simply each build having its OWN save/config.*
(and its own Motif key). -config + -r are the runtime overrides.
Source: https://raw.githubusercontent.com/ikemen-engine/Ikemen-GO/v0.99.0/src/main.go (2026-07-19)
        (master help text matches; -config -> save/config.ini)

--------------------------------------------------------------------------------
## 6. Bottom line for the Deck

- Resolution: set GameWidth/GameHeight (render res). Window/fullscreen size is separate
  (FullscreenWidth/Height in JSON, WindowWidth/Height in INI; 0/-1 = follow desktop or
  GameWidth). localcoord is a .def authoring concept, not a config key.
- Per-gamepad-FAMILY stable profiles: NOT natively delivered on the Deck.
    * JSON builds: index-only, no GUID at all -> external management required.
    * INI/nightly builds: a GUID IS stored per slot, but the GUID auto-remap is macOS-only
      (darwin-gated) in current master, so on Linux/Deck it still resolves pads by index
      -> external ordering (controller router) still required.
