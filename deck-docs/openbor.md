# OpenBOR on the Deck

Researched 2026-07-13. Question asked: "is there a Linux OpenBOR that will run all my
current OpenBOR games without issues?"

## ANSWER: No. Keep the bundled Windows engines under Proton.

Our 34 games ship ~20 DIFFERENT engine builds between them. There is no single engine,
Linux or Windows, that runs them all. Our current setup (openbor.sh runs each game's own
bundled .exe under GE-Proton10) is not a workaround, it is what the OpenBOR team
recommends. Do not "upgrade" the collection to one native engine.

## The official position (primary sources)

- OpenBOR team member O Ilusionista, ChronoCrash thread 8185 "OPENBOR - EMUDECK/BATOCERA",
  2026-01-02, answering EXACTLY this question (a user wanting Evil Dead + Contra + the
  Marvel games in one OpenBOR folder on a Steam Deck):
  "you need to use the same build that the developer used to create the game (which are
  usually provided with the games). Using different versions can lead to various problems,
  including not working at all."
  https://www.chronocrash.com/forum/threads/openbor-emudeck-batocera.8185/
- Project lead Damon Caskey (DCurrent), official 4.0 release notes, 2023-12-31:
  "moving forward, I am striking the forced legacy compatibility policy" and
  "The idea of using one copy of OpenBOR to open a big bag of games was originally intended
  as an add-on convenience, not the primary engine design, and this functionality is
  phasing out."
  https://www.chronocrash.com/forum/resources/openbor.1/updates
- DCurrent again, 2026-05-28: "The game was built with that engine version. Play it with
  what it came with."
  https://www.chronocrash.com/forum/threads/games-crash-in-openbor4-but-not-in-openbor3.8363/
- There is NO compatibility mode / "run as 3.0" switch. Nothing in the repo offers one.

## Official Linux builds

- Newest official Linux binary: OpenBOR 4.0 Build 7533, x86_64 AppImage,
  published 2024-01-01. Also an x86 32-bit AppImage.
  https://github.com/DCurrent/openbor/releases (tag v7533)
- Older Linux AppImage: v3.0 Build 6391 (2018-08-21), still downloadable.
- ChronoCrash's resource page advertises 4.0 Build 7735 (2025-05-04) but lists only
  Android / Wii / Windows. So 7533 is still the newest official LINUX binary.
- Repo is alive (master pushed 2026-07-12). CI builds Ubuntu-x64 on every push, but those
  are GitHub Actions ARTIFACTS: they expire and need a login. No stable nightly page.
