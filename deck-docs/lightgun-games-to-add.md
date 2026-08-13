# Lightgun games NOT on the stack, worth adding (verified)

Research date: 2026-06-26. Two multi-agent web-research+verify sweeps (141 candidates).
Filters applied to every entry: true lightgun/pointer game (not analog FPS), NOT already
owned, runs on an installed-or-addable system, full FPS on Deck, Sinden path checked.
Sources: Sinden wiki (Supermodel_M3 / Dolphin / DuckStation / RPCS3 / Hypseus pages),
MAME ADB driver status, libretro core docs+issues, lindbergh-loader gameData.c (v2.1.4),
opera-libretro #176, PCSX2 GunCon2 compat #7619, ProfgLX Dolphin-Lightguns-Accuracy-Inis.

His current Pew-Pew-Pew collection is the exclusion list (see custom-Pew-Pew-Pew!!!.cfg).

## ALREADY ON THE STACK (on disk + scanned) -- WIRED INTO PEW 2026-06-26 (catalog DONE, aim calibration owed)
DONE 2026-06-26 (ES-DE closed, verified): added all 8 below to custom-Pew-Pew-Pew!!!.cfg (61->69 lines) and
set lamachin/oceanhun/swtrilgy altemulator to "Supermodel (Linux Sinden, 2-player)". Router lightgun-rom now
returns LIGHTGUN for carnevil/lamachin/oceanhun/swtrilgy + the 4 Wii games (drives the Dolphin Sinden hook).
Backup: ~/Downloads/_TMP-pew-lightgun-wire-20260626-180844/ (naomi gamelist + Pew cfg + RECOVERY.txt).
KEY MECHANISM learned: Wii Sinden is NOT an altemulator -- the game-start hook dolphin-wii-mode.sh flips
Dolphin's WiimoteNew.ini Source to the 2 Sinden emulated Wiimotes IFF the ROM is a Pew (require_sinden)
member (it asks controller-router.py lightgun-rom). So Pew membership alone wires the Wii guns. CarnEvil
needs NO altemulator (default MAME Current = same as his working hogalley/cryptklr/ghlpanic).
OWED on-device (user must verify on-screen, cannot do headless):
  - Model 3 trio: per-game gun ID/axis calibration in Supermodel.ini (SindenRemap); swtrilgy is fussiest.
  - Wii 4: hook gives the 2 Sinden Wiimotes, but per-game aim/crosshair-removal inis (ProfgLX, keyed to game
    IDs) are NOT auto-applied. Dead Space + Ghost Squad are EU discs (IDs differ from USA-keyed ProfgLX) so
    those two likely need a calibration/crosshair nudge; Gunblade + Target Terror are USA (match).
  - CarnEvil: set the RA gun device/ADSTICK X&Y or shots fire but do not register hits.
- CarnEvil (Arcade gamelist): carnevil.zip + carnevil.chd both on disk. altemulator=DEFAULT (MAME Current);
  needs lightgun device/ADSTICK config, no Sinden tile set. 2P.
- L.A. Machineguns / The Ocean Hunter / Star Wars Trilogy Arcade (Naomi gamelist, Model 3): ROMs on disk.
  altemulator currently = "Supermodel (Standalone)" (NON-Sinden). Switch to "Supermodel (Linux Sinden,
  2-player)" (naomi tile index 4) to get the guns. LAM+Ocean = 2P, SW Trilogy = 1P. Sinden-wiki listed.
- Dead Space Extraction (EU) / Ghost Squad (EU) / Gunblade NY & LAM Pack (USA) / Target Terror (USA)
  (Wii gamelist): .rvz on disk. altemulator=DEFAULT (no Sinden Dolphin tile set). ProfgLX inis exist. 2P.
- The Maze of the Kings (mok) + Lupin the 3rd: The Shooting (lupinsho): ROMs on disk, naomi/flycast.
  2P, flycast 80H calibration bug. (Check Pew/gun-wire status same as above before treating as new.)

## MUST-HAVE (highest value, on-taste, mostly easy)
- Point Blank 1 / 2 / 3 -> best 2-Sinden route = PSX/DuckStation (native DUAL GunCon = true 2-gun co-op,
  unlike PCSX2). Also works in arcade MAME (gnbarl S11 / pblank2 S12 / S10) 2P. THE party gallery series.
