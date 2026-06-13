# MAD / controller-router — fix plan

> Generated 2026-06-13 from the review findings (`REVIEW-FINDINGS.md`). Each fix below was designed by reading the **actual code at the fix site**, then sanity-checked by an adversarial fix-critic. Execute one numbered step per fresh session. **Read-only design — no code was changed.**

**Risk marks:** `·` trivial · `○` low · `◆` moderate · `◆◆` high. **Gates:** `rebuild` = ships in the ES-DE rebuild · `on-device` = needs you to verify on-screen/hardware · `verify-lead-first` = confirm the issue before fixing (unverified critic lead or contested). Severity per finding is in `REVIEW-FINDINGS.md` (same IDs).

## ⚠️ Correction to the wave-3 seam conclusion (verified in code)

The wave-3 seam analysis declared `[0.2]`/`[10.11]` (unlocked `_ENUM_CACHE`) **dead** on the grounds that backend state is mutated only on the single stdin-reader thread. **That was wrong.** `_WatchStream` (`lib/madsrv/device_cmds.py:128`) runs on its **own daemon thread** (`rpc.py:107`) and calls `enumerate_devices()` every 2s, while `slow=True` pool workers (tester/preview/capture) call it concurrently — both mutate the module-global `_ENUM_CACHE` (`lib/devices.py:163-217`) with no lock. So **`[0.2]` is a confirmed live data race**, not refuted. `[2.1]` (`set_ra_option` RMW on the pool) is live for the same reason. Both are addressed in Batch D. (`[10.2]`, the watch/unwatch token swap, stays refuted — that part is serial on the stdin thread.)

## Execution sequence

