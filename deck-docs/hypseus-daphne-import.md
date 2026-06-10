# Importing a Daphne/LaserDisc game into the Hypseus `.daphne` layout (cached)

Cached 2026-06-08 while importing Galaxy Ranger from a Windows DAPHNE "g2g" pack
(`~/Downloads/Daphne-g2g/`) into `/run/media/deck/1tbDeck/ROMs/daphne/`. Source of
truth = empirical headless test against `hypseus.real` v2.11.6 + the live working games.

## The `.daphne` game layout (what ES-DE/Hypseus expects)
ES-DE `daphne` system uses **"directories interpreted as files"**: each game is a
`<driver>.daphne/` DIRECTORY. es_systems command (custom_systems/es_systems.xml):
`hypseus-pin.sh %BASENAME% vldp -framefile %GAMEDIR%/%BASENAME%.txt %INJECT%=%BASENAME%.commands`
→ `%BASENAME%` = the dir name minus `.daphne` = **the Hypseus game-driver name** (NOT the
video-folder name). Hypseus runs with `-homedir <the .daphne dir>`, so it finds:
- `roms/<driver>.zip` (+ any parent-rom zips — see below) under the dir's `roms/`
- the framefile `<driver>.txt` in the dir
- the video files referenced by the framefile, resolved relative to the dir.

A working game dir (e.g. `roadblaster.daphne/`) contains:
- `<driver>.txt` framefile — line 1 = `.` (video dir = here), then `<frame> <file.m2v>` lines.
- the video(s): `*.m2v` (+ `*.ogg` audio; `*.dat` seek-index is auto-generated on first play).
- `roms/<driver>.zip`
- empty placeholder subdirs: `fonts/ framefile/ ram/ logs/ screenshots/` (Hypseus supplies its own fonts)
- an empty `<driver>.daphne` marker file inside (mirror it; harmless).

## GOTCHA — parent/clone rom dependencies (MAME-style)
A driver may need MORE than its own zip. **`galaxyp`** (Galaxy Ranger, Pioneer) loads its
own `galaxyp.zip` (`epr-5613/5614`) **AND** the base `galaxy` roms (`gr5592–5595`, proms) —
Hypseus looks for `roms/galaxy.zip` or `roms/galaxy/`. Symptom if missing:
`ROM gr5592.bin couldn't be found … NOTE: this ROM comes from the folder 'galaxy', which
belongs to another game … Could not load ROM images!`. Fix = also drop the parent zip in `roms/`.
Zip rom matching is case-insensitive (`EPR-5613.BIN` satisfies `epr-5613.bin`).

## Headless verify recipe (no display on this box; per CLAUDE.md)
```bash
cd <game>.daphne
timeout 35 env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  ~/Applications/hypseus-singe/hypseus.real <driver> vldp \
  -framefile "$PWD/<driver>.txt" -homedir "$PWD"  > /tmp/out 2>&1
```
PASS indicators in the log: every `Loading compressed ROM image … N bytes read` (no
`Could not load ROM images`), then `Framefile parse succeeded. Video/Audio directory is: …`.
EXPECTED-and-harmless: `video::init_display … Could not initialize renderer: That operation
is not supported` (dummy SDL has no GPU renderer) → it then quits. On the real Deck display
it renders/plays. `hypseus.bin` is a wrapper → `hypseus.real "$@" -fullscreen`.

## g2g pack → `.daphne` mapping notes
g2g `vldp/<name>/` holds the single big `.m2v`+`.ogg`+framefile (old DAPHNE single-video form);
`roms/*.zip` holds the chips. Name ≠ driver: g2g `rb` = `roadblaster`; g2g `galaxyr` (video
folder) → driver `galaxyp` (+`galaxy` parent). After placing the dir, ES-DE auto-adds the game
on next start (name defaults to the dir basename); set the display name in gamelist.xml only
while **ES-DE is closed** (it rewrites the file on exit), or scrape it.

## Troubleshooting — game won't start / wrong controls (cached 2026-06-09, empirical vs hypseus.real v2.11.6)
Diagnosed a batch of failing games. Four distinct failure classes, each with a headless tell:

1. **"Could not open file : .../SOMENAME.m2v" (ROMs loaded, framefile parsed, then dies on the video)**
   = **filename CASE MISMATCH**. The SD card is **ext4 = case-sensitive**; Windows-authored packs
   reference e.g. `CH_640x480_24p.m2v` but the on-disk file is `ch_640x480_24p.m2v`. Fix = edit the
   framefile `<driver>.txt` (and any alt like `*_letterbox.txt`) to match the on-disk case EXACTLY.
   This defect is usually present in the source pack too, so re-importing won't fix it. (Hit on `cliff`.)