- CarnEvil (arcade) -> on disk, turnkey. config gotcha: set gun device/ADSTICK or shots dont register.
- Resident Evil: The Umbrella Chronicles (wii/Dolphin) -> headline 2P co-op; ProfgLX REUmbrella ini; use USA disc.
- Let's Go Jungle! Lost on the Island of Spice (lindbergh) -> WORKING in gameData.c (DVP-0011/0011A/0036),
  2P co-op, proven HOTD4 pipeline. base is 1360x768 (~16:9, no stretch); Special DVP-0036 is 4:3.
- Gangster Town (mastersystem, Genesis Plus GX Light Phaser) -> TRUE SIMULTANEOUS 2-gun co-op, clean Sinden,
  trivial add. Rare console game built for a dual-gun rig.
- Time Crisis 2 (ps2, PCSX2 EvdevLightgun) -> the home TC he lacks. SP works on his abs path; 2P is SPLIT-SCREEN
  with a known PCSX2 calibration bug (#10118) + stock PCSX2 = 1 gun only (#7888). Treat as SP for now.

## GREAT (strong adds)
Model 3 (Supermodel Sinden 2P tile, ROMs on disk): L.A. Machineguns (2P), The Ocean Hunter (2P),
  Star Wars Trilogy Arcade (1P).
Lindbergh (loader, WORKING in gameData.c, proven pipeline; need the dumps):
  Ghost Squad Evolution (DVP-0029A, 2P, 640x480 so apply 1440x1080 fix; fire-selector to spare button),
  Let's Go Jungle (above), House of the Dead EX (net-new HOTD, 2P, JP minigames).
Wii (Dolphin Sinden tile, ProfgLX inis exist):
  RE: Darkside Chronicles (2P co-op, need ROM), Ghost Squad (on disk), Dead Space Extraction (on disk),
  Gunblade NY & LAM pack (on disk), Mad Dog McCree Gunslinger Pack (FMV westerns 1+2+LBH, 2P, need ROM).
Arcade (MAME/FBNeo, Sinden via RetroArch):
  Bang! (Gaelco, 2P Point Blank clone, clean), CarnEvil (above),
  Exidy 440 trio Crackshot/Combat/Clay Pigeon (completes his Crossbow/Cheyenne/Chiller set; source ROMs).
PS1 GunCon (DuckStation, his true 2-gun route):
  Time Crisis + Project Titan (1P, first-class GunCon), Elemental Gearbolt (cult gem, 2-gun via NA Working
  Designs disc only).
PS2 GunCon2 (PCSX2 EvdevLightgun):
  Gunfighter II: Revenge of Jesse James (rated PERFECT in #7619, western 2P co-op),
  Time Crisis: Crisis Zone (machine-gun TC, genuine 2-gun mode; calibration pass needed),
  Resident Evil: Dead Aim (cult hybrid: pad move + gun aim).
FMV/laserdisc via Singe on his daphne system (2P co-op, HD, Sinden/Gun4IR-built rompacks in 00-zip-roms):
  Mad Dog McCree, Mad Dog II, Crime Patrol, Crime Patrol 2: Drug Wars, Who Shot Johnny Rock?,
  The Last Bounty Hunter, Space Pirates, plus Tierras Salvajes (new 2025, native Sinden+2P) and Marbella Vice.
  SETUP CAVEAT: daphne system is X-Arcade-pinned today (guns excluded from SDL); needs a -zlua launch command +
  Sinden/manymouse wiring added before guns work in Hypseus. Honest taste flag: grainy live-action FMV.
Master System Light Phaser (Genesis Plus GX, clean Sinden): Rescue Mission, Assault City (use Light Phaser ROM,
  not the pad ROM), Rambo III (distinct from Lindbergh Rambo).
NES Zapper (FCEUmm, clean Sinden): Wild Gunman, Barker Bill's Trick Shooting.
SNES Super Scope (Snes9x, User 2; Sinden mapping quirk caveat): Yoshi's Safari, Metal Combat: Falcon's Revenge,
  Tin Star (1P, not 2P).

## NICHE / completionist / caveat (worth knowing, lower priority)
- Naomi flycast: Maze of Kings, Lupin Shooting (on disk; calibration-fiddly, 80H bug).
- Lindbergh: Primeval Hunt (touch-panel nav needs a mouse; gun part works).
- Arcade: Point Blank arcade extras, Police Trainer (2P competitive), Area 51: Site 4 (preliminary driver),
  Locked 'n Loaded/Gun Hard (Dragon Gun board, rough A/V), Target Hits (Gaelco), Golgo 13 set (S12/S10, light,
  JP scope-sniper), Time Crisis original arcade (Super System 22, heavy, use PSX port instead),
  Silent Scope 2 (scope fights absolute aim, heavy Hornet, skip unless he wants the subgenre).
- PSX: Resident Evil Survivor (PAL/JP disc only, or US lightgun patch), Project Horned Owl (Justifier; 2-gun
  has DuckStation shared-XY bug, play 1P), Die Hard Trilogy (only the DH2 mode is the gun game).
- PS2: Dino Stalker (rated BAD #7619, 2P co-op unlock), Starsky & Hutch (asymmetric: pad driver + 1 gunner).
- Wii: Link's Crossbow Training (1P), Big Buck Hunter Pro (2P turn-based), Mad Dog Gunslinger Pack (see above).
- Retro console extras: SMS Wanted + Marksman/Trap/Safari trio; NES Gumshoe/Gotcha/To the Earth;
  SNES Battle Clash/Super Scope 6/X-Zone; Genesis Menacer Body Count + Menacer 6-Game (undocumented Sinden).
- FMV alternates (overlap Singe): Sega CD ALG (Mad Dog 1/2, Crime Patrol, WSJR; needs Sega CD BIOS, Menacer
  more reliable than Justifier), 3DO ALG via Opera (1P; Mad Dog II / Crime Patrol 1+2 / Last Bounty Hunter
  reload OK, Mad Dog McCree + Space Pirates have reload bugs).
- C64 / Amstrad CPC Magnum-gun games (need new system, 1P only, mostly overlap arcade Op Wolf/Thunderbolt):
  CPC via cap32 has the best-supported computer-gun path (Operation Wolf, Solar Invasion, Robot Attack, Rookie,
  Missile Ground Zero); C64 via VICE works but flaky.

## PS3 (heaviest, 1-gun only, Linux plumbing needed)
- Time Crisis: Razing Storm bundle (= Razing Storm + Time Crisis 4 + Deadstorm Pirates), RPCS3 PS Move Mouse
  Handler. Sinden wiki confirms WORKING for the BUNDLE (BLUS30528; standalone TC4 does NOT work; enable Write
  Color Buffers to fix TC4 white screen). Caveats: RPCS3 = 1 lightgun (no 2P), heaviest emu on the Deck (perf
  unproven), published guides are Windows/AutoHotkey so needs his own evdev plumbing. His ONLY route to TC4.

## DEAD ENDS (do not chase)
- Virtua Cop 1 & 2: no clean route. Model 2 = needs new system + DemulShooter-under-Proton (unproven 2P Sinden);
  PS2 Virtua Cop Elite Edition = rated BAD in #7619 (aim sticks center); Saturn (Beetle, 2-gun unproven).
  Try the PS2 disc on his evdev path as a "maybe", do not promise.
- Virtua Cop 3 + arcade Ghost Squad: Sega Chihiro, NO Linux emulator (Cxbx Windows-only, experimental). VC3
  never got a home port = genuinely unobtainable. Ghost Squad has the Wii port (use that instead).
- Philips CD-i, Atari XG-1 (7800/8-bit/XEGS), ZX Spectrum, MSX gun games: the RetroArch cores expose NO lightgun
  device, so no Sinden path. (CD-i/Atari ALG content covered by the 3DO + Singe versions.)
- Redundant ports of games he owns in arcade form: SNES/Genesis/Sega CD Lethal Enforcers I/II, NES Hogan's Alley.
- Xbox Silent Scope Complete (xemu): SS2 crashes xemu, scope fights Sinden, SS1 owned on DC. Net-new = SS3/EX only.
