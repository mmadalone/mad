# RPCS3 — God of War Collection (PS3 2009/2010, GoW 1 + 2 HD)

Cached 2026-06-08. Sources: RPCS3 Wiki (via Wayback 2026-03-05 snapshot), rpcs3.net
compatibility list (Wayback snapshot), Wikipedia, SerialStation/GameTDB, RPCS3 GitHub
issues #4974/#5864/#11099, Steam community + YouTube Deck reports.

## What it is
- "God of War Collection" (2009 NA / 2010 EU) = HD remasters of **God of War (2005)**
  and **God of War II (2007)**, BOTH originally **PlayStation 2**. Ported by Bluepoint
  Games. 720p, targets 60 fps, Trophies. First "Classics HD" title.
- DO NOT confuse with: God of War: Origins Collection (2011, the two PSP games),
  God of War Saga (2012, 5-game bundle), or "Volume II"/Origins.

## Title / serial IDs
- Disc collection: **BCES00791** (EU), **BCES00800**, **BCUS98229** (NA),
  **BLJM60200** (JP), NPJA00066, BCAS20102 (Asia), BCKS10093 (KR).
- Also sold as standalone PSN HD titles:
  - God of War HD: NPEA00255, NPUA80490, NPHA80104, NPJA00061
  - God of War II HD: NPEA00256, NPUA80491, NPJA00062, NPHA80105
- Internal resolution listed on wiki: 960x1080.

## RPCS3 compatibility
- **Status: PLAYABLE** (top tier). Stable for years — compat-list status update
  2020-10-09, tested build #9024; still Playable on current builds (re-listed 2026-02).
- Covers BCES00791 / BCES00800 / BCUS98229 / BLJM60200.

## Required / recommended config (from wiki)
- **Framelimit = 60.** Game logic is tied to 60 fps: if it runs faster it speeds up;
  if it can't hold 60 it slows down (no fluid sub-60). So a locked 60 is needed for
  correct speed — this is the crux of the Deck challenge.
- Recommended patch (Patch Manager): "Skip any videos with X button".
- Aspect-ratio note / patches: the HD remasters fake 16:9 by cropping 25% off the
  vertical viewport (same as PS2). Community patches exist for true 16:9 and for 4:3
  (the 4:3 patch needs "Stretch To Display Area" checked).

## Known issues
- **Narrow / squished graphics at 1080p** (GitHub #11099, Nov 2021). The render gets
  distorted at certain output resolutions. Mitigated by the aspect-ratio patches above
  / not forcing problematic resolution-scale values. Relevant because the Deck panel is
  1280x800 — keep an eye on AR.
- **Engine-level progression bugs from the original games** (NOT emulator bugs): GoW1
  Pandora's Box may not spawn; GoW2 Titan's Cave event may fail to trigger and halt
  progress. Restarting the game (sometimes a few times) clears it. Happens on real
  PS3/PS2 too.
- Old regressions (#4974 from 2018, game-selection-menu #5864) are historical; the
  game has long since returned to Playable.

## Steam Deck performance (honest)
- Best, most-corroborated outlook: GoW 1 & 2 (PS2-era assets, light vs GoW III) run
  WELL on the Deck — community reports of locked/near-locked **60 fps** with the
  60 framelimit. This is the well-behaved end of RPCS3 on Deck.
- BUT because speed is tied to 60 with no sub-60 fluidity, any dip below 60 = the game
  slows down (not just lower fps). Expect occasional dips in heavy combat / busy scenes
  / shader-compilation stutter on first encounters; build the shader cache by playing.
- Contrast for taste/expectation setting: GoW III / Ascension on Deck are the HARD end
  (~30–40 fps, often unstable). The Collection (1+2) is the safe pick for "smooth".
- Alternative worth flagging to user: GoW 1 & 2 also exist as native PS2 games — PCSX2
  on the Deck runs them more easily/reliably than RPCS3 if absolute smoothness is the
  priority. RPCS3 Collection gives the HD remaster + trophies.
- Practical Deck tuning: SPU LLVM (default), Vulkan, framelimit 60, async shader +
  build cache, watch AR/resolution-scale to avoid the narrow-graphics distortion.