2. **"ROM xxx couldn't be found ... Could not load ROM images!"** with an **empty `roms/`** dir
   = the rom **zip is missing**. Drop `<driver>.zip` into `<game>.daphne/roms/`. (Hit on `tq` / Thayer's
   Quest — zip was at `~/Downloads/Daphne-g2g/roms/tq.zip`.) See also the galaxyp parent-rom gotcha above.
3. **"Unknown game type specified : NAME"** = it's a **SINGE** game, not vldp/Daphne. The default ES-DE
   daphne command `Hypseus [Daphne] (X-Arcade)` is `<basename> vldp -framefile <txt>` and can NEVER launch
   a Singe game. Set the game's **Alternative emulator → "Hypseus [Singe] (X-Arcade)"** (the 2nd daphne
   command, `singe vldp -framefile <txt> -script <singe>`). Writes `<altemulator>` to gamelist.xml — do it
   in-GUI or with ES-DE CLOSED (rule #3). Singe dirs end in `.singe`. (Hit on `Time_Holo` / Time Traveler.)
4. **Game runs but a control is dead on the X-Arcade** (can't fire 2nd weapon / no accelerate / no brake)
   = the action's joystick button is **unbound** in `~/Applications/hypseus-singe/hypinput.ini`.
   The 3rd column on each `KEY_*` line = **SDL button index + 1** (0 = none). Daphne games map their
   2nd action to **SWITCH_BUTTON2**; if `KEY_BUTTON2`'s joy col is `0`, that action is unreachable on the
   stick (keyboard syms still work, but the X-Arcade only emits joystick events). Fixed all of mach3 (bombs),
   roadblaster (brake), uvt (2nd weapon), gpworld (accelerate) with ONE edit: `KEY_BUTTON2 ... 0` → `... 2`
   (X-Arcade P1 'B'); also `KEY_BUTTON3 ... 11`(R3, unreachable) → `... 3` ('X') for brake/booster.
   X-Arcade P1 SDL btn map (decoded from live 045e:02a1): A=1,B=2,X=3,Y=4,L1=5,R1=6,Select=7,Start=8,
   Guide=9,L3=10,R3=11. NOTE `KEY_SKILL1/2/3` already use 3/4/2 → harmless double-bind (skills are a no-op
   in these drivers). hypinput.ini is NOT touched by ES-DE; back up to `.bak` before editing.

Headless smoke-test PASS = every ROM "bytes read", "Framefile parse succeeded", then the expected dummy
"Could not initialize renderer" (no GPU headless). Button BEHAVIOR can't be tested headlessly — verify on TV.

## Controls: "pedals" & "steering wheel" are DIGITAL in Hypseus (researched 2026-06-09)
Source: official Hypseus src/game/*.cpp (DirtBagXon/hypseus-singe) + doc/hypinput.ini. Classic hypinput.ini
has NO analog pedal/accelerator/throttle and NO analog axis EXCEPT the 4 stick directions (UP/DOWN/LEFT/RIGHT,
col-4 axis). Driving games map "pedals" and "wheel" to digital controls:
- **GP World** (gpworld.cpp): steering = SWITCH_LEFT/RIGHT (a digital ramp, NOT an axis); BUTTON1 = gear shift,
  BUTTON2 = accelerate, BUTTON3 = brake. (The real cabinet had a wheel + accel/brake pedals; Hypseus is digital.)
- **Road Blaster** (bega.cpp): steer = LEFT/RIGHT; BUTTON1 = gas, BUTTON2 = brake, BUTTON3 = booster.
- **M.A.C.H. 3** (mach3.cpp): BUTTON1 = fire, BUTTON2 = bombs. **Cobra Command** (cobraconv.cpp): BUTTON1 = gun,
  BUTTON2 = missile (BUTTON3 commented out / unused).
So: a real wheel → bind LEFT/RIGHT to its axis (col-4); a real pedal → it must register as a BUTTON (Hypseus has
no in-game analog pedal). Analog triggers only exist in the separate `-gamepad` + hypinput_gamepad.ini mode (AXIS_TRIGGER_*).
The MAD "Daphne" page exposes these as the bindable action buttons + the bindable directions (steering); per-game
hints in lib/hypinput.py GAME_HINTS name each game's mapping.

## Capturing Hypseus's runtime log (for Singe/Lua errors)
ES-DE discards Hypseus's stdout, so Singe Lua errors are invisible. hypseus-pin.sh now redirects Hypseus output to
`$XDG_RUNTIME_DIR/hypseus-run.log` (ephemeral, overwritten each launch) — read it right after a failed on-screen run.
Headless CANNOT reach the Singe Lua stage: SDL `dummy`/`offscreen` video drivers both fail `video::init_display`
("offscreen not available"), and the Lua script runs only AFTER the renderer inits — so Singe runtime bugs are on-TV-only.

## Seek / "loading screens" between scenes (researched 2026-06-09)
The pauses between actions in laserdisc games are the emulated DISC SEEK (Hypseus simulates the real player moving its
laser). Tune via CmdLine.md flags (verified accepted on v2.11.6): **`-seek_frames_per_ms 0`** = instantaneous seeking
(logs `NOTE : Max seek delay disabled`) — removes the seek waits; values 12.0–600.0 = faster-but-not-instant.
`-min_seek_delay <ms>` (0=off) forces a minimum; `-latency <ms>` adds delay before searches; `-blank_searches` BLANKS
the screen during seeks (opposite of what you usually want). Hypseus has NO `-precache`/RAM-preload (the Daphne flag is
gone) — instant seek is the substitute. Exposed in MAD's **Daphne** page as the scope-aware **"Scene transitions → Instant"** toggle: GLOBAL writes
`~/Emulation/storage/hypseus/global-args` (a flags string hypseus-pin.sh appends to EVERY laserdisc launch);
PER-GAME rides the `%INJECT%` file `<game>.commands` (composing with the `-keymapfile` keymap inject). lib/hypinput.py
`set_global_seek`/`set_per_game_seek` manage them.

## GOTCHA — `-keymapfile` path must be RELATIVE + lowercase (root-caused 2026-06-09)
Hypseus **lowercases the `-keymapfile` path** internally (a Windows-ism), so an ABSOLUTE path with uppercase dirs
(`/home/deck/ROMs/...` → `/home/deck/roms/...`) is not found on case-sensitive Linux and Hypseus ABORTS the whole
launch: `Invalid -keymapfile file: <path> [Use .ini]` → `Bad command line or initialization problem.` (= the game
"crashes" — really a command-line abort). FIX: write the per-game keymap reference as a **bare, lowercase filename**
(`lair.ini`, `time_holo.ini`) — Hypseus resolves it relative to `-homedir` (= the game dir, set by hypseus-pin.sh), and
its lowercase matches the file. So `lib/hypinput.py` `per_game_ini` names the file `<basename>.lower().ini` and
`merge_keymapfile_commands` writes the bare name. (Verified: absolute path aborts; `-keymapfile lair.ini -homedir <gd>`
from any cwd loads fine — "Joystick HAT enabled".) NOTE: the seek toggle itself does NOT crash — single or duplicated
`-seek_frames_per_ms 0` parses cleanly on both Daphne and Singe; the crash was always the keymapfile path.
CAVEAT: instant seek can rarely desync audio/timing on a Singe game; fall back to a high value (e.g. `-seek_frames_per_ms 200`).
NOTE: loading screens DRAWN BY the Singe Lua script (not the seek) can't be removed this way.

## Dragon's Lair "audible-but-invisible intro" at launch = Hypseus BOOT, not ES-DE preview (root-caused 2026-06-09)
Symptom: launching DL/Space Ace plays intro music over a black/loading screen. **It is NOT the ES-DE gamelist preview
video** — the fork already PAUSES+MUTES the preview the instant the launch button is pressed (GamelistBase.cpp:74 →
VideoFFmpegComponent::pauseVideoPlayer → muteVideoPlayer → AudioManager clearStream+muteStream), and during the game ES-DE's
main loop is BLOCKED inside launchGameUnix (PlatformUtil.cpp, RunInBackground=false) — no preview audio escapes. The sound is
Hypseus's OWN game audio: the CPU/ROM (and its attract/intro music) boot ~1s before the VLDP video is ready, and the DL boot
SLATE is silent (no dl-slates.ogg) so the music leads the first frame. FIX = Hypseus **`-fastboot`** (patches the game ROM to
skip the emulated LDP power-on/boot and jump to gameplay; src/game/lair.cpp). Documented + vtable-confirmed for Dragon's Lair,
Space Ace, Cliff Hanger (+ enhanced variants dle21/sae), Goal to Go — applied per-game via `<game>.commands` (composes with
-keymapfile / -seek_frames_per_ms). NOT supported by lair2 (DL2) / cobra (harmless no-op there). `-seek_frames_per_ms 0`
removes inter-SCENE seek gaps but does NOT shorten the boot. (Aside: ES-DE preview audio key = `ViewsVideoAudio`, default
true, menu: Sound Settings → "Play audio for gamelist and system view videos" — only relevant to BROWSING, not launch.)

## Singe games OUTSIDE the Hypseus dir — the CWD trap (root-caused 2026-06-09, Time Traveler)
**Hypseus `main()` calls `set_cur_dir(argv[0])` → it `chdir()`s to the EXECUTABLE's own directory** (`~/Applications/
hypseus-singe`). A Singe game's main `.singe` script (loaded via `-script <abs path>`, which works) then runs
`dofile("singe/<x>/…")` + loads images/sounds/fonts via RELATIVE paths — and those resolve against the CWD = the
**install dir**, NOT the game dir. `-homedir` does NOT move the CWD (homedir.cpp only builds path strings; confirmed in
src/hypseus.cpp), so for Time Traveler every include failed: `SINGE: error compiling script: cannot open
singe/timetraveler/dvd-globals.singe: No such file or directory`. (Videos are fine — VLDP finds them via the
framefile + -homedir.) The `-espath`/`-singedir` "ES rewrite" flags exist but key the rewrite off the script's internal
`singe/<x>/` name vs an external `<x>.daphne` folder — our folder is `Time_Holo.singe` (≠ `timetraveler`), so they don't
fit without renaming. **FIX (implemented in hypseus-pin.sh):** on a Singe (`-script`) launch, mirror the game's small
`singe/` tree (Lua + png/wav/ttf, ~hundreds of KB — NOT the m2v/ogg) into `<install>/singe/` with `cp -ru`, so the
relative includes resolve at the CWD. Re-synced each launch (self-heals if the hypseus install is refreshed). Verified:
the includes resolve from the install dir; on-TV confirmation still required for the Lua actually running.

## Seek-index (`.dat`) pre-builder — kill the per-scene "seeking" pause (root-caused + built 2026-06-09)
Each laserdisc video `<x>.m2v` has a sidecar `<x>.dat` SEEK INDEX (I-frame byte offsets). **The first 2 bytes are a
version**: rips ship `02 01`; Hypseus v2.11.6 wants version 3 (`DAT_VERSION=3` in `src/vldp/vldp_internal.h` →
`03 01`). When VLDP opens an `.m2v` whose `.dat` isn't `0301` it logs `…dat is outdated and has to be created again!`
and **rebuilds the whole index on the spot** (`ivldp_parse_mpeg_frame_offsets`, `src/vldp/vldp_internal.cpp`) — THAT
rebuild is the visible "seeking" pause on a scene's first visit (e.g. Dirk dying into a not-yet-played scene). It
self-heals per scene as you play, but ONLY on-screen (a headless `SDL_VIDEODRIVER=dummy` run does NOT build them —
the renderer must run). Nobody distributes `0301` indexes (Downloads rips are all `0201`; DirtBagXon/hypseus_singe_data
is Singe-only and ships no `.dat`). Detect version: `xxd -l2 -p <x>.dat`.

**Pre-builder (implemented):** `singe-indexer.sh all|<game-folder>` parses a game's framefile, skips segments already
`0301`, generates a tiny self-contained Singe script that `discSkipToFrame()`s into each remaining segment (waiting for
`discGetFrame()` to land = parse complete), and launches Hypseus **on-screen**; a `.dat`-watcher in the driver stops
Hypseus once all targets are `0301`. The `.dat` is built by the **shared VLDP layer**, so a Singe-driven seek builds the
exact index a Daphne game reuses — works for both `.daphne` and `.singe`. ~1s per segment + ~4s Hypseus startup.
Exposed in MAD's Daphne page: **global scope → "Build seek indexes — ALL games"**, **per-game scope → "Build seek index
— <game>"** (`_dp_build_index` → `self._run`). Verified on-screen: gpworld (1 seg) and roadblaster (19 segs, 18s) all
flipped to `0301`, both from Konsole and nested from MAD-under-gamescope. The min Singe loop needs callbacks
`onOverlayUpdate/onInputPressed/onInputReleased/onMouseMoved/onSoundCompleted/onShutdown` or it aborts; key Lua:
`discSetFPS, discSkipToFrame, discGetFrame, discPause, discStop`; `OVERLAY_UPDATED=1`. There is NO Lua quit — the engine
quits only on `SWITCH_QUIT` (Start+Select), so the driver kills Hypseus once the indexes are done.
