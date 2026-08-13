# Sega Lindbergh on the Deck (lindbergh-loader)

Setup + findings for HOTD4 / HOTD4 Special / Rambo. Built 2026-06-25. WORKING on-device
(renders, aims, calibrates, all buttons mapped). Sources cited inline; verified on this rig.

## Emulator
- **Prebuilt lindbergh-loader v2.1.4 AppImage** at `~/Applications/lindbergh-loader.AppImage` (chmod +x).
  NO build needed; the AppImage self-bundles the 32-bit runtime (game ELFs are 32-bit i386).
  Matches ES-DE find-rule glob `~/Applications/lindbergh-loader*.AppImage`. EmuDeck does NOT manage it.
  Releases: https://github.com/lindbergh-loader/lindbergh-loader/releases
- CLI: `--version`, `--list-controllers` (AUTHORITATIVE token names), `--create config` (writes the
  per-game **`lindbergh.ini`** -- NOT `.conf`, a real v2.1.4 trap), `-t` (test/operator mode),
  `-g <dir>`, `-c <cfg>`. `deck`-user is already in `input` group; JVS is software-emulated -> no sudo.

## ES-DE wiring
- Dedicated **`lindbergh`** system in `~/ES-DE/custom_systems/es_systems.xml` (+ `es_systems_sorting.xml`
  "Arcade Lindbergh" + reference copy in launchers/data). Theme + launch screen are user-made
  (`themes/pixel-es-de/lindbergh`). `<extension>.lindbergh`, `<theme>lindbergh`, `<platform>arcade`.
- **Dir-as-file**: each game is `~/ROMs/lindbergh/<name>.lindbergh/` containing `<name>.lindbergh.commands`
  (one line = relative ELF path, e.g. `elf/hod4M.elf`) + `elf/<game>.elf` (chmod +x) + `elf/lindbergh.ini`.
  Command: `controller-router-wrap.sh lindbergh %ROM% "%BASENAME%" "Sega Lindbergh" -- %STARTDIR%=%GAMEENTRYDIR% %EMULATOR_LINDBERGH-LOADER% %INJECT%=%BASENAME%/%BASENAME%.commands`.
  `%BASENAME%` for a dir-as-file INCLUDES the `.lindbergh` extension.
- **Test/Calibrate tiles**: separate `<name>-test.lindbergh/` dirs whose `.commands` = `-t ../<name>.lindbergh/elf/<ELF>`
  (loader test mode, reuses the real game data + ini via the relative path). `%INJECT%` tokenizes flags
  (so `-t <elf>` works). In the gamelist + Pew collection.
- **Sinden auto-start** = membership in the `Pew-Pew-Pew!!!` collection (`require_sinden`) -> the `sinden.sh`
  game-start hook runs the driver. `[systems.lindbergh] inherits="arcade"` in controller-policy.toml.
- gamelist at `~/ES-DE/gamelists/lindbergh/gamelist.xml` (display names). Edit collection/gamelist only
  while ES-DE is CLOSED.

## Display / aspect (the 16:9 stretch)
- Native aspects: HOTD4 1280x768 (~5:3), HOTD4SP 1024x768 (4:3), Rambo 1280x768 (~5:3).
- `KEEP_ASPECT_RATIO=true` is set but the loader only honors it for a FEW specific titles (NOT HOTD4) ->
  it stretched the 4:3/5:3 image to fill 1920x1080.
- FIX: set `WIDTH`/`HEIGHT` to the game's native aspect so the scale is uniform (no stretch), side bars:
  **HOTD4 & Rambo = 1800x1080** (5:3), **HOTD4SP = 1440x1080** (4:3). The loader has NO crop/zoom, so a
  4:3 game can't fill 16:9 without stretch or crop -- side bars are the geometry-correct result.
- Border OFF (`BORDER_ENABLED=false`) -- physical LED strip is the Sinden border. Crosshairs = generated
  PNGs in `~/ROMs/lindbergh/_crosshairs/` (built with a pure-python PNG generator; no PIL/ImageMagick).