- Third-party: AUR openbor-bin 4.0.7533-2 (repackages the official build); Snap openbor4
  (unofficial). No Flathub package. EmuDeck does NOT integrate OpenBOR (issue #923).
- THERE IS NO LIBRETRO OPENBOR CORE. libretro/libretro-openbor and libretro/openbor both
  404. RetroArch cannot run OpenBOR games. Batocera/RetroPie run standalone binaries.

## How a native engine loads games (read from engine/sdl/sdlport.c, master)

- Paths are relative to the PROCESS CWD, not the binary. Paks/, Saves/, Logs/,
  ScreenShots/ are auto-created there. No chdir on Linux. So a shared engine works by
  cd-ing into each game folder and running it there.
- No argument: engine shows its built-in pak-select menu, scanning Paks/ for *.pak
  (excluding menu.pak).
- argc == 2 and the file exists: it loads that pak directly and skips the menu.
  Added 2021-03-01 (commit 63b04f0f). Present in 7530/7533+, NOT in 3.0 build 6391.
  Path must be under 256 chars (MAX_FILENAME_LEN).
- Only other args: offscreenkill=N and showfilesused=N (both must be argv[1]).
  No --fullscreen-style flags; video/input/audio live in the config file.
- Can also run an extracted lowercase data/ folder instead of a pak (remove/rename Paks/).
- Logs to Logs/OpenBorLog.txt in the CWD. FIRST LINE FINGERPRINTS THE ENGINE, e.g.
  "OpenBoR v4.0 Build 7530 (commit hash: 9695908), Compile Date: Jan  1 2024".
  This is how we identified every engine in our library. Reuse this trick.

## What actually breaks between 3.0 and 4.0 (concrete, from the 4.0 changelog)

- Model commands renamed/merged: jumpmove + walkmove become air_control;
  subject_to_* become move_constraint; falldie + nodieblink become death_config;
  nodrop/nopain/backpain become pain_config;
  nopassiveblock/holdblock/blockback become block_config.
- get_global_config() / set_global_config() now take CONSTANTS, not strings (Build 7556).
- Entries moved from openborconstant() to openborvariant(): PLAYER_MIN_Z, PLAYER_MAX_Z,
  SCREENPANEL_Z, PANEL_Z. Sound sample constants renamed.
- levelorder commands removed (e.g. versusdamage "not understood in level order").
- Legacy smartbomb in a character header CRASHES 4.0. This is the documented cause of
  Golden Axe Genesis (WE HAVE IT) crashing on magic attacks under v4 while fine on v3.
- Projectile system deprecated in favour of a new Child system.
- FAILURE MODE: the models.txt / levels parser ABORTS on an unknown/renamed command, so a
  bad pak will not even load. Script breakage instead surfaces at runtime, usually on
  specials/magic.
- SAVES: OpenBOR dev msmalik681 warned that changing engine version "could introduce bugs
  into your mod and corrupt save/configuration data". No migration path.
- Nuance from DCurrent: "3.0" spans nearly a decade. A LATE 3.0 game may need only a tweak
  on 4.0; an EARLY 3.0 game would take substantial work. Team member Danno: "think of it as
  trying to run Unreal engine 3 games on Unreal engine 4."

## OUR LIBRARY, MEASURED (2026-07-13, from each game's Logs/OpenBorLog.txt)

Total 34 games in ~/OpenBor (ROM dir ~/ROMs/openbor).

- 10 games on STOCK 4.0 Build 7530 (Jan 1 2024) = the only realistic native-Linux
  candidates (official 7533 AppImage differs only in build meta / copyright date):
  DD_FINAL, DD_III, jll, Justice_League_United, Maximun_Carnage_Returns,
  Neon_Lightning_Force_1.5_demo, Silver_Nights_Crusaders, TMNT_Recolored_and_Extended,
  TMNT_RP_1_1_5, wargems.
- 3 games on STOCK 3.0 Build 6392 (2021-03-29), one commit past official v6391:
  evildead, killbill, simpsons.
- 1 on 3.0 Build 3789 (2013): Contrav2.
- 1 on 3.0 Build 2862 (2010): Jennifer_By_MasterDerico.
- 19 games on SELF-COMPILED engines with an EMPTY build number, i.e. the banner reads
  "OpenBoR v3.0 Build , Compile Date: <date>". An official release ALWAYS stamps the build
  number, so these were built from source by the game authors. Compile dates 2013 to 2023.
  NO OFFICIAL BUILD MATCHES THEM ON ANY PLATFORM, so there is no Linux engine to swap in.
  AvengersUnitedBattleForce, BDD_The_Revenge_v.9, CAPAv104, CARNAGEv101,
  DD_Reloaded_Alternate_5.1.1, DD_Remix, Dungeons_and_Dragons_-_Animated_Series, GHDC,
  Golden_Axe_Genesis_v3.0_Build_4086, Golden_Axe_Myth, Golden_Axe_Returns, GUG, he-man-pc,
  MFA2, MIW_Definitive, PUNIv1, UDD_ver3.0, vsr_kottono_edition, XMEN_MAv1.

### Gotchas verified on our own files

- All SIX ZVitor games (CAPAv104, CARNAGEv101, PUNIv1, XMEN_MAv1, MIW_Definitive,
  Dungeons_and_Dragons_-_Animated_Series) run a BYTE-IDENTICAL engine,
  sha256 ac8096da77c498bed15387d1133cef5efd63935d315f55fcd189f66b4e77dfce,
  a 2023-05-12 self-build sitting between official 6391 and 7530. Whether he also PATCHED
  the source is UNKNOWN (the empty build number proves only "not an official release").
- evildead/evil.exe (280MB) and killbill/killbill.exe (165MB) have NO .pak on disk. They
  are NOT OpenBOR "self-contained" exes (that mode has never existed; packfile.c only ever
  opens a pak path, the Paks/ menu, or a loose data/ folder). They are wrapped with
  ENIGMA VIRTUAL BOX (PE sections .enigma1/.enigma2), a Windows tool that mounts the game
  data as a virtual FS at runtime. A native engine has nothing to load. Un-wrapping needs a
  third-party Windows EVB extractor. THESE TWO CAN ONLY RUN UNDER WINE/PROTON.
- GHDC has a 0-byte bor.pak plus loose Data/ + sprites/ + gui/ = the extracted-data-folder
  layout, 2014-era engine.
- A RENAMED exe is usually just branding, not a fork. Proof in our own tree:
  Golden_Axe_Myth ships OpenBOR.exe AND OpenBOR_original.exe AND resource_hacker.zip.
- Official pak tool works on Linux: tools/borpak/ in the repo (build with ./build.sh lin).
  borpak [-d DIR] [-b] [-f pak32|pak64] [-l] [-p PAT] <file.PAK>; default action EXTRACTS.
  paxplode <file.pak> is the frontend. New PAK64 format (borpak v0.4, June 2026) requires
  OpenBOR 4.x.

## What other distros do (nobody uses one engine)

- BATOCERA is the decisive data point: es_systems.yml exposes FOUR OpenBOR engines side by
  side (openbor4432, openbor6412, openbor7142, openbor7530) and openborGenerator.py
  AUTO-SELECTS the engine by parsing a build number out of the pak FILENAME:
    version < 6000 -> openbor4432
    version < 6500 -> openbor6412
    version < 7530 -> openbor7142
    else           -> openbor7530
  It chdirs into the rom dir, passes the pak as argv[1], keeps a separate config INI per
  engine. Their openbor7530 is built from tag v7533 + 4 patches (deps: SDL2, libpng, libogg,
  libvorbis, libvpx). Wiki caveat: some games need the pak NOT renamed and in a Paks/ folder.
  (Our "Golden Axe Genesis [Ver. 3.0][v.3.0 Build 4086].pak" is named in exactly that
  convention.)
- RetroPie community scripts install ONE engine and run extracted folders; they do not
  address version compat at all.
- RetroDECK bundles OpenBOR; games go in as uncompressed folders.
- Positive report: ChronoCrash thread 5989 "Openbor on Steam Deck", a user got "10 games
  working perfectly" on the Linux AppImage. Ten, not a whole library. Matches our numbers.

## If we ever DO want a native slice (the only sane option, NOT currently done)

Hybrid, mirroring Batocera: official 7533 AppImage for the 10 stock-7530 games (cd into the
game dir, pass the pak as argv[1]); Proton for the other 24. Buys native speed + clean SDL
controller handling (would sidestep the winebus IGNORE-list hack in openbor.sh) for under a
third of the library, at the cost of a second code path and a save-corruption risk.
Judged NOT worth it on 2026-07-13; the Proton path already works.

## Sources

- https://github.com/DCurrent/openbor (repo, releases, CI, engine/sdl/sdlport.c,
  engine/source/packfile.c, tools/borpak/)
