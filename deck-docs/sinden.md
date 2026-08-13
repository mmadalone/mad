# Sinden Lightgun — doc findings (cached)

## Calibration on Linux (sindenwiki.org/wiki/Linux_Unix_Guide, fetched 2026-06-05)
- Official calibration: **`mono LightgunMono.exe sdl`** → enters calibration mode; the
  utility **"draws its own rectangular border"** to support tracking, then exits. You
  calibrate by shooting that border's reference points.
- Documented args: **`sdl`** (calibration UI) and **`joystick`** (joystick mode instead
  of the default mouse mode; mouse mode is "more stable for most games").
- **No documentation of calibration window size / resolution / scaling / fullscreen.**
  The wiki never says the calibration window should fill the screen — calibration is
  about the *border it draws*, not the window filling the display.

## Our `steam` arg is UNDOCUMENTED
- Our `sinden-calibrate.sh` runs `mono LightgunMono.exe sdl steam joystick`. The wiki
  documents only `sdl` + `joystick` — **`steam` is not an official argument** (our old
  comment "steam = scale to deck screen" was an unverified assumption / reverse-engineer).
  Unknown args are likely ignored by LightgunMono → would explain the default-size window.
- TODO if revisited: test calibration with the documented `mono LightgunMono.exe sdl
  joystick` (no `steam`) and compare; don't force the window fullscreen externally —
  the window geometry feeds `SDL_WarpMouse` calibration mapping (memory
  `lightgun-sinden-architecture`), so external resizing risks misaligning calibration.

## Steam Deck Sinden support is recent/prototype
- Sinden Software V2.08b (Sept 2025) added **prototype** Steam Deck driver support
  (sindenlightgun.com/drivers). Deck-specific calibration UX is immature; a non-filling
  calibration window in **desktop mode** is unsurprising. The `steam`-style screen
  scaling is a **Game-Mode (gamescope)** behavior — test calibration in Game Mode.

## Conclusion (2026-06-05)
The calibration screen NOT filling the screen is **not a documented bug** — Sinden never
promises a fullscreen calibration window. Don't force it (no docs basis + risks the
calibration mapping). Definitive Q&A lives in the Sinden Discord (live community);
the wiki/forums don't document a fullscreen calibration.

Sources: https://www.sindenwiki.org/wiki/Linux_Unix_Guide ·
https://www.sindenwiki.org/wiki/Sinden_Troubleshooting · https://sindenlightgun.com/drivers/

## Flycast (Dreamcast) lightgun crosshair HORIZONTAL misalignment — root cause (researched 2026-06-08)
The misalignment is an ABSOLUTE-POINTER MAPPING problem, not a Flycast bug per se:
- **Mechanism**: an absolute-pointer gun (Sinden/Wiimote/GUN4IR) reports a position over the
  WHOLE physical screen. The core expects coordinates within the RENDERED game area. When the
  rendered image doesn't fill the screen 1:1 (pillarbox/letterbox) OR is stretched to a different
  aspect than it maps gun input to, physical aim and on-screen crosshair diverge — worst at the
  horizontal edges (gun "moves too fast" toward the sides, off by inches).
  - RetroArch issue libretro/RetroArch#13255: udev absolute-pointer maps gun to the VIEWPORT, not
    the full screen → with 4:3 content pillarboxed on 16:9, X is horizontally misaligned. Fix in
    `input/drivers/udev_driver.c` (`udev_mouse_get_pointer_xy`) maps to screen not viewport.
  - Flycast issue libretro/flycast#1175 (GUN4IR, HOTD2): gun inaccurate toward edges because the
    emu treats the entire widescreen (incl. black borders) as the playfield instead of the 4:3
    render area — same class as "MAME aspect set to 16:9 on a 4:3 rom."
