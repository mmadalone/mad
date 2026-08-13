# RPCS3 on Steam Deck — settings notes

Hardware context: Van Gogh RDNA2, 8 CU GPU (weak), Zen2 4c/8t CPU, 16GB shared. Mesa RADV Vulkan. EmuDeck AppImage.

## Key authoritative findings (sources + date: 2026-06-08)

- **Async Texture Streaming**: RPCS3 official tweet (2022-02-18, x.com/rpcs3/status/1494844126365458440) explicitly warns "enabling this setting on weak GPUs can worsen performance instead of improving it." Deck has a weak GPU → keep Async Texture Streaming 2 = OFF globally. Also has known per-game bugs (Killzone 3 #11707, Iron Man #16368). Source of truth for Deck.

- **Shader Mode — "Async with Shader Interpreter" is risky on AMD/RDNA**: long history of AMD GPU crashes & graphics corruption with the shader interpreter: issues #8200, #16374 (RDNA crash), regression #16512 (Jan 2025, games won't boot), repeated fixes #16540/#16576/#16652/#17678/#18676 (2025-2026), plus AMD Adrenalin 26.6.1 needed to fix RDNA3/4 (Windows). Conclusion: on the Deck (RADV), default **"Async (multi-threaded)" = Async Shader Recompiler** is the safe, recommended global default. The interpreter eliminates first-encounter shader stutter (renders missing shaders via interpreter while compiling) but is unstable on AMD → only try per-game if a specific title stutters badly, and verify stability. Do NOT make it the global default.

- **SPU Block Size**: Mega became the upstream **default** on 2025-11-29 (PR #17769) — ties smaller units together, fewer/larger compiled units, generally faster. BUT it introduced game compat regressions (issue #17774 "Several games do not work with SPU Block Size Mega"). So: Mega is a reasonable global default for speed, fall back to Safe per-game if a title breaks. "Safe" = max compatibility, slower.

- **Write Color Buffers (WCB)**: default OFF. ~10-15% perf cost when ON. Per-game-only fix for games that need it (Demon's Souls etc.); RPCS3 auto-enables it for known titles when using per-game configs. Should NOT be global-ON on a weak GPU. (RPCS3 wiki Default Settings / FAQ.)

- **Multithreaded RSX**: disabled by default specifically for low-core-count CPUs. On weak CPUs it can add stutter / 100% load spikes without raising FPS; flickering = the extra thread being unscheduled. Keep OFF on Deck (4c/8t). (rpcs3 progress report June 2019, issue #6351.)

- **RSX FIFO Accuracy**: default = Fast (fastest). Raise to Atomic / Ordered&Atomic only per-game if a title crashes/glitches (PR #12204 suggests raising on crash). Keep Fast globally.

- **Accurate SPU Reservations**: some of this functionality was folded into defaults; the dedicated accurate-RSX-reservation access has a perf penalty on some games. Leave at the RPCS3 default unless a game needs it.

- **Driver Wake-Up Delay**: default 0. Range 0-7000µs; even 800µs = 1/20 of a 60fps frame. Increasing it caused a severe perf regression (issue #12295). Keep 0 globally; only a few games (God of War 3/Ascension) want a small bump per-game.

- **Resolution / Resolution Scale**: 100% scale = native PS3 res (often 720p). Internal res 1280x720 + 100% scale is correct for Deck (1280x800 panel, ~720p docked-friendly). Do NOT raise resolution scale on the 8-CU GPU — GPU-bound. Anisotropic only if GPU headroom (usually leave auto).

- **Frame Limit**: Auto is fine (matches game's vblank). For battery/frame-pacing, an explicit cap (e.g. 60/30) via RPCS3 or Deck's per-game FPS cap gives steadier frametimes & lower power than uncapped.

## General Deck workflow
- Tune globally for safe/fast defaults; do PER-GAME custom configs (right-click game → Create Custom Configuration) for WCB, SPU Block Size fallback, shader interpreter trials, FIFO accuracy, wake-up delay. Check RPCS3 compatibility wiki per title.

Sources: wiki.rpcs3.net (Help:Default Settings, Help:Configurations, Template:Config), blog.rpcs3.net async shader post (2018-08-08), github.com/RPCS3/rpcs3 issues/PRs cited above, x.com/rpcs3 (2022-02-18), EmuDeck manual (manual.emudeck.com/tricks/rpcs3). Retrieved 2026-06-08.