- https://www.chronocrash.com/forum/resources/openbor.1/ and .../updates (official 4.0 notes)
- https://www.chronocrash.com/forum/threads/openbor-emudeck-batocera.8185/ (2026-01-02)
- https://www.chronocrash.com/forum/threads/games-crash-in-openbor4-but-not-in-openbor3.8363/
- https://www.chronocrash.com/forum/threads/openbor-3-0-to-4-and-hyperspin-questions.7530/
- https://www.chronocrash.com/forum/threads/openbor-on-steam-deck.5989/
- https://www.chronocrash.com/forum/threads/openbor-linux.4157/page-2 (AppImage, borpak, data folder)
- https://www.chronocrash.com/forum/threads/working-openbor-games-for-android.6388/ (compat list)
- https://chronocrash.com/obor/wiki/ (official manual)
- https://github.com/batocera-linux/batocera.linux (es_systems.yml, openborGenerator.py)
- https://wiki.batocera.org/systems:openbor
- https://retrodeck.readthedocs.io/en/latest/wiki_engine_guides/openbor/openbor-guide/

## OpenBOR input config (.cfg) binary format — for MAD input editing (2026-07-16)

Investigated 2026-07-16 for the "edit OpenBOR controls in MAD" feature. Sources: OpenBOR
source (DCurrent/openbor savedata.c/control.c, via a verify workflow) + on-device byte diffs
of our own `Saves/*.cfg` + OpenBorLog.txt joystick enumeration.

- Controls live in a per-game BINARY blob `<gamedir>/Saves/<pakname>.cfg`. There is NO global
  OpenBOR config; config is inherently per-game-folder. The engine writes it on quit (raw
  `fwrite` of the `s_savedata` struct, NO checksum/hash). `loadsettings` validates only dword0
  (`compatibleversion`) against the engine's own constant; since each game bundles the engine
  that wrote its file, dword0 already matches. => an in-place splice that preserves dword0 +
  byte length and overwrites only the control-keys ints is accepted unconditionally.
- The control map is `keys[MAX_PLAYERS=4][~13]` int32 LE. Slot order (SDID_*, constant across
  builds): UP, DOWN, LEFT, RIGHT, ATTACK1, ATTACK2, ATTACK3, ATTACK4, JUMP, SPECIAL, START,
  SCREENSHOT, ESC. Unmapped sentinel = -999 (0xFFFFFC19). Keyboard binds = small scancodes
  (<~300); joystick binds = `601 + port*64 + offset`.
- **Keycode = 601 + port*64 + within-device-offset.** `port` = the joystick ENUMERATION index
  (0..3), NOT a device GUID/identity (Model2-like ordinal). `offset` sub-layout per device =
  buttons [0..NB-1], then axes [NB .. NB+2*NA-1] (each axis = 2 dirs), then hats
  [NB+2*NA .. +4*NH-1] (each hat = 4 dirs). Verified: an XInput pad (NB=11, NA=6, NH=1) puts
  buttons 0-10, axes 11-22, HAT at 23,24,25,26. So a d-pad reported as a hat lands at 23-26.
- **keys[] byte offset is NOT constant across engine builds** and does NOT track dword0. Three
  layouts in our library: size 248 -> keys@0x18 (1 game, build 2862/2010); size 324 -> keys@0x28
  (13 games, incl. official 4.0 build 7530); sizes 332/340/348/352 -> keys@0x34 (self-builds
  2013-2023 + build 6392). 332/340/348/352 differ only in the TAIL, so keys@0x34 is stable
  within that family. A `<gamedir>/Saves/default.cfg` may sit next to the real `<pak>.cfg` (pick
  the non-default). => locate keys[] by a size-class->offset table + validate all ints are
  keycode-range/sentinel; special-case the 248-byte 2010 file.