## Input (INPUT_MODE=2 EVDEV) -- captured live, NO P1/P2 swap
Gun token map (physical button -> exact loader token), captured with `guncap.py`:
- **P1**: trigger=`SINDENLIGHTGUN_MOUSE__SMOOTHED_P1__BTN_LEFT`, pump=`...P1__BTN_RIGHT`;
  keyboard `UNKNOWN_SINDENLIGHTGUN_KEYBOARD_KEY_*`: Front-Left=ENTER, Front-Right=Z, Rear-Left=ESC,
  Rear-Right=X, d-pad=UP/DOWN/LEFT/RIGHT.
- **P2**: trigger=`...SMOOTHED_P2__BTN_LEFT`, pump=`...P2__BTN_RIGHT`;
  keyboard `UNKNOWN_SINDENLIGHTGUN_KEYBOARD_2_KEY_*`: Front-Left=C, Front-Right=G, Rear-Left=V,
  Rear-Right=H, d-pad=8/5/4/6 (numbers, NOT arrows -- P2 differs from P1).
- Loader `PLAYER_n_BUTTON_X` = JVS switch bit X; the ACTION per bit is the game's. Start=gun Front-Left,
  Coin=gun Rear-Right (each on their own gun).

### Per-game JVS button findings (empirical, on-device)
- **HOTD4 / HOTD4SP grenade = `BUTTON_3`** (NOT button 2, despite arcadeitalia/MAME saying "button 2").
  Mapped to the **pump** (`PLAYER_n_BUTTON_3 = ...BTN_RIGHT`). No reload button (reload is off-screen).