- **Widescreen Hack** `[flycast_widescreen_hack]` (Off|On, "Draw geometry outside the normal 4:3
  aspect ratio"): changes the 3D polygon FOV to render wider. Per Sinden wiki (Flycast Windows),
  it "doesn't affect alignment" BUT "you cannot shoot outside of the 4:3 rendering area" — i.e. the
  gun still maps to the 4:3 zone, so the wider image makes aim feel wrong at the sides. libretro
  core hardcodes 16:9 expansion (flyinghead/flycast#2098) regardless of frontend AR → can stretch.
- **Widescreen Cheats** `[flycast_widescreen_cheats]` (Off|On): game-specific patches that STRETCH/
  reposition 2D elements to fill width. Hack+Cheats together = image too wide / over-expanded
  (flyinghead/flycast#1551) → worse for aim.
- **Core-provided aspect ratio = 4:3** (default). Internal resolution `[flycast_internal_resolution]`
  default 640x480; raising it (4K) is fine and does NOT change aspect/mapping.

### RECOMMENDED Flycast lightgun config (accurate aim)
- Flycast **Widescreen Hack = OFF**, **Widescreen Cheats = OFF** (keep native 4:3 geometry).
- RetroArch **Aspect Ratio = Core Provided** (= 4:3) — do NOT force 16:9 or Full/Stretch.
- Crosshair: `[flycast_lightgunN_crosshair]` per port; turn OFF when using a Sinden border overlay.
- Internal resolution: raise freely (4K ok) — only affects sharpness, not pointer mapping.
- On a 16:9 panel, 4:3 content is pillarboxed; the gun must map to the 4:3 zone. If your gun lets
  you, calibrate to the 4:3 image area, or use a frontend/driver that maps pointer to rendered
  content (not full screen). Stretching to fill 16:9 = horizontal misalignment.
Sources: docs.libretro.com/library/flycast/ ; github.com/flyinghead/flycast/issues/2098, /1551 ;
github.com/libretro/flycast/issues/1175 ; github.com/libretro/RetroArch/issues/13255, /5785 ;
sindenwiki.org/wiki/Flycast_(Windows) ; forums.libretro.com/t/flycast-widescreen-hacks-vs-cheats/35142

## Crosshair alignment — RetroArch/Flycast Dreamcast (cached 2026-06-08)
Sources: sindenwiki.org/wiki/Retroarch, /wiki/Flycast_(Windows), /wiki/Sinden_Troubleshooting;
docs.libretro.com/library/flycast/ + /guides/overlay-pointing-devices/;
emudeck.github.io/emulators/steamos/flycast/; libretro forums t/25413, t/49025;
LaunchBox forum 72689; github libretro/RetroArch#5785, #12736.

KEY MENTAL MODEL: Sinden = USB MOUSE. RA maps mouse/pointer X,Y across the FULL display.
The CORE crosshair (flycast_lightgun1_crosshair) is drawn in GAME/viewport coords, so it
tracks the gun 1:1 and stays aligned regardless of bezel/aspect — USE IT. An RA input-overlay
crosshair or a frontend crosshair would live in SCREEN coords and DRIFT when the 4:3 core
viewport sits inside a 16:9 screen (mouse maps to whole screen, game renders in a sub-rect) →
edge offset that grows toward L/R edges (#5785, LaunchBox 72689). Sinden border is NOT a sight,
it's the camera's tracking reference; never use it as the aim point.

Concrete Flycast (RA) settings:
- Quick Menu > Controls > Port 1 Device Type = **Light Gun** (NOT Pad/Mouse). The lightgun
  Core Options ("Show Lightgun Settings" then Gun Crosshair) only appear AFTER this + a
  game restart / quick-menu re-enter.
- Core Opt: **Gun Crosshair 1 Display [flycast_lightgun1_crosshair] = White/Red/Green/Blue**
  (legacy alias reicast_lightgun1_crosshair; current key = flycast_). This is the crosshair to use.
- Bind in Settings > Input > Port 1 Controls (RetroPad binds): Gun Trigger=Mouse1,
  Gun Reload=Mouse2/offscreen, plus Coin/Start (hunterk: bind here, not via Quick Menu remap).
- Aspect: to make the gun map 1:1 to the game, render the core to fill the gun's calibrated
  area — either Video Aspect = Full (stretch to screen) OR calibrate Sinden to the 4:3 sub-rect.
  Pillarboxed 4:3-in-16:9 is the classic drift cause.
- EmuDeck STANDALONE Flycast already uses Port A = Light Gun + Crosshair ON (its
  "EmuDeck - Steam Deck Light Gun Controls" Steam Input profile maps trackpads→mouse).

Sinden wiki's own RA guide says turn OFF in-game crosshairs and aim by the border, BUT that's
for cores w/o a tracking core crosshair; where a core crosshair exists (Flycast) it's more
accurate. Border MUST be visible at all times (camera needs it) → use borderless-windowed,
not exclusive fullscreen, or the white border vanishes.

## Confidential Mission (DC) — widescreen BREAKS gun aim (game-specific, researched 2026-06-08)
Absolute light-gun rail shooter (Virtua Cop / House of the Dead style; Sega 2000 arcade NAOMI,
2001 DC port). Aimed with an absolute-position device (Sinden/lightgun/pointer) in Flycast.
DECISIVE upstream evidence — Flycast core/cheats.cpp:
  - `// Confidential Mission (PAL) 022F0D58 43700000 - Only works on real Dreamcast`
  - `// gun coords problem with these 2 cheats`
    `// { " CONFIDENTIAL MISSION ---------", nullptr, { 0x24F798 }, { 0x43700000 } },`
  Both widescreen cheats are COMMENTED OUT / disabled precisely because they break gun
  coordinates under emulation. This is dev confirmation that WS+lightgun don't coexist here.
- Native widescreen: NO (not in DC native-16:9 lists; only an external WS cheat exists, and it's
  emulation-broken). So the WS hack is NOT legitimate for this game.
- Game DOES draw its own on-screen cursor/crosshair (also movable by pad/analog without snapback,
  flycast#1285) → no separate aim sight needed; the gun should map 1:1 to that cursor.
- VERDICT: apply the standard fix. widescreen_hack=disabled, widescreen_cheats=disabled,
  aspect=4:3 (Core Provided). This matches the per-game .opt fix for absolute lightgun titles.
Sources: github.com/flyinghead/flycast/blob/master/core/cheats.cpp ;
en.wikipedia.org/wiki/Confidential_Mission ; github.com/flyinghead/flycast/issues/1285 ;
github.com/nexus382/Flycast-Widescreen-Compatability-And-Cheat-Chart (Dreamcast Widescreen.md:
WS status "???", not native) ; forums.libretro.com/t/flycast-dreamcast-naomi-lightgun-aimtrack/27406

## House of the Dead 2 (DC, Sega 1999) — widescreen-off + 4:3 CONFIRMED (verified 2026-06-08)
Adversarial re-verification of the prior agent's "apply_widescreen_off_4_3" — CONFIRMED.
- True ABSOLUTE light-gun rail shooter (arcade NAOMI -> DC port; DC version shipped w/ native
  light-gun support, region-locked). Opposite of Silent Scope. Sinden IS the correct device.
- Native widescreen: NO. Dreamcast outputs only 640x480 (4:3); HOTD2 has no native anamorphic
  16:9. Not in nexus382 NAOMI Widescreen chart -> any WS is a non-native hack/cheat, not legit.
- flycast issue #1175 is LITERALLY HOTD2 + light gun: gun "moves too fast toward edges, off by
  inches" because the emu/frontend treats the whole widescreen (incl. black borders) as playfield
  instead of the 4:3 render area (reporter analogy: "MAME aspect set to 16:9 on a 4:3 rom").
- MECHANISM nuance (prior finding slightly overstated the HACK's role): the Widescreen HACK alone,
  per Sinden wiki, "doesn't affect your alignment" — calibration stays valid. Its harm is you
  "cannot shoot outside the 4:3 rendering area," so with WS on, visible image != shootable area ->
  edge content you can't hit (feels like inaccuracy). The aim-WARPING divergence comes from the
  FRONTEND aspect (16:9/Full/Stretch) mapping the udev pointer across the full screen incl. borders
  (RetroArch #13227/#13255, flycast #1175), and from widescreen_cheats over-stretching 2D. Net: all
  paths -> render native 4:3 so visible==shootable==calibrated. Recommendation correct regardless.
- VERDICT: apply_widescreen_off_4_3. widescreen_hack=disabled, widescreen_cheats=disabled,
  RA Aspect=Core Provided (4:3). Port1=Light Gun. Core crosshair (flycast_lightgun1_crosshair)
  tracks in viewport coords; Sinden border is tracking ref, not aim point.
Sources: github.com/libretro/flycast/issues/1175 ; github.com/flyinghead/flycast/issues/2098,/1551 ;
github.com/libretro/RetroArch/issues/13227,/13255 ; sindenwiki.org/wiki/Flycast_(Windows) ;
docs.libretro.com/library/flycast/ ; github.com/nexus382/Flycast-Widescreen-Compatability-And-Cheat-Chart
(HOTD2 absent from NAOMI chart) ; en.wikipedia.org/wiki/Dreamcast_light_guns

## Silent Scope (Dreamcast, Konami) — NOT an absolute-lightgun game (researched 2026-06-08)
The DC home port has **NO light gun support** (multiple sources). It is aimed with **RELATIVE/cursor input**:
the **analog stick** (or a **Dreamcast mouse**, a relative-motion device) moves a circular targeting reticle;
L/R trigger removes the scope to move the cursor fast, release to zoom in for precise aim (relative 2-stage,
mirrors the arcade rifle). The GAME draws its own on-screen reticle. (GameSpot hands-on; ChapterCheats DC alt
controls "Dreamcast mouse to aim, LMB shoot, RMB toggle reticle"; GameFAQs review "no light-gun support".)
- Therefore the "widescreen hack breaks ABSOLUTE light-gun aim" problem does NOT apply: that failure mode is
  specific to absolute-position guns (Sinden/GUN4IR) where physical screen pos maps to the 4:3 hit area. A
  Sinden (absolute) is not the right device for this game — it uses relative deltas, and the game moves its
  own reticle. Widening the FOV via the hack doesn't cause the edge-divergence/unhittable-edges failure.
- Native widescreen: EU version has a "Perfect" anamorphic WS cheat (02D1FB14 3F400000 / 02096A4C 3FAAAAAB),
  per nexus382 Flycast Widescreen chart → 16:9 is at least partly legitimate for this title (unlike a pure
  4:3 absolute-lightgun shooter).
- RECOMMENDATION: this is the EXCEPTION — does NOT need the 4:3/widescreen-off lightgun fix for "Sinden aim".
  If Miquel insists on a Sinden it would act as a relative mouse at best; better to aim with analog stick/mouse.
Sources: gamespot.com/articles/silent-scope-hands-on/1100-2571607/ ;
chaptercheats.com/cheat/dreamcast/8701/silent-scope/hint/31814 ;
gamefaqs.gamespot.com/dreamcast/914118-silent-scope/reviews/27611 ;
github.com/nexus382/Flycast-Widescreen-Compatability-And-Cheat-Chart

### ADVERSARIAL RE-VERIFY (2026-06-08) — CONFIRMS special_handling
Re-checked with fresh sources, skeptical of the cache. Findings hold:
- NO native DC light gun. Aim = relative analog stick (default zoomed scope; hold L/R to zoom
  out + move cursor fast, release to re-zoom) OR Dreamcast MOUSE (relative): LMB shoot, RMB
  toggle reticle, thumb pause (chaptercheats/DC manual). The "light gun" phrasing on ggdreamcast/
  wikipedia echoes refers to the ARCADE rifle peripheral & genre, NOT a DC light-gun mode — same
  page says "no sniper rifle... released for the home market" and "like playing HOTD with a controller."
- REAL-WORLD SINDEN TEST (forum.arcadecontrols.com topic 165285): users ran Silent Scope with a
  Sinden as a mouse (PS2/PCSX2 patch). Verdict: "Way too shaky", "very difficult to aim... too
  many micro movements to be playable for a sniper game." Konami never made it GunCon2-compatible
  either. => absolute gun on a relative-cursor sniper = JITTER, the wrong tool. This is a
  device-CLASS mismatch, NOT the aspect-ratio edge-divergence that the 4:3 fix targets.
- WIDESCREEN does NOT break aim here: no fixed screen->4:3-hitbox mapping exists (game integrates
  relative deltas + draws its own reticle in viewport coords). nexus382 chart: EU "Perfect" WS
  cheat (02D1FB14 3F400000 / 02096A4C 3FAAAAAB); JP/NA "???". So 16:9 is partly legit for EU.
- VERDICT: CONFIRM special_handling. Exclude from the absolute-lightgun widescreen-off+4:3 fix
  list. Do NOT set Port = Light Gun, do NOT enable flycast_lightgunN_crosshair (game draws its own
  reticle). Play with analog stick or trackpad-as-mouse. Global 16:9 may stay; no aiming reason to
  force 4:3. Confidence HIGH.
Sources (fresh): forum.arcadecontrols.com/index.php?topic=165285.0 ; ggdreamcast.com/games/silent-scope ;
gamespot.com/articles/silent-scope-hands-on/1100-2590933/ ; chaptercheats.com/cheat/dreamcast/8701/silent-scope/hint/31814 ;
github.com/nexus382/Flycast-Widescreen-Compatability-And-Cheat-Chart/blob/main/Dreamcast%20Widescreen.md

## Official software bundle layout (fetched + inspected 2026-06-12)
- Download: https://www.sindenlightgun.com/software/SindenLightgunSoftwareReleaseV2.08b.zip (~25 MB, stable since 2025-09-09).
- The Steam Deck driver lives at zip path
  `SindenLightgunSoftwareReleaseV2.08b/SindenLightgunLinuxSoftwareV2.05/SteamdeckVersion/Lightgun/`
  and maps 1:1 onto `~/Lightgun/`: LightgunMono.exe, LightgunMono.exe.config,
  libCameraInterface.so, libSdlInterface.so, License.txt, Overlays/ (MAME .lay borders).
- The driver itself is CLOSED-SOURCE: github.com/SindenLightgun/SindenLightgunLinux ships only
  compiled binaries + RetroPie setup scripts (85% Python install tooling). No code to adopt.
- `sinden-install.sh` (MAD) downloads this bundle and refreshes ONLY the official files,
  preserving LightgunMono.exe.config (user tuning) and any extra files (sinden-smooth.so etc.);
  replaced files go to ~/Downloads/_TMP_sinden-install-<ts>/ with RECOVERY.txt.
- mono comes from pacman (wiped by SteamOS updates) — deck-post-update.sh step 2 owns reinstall.