## X-Arcade under Proton — the hard case (2026-07-16, MIW_Definitive on-device)

- OpenBORLog.txt: `2 joystick(s) found! 1. XInput Controller #1 - 6 axes, 11 buttons, 1 hat;
  2. XInput Controller #2 - ...`. The X-Arcade (Xbox mode 045e:02a1) presents its TWO USB
  interfaces (:1.0 / :1.1) as two identical XInput pads; its stick d-pad = the HAT (offsets
  23-26), which is why directions bind rotated easily.
- **openbor.sh's `SDL_JOYSTICK_DEVICE=:1.0` P1 pin is INEFFECTIVE under Proton.** That is a
  native-LINUX-SDL hint; OpenBOR.exe is a WINDOWS SDL2 app getting pads from Wine/XInput, which
  never sees the Linux env var. So which half is "Controller #1" (OpenBOR port 0 = P1) is decided
  by Wine/XInput, not us. Observed 2026-07-16: player halves SWAPPED (physical P1 read as P2) +
  d-pad rotated, because the saved .cfg was configured under a different enumeration.
- USER CLUE (to chase next): 2-player X-Arcade OpenBOR worked at some point "when we disabled the
  steamdeck pad for openbor games". The Steam Deck virtual pad (28de:11ff, presents as an XInput
  "Microsoft X-Box 360 pad") likely takes an XInput slot and shifts the X-Arcade halves. NOTE the
  SDL `sdl_ignore` blocklist may not remove it from Wine's XInput layer. Fix path if enumeration
  proves non-deterministic: pin XInput/winebus device order at the WINE level (research proper
  method; do not guess). DS4 and the Deck are single pads => no half-swap; X-Arcade is the only
  hard pad.

## PAD GEOMETRY IS PER-ENGINE (2026-07-16, on-device — read this before touching offsets)

An OpenBOR joystick offset is `buttons + 2*axes + dir`, so it depends on how THAT engine's
SDL enumerates the pad — and our engines disagree about the SAME physical pad:

    "UNKNOWN (XInput Controller #1) - 6 axes, 11 buttons, 1 hat(s)"   SDL2 engines  -> hat base 23
    "Wine joystick driver - 5 axes, 10 buttons, 1 hat(s)"             pre-SDL2      -> hat base 20

Census of our 34 games (via `python3 -m lib.openbor_cfg inventory ~/OpenBor`): **29 XInput view,
4 Wine-joystick view** (Contrav2 2013, Jennifer 2010, + 2), 1 no-pad-line, 1 skipped 248.
=> NEVER hardcode a base. Read the pad line from the game's own `Logs/OpenBorLog.txt`
(`openbor_cfg.pad_geometry`) and derive offsets (`openbor_maps.offsets_for`); refuse if absent.
Verified: the derived map reproduces Miquel's hand-made Contrav2 d-pad byte-exactly
(621/623/624/622 = offsets 20-23) and predicts his atk2=615 as the LEFT trigger (10+2*2+1).
Buttons 0..9 keep the same order under both drivers (A=0,X=2,Y=3,RB=5,Start=7 confirmed);
only Guide(10) is XInput-only. A 5-axis view has NO axis 5 => `ax:rt` is inexpressible there
(SPECIAL lands unmapped on those 4 games — a known gap, not a wrong binding).
Same one-launch staleness as the engine fingerprint: the log describes the PREVIOUS run.

## winebus / virtual-pad facts (2026-07-16, research fleet + on-device spike, PROVEN)

- **`SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT` (whitelist) WORKS and WINS** for OpenBOR under
  Proton: Wine source (`bus_sdl.c` ~925, `bus_udev.c` ~1740 call `is_sdl_ignored_device()`) +
  on-device control run (whitelist = a nonexistent vid:pid -> "No Joystick(s) Found!").
  LANDMINE: an EMPTY whitelist string hides EVERY pad (`if(whitelist)` is true for "").