- **HOTD4SP "danger"/escape QTE = the START switch**, NOT a push button (Sinden wiki:
  https://www.sindenwiki.org/wiki/TeknoParrot ; Wiki of the Dead). The QTE polls P1 & P2 Start. Mapped
  **2-player**: each gun's **Front-Right = that player's Start = their danger** (so Front-Right is also the
  start/insert button; Front-Left freed). The seat-motion dodge is separate (irrelevant on a non-moving rig).
- **Rambo rage = `BUTTON_3`** (Front-Right found it) -> mapped to the **pump**. Other Rambo buttons left on a
  profiling spread (Front-Right=B2, Rear-Left=B4, d-pad=B5-8) -- accepted as-is by user.

### Service menu / calibration (off-aim fix)
- The gun TEST button caused **accidental game EXITS** (pressing TEST asserts the JVS test switch ->
  operator menu drops you out of play). So keep TEST/SERVICE **OFF the guns**.
- Calibration is via the **(Test / Calibrate) `-t` tiles** + the **WIRELESS XBOX PAD** (device token
  `XBOX_360_WIRELESS_RECEIVER`): d-pad Up/Down = `BTN_DPAD_UP/DOWN` (move), **A = `BTN_SOUTH` = SERVICE**
  (scroll), **B = `BTN_EAST` = TEST/select** -> GUN ADJUST -> shoot the corner targets with the gun.
  (A/B report as `BTN_SOUTH`/`BTN_EAST` in the loader, NOT BTN_A/BTN_B -- always confirm via
  `--list-controllers`.)

## Capture tooling (in `~/Emulation/tools/launchers/`)
- **`guncap.py`** -- prints the exact loader token per gun button press. REQUIRES the smoother running
  (the Sinden gun only emits once the pipeline has woken it) but **LightgunMono OFF** (LightgunMono GRABS
  the gun devices exclusively -> a passive capture sees nothing). Workflow:
  `sinden-start.sh` then **`pkill -f LightgunMono.exe`** (keeps the smoother), then run guncap.py via Monitor.
  (The auto-mode classifier blocks Claude from killing LightgunMono -- the USER must pkill it.)
- **`xarcade-cap.py`** -- same idea for gamepads/X-Arcade/Xbox pad (captures buttons AND stick axes).
- `/tmp` is tmpfs (wiped on reboot); don't rely on capture logs surviving a reboot.

## Quit + routing (fixed earlier this day)
- The game process runs as `./<name>.elf` (no "lindbergh" in argv), so the quit hook uses a
  self-match-safe `[.]elf` pkill in the game-start hook (launchers `1257436`). Routing `inherits="arcade"`
  (fork `9e164a0`). See memory `esde-hooks-active-copy`, `quit-combo-keyboard`.

## 2 Spicy / "Too Spicy" (4th game, added 2026-06-25)
- Game ID **SBMV**, dev codename **apache** (ELF `apacheM.elf`, test `apachetestM.elf`), Sega 2007,
  **SHOOTING** (2-player co-op lightgun), status **WORKING** in the loader's gameData table. Native
  **1280x768 = 5:3** -> same aspect fix as HOTD4/Rambo: `WIDTH=1800 HEIGHT=1080`. ROM dirs
  `2spicy.lindbergh/` + `2spicy-test.lindbergh/`; in gamelist + Pew collection.
- **Region:** dump is EXPORT (teknoparrot.ini DongleRegion/PcbRegion=EXPORT) -> `REGION = EX` in the ini.
  Loader's ONLY valid REGION values are **JP / US / EX** (config.c strcmp; "EXPORT"/"EXP" are NOT accepted).
- Controls start on the Rambo-style PROFILING SPREAD (trigger=B1 both players; Front-Right/Pump/Rear-Left/
  d-pad on distinct JVS buttons; Start=Front-Left, Coin=Rear-Right) so the user profiles 2 Spicy's extra
  buttons on-device, then finalize. Crosshairs + Xbox-pad calibration identical to the other three.
  PENDING: on-device render/aim/calibration sign-off + button profiling.

## Loader game matching (generic, learned wiring 2 Spicy)
- The loader identifies the running game by **CRC32 of the ELF** (`getGameData(elf_crc)` in gameData.c),
  NOT by filename -> the `apacheM.elf`-vs-codename-`2spicy` mismatch is irrelevant. TeknoParrot "M" ELFs
  print a "not Clean" warning but still run (same as ramboM/hod4M, which are verified-working). The
  supported-games table lives in `src/lindbergh/gameData.c`; each struct has native width/height + the
  SHOOTING/SEGA_TYPE flags. Default branch is **master** (raw github main/ URLs 404).

## Batch of 6 non-gun games added (2026-06-28)
Added from TeknoParrot-style raw dumps in ~/Downloads/_lindbergh (driving/fighting/flight, NOT lightgun,
so NOT in the Pew collection and no Sinden). Each + a matching "(Test)" operator-menu tile:
- abc (After Burner Climax, ELF abc.elf, 640x480 -> 1440x1080 4:3, flight)
- hummer (Hummer Extreme, ELF disk0/hummer_Master.elf, 1280x768 -> 1800x1080 5:3, driving)
- outrun2 (OutRun 2 SP SDX, ELF or2g/disk0/Jennifer/Jennifer.elf, 800x480 -> 1800x1080 5:3, driving)
- id4 (Initial D Arcade Stage 4 Export, ELF id4.elf NOT id4rc which is a wrapper script, 1360x768 -> 1920x1080, driving)
- harley (Harley-Davidson: King of the Road, ELF disk0/elf/chopperM.elf, 1360x768 -> 1920x1080, driving)
- vf5 (REPLACED old "Virtua Fighter 5" with Final Showdown REV B, ELF vf5.elf, 1280x768 -> 1800x1080, fighting).
  Old vf5 moved to /run/media/deck/1tbDeck/_TMP_lindbergh-vf5-replace-20260628-012741.
Layout: .lindbergh dir = the dump's top folder kept INTACT (preserves disk0..disk9 multi-disk trees);
.commands = ELF path relative to that dir; lindbergh.ini next to the ELF; REGION=US (matches all working
setups incl Export id5); input left UNBOUND (empty EVDEV scaffold, bind on-device in MAD). id5 skipped
(already installed + working).

### 2 more added (2026-06-28, same recipe)
- rtuned (R-Tuned: Ultimate Street Racing, ELF dsr renamed to dsr.elf, native 640x480 -> 1440x1080 4:3, driving).
  Dump has dsr/dsr_HD/dsr_VGA + _FFB variants; used base `dsr` (what the original `game` script launches via
  `dsr -fs`); the original also `pushd /mnt/disk2` and passes `-fs` (NOT forwarded through the loader; flag if
  it launches windowed). Try dsr_HD on-device if you want HD and the CRC is recognized.
- racetv (SEGA Race TV, ELF drive.elf already .elf, native 640x480 -> 1440x1080 4:3, driving).
Both: REGION=US, input UNBOUND, + a "(Test)" tile, NOT in Pew. 640x480 native per gameData -> 4:3 1440x1080;
bump to 1920x1080 on-device if the image is actually 16:9 anamorphic.

### INPUT must come from the per-game TeknoParrot XML (2026-06-28)
A generic analog scaffold is WRONG: each game puts wheel/gas/brake on different JVS analog channels.
Source of truth = the game's TeknoParrot GameProfile XML (dumped in ~/Downloads/_lindbergh/_xmls).
Translation: loader ANALOGUE_n maps to JVS channel n-1 (jvs.h: ANALOGUE_1=0), so TeknoParrot AnalogN ->
ANALOGUE_(N+1). Buttons: P1ButtonK->PLAYER_1_BUTTON_K, P2...->PLAYER_2_..., Service1->PLAYER_1_BUTTON_SERVICE,
Coin1->PLAYER_1_COIN, P1ButtonStart->PLAYER_1_BUTTON_START, Test->TEST_BUTTON.
Channel map found (parse_xml.py in scratch did this for all):
- MOST racers (hummer, outrun2, id4, id5, rtuned, racetv): Wheel=Analog0=ANALOGUE_1, Gas=Analog2=ANALOGUE_3,
  Brake=Analog4=ANALOGUE_5. (Confirmed by the WORKING id5: steering ANALOGUE_1, gas ANALOGUE_3.)
- HARLEY is the odd one: Wheel=Analog2=ANALOGUE_3, Gas=Analog0=ANALOGUE_1, Brake=Analog6=ANALOGUE_7.
- abc (flight): JoyX=ANALOGUE_1, JoyY=ANALOGUE_3, Throttle=Analog4=ANALOGUE_5; Gun/Missile/Climax=P1Button1/2/3.
- vf5 (fighting): P1/P2 dpad + Punch/Kick/Block = P1/P2 Button1/2/3 (no analog).
Per-game digital quirks: single-seat racers wire shifter/boost/gears to the P2 JVS block
(hummer Boost=P2ButtonDown; outrun2/racetv GearUp/Down=P2ButtonUp/Down; id4/id5 Gear1-6=P2Button1-6;
rtuned Shift=P2ButtonUp/Down, BoostL=P2Button1, BoostR=P1ButtonRight, EnterCard=P1ButtonUp; View=P1ButtonDown
on most, P1Button1 on id4). Fixed abc/hummer/outrun2/id4/rtuned/racetv inis to match (bound to
WIRELESS_CONTROLLER); harley fixed separately; vf5/id5 already correct. .ini.bak2 backups kept next to each.

### BOOT FAILURES found 2026-06-28 (harley + rtuned didn't start)
Diagnosed from the REAL launch error in ~/ES-DE/logs/es_log.txt (headless reruns are useless: they die at
"SDL could not initialize: x11 not available" before the game loads data, so the real fault never shows).
- HARLEY: game (chopperM.elf, runs from disk0/elf, opens ../fs/shader/16box.frag) died on
  "No such file or directory". The dump SPLIT the logical fs into disk0/fs/ (only .sfd movies) +
  disk0/fs2/ (shader, data, sound, sofdec, compiledshader, occbin, inc, textparam). FIX: merged fs2's
  data dirs INTO fs/ (mv, same fs, instant; only [SYS]/lost+found left in fs2). Now ../fs/shader resolves.
  REUSABLE: a raw dump with both fs/ and fs2/ under disk0 needs them merged (game opens everything via ../fs).
- RTUNED: ROOT CAUSE = the dump shipped a REAL libsegaapi.so in the game root. lindbergh-loader bundles its
  OWN stub libsegaapi.so + libkswapapi.so (in the AppImage usr/lib32) and the WORKING id5 has NO .so in its
  game root, so the loader stub is used. R-Tuned's root libsegaapi.so shadowed the stub and pulled in the real
  Sega ssound stack -> first "libosuser.so: cannot open", and if you (wrongly) supply libosuser then
  "DevReg initialization failed / Could not acquire required interfaces" and the game exits right after
  Starting. FIX = REMOVE the dump's libsegaapi.so (and any ssound libs) from the game root so the loader stub
  is used (moved to rtuned.lindbergh/_disabled_libs/). Do NOT copy the real ssound libs in (that was a wrong
  turn: the 22-24 byte .so are Sega version-redirect text stubs, the real ELF is the -2.07.0000 file, but you
  want NEITHER in root). All this is a runtime dlopen AFTER SDL -> NOT reproducible headless.
  RULE: a game dir should have NO libsegaapi.so/ssound libs; let the loader stub them (check vs id5).
  Region: EXPORT dump -> REGION=EX. rtuned ships dsr + dsr_HD (same crc) + dsr_VGA + _FFB variants;
  used base dsr (renamed dsr.elf). The loader's getGameData CRC (config.h R_TUNED=0xa68d053d) is computed
  over part of the ELF, NOT whole-file zlib, so you can't pick the variant by whole-file crc; the loader runs
  any ELF anyway (CRC only selects config), so variant is not the boot blocker.

### REGION must match the dump's dongle (align ini REGION to teknoparrot.ini DongleRegion)
DongleRegion EXPORT -> EX, JAPAN -> JP, USA -> US. Audit 2026-06-28 (some are user-set mid-experiment):
hummer(EXPORT)=US, outrun2(EXPORT)=JP, racetv(JAPAN)=JP[ok], rtuned(EXPORT)=EX[fixed]; abc/id4/harley/vf5
have no dongle region (US fine). Mismatches don't always block boot but are the first thing to align.

### QUIT GOTCHA for raw dumps (IMPORTANT, reusable)
The game-end quit hook reaps the game only if its /proc/<pid>/exe ends in ".elf" (see
hooks/game-start/quit-combo-watcher.sh). Raw dumps whose main binary is NOT named *.elf (abc, vf5, Jennifer)
would survive the quit combo = WEDGE (the old `vf5` had this latent bug). FIX: rename the binary to add a
.elf suffix (loader detects games by ELF CRC, NOT filename, so a rename is safe) and point .commands at it.
TeknoParrot "M" ELFs (ramboM.elf, hod4M.elf) and id4.elf/hummer_Master.elf/chopperM.elf already end in .elf.

### OWED on-device (no display here, user verifies)
Per game: boots/renders, aspect correct, controls (bind in MAD). Likeliest tweaks: OutRun2 uses the plain
`Jennifer` ELF (variants Jennifer_VGA/_LOWRES/_patched exist - swap if CRC not recognized); REGION=US is the
first thing to flip to EX if an Export dump refuses to boot. Source .7z files still in ~/Downloads/_lindbergh
(~20GB, removable once verified).

## Status (2026-06-25)
WORKING + user-accepted: install, ES-DE system + tiles, rendering, aim, in-game calibration, aspect fix,
and all button mappings for the 3 original games (HOTD4, HOTD4SP, Rambo). Both guns mapped (no swap).
4th game **2 Spicy WIRED 2026-06-25** (same recipe; profiling-spread controls) -> on-device sign-off +
button profiling pending. Reusable capture tools saved.