| # | Batch | What | Effort | Depends on |
|---|---|---|---|---|
| 1 | `B-helpers` | Create lib/fsutil.py + lib/proc_guard.py shared helpers (no callers yet) | S (1 focused session, ~1-2… | none |
| 2 | `B-gamelist-guards` | Add ES-DE-running abort guard to every gamelist writer (lowest risk, high value) | S (one session; mechanical… | order 1 (lib/proc_guard.py) |
| 3 | `B-atomic-writes` | Route all config/gamelist/cfg writers through fsutil atomic helpers | M (one larger session; ~15… | order 1 (lib/fsutil.py) |
| 4 | `B-recoverable-delete` | Replace bare unlink() of user data with recoverable_delete (rule #5) | M (one session; 6.5 needs… | order 1 (lib/fsutil.py) |
| 5 | `A-cpp-fork` | All C++ fork fixes — ONE rebuild from ~/esde-build → ~/Applications/ES-DE-MAD.AppImage | M-L (one session + a full… | none (independent of Python batches). Gates on… |
| 6 | `A-cpp-W7.1` | VERIFY-FIRST then fix W7.1 per-frame setBlockInput override (separate so it doesn't gate t… | S fix + on-device verify (… | verify-lead-first (on-device launch-screen beh… |
| 7 | `C-update-recovery` | SteamOS-update recovery script fixes (readonly + elevation chain) | M (one session; the C0.x f… | verify-lead-first for C0.0/C1.1/C1.0/C0.1 (the… |
| 8 | `D-daemon-concurrency` | Daemon concurrency: lock _ENUM_CACHE, serialize set_ra_option, detach quit_cmd | M (one session; 3 distinct… | verify-lead-first is satisfied by my code read… |
| 9 | `D-daemon-gui-robustness` | Daemon/GUI fail-soft + leak fixes (policy guards, ffmpeg, regex .group, pipefail) | L (largest session by coun… | verify-lead-first for 4.0/N0.1, N1.0, N0.0, N6… |

**1. Create lib/fsutil.py + lib/proc_guard.py shared helpers (no callers yet)** (`B-helpers`)  
Create two NEW modules. lib/proc_guard.py: esde_running() -> bool wrapping subprocess.run(['pgrep','-f','ES-DE|emulationstation'],capture_output=True).returncode==0 (copy steam-collection-sync.py:83-86 verbatim); process_running(pattern, exact=False) generalizing it (pgrep -x for exact, mirroring systems_cmds.py:53-58 _retroarch_running). lib/fsutil.py: atomic_write_text(path, text) and atomic_write_bytes/atomic_write(path, data) accepting str OR bytes — modeled on localpolicy.py:88-98 (mkdir parents; write path.with_suffix(suffix+'.router-tmp'); os.replace; on OSError unlink tmp+re-raise); atomic_write_json (json.dumps then atomic_write_text); atomic_replace_artwork(dst, src) (copy src to d…

**2. Add ES-DE-running abort guard to every gamelist writer (lowest risk, high value)** (`B-gamelist-guards`)  
Add a 3-line `from lib.proc_guard import esde_running; if esde_running(): print('Close ES-DE first…'); return 1` (or sys.exit) at the TOP of main() in: steam-collection-gen.py:171 (N7.0), steam-fetch-metadata.py (10.5/9.1, before the GL.write at :252), dedup-disc-gamelists.py (11.1/9.3, before :68), skyscraper-apply.py (11.2/9.4, before :41), openbor-gen-gamelist.py (9.2, before :139). Skip the guard under --dry-run/--check where those flags exist. These are behavior-preserving when ES-DE is closed (the documented normal case), so zero regression risk for correct usage. RISK NOTE: steam-collection-sync.py already proves this exact pattern is safe and accepted. Also convert the steam-collecti…

**3. Route all config/gamelist/cfg writers through fsutil atomic helpers** (`B-atomic-writes`)  
Replace bare write_text/open('w')/yaml.safe_dump-to-file with fsutil atomic helpers. Targets (all confirmed non-atomic): standalone backends 5.0/10.7 — pcsx2_cfg.py:195, eden_cfg.py:187, xemu_cfg.py:110, cemu_cfg.py:132+:203, rpcs3_cfg.py:183 (rpcs3: yaml.safe_dump(data) to a STRING then atomic_write_text). supermodel-sinden-smart.py:178-193 (8.1, + one-time .router-backup). sinden-update-retroarch-mouseindex.py:43 (8.2, + handle missing-key by appending). steam-fetch-metadata.py:252 (10.6). steam-collection-gen.py (if not already done in order 2). tester_cmds.py:228-230 (3.0, calibration/position/P2 JSON). mad_gamepad_tester.py:407/668/672/1121 + mad_xarcade_tester.py:212/600 (N7.3). sinden…

**4. Replace bare unlink() of user data with recoverable_delete (rule #5)** (`B-recoverable-delete`)  
Replace e.unlink()/LOCAL.unlink() with fsutil.recoverable_delete(...) and REPORT the returned _TMP path. Targets: steam-fetch-metadata.py:138/145 (11.4/12.3/9.5 — cover deletion; tmp_base=/run/media/deck/1tbDeck since covers live under downloaded_media; per 11.4 prefer leaving a landscape cover OR moving to covers/_rejected-landscape rather than deleting — a sideways cover beats no cover, so the move is the right call). mad_backup.py:158 reset_local (6.1 — move LOCAL to ~/Downloads/_TMP-mad-reset-<ts>). C0.3 clean-manual-cruft.py:60/69-70 (already uses _TMP; add the duplicate-target collision branch: group recover targets by dst and route all-but-one to the _TMP branch, or re-check dst.exist…

**5. All C++ fork fixes — ONE rebuild from ~/esde-build → ~/Applications/ES-DE-MAD.AppImage** (`A-cpp-fork`)  
Apply every C++ fix in one batch (single rebuild boundary). READY fixes: W7.0/W6.0 (same bug) — add `if (mCurrentView != nullptr)` inside start/stop/pause/muteViewVideos in ViewController.h:87-90, mirroring resetViewVideosTimer:91-95 (makes ALL external callers safe). W4.0/CC.2 (same bug) — in GuiMadPageSplash.cpp:175-209 replace the raw `SwitchComponent* sc=switchComp.get()` capture with `std::weak_ptr<SwitchComponent> weakSc{switchComp};` and at the top of BOTH lambdas do `auto sc{weakSc.lock()}; if(!sc) return;` before any sc-> deref (mirror GuiMadPageBackends.cpp:510-540 weakChips). W2.0 — GuiMadPageGamepads.cpp:490-492 wrap GetString() in `if (allowedArr[i].IsString())` like the surroun…

**6. VERIFY-FIRST then fix W7.1 per-frame setBlockInput override (separate so it doesn't gate the order-5 rebuild)** (`A-cpp-W7.1`)  
W7.1 (main.cpp:549) writes window->setBlockInput(!esHasFocus) UNCONDITIONALLY every loop iteration, clobbering ViewController's intentional launch/rescan input-blocks. FIRST verify on-device whether a stray button press during the launch-screen countdown actually dismisses it early (the stated visible symptom) — this is our patched launch-screen feature so the regression matters. THEN fix: only write the flag on the focus TRANSITION (move setBlockInput into the existing `if (esHasFocus != sHadFocus)` block at :555-564 that already gates the video pause/resume), OR OR the focus-desire with the existing block rather than overwriting, so ViewController's own setBlockInput(true) during triggerGa…

**7. SteamOS-update recovery script fixes (readonly + elevation chain)** (`C-update-recovery`)  
All C0.x/C1.x leads were verified against real code (all confirmed). FIX-C0.0/C1.1: deck-post-update.sh:129 — call samba with elevation `sudo bash "$T/samba-setup.sh"` (matching the script's per-command sudo style) OR make samba-setup.sh self-elevate like sinden-reinstall-deps.sh. FIX-C0.1: samba-setup.sh — append `steamos-readonly enable || true` at the end (after step 6), mirroring install.sh:151 / sinden-reinstall-deps.sh:80; ideally guard so it only re-enables if it disabled. FIX-C1.0: deck-post-update.sh step 7 (:200) — wrap the `sudo pacman -S … python-evdev tk` in `sudo steamos-readonly disable` before and `sudo steamos-readonly enable` after, AND re-init the keyring if empty before i…

**8. Daemon concurrency: lock _ENUM_CACHE, serialize set_ra_option, detach quit_cmd** (`D-daemon-concurrency`)  
0.2/10.11 (CONFIRMED LIVE — the review's seam conclusion was wrong; _WatchStream._scan() on its own thread + slow pool both hit _ENUM_CACHE): add a module-level threading.Lock in lib/devices.py and hold it around the enumerate_devices() cache read/populate/sweep (devices.py:163/193/216-217), mirroring _SDL_LOCK at :394. 2.1: make systems.set_ra_option FAST — drop slow=True at systems_cmds.py:242 so it serializes on the stdin thread like model2.set/profiles.apply_slot (the write is tiny and _retroarch_running() is a quick pgrep); this is cleaner than adding a lock and matches the existing fast tiny-config-writer pattern. 0.0: quit-combo-watcher.py:120 — run quit_cmd detached so the watcher is…

**9. Daemon/GUI fail-soft + leak fixes (policy guards, ffmpeg, regex .group, pipefail)** (`D-daemon-gui-robustness`)  
Fail-soft + resource fixes, reusing same-file guarded patterns. 0.1/12.4/1.5/C1.2: lib/routing.py:48-50 AND lib/policy.py:21-22 — wrap the BASE controller-policy.toml tomllib.load in try/except(TOMLDecodeError,OSError), log to stderr/router.log, fall back to {'systems':{},'backends':{}} (the LOCAL parse at routing.py:51-56 already does this — make base match); also use a context manager (1.3). 1.0/N4.1: lib/policy.py:24-32 load_merged — replace the bespoke 2-level merge with routing.deep_merge so the two never diverge. 4.0/N0.1: router-config-gui.py:1870-1875 quit() — call self._clear() at the top (or _cam_kill_ffmpeg() before _cam_restore_driver()) so the camera ffmpeg + Daphne capture proc…

---

## Shared helpers — build these first (step 1)

Factor these once so the Python batches reuse one implementation instead of N copies. `replaces_existing` = an equivalent already lives inline somewhere and should be lifted into the shared module.

### `esde_running` _(lift existing inline copy into the shared module)_
- **Home:** `lib/proc_guard.py (NEW shared module)`
- **Signature:** `def esde_running() -> bool`
- **Behavior:** Returns True iff ES-DE is up. Body is exactly the existing inline check from steam-collection-sync.py:83: `return subprocess.run(['pgrep','-f','ES-DE|emulationstation'], capture_output=True).returncode == 0`. NOTE the regex is intentionally 'ES-DE|emulationstation' (matches both the AppImage process name 'ES-DE' and the legacy 'emulationstation' binary) — do NOT 'simplify' it. Pair it with a thin helper `def abort_if_esde_running(action='write the gamelist') -> bool` that prints the standard message ('ES-DE is running — close it first (it rewrites gamelists on exit). Aborting.') and returns True when it aborted, so each caller is one line: `if abort_if_esde_running(): return 1`. An equivalen…
- **Used by:** N7.0, 10.5, 9.1, 11.1, 9.3, 11.2, 9.4, 9.2, 6.6 (deck-restore.sh — shell, see notes), C1.3 (mount-check sibling, see notes)

### `process_running`
- **Home:** `lib/proc_guard.py (NEW shared module)`
- **Signature:** `def process_running(pattern: str) -> bool`
- **Behavior:** Generalized pgrep -f wrapper used by esde_running() and by the emulator-restore guard. `return subprocess.run(['pgrep','-f',pattern], capture_output=True).returncode == 0`. esde_running() becomes `return process_running('ES-DE|emulationstation')`. Mirrors dolphin-wii-mode.sh:32's `pgrep -fa` guard but in Python. Used by mad_backup.do_restore to refuse a restore while cemu/pcsx2/rpcs3/xemu/eden (or ES-DE) is open.
- **Used by:** 11.5

### `atomic_write_text` _(lift existing inline copy into the shared module)_
- **Home:** `lib/fsutil.py (NEW shared module)`
- **Signature:** `def atomic_write_text(target: Path, content: str, *, encoding: str = 'utf-8', backup_once_suffix: str | None = None) -> None`
- **Behavior:** Write `content` to a sibling temp file in the SAME directory (`target.with_suffix(target.suffix + '.tmp')`), then `os.replace(tmp, target)` so the live file is only ever swapped atomically and can never be left truncated. On any OSError, unlink the tmp and re-raise (mirrors localpolicy.dump:88-98). target.parent.mkdir(parents=True, exist_ok=True) first (mirrors retroarch_cfg._atomic_write:214). Optional `backup_once_suffix` (e.g. '.router-backup'): if given and `target` exists and the backup does not yet exist, shutil.copy2 it once before the swap (folds in the supermodel/sinden 'one-time backup' ask). This is the canonical version of THREE existing copies: retroarch_cfg._atomic_write:213, l…
- **Used by:** 10.7, 5.0 (cemu/pcsx2/xemu/eden/rpcs3), 8.1, 8.2, 10.6, N7.0, 10.5, 9.2, 3.0, N7.1, N7.3, 12.0 (after escaping)

### `atomic_write_json` _(lift existing inline copy into the shared module)_
- **Home:** `lib/fsutil.py (NEW shared module)`
- **Signature:** `def atomic_write_json(path: Path, data, *, indent: int = 2) -> None`
- **Behavior:** Thin convenience: `atomic_write_text(path, json.dumps(data, indent=indent))`. Exists so tester_cmds._write_json and the Tk testers' JSON saves route through one place. REPLACES the non-atomic body of tester_cmds._write_json:228-230 (`path.write_text(json.dumps(...))`) — change that function's last line to call this. mad_gamepad_tester.py / mad_xarcade_tester.py JSON saves should call it too.
- **Used by:** 3.0, N7.3

### `recoverable_delete`
- **Home:** `lib/fsutil.py (NEW shared module)`
- **Signature:** `def recoverable_delete(paths: list[Path] | Path, *, tmp_base: Path, tag: str, recovery_note: str) -> Path`
- **Behavior:** Project rule #5 'never delete — move to recoverable _TMP'. Creates `tmp_base / f'_TMP_{tag}-{time.strftime("%Y%m%d-%H%M%S")}'`, shutil.move() each given path into it, and writes/append a RECOVERY.txt containing recovery_note + a per-file manifest. Returns the _TMP dir Path so the caller can PRINT it (rule #5 requires reporting the actual path). `tmp_base` MUST be chosen same-filesystem as the files (instant move): for SD-card media use Path('/run/media/deck/1tbDeck') (matches steam-collection-sync TMP_BASE:27); for /home files use Path.home()/'Downloads'/'_TMP'. This canonicalizes the pattern that today exists ONLY inline in steam-collection-sync.py:141-155 and as ad-hoc shell in deck-fetch-…
- **Used by:** 11.4 / 12.3 / 9.5 (steam-fetch-metadata cover delete — tmp_base = /run/media/deck/1tbDeck), 6.1 (mad_backup.reset_local — tmp_base = ~/Downloads/_TMP), 6.2 (deck-backup.sh prune — shell equivalent, see notes), 6.5 (mad_backup.do_restore dir-clear — see notes), C0.3 (clean-manual-cruft collision — already uses _TMP, just needs the collision branch)

### `atomic_replace_artwork`
- **Home:** `lib/fsutil.py (NEW shared module)`
- **Signature:** `def atomic_replace_artwork(dst_dir: Path, stem: str, src_path: Path) -> Path`
- **Behavior:** Fixes the unlink-before-copy artwork races (steam-fetch-media.place:93-97, steam-fetch-metadata fix_cover). Copy src to `dst_dir/(stem + '.router-tmp' + suffix)`, then os.replace onto the final `dst_dir/(stem+suffix)`, and ONLY AFTER the replace succeeds, unlink the OTHER differently-suffixed `glob(stem + '.*')` siblings. So an interruption never leaves the game with no artwork. Returns the final path.
- **Used by:** 11.3, N7.2

### `guard_block_input_on_focus_change`
- **Home:** `es-app/src/main.cpp (edit existing block at 547-563 — NOT a new function; C++ fix)`
- **Signature:** `(in-place edit; track `static bool sFocusBlocked` and only setBlockInput on transition)`
- **Behavior:** For W7.1: stop the UNCONDITIONAL per-frame `window->setBlockInput(!esHasFocus)` at main.cpp:549. Move the setBlockInput into the existing `if (esHasFocus != sHadFocus)` transition branch (lines 555-563) so the focus logic only WRITES the block flag when focus actually changes, leaving ViewController's intentional launch/rescan input-blocks intact between transitions. This reuses the transition-detection (`sHadFocus`) that the video-pause code at 555 already established — no new state machine needed beyond reusing that bool.
- **Used by:** W7.1

### `ViewController null-guard (startViewVideos/stopViewVideos/pauseViewVideos/muteViewVideos)` _(lift existing inline copy into the shared module)_
- **Home:** `es-app/src/views/ViewController.h:87-90`
- **Signature:** `void startViewVideos() override { if (mCurrentView != nullptr) mCurrentView->startViewVideos(); } (and the 3 siblings)`
- **Behavior:** For W7.0 + W6.0: add `if (mCurrentView != nullptr)` inside the four single-line video overrides at ViewController.h:87-90, EXACTLY mirroring the sibling resetViewVideosTimer() at :91-95 which already guards the same member. This makes every external caller (including our new main.cpp focus handler that calls start/pauseViewVideos) null-safe in the no-games/no-ROMs/invalid-systems state. This is the file's own established convention — reuse it, do not invent a call-site guard.
- **Used by:** W7.0, W6.0

---

## Batch `A-cpp-fork` — 21 fixes · **needs a rebuild**

_Approach:_ All of these ship in ONE rebuild from ~/esde-build (build with the project's existing ES-DE build flow, then install as ~/Applications/ES-DE-MAD.AppImage). None of them touch Python/shell, so the esde_running()/atomic_write()/move_to_tmp() helpers do NOT apply here — those are for the Python batch. The unifying patterns reused from the fork itself: (1) the resetViewVideosTimer() null-guard idiom in ViewController.h:91-95 (`if (mCurrentView != nullptr)`) for the W7.0/W6.0 deref; (2) the std::weak…

#### [W7.0 + W6.0 (same bug)] · `/home/deck/esde-build/ES-DE/es-app/src/views/ViewController.h:87-90` · gate: **rebuild**

- **Change:** Make the four external video controls null-safe, exactly mirroring the sibling resetViewVideosTimer() at :91-95 which already guards mCurrentView. Change the one-line overrides to: void startViewVideos() override { if (mCurrentView != nullptr) mCurrentView->startViewVideos(); } void stopViewVideos() override { if (mCurrentView != nullptr) mCurrentView->stopViewVideos(); } void pauseViewVideos() override { if (mCurrentView != nullptr) mCurrentView->pauseViewVideos(); } void muteViewVideos() override { if (mCurrentView != nullptr) mCurrentView->muteViewVideos(); } This fixes BOTH W7.0 and W6.0 (…
- **Reuses:** Existing null-guard pattern in the same file: ViewController.h:91-95 resetViewVideosTimer() and :80-83 updateView()
- **Risk (trivial):** Pure defensive guard. When a view exists (the normal case) behavior is byte-for-byte identical. The only behavior change is: in the no-view state these calls now no-op instead of dereferencing null. No regression to video playback when a vi…
- **Verify:** Headless gate: rebuild links and `git diff` shows only these 4 lines changed. On-device (gates_on): boot ES-DE with no ROMs (or temporarily invalid es_systems.xml) so the no-games dialog shows, wait >2s, open the Steam overlay/QAM; before the fix this SIGSEGVs…

#### [W4.0 + CC.2 (same bug)] ○ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageSplash.cpp:175-209` · gate: **on-device**

- **Change:** Replace the raw `SwitchComponent* sc {switchComp.get()}` capture (line 181) used inside the ASYNC RESPONSE lambda with a std::weak_ptr, mirroring GuiMadPageBackends.cpp:506-540. Concretely: (a) After `auto switchComp = std::make_shared<SwitchComponent>();` add: `std::weak_ptr<SwitchComponent> weakSc {switchComp};` (b) Keep the SYNCHRONOUS read in the setCallback body (`const bool on {sc->getState()}` at :183) using the raw `sc` — that fires from the live component's own input() so it cannot dangle (same justification as the existing comment at :177-180). Actually simplest: also lock weakSc the…
- **Reuses:** std::weak_ptr capture idiom at GuiMadPageBackends.cpp:510 (weakChips) with the explicit 'a raw pointer would dangle' comment; the row still owns switchComp via row.addElement(switchComp,false) at :217
- **Risk (low):** Behavior change only in the race window (toggle a pool image, then cycle MODE/FIT before the toggle response lands): before = heap write through a freed SwitchComponent (UAF/corruption); after = the stale setState is skipped (correct — the…
- **Verify:** Headless gate: rebuild compiles (SwitchComponent is already managed by shared_ptr here, so weak_ptr is valid). On-device/ASAN (gates_on): open MAD -> Splash -> random_image with <=picker-cap images; toggle a pool checkbox and immediately press left/right on MO…

#### [W7.1] ◆ `/home/deck/esde-build/ES-DE/es-app/src/main.cpp:547-549, 555-564` · gate: **verify-lead-first**

- **Change:** Stop asserting setBlockInput every frame; only drive it on the focus TRANSITION, alongside the existing pause/resume video logic. Currently :549 does an UNCONDITIONAL `window->setBlockInput(!esHasFocus);` every loop iteration, which clobbers ViewController's intentional input-blocks (launch animation via triggerGameLaunch() ViewController.h:101, rescan, return-from-game swallow). Fix: delete the per-frame write at :549 and fold the block into the transition branch at :555-563 so it only fires when esHasFocus changes: static bool sHadFocus {true}; if (esHasFocus != sHadFocus) { window->setBlock…
- **Reuses:** The existing focus-transition block already present at main.cpp:555-563 (just move the setBlockInput into it); mirrors how pauseViewVideos is already transition-gated
- **Risk (moderate):** This is the trickiest fix because of edge ordering. Consideration: today, if ES-DE loses focus mid-launch-animation, the per-frame write keeps input blocked while unfocused — after the fix, input is blocked on the loss transition and stays…
- **Verify:** Headless gate: rebuild compiles, `git diff` shows the per-frame :549 write removed and the transition branch updated. On-device (gates_on): trigger a game launch (launch-screen countdown visible) and mash a face button — before the fix a stray press can dismis…

#### [W1.0] ◆ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/GuiMadPanel.cpp:456-474 (update), context 147-152 / 411-412` · gate: **verify-lead-first**  🛑 **fix-critic: NOT sound as written**

- **Change:** Add a client-side safety reset so a missed input.lock 'locked:false' event can't wedge the panel forever. mInputLocked is set ONLY by the daemon's input.lock event (:148); if the daemon dies or drops the unlock, :411-412 swallows ALL input permanently. The capture modal is window-topmost while a capture is live, and GuiMadPanel::update() only runs when the PANEL is topmost (the comment at :458-459 states this). So: when update() runs (panel is topmost) AND no GuiMadCaptureModal is currently on the Window stack above us, there is no legitimate reason for the panel to be input-locked — clear it.…
- **Reuses:** Existing reset points already in the file (onBackendReady :163, :192) prove the maintainers consider mInputLocked safe to force-clear; the topmost-only update() invariant documented at :458-459
- **Risk (moderate):** Must NOT clear the lock while a capture/tester modal is legitimately open (that would let the panel steal input from a live capture). The whole correctness hinges on accurately knowing 'is a capture modal currently above me'. If GuiMadPanel…
- **Verify:** Headless gate: compiles. On-device (gates_on): open MAD, start a Daphne/button capture (input locks), then kill mad-backend.py (pkill -f mad-backend.py) so the 'locked:false' never arrives; before the fix the panel is wedged (no input), after the fix the panel…
- **🛑 Critic concern:** The fix's central premise is WRONG. It claims 'GuiMadPanel already pushes GuiMadCaptureModal (it owns the push site)' and that captureModalActive() is easy to implement by tracking the panel's own push. It does NOT: GuiMadCaptureModal is pushed by the PAGES (GuiMadPagePlayers.cpp:128, GuiMadPagePreview.cpp:278, GuiMadP…
- **→ Adjustment:** Do NOT implement captureModalActive() via a panel-owned bool. First VERIFY the residual gap is real on-device (kill the daemon mid-capture, confirm input wedges with the modal already gone). If real, the robust fix is daemon-side or via the modal's own lifetime: have GuiMadCaptur…

#### [W2.0] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageGamepads.cpp:490-493` · gate: **rebuild**

- **Change:** Guard the rapidjson GetString() on the Wii accessory 'allowed' array, mirroring every other array loop in this codebase (e.g. the images loop in GuiMadPageSplash.cpp:60-63 checks IsString() first). Change: for (rapidjson::SizeType i {0}; i < allowedArr.Size(); ++i) extAllowed.emplace_back(allowedArr[i].GetString()); to: for (rapidjson::SizeType i {0}; i < allowedArr.Size(); ++i) { if (allowedArr[i].IsString()) extAllowed.emplace_back(allowedArr[i].GetString()); } rapidjson's GetString() asserts (or returns garbage in release with assertions off) if the element is not a string; a malformed daem…
- **Reuses:** The IsString()-before-GetString() guard already used in this exact file's other arrays and in GuiMadPageSplash.cpp:61-62
- **Risk (trivial):** Defensive; well-formed payloads (the only ones the trusted local daemon sends today) are unaffected — every element is already a string. Only changes behavior on a malformed/garbage element (skip instead of crash).
- **Verify:** Headless gate: compiles. Functional: open MAD -> Gamepads with a Wii accessory (nunchuk/classic) and confirm the accessory art/allowed-sprites still render correctly (the guard is a no-op for valid data).

#### [W1.2] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadTheme.cpp:203` · gate: **rebuild**

- **Change:** Replace the throwing `return STOCK.at(key);` with a non-throwing lookup so a future MadColor enum value without a STOCK entry can't throw std::out_of_range out of color(). Change line 203 to: const auto stockIt = STOCK.find(key); return stockIt != STOCK.cend() ? stockIt->second : 0xFF00FFFF; // magenta = 'missing color' tell This matches the find()-not-at() style already used throughout color() itself (lines 188-202 all use find()/cend()).
- **Reuses:** The std::map::find()/cend() pattern used everywhere else in this same function (MadTheme.cpp:188-202)
- **Risk (trivial):** No behavior change for any current MadColor (all 11 are in STOCK). Only changes the impossible-today path (new enum value added without STOCK entry) from a throw/abort to a visible magenta fallback. The findings note 'no such call path exis…
- **Verify:** Headless gate: compiles. No runtime change observable today; visual confirmation that MAD panel colors are unchanged on-device.

#### [W4.1] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageModel2.cpp:169-172` · gate: **rebuild**

- **Change:** Guard against inverted bounds before std::clamp (std::clamp is UB if hi < lo). After computing lo/hi/step, add a swap: float lo {static_cast<float>(numberAt(setting, "min", 0.0))}; float hi {static_cast<float>(numberAt(setting, "max", isFloat ? 2.5 : 9.0))}; if (hi < lo) std::swap(lo, hi); const float step {...}; const float cur {std::clamp(static_cast<float>(numberAt(setting, "value", lo)), lo, hi)}; (Drop the `const` on lo/hi or compute the swap before binding to const — easiest is to make lo/hi non-const as shown.) <algorithm> is already included for std::clamp; <utility> for std::swap is t…
- **Reuses:** Standard std::swap guard recommended in the finding; pairs with the W5.1 MadStepper guard below
- **Risk (trivial):** Only triggers on a malformed daemon-supplied min>max (never happens with the current trusted model2 schema). For valid data the swap is a no-op. Removes a UB edge.
- **Verify:** Headless gate: compiles. Functional: open MAD -> Model 2, exercise the number steppers (resolution/etc.) and confirm clamping behaves (values stay in range). Valid data path unchanged.

#### [W5.1] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadStepper.cpp:33 (constructor init list)` · gate: **rebuild**

- **Change:** Clamp mStep to a tiny positive floor in the constructor so adjust()'s `(mMax - mMin) / mStep` at :90 can't divide by zero. Change the member init at :33 from `, mStep {step}` to: , mStep {std::max(step, 1e-6f)} Requires <algorithm> for std::max — already included at :13 (<cmath>) plus this file uses glm::clamp; add `#include <algorithm>` if not already present (it is needed for std::max). A mStep of 0 would today yield a division by zero in maxSteps/stepIndex (inf/NaN -> garbage clamp).
- **Reuses:** Same clamp-the-divisor hardening the finding W5.1 recommends; consistent with W4.1's defensive bounds handling
- **Risk (trivial):** Every real caller passes a positive step (1.0 or 0.1, see GuiMadPageModel2.cpp:171 and the int stepper at GuiMadPageBackends.cpp:564 passing 1.0f), so this never changes observed behavior; it only removes the divide-by-zero UB if a future c…
- **Verify:** Headless gate: compiles. Functional: steppers still increment/decrement by their configured step on-device.

#### [W6.2 + CC.3 (same bug)] · `/home/deck/esde-build/ES-DE/es-app/src/ApplicationUpdater.cpp:419-425` · gate: **rebuild**

- **Change:** std::stoi at :425 still throws std::out_of_range for an all-digit string larger than INT_MAX, defeating the :419-424 non-digit guard's stated goal ('must not throw on the updater thread'). Replace the parse with a range-safe one. Simplest: keep the non-digit guard, then wrap the comparison: try { mNewVersion = (std::stoi(releaseType->releaseNum) > MAD_RELEASE_NUMBER); } catch (const std::exception& e) { LOG(LogWarning) << "ApplicationUpdater: release number \"" << releaseType->releaseNum << "\" out of range — skipping"; continue; } Alternative (no exceptions): use std::strtol into a long and c…
- **Reuses:** The finding's own recommended try/catch; LOG(LogWarning)+continue pattern already used at :421-423 for the non-digit case
- **Risk (trivial):** Reachable only via a hand-edited/corrupted latest_release.json with an all-digit release number > 2^31-1 (CI run numbers never get there). Updater runs on a background thread but an uncaught std::out_of_range there still terminates the proc…
- **Verify:** Headless gate: compiles. Hard to exercise without a crafted feed; optionally unit-confirm by pointing mUrl at a local file with releaseNum="99999999999999" and verifying ES-DE logs the warning and starts normally instead of aborting.

#### [W7.2] ◆ `/home/deck/esde-build/ES-DE/es-app/src/GamescopeFocus.cpp:80-122 (init) and a new file-scope handler` · gate: **on-device**

- **Change:** Install an XSetIOErrorHandler so a broken X connection to the gamescope server doesn't terminate ES-DE. Xlib's DEFAULT IO-error handler calls exit() when the connection drops (e.g. gamescope restarts) — and this code holds a long-lived Display* (mDisplay) polled every frame in hasFocus(). Add, in the anonymous namespace (near tryDisplay, inside the `#if defined(__linux__) && !defined(__ANDROID__)` block): int gamescopeIOErrorHandler(Display*) { // The gamescope X server went away. Returning here lets Xlib NOT exit(); // we longjmp back so the caller can disable polling and fall back to 'focuse…
- **Reuses:** The 'fail toward focused' safe-default already used at GamescopeFocus.cpp:145-148 and :172 (mMyAppId==0 -> true)
- **Risk (moderate):** setjmp/longjmp across the Xlib call is the standard (and only correct) way to survive an XIOError without exit(); but longjmp out of Xlib internals can leak whatever Xlib allocated for the in-flight request — acceptable because we are aband…
- **Verify:** Headless gate: compiles and links (X11 already linked per CC.6). On-device (gates_on): hard to force a gamescope X-server drop safely; at minimum confirm normal focus/overlay behavior is unchanged. If feasible, restart gamescope/Steam session while ES-DE runs…

#### [CC.0 (do this; supersedes W6.1 polish)] ○ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadWiiBridge.cpp:68-81 (child fork branch)` · gate: **on-device**

- **Change:** Add PR_SET_PDEATHSIG in the forked child so the wii-nav-bridge actually dies with ES-DE, making the existing comments at :88 ('PDEATHSIG would be its only lifeline') and :122 ('PDEATHSIG is the backup') TRUE. The header already advertises this (MadWiiBridge.h:11 'dies with ES-DE (stdin EOF + PR_SET_PDEATHSIG on the Python side)'). In the child branch right after fork (before the dup2 chain at :70, or at least before execlp at :80) add: prctl(PR_SET_PDEATHSIG, SIGTERM); and add `#include <sys/prctl.h>` to the includes (the file already has csignal/fcntl/sys/wait/unistd). prctl is async-signal-s…
- **Reuses:** The header/inline comments already document PDEATHSIG as the intended mechanism — this makes the code match the documented design
- **Risk (low):** PDEATHSIG fires when the PARENT THREAD that forked dies, not the whole parent process — so if MadWiiBridge::spawn() is called from a thread that later exits while ES-DE lives, the bridge could get a premature SIGTERM. Mitigation: spawn() is…
- **Verify:** Headless gate: compiles. On-device (gates_on): with a DolphinBar in mode 4, start ES-DE (bridge spawns), then `kill -9` the ES-DE process (bypassing the clean EOF path) and confirm `pgrep -f wii-nav-bridge.py` shows the child has also exited (before the fix, w…

#### [W6.1 (optional polish; low value)] ○ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadWiiBridge.cpp:51` · gate: **rebuild**

- **Change:** OPTIONAL / LOW PRIORITY — recommend SKIP or do minimally. MadWiiBridge.cpp:51 sets `signal(SIGPIPE, SIG_IGN)` process-globally and never restores it. Two honest options: (a) SKIP IT: MadBackend already saves+restores the real SIGPIPE disposition (MadBackend.cpp:60-68 save in spawn, :48-49 restore in dtor), and the code comment at :48-50 explicitly argues that an emulator receiving EPIPE instead of dying on SIGPIPE is the benign direction. The process-global ignore being left set is low-impact. Given MadWiiBridge is an all-static class with NO destructor (MadWiiBridge.h:17-31), restoring is awk…
- **Reuses:** MadBackend.cpp:60-68 (save with sigaction) + dtor restore :48-49 — but adapted to MadWiiBridge::shutdown() since there is no dtor
- **Risk (low):** Honest assessment: this is the lowest-value item in the batch and largely mooted by MadBackend's existing handling. The risk of option (b) is restoring SIGPIPE too early (in shutdown()) while the bridge pipe is still being written elsewhere…
- **Verify:** Headless gate: compiles (if option b taken). No observable runtime change either way; the bridge already behaves correctly.

#### [W0.0] ○ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:134-160 (readerLoop)` · gate: **rebuild**

- **Change:** Cap the reader-thread line buffer so a daemon line with no newline can't grow unbounded. After `buffer.append(chunk, ...)` at :143, add a sanity cap on the un-newlined remainder. Simplest: after the inner newline-draining while-loop and the `buffer.erase(0, pos)` at :159, check: static constexpr size_t kMaxLineBytes {8 * 1024 * 1024}; if (buffer.size() > kMaxLineBytes) { LOG(LogWarning) << "MadBackend: backend emitted a >8MB line with no newline — " "treating the stream as corrupt and dropping the reader"; break; // falls through to mDead=true; mReaderDone=true; like EOF } Breaking out mirrors…
- **Reuses:** The EOF/break path already at MadBackend.cpp:141-142 + the mDead/mReaderDone teardown at :162-163; LOG(LogWarning) convention used at :154
- **Risk (low):** Only triggers on a malformed/runaway daemon line that never terminates — which is also a sign the daemon is broken, so treating the stream as corrupt (drop reader -> restart path) is the right response. No legitimate payload approaches 8MB.…
- **Verify:** Headless gate: compiles. Hard to exercise without a misbehaving daemon; optionally inject a giant no-newline write from a stub mad-backend.py and confirm ES-DE logs the warning and restarts the backend instead of growing memory.

#### [W0.1] ◆ `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:414-430 (writeLine) + 79/84 (pipe2 flags)` · gate: **verify-lead-first**  🛑 **fix-critic: NOT sound as written**

- **Change:** ASSESSMENT FIRST: This is LOW and somewhat speculative. writeLine() does a blocking write() to the daemon's stdin on the UI/render thread; if the daemon stops reading and the pipe fills (64KB default), the UI thread stalls. In practice the daemon drains continuously and requests are tiny NDJSON lines, so the pipe rarely fills. RECOMMENDED MINIMAL FIX: make the write non-blocking and treat EAGAIN as backend-died backpressure rather than a UI stall. In spawn(), after obtaining the write fd (mStdinFd = inPipe[1] at :119), set non-blocking: `fcntl(mStdinFd, F_SETFL, O_NONBLOCK);`. Then in writeLin…
- **Reuses:** The existing writeLine()->false->mDead/EBACKEND_DIED path at MadBackend.cpp:405-411; fcntl flag-setting pattern already used in MadWiiBridge.cpp:89 (FD_CLOEXEC)
- **Risk (moderate):** Behavior change: today a full pipe blocks (UI freezes but the request eventually goes through when the daemon drains); after the fix a full pipe is treated as backend-death (request fails fast, backend restart path). That is arguably MORE c…
- **Verify:** Headless gate: compiles. On-device (gates_on): normal MAD usage must be unaffected (requests still send). To exercise the new path, stub a daemon that stops reading stdin and confirm ES-DE reports backend-died/restarts instead of freezing.
- **🛑 Critic concern:** The proposed non-blocking fix would CORRUPT the wire protocol. writeLine() (MadBackend.cpp:414-430) is a partial-write loop: `while (written < payload.length())`. If mStdinFd is set O_NONBLOCK and a line partially writes then the pipe fills, write() returns -1/EAGAIN MID-LINE — the proposed handler returns false ('back…
- **→ Adjustment:** Recommend SKIP / defer. The blocking-UI-thread risk is largely theoretical given tiny requests vs a 64KB pipe and a continuously-draining daemon. If pursued, do NOT make the write non-blocking with the current partial-write loop. A safe alternative: keep blocking writes but bound…

#### [W0.2] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadJson.h:25-29 (parseLine)` · gate: **rebuild**

- **Change:** OPTIONAL hardening (INFO severity). Use RapidJSON's iterative (non-recursive) parser so a deeply-nested daemon line can't blow the stack. Change parseLine() at :27 from: doc.Parse(line.c_str(), line.length()); to: doc.Parse<rapidjson::kParseIterativeFlag>(line.c_str(), line.length()); rapidjson/document.h is already included (:12). kParseIterativeFlag bounds stack usage regardless of nesting depth; the trusted local daemon never sends deep nesting today, so this is purely defensive.
- **Reuses:** RapidJSON's built-in kParseIterativeFlag; no new code
- **Risk (trivial):** The iterative parser is a drop-in for the recursive one (same acceptance, slightly different perf characteristics, negligible for KB-sized lines). No behavior change for valid input. Pure stack-overflow defense against a malformed/hostile p…
- **Verify:** Headless gate: compiles. No observable change for normal payloads; confirm MAD pages still parse daemon responses correctly on-device.

#### [W0.3] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:125 (spawn, mReaderThread assignment)` · gate: **rebuild**

- **Change:** OPTIONAL defensive (INFO). Before reassigning mReaderThread, assert/ensure it is non-joinable, so a future code path that calls spawn() without a preceding shutdownChild()/join can't trip std::terminate (assigning to a joinable std::thread terminates). Today restart()->terminate()->shutdownChild() joins it (:479-486) before spawn(), so the invariant holds — this just makes it explicit. Add right before :125: if (mReaderThread.joinable()) { LOG(LogWarning) << "MadBackend: spawn() found a live reader thread — joining first"; mReaderThread.join(); } (Only reached if mStdoutFd were still open; har…
- **Reuses:** The joinable()/join() guard already used in shutdownChild() at MadBackend.cpp:479-485
- **Risk (trivial):** No behavior change on any current path (the thread is always joined before spawn). Purely guards a latent invariant against future refactors. Could be skipped without functional impact.
- **Verify:** Headless gate: compiles. No runtime change; normal backend spawn/restart unaffected.

#### [CC.4] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadTileGrid.cpp:212-218 (render, before pushClipRect)` · gate: **rebuild**

- **Change:** Add the degenerate-clip guard MadScrollView already has. MadTileGrid computes scale-aware dims at :213-214 then pushClipRect at :215-218 with NO check that the dims are >=1; pushClipRect treats a zero dimension as 'extend to screen edge', DISABLING clipping (tiles bleed outside a zero-height grid). Mirror MadScrollView.cpp:77-82: compute the int clipDim, then before pushClipRect add: const glm::ivec2 clipDim {static_cast<int>(std::round(dim.x)), static_cast<int>(std::round(dim.y))}; if (clipDim.x < 1 || clipDim.y < 1) return; and pass clipDim into pushClipRect instead of recomputing. (Restruct…
- **Reuses:** Exact sibling guard at MadScrollView.cpp:79-82 (the finding explicitly says to match it)
- **Risk (trivial):** Cosmetic-only and not currently reachable (no zero-height tile grid arises today). Matches the sibling widget for consistency. For non-degenerate sizes (every real case) behavior is identical.
- **Verify:** Headless gate: compiles. Visual on-device: tile grids (e.g. theme/art pickers) render and clip identically to before.

#### [CC.1] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/GuiMadCaptureModal.cpp:242-251 (render)`

- **Change:** INFO / DOCUMENT-ONLY (no current bug). The modal manually renders mPanel then itself because Window only renders bottom+top GUI; this is correct ONLY while the modal is the immediate top GUI and the panel is immediately beneath. There is an existing comment at :244-247 explaining the trick — extend it to state the INVARIANT explicitly so a future change that pushes any GUI above an open capture modal doesn't silently break the backdrop. Add to the comment: '// INVARIANT: this assumes the capture modal is the topmost GUI and the panel is the GUI directly beneath it on the Window stack. Do not p…
- **Reuses:** The existing explanatory comment at GuiMadCaptureModal.cpp:244-247
- **Risk (trivial):** Comment-only; zero runtime effect. Purely guards future maintainers. Safe to include or skip.
- **Verify:** Headless gate: compiles (trivially). No runtime verification needed.

#### [CC.5] · `/home/deck/esde-build/ES-DE/es-app/src/GamescopeFocus.cpp:16-26 (debugLog)`

- **Change:** INFO / OPTIONAL — recommend SKIP. debugLog opens+appends+closes an ofstream on every focus transition (cheap, only fires on transitions not per-frame) and the file grows unbounded over a long session. Negligible impact. If desired, the only worthwhile tweak is a size cap: before writing, if the file exceeds e.g. 1MB, truncate/rotate it. Given it's a low-volume diagnostic that the header (:34-36) says is 'safe to leave on', the honest recommendation is to leave it as-is. Do NOT keep the stream open across calls (that complicates lifetime for no real gain on a transition-only path).
- **Reuses:** n/a (recommend no change)
- **Risk (trivial):** No functional impact either way. Listed for completeness; the right call is almost certainly to skip it.
- **Verify:** n/a — no change recommended.

#### [W3.0] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageDaphne.cpp:19-25, 60-66`

- **Change:** INFO / NO CHANGE NEEDED (self-heals). The file-scope statics sScope/sGamedir/sBase (:22-24) persist across page entries within a session; on panel reopen a stale per-game scope could briefly show a wrong/failed map — but load()'s response handler ALREADY falls back to global on failure (:60-66: if scope=='game' and !ok, reset to global and reload). So it self-corrects within one request. If belt-and-suspenders is wanted, reset the statics to global in the GuiMadPageDaphne destructor, but it's unnecessary. Recommend SKIP.
- **Reuses:** The existing stale-scope fallback at GuiMadPageDaphne.cpp:60-66
- **Risk (trivial):** Already handled by the existing fallback. Any change here is pure cosmetic robustness. Skip unless the user reports a visible wrong-map flash on reopen.
- **Verify:** On-device only if pursued: reopen MAD->Daphne after a per-game scope and confirm no wrong-map flash (already true today).

#### [W3.1] · `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageBackends.cpp:675-705, 349-357`

- **Change:** INFO / ACCEPTABLE AS-IS. mSuppressChildPopRefresh is set true before the apply request (:675) so the child-pop doesn't double-refresh; the apply response refreshes from post-apply truth (:702 refresh()). The theoretical gap (a never-arriving apply response leaves the suppress flag set and skips the pop refresh) is bounded — the next user action or page rebuild refreshes anyway. If full robustness is wanted: in onChildPopped refresh unconditionally and let the apply response's refresh() be idempotent (drop the suppress flag). Recommend SKIP for this rebuild unless the user sees a stale Backends…
- **Reuses:** n/a (recommend no change); the existing refresh() at :702 is the truth path
- **Risk (trivial):** Acceptable per the finding. A dropped apply response is already rare (and itself signals backend trouble that triggers other recovery). Skipping is the honest call.
- **Verify:** On-device only if pursued: apply a profile slot on Backends and confirm the list reflects it (already works).

_Batch notes:_ SEVERITY/SCOPE NOTES the implementer must know before touching anything: 1. W6.0 is a DUPLICATE of W7.0 — same two call sites (main.cpp:555-562 -> ViewController.h:87,89). One fix (the null-guard inside ViewController.h startViewVideos/stopViewVideos/pauseViewVideos/muteViewVideos) resolves BOTH. Do not write two fixes. 2. W4.0 and CC.2 are the SAME bug (CC.2 explicitly says it 'confirms W4.0'). CC.2 is a not-yet-hardened completeness-critic lead but it only sharpens W4.0, which IS hardened (sta…

---

## Batch `B-data-safety` — 17 fixes

_Approach:_ All fixes lean on three NEW shared helpers that a separate spec will create in a new module `lib/fsops.py` (chosen because every lib/*_cfg.py and lib/mad_backup.py already do relative imports `from . import X`, and root scripts already use the `sys.path.insert(0, parent); from lib import sgdb` idiom — so both layers can import fsops with zero new plumbing). The three helpers, modeled on patterns ALREADY in the tree: 1) `esde_running() -> bool` — wraps `subprocess.run(["pgrep","-f","ES-DE|emulati…

#### [N7.0] ○ `/home/deck/Emulation/tools/launchers/steam-collection-gen.py:171 (write), add guard in main() before the gamelist build ~line 150; imports at top ~line 14`

- **Change:** Add the ES-DE guard + atomic write. (1) After the existing top-of-file imports add the lib import using the established idiom: `import sys; sys.path.insert(0, str(Path(__file__).resolve().parent)); from lib import fsops` (Path is already imported line 15; sys is not — add `import sys`). (2) At the very top of main() (before the gamelist `lines = [...]` build), add: `if fsops.esde_running(): print('ES-DE is running — close it first (it rewrites the gamelist on exit). Aborting.'); return`. (3) Replace line 171 `GAMELIST.write_text('\n'.join(lines) + '\n', encoding='utf-8')` with `fsops.atomic_wr…
- **Reuses:** fsops.esde_running() (==steam-collection-sync.py:83 pattern) + fsops.atomic_write() (==retroarch_cfg.py:213)
- **Risk (low):** Pure addition; when ES-DE is closed (the documented normal case) behavior is unchanged. Only behavior change: refuses to run while ES-DE is up, which is the intended fix. Adding `import sys` is safe.
- **Verify:** Headless: with ES-DE NOT running, `python3 steam-collection-gen.py` writes the gamelist as before (diff the output). Then start ES-DE (user, on-device) and re-run — must print the abort message and exit without touching gamelist.xml (check mtime unchanged).

#### [10.5/9.1] ○ `/home/deck/Emulation/tools/launchers/steam-fetch-metadata.py:guard at top of main() ~line 194; write at line 252; lib import already present line 33`

- **Change:** Same single bug seen by two finders (10.5 and 9.1) — one fix. (1) Add `from lib import fsops` next to the existing `from lib import sgdb` (line 33; sys.path.insert already done line 32). (2) In main(), right after `dry = '--dry-run' in sys.argv` (line 195), add: `if not dry and fsops.esde_running(): print('ES-DE is running — close it first (it rewrites gamelist.xml on exit). Aborting.'); return`. Gating on `not dry` matches the verification note that --dry-run must skip the check. (3) For 10.6 (atomic): replace line 252 `GL.write_text(new, encoding='utf-8')` with `fsops.atomic_write(GL, new)`.…
- **Reuses:** fsops.esde_running() + fsops.atomic_write(); existing .bak at line 217 retained
- **Risk (low):** Guard only fires on a real (non-dry) run while ES-DE is up. The atomic write changes nothing observable when the write succeeds; it only prevents truncation on a mid-write kill.
- **Verify:** Headless: `python3 steam-fetch-metadata.py --dry-run` still runs even if ES-DE is up (guard skipped under dry). On-device: with ES-DE running, a plain `python3 steam-fetch-metadata.py` aborts with the message; with it closed, it writes normally. Atomic: in a /…

#### [11.1/9.3] ○ `/home/deck/Emulation/tools/launchers/dedup-disc-gamelists.py:guard before the dedup() loop in main flow ~after line 20; write at line 68; imports line 14-15`

- **Change:** (1) Add lib import: this script uses `os.path.expanduser` not pathlib, so add `import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from lib import fsops` after the existing imports (line 14-15). (2) The script iterates systems and calls dedup(sysname). Add the guard ONCE before the loop that calls dedup (top of the `__main__`/driver section, before the first write): `if fsops.esde_running(): print('ES-DE is running — close it first (it rewrites gamelists on exit). Aborting.'); sys.exit(1)`. (3) Replace line 68 `open(gl, 'w', encoding='utf-8').write(new)` with `fsops.ato…
- **Reuses:** fsops.esde_running() + fsops.atomic_write(); existing .bak-{TS} at line 48 retained
- **Risk (low):** Guard is a pure precondition. atomic_write must tolerate an os.path str — confirm the helper does Path(path) coercion (the spec should). No change when ES-DE closed.
- **Verify:** Headless with ES-DE closed: `python3 dedup-disc-gamelists.py psx` produces the same hide/show edits (diff the .bak vs new). On-device with ES-DE up: aborts before writing any gamelist.

#### [11.2/9.4] ○ `/home/deck/Emulation/tools/launchers/skyscraper-apply.py:guard before the `for sysn in SYS:` loop ~line 13; write at line 41; imports line 1-6`

- **Change:** (1) Add import: this script has no sys.path setup and imports `sys as _s` (line 6). Add `import os.path; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from lib import fsops` after the existing imports. (2) Add the guard ONCE before the `for sysn in SYS:` loop (line 14): `if fsops.esde_running(): print('ES-DE is running — close it first (it rewrites gamelists on exit). Aborting.'); _s.exit(1)`. (3) Replace line 41 `open(real,'w',encoding='utf-8').write(new)` with `fsops.atomic_write(real, new)`. The per-system `shutil.copy(real, real+'.bak-metaall-'+TS)` at line 24 and the roll…
- **Reuses:** fsops.esde_running() + fsops.atomic_write(); existing per-system .bak + ET validation rollback retained
- **Risk (low):** Guard added once at the top of the multi-system loop, so it aborts before ANY system is touched (not mid-loop). atomic_write replaces the truncate-in-place; the existing post-write ET.fromstring validation + .bak rollback continue to work.
- **Verify:** Headless with ES-DE closed and a populated /tmp/sky-<sys>/gl: run `python3 skyscraper-apply.py snes` → same applied edits as before. On-device with ES-DE up: aborts immediately, no gamelist touched.

#### [9.2] ○ `/home/deck/Emulation/tools/launchers/openbor-gen-gamelist.py:guard at top of main() before the write block ~line 139; write at lines 139-145; imports line 12-15`

- **Change:** (1) Add import: uses os.path (line 12-15). Add `import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from lib import fsops`. (2) At the top of main() (before the `os.makedirs(os.path.dirname(OUT)...)`/write at 139-145), add: `if fsops.esde_running(): print('ES-DE is running — close it first (it rewrites the openbor gamelist on exit). Aborting.'); sys.exit(1)`. (3) ADD A BACKUP (the finding notes this file regenerates from scratch and can drop favourites/playcounts): before writing, if OUT exists, write a timestamped .bak: `if os.path.isfile(OUT): import shutil, time; shu…
- **Reuses:** fsops.esde_running() + fsops.atomic_write(); new .bak mirrors skyscraper-apply.py:24
- **Risk (low):** This script REGENERATES the whole gamelist from CURATED+enrichment data, so it inherently drops any ES-DE-added fields (favourites/playcount) regardless — the .bak makes that recoverable, the guard stops the wasted run while ES-DE is up. Ad…
- **Verify:** Headless with ES-DE closed: run it, confirm a gamelist.xml.bak-<ts> is created and the new gamelist matches prior output. On-device with ES-DE up: aborts.

#### [5.0/10.7] ◆ `/home/deck/Emulation/tools/launchers/lib/pcsx2_cfg.py, lib/eden_cfg.py, lib/xemu_cfg.py, lib/cemu_cfg.py, lib/rpcs3_cfg.py:pcsx2_cfg.py:195; eden_cfg.py:187; xemu_cfg.py:110; cemu_cfg.py:132 and 203-204; rpcs3_cfg.py:183-185`

- **Change:** Route all five standalone-emulator config writers through fsops.atomic_write. Each module already does relative imports (`from .devices import ...`), so add `from . import fsops` at the top of each. Then: pcsx2_cfg.py:195 `ini.write_text(text, encoding='utf-8')` → `fsops.atomic_write(ini, text)`. eden_cfg.py:187 `ini.write_text(text, encoding='utf-8')` → `fsops.atomic_write(ini, text)`. xemu_cfg.py:110 `path.write_text(text, encoding='utf-8')` → `fsops.atomic_write(path, text)`. cemu_cfg.py:132 `_port_path(cfg_dir, port0).write_text(text, encoding='utf-8')` → `fsops.atomic_write(_port_path(cfg…
- **Reuses:** fsops.atomic_write() (==retroarch_cfg.py:213 idiom these modules are inconsistent with); existing .router-backup / _backup_once retained
- **Risk (moderate):** Five files, all on the GAME-START hot path (controller-router writes them every launch). Low logical risk (atomic write produces byte-identical output) but it touches the live controller-assignment path, so a typo here breaks a launch. The…
- **Verify:** Headless per backend: call the module's write function in a /tmp sandbox with a sample config and confirm output is byte-identical to the old write_text path (esp. rpcs3 YAML: diff old-streamed vs new-string output — must match). Then SIGKILL mid-write → targe…

#### [8.1] ○ `/home/deck/Emulation/tools/launchers/supermodel-sinden-smart.py:187-193 (read INI, truncate-rewrite); add import + backup near top`

- **Change:** patch_ini() reads INI then reopens the same path with `open(INI,'w')` (lines 187-193) — non-atomic, no backup. (1) Add `sys.path.insert(0, str(Path(__file__).resolve().parent)); from lib import fsops` (script already imports os/sys/re; confirm Path import or use os.path). (2) Add a one-time backup before the first write (mirror xemu_cfg .router-backup): `bak = INI + '.router-backup';` `if not os.path.exists(bak): shutil.copy2(INI, bak)` (add `import shutil`). (3) Replace the `out = [...]; with open(INI,'w') as f: f.writelines(out)` tail with: build the full text `text = ''.join(out)` then `fso…
- **Reuses:** fsops.atomic_write() + one-time .router-backup (==xemu_cfg.py:104 pattern)
- **Risk (low):** On the Supermodel game-start path (supermodel-sinden.sh invokes it every launch). Atomic write + a one-time backup is additive; the only behavior change is INI is now written via tmp+rename. Confirm writelines vs ''.join(out) produce identi…
- **Verify:** Headless: run `supermodel-sinden-smart.py --info` (no launch) — unaffected. Sandbox: copy a Supermodel.ini, run patch_ini logic, diff result vs old behavior (identical), confirm .router-backup created once, SIGKILL mid-write → INI intact. On-device: launch a M…

#### [8.2] ○ `/home/deck/Emulation/tools/launchers/sinden-update-retroarch-mouseindex.py:43 (CFG.write_text(new)); imports line 11-17`

- **Change:** (1) Add `sys.path.insert(0, str(HERE)); from lib import fsops` — HERE is already defined at line 15 and sys.path.insert(0,str(HERE)) already exists at line 16, so just add `from lib import fsops` after line 17. (2) Replace line 43 `CFG.write_text(new)` with `fsops.atomic_write(CFG, new)`. OPTIONAL per the finding: handle the missing-key case — currently if `input_player1_mouse_index` isn't present the re.sub is a no-op and `new==text` so nothing is written (line 42 guard); the finding suggests appending the line. RECOMMEND keeping the no-op behavior (appending RA keys that RA may not expect is…
- **Reuses:** fsops.atomic_write(); existing sys.path.insert at line 16
- **Risk (low):** On the sinden-start.sh path (game-start-ish, runs after smoother). Atomic write only. I deliberately do NOT implement the append-missing-key suggestion — that is a behavior change with its own risk (RA config semantics); flag it for the use…
- **Verify:** Sandbox: copy global retroarch.cfg, run update_retroarch_cfg(2,3), confirm keys updated atomically and byte-diff matches old write. SIGKILL mid-write → cfg intact. On-device: run sinden-start.sh, confirm P1/P2 mouse_index get pinned and RA still loads its conf…

#### [3.0] ○ `/home/deck/Emulation/tools/launchers/lib/madsrv/tester_cmds.py (+ lib/mad_xarcade_tester.py, lib/mad_gamepad_tester.py):tester_cmds.py:228-230 (_write_json); twins at mad_xarcade_tester.py:212,600 and mad_gamepad_tester.py:407,668,672,1121`

- **Change:** Make _write_json atomic — this is the single shared writer for every persisted tester file (positions/calib/p2-units). Replace tester_cmds.py:228-230 body `path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(data, indent=2))` with: `from .. import fsops` (top of file, alongside `from .. import devices as dv`) then `fsops.atomic_write(path, json.dumps(data, indent=2))` (atomic_write already mkdirs the parent). NOTE this file ALREADY writes atomically for the slot file at lines 882-884 — so the pattern is already trusted here; this just makes _write_json consistent. For th…
- **Reuses:** fsops.atomic_write() (==tester_cmds.py:882-884 slot-file idiom already in this same file)
- **Risk (low):** Regenerable layout/calibration data with self-healing fallback (_read_json swallows corruption → baked defaults), so low. Single-writer (stdin thread), no concurrency concern. Must confirm whether the Tk twins share the helper or inline-wri…
- **Verify:** Sandbox: call _write_json on a tmp path, SIGKILL between truncate and write → file is old-or-new, never empty. On-device: in MAD gamepad tester, drag-align sprites and Save → reopen tester, positions persist (confirms write still works).

#### [11.4/9.5/12.3] ○ `/home/deck/Emulation/tools/launchers/steam-fetch-metadata.py:fix_cover(): 137-139 (replace path) and 143-146 ('removed' path)`

- **Change:** ONE root bug seen by three finders. fix_cover() deletes user covers via e.unlink() with no recovery. Add `from lib import fsops` (line 33). Two sub-fixes: (A) line 143-146 'removed' branch — the destructive one (deletes the ONLY cover with no replacement just for being landscape). RECOMMENDED per the finding's own preferred option: DON'T delete a landscape cover when no portrait was obtained — a sideways cover beats no cover. Change `if existing and not dry: for e in existing: e.unlink(); return 'removed'` to simply `return 'none'` (leave the landscape cover in place); drop the 'removed' stat…
- **Reuses:** fsops.move_to_tmp() (==steam-collection-sync.py:141 pattern); option (A) reuses nothing — it just stops deleting
- **Risk (low):** Behavior change: the 'removed' outcome disappears (a landscape cover is now kept rather than deleted) — this is the intended, safer behavior and the finding's own recommended fix. The replace-branch move_to_tmp adds files under _TMP on ever…
- **Verify:** Sandbox: put a landscape cover, stub the download to fail → confirm cover is now LEFT in place (not deleted), returns 'none'. Put a landscape cover + a working portrait download → confirm old cover moved to _TMP_steam-cover-<ts> with RECOVERY.txt and portrait…

#### [11.3] ○ `/home/deck/Emulation/tools/launchers/steam-fetch-media.py:place(): 93-97 (unlink-then-copy)`

- **Change:** place() unlinks existing art BEFORE copying the new file — if the copy fails the old art is gone. Make it copy-then-replace (mirror the safer cdn_download/sgdb_download in the same file which read bytes first). Replace lines 93-97: `for other in (MEDIA/sub).glob(...): other.unlink(); dst = ...; shutil.copy2(src_path, dst)` with: copy to a temp sibling first, then atomically place, then remove stale OTHER-suffix siblings: `dst = MEDIA/sub/(stem + Path(src_path).suffix); part = dst.with_suffix(dst.suffix + '.part'); shutil.copy2(src_path, part); part.replace(dst); for other in (MEDIA/sub).glob(g…
- **Reuses:** the copy-then-replace pattern already in this file's cdn_download (line 109-114); optionally fsops.move_to_tmp for stale siblings
- **Risk (low):** Regenerable fetch cache; src and dst are confirmed different trees so copy can't self-clobber. The .part temp + replace is additive. Edge: if src_path suffix == an existing sibling suffix, the new copy overwrites it (intended). Behavior is…
- **Verify:** Sandbox: existing Game.jpg, copy a Game.png → confirm Game.png present, Game.jpg removed (stale sibling), and if copy2 is made to fail (point src at a missing file) the existing Game.jpg SURVIVES. On-device: run steam-fetch-media.py, confirm art still updates…

#### [11.5] ◆ `/home/deck/Emulation/tools/launchers/lib/mad_backup.py:do_restore(): 102-130 (no emulator-running guard); copytree merge at 115` · gate: **verify-lead-first**

- **Change:** Add an EMULATOR-running guard (NOT the ES-DE/gamelist guard — backup_targets are emulator INPUT configs per mad_config.py:181, verified by the skeptic). mad_backup.py already imports subprocess (line 16). Best reuse: at the top of do_restore() (after the `if not snap.is_dir()` check, before the copy loop at line 107), pgrep the emulators whose configs are being restored + RetroArch + ES-DE, and hard-stop. Concretely add: `running = [n for n,pat in {'RetroArch':'retroarch','Cemu':'[Cc]emu','PCSX2':'pcsx2','Eden/Yuzu':'Eden|Yuzu|Suyu','RPCS3':'rpcs3','xemu':'xemu','ES-DE':'ES-DE|emulationstation…
- **Reuses:** the pgrep-EBUSY guard pattern from systems_cmds.py:53/253-254 + dolphin-wii-mode.sh:32; could centralize as fsops.proc_running(pattern)
- **Risk (moderate):** VERIFY-FIRST nuance: the headline said 'ES-DE-running guard' but the verified targets are emulator configs, so an ES-DE-only guard would be WRONG. The pgrep patterns must match the actual emulator process names on this Deck (e.g. Eden vs Yu…
- **Verify:** On-device (needs the MAD panel): open an emulator (or RetroArch), tap Restore in MAD Backends/Backup page → must show the 'Close these first: …' message and restore nothing. Close all emulators, tap Restore → restores as before. Headless: call mad_backup.do_re…

#### [6.1] · `/home/deck/Emulation/tools/launchers/lib/mad_backup.py:reset_local(): 155-159 (LOCAL.unlink())`

- **Change:** reset_local() hard-deletes controller-policy.local.toml. Downgraded to low because it is the user's explicitly-requested 'Reset overrides' action — so a proportionate fix is a one-time recovery copy before unlink, NOT a full _TMP+RECOVERY (overkill for a deliberate reset). Change `if LOCAL.is_file(): LOCAL.unlink()` to: `if LOCAL.is_file(): bak = LOCAL.with_suffix('.toml.bak-reset-' + time.strftime('%Y%m%d-%H%M%S')); shutil.copy2(LOCAL, bak); LOCAL.unlink()` (time and shutil already imported in mad_backup.py, lines 16-17). Then change the return message to mention recoverability: `return f'Cle…
- **Reuses:** fsops.move_to_tmp() (==steam-collection-sync.py:141 + deck-fetch-esde.sh:119-132 pattern) OR a plain .bak copy
- **Risk (trivial):** Only fires on the explicit 'Reset overrides' button/RPC. Adds a recovery copy — pure safety addition, no downside. Note reset_local is RPC-exposed twice (backup_cmds.py:206 and policy_cmds.py:230) but both call this one function, so one fix…
- **Verify:** Sandbox: with a sample local.toml, call reset_local() → confirm the file is gone from its live path but a recovery copy/_TMP dir exists with the content. On-device: tap 'Reset overrides' in MAD → footer message now names the recovery location; confirm the .bak…

#### [6.2] ○ `/home/deck/Emulation/tools/launchers/deck-backup.sh:296-300 (ls -t … | xargs rm -v retention prune)`

- **Change:** Low/borderline (the tool's own rolling backups, documented retention). This is SHELL not Python so it can't use fsops, but the project _TMP rule still applies. Replace the `ls -t "$DEST"/deck-config-*.tar.gz | tail -n +$((KEEP+1)) | xargs -r rm -v` with a move to a _TMP retire dir on the same filesystem as $DEST: e.g. `RETIRE="$DEST/_TMP-retired-backups"; mkdir -p "$RETIRE"; ls -t "$DEST"/deck-config-*.tar.gz | tail -n +$((KEEP+1)) | while read -r f; do mv -v "$f" "$RETIRE/"; done`. Optionally print a one-line note that retired archives are in $RETIRE (and that THIS dir is not auto-pruned, so…
- **Reuses:** mv-to-same-fs-_TMP idea (==move_to_tmp concept, but in bash); $DEST is the backup dest filesystem
- **Risk (low):** Borderline-info finding. Moving instead of rm means retired archives accumulate in _TMP-retired-backups and are never reclaimed automatically — that quietly defeats the point of retention (disk fills). So this 'fix' has a real downside; rec…
- **Verify:** Headless: create 7 dummy deck-config-*.tar.gz, set BACKUP_RETENTION_COUNT=5, run the prune block, confirm the 2 oldest moved to _TMP-retired-backups (not deleted) and 5 newest remain.

#### [C0.3] · `/home/deck/Emulation/tools/launchers/clean-manual-cruft.py:60 (exists-check at scan) and 69-70 (shutil.move recover loop)` · gate: **verify-lead-first**

- **Change:** NOT-YET-VERIFIED critic lead (confidence medium, no execution) — VERIFY FIRST that two same-stem multi-format PDFs can actually coexist for one game before implementing. If confirmed: the RECOVER branch (line 69-70) does a direct shutil.move that can overwrite a just-recovered Game.pdf when two leftover-ext PDFs (Game.bin.pdf, Game.cue.pdf) map to the same Game.pdf. The script ALREADY has a correct _TMP move branch (TMP=line 21, moves at line 70/75) — so reuse it. Fix: immediately before each `shutil.move(str(src), str(dst))` in the recover loop, re-check the live target: `if dst.exists(): shu…
- **Reuses:** the script's OWN existing _TMP move branch (clean-manual-cruft.py:70/75) — no fsops needed
- **Risk (trivial):** Narrow (needs two same-stem multi-format manuals) and both inputs were 'cruft' anyway, so real loss is one redundant copy. Default dry-run, --apply required. Verify the collision is even reachable before coding — if no system ever has Game.…
- **Verify:** Headless: construct a tmp manuals dir with Game.bin.pdf and Game.cue.pdf (same inner stem) and no Game.pdf, run with --apply → confirm ONE becomes Game.pdf and the OTHER lands in _TMP/<sys>/manuals-cruft/ (neither silently overwritten).

#### [W8.0] · `/home/deck/Emulation/tools/launchers/romhack-art-urls.json:1-182 (whole file — orphaned, no consumer)` · gate: **verify-lead-first**

- **Change:** NOT a data-safety bug — a config/intent gap (mis-batched by id). The file is well-formed but nothing reads it (verified: grep across launchers + ES-DE finds no consumer). This is a DECISION, not a mechanical fix — present 3 options to the user, don't pick silently: (a) if a hand-run external tool consumes it, add a one-line header comment in the JSON (or a README note) naming that tool; (b) if the fetch wiring was meant to live here, write a small fetcher mirroring openbor-fetch-media.py that downloads each {system,stem,url} into downloaded_media/<system>/covers/<stem>.<ext> (reuse openbor-fet…
- **Reuses:** openbor-fetch-media.py as the fetcher template (if option b); fsops.move_to_tmp (if option c)
- **Risk (trivial):** No runtime impact today (nothing reads it, can't crash). The 'fix' is clarifying intent. Do NOT delete/move without the user's decision — it may be a deliberate manual manifest.
- **Verify:** If (b): run the new fetcher in --dry-run, confirm it resolves each URL to a target media path under downloaded_media/<system>/covers/. If (a): grep confirms the comment/README points at a real tool. No on-device step.

#### [W8.1] · `/home/deck/Emulation/tools/launchers/data/gp-defaults/gp-dualshock4-positions.json (to be created):(missing file — every other GP_PROFILES key has one)` · gate: **on-device**

- **Change:** NOT a data-safety bug — a missing baked-layout JSON (cosmetic, graceful fallback already exists). The dualshock4 profile (GP_PROFILES in lib/madsrv/tester_cmds.py / mad_gamepad_tester.py:29-38, vid 0x054c pid 0x09cc) has no data/gp-defaults/gp-dualshock4-positions.json, so a DS4 tester opens with sprites in a generic grid until the user drag-aligns+Saves. Fix: create gp-dualshock4-positions.json with the 17 sprite stems from art/icons/dualshock4-tester/ (circle, square, triangle, x, l1, l2, r1, r2, dpad-up/down/left/right, lstick, rstick, ps, select, start) each mapped to a [x,y] normalized po…
- **Reuses:** data/gp-defaults/gp-dualsense-positions.json as the coordinate template (shared stems)
- **Risk (trivial):** Purely cosmetic; both Python (_baked_positions→{}) and C++ (posOf→false→grid) already handle the missing file gracefully. Risk is only that mirrored coordinates are slightly off until the user fine-tunes. No crash possible.
- **Verify:** Headless: validate the new JSON parses and its keys exactly equal the sprite stems in art/icons/dualshock4-tester/ (cross-check like the finding did for other profiles). On-device: connect a DS4, open the gamepad tester, confirm sprites land on the controller…

_Batch notes:_ SCOPE & HONESTY NOTES (the user is non-technical — read these): • NO C++ in this batch. W8.0/W8.1 are tagged (C) in the scorecard but are actually launchers-tree DATA files (a JSON and a missing JSON under data/gp-defaults/), not ES-DE C++ — so NOTHING here gates on a rebuild. The whole batch ships by editing Python/data files in /home/deck/Emulation/tools/launchers. • SEVERITY DRIFT — be honest with the user: wave-2 skeptics DOWNGRADED several of these from the headline severity. 10.6→low (a ti…

---

## Batch `C-update-recovery` — 7 fixes

_Approach:_ SteamOS-update recovery + system scripts. Every C0.x/C1.x lead in this batch was VERIFIED against the real code before designing the fix (all confirmed). Unifying principle across the deck-post-update.sh / samba-setup.sh fixes: each privileged step must (a) be invoked with the elevation it actually needs, and (b) self-balance the steamos-readonly state it touches (disable->work->re-enable), because deck-post-update.sh uses `set -uo pipefail` (NO set -e) so steps run independently and a step that…

#### [FIX-C0.0-C1.1] ○ `/home/deck/Emulation/tools/launchers/deck-post-update.sh:129` · gate: **verify-lead-first**

- **Change:** C0.0 and C1.1 are the SAME confirmed bug (filed twice): step 1/9 calls `bash "$T/samba-setup.sh"` with NO sudo, but samba-setup.sh:16 is `[[ $EUID -eq 0 ]] || { echo "Run with sudo..."; exit 1; }`, so when deck-post-update.sh runs as user `deck` the samba step instantly prints 'Run with sudo' and exits 1 — Samba (the #1 advertised recovery job) is NEVER restored, and esde-health-check keeps nagging forever because `command -v smbd` (check_missing line 49) stays false. FIX: change line 129 from `if [ -x "$T/samba-setup.sh" ]; then bash "$T/samba-setup.sh" || log " samba-setup.sh returned nonzer…
- **Reuses:** existing per-command sudo convention in deck-post-update.sh (lines 142-150, 200, 234); same shape as install.sh's sudo calls
- **Risk (low):** Behavior change: the samba step now actually runs (currently it is a silent no-op). The only side effect is samba being installed/started as intended. samba-setup.sh is documented idempotent and safe to re-run. sudo will prompt for password…
- **Verify:** On-device, run as user deck: `bash ~/Emulation/tools/launchers/deck-post-update.sh` and watch step 1/9; afterwards confirm `command -v smbd` resolves and `systemctl is-active smb` (or smbd) is active. Headless static check now: `grep -n samba-setup deck-post-u…

#### [FIX-C1.0] ◆ `/home/deck/Emulation/tools/launchers/deck-post-update.sh:199-206 (step 7/9 pacman block)` · gate: **verify-lead-first**

- **Change:** CONFIRMED: `grep -n steamos-readonly deck-post-update.sh` returns ZERO matches — the script never disables the immutable root, yet step 7 (line 200) runs `sudo pacman -S --needed --noconfirm python-evdev tk` directly. Right after a SteamOS update the A/B root is read-only, AND step 2 (sinden-reinstall-deps.sh) explicitly re-enables readonly at its line 80, so by the time step 7 runs the root is read-only and pacman fails to write /usr — MAD GUI deps (tkinter/evdev) are never restored, and the user is misdirected by the 'check pacman keyring' message (line 203). FIX: wrap the step-7 install exa…
- **Reuses:** install.sh:146-151 (verbatim disable / keyring-init / pacman / enable pattern); sinden-reinstall-deps.sh:20-21,79-80 for the disable/enable bracketing
- **Risk (moderate):** Touches the immutable system root (steamos-readonly), which CLAUDE.md rule 6 says needs explicit authorization and is wiped by updates. The change itself is the documented-correct way to pacman on SteamOS and is already used in install.sh,…
- **Verify:** Cannot verify headlessly without running pacman on the root (not authorized here). On-device after a real SteamOS update (or after manually wiping the deps): run deck-post-update.sh, then `python3 -c 'import tkinter, evdev'` must succeed and step 7 must log 'r…

#### [FIX-C0.1] ○ `/home/deck/Emulation/tools/samba-setup.sh:22 (disable) / end-of-file (missing enable)` · gate: **verify-lead-first**

- **Change:** CONFIRMED: line 22 `steamos-readonly disable || true` has NO matching `steamos-readonly enable` anywhere (grep for 'enable' matches only the systemctl line 46). After running samba-setup.sh directly (the documented `sudo bash ~/Emulation/tools/samba-setup.sh` invocation) the immutable root is left mounted read-write until the next reboot/update. FIX: append a re-enable at the very end of the script (after the closing IP/echo block, line 61). Guard it so it only re-enables if THIS script disabled it, matching sinden-reinstall-deps.sh:79-80 / install.sh:151. Concretely: at line 22 capture state…
- **Reuses:** sinden-reinstall-deps.sh:79-80 and install.sh:151 (the `steamos-readonly enable || true` re-lock pattern)
- **Risk (low):** Re-enabling readonly at script end is the correct end-state and matches both sibling scripts. The `|| true` keeps it non-fatal on a non-SteamOS host. The only behavior change is that the root is correctly re-locked instead of left writable.…
- **Verify:** On-device: `sudo bash ~/Emulation/tools/samba-setup.sh`, then `steamos-readonly status` must report enabled/read-only. Headless static check: `grep -c 'steamos-readonly enable' samba-setup.sh` returns >=1.

#### [FIX-6.0] ○ `/home/deck/Emulation/tools/launchers/lib/esde_paths.py:20 (_LEGACY) and 33-36 (fallback loop)`

- **Change:** CONFIRMED (latent): the live wrapper (deck-post-update.sh rewrite_wrapper) runs ES-DE from a PERMANENTLY EXTRACTED AppDir at `~/Applications/ES-DE-MAD.AppDir`, creating NO /tmp FUSE mount. esde_resources() resolution order is: $ESDE_RESOURCES env, `/tmp/.mount_ES-DE*` (never present with the wrapper), `~/AppDir` (legacy manual extraction, verified mtime Apr 4 — STALE), `/usr/share`. So it resolves to the stale legacy `~/AppDir`. Verified on disk: real running resources exist at `/home/deck/Applications/ES-DE-MAD.AppDir/usr/share/es-de/resources/systems` (mtime Jun 13) and the legacy `/home/dec…
- **Reuses:** the file's own existing `(cand / "systems").is_dir()` probe pattern (line 31/34) — just adds two more candidates to the same loop; no new helper
- **Risk (low):** Pure resolution-order addition. The new candidates are probed by the SAME `.is_dir()` guard, so if neither AppDir exists nothing changes (still falls through to legacy/usr). Could in theory change which es_systems.xml es_systems_wrap.py / e…
- **Verify:** Headless: `cd /home/deck/Emulation/tools/launchers && python3 -c 'from lib.esde_paths import esde_resources, bundled_es_systems; print(esde_resources()); print(bundled_es_systems())'` must now print the ES-DE-MAD.AppDir path (not ~/AppDir) and the printed es_s…

#### [FIX-C0.2] · `/home/deck/Emulation/tools/sinden-shim/etc-backup/99-sinden-lightgun.rules (master) + /home/deck/Emulation/tools/launchers/sinden-mpx-setup.sh:69:rules: after line 23 (P2 block); script: 69` · gate: **on-device**

- **Change:** CONFIRMED: sinden-mpx-setup.sh:69 resolves the P2 keyboard node via a hardcoded `readlink -e /dev/input/event27` — a kernel event number that is NOT stable across reboot/replug. The udev rules file (verified: master == live /etc copy, so editing the master and reinstalling suffices) creates SYMLINKs only for js* and ENV{ID_INPUT_MOUSE} interfaces (lines 18-23), NOT for the keyboard interface. FIX (two coordinated edits): (1) In the master rules file, in the Player-2 block right after line 23, add a keyboard symlink mirroring the mouse rule: `SUBSYSTEM=="input", KERNEL=="event*", ATTRS{idVendor…
- **Reuses:** the existing mouse udev symlink rules (lines 19/23) and the script's own mouse-node resolution at line 24 (readlink -f of a stable udev symlink)
- **Risk (trivial):** Adding a new SYMLINK line only creates an additional stable name; it does not alter existing mouse/js/tty/camera symlinks. The script edit swaps an unstable literal for a stable symlink and keeps the same graceful-skip fallback. Worst case…
- **Verify:** On-device: install the updated rule (deck-post-update.sh step 3, or `sudo cp` + `sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=input`), replug the P2 gun, then `readlink -f /dev/input/sinden-gun-p2-kbd` should resolve to the P2 keyboa…

#### [FIX-C1.4] ○ `/home/deck/Emulation/tools/launchers/deck-post-update.sh:98-111 (rewrite_wrapper heredoc re-extract block)` · gate: **on-device**

- **Change:** CONFIRMED: inside the wrapper heredoc, the re-extract block does `rm -rf "$APPDIR" "$TMP"` then extracts into $TMP then `mv "$SRC" "$APPDIR"`, writing the stamp only on a successful mv; on failure (disk full on /home, power-loss mid-extract) it falls through to `[ -x "$APPDIR/AppRun" ] || exec "$IMG" "$@"` (line 110) — the FUSE-mount path that the whole wrapper exists to AVOID (it re-introduces the request_wait_answer deadlock for native Steam games launched from ES-DE), and it does so SILENTLY. FIX (two cheap guards, both inside the heredoc so they ship in the written wrapper): (1) Before `rm…
- **Reuses:** no existing free-space helper in the repo (grep confirms none) — uses plain df/stat, matching the rest of the script's shell idiom; reuses the script's own `[ -x "$APPDIR/AppRun" ]` validity probe (al…
- **Risk (low):** All edits are inside the WRAPPER HEREDOC, so they take effect only after rewrite_wrapper runs (deck-post-update.sh --wrapper, or step 6, or install.sh). The disk guard prefers keeping a working AppDir over wiping it — strictly safer. The va…
- **Verify:** Headless: after editing, run `bash deck-post-update.sh --wrapper` then `bash -n ~/Applications/ES-DE.AppImage` (syntax check the generated wrapper) and `grep -n 'low disk\|extraction failed' ~/Applications/ES-DE.AppImage` to confirm the guards were written. Si…

#### [FIX-C1.5] ○ `/home/deck/Emulation/tools/launchers/esde-health-check.sh:30-42 (BUILD_ID gate + marker) and deck-post-update.sh end-of-run (marker write)`

- **Change:** CONFIRMED: the marker $L/.last-os-build is read at line 32 and written ONLY at line 37 (when check_missing returns nothing). Two edge cases: (1) first-run / fresh Deck — .last-os-build absent, `cat` returns '', `$cur` != '' so the FULL check runs and, if anything is missing, the user sees a 'A SteamOS update reset the system and wiped...' dialog (_body, line 19) that MISDESCRIBES a brand-new setup as an update casualty; (2) after a successful manual `deck-post-update.sh` restore, the marker is NOT written by deck-post-update.sh itself, so the nag persists until a later all-present ES-DE launch…
- **Reuses:** deck-post-update.sh's own check_missing() (lines 32-52) as the success gate for writing the marker; the BUILD_ID extraction idiom is copied verbatim from esde-health-check.sh:30
- **Risk (low):** Cosmetic/UX fix on a best-effort path that never blocks launch, so blast radius is tiny. Risk is only in message wording — verify the first-run branch points the user at the correct first-time setup command (install.sh), not deck-post-updat…
- **Verify:** Headless: simulate first-run with `rm -f` a COPY (move to _TMP, never delete the real one) of .last-os-build and run `bash esde-health-check.sh` under a stubbed _warn that echoes its args — confirm the first-run title fires, not the update title. Use the built…

_Batch notes:_ VERIFICATION RESULT: all eight critic leads in this batch were checked against the real files and ALL are CONFIRMED — none refuted. C0.0 and C1.1 are the SAME bug filed twice (samba never restored: no-sudo caller + EUID==0 hard-exit) — I merged them into one fix (FIX-C0.0-C1.1) so an implementer does not write two conflicting patches. Honesty caveats the user should know: (1) 6.0 is LATENT today — the stale ~/AppDir (Apr 4) and the real running ~/Applications/ES-DE-MAD.AppDir (Jun 13) currently…

---

## Batch `D-daemon-gui-robustness` — 16 fixes

_Approach:_ Two cross-cutting patterns drive this batch. (1) CONCURRENCY ROOT: the mad-backend daemon is NOT single-threaded. rpc.py:24 runs slow=True methods on a 4-worker ThreadPoolExecutor, AND device_cmds.py:128 _WatchStream runs enumerate/scan on its own Stream thread every 2s. So any module-global mutated by a slow method or a Stream can be touched by >=2 threads at once. The seam conclusion (REVIEW-FINDINGS.md line 17) declared 0.2/10.11 DEAD on a "single stdin thread" premise — that premise is WRONG…

#### [2.1] ○ `/home/deck/Emulation/tools/launchers/lib/madsrv/systems_cmds.py:242`

- **Change:** PRIMARY FIX (preferred, matches the seam recommendation): change the decorator at line 242 from `@method("systems.set_ra_option", slow=True)` to `@method("systems.set_ra_option")` so it runs inline on the single stdin thread like the other tiny config writers (model2.set @ model2_cmds.py:198 and profiles.apply_slot @ backends_cmds.py:199 are already fast). The body is a cheap read-modify-write plus one quick pgrep (_retroarch_running) — no SDL init, no device probe, no long file sweep — so it does not belong on the slow pool. This makes all config writers serialize on one thread, eliminating B…
- **Reuses:** model2.set / profiles.apply_slot fast-method pattern (already inline); or _SDL_LOCK pattern (devices.py:394) for the lock alternative
- **Risk (low):** PRIMARY: moving set_ra_option off the slow pool means it now runs on the stdin thread; the write touches all of a system's core cfgs (a few small files) — bounded, fast, no blocking syscall, so it will not stall the next request meaningfull…
- **Verify:** Headless: python3 -c reproduction copying retroarch_cfg.py, spawn 2 threads calling set_system_option on the SAME system with two distinct keys; before fix you can hit FileNotFoundError on tmp.replace and a dropped key; after the fast-method fix the daemon dis…

#### [0.2/10.11] ○ `/home/deck/Emulation/tools/launchers/lib/devices.py:134,156-218`

- **Change:** Add a module-level lock next to the cache declaration: after line 134 (`_ENUM_CACHE: dict = {}`) add `_ENUM_CACHE_LOCK = threading.Lock()` (threading is already importable — _SDL_LOCK at line 394 uses it; add `import threading` at top if not already present, confirm first). Then hold the lock around the cache reads/writes/prune inside enumerate_devices: simplest safe approach is to wrap the whole per-node loop body's cache access and the final prune. Concretely: take `with _ENUM_CACHE_LOCK:` around the block that does `hit = _ENUM_CACHE.get(path)` (163) through `_ENUM_CACHE[path] = (sig, f)` (…
- **Reuses:** _SDL_LOCK pattern in the same file (devices.py:394, used at :422); _WII_LOCK (device_cmds.py:105)
- **Risk (low):** Adds a lock around a function that can be called from multiple daemon threads; holding it across the evdev open/close serializes concurrent full walks (so two simultaneous walks run one-after-another instead of interleaving). Worst case is…
- **Verify:** Headless: `python3 -c "from lib import devices; import threading; [t.start() for t in [threading.Thread(target=devices.enumerate_devices) for _ in range(6)]]"` and confirm no exception and stable cache; `python3 -m py_compile lib/devices.py`. The race is proba…

#### [0.1/12.4] ○ `/home/deck/Emulation/tools/launchers/lib/routing.py:46-57`

- **Change:** Guard the BASE policy parse the same way the LOCAL parse (lines 51-56) is already guarded. Replace lines 48-50 (`if POLICY_FILE.is_file(): with POLICY_FILE.open('rb') as f: base = tomllib.load(f)`) with a try/except: `if POLICY_FILE.is_file():\n try:\n with POLICY_FILE.open('rb') as f:\n base = tomllib.load(f)\n except (tomllib.TOMLDecodeError, OSError) as exc:\n print(f'controller-router: controller-policy.toml parse error ({exc}); routing disabled (RetroArch defaults).', file=sys.stderr)`. On failure `base` keeps its initialized `{"systems": {}}` value (line 47), so resolve_system returns No…
- **Reuses:** the LOCAL-file try/except at routing.py:51-56 (same except tuple, same fail-soft intent)
- **Risk (low):** Behavior change ONLY on the error path: today a base-policy typo raises out of load_policy and aborts the launch; after the fix it logs and degrades to no-routing. This is strictly safer. No change on the happy path. A genuinely intended ex…
- **Verify:** Headless: copy routing.py + a deliberately corrupt controller-policy.toml to /tmp, `python3 -c "import routing; print(routing.load_policy())"` — before fix raises TOMLDecodeError, after fix prints the error to stderr and returns {'systems': {}}. `python3 -m py…

#### [C1.2] ○ `/home/deck/Emulation/tools/launchers/lib/policy.py:21-22`

- **Change:** Same base-parse guard as 0.1/12.4 but on the MAD-panel surface (this is a DISTINCT file from routing.py — load_merged is what every MAD page calls). Replace lines 21-22 (`if POLICY.is_file(): base = tomllib.load(POLICY.open('rb'))`) with a try/except + context manager (also fixes the dormant file-handle leak noted in 1.3): `if POLICY.is_file():\n try:\n with POLICY.open('rb') as f:\n base = tomllib.load(f)\n except (tomllib.TOMLDecodeError, OSError):\n pass # keep the safe default; a corrupt base must not brick the panel`. On failure `base` keeps its init value `{"systems": {}, "backends": {}}…
- **Reuses:** the safe-default dict already initialized at policy.py:20; the with-open pattern recommended in finding 1.3
- **Risk (low):** Error-path-only change. Today a corrupt base policy raises TOMLDecodeError out of load_merged and (per C1.2) breaks every MAD panel page; after the fix the panel still loads with empty systems/backends. Note: load_merged and routing.load_po…
- **Verify:** Headless: corrupt-base reproduction as in 0.1, `python3 -c "from lib import policy; print(policy.load_merged())"` returns the safe default instead of raising. `python3 -m py_compile lib/policy.py`.

#### [0.0] ◆ `/home/deck/Emulation/tools/launchers/quit-combo-watcher.py:103-121`

- **Change:** The watcher self-SIGTERMs because line 120 runs `subprocess.run(quit_cmd, shell=True)` SYNCHRONOUSLY in-process, and quit_cmd's own `pkill -TERM -f 'Eden|Yuzu|...'` matches the watcher's own argv (the pattern is on the watcher's /proc cmdline via the game-start hook). Fix: run the quit_cmd in its own detached session so the watcher is out of the pkill blast radius, mirroring how the KILL backstop already protects itself (lines 113-116 already use `setsid` + `trap '' TERM`). Change line 120 to: `subprocess.Popen(["setsid", "bash", "-c", quit_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DE…
- **Reuses:** the existing setsid + DEVNULL pattern already in _quit at lines 113-116
- **Risk (moderate):** Changing subprocess.run (blocking) to Popen+setsid (detached, non-blocking) means _quit returns immediately and the watcher exits without waiting for the quit_cmd to finish. That is the intended behavior (the quit_cmd handles its own escala…
- **Verify:** Headless simulation (the wave2 skeptic already did this in /tmp/_madrev): fake a SIGTERM-ignoring emulator, run the watcher with a quit_cmd matching its own argv; BEFORE fix the watcher dies at line 120 and the emulator survives until the +6s global backstop;…

#### [12.0] ○ `/home/deck/Emulation/tools/launchers/lib/cemu_cfg.py:131`

- **Change:** Line 131 `text = _DISPLAY_RE.sub(rf"\g<1>{dev.name}\g<3>", text, count=1)` interpolates the raw evdev device name into a re.sub REPLACEMENT template, where backslashes and `\g<n>` sequences are special — a name with `\`, `&`, `<`, `>` corrupts the controllerN.xml or raises. Fix two things: (1) treat the value LITERALLY via a function replacement so backslashes/group-refs are not interpreted, and (2) XML-escape the name since it lands inside an XML element. Add at top of file (after `import re`, line 32): `from xml.sax.saxutils import escape as _xml_escape`. Then change line 131 to: `text = _DI…
- **Reuses:** xml.sax.saxutils.escape (stdlib); lambda-replacement is the standard fix for re.sub backreference injection (also recommended in finding 5.1)
- **Risk (low):** Behavior change only for device names containing regex/XML-special chars; common pad names (alphanumerics + spaces) produce identical output to today (escape() is a no-op on them, and the lambda emits the same literal). No change to the UUI…
- **Verify:** Headless: `python3 -c "import re; from xml.sax.saxutils import escape; R=re.compile(r'(<display_name>)(.*?)(</display_name>)'); t='<display_name>OLD</display_name>'; print(R.sub(lambda m:m.group(1)+escape(r'x&<\\1>')+m.group(3),t,count=1))"` — must emit `<disp…

#### [12.5] · `/home/deck/Emulation/tools/launchers/openbor-fetch-media.py:72`

- **Change:** Line 72 `dirn=re.search(r'^DIR=(.*)$', man, re.M).group(1)` calls .group(1) directly on the search result; a manifest with no DIR= line returns None and raises AttributeError, crashing the whole media fetch. Guard it: replace line 72 with `dm = re.search(r'^DIR=(.*)$', man, re.M); dirn = dm.group(1) if dm else ''`. The two following loops (73-76) already test `if dirn and dirn in path` (73) so an empty dirn naturally skips the path-match loop; the name-match loop at 75-76 (`if name==dirn or name==stem`) still works via the `name==stem` fallback. So with dirn='' the function falls through to `r…
- **Reuses:** the `if m:` guarded-search pattern already used at openbor-fetch-media.py:68-71 for the PREFIX line
- **Risk (trivial):** Pure defensive guard; on a well-formed manifest (DIR= present) behavior is identical. Only effect is that a DIR-less manifest now skips that one game (returns None) rather than aborting the entire run. Manually-run utility, no daemon impact…
- **Verify:** Headless: `python3 -c "import re; man='PREFIX=foo'; dm=re.search(r'^DIR=(.*)$',man,re.M); print(dm.group(1) if dm else '<empty-ok>')"` prints `<empty-ok>` instead of raising. `python3 -m py_compile openbor-fetch-media.py`.

#### [4.0/N0.1] ◆ `/home/deck/Emulation/tools/launchers/router-config-gui.py:1870-1875` · gate: **verify-lead-first**

- **Change:** quit() (1870-1875) calls only _gp_cleanup() + _cam_restore_driver() — it NEVER calls _cam_kill_ffmpeg() (so an orphaned ffmpeg keeps the V4L2 camera open, Sinden aim dead = 4.0) and never kills self._dp_proc (so an in-flight Daphne hypseus_capture SDL subprocess is orphaned = N0.1). The cleanest reuse-based fix: call the existing _clear() at the top of quit(), because _clear() (lines 1340-1389) ALREADY performs full deterministic page teardown — it kills _dp_proc (1350-1356), calls _cam_kill_ffmpeg() (1374-1375), _cam_restore_driver() (1376), _gp_cleanup() (1346), releases tester grabs, and ca…
- **Reuses:** the existing _clear() teardown (router-config-gui.py:1340-1389) and _cam_kill_ffmpeg (2398-2409); _gp_cleanup (mad_gamepad_tester.py:221)
- **Risk (moderate):** Calling _clear() at quit runs more teardown than today, including widget destruction of body children (1384-1388) and _footer_restore (1380) — harmless immediately before root.destroy(), but verify _clear() does not re-enter the mainloop or…
- **Verify:** On-device (no display here): open MAD camera-tuning page, press Preview (ffmpeg starts), then quit MAD; before fix `pgrep -af ffmpeg` shows a stray ffmpeg and the Sinden stays dead; after fix no ffmpeg remains and the gun works. For N0.1: start a Daphne X-Arca…

#### [N1.0] ○ `/home/deck/Emulation/tools/launchers/router-config-gui.py:2478-2488` · gate: **verify-lead-first**

- **Change:** _cam_tick (2478-2488) guards only `if not self._cam_proc:` (truthiness of the retained Popen object) and never calls .poll(), so if ffmpeg dies on its own (camera busy/unplugged, driver still holding it) the page shows a frozen frame while still claiming 'live', and the dead child becomes a zombie until the user leaves the page. Fix: in _cam_tick, after `self._cam_after = None` and the existing `if not self._cam_proc: return`, detect death before re-arming. Insert: `if self._cam_proc.poll() is not None:\n self._cam_kill_ffmpeg()\n try:\n self._cam_lbl.config(image='', text='( camera busy / pre…
- **Reuses:** the existing _cam_kill_ffmpeg (router-config-gui.py:2398-2409); the same poll()-before-rearm idiom recommended in the finding
- **Risk (low):** Behavior change only when ffmpeg has actually died: today the loop keeps re-arming every 66ms against a frozen frame; after the fix it stops, reaps the zombie, and tells the user to retry. Healthy-feed path is unchanged (poll() returns None…
- **Verify:** On-device: start a Preview while the Sinden driver still holds the camera (or unplug the cam mid-preview); before fix the frame freezes but status says 'live' and `pgrep -af ffmpeg` shows a defunct/zombie; after fix the status flips to 'Preview ended (camera b…

#### [N0.0] ◆ `/home/deck/Emulation/tools/launchers/router-config-gui.py:279-303, 599-700` · gate: **verify-lead-first**

- **Change:** The Sinden mouse interface is admitted to the nav-device set (_scan sets sinden=True at 279-283 and populates _dir_thresh because its ABS_X/ABS_Y report max>min), so aiming the gun drives stray menu focus via _handle's EV_ABS branch (683-700) — but the `_mad_sinden` flag (set at line 283) is NEVER read (confirmed dead: grep shows only 277/279/283). Preferred fix (a): HONOR the flag in the input path. In _handle (or _poll's dispatch around 729), early-return for sinden devices before the EV_ABS directional branch: `if getattr(d, '_mad_sinden', False): return` placed before the EV_ABS handling a…
- **Reuses:** the existing getattr(dev,'_mad_sinden',False) flag already set at line 283 (just never read); mirrors how other non-nav devices are excluded
- **Risk (moderate):** Behavior change: a powered-on Sinden gun will stop moving the MAD focus ring. That is the intended fix, but VERIFY on-device that no current MAD workflow relies on the gun for nav (the camera/calibration pages use the gun for aiming preview…
- **Verify:** On-device only (needs the live Sinden gun + display): power on a Sinden, open MAD, aim the gun around; before fix the focus ring jumps; after fix it does not. Confirm camera-tuning preview still works (gun aim still drives the preview, which is a different cod…

#### [N3.1] ○ `/home/deck/Emulation/tools/launchers/supermodel-sinden.sh:1,11-15`

- **Change:** The script is `#!/bin/sh` and runs `{ ...; supermodel-sinden-smart.py "$@" 2>&1; } 2>&1 | tee "$LOG"` with no pipefail/PIPESTATUS, so the script's exit status = tee's status = 0 always — a failed launch (smart launcher error, or os.execvpe failing to exec supermodel) is invisible to ES-DE. Fix mirroring the sibling hypseus-pin.sh (which uses bash + `exit "${PIPESTATUS[0]}"` at line 84): (1) change the shebang line 1 to `#!/usr/bin/env bash`; (2) after the closing `} 2>&1 | tee "$LOG"` (line 15) add a new line `exit "${PIPESTATUS[0]}"` so the inner command's status (the smart launcher, which os…
- **Reuses:** the bash-shebang + `exit "${PIPESTATUS[0]}"` pattern from hypseus-pin.sh:1,84
- **Risk (low):** Switching sh->bash is safe on SteamOS (bash is present; the script uses no sh-only constructs). The only behavior change is that ES-DE now sees the real exit code instead of always 0 — i.e. a failed Supermodel launch will now correctly surf…
- **Verify:** Headless: copy the script to /tmp, replace the .py call with `bash -c 'exit 42'`, run it, and check `echo $?` is 42 (before fix it is 0). `bash -n supermodel-sinden.sh` to syntax-check.

#### [N6.0] ◆ `/home/deck/Emulation/tools/launchers/lib/madsrv/tester_cmds.py:826-839, 716-727` · gate: **verify-lead-first**

- **Change:** The headless X-Arcade escape (line 827 `both = self.spots.get('mouse1') and self.spots.get('mouse2')`) derives the P1+P2-Start gesture from the CALIBRATABLE spot dict (spots['mouse1']/['mouse2'] are only set when BTN_START maps to those spots via self.cal at line 722/673-674). If the user re-calibrates Start to a different spot, the escape breaks or fires on the wrong buttons; and a mid-calibration Start press can end the test, discarding unsaved bindings. Fix mirroring the Tk version (_xa_quit_check, mad_xarcade_tester.py:728-730 which counts raw `f"{od['tag']}:k{e.BTN_START}"` keys in self._…
- **Reuses:** the raw-BTN_START-tracking pattern from the Tk _xa_quit_check (mad_xarcade_tester.py:728-730); _cal_armed gating already exists (set/cleared at tester_cmds.py:691, 1076-1079)
- **Risk (moderate):** Behavior change to the escape gesture: it now keys off the physical Start buttons, not the (possibly re-mapped) spots, and is suppressed during calibration. This is the intended fix and matches the Tk version, but it CANNOT be verified head…
- **Verify:** On-device with the X-Arcade: re-calibrate P1/P2 Start to other spots, then hold both physical Start buttons 3s — escape must still fire (before fix it would not). Also arm a calibration and press Start+Start — must NOT end the test. Headless: `python3 -m py_co…

#### [C1.3] ○ `/home/deck/Emulation/tools/launchers/steam-fetch-media.py:43,188 (and openbor-fetch-media.py:30-31,90,131-132,167)`

- **Change:** Both media-fetch scripts mkdir -p into hardcoded /run/media/deck/1tbDeck/... paths with no mount check; when the SD is unmounted they create the tree on the root tmpfs/partition, silently filling root and shadowing the real card on remount. Fix: add a one-time mount/existence assertion at the START of main() before the first mkdir, aborting with a clear message. For steam-fetch-media.py, in main() (line 183) before the mkdir loop (188) add: `import os.path as _osp\nif not _osp.ismount('/run/media/deck/1tbDeck'):\n print('SD card /run/media/deck/1tbDeck is not mounted — aborting (refusing to wr…
- **Reuses:** os.path.ismount (stdlib); the early-abort+message pattern from steam-collection-sync.py:80-86
- **Risk (low):** Adds a guard that only triggers when the SD is genuinely unmounted (rare on a normally-running Deck where the card is the documented constant). On the happy path (card mounted) ismount returns True and nothing changes. CONTESTED-adjacent: t…
- **Verify:** Headless: `python3 -c "import os.path; print(os.path.ismount('/run/media/deck/1tbDeck'))"` (True now, since the card is mounted); to test the abort, point the guard at a known-unmounted path temporarily and confirm main() returns with the message. `python3 -m…

#### [10.0] ○ `/home/deck/Emulation/tools/launchers/mad-backend.py:159-162 (+ lib/madsrv/rpc.py:24,147-156)` · gate: **verify-lead-first**

- **Change:** CONTESTED — the refuter rates this refuted/uncertain: the C++ supervisor MadBackend.cpp shutdownChild() hard-caps teardown at ~2s then SIGKILLs, and the only pool subprocess (daphne.bind/hypseus_capture) self-times-out at ~10s and holds no evdev grab — so this is NOT a hang and NOT a grab-leak, only a minor up-to-~2s slower exit when a slow method is in flight at teardown (the ThreadPoolExecutor atexit _python_exit join is non-daemon). Design IF the user wants the polish: in rpc.py add a teardown helper `def shutdown_pool():\n _POOL.shutdown(wait=False, cancel_futures=True)` and call it from m…
- **Reuses:** stop_all_streams teardown call site (mad-backend.py:159-162); ThreadPoolExecutor.shutdown stdlib API
- **Risk (low):** cancel_futures=True drops not-yet-started tasks and wait=False does not block on running ones — so a slow method running at teardown is abandoned (its response never sent), which is fine because the panel is already gone. RISK: if any slow…
- **Verify:** Headless: reproduce the wave2 measurement — a script submitting a 3s pool task then sys.exit(0) takes ~3s without shutdown and ~0s with shutdown(cancel_futures=True). Confirm mad-backend still exits 0 on EOF. `python3 -m py_compile lib/madsrv/rpc.py mad-backen…

#### [10.3] ◆ `/home/deck/Emulation/tools/launchers/controller-router.py:96-111 (callers 201-203, 210-212)` · gate: **verify-lead-first**

- **Change:** CONTESTED (downgrade med->low per refuter). The warning dialog's _show_warning_blocking returns the child exit code and callers treat ANY non-zero as Cancel -> abort the launch. The refuter showed: (i) a headless/broken DISPLAY does NOT hit the except branch — subprocess.run returns returncode==1 (so the abort comes via the normal return path, not the except), and (ii) on a real Gamescope launch the dialog renders fine, so reachability is narrow. Design IF pursued: distinguish 'dialog failed to display' from 'user pressed Cancel'. Make lib/warning_dialog emit a DISTINCT exit code on a Tk/displ…
- **Reuses:** the existing exit-code return contract in _show_warning_blocking; the env.setdefault DISPLAY guard at controller-router.py:101
- **Risk (moderate):** Changing fail-closed to fail-open for the warning dialog means that if the warning UI is genuinely broken, the game launches WITHOUT the user seeing the (e.g. 'no lightgun connected') warning. That is a deliberate trade-off and could mask a…
- **Verify:** On-device: force a warning condition (e.g. require_sinden with no gun) on a real Gamescope launch and observe whether the dialog shows; if it shows fine, this fix is unnecessary. If it genuinely fails to show and aborts the launch, confirm the new exit-2 path…

#### [8.3] ○ `/home/deck/Emulation/tools/launchers/install-bezels.sh:74-82 (and install-bezels-all.sh:95-103)`

- **Change:** CONTESTED (downgraded med->low per refuter — no current victim file can collide with a Bezel-Project .cfg name, since those are ROM-named per-GAME cfgs and the irreplaceable user data is per-CORE/per-CONTENT cfgs which $game never produces). The `cat > "$core_dir/$game.cfg" <<EOF` truncates in place with no backup, violating the house 'never destroy user data' rule. Design IF pursued: before the `cat >`, if the target exists AND is not already a prior bezel/wire-bezels output, move it to a recoverable _TMP (per project rule #5 / the move_to_tmp helper) rather than clobbering. Concretely, befor…
- **Reuses:** the move_to_tmp() spec helper / the project _TMP+RECOVERY.txt convention (CLAUDE.md rule #5)
- **Risk (low):** The guard only moves a file that exists AND lacks a bezel/wire-bezels marker (i.e. a genuine hand-made override), so re-running a bezel install over its own prior output is unaffected (the script's documented idempotency is preserved). RISK…
- **Verify:** Headless: seed a fake $core_dir/$game.cfg with hand-tuned content (no bezel marker), run the guarded snippet, confirm the file is MOVED to _TMP (not truncated) and a RECOVERY.txt is written; seed a bezel-marked cfg and confirm it is overwritten in place (idemp…

_Batch notes:_ HONESTY / CONTEST FLAGS: (a) The seam conclusion is WRONG about 0.2/10.11 being dead. enumerate_devices() IS reached concurrently in the daemon: _WatchStream.run (device_cmds.py:141) calls _scan->enumerate_devices on its own thread while a slow method (devices.sdl, preview.route, gamepads.list, tester.start) calls it on the 4-worker pool. The GIL makes a hard crash unlikely but the unlocked dict read+populate+prune (devices.py:163/193/216-217) can still mis-cache/evict under concurrency. The loc…

---

## Risk matrix

HIGH RISK (behavior-altering; test carefully): - W7.1 (main.cpp:549, order 6): changing setBlockInput from per-frame to transition-only could re-introduce input-leaking-behind-the-Steam-overlay if the focus-LOSS path stops blocking. TEST: on-device, (a) launch-screen countdown no longer dismissed by a stray press, (b) bringing up the Steam overlay still blocks nav, (c) returning still unblocks. Verify-first on the symptom before editing. - W4.0/CC.2 (GuiMadPageSplash.cpp, order 5): weak_ptr conversion must lock() and bail in BOTH the success and failure lambda branches; missing one still derefs a freed switch. TEST: build + on-device (ideally ASAN) — toggle a splash-pool image then cycle MODE/FIT before the response lands; confirm no crash. - FIX-C1.0 (deck-post-update.sh:200, order 7): wrong steamos-readonly balance silently breaks a LATER step (set -uo pipefail, no set -e). TEST: read the full 9-step readonly state machine after editing; confirm disable/enable pairs nest correctly and don't leave root writable or break step 8/9. - 0.0 (quit-combo-watcher.py:120, order 8): detaching quit_cmd could regress the normal clean-quit path. TEST: confirm the watcher exits and the emulator dies on a clean quit; confirm Eden's fast 2s escalation now fires (was taking the 6s backstop). - 4.0/N0.1 + N2.0 (router-config-gui.py quit/SIGTERM, order 9): calling _clear() at quit-top or adding signal handlers could double-teardown or interact with Tk's mainloop teardown. TEST: on-device — quit from the camera page (no orphaned ffmpeg, Sinden alive), and ES-DE-kills-MAD (cleanup runs). - N6.0/3.3 (tester_cmds.py:826-839, order 9): escape-combo rework needs the physical X-Arcade; getting the raw-BTN_START tracking wrong could make the only exit from an input-locked test unreachable. TEST: on-cabinet, after re-calibrating Start. - 5.0/10.7 cemu XML escape (cemu_cfg.py:130-131, order 3): the injection fix (function replacement + xml_escape) must produce byte-identical output for normal device names or it changes every Wii U controller profile. TEST: headless — feed a normal name and a name with &/</>/backslash; assert valid XML out. MODERATE RISK: - Atomic-write conversions (order 3…

## Verification plan

HEADLESS (can do on this Deck now, no display): - Helpers (order 1): run lib/fsutil.py and lib/proc_guard.py __main__ self-tests; assert esde_running() returns a bool, atomic_write_text round-trips, recoverable_delete creates the _TMP dir on the correct filesystem and writes RECOVERY.txt. - Gamelist guards (order 2): `python3 -c 'import …'` import-check each edited script; with ES-DE not running, dry-run each and confirm it proceeds; (cannot safely fake ES-DE running, but can unit-test the guard function in isolation). - Atomic writes (order 3): for each cfg writer, write with a normal device/system and diff the output bytes against a pre-change capture (must be identical); confirm the tmp file is created in the target's parent dir. cemu XML escape: feed names with &,<,>,backslash and assert ET.fromstring() parses the result. - C++ build (order 5/6): the ONLY headless gate — rebuild from ~/esde-build using the project's existing build flow and confirm it compiles + links with no missing-symbol/ODR errors (CC.6 says the CMake wiring is complete; this proves it). Cannot run the GUI here. - Update-recovery (order 7): `bash -n` syntax-check the edited scripts; statically trace the steamos-readonly disable/enable pairs across all 9 deck-post-update.sh steps and confirm they balance; confirm samba-setup.sh now has a matching enable. - Daemon (order 8): unit-test enumerate_devices() under a thread pool hammering it concurrently with a simulated _WatchStream _scan loop and assert no KeyError/RuntimeError on the cache; `mad-backend.py --selfcheck` (per deck-post-update.sh:209) still passes; confirm set_ra_option is registered fast (not in the slow set). - Daemon/GUI robustness (order 9): import-check; feed routing.load_policy()/policy.load_merged() a deliberately-corrupt base TOML and assert it falls back instead of raising; feed openbor-fetch-media a manifest with no DIR= line and assert it skips not crashes; `bash -n` supermodel-sinden.sh and confirm `exit \"${PIPESTATUS[0]}\"`. ON-DEVICE (needs the user + a display; from the review's checklist): - W7.0/W6.0: fresh/empty install (no ROMs or invalid es_systems.xml) → open Steam overlay/QAM on the no-games dialog → confi…

## Open questions / verify-first

VERIFY-FIRST CRITIC LEADS (do NOT treat as ready fixes): 1. W1.0 (GuiMadPanel input wedge) — the critic is RIGHT and I confirmed it in code: onBackendReady() at GuiMadPanel.cpp:163 already force-clears mInputLocked on daemon restart, and GuiMadPanel::update() does NOT run while a capture modal is topmost (the modal renders the panel itself). The proposed panel-owned captureModalActive() bool would have to be threaded through 5 page push-sites and could clear a legitimately-held lock. ACTION: on-device, kill the daemon mid-capture and check whether input actually wedges with the modal already gone. Only if a real residual wedge is reproduced, implement it the safe way (clear the lock from GuiMadCaptureModal's destructor/close path, which already holds mPanel, OR a >30s wall-clock watchdog) — NOT the panel-bool approach. Likely SKIP. 2. W0.1 (blocking write on UI thread) — the critic is RIGHT: the pipes are pipe2(O_CLOEXEC) not O_NONBLOCK, and writeLine() is a partial-write loop; making it non-blocking would send a partial NDJSON line on EAGAIN and desync the protocol + falsely declare the backend dead. The finding is rated LOW/speculative (tiny KB lines vs a 64KB pipe, continuously-draining daemon). ACTION: SKIP unless an on-device UI stall is actually observed; if pursued, only a poll(POLLOUT)-timeout-before-write redesign is safe. 3. 8.0 vs N3.0 — RESOLVED by my verification, needs a user decision: es_systems.xml:8 invokes model2-m2emu.sh (which ALREADY uses "$INI" correctly), NOT model-2-emulator.sh. So finding 8.0 ("M2CONFIGFILE literal sed") is DEAD CODE in an unused EmuDeck-stock file — do NOT 'fix' it; instead move model-2-emulator.sh (and its sibling .config) to _TMP as cleanup, OR leave it. The REAL live-path crosshair robustness work is finding N3.0 (guard model2-m2emu.sh:28-29 with `[ -f \"$INI\" ]` so a missing EMULATOR.INI doesn't abort the launch under set -eu). Confirm with the user whether to delete the stale EmuDeck file. 4. N0.0 (Sinden stray-nav) — the _mad_sinden flag is provably dead (set at router-config-gui.py:283, never read). But whether the user-visible symptom (gun drives stray nav, trigger-quit dead) actually manifests needs on-device confirmation, and there's a choice: (a) honor the flag (skip EV_ABS nav for sinden devices + add a BTN_LEFT hold-to-quit) vs (b) stop admitting the Sinden mouse interface at all. Decide intent before coding. 5. C0.0/C1.0/C1.1/C0.1 — static evidence is strong (I traced the readonly chain and the EUID gate myself), but the review's checklist asks to confirm on a REAL post-update Deck that Samba isn'…

## Fixes the critic rejected (do NOT apply as written)

- **[W1.0]** The fix's central premise is WRONG. It claims 'GuiMadPanel already pushes GuiMadCaptureModal (it owns the push site)' and that captureModalActive() is easy to implement by tracking the panel's own push. It does NOT: GuiMadCaptureModal is pushed by the PAGES (GuiMadPagePlayers.cpp:128, GuiMadPagePrev… — _Do NOT implement captureModalActive() via a panel-owned bool. First VERIFY the residual gap is real on-device (kill the daemon mid-capture, confirm input wedges with the modal already gone). If real,…_
- **[W0.1]** The proposed non-blocking fix would CORRUPT the wire protocol. writeLine() (MadBackend.cpp:414-430) is a partial-write loop: `while (written < payload.length())`. If mStdinFd is set O_NONBLOCK and a line partially writes then the pipe fills, write() returns -1/EAGAIN MID-LINE — the proposed handler… — _Recommend SKIP / defer. The blocking-UI-thread risk is largely theoretical given tiny requests vs a 64KB pipe and a continuously-draining daemon. If pursued, do NOT make the write non-blocking with th…_

---

_Source: fix-plan workflow `wf_9f6e221c-e16` (7 agents) over `REVIEW-FINDINGS.md`. Full structured output: `review-findings/` + the workflow transcript._