- **★ EXCEPTION — winebus EXEMPTS Steam's virtual Deck pad (28de:11ff) from the whitelist.**
  It walks straight past `..._EXCEPT` and, being created at boot, holds the LOWEST node => it
  takes **port 0** and shifts every other player up a seat (with 4 pads, the 5th device exceeds
  OpenBOR's 4-port limit and the last pad is simply lost). The ONLY thing that hides it is an
  explicit `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x28de/0x11ff,0x28de/0x1205` blocklist.
  This was removed from openbor.sh as "dead code" on 2026-07-16 (the whitelist-wins finding is
  true for ORDINARY pads only) and it BROKE docked seating; restored same day (`1714eef`).
  **28de:11ff EXISTS ONLY INSIDE GAME MODE**, so no headless test can see this — a headless probe
  will "prove" the whitelist sufficient and be wrong. Never delete that blocklist without a
  Game-Mode test.
- **winebus NORMALIZES every uinput pad to the canonical XInput shape** (11 btn / 6 axes / 1 hat,
  hat base 23) and renames it `UNKNOWN (XInput Controller #N)`, whatever the device declares
  (proved: 11/10/9-button twins all logged as 11 buttons). => the engine's log can NEVER identify
  which virtual pad took which port; do not try the distinct-signature trick.
- **A uinput virtual pad reaches OpenBOR-under-Proton in Game Mode, and synthesized presses
  register in-game** (proven on-device 2026-07-16: capture screen bound "P1 button 10" from a
  scripted press, hands-off; saved cfg int 610 = port 0 offset 9). Vpad must exist BEFORE launch
  (startup enumeration; hotplug unproven on the 2023 engines). Precedent: the Deck's own controls
  ONLY ever reach Proton games as Steam's uinput virtual pad 28de:11ff.
- **winebus NORMALIZES any wrapped pad to canonical XInput 11 buttons / 6 axes / 1 hat (hat base
  23)** regardless of the uinput device's declared shape (a deliberate 9/4/1 pad still enumerated
  as 11/6/1, "XInput Controller", rumble:yes always). Button ORDER = classic XInput: A,B,X,Y,LB,RB,
  Back,Start,ThumbL,ThumbR(=9),Guide(=10) - NOT the raw uinput declaration order. OpenBOR's
  controls UI displays buttons 1-BASED.
- **★★ EVERY VIRTUAL PAD NEEDS ITS OWN vid:pid — this is what pins the player seats.**
  **Wine enumerates its HID registry keys ALPHABETICALLY: the string order IS the port order.**
  The key (in `$PREFIX/pfx/system.reg` — note `pfx/` under a Proton prefix, NOT the prefix root):
  ```
  ##?#HID#VID_4D41&PID_0002&IG_00#1&03004281414D00000200000001000000.0&0&0&1#{guid}
                        ^^^^ decides first        ^^^^ crc16(NAME), decides when pids tie
  GUID = bus(0300) + crc16(name) + vid(414D) + pid(0200) + version(0100)
  ```
  **The GUID INCLUDES crc16 of the device NAME**, so same-pid twins do NOT collide — they are
  ordered by a hash of their names. Measured 2026-07-16: `crc16("MAD OpenBOR P1") = 0x8142` and
  `crc16("MAD OpenBOR P2") = 0x8002` (SDL crc16: poly 0xA001, init 0 — both reproduced exactly from
  the names), so `0280` sorted ahead of `4281` and **P2 silently took port 0** = the docked P1/P2
  swap. **Fix (`a65dd08`): one pid per player, P1=0x0002 .. P4=0x0005** — the pid sits EARLIER in
  the key than the GUID, so it decides before the name hash can. Keep the whitelist generated from
  the same code (`mad-openbor-pads.sdl_whitelist()`); a forgotten pid = a player the game cannot see.
  The same rule explains the **Steam Deck phantom** (`VID_28DE` sorts before `VID_4D41` -> port 0).
  ELIMINATED BY EXPERIMENT (do not re-investigate): node number, sysfs `inputNN`, creation order —
  reversing creation moved event28->event30 and input776->input777 and the seats did not budge. The
  kernel DOES allocate `inputNN` in creation order (measured); it just does not matter, because the
  sort key is a pure function of vid + pid + name.
  **CORRECTION (`35a43cb`):** the first write-up of this said the GUID ignores the name and that the
  order came from stale `.N` suffixes. FALSE — read off a truncated `head -10` that cut the second
  GUID family below the fold. Read the WHOLE grep before concluding a mechanism.
- **DualSense goes through hidraw** when whitelisted (14 buttons/6 axes/1 hat, hat base 26,
  triggers-as-buttons, "Wireless Controller", rumble:no); `PROTON_DISABLE_HIDRAW` exists in
  GE-Proton10 to force it onto the normalized path (found in research, NOT yet verified on-device).
- **`controller-policy.local.toml` deep-merges OVER `controller-policy.toml`** (`lib/policy.
  load_merged`); MAD's UI writes user edits there. Edit the LOCAL file to change effective policy.
- SteamOS costs for uinput: NONE (/dev/uinput ACL'd to deck; Valve udev rules in /usr/lib survive
  updates; python-evdev in base image; gamescope ignores joysticks).

## Engine input model: can stick + d-pad BOTH drive movement? (2026-07-16)

Read from AUTHORITATIVE source (DCurrent/openbor) at master, tag v7530 (our 4.0 games),
tag v6391 (our 3.0 line) and tag v4696 (2014, our 0x34-family self-build era).
Files: engine/sdl/control.c, engine/sdl/control.h, engine/sdl/joysticks.h,
engine/source/savedata.h, engine/openbor.c.

### VERDICT: NO. There is no native simultaneous stick + d-pad, on ANY generation.

- Binding is strictly ONE int per control slot. `apply_controls()` (openbor.c) copies
  `savedata.keys[p][SDID_X]` 1:1 into `playercontrols->settings[]`, one int per slot.
- `control_update()` (control.c) resolves a joystick bind by EXACT BIT EQUALITY:
      int portnum = (t-JOY_LIST_FIRST-1) / JOY_MAX_INPUTS;
      int shiftby = (t-JOY_LIST_FIRST-1) % JOY_MAX_INPUTS;
      if((joysticks[portnum].Data >> shiftby) & 1) k |= (1<<i);
  One slot -> one bit of `joysticks[port].Data`. Hat-up and stick-up are DIFFERENT bits,
  so a slot bound to the hat can never fire from the stick.
- ZERO occurrences of `analog`, `deadzone`, or `SDL_GameController` anywhere in the shipped
  engine. There is NO analog movement path at all: OpenBOR movement is 8-way digital.
- The stick is DIGITIZED into 2 pseudo-buttons per axis at a hardcoded threshold:
      #define T_AXIS 7000
      axis = SDL_JoystickGetAxis(joystick[i], j);
      if(axis < -1*T_AXIS)  { joysticks[i].Axes |= 0x01 << (j*2); }
      if(axis >    T_AXIS)  { joysticks[i].Axes |= 0x02 << (j*2); }
  T_AXIS is a #define, NOT a savedata field. Not user-settable. => merging the stick into
  the d-pad loses NOTHING; OpenBOR never reads a stick as analog.
- Data bit layout confirmed from source (matches our measured offsets):
      Data  = Buttons;
      Data |= Axes << NumButtons;
      Data |= Hats << (NumButtons + 2*NumAxes);
- Only secondary bind array = `default_control`, and it is USELESS here: player 1 ONLY,
  KEYBOARD ONLY, and only when nothing else is pressed:
      if (player <= 0 && !k) { ... if(t >= SDLK_FIRST && t < SDLK_LAST) ... }

### Unchanged across ALL our engine generations
`control_update()` is byte-identical at v6391 (3.0) and v7530 (4.0) and master. v4696 (2014)
is the same design (loop bound 32 not 64, literal 7000 not T_AXIS). Even the UNMERGED
`input-rework-revival` branch (2024, SDL_GameController rewrite) keeps one keycode per slot
(`is_key_pressed(device, device->mappings[i])`) and the same 7000 threshold. Upstream has
never shipped multi-bind.

### s_savedata: no input option beyond keys[]
Fields are `compatibleversion, gamma, brightness, soundvol, usemusic, musicvol, effectvol,
usejoy, mode, windowpos, keys[4][13], joyrumble[4], showtitles, videoNTSC, swfilter, logo,
uselog, debuginfo, fullscreen, stretch, screen[1][2], (vsync/fpslimit), usegl, hwscale,
hwfilter`. Input-related = ONLY `keys[4][13]`, `usejoy` (global joystick on/off) and
`joyrumble[4]`. NOTHING to enable analog. MAX_BTN_NUM 13 / MAX_PLAYERS 4 constant on every
generation checked. NOTE v4696-era struct has NO joyrumble (explains our size classes).

### Gotchas
- `safe_set()` (openbor.c) makes the IN-ENGINE Options->Controls menu enforce unique binds:
  assigning a code already used by another slot SWAPS the old code into that slot. It does
  NOT run on load, so a MAD-written .cfg is not subject to it. Do not rely on duplicate ints.
- LATENT ENGINE BUG: `Hats` is u32 and `Hats << (NumButtons + 2*NumAxes)` is evaluated in u32
  BEFORE promotion to u64 Data. If a pad reports NumButtons + 2*NumAxes >= 32 the hat bits are
  lost/UB. All our pads are safe (Deck/X-Arcade 11+12=23; DualSense 14+12=26) but a
  many-button pad would silently break hat binds.
- Engine uses the RAW SDL_Joystick API, never SDL_GameController => SDL_GAMECONTROLLERCONFIG
  and gamecontrollerdb mappings CANNOT remap it. Combined with the known "Windows SDL under
  Proton never sees Linux SDL env vars", any stick->d-pad merge must happen at the LINUX EVDEV
  level (uinput virtual pad feeding winebus), not via SDL config.

### Consequence for the MAD feature
Making stick AND d-pad both move the character is IMPOSSIBLE inside the .cfg. It requires a
uinput virtual pad that ORs the physical stick into the hat, exposed to Wine while the physical
pad is hidden via the winebus IGNORE list openbor.sh already has. Scope note: X-Arcade has NO
analog stick (its joystick IS the hat), so the merge is only needed for DualSense/DS4 and the
Deck handheld pad.

Sources (fetched 2026-07-16):
- https://github.com/DCurrent/openbor/blob/master/engine/sdl/control.c
- https://github.com/DCurrent/openbor/blob/master/engine/sdl/control.h
- https://github.com/DCurrent/openbor/blob/master/engine/sdl/joysticks.h
- https://github.com/DCurrent/openbor/blob/master/engine/source/savedata.h
- https://github.com/DCurrent/openbor/blob/master/engine/openbor.c (apply_controls, safe_set)
- https://raw.githubusercontent.com/DCurrent/openbor/v7530/engine/sdl/control.c
- https://raw.githubusercontent.com/DCurrent/openbor/v6391/engine/sdl/control.c
- https://raw.githubusercontent.com/DCurrent/openbor/v4696/engine/sdl/control.c
- https://github.com/DCurrent/openbor/tree/input-rework-revival (unmerged, 2024-04-26)

## Art + metadata sources for OpenBOR fan games (2026-08-01)

Filling the last media/description gaps in our 36-game collection. The lesson: OpenBOR
fan games are in NO conventional scraper DB (no ScreenScraper, no TheGamesDB, no
libretro thumbnails), so art comes from a LADDER of sources, each verified before use.

### The ladder, in the order openbor-fetch-media.py applies it

1. **Local Steam grid** (`copy_art`, pre-existing). Only works for games added to Steam.
   Covered 24/36. The other 12 have no shortcut at all, so this path can never help them.
2. **SteamGridDB** (`--sgdb`, lib/sgdb.py). Has non-Steam fan games. grids->covers,
   logos->marquees, heroes->fanart.
3. **LaunchBox Games DB** (`--launchbox`, lib/launchbox.py). THE ONLY SOURCE WITH REAL
   BOXART for these fan games. Platform id 139 "OpenBOR", ~404 entries.
4. **yt-dlp video frame** - last resort placeholder, and it must be REPORTED as such.

### SteamGridDB gotchas (both cost real time)

- **Cloudflare 403s Python's default `Python-urllib/3.x` User-Agent.** Symptom: every
  lookup "finds nothing" while the SAME url works under curl. Send a browser UA.
- **`/search/autocomplete` is fuzzy and ALWAYS returns something.** Taking `data[0]` is
  how you plaster the wrong game's art. Measured on our own collection:
      "The Punisher and Nick Fury" -> "The Punisher"          (1993 Capcom arcade)
      "Showdown Revenge"           -> "Samurai Shodown IV"
      "Ultimate Double Dragon"     -> "Battletoads/Double Dragon"
  All three had 20-50 grids ready to download. Fix = normalise both titles (strip
  bracketed notes, version numbers, filler words) and require >=0.85 similarity.
  `lib/sgdb.find_game()` does this; `lib/launchbox.find()` reuses `sgdb.similarity`
  at a stricter 0.90 (that catalogue is full of same-franchise fan games).

### LaunchBox DB has no API - parse the HTML (stable as of 2026-08-01)

- Platform listing paginates with `?page=N`, 100 per page, empty page = end:
  `https://gamesdb.launchbox-app.com/platforms/games/139-openbor?page=N`
  yields `href="/games/details/<id>-<slug>"`.
- Per-game images page `/games/images/<id>-<slug>` lists every asset as
  `<a href="https://images.launchbox-app.com/<uuid>.<ext>"
      data-title="<Game> - <Type> Image (<Region>)"
      data-footer="1800 x 2550 JPEG, 1 MB...">`
  so data-title = asset TYPE, data-footer = dimensions.
- Types that matter: `Box - Front` -> cover, `Clear Logo` -> marquee,
  `Fanart - Background` -> fanart. Catalogue cached 14 days in
  `~/.cache/openbor-launchbox-catalogue.json`.
- The catalogue contains MISSPELLED entries: our Showdown Revenge is filed as
  **"Shodown Revenge"** (417041). The 0.90 gate still clears it at 0.97.

### A "cover" is only real if it is PORTRAIT

Two kinds of wrong art land in the boxart slot and BOTH are landscape: the video-frame
placeholder (640x360) and small Steam-grid banners (396x224 on the ZVitor titles).
Orientation is the honest test. **Byte-comparing cover vs screenshot does NOT work** -
they are extracted independently and differ even when the cover plainly is a video
frame (verified on all six placeholders). See `cover_is_weak()` / `cover_acceptable()`.

### Remakes may legitimately inherit the ORIGINAL game's boxart

ZVitor's Captain America / X-Men Mutant Apocalypse / Punisher remakes have no fan-game
entry anywhere, but each remakes ONE specific unambiguous arcade/console original, so
the original's boxart is the right answer (and is what SGDB returns). Do NOT extend
this to fan games that merely share a franchise (Ultimate Double Dragon, TMNT
Recolored, Evil Dead Redux) - there is no single "the original" for those.

### Reachability from this Deck

- `img.itch.zone` is FLAKY: resolves to IPv6 first and this Deck has NO working IPv6
  route (`curl -6` -> 000). Even forced IPv4 timed out once, then worked minutes later.
  Retry before concluding a game has no art there.
- chronocrash.com, gamesdb.launchbox-app.com, images.launchbox-app.com, archive.org and
  the SGDB CDN are all reachable.
- ChronoCrash resource pages only expose a **96x96** `resource_icons/0/<id>.jpg`.
  Useless as cover art. Attachments (`/forum/attachments/...`) are per-thread and rare.

### Games with genuinely NO art anywhere (checked SGDB + LaunchBox + ChronoCrash + itch)

  Jennifer_By_MasterDerico              no boxart, no logo
  The_Punisher_and_Nick_Fury_2.0_final  no boxart/logo/fanart; author's own itch cover
                                        is only 320x244 landscape
  TMNT_Recolored_and_Extended           LaunchBox entry 156422 has no Clear Logo
                                        (the DreamTurtle Edition 291779 does, but it is
                                        a DIFFERENT edition - do not borrow it)
  showdown_revenge                      LaunchBox entry has no Fanart - Background

### Collection facts corrected while doing this

- `Maximun_Carnage_Returns` (HeatGames / Silas Elrick Heat, 25 Nov 2017, Spider-Man +
  Venom + Deadpool) and `CARNAGEv101` (ZVitor's "Maximum Carnage", v1.01, 13 Apr 2025,
  20+ characters, 4 players) are TWO DIFFERENT GAMES. Do not conflate them.
- `MIW_Definitive` had lost its developer/year because the enrichment file still keyed
  it as the OLD folder name `MIWv100`. Folder renames silently break that join.
- `showdown_revenge` is by an UNCONFIRMED author - the ChronoCrash upload is an
  archival post by another member. Left as Unknown rather than guessed.

## Scraper interaction + safe regeneration (2026-08-01, learned the hard way)

A scraper was run over the OpenBOR system on 2026-08-01 00:45. It enriched 12 games and
got 11 of them RIGHT, often BETTER than our curated table (exact release dates like
20230117 instead of our 20230101 guesses, a `publisher` field we never generate,
corrected player counts and developers). It got exactly ONE wrong.

### The one it got wrong, and why it is the predictable one

`CARNAGEv101` was relabelled "Maximum Carnage Returns" / HeatGames. That is a DIFFERENT
GAME which this collection already owns as `Maximun_Carnage_Returns`. CARNAGEv101 is
ZVitor's "Maximum Carnage" (v1.01, 13 Apr 2025). One title is a strict PREFIX of the
other, so every fuzzy matcher - scraper, SteamGridDB autocomplete, and yt-dlp's video
scorer - ranks the longer "…Returns" title top when asked for the shorter name.
`openbor-fetch-media.py` now pins both:
  LAUNCHBOX_QUERY["CARNAGEv101"] = ["Spider-Man and Venom Maximum Carnage"]  (the 1994
      SNES original it remakes; SGDB has no entry and correctly returns no match)
  VIDEO_QUERY["CARNAGEv101"]     = "Spiderman and Venom Maximum Carnage Remake"
Without the VIDEO_QUERY pin, yt-dlp picked "Maximum Carnage Returns with Deadpool
[Openbor] Longplay" - literally the same video already used for the other game.

### NEVER regenerate a whole gamelist to fix one game

Doing exactly that wiped the scraper's improvements on 11 other games. The repair was to
restore the scraper's file verbatim and rewrite ONLY the one <game> block as a TEXT edit.

Two tools were hardened so this cannot repeat:
- **openbor-gen-gamelist.py**: an EXISTING entry's name/desc/developer/publisher/genre/
  players/releasedate now WIN by default. `--refresh-metadata` re-imposes the curated
  table. Verified idempotent: two consecutive runs change nothing.
- **openbor-gen-manifests.py**: an existing manifest's `PREFIX=` is now KEPT rather than
  recomputed. It used to recompute, which silently moved 8 games from the shared prefix
  onto their own Steam compatdata prefix. NOT cosmetic: the shared prefix
  `~/Emulation/storage/openbor/prefix/pfx/system.reg` holds ~460 `VID_4D41` MAD
  virtual-pad registry keys, and the per-game compatdata prefixes hold ZERO (measured:
  MFA2 and MIW_Definitive both 0), so a moved game comes up with mis-seated players.
  MIW_Definitive even had its compatdata line DELIBERATELY commented out; the regen
  resurrected it. Where a game's Wine prefix points is a human decision.

### minidom toprettyxml destroys blank lines inside text

The generator used `minidom.toprettyxml()` followed by
`"\n".join(l for l in xml.splitlines() if l.strip())` to drop minidom's filler lines.
That filter cannot tell a filler line from a BLANK LINE INSIDE A <desc>, so it welded
together the paragraphs of two scraped descriptions. Now uses `ET.indent(root)` +
`ET.tostring`, which only touches whitespace around elements that have children and
leaves leaf text byte-for-byte intact.

### Verify with a diff that actually runs

An earlier "confirm nothing else drifted" check was `diff -rq A B --include='*.openbor'`.
`diff` has no `--include` (that is grep), so it silently compared nothing and reported
success while 8 manifests had changed. Compare file-by-file and count the differences.
