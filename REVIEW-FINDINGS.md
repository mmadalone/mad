# MAD / controller-router — code review findings

> Generated 2026-06-13 from a three-wave adversarial multi-agent review. Findings are sorted by **re-verified** severity. Each carries a verification status: wave 2/3 used strict per-finding skeptics that *executed* checks where possible (`reproduced-by-execution`) or reasoned from source (`static-confirmed`). **This was a static review — no finding was reproduced on a running Deck with a display.** Absence of a finding is not proof of correctness.

## Scorecard

| Wave | Scope | Confirmed | High | Refuted | Critic leads |
|---|---|---:|---:|---:|---:|
| 1→2 | Python / shell (`/home/deck/Emulation/tools/launchers`) | 79 | 3 | 21 | 11 |
| 3 | ES-DE C++ fork patches + config | 22 | 2 | 3 | 9 |
| **Total** | | **101** | **5** | **24** | **20** |

`wave` column on each finding: **P** = Python/shell, **C** = C++/config.

## Seam conclusion (cross-wave)

VERIFIED CONCURRENCY/SERIALIZATION ANSWER (read MadBackend.{h,cpp}, MadJson.h, MadWiiBridge, plus every pages/*.cpp request call site): The C++ client is single-client, single-writer, and strictly ordered ON THE WIRE, but it is NOT a synchronous handshake — it PIPELINES. MadBackend::request() (MadBackend.cpp:387-412) is fire-and-forget: assign incrementing id, store PendingRequest only if a callback exists, writeLine() one NDJSON line over the single blocking mStdinFd, return immediately. Responses arrive on a dedicated reader thread that only appends to a mutex-guarded mQueue; the UI thread drains it once per frame in poll()->dispatchMessage(). ALL request() calls originate on the SINGLE UI thread (page build/input/update, poll-driven response/event/stream callbacks, and GuiMadCaptureModal::update() which polls the SAME backend) — NO request() call from the reader thread or any other thread. So the daemon sees exactly one client, one stdin reader, lines in monotonic id order, one writer. CONFIRMS DEAD: Python [0.2] (unlocked _ENUM_CACHE) and [10.2]/[10.x] (hotplug watcher) — both were refuted on 'requests arrive serially on one stdin thread + single client,' which the C++ side fully confirms, AND they concern state mutated synchronously on that stdin-reader thread, so they stay dead. CONDITIONALLY REVIVES / MUST RE-CHECK: Python [2.1] (set_ra_option read-modify-write). It stays dead ONLY IF the RMW executes synchronously on the Python stdin-reader thread. If mad-backend.py dispatches that handler (or any slow handler) to a ThreadPool, the C++ client's pipelining means requ…

**Consequence:** `set_ra_option` is `slow=True` (runs on a 4-worker pool) and does an unlocked read-modify-write of a shared RetroArch `.cfg`. Because the C++ client pipelines, two such calls can overlap → finding **[2.1]** is a *confirmed* race, not refuted.

> **⚠️ Correction (verified after this section was written — see `FIX-PLAN.md`):** the "CONFIRMS DEAD … [0.2]" claim above is **wrong**. `_WatchStream` (`lib/madsrv/device_cmds.py:128`) runs on its **own daemon thread** (`rpc.py:107`) and calls `enumerate_devices()` every 2s, while `slow=True` pool workers call it concurrently — both mutate the unlocked module-global `_ENUM_CACHE` (`lib/devices.py:163-217`). So **`[0.2]`/`[10.11]` is a confirmed live race** (as wave 2 originally rated it). Wave 3's seam reasoning only considered the stdin-reader thread and missed the watch thread + pool. `[10.2]` (watch/unwatch token swap) stays refuted — that part is serial.

---

## 🔴 Confirmed — HIGH (5)

### [W4.0] (C) Use-after-free: Splash random-pool switch callback captures a raw SwitchComponent* that a concurrent MODE/FIT rebuild frees

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageSplash.cpp:181-209 (capture at 181-182, deref at 203; also ETIMEOUT/EBACKEND path); rebuild that frees it at 88-97 / 320` · _memory-safety_
- **Impact:** Heap use-after-free (write through a dangling SwitchComponent*) reachable in normal use of the random-splash picker if the user cycles MODE/FIT while a pool toggle is still in flight (slow write or backend death). Likely a crash or silent heap corruption.
- **Fix:** Don't capture the raw widget pointer in the async response. Either (a) re-locate the switch at response time by `name` against the live mList rows (mirroring how setFlag() searches mToggles), bailing if not found, or (b) capture a std::weak_ptr<SwitchComponent> (store the shared_ptr in a member keyed by name) and check before deref. Option (a) matches the existing safe pattern in this codebase. Th…
- **Verification:** `static-confirmed` — CONFIRMED heap use-after-free (write through a dangling SwitchComponent*) reachable in normal use of the random-splash picker.  OWNERSHIP/LIFETIME (proven): In GuiMadPageSplash::rebuildList() the SwitchComponent for each random_image row is owned ONLY by the ComponentListRow inside mList (std::shared_ptr<ComponentList>). At GuiMadPageSplash.cpp:90-93 rebuildList does `removeChild(mList.get()); mList.reset();`, destro…

### [W7.0] (C) Focus-change handler dereferences mCurrentView unconditionally — null-pointer crash in the no-games state

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/main.cpp:555-562 (calls into es-app/src/views/ViewController.h:87 and :89)` · _memory-safety_
- **Impact:** Hard SIGSEGV / crash of ES-DE. Reachable on a fresh EmuDeck install with no ROMs yet, or any time all games are removed/filtered out so the no-games dialog is showing, the instant the user brings up the Steam overlay or QAM. Not the everyday path (requires the no-games state), hence high rather than critical, but it is a definite memory-safety hole introduced by the patch in normal Steam Deck usag…
- **Fix:** Guard both calls the same way the existing siblings do: either change the call sites in main.cpp to skip when there is no current view, or (better, matching the file's own convention) add `if (mCurrentView != nullptr)` inside startViewVideos()/stopViewVideos()/pauseViewVideos()/muteViewVideos() in ViewController.h so every external caller is safe.
- **Verification:** `static-confirmed` — CONFIRMED. The new focus-change handler we added in main.cpp:555-562 (verified ours via `git diff base/v3.4.1..HEAD -- es-app/src/main.cpp`) calls ViewController::getInstance()->startViewVideos() / pauseViewVideos() on every gamescope focus transition. Those overrides at /home/deck/esde-build/ES-DE/es-app/src/views/ViewController.h:87 and :89 are: `void startViewVideos() override { mCurrentView->startViewVideos(); }`…

### [11.4] (P) steam-fetch-metadata.py fix_cover() deletes the user's cover with no recovery when no portrait exists anywhere

- **Where:** `steam-fetch-metadata.py:143-146` · _data-loss_  _(was medium)_
- **Impact:** A user-supplied or previously-good (possibly hand-picked) cover that merely happens to be wider-than-tall is permanently deleted with no _TMP copy and no re-download, leaving the game with no cover. V…
- **Fix:** Instead of e.unlink(), move the rejected cover to a recoverable location (e.g. covers/_rejected-landscape/ or Downloads/_TMP) with a note, or simply leave it — a sideways cover is less bad than no cov…
- **Verification:** `reproduced-by-execution` — CONFIRMED. steam-fetch-metadata.py:143-146 permanently deletes the user's cover with a bare unlink() and no recovery, violating the project hard rule "NEVER rm user data — deletions must MOVE to a recoverable _TMP + RECOVERY.txt".  Code path (steam-fetch-metadata.py): - fix_cover() L119: existing = covers.glob(stem+".*"); L120-123 returns "ok" ONLY if dims() says portrait/square (w<=h). Otherwise falls through. - L12…

### [8.0] (P) model-2-emulator.sh sed targets literal "M2CONFIGFILE" instead of a variable — DrawCross crosshair toggle is dead

- **Where:** `model-2-emulator.sh:27-30` · _bug_
- **Impact:** The Model 2 Emulator crosshair is never hidden for the gun games (bel/gunblade/rchase2) nor re-enabled for other games — DrawCross stays at whatever the .ini already had. Lightgun games show a doubled…
- **Fix:** Define the variable (e.g. `M2CONFIGFILE="$romsPath/model2/EMULATOR.INI"`) and reference it as `"$M2CONFIGFILE"`, and move the sed AFTER the path is known. Mirror model2-m2emu.sh which already does thi…
- **Verification:** `reproduced-by-execution` — model-2-emulator.sh:27 `sed -i 's/DrawCross=1/DrawCross=0/' "M2CONFIGFILE"` and :30 `sed -i 's/DrawCross=0/DrawCross=1/' "M2CONFIGFILE"` both target the literal quoted string "M2CONFIGFILE", not a variable ($M2CONFIGFILE). grep over the whole repo shows M2CONFIGFILE appears ONLY on these two lines — it is never defined as a shell var: `grep -rn "M2CONFIGFILE" /home/deck/Emulation/tools/launchers/` returns only lines…
- **⚠️ Refuter caveat (uncertain):** The literal-string typo is REAL: /home/deck/Emulation/tools/launchers/model-2-emulator.sh:27,30 run `sed -i 's/DrawCross=1/.../' "M2CONFIGFILE"` — a quoted literal, not "${M2CONFIGFILE}". `grep -n M2CONFIGFILE model-2-emulator.sh` shows it appears ONLY on lines 27/30, never assigned. Executed in /tmp/_madrev: `sed -i '…

### [N7.0] (P) steam-collection-gen.py rewrites the live steam gamelist.xml with NO ES-DE-running guard

- **Where:** `/home/deck/Emulation/tools/launchers/steam-collection-gen.py:171` · _data-loss_
- **Impact:** If the user runs steam-collection-gen.py while ES-DE is open (the docstring explicitly tells them to run it), ES-DE rewrites gamelist.xml on its own exit and silently clobbers the freshly generated steam collection — and a crash mid-write leaves a truncated/empty steam gamelist. Same class and severity as the listed [10.5]/[9.1]/[10.6] steam-fetch-metadata findings, just a different (unlisted) scr…
- **Fix:** Add the same pgrep('-f','ES-DE|emulationstation') abort guard steam-collection-sync.py:83 already uses at the top of main(), and write atomically (write to GAMELIST.with_suffix('.tmp') then os.replace) to avoid truncation.
- **Verification:** `static-confirmed` — steam-collection-gen.py:171 does `GAMELIST.write_text("\n".join(lines)+"\n", ...)` where GAMELIST=line 21 `HOME/"ES-DE"/"gamelists"/"steam"/"gamelist.xml"` — the LIVE ES-DE user-data file (`ls` shows real 48456-byte file, and `pgrep -f 'ES-DE|emulationstation'` returned RUNNING during this review, proving the concurrency window is real). No guard exists: `grep -niE 'pgrep|pidof|running|emulationstation|ES-DE'` over t…

---

## 🟠 Confirmed — MEDIUM (28)

### [W6.0] (C) Focus-change handler dereferences ViewController::mCurrentView, which is null on the no-ROMs / invalid-systems startup path

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/main.cpp:555-562` · _memory-safety_
- **Impact:** Crash (null pointer dereference) when, on a broken/empty ES-DE install (no ROMs found or invalid es_systems.xml), the user is sitting on the error dialog in Game Mode and opens the Steam overlay/QAM after ~2 seconds. Does not affect a normal install with games (mCurrentView is always set before the loop). Uncommon path but a real, deterministic crash once reached.
- **Fix:** Guard the two calls: either add `if (mCurrentView != nullptr)` inside ViewController::startViewVideos()/pauseViewVideos() (mirroring resetViewVideosTimer), or wrap the focus-change branch in main.cpp with a null/has-view check (e.g. only run the pause/resume if a view exists). The simplest is to make startViewVideos/pauseViewVideos null-safe like resetViewVideosTimer already is.
- **Verification:** `static-confirmed` — The finding is real. Verified statically end-to-end.  UNGUARDED DEREF (confirmed): /home/deck/esde-build/ES-DE/es-app/src/views/ViewController.h:87 `void startViewVideos() override { mCurrentView->startViewVideos(); }` and :89 `void pauseViewVideos() override { mCurrentView->pauseViewVideos(); }` dereference mCurrentView with NO null check, whereas the sibling resetViewVideosTimer() (:91-95) does guard `if (mCurrentV…

### [W7.1] (C) Per-frame setBlockInput(!esHasFocus) overrides ViewController's intentional input-block during game launch / rescan

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/main.cpp:549` · _correctness_
- **Impact:** ES-DE's intentional input-block windows (launch animation, rescan, return-from-game key swallow) are reset to unblocked every frame whenever ES-DE has focus. Today the visible effect is minor (a stray button press during the launch-screen countdown can dismiss the launch screen early), but the input-blocking contract is no longer honored.
- **Fix:** Don't blindly assert the flag every frame. Track the focus-derived desire separately and only setBlockInput(true) on focus loss / setBlockInput(false) on focus regain (i.e. on the transition, like the video pause already does at main.cpp:555-563), instead of writing it every iteration. Better still, OR the focus state with the existing block rather than overwriting, so ViewController's blocks surv…
- **Verification:** `static-confirmed` — The patch adds, at the very top of every applicationLoop() iteration (main.cpp:547-549), an UNCONDITIONAL write: gamescopeFocus.init(); const bool esHasFocus {gamescopeFocus.hasFocus()}; window->setBlockInput(!esHasFocus); — this runs BEFORE SDL_PollEvent/parseEvent and BEFORE window->update(). The diff base/v3.4.1..HEAD confirms upstream had no per-frame setBlockInput at the loop top (only an Android-specific path).…

### [0.0] (P) quit-combo-watcher self-SIGTERMs and breaks the policy's fast KILL escalation (Eden/multi-app quit_cmd)

- **Where:** `quit-combo-watcher.py:103-121` · _correctness_
- **Impact:** Eden (and any multi-token quit_cmd whose pattern matches the watcher's own argv) takes the global 6s backstop to die instead of the intended 2s grace; the policy's documented 'fast escalate after 2s' …
- **Fix:** Run the quit_cmd in its own detached session so the watcher isn't in the pkill blast radius (e.g. subprocess.run(["setsid","bash","-c",quit_cmd]) and have the watcher exit immediately), or make the ch…
- **Verification:** `reproduced-by-execution` — CONFIRMED by execution. Reachability: the watcher is launched by ES-DE game-start hook /home/deck/ES-DE/scripts/game-start/quit-combo-watcher.sh:22-23 as `python3 quit-combo-watcher.py --system "$SYSTEM" --quit-cmd "$QUIT"`, so the watcher's own /proc cmdline literally contains the quit pattern. For the switch/Eden backend the pattern is `Eden|Yuzu|Suyu|Ryujinx` (controller-policy.toml:415: `pkill -TERM -f 'Eden|Yuzu…

### [0.1] (P) Base controller-policy.toml parse is unguarded; a hand-edit typo aborts EVERY game launch

- **Where:** `lib/routing.py:46-57` · _robustness_
- **Impact:** One stray character in the base policy makes nothing launchable until the file is fixed, with the only signal being an ES-DE 'launch failed'. Inconsistent with the deliberately fail-soft handling ever…
- **Fix:** Guard the base parse too (catch TOMLDecodeError/OSError, log, fall back to {'systems': {}} so routing degrades to RetroArch defaults), and/or wrap _setup/_standalone in main() so an unexpected router …
- **Verification:** `reproduced-by-execution` — lib/routing.py:48-50 parses the BASE controller-policy.toml UNGUARDED (tomllib.load with no try/except), while lib/routing.py:51-56 wraps ONLY the LOCAL file parse in try/except(TOMLDecodeError,OSError) with the comment "a broken local file must never break routing" — so the human-edited base file (per the lines 26-27 comment, "the (commented, human-edited) defaults") is the unprotected one. Reproduced with the REAL…

### [0.2] (P) enumerate_devices() mutates the module-global _ENUM_CACHE with no lock; unsafe under mad-backend's concurrent threads

- **Where:** `lib/devices.py:134-218` · _concurrency_
- **Impact:** Cache effectiveness collapses under controller hotplug bursts while MAD is open (the exact scenario the cache was built for), re-introducing the multi-hundred-ms per-walk cost and GUI lag; in the wors…
- **Fix:** Add a module-level threading.Lock and hold it around the whole enumerate_devices() body (or at least the cache read/populate + prune), matching the existing _SDL_LOCK pattern in the same file.
- **Verification:** `reproduced-by-execution` — CLAIM IS TECHNICALLY TRUE AND REACHABLE — kept at medium (genuine but edge-case + self-healing).  (1) No lock guards _ENUM_CACHE. devices.py:134 declares it module-global; it is read (.get L163), written (_ENUM_CACHE[path]=... L193), popped (L171), and SWEPT by iterating the live dict in a comprehension then deleting (L216-217: `for p in [p for p in _ENUM_CACHE if p not in alive]: del _ENUM_CACHE[p]`). `grep -n Lock…

### [10.5] (P) steam-fetch-metadata.py rewrites steam/gamelist.xml with NO ES-DE-running guard (its sibling has one)

- **Where:** `steam-fetch-metadata.py:215-252` · _data-loss_  _(was high)_
- **Impact:** Running the Steam metadata fetch while ES-DE is open either loses all the scraped metadata/cover work on ES-DE exit or risks an inconsistent gamelist; the timestamped .bak only protects the PRE-edit s…
- **Fix:** Add the same pgrep -x es-de/emulationstation guard steam-collection-sync.py uses and refuse to write (or warn loudly) when ES-DE is running; the write is already backed up but the guard is the real pr…
- **Verification:** `static-confirmed` — CLAIM IS REAL (not a false positive), but severity downgraded high->medium.  Guard truly missing in target: steam-fetch-metadata.py writes the live ES-DE user-data gamelist GL = ~/ES-DE/gamelists/steam/gamelist.xml (defined line 37; confirmed live file exists: `ls -la /home/deck/ES-DE/gamelists/steam/gamelist.xml` -> 48456 bytes). The write is a non-atomic in-place truncate at line 252: `GL.write_text(new, encoding="…

### [10.7] (P) Standalone emulator config writers (cemu/xemu/pcsx2/rpcs3/eden) overwrite the live profile non-atomically

- **Where:** `lib/cemu_cfg.py:132,203-204` · _data-loss_
- **Impact:** A power loss / kill during the controller-assignment write (game-start) can truncate the active emulator controller profile; the game then launches with a broken/empty controller config until the user…
- **Fix:** Use the same temp+os.replace atomic write the RetroArch/localpolicy writers use, so the live file is only ever swapped atomically. Keep the one-time backup as the original-recovery escape hatch.
- **Verification:** `static-confirmed` — The non-atomic write is real and verified by execution. grep over lib/*cfg*.py shows cemu_cfg.py:132 `_port_path(...).write_text(text,...)` and :203-204 `_port_path(cfg_dir, first).write_text(tpath.read_text(...),...)` are bare truncate-in-place writes; pcsx2_cfg.py:195 `ini.write_text(text,...)`, xemu_cfg.py:110 `path.write_text(text,...)`, eden_cfg.py:187 `ini.write_text(text,...)`, and rpcs3_cfg.py:183 `with ymlp.…

### [11.1] (P) dedup-disc-gamelists.py rewrites per-system gamelist.xml with no ES-DE-running guard

- **Where:** `dedup-disc-gamelists.py:68` · _data-loss_  _(was high)_
- **Impact:** Running this with ES-DE open (the user is told to set ShowHiddenGames=off, which they'd toggle inside ES-DE — so ES-DE is plausibly running right before/after) silently discards the hidden-flag edits …
- **Fix:** Add an early pgrep -f 'ES-DE|emulationstation' guard that aborts with a 'close ES-DE first' message, matching steam-collection-sync.py:83-86, before dedup() writes any gamelist.
- **Verification:** `reproduced-by-execution` — CONFIRMED but DOWNGRADED high->medium.  (1) No ES-DE-running guard exists. dedup-disc-gamelists.py line 68 `open(gl, 'w', encoding='utf-8').write(new)` truncate-rewrites the live gamelist in place (NOT atomic temp+rename). The only imports (line 14-15) are sys, re, glob, os, html, time, collections, ET — `grep -niE 'pgrep|pidof|subprocess|running'` finds NO guard. gl = ~/ES-DE/gamelists/<sysname>/gamelist.xml (line 3…

### [11.2] (P) skyscraper-apply.py rewrites per-system gamelist.xml with no ES-DE-running guard

- **Where:** `skyscraper-apply.py:41` · _data-loss_  _(was high)_
- **Impact:** If ES-DE is open, all applied metadata for every processed system is clobbered when ES-DE rewrites the gamelists on exit; the user's scrape-apply run is silently lost.
- **Fix:** Add the steam-collection-sync.py-style pgrep ES-DE/emulationstation guard at the top, aborting before the first gamelist write.
- **Verification:** `static-confirmed` — skyscraper-apply.py:3,15 target the LIVE ES-DE user-data file: GLD=~/ES-DE/gamelists, real=os.path.join(GLD,sysn,"gamelist.xml"). Confirmed live: `ls -la /home/deck/ES-DE/gamelists/nes/gamelist.xml` -> 674317 bytes, mtime Jun 9; doc cache deck-docs line 362 states "gamelists/<system>/gamelist.xml ... ES-DE keeps these here"; es_settings has LegacyGamelistFileLocation=false. NO ES-DE-running guard exists: `grep -niE "…

### [11.5] (P) mad_backup.do_restore copies emulator configs back with no emulator/ES-DE running guard (text says 'close emulators' but does not enforce)

- **Where:** `lib/mad_backup.py:102-130` · _data-loss_
- **Impact:** A user restoring a controller snapshot from the MAD panel while an emulator is still open gets a silent partial/overwritten restore, defeating the safety-net purpose; the merge semantics also leave st…
- **Fix:** Before restoring, pgrep the relevant emulators (and ES-DE) and refuse / warn loudly with a hard stop, mirroring dolphin-wii-mode.sh:32-35 and steam-collection-sync.py:83. Document that copytree merges…
- **Verification:** `static-confirmed` — lib/mad_backup.py:102-130 do_restore() copies each snapshot file back onto the live target with shutil.copy2(f, p) (line 113) / shutil.copytree (115) / shutil.copy2(lp, LOCAL) (121), with NO check that the owning emulator is closed. The "Close emulators first if any were open." string is only in the AFTER-the-fact success message (line 130), not an enforced precondition. Targets come from backup_targets() (lib/mad_co…

### [12.0] (P) Device name interpolated unescaped into a regex replacement template corrupts Cemu controller XML

- **Where:** `lib/cemu_cfg.py:131` · _injection_
- **Impact:** A Wii U game launched with a pad whose name contains `&`/`<`/`>`/`\` gets a silently corrupted controllerN.xml: Cemu can't load the profile so that controller doesn't work, and in the `\`+invalid-grou…
- **Fix:** Don't interpolate into a replacement template. Use a function replacement so the value is treated literally, and XML-escape the name first, e.g.: `from xml.sax.saxutils import escape` then `text = _DI…
- **Verification:** `reproduced-by-execution` — lib/cemu_cfg.py:131 — `text = _DISPLAY_RE.sub(rf"\g<1>{dev.name}\g<3>", text, count=1)` interpolates dev.name (the raw evdev kernel device name, devices.py:48/181) into a re.sub REPLACEMENT template, where backslashes are special. Executed proof: name=r"x\" -> '<display_name>x\g<3>' (closing </display_name> tag LOST -> corrupt XML); name=r"num\1ref" -> '<display_name>num<display_name>ref</display_name>' (group ref in…

### [12.3] (P) Cover/media files deleted outright instead of moved to recoverable _TMP

- **Where:** `steam-fetch-metadata.py:138,145` · _data-loss_
- **Impact:** A user-supplied or previously-good landscape cover is permanently deleted when no portrait alternative exists; there is no _TMP copy to restore from (only the gamelist text is backed up, not the image…
- **Fix:** Move deleted media to a timestamped _TMP dir (same filesystem as MEDIA, e.g. /run/media/deck/1tbDeck/_TMP_steam-cover-<ts>) with a RECOVERY.txt instead of unlink(), per project rule #5 — or at minimum…
- **Verification:** `static-confirmed` — steam-fetch-metadata.py:118 covers = MEDIA/"covers" where MEDIA=/run/media/deck/1tbDeck/downloaded_media/steam (line 38) — confirmed real user-data dir (ls shows ~real .jpg covers e.g. "Blasphemous 2.jpg"). fix_cover() deletes covers via e.unlink() with NO move-to-_TMP, violating CLAUDE.md rule #5.  Line 138 (cv=="fixed"): `for e in existing: e.unlink()` then `tmp.replace(...)` — a replace-with-verified-better (old c…

### [12.4] (P) Base controller-policy.toml parsed with no error handling — a typo aborts every game launch

- **Where:** `lib/routing.py:48-50` · _robustness_
- **Impact:** One stray character in controller-policy.toml makes every routed game fail to launch with a generic ES-DE error and no obvious cause; the user (non-technical) cannot tell the policy file is at fault.
- **Fix:** Wrap the base parse in try/except TOMLDecodeError and on failure log the parse error to stderr/router.log and fall back to a minimal safe default (e.g. `{'systems': {}}`), so a typo degrades to 'no ro…
- **Verification:** `reproduced-by-execution` — CONFIRMED but the title overstates it ("every game launch" is wrong). lib/routing.py:46-57 load_policy(): the BASE file read (lines 48-50 `with POLICY_FILE.open("rb") as f: base = tomllib.load(f)`) has NO try/except, while the immediately following LOCAL file branch (lines 51-56) IS wrapped in `try/except (tomllib.TOMLDecodeError, OSError): pass`. The asymmetry confirms it's an oversight, not intent.  Verified the ex…

### [12.5] (P) openbor-fetch-media.py crashes on a manifest missing a DIR= line (unguarded .group)

- **Where:** `openbor-fetch-media.py:72` · _robustness_
- **Impact:** A single malformed/incomplete .openbor manifest crashes the entire OpenBOR media fetch instead of skipping that one game. It's a manually-run utility, so no daemon dies, but the run fails wholesale.
- **Fix:** Guard it: `dm = re.search(r'^DIR=(.*)$', man, re.M); dirn = dm.group(1) if dm else ''` and skip the vdf-matching loop when dirn is empty, returning None for that stem.
- **Verification:** `reproduced-by-execution` — openbor-fetch-media.py:72 — `dirn=re.search(r'^DIR=(.*)$', man, re.M).group(1)` calls `.group(1)` directly on the re.search result with no None-guard. If a manifest lacks a `DIR=` line, re.search returns None and `.group(1)` raises AttributeError. The path IS reachable: line 72 is only skipped when the PREFIX basename `isdigit()` returns early (line 71). Many real manifests have a non-digit PREFIX (`prefix_base=prefi…

### [2.1] (P) systems.set_ra_option runs on the worker pool and does an unlocked read-modify-write of shared .cfg files

- **Where:** `lib/madsrv/systems_cmds.py:242-257` · _concurrency_
- **Impact:** Under pipelined/rapid requests a RetroArch per-system option toggle can be silently lost (the response even reports success via the re-read, which may reflect the OTHER concurrent write). The atomic r…
- **Fix:** Either make set_ra_option fast (inline, like model2.set and profiles.apply_slot — the write is tiny) so all config writers serialize on the stdin thread, or guard set_system_option with a module-level…
- **Verification:** `reproduced-by-execution` — CONFIRMED, severity stays medium (calibrated — timing-dependent, single-user, non-destructive).  Root facts: - lib/madsrv/systems_cmds.py:242 `@method("systems.set_ra_option", slow=True)` → dispatched on the worker pool. rpc.py:24 `_POOL = ThreadPoolExecutor(max_workers=4)`; rpc.py:82-83 slow methods run via `_POOL.submit`, so up to 4 run concurrently. Comment rpc.py:10-12 confirms slow=True ⇒ worker pool. - No lock…

### [4.0] (P) Quitting MAD from the camera-tuning page orphans the ffmpeg preview and leaves the Sinden lightgun dead

- **Where:** `router-config-gui.py:1870-1875` · _resource-leak_  _(was high)_
- **Impact:** After quitting MAD from the camera-tuning page, an orphaned ffmpeg keeps the V4L2 camera open and the Sinden lightgun stays non-functional (aiming dead) until the user finds and kills the stray ffmpeg…
- **Fix:** In quit() call self._cam_kill_ffmpeg() BEFORE self._cam_restore_driver() (mirroring _clear's ordering), or simply call self._clear() at the top of quit() so every page's teardown (camera ffmpeg, Daphn…
- **Verification:** `static-confirmed` — router-config-gui.py:1870-1875 quit() = _gp_cleanup(); _cam_restore_driver(); self.root.destroy() — it NEVER calls _cam_kill_ffmpeg() and NEVER calls _clear(). _cam_kill_ffmpeg() is invoked only inside _clear() (line 1374-1375: `if self._cam_proc or self._cam_driver_paused: self._cam_kill_ffmpeg(); self._cam_restore_driver()`) and in the camera page's own stop/preview/save handlers — none of which run on the quit pat…

### [5.0] (P) Standalone backends (cemu/pcsx2/eden/rpcs3/xemu) overwrite live config non-atomically

- **Where:** `lib/pcsx2_cfg.py:195` · _data-loss_
- **Impact:** A power loss or kill mid-write (the file is open for truncation) corrupts the live controller profile; user input config is lost and the stale .router-backup cannot recover edits made after first laun…
- **Fix:** Route all standalone writes through a shared atomic helper (write to `<target>.router-tmp` in the same dir, then os.replace onto the target), mirroring retroarch_cfg._atomic_write. The text/bytes are …
- **Verification:** `static-confirmed` — All five standalone backends write the live user config non-atomically (truncate-in-place). Confirmed by reading the code: pcsx2_cfg.py:195 `ini.write_text(text, encoding="utf-8")`; eden_cfg.py:187 `ini.write_text(...)`; xemu_cfg.py:110 `path.write_text(...)`; cemu_cfg.py:132 and :203 `_port_path(...).write_text(...)`; rpcs3_cfg.py:183-185 `with ymlp.open("w") ... yaml.safe_dump(data, f, ...)` (worst case: a safe_dum…

### [6.0] (P) esde_paths never finds the extracted ES-DE AppDir the wrapper actually runs — silently reads the stale legacy ~/AppDir bundled system set

- **Where:** `lib/esde_paths.py:23-36` · _correctness_
- **Impact:** After any future ES-DE version bump that adds/changes bundled systems, es_systems_wrap.wrap_system() would wrap a system using the stale legacy command (or fail to find a newly-added system), and es_s…
- **Fix:** Add the actual installed extracted AppDir to the resolution order, e.g. ~/Applications/ES-DE-MAD.AppDir/usr/share/es-de/resources (and ES-DE.AppDir), before the legacy ~/AppDir fallback; or have the w…
- **Verification:** `reproduced-by-execution` — The finding's mechanism is REAL and reachable. The live launcher /home/deck/Applications/ES-DE.AppImage is a bash script (file => "Bourne-Again shell script") whose own header says it runs the build "from a PERMANENTLY EXTRACTED AppDir instead of FUSE-mounting the AppImage ... Running the extracted AppRun creates NO FUSE mount." It ends with `APPDIR="$HOME/Applications/ES-DE-MAD.AppDir"` then `exec "$APPDIR/AppRun" "…

### [8.1] (P) supermodel-sinden-smart.py rewrites Supermodel.ini non-atomically with no backup — interrupted launch corrupts the user's config

- **Where:** `supermodel-sinden-smart.py:178-193` · _data-loss_
- **Impact:** A crash/kill/power-loss during the Supermodel launch can wipe or truncate Supermodel.ini, losing all of the user's input bindings and emulator settings, with no recovery copy.
- **Fix:** Write to a temp file in the same directory then os.replace() it onto INI (atomic), and/or take a one-time `Supermodel.ini.router-backup` before the first write as the cemu/serial-preflight writers do.
- **Verification:** `reproduced-by-execution` — supermodel-sinden-smart.py:178-193 reads the whole INI then reopens the SAME path with open(INI,'w') (truncate-in-place) and writelines() — classic non-atomic rewrite. No temp+rename/backup/fsync: grep for tempfile|os.replace|os.rename|.bak|backup|fsync|mkstemp returned "(no matches)".  Reachability confirmed: supermodel-sinden.sh:14 unconditionally invokes the launcher on every game start, and main() (line 250) alwa…

### [8.2] (P) sinden-update-retroarch-mouseindex.py overwrites global retroarch.cfg non-atomically and with no backup, from a game-start path

- **Where:** `sinden-update-retroarch-mouseindex.py:31-44` · _data-loss_
- **Impact:** Power-loss/crash during the write can truncate the global retroarch.cfg (loss of all the user's RetroArch settings, unrecoverable). If the mouse_index keys aren't present, lightgun aim silently isn't …
- **Fix:** Write via tmp file + os.replace in the same dir (atomic), like lib/retroarch_cfg. Optionally take a one-time backup. Handle the missing-key case (append the line) instead of silently no-op'ing.
- **Verification:** `static-confirmed` — CONFIRMED at medium. The write is non-atomic and backup-less, on a real game-start path, against the codebase's own atomic-write convention.  (1) Non-atomic write — sinden-update-retroarch-mouseindex.py:43 `CFG.write_text(new)`. Python's Path.write_text opens the target in mode 'w', which TRUNCATES the file to 0 bytes BEFORE writing. Proven by execution in /tmp/_madrev: after `open(p,"w")` and before any write, `p.re…

### [9.1] (P) steam-fetch-metadata.py rewrites gamelist.xml with no ES-DE-running guard

- **Where:** `steam-fetch-metadata.py:215-252` · _data-loss_
- **Impact:** If the user runs this while ES-DE is open (common — they may be browsing the steam collection), the metadata/cover changes are silently discarded when ES-DE exits and rewrites gamelist.xml from its in…
- **Fix:** Mirror steam-collection-sync.py: before writing, run `subprocess.run(['pgrep','-f','ES-DE|emulationstation'])` and abort with a clear message if returncode==0 (skip the check under --dry-run).
- **Verification:** `static-confirmed` — steam-fetch-metadata.py:37 GL = HOME/"ES-DE"/"gamelists"/"steam"/"gamelist.xml" resolves to the LIVE user-data file (confirmed: /home/deck/ES-DE/gamelists/steam/gamelist.xml exists, 48456 bytes of real data). Line 252 `GL.write_text(new, encoding="utf-8")` rewrites it on the DEFAULT path: `dry` is False unless --dry-run (line 196) and `do_meta` True unless --no-metadata (line 198), so plain `python3 steam-fetch-metad…

### [9.2] (P) openbor-gen-gamelist.py overwrites gamelist.xml with no ES-DE-running guard and no backup

- **Where:** `openbor-gen-gamelist.py:139-145` · _data-loss_
- **Impact:** Running while ES-DE is up makes the regeneration ineffective (overwritten on exit). Running while ES-DE is closed silently drops any favourites flag, play counts, custom metadata, or scraped fields ES…
- **Fix:** Add the pgrep ES-DE guard before writing, and write a timestamped .bak of the existing gamelist.xml before overwriting (as steam-collection-sync does).
- **Verification:** `static-confirmed` — All three claims verified against actual code in /home/deck/Emulation/tools/launchers/openbor-gen-gamelist.py:  1) Target IS the live ES-DE user-data gamelist. Line 19: OUT = f"{HOME}/ES-DE/gamelists/openbor/gamelist.xml". The file exists and is live data: `ls -la` shows /home/deck/ES-DE/gamelists/openbor/gamelist.xml = 18349 bytes, 291 lines, mtime Jun 4. Per project rules ES-DE rewrites gamelists on exit, so writin…

### [9.3] (P) dedup-disc-gamelists.py edits gamelist.xml with no ES-DE-running guard

- **Where:** `dedup-disc-gamelists.py:47-68` · _data-loss_
- **Impact:** If run while ES-DE is open, the de-dup hide/show changes are reverted when ES-DE rewrites the gamelist on exit, so duplicates reappear and the user believes the tool failed. (Backup + ET validation pr…
- **Fix:** Add the same `pgrep -f 'ES-DE|emulationstation'` abort used in steam-collection-sync.py before writing.
- **Verification:** `static-confirmed` — dedup-disc-gamelists.py:33 targets the LIVE ES-DE user-data gamelist: `gl = os.path.join(HOME, "ES-DE", "gamelists", sysname, "gamelist.xml")`. Verified the path is real, populated user data: `ls /home/deck/ES-DE/gamelists/` shows 41 system dirs and `find` lists real gamelist.xml files (psx, dreamcast, etc.). Lines 47-68 read the file (47), write a `.bak-{TS}` (48), then truncate-in-place rewrite it (68: `open(gl, 'w…

### [9.4] (P) skyscraper-apply.py edits live gamelist.xml with no ES-DE-running guard

- **Where:** `skyscraper-apply.py:24-41` · _data-loss_
- **Impact:** Run while ES-DE is open, the applied metadata/name overwrites and art are reverted on ES-DE exit (gamelist portion). The backup+validate logic protects against corruption, so the main risk is silently…
- **Fix:** Add the ES-DE pgrep guard before the first gamelist write.
- **Verification:** `static-confirmed` — CLAIM IS TRUE AND REACHABLE. skyscraper-apply.py:15 `real=os.path.join(GLD,sysn,"gamelist.xml")` with GLD=`~/ES-DE/gamelists` (line 3) targets the LIVE ES-DE gamelists. Confirmed live: `ls -la /home/deck/ES-DE/gamelists/snes/gamelist.xml` -> 608158-byte real file. Confirmed it's the same data ES-DE uses: es_settings.xml has `MediaDirectory=/run/media/deck/1tbDeck/downloaded_media` (== MEDIA const, line 3) and crucial…

### [N0.0] (P) Sinden lightgun is admitted to nav devices but its `_mad_sinden` guard is dead — aiming drives stray menu navigation and the documented trig…

- **Where:** `/home/deck/Emulation/tools/launchers/router-config-gui.py:279-303, 599-700 (flag set at 283, never read)` · _bug_
- **Impact:** With a Sinden lightgun powered on while the MAD panel is open (a likely scenario — MAD is the lightgun control panel and the camera/calibration pages assume a live gun), pointing the gun at the screen randomly moves the focus ring, making the panel hard to drive. The documented gun-trigger quit silently does nothing (user-facing dead feature). The gun is also needlessly opened/polled by the nav lo…
- **Fix:** Either (a) actually honor `_mad_sinden` in `_handle`: skip the EV_ABS directional/trigger branches for `getattr(dev, '_mad_sinden', False)` devices (and don't populate `_dir_thresh`/`_trig_thresh` for them in `_scan`), and add a BTN_LEFT hold-to-quit timer for sinden devices if that behavior is still wanted; or (b) drop the dead comments and stop admitting the Sinden mouse interface at all (line 2…
- **Verification:** `reproduced-by-execution` — Core claim CONFIRMED on live hardware. (1) `_mad_sinden` is dead: `grep -rn _mad_sinden /home/deck/Emulation/tools/launchers/` returns ONLY router-config-gui.py:277 (comment), 279, 283 (set) — never read. (2) Live Sinden devices are connected; reading capabilities (no grab) of the "SindenLightgun Mouse" interfaces /dev/input/event10 and event20: vendor=0x16c0, BTN_LEFT present, NOT gamepad (no 0x130-0x13f) -> `_scan`…

### [N1.0] (P) Camera preview never polls ffmpeg: if it dies on its own the feed freezes-but-claims-live and leaves a zombie

- **Where:** `/home/deck/Emulation/tools/launchers/router-config-gui.py:2478-2488 (_cam_tick); reaped only in _cam_kill_ffmpeg 2398-2409` · _resource-leak_
- **Impact:** On a common-enough failure (camera still held by the just-stopped driver, camera busy/unplugged, ffmpeg error) the tuning page shows a frozen frame while telling the user it is 'live' — they tune sliders against a dead feed and can't tell. A zombie ffmpeg lingers until they leave the page. No crash, but a confusing dead-feed that looks like a hung preview.
- **Fix:** In _cam_tick, before re-arming, detect ffmpeg death: `if self._cam_proc.poll() is not None: self._cam_kill_ffmpeg(); self._cam_lbl.config(image='', text='( camera busy / preview ended — retry )'); self._cam_status.config(text='Preview ended (camera busy?). Press Preview to retry.'); return`. poll() also reaps the child, eliminating the zombie. (Optionally cap retries before showing the busy hint.)
- **Verification:** `reproduced-by-execution` — CODE: router-config-gui.py:2478-2488 _cam_tick guards only `if not self._cam_proc:` (line 2480) — truthiness of the retained Popen object; it never calls self._cam_proc.poll(). The only poll/wait in the cam path is `p.wait(timeout=2)` at _cam_kill_ffmpeg:2403, which runs only on stop/Save/page-leave (callers: _cam_preview start 2444, _cam_stop_preview 2429, _cam_save 2515, _clear 1375). `grep -n '\.poll()\|\.wait(\|_…

### [N3.1] (P) supermodel-sinden.sh: launcher/emulator exit code is swallowed by the `tee` pipe — ES-DE always sees success

- **Where:** `/home/deck/Emulation/tools/launchers/supermodel-sinden.sh:11-15` · _robustness_
- **Impact:** A failed Supermodel (Sinden fork) launch is invisible to ES-DE; the user gets no error feedback and the game-end hooks/controller-router cleanup run as if a game had actually played. Masks real failures and makes diagnosis harder.
- **Fix:** Switch the shebang to bash (or add `set -o pipefail` if the shell supports it) and end with `exit "${PIPESTATUS[0]}"` (or in POSIX sh, capture the inner status via a fifo / `{ cmd; echo $? >status; } | tee`), mirroring hypseus-pin.sh.
- **Verification:** `reproduced-by-execution` — Mechanism confirmed in /home/deck/Emulation/tools/launchers/supermodel-sinden.sh:11-15 — it is `#!/bin/sh` (line 1), with the entire launch as `{ echo...; supermodel-sinden-smart.py "$@" 2>&1; } 2>&1 | tee "$LOG"`. No `set -o pipefail` and no `exit "${PIPESTATUS[0]}"` after it, so the script's exit = tee's exit = 0. Reproduced on /tmp copies: inner `exit 42` -> wrapper exit 0; `os.execvpe('/tmp/_madrev/NOPE_supermode…

### [N6.0] (P) Headless X-Arcade tester escape combo (P1+P2 Start) keys off calibration-driven spot state, not raw BTN_START — breaks under user calibratio…

- **Where:** `lib/madsrv/tester_cmds.py:826-839, 716-727` · _correctness_
- **Impact:** On the ES-DE-native panel (which input-LOCKs nav during the test via input.lock {nav:True}, line 806, so the escape gesture is the intended way out), a user who has calibrated their X-Arcade can find the documented 'hold P1+P2 Start 3s' escape doesn't work, or fires off the wrong buttons; and a mid-calibration accidental Start+Start ends the test and discards unsaved bindings. ■ Stop with the Deck…
- **Fix:** Track raw BTN_START per gamepad node (mirror the Tk version: maintain a set of pressed `{tag}:k{BTN_START}` keys directly from the event stream) and compute `both` from that rather than from the calibratable spot dict; and gate the escape check on `self._cal_armed is None`/not-calibrating so the combo can't end the test mid-calibration.
- **Verification:** `static-confirmed` — All structural claims verified against the actual code; behavior needs the X-Arcade cabinet to reproduce live (static-confirmed, not executed).  (1) Headless escape keys off CALIBRATION-driven spot state, not raw BTN_START — tester_cmds.py:826-827 `both = self.spots.get("mouse1") and self.spots.get("mouse2")`. spots is set by set_spot (line 404-407) keyed by SPOT name. In _event (line 722): `spot = self.cal.get(f"{ta…

---

## 🟡 Confirmed — LOW (49)

| ID | W | Where | Issue | Fix |
|---|---|---|---|---|
| W0.0 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:134-160` | Reader thread accumulates an unbounded line buffer when the daemon emits a long line with… | Cap buffer (e.g. if buffer.size() exceeds a few MB with no newline, log a warning, clear it, and treat the stream as cor… |
| W0.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:414-430` | Blocking write() to the daemon's stdin pipe runs on the UI/render thread and can stall the… | Make mStdinFd non-blocking and treat EAGAIN as backpressure (drop/queue the request or fail it with EBACKEND_DIED after… |
| W1.0 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/GuiMadPanel.cpp:147-152, 411` | Panel can wedge all input if the daemon's input.lock 'locked:false' event is missed (no cl… | Add a client-side safety reset: clear mInputLocked whenever the panel regains topmost focus with no GuiMadCaptureModal o… |
| W1.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:446-486 (shutdownChild), reached from GuiMadPanel.cpp:423 delete this, :129 RETRY restart(), and poll() handleChildDeath` | Backend teardown does a synchronous ~2-4s blocking reap on the UI/render thread (panel clo… | Acceptable for a control panel; consider shortening the SIGTERM grace (e.g. 500ms before SIGKILL on the close path), or… |
| W2.0 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageGamepads.cpp:490-492` | Unguarded rapidjson GetString() on Wii accessory "allowed" array element | Mirror the other loops: `for (...) { if (allowedArr[i].IsString()) extAllowed.emplace_back(allowedArr[i].GetString()); }… |
| W4.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageModel2.cpp:172` | std::clamp with potentially inverted bounds in Model2 number steppers (theoretical UB if b… | Guard before clamping: `if (hi < lo) std::swap(lo, hi);` (or skip the setting), then clamp. Cheap and removes the UB edg… |
| W6.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadWiiBridge.cpp:51` | MadWiiBridge::spawn sets SIGPIPE to SIG_IGN process-globally and never restores it, defeat… | Have MadWiiBridge save the previous SIGPIPE disposition with sigaction(&prev) before setting SIG_IGN and restore it in s… |
| W6.2 | C | `/home/deck/esde-build/ES-DE/es-app/src/ApplicationUpdater.cpp:419-425` | ApplicationUpdater::compareVersions still can throw std::out_of_range from std::stoi on an… | Wrap the std::stoi in try/catch (catch std::exception, log + continue) or parse with std::strtol and validate the range,… |
| W7.2 | C | `/home/deck/esde-build/ES-DE/es-app/src/GamescopeFocus.cpp:65-76, 139-154` | No XSetIOErrorHandler — a broken X connection to the gamescope server terminates ES-DE via… | Install an XSetIOErrorHandler (and optionally XSetErrorHandler) that marks mEnabled=false and returns/longjmps instead o… |
| W8.0 | C | `/home/deck/Emulation/tools/launchers/romhack-art-urls.json:1-182` | romhack-art-urls.json is orphaned: no code path consumes it | Decide intent: (a) if a fetch/apply step exists outside the repo, add a one-line README/comment in the file pointing to… |
| W8.1 | C | `/home/deck/Emulation/tools/launchers/data/gp-defaults/:(directory listing)` | No baked default-positions file for the 'dualshock4' profile (every other key has one) | Add data/gp-defaults/gp-dualshock4-positions.json with the 17 stems positioned on the dualshock4-tester base art (mirror… |
| 0.3 | P | `controller-router-wrap.sh:19-34` | controller-router-wrap.sh silently succeeds (launches nothing) when the emulator command a… | After the final shift, assert there is a command: `[[ $# -ge 1 ]] || { echo "...: no emulator command after --" >&2; exi… |
| 0.4 | P | `quit-combo-watcher.py:75-82` | quit-combo button list from policy/env is int()-converted with no guard; a bad value crash… | Wrap the int conversions in try/except and fall back to DEFAULT_BUTTONS on failure, logging the bad value; skip non-nume… |
| 1.0 | P | `lib/policy.py:24-32` | load_merged()'s hand-rolled 2-level merge diverges from the router's recursive deep_merge… | Replace the bespoke loop with the same recursive merge the router uses (import routing.deep_merge or inline an identical… |
| 10.11 | P | `lib/devices.py:134,164-217` | _ENUM_CACHE module global is mutated by the watch thread and inline router/RPC calls with… | Guard _ENUM_CACHE access with a small module lock (the file already uses _SDL_LOCK as precedent), or build a fresh local… |
| 10.6 | P | `steam-fetch-metadata.py:252` | steam-fetch-metadata.py rewrites the gamelist via a single non-atomic write_text — a crash… | Write to a sibling temp file and os.replace() it onto GL (atomic same-dir rename), matching the project's established at… |
| 11.3 | P | `steam-fetch-media.py:93-97` | steam-fetch-media.py place() unlinks the existing artwork BEFORE copying the new one (non-… | Copy to a temp name first, then os.replace onto the final path, deleting the old differently-suffixed siblings only AFTE… |
| 12.1 | P | `steam-fetch-metadata.py:206-208` | steam-fetch-metadata.py crashes on any non-Steam launcher .sh (unguarded regex .group) | Mirror the siblings: `m = re.search(r"rungameid/(\d+)", Path(sh).read_text()); if not m: continue; rg = int(m.group(1))`… |
| 2.2 | P | `lib/madsrv/device_cmds.py:150-156` | devices.watch can latch a stale token if the watch stream thread dies, preventing restart | In devices.watch, validate the cached token is still live (rpc has the _STREAMS map; expose a helper or check stop_strea… |
| 2.3 | P | `lib/madsrv/backends_cmds.py:93,208-209,281` | Required request fields are accessed via params[...] producing EINTERNAL instead of a clea… | Use params.get(...) with an explicit RpcError("EINVAL", ...) for missing/invalid required fields, and coerce numeric fie… |
| 3.0 | P | `lib/madsrv/tester_cmds.py:228-230` | User calibration/position/P2 JSON files are written non-atomically (rule violation; trunca… | Route all of these through an atomic helper (write to path.with_suffix('.tmp'), then os.replace), matching lib/localpoli… |
| 3.1 | P | `lib/madsrv/tester_cmds.py:276-279` | P2 auto-detection matches a bare "2" token in any controller name | Drop the bare "2" from the auto set (keep "p2"/"ii"/"player2"), or require it adjacent to "player"/"#". The "ii" token i… |
| 3.2 | P | `lib/madsrv/tester_cmds.py:468-477` | Stickless pads (FC30) emit phantom lstick/rstick tokens in the snapshot | Only set_stick for a stick the pad actually has, e.g. guard with `if any(s.startswith('lstick_') for s in self.stems)` f… |
| 3.3 | P | `lib/madsrv/tester_cmds.py:826-839` | X-Arcade on-cabinet escape combo breaks if Start is re-calibrated | Track the raw P1/P2 BTN_START key state for the escape (as the Tk _xa_quit_check at lib/mad_xarcade_tester.py:728-729 do… |
| 4.2 | P | `router-config-gui.py:2459` | Camera-preview ffmpeg stderr log file descriptor is leaked on every preview start | Open the log with a context manager before Popen (as _run() does): 'with open(logdir / "sinden-preview.log", "ab") as lf… |
| 5.1 | P | `lib/cemu_cfg.py:130-131` | Cemu display_name/uuid substitution can corrupt XML or crash on device names with regex-re… | Use a function replacement to avoid backreference interpretation, e.g. `_DISPLAY_RE.sub(lambda m: m.group(1)+xml_escape(… |
| 5.2 | P | `lib/eden_cfg.py:82-97` | Eden _apply_player emits a duplicate \default line when the existing key has no value line | Add `base` to `seen` in the `\default` branch too (or track handled keys uniformly), so the append loop skips keys alrea… |
| 5.3 | P | `lib/rpcs3_cfg.py:166-167` | rpcs3 assign() does not guard yaml.safe_load against a corrupt/partial Default.yml | Wrap the load in try/except yaml.YAMLError, log a warning, and return 0 to leave the file untouched (consistent with the… |
| 6.1 | P | `lib/mad_backup.py:155-159` | reset_local() deletes the user's GUI-overrides file with unlink() — no recoverable _TMP mo… | Instead of unlink(), move LOCAL to a timestamped recoverable location (e.g. ~/Downloads/_TMP-mad-reset-<ts>/controller-p… |
| 6.2 | P | `deck-backup.sh:296-300` | deck-backup.sh auto-prunes old config archives with `rm` — deletes user backup data withou… | Move pruned archives to a _TMP/RECOVERY area instead of rm, or at least gate the prune behind a flag / print a clear war… |
| 6.3 | P | `lib/es_collections.py:49-113` | es_collections lru_cache is process-lifetime but mad-backend is a long-lived daemon — stal… | Either drop the lru_cache on these read paths (the calls are cheap), or expose a cache-clear hook the backend calls when… |
| 6.5 | P | `lib/mad_backup.py:102-130` | do_restore merges directory targets (dirs_exist_ok=True) instead of replacing — resurrects… | For dir targets, either clear the destination first (moving it to a recoverable _TMP, never rm) before copytree, or docu… |
| 6.6 | P | `deck-restore.sh:56-68` | deck-restore.sh extracts the config archive over $HOME with no check that ES-DE is not run… | Before extracting the config archive, detect a running ES-DE (pgrep -f es-de / ES-DE.AppImage) and warn/abort, instructi… |
| 7.1 | P | `lib/madsrv/daphne_cmds.py:191-203` | daphne.bind / _dp_bind_press leak proc on communicate() timeout without reaping | In the except branch after proc.kill(), call `try: proc.communicate(timeout=2) except Exception: pass` (or proc.wait(tim… |
| 8.4 | P | `sinden-smoother-tune.sh:24-28` | sinden-smoother-tune.sh drops the snap_threshold key on save, resetting it to the daemon d… | Read the current snap_threshold first (like CUR_ALPHA/CUR_DZ) and include `snap_threshold = $SNAP` in the heredoc, or me… |
| 8.8 | P | `sinden-camera.sh:13-49` | sinden-camera.sh edits the user's tuned LightgunMono.exe.config via `sed -i` with no backu… | Take a one-time backup (reuse sinden_cfg.backup_once semantics) before editing, and validate inputs are integers before… |
| 9.0 | P | `wire-bezels.py:66` | wire-bezels.py writes per-game bezel configs into non-installed RetroArch cores (operator-… | Add parentheses to express the intended logic, and drop the redundant tautological clause: `if c not in detected and (CF… |
| 9.5 | P | `steam-fetch-metadata.py:142-146` | steam-fetch-metadata.py permanently deletes existing cover when no portrait can be fetched… | Only delete the landscape cover after confirming a portrait was successfully obtained, OR move the bad cover to a recove… |
| 9.6 | P | `reorganize-cd-games.py:155-157` | reorganize-cd-games.py can leave a renamed game file orphaned as '<name>.__moving' if mkdi… | Wrap the rename/makedirs/rename sequence in try/except and on failure rename the temp back to the original name (or writ… |
| N0.1 | P | `/home/deck/Emulation/tools/launchers/router-config-gui.py:1870-1875 (quit) vs 1347-1357 (_clear Daphne teardown)` | Quitting MAD during a Daphne X-Arcade capture orphans the hypseus_capture SDL subprocess (… | Have App.quit() run the relevant teardown (kill `self._dp_proc` if set, like _clear does) before root.destroy(); simples… |
| N1.1 | P | `/home/deck/Emulation/tools/launchers/router-config-gui.py:2443-2446 (_cam_preview) -> _cam_apply_live 2411-2417 -> sinden_cfg.set_ctrl (lib/sinden_cfg.py:168-174); also _cam_set 2490-2502 on every slider step` | Camera preview start runs up to 4 synchronous v4l2-ctl calls + sinden-stop on the UI threa… | Move the pause+apply+launch sequence (and per-step set_ctrl) onto a worker thread that posts results back via self._ui_q… |
| N2.0 | P | `/home/deck/Emulation/tools/launchers/router-config-gui.py:73-86 (faulthandler/atexit only), 1870-1875 (quit), 4601-4606 (main)` | SIGTERM from ES-DE (or session) bypasses MAD's quit/_clear cleanup — Sinden left dead / LE… | Install a SIGTERM/SIGINT handler (and root.protocol('WM_DELETE_WINDOW', self.quit)) that routes through the same cleanup… |
| N3.0 | P | `/home/deck/Emulation/tools/launchers/model2-m2emu.sh:14,17,28-29` | model2-m2emu.sh: unguarded `sed -i` on a missing EMULATOR.INI aborts the whole launch unde… | Guard the sed: `[ -f "$INI" ] && case "$GAME" in ... esac` (or wrap the case in `if [ -f "$INI" ]; then ... fi`). The cr… |
| N3.2 | P | `/home/deck/Emulation/tools/launchers/supermodel.sh:4-6` | supermodel.sh: `param="${@}"` collapses all launch arguments into a single quoted word | Use the array form: drop the `param` collapse and call `/usr/bin/flatpak run com.supermodel3.Supermodel "$@"` (do the si… |
| N3.3 | P | `/home/deck/Emulation/tools/launchers/install-bezels.sh:48 (and install-bezels-all.sh:70)` | install-bezels.sh / install-bezels-all.sh create symlinks into the RetroArch overlays tree | Copy the .cfg/.png files into the overlay dir instead of symlinking (they are small), matching the no-symlink data-layou… |
| N6.2 | P | `lib/mad_gamepad_tester.py:384-389` | Tk Gamepad-tester P2 auto-assignment matches a bare "2" token in the pad name — false-posi… | Match P2 only on unambiguous tokens (drop bare '2'/'ii'; require 'p2'/'player2'/'player 2', or word-boundary 'ii' as a s… |
| N6.3 | P | `lib/madsrv/tester_cmds.py:881-891` | WiiTesterStream slot-claim handover sleeps 0.1s uninterruptibly after writing the claim fi… | Make the handover sleep interruptible and bail early: `if self.stopped.wait(0.1): return` (matching the pad/x-arcade pat… |
| N7.1 | P | `/home/deck/Emulation/tools/launchers/lib/madsrv/sinden_cmds.py:203 (and router-config-gui.py:2130)` | sinden.led_set RPC and the GUI LED toggle both rewrite the user's sinden.conf non-atomical… | Write via a sibling temp file and os.replace (the repo already has this pattern in lib/sinden_cfg.py:128 which uses tmp.… |
| N7.3 | P | `/home/deck/Emulation/tools/launchers/lib/mad_gamepad_tester.py:407, 668, 672, 1121 (and lib/mad_xarcade_tester.py:212, 600)` | GUI gamepad/xarcade testers write user calibration & position JSON non-atomically (same cl… | Funnel all these saves through one atomic helper (write to <file>.tmp in the same dir, then os.replace), matching the te… |

## ⚪ Confirmed — INFO (19)

| ID | W | Where | Issue | Fix |
|---|---|---|---|---|
| W0.2 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadJson.h:25-29` | RapidJSON parses daemon lines with the default recursive descent and no depth limit on the… | If hardening is desired, parse with kParseIterativeFlag (RapidJSON's non-recursive parser) to bound stack usage regardle… |
| W0.3 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadBackend.cpp:125` | spawn() reassigns mReaderThread without asserting it is non-joinable — a latent std::termi… | Add a defensive `if (mReaderThread.joinable()) mReaderThread.join();` (or an assert) at the top of spawn() so the invari… |
| W1.2 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadTheme.cpp:203` | MadTheme::color() uses STOCK.at(key) which throws if a future MadColor enum value is added… | Make the final fallback non-throwing (e.g. `auto it = STOCK.find(key); return it != STOCK.end() ? it->second : 0xFF00FFF… |
| W3.0 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageDaphne.cpp:22-25, 38, 57-72` | Stale Daphne session scope can briefly show a wrong/failed map on panel reopen (self-heals… | None required. If desired, the statics could be reset to global in the GuiMadPageDaphne destructor or validated against… |
| W3.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageBackends.cpp:675-705, 349-357` | GuiMadPageBackendDetail::mSuppressChildPopRefresh is set before the apply request; a never… | Acceptable as-is. Could be made fully robust by refreshing in onChildPopped unconditionally and letting the apply respon… |
| W5.1 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadStepper.cpp:90-91 (adjust)` | MadStepper auto-repeat divides (mMax-mMin)/mStep with no guard against mStep == 0 | In the MadStepper constructor clamp `mStep` to a tiny positive floor, e.g. `mStep {std::max(step, 1e-6f)}`, so adjust()… |
| W8.3 | C | `/home/deck/Emulation/tools/launchers/.github/workflows/build-appimage.yml:101` | Third-party release action pinned to a mutable major-version tag, not a commit SHA | Pin softprops/action-gh-release to a commit SHA with a trailing '# v2.x.y' comment, and consider pinning actions/checkou… |
| 0.5 | P | `quit-combo-watcher.py:111-117` | _quit backstop derives the KILL command by blind string replace of '-TERM'->'-KILL' | Either gate the backstop on the backend declaring it needs escalation, or build the KILL command explicitly rather than… |
| 1.3 | P | `lib/policy.py:22` | Two tomllib.load() calls leak the underlying file object (never closed) | Use a context manager: `with POLICY.open('rb') as f: base = tomllib.load(f)` in both spots. |
| 1.5 | P | `lib/policy.py:21-22` | load_merged parses the base controller-policy.toml with no error guard | Optionally catch TOMLDecodeError around the base load in load_merged() and raise an RpcError-friendly message (or surfac… |
| 3.4 | P | `joystick-button-detector.py:25-26` | Dead/duplicate JSIOCGNAME ioctl constant assignment | Remove the first JSIOCGNAME assignment (line 25). |
| 4.3 | P | `show-launchscreen.py:9-14` | show-launchscreen.py docstring claims a FocusOut-driven close that the code deliberately d… | Update the docstring (lines 9-14) to match reality: the splash is held until the game window covers it / the game-end ho… |
| 7.4 | P | `hypseus-pin.sh:58-61` | hypseus-pin.sh appends global-args via unquoted command substitution (intentional word-spl… | If desired, read the flags into a bash array (mapfile/read -a) instead of bare `$(cat)`, or set -f around the expansion… |
| 8.5 | P | `lib/madsrv/sinden_cmds.py:450-459` | camera.set RPC does not validate player ∈ {1,2} before indexing — KeyError on bad input | Add `if player not in (1, 2): raise RpcError("EINVAL", "player must be 1 or 2")` at the top of _camera_set (mirroring ca… |
| 8.7 | P | `mugen.sh:46-66` | mugen.sh creates symlinks into the game/data folders (external/, data/, font/) — runs agai… | Confirm this is the explicitly-OK'd exception; otherwise copy the bootstrap files instead of symlinking, or keep Ikemen'… |
| 9.9 | P | `skyscraper-apply.py:53-57` | skyscraper-apply.py uses bare except blocks that swallow art-copy failures silently | Catch the specific exceptions and at least print a one-line warning per failure, or accumulate a failed-copy count to su… |
| N2.1 | P | `/home/deck/Emulation/tools/launchers/router-config-gui.py:3839 (_set_sys), 4161 & 4175 (_slot_binding)` | GUI config-save helpers open the base policy / emulator config files without a context man… | Use `with open(...) as f:` (or pathlib read_text / `with POLICY.open('rb') as f: tomllib.load(f)`) to make the close det… |
| N4.1 | P | `/home/deck/Emulation/tools/launchers/lib/policy.py:24-32` | GUI load_merged vs router deep_merge: confirmed DORMANT on live files — divergence only re… | Optional hardening: have policy.load_merged() simply call routing.deep_merge() so the two never diverge even on a hand-e… |
| N7.2 | P | `/home/deck/Emulation/tools/launchers/steam-fetch-media.py:113, 134` | steam-fetch-media.py cdn_download/sgdb_download unlink existing artwork before writing the… | Write the new file to a temp name first, fsync/close, then unlink the old glob matches and rename — or move the old matc… |

---

## 🔎 Completeness-critic leads (20)

> Surfaced by the completeness-critic agents. **These were NOT run back through the per-finding adversarial verifier** — treat as strong leads to confirm, not hardened findings.

### [CC.2] (C) 🔴 high — Splash uses a raw SwitchComponent* in an async response while every sibling page that learned the lesson uses weak_ptr — confirms W4.0 is a…

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/pages/GuiMadPageSplash.cpp:181 (raw sc), 193-203 (async deref); contrast GuiMadPageBackends.cpp:510 weakChips, GuiMadPageSystems.cpp:282-287 lookup-by-key` · _memory-safety_
- **Impact:** Use-after-free / heap-write-after-free on a SwitchComponent if the user toggles a splash-pool entry and then changes MODE or FIT (which rebuilds the list) before the toggle response returns. Crash or memory corruption, reachable in normal use on the random-image splash page.
- **Fix:** Mirror Backends: capture std::weak_ptr<SwitchComponent> weakSc {switchComp}; in the response, auto sc{weakSc.lock()}; if(!sc) return; before setState. Or mirror Systems: re-find the live switch by name in mRows/the list.
- **Detail:** This corroborates and sharpens the existing W4.0 finding. The codebase has THREE switch/chip-toggle pages whose response callbacks may outlive the widget across a rebuild: (a) GuiMadPageBackends.cpp:510 uses std::weak_ptr<MadChipRow> with an explicit comment 'a raw pointer would dangle'; (b) GuiMadP…

### [C0.0] (P) 🔴 high — deck-post-update.sh runs samba-setup.sh as the user, but samba-setup.sh requires root and exits — Samba is never reinstalled after a SteamOS…

- **Where:** `/home/deck/Emulation/tools/launchers/deck-post-update.sh:129 (calls bash "$T/samba-setup.sh"); /home/deck/Emulation/tools/samba-setup.sh:16 (EUID guard), :22 (bare steamos-readonly), :33/:37/:46/:53 (bare pacman/install/systemctl/smbpasswd)` · _bug_
- **Impact:** The #1 advertised job of the post-SteamOS-update recovery (re-installing the update-wiped Samba file sharing) silently never happens. The user follows the documented 'after every SteamOS update, run deck-post-update.sh' flow, sees a 'done' banner, but loses SMB file sharing with only a buried 'returned nonzero' log line. esde-health-check…
- **Fix:** In deck-post-update.sh line 129, call samba-setup.sh with elevation: `sudo bash "$T/samba-setup.sh"` (matching how the rest of the script elevates per-command). OR make samba-setup.sh self-elevate like sinden-reinstall-deps.sh (re-exec itself via `sudo` if EUID!=0, and prefix its privileged commands…
- **Detail:** deck-post-update.sh is written to run AS THE USER and elevate individual commands with sudo (10 internal `sudo` calls; no root assertion at top; header says 'Needs sudo for the root bits'). Step 1/9 calls `bash "$T/samba-setup.sh"` with NO sudo. But samba-setup.sh line 16 is `[[ $EUID -eq 0 ]] || {…

### [C1.0] (P) 🔴 high — deck-post-update.sh reinstalls python-evdev/tk with pacman but never disables the read-only SteamOS root — the documented MAD recovery step…

- **Where:** `/home/deck/Emulation/tools/launchers/deck-post-update.sh:200 (step 7/9); whole script has 0 `steamos-readonly disable` calls` · _gap_
- **Impact:** After a SteamOS update wipes python-evdev/tk (a pacman-on-root scenario the script's own header lists as the reason it exists), the documented recovery (`deck-post-update.sh`, per CLAUDE.md rule 6) silently fails to restore the MAD panel's Python deps. MAD.sh / router-config-gui.py then won't launch (no tkinter/evdev) and the user is told…
- **Fix:** Wrap the step-7 pacman in `sudo steamos-readonly disable` before and `sudo steamos-readonly enable` after (mirroring install.sh:146-151), or run the install via sinden-reinstall-deps.sh's pattern. Also re-init the keyring if empty before this pacman since step 2 only inits it when mono is absent.
- **Detail:** Step 7/9 runs `sudo pacman -S --needed --noconfirm python-evdev tk` to restore the MAD GUI's wiped deps, but deck-post-update.sh never calls `sudo steamos-readonly disable` first. On SteamOS the immutable root is read-only by default right after an update (exactly when this script runs), so pacman f…

### [C1.1] (P) 🔴 high — deck-post-update.sh runs samba-setup.sh without sudo, but that script hard-exits unless EUID==0 — Samba is never actually restored after a S…

- **Where:** `/home/deck/Emulation/tools/launchers/deck-post-update.sh:129 (calls `bash "$T/samba-setup.sh"`); samba-setup.sh:16 gates on `[[ $EUID -eq 0 ]] || exit 1`` · _gap_
- **Impact:** Samba file sharing — listed by the script header as item 1 of what a SteamOS update wipes (root pacman) — is never reinstalled by the documented recovery tool. The user believes post-update restored sharing; it didn't. They must separately know to run `sudo bash ~/Emulation/tools/samba-setup.sh`.
- **Fix:** Call it with sudo from post-update (`sudo bash "$T/samba-setup.sh"`), or refactor samba-setup.sh to use per-command sudo like sinden-reinstall-deps.sh instead of gating the whole script on EUID==0.
- **Detail:** Step 1/9 of the post-update recovery invokes `bash "$T/samba-setup.sh"` with NO sudo. samba-setup.sh line 16 is `[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0"; exit 1; }`, so when run as the `deck` user it immediately prints 'Run with sudo' and exits 1. deck-post-update.sh just swallows…

### [C0.1] (P) 🟠 medium — samba-setup.sh disables read-only root and never re-enables it — leaves the immutable SteamOS root writable

- **Where:** `/home/deck/Emulation/tools/samba-setup.sh:22 (steamos-readonly disable); no matching 'steamos-readonly enable' anywhere in the file` · _robustness_
- **Impact:** After running samba-setup.sh directly (the documented invocation), the SteamOS A/B immutable root is left mounted read-write until the next reboot/update. That defeats the OS's tamper/corruption protection, lets stray writes land on the root that the next OS update will silently wipe, and can confuse later steamos-readonly state checks. L…
- **Fix:** Append `steamos-readonly enable || true` at the end of samba-setup.sh (after step 6), mirroring sinden-reinstall-deps.sh:80 and install.sh:151. Ideally guard it so it only re-enables if the script disabled it.
- **Detail:** samba-setup.sh line 22 runs `steamos-readonly disable || true` to allow the pacman install, but the script has NO `steamos-readonly enable` at the end (confirmed: grep for 'enable' matches only the systemctl line). Both sibling scripts that touch the immutable root balance it correctly: install.sh d…

### [C1.2] (P) 🟠 medium — MAD backend load_merged() crashes on a malformed base controller-policy.toml — taking down EVERY panel page (distinct file/surface from the…

- **Where:** `/home/deck/Emulation/tools/launchers/lib/policy.py:21-22` · _robustness_
- **Impact:** A single hand-edit typo in the base controller-policy.toml doesn't just break game launches (already filed) — it makes the entire MAD CONTROL PANEL non-functional: the first devices.scan errors, every Players/Priority/Systems/Backends page errors, and the user has no in-panel way to see or fix the cause. The panel is exactly where a non-t…
- **Fix:** Guard the base load like routing.deep_merge's callers should: wrap `tomllib.load` in try/except (tomllib.TOMLDecodeError, OSError) and fall back to `{"systems": {}, "backends": {}}`, emitting a structured error event so the panel can surface 'controller-policy.toml has a syntax error' instead of fai…
- **Detail:** load_merged() does `base = tomllib.load(POLICY.open("rb"))` with NO try/except (the local-overrides load below it IS guarded; the base is not). A TOML syntax error in controller-policy.toml raises TOMLDecodeError straight out of load_merged(). Verified by sandbox test in /tmp: feeding a corrupt base…

### [C1.3] (P) 🟠 medium — Media-fetch scripts mkdir -p straight into a hardcoded SD-card path with no mount check — when the SD is unmounted they write media into the…

- **Where:** `/home/deck/Emulation/tools/launchers/steam-fetch-media.py:43, 188 (mkdir); openbor-fetch-media.py:31,90,131-132,167 same pattern` · _data-loss_
- **Impact:** Silent root-partition fill (videos are tens of MB each) plus user-invisible media loss/duplication when an SD card is temporarily unmounted during a scrape. The user (non-technical) sees 'media fetched OK' yet ES-DE shows nothing, and the root FS quietly runs out of space.
- **Fix:** Before any mkdir/write, assert the SD root is a real mountpoint (`os.path.ismount('/run/media/deck/1tbDeck')`) or that the parent `downloaded_media` already exists as a directory ON the mounted card; abort with a clear message otherwise. Better: derive the media root from ES-DE's MediaDirectory sett…
- **Detail:** MEDIA/MED point at the literal `/run/media/deck/1tbDeck/downloaded_media/...` and the place/download paths do `(MEDIA/sub).mkdir(parents=True, exist_ok=True)` then copy/write files, with no check that `/run/media/deck/1tbDeck` is actually a mountpoint. On the Steam Deck `~/ROMs` and `~/ES-DE/downloa…

### [CC.0] (C) 🟡 low — MadWiiBridge claims a PR_SET_PDEATHSIG backstop that does not exist — EOF-on-stdin is the only lifeline

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/MadWiiBridge.cpp:68-82 (child fork, no prctl); comments at 88 and 122` · _robustness_
- **Impact:** Orphaned wii-nav-bridge.py process surviving ES-DE exit in the (uncommon) case EOF is not seen; the documented 'PDEATHSIG backup' that a reader would rely on is not implemented. No crash. Trivially fixable by adding prctl(PR_SET_PDEATHSIG, SIGTERM) in the child right after fork (Linux-only, which is the only target).
- **Fix:** In the child branch (after fork, before execlp) add: #include <sys/prctl.h>; prctl(PR_SET_PDEATHSIG, SIGTERM); — then the comments become true and EOF is genuinely a secondary path. Alternatively, correct the comments to say the sole mechanism is stdin EOF.
- **Detail:** The forked child (lines 68-82) execs python3 wii-nav-bridge.py without ever calling prctl(PR_SET_PDEATHSIG, SIGTERM). Yet two comments assert PDEATHSIG is the bridge's safety net: line 88 'PDEATHSIG would be its only lifeline' and line 122 'EOF ... PDEATHSIG is the backup.' The ONLY actual terminati…

### [CC.1] (C) 🟡 low — GuiMadCaptureModal::render() assumes mPanel is the gui directly beneath it on the Window stack

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/GuiMadCaptureModal.cpp:242-251 (render); pushed from e.g. GuiMadPagePlayers.cpp:128, GuiMadPageQuitCombo.cpp:322/724, GuiMadPagePreview.cpp:278` · _correctness_
- **Impact:** No current crash or visual bug. A future change that pushes any Gui above an open capture modal would produce a missing/incorrect backdrop. Latent coupling to Window's two-layer render assumption.
- **Fix:** Document the invariant at the render() override, or have the modal render unconditionally opaque (fill its own background fully) so it does not depend on re-rendering mPanel; long term, mark the modal so Window renders the full stack beneath it.
- **Detail:** The modal compensates for Window only rendering bottom+top gui by manually drawing mPanel first (mPanel->render(parentTrans)) then itself, to repaint the opaque panel that would otherwise be skipped. This is correct ONLY while the capture modal is the immediate top gui and the panel is immediately b…

### [CC.3] (C) 🟡 low — ApplicationUpdater non-numeric guard still lets std::stoi throw std::out_of_range on an oversized all-digit release number (corroborates W6.…

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/ApplicationUpdater.cpp:417-425` · _robustness_
- **Impact:** Startup abort if the published latest_release.json ever carries a release number above 2^31-1 (run numbers won't reach that, but a hand-edited or corrupted feed would). Defeats the stated goal of the guard ('a malformed releaseNum must not throw on the updater thread').
- **Fix:** Replace std::stoi with std::strtol/std::from_chars into a long with range handling, or wrap the comparison in try/catch and 'continue' on any exception. Same treatment for the Android androidVersionCode stoi if reachable.
- **Detail:** Confirming and adding precision to W6.2. The new guard at 417-419 rejects any release number containing a non-digit and 'continue's, but std::stoi(releaseType->releaseNum) at line 425 still throws std::out_of_range for an all-digit string larger than INT_MAX (e.g. '99999999999999'). compareVersions(…

### [C0.2] (P) 🟡 low — sinden-mpx-setup.sh hardcodes /dev/input/event27 for the P2 keyboard node instead of resolving a stable symlink

- **Where:** `/home/deck/Emulation/tools/launchers/sinden-mpx-setup.sh:69 (P2_KBD_NODE=$(readlink -e /dev/input/event27 ...))` · _correctness_
- **Impact:** Bounded: the code comment (66-68) notes the P2-keyboard reattach is 'not strictly required for Dolphin' and it fails gracefully (readlink -e returns empty if event27 isn't the keyboard → find_slave_by_node returns nothing → skip). So worst case the P2 gun's keyboard slave silently stays merged into the Virtual core keyboard rather than be…
- **Fix:** Add a udev SYMLINK for the keyboard interface in 99-sinden-lightgun.rules (e.g. match ATTRS{idProduct}=="0f39" with ENV{ID_INPUT_KEYBOARD}=="1" -> SYMLINK+="input/sinden-gun-p2-kbd") and resolve that symlink here, mirroring the mouse path at line 24.
- **Detail:** The P2 mouse node is resolved correctly via the udev symlink `/dev/input/sinden-gun-p2-event` (line 24). But the P2 keyboard node falls back to a literal `/dev/input/event27` (line 69). The 99-sinden-lightgun.rules file (confirmed: it only creates SYMLINK for js* and ENV{ID_INPUT_MOUSE} interfaces,…

### [C0.3] (P) 🟡 low — clean-manual-cruft.py --apply can silently clobber one recovered manual when two leftover-extension PDFs map to the same target

- **Where:** `/home/deck/Emulation/tools/launchers/clean-manual-cruft.py:60 (existence check at scan time) and 69-70 (shutil.move recover loop)` · _data-loss_
- **Impact:** Narrow (requires two same-stem multi-format manual files), and both inputs were 'wrong-named cruft' to begin with, so the practical loss is one redundant copy. Still a silent overwrite of a user file on the --apply path, against the house rule.
- **Fix:** Detect duplicate recover targets (group by dst) and route all-but-one to the _TMP move branch, or skip recover and move both to _TMP when a target collision is detected. Alternatively re-check `dst.exists()` immediately before each shutil.move and divert to _TMP if it now exists.
- **Detail:** The recover list is built per-system before any move; each entry's target is guarded by `not (mandir / f"{inner.stem}.pdf").exists()` (line 60) — evaluated at SCAN time. If a manuals dir holds two leftover-extension PDFs for the same game (e.g. `Game.bin.pdf` and `Game.cue.pdf`, both with inner.stem…

### [C0.4] (P) 🟡 low — supermodel-proton.sh leaves the X-Arcade pointer floated if proton is SIGKILLed (trap bypassed)

- **Where:** `/home/deck/Emulation/tools/launchers/supermodel-proton.sh:59 (xinput float), 63-68 (restore trap on EXIT INT TERM)` · _robustness_
- **Impact:** After a hard-killed Supermodel session, the X-Arcade trackball stops moving any cursor system-wide (floated) until the user reboots or manually reattaches — surprising for a non-technical user. SIGKILL-bypassing-trap is inherent to shell, but a self-healing reattach at the next launch would close the gap.
- **Fix:** At the START of supermodel-proton.sh (and/or in a shared sinden/x-arcade setup step run on every game launch), reattach any floating X-Arcade/Sinden slaves back to their masters before floating again — so a previous crashed session self-heals. Optionally drop a sentinel file and reattach it on the n…
- **Detail:** Before launching, the script `xinput float`s the X-Arcade pointer (line 59) to keep its trackball off the crosshair, and reattaches it to the core pointer via a `restore()` trap on EXIT/INT/TERM (63-68). SIGKILL (e.g. the policy's KILL-escalation quit path, an OOM kill, or `kill -9`) bypasses the tr…

### [C1.4] (P) 🟡 low — ES-DE wrapper re-extraction has no disk-space/partial-extract guard; on a full ~/Applications it falls back to FUSE-mount, silently re-intro…

- **Where:** `/home/deck/Emulation/tools/launchers/deck-post-update.sh:98-111 (rewrite_wrapper heredoc body, the re-extract block)` · _robustness_
- **Impact:** On a full /home (common when backups/cores accumulate) or a power-loss mid-extract, ES-DE silently reverts to the FUSE path and Steam games launched from it can hang forever (request_wait_answer deadlock). Hard for a non-technical user to diagnose since ES-DE itself still starts.
- **Fix:** Before extracting, check free space on ~/Applications against the AppImage size; if the fallback `exec "$IMG"` path is taken, log a visible warning (it's the deadlock-prone mode). Optionally validate the extracted AppDir (e.g. presence of usr/bin/es-de) before writing the stamp.
- **Detail:** The wrapper re-extracts the AppDir when the source AppImage's mtime:size stamp changes: `rm -rf "$APPDIR" "$TMP"` then extract into TMP then `mv "$SRC" "$APPDIR"`, writing the stamp only on a successful mv. If extraction fails (disk full on /home, interrupted), it does `[ -x "$APPDIR/AppRun" ] || ex…

### [C1.5] (P) 🟡 low — esde-health-check BUILD_ID gate cannot distinguish a fresh/never-configured Deck from an update-wiped one, and a successful manual restore t…

- **Where:** `/home/deck/Emulation/tools/launchers/esde-health-check.sh:30-42 (and the .last-os-build marker write at 37)` · _robustness_
- **Impact:** Mostly cosmetic but user-confusing: a first-run or a freshly-restored-but-not-yet-relaunched Deck shows a 'SteamOS update wiped your setup — run the restore' dialog that misdescribes the situation. Best-effort and never blocks launch, so low severity.
- **Fix:** Distinguish first-run (marker absent) from update (marker present but differs) and use a first-run-appropriate message; have deck-post-update.sh write .last-os-build on a fully-successful run so a completed restore clears the nag immediately.
- **Detail:** The check skips when BUILD_ID equals the recorded .last-os-build, else runs deck-post-update.sh --check. The marker is only written (line 37) when NOTHING is missing. Two edge cases: (1) On a fresh install where .last-os-build is absent, `cat` returns empty, BUILD_ID != '' so the full check runs and…

### [CC.4] (C) ⚪ info — MadTileGrid::render does not guard a degenerate (rounds-to-zero) clip rect, unlike its sibling MadScrollView

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadTileGrid.cpp:212-218` · _robustness_
- **Impact:** Cosmetic only and not currently reachable; tiles could bleed outside a zero-height grid. Worth matching MadScrollView's guard for robustness.
- **Fix:** Add the same clipDim < 1 early-return before pushClipRect, matching MadScrollView.
- **Detail:** MadScrollView::render (MadScrollView.cpp:77-82) explicitly checks 'if (clipDim.x < 1 || clipDim.y < 1) return;' with a comment that pushClipRect treats a zero dimension as 'extend to the screen edge', which would DISABLE clipping. MadTileGrid::render computes the same scale-aware dims at 213-214 and…

### [CC.5] (C) ⚪ info — GamescopeFocus::debugLog reopens the log file on every focus transition with no error handling on a power-loss-prone path

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/GamescopeFocus.cpp:16-26` · _robustness_
- **Impact:** Negligible. Log file grows without bound across many focus transitions over a long session; no functional impact.
- **Fix:** Optional: cap/rotate the log, or open once and keep the stream. Not required.
- **Detail:** debugLog opens an std::ofstream in append mode and closes it on every call. It is invoked on each focus gained/lost transition (hasFocus lines 174-178) and at init. This is on the render thread. The open/append/close per event is cheap relative to a frame and only fires on transitions (not per-frame…

### [CC.6] (C) ⚪ info — Build packaging is correct: all 62 MAD sources/headers are in es-app/CMakeLists.txt, X11 is linked for GamescopeFocus, and MAD_RELEASE_NUMBE…

- **Where:** `/home/deck/esde-build/ES-DE/es-app/CMakeLists.txt:es-app/CMakeLists.txt:52-80 (headers), 138-167 (sources), 215-220 (X11); CMakeLists.txt:329-336 (MAD_RELEASE_NUMBER); .github/workflows/build-appimage.yml:86-99 (MAD_RELEASE_NUMBER env), 100-148 (build/publish)` · _gap_
- **Impact:** None — documents that the build/link/bundle wiring is sound and complete, so a broken-link or missing-source class of bug is ruled out.
- **Fix:** No change needed. (Optional hardening already captured by W8.3.)
- **Detail:** Positive confirmation, not a defect. I cross-checked every new .cpp/.h against the diff --name-only list: all 30 MAD .cpp and 31 MAD .h files plus GamescopeFocus.cpp/.h are listed in ES_SOURCES/ES_HEADERS. X11 is found+linked only in the Linux else() branch that builds the es-de target (find_package…

### [CC.7] (C) ⚪ info — FileData game-end event param count (producer) matches ViewController consumer at the new size 6 — the 5th scripting arg patch is internally…

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/FileData.cpp:FileData.cpp:2256-2264 (producer, 6 emplace_back); views/ViewController.cpp:1265-1269 (consumer, size()==6, arg index [5]); Scripting.cpp/h (5-arg fireEvent)` · _correctness_
- **Impact:** None — rules out a vector-size mismatch / out-of-bounds on the game-end scripting path that the size constant change could have introduced.
- **Fix:** No change needed.
- **Detail:** Positive confirmation. The patch adds a 5th script argument (launched-from custom collection) to game-start/game-end. I verified the deferred-fire path: FileData::launchGame for runInBackground pushes exactly 6 strings into gameEndEventParams (romPath, name, system, fullName, collection), and ViewCo…

### [CC.8] (C) ⚪ info — PDFViewer standalone (null-FileData) path is fully null-safe — no residual mGame-> deref reachable from the new path/title overload

- **Where:** `/home/deck/esde-build/ES-DE/es-app/src/PDFViewer.cpp:78-82 (guarded mGame deref), 211-218 (new overload), 229-234 (launchMediaViewer guard), 723-728 (getHelpPrompts guard), input->776 launchMediaViewer` · _correctness_
- **Impact:** None — rules out a null-pointer crash on the new PDF feature, which was the obvious risk when a path-based open reuses the game-based code with a null FileData.
- **Fix:** No change needed.
- **Detail:** Positive confirmation of the new Window::PdfHandler pure-virtual overload startPDFViewer(path,title) (added to the abstract interface in Window.h, implemented only by PDFViewer, so no missing-override compile break). I grepped every mGame use: line 80-81 deref is now guarded by 'if (game != nullptr)…

---

## ⚖️ Contested / needs-judgment (9)

| ID | W | Where | Issue | Status (was) |
|---|---|---|---|---|
| 10.0 | P | `lib/madsrv/rpc.py:24,147-156` | mad-backend teardown never shuts down the slow-method ThreadPool, so a SIGTERM/EOF can han… | uncertain (was high) |
| 8.3 | P | `install-bezels.sh:74-82` | install-bezels.sh / install-bezels-all.sh overwrite per-game RetroArch <game>.cfg with `ca… | uncertain (was medium) |
| 10.1 | P | `lib/madsrv/device_cmds.py:121` | Preview's active Wiimote probe writes to DolphinBar hidraw slots that the wii-nav-bridge h… | uncertain (was medium) |
| 10.3 | P | `controller-router.py:103-111` | controller-router warning dialog defaults to Cancel (abort launch) on ANY subprocess failu… | uncertain (was medium) |
| 6.4 | P | `esde-splash-gen.sh:26-37, 101-105` | esde-splash-gen.sh silently deletes splash.svg if the tomllib parse transiently fails on a… | uncertain (was low) |
| 7.0 | P | `wii-monitor.py:127-130` | wii-monitor.py busy-loops and never drops a slot on remote-disconnect EOF | uncertain (was low) |
| 7.2 | P | `singe-indexer.sh:33` | singe-indexer.sh framefile path parsing truncates at first space | uncertain (was low) |
| 9.8 | P | `convert-pixel-systems.py:101-113` | convert-pixel-systems.py overwrites per-system theme.xml with a fixed template, discarding… | uncertain (was low) |
| N6.4 | P | `lib/madsrv/tester_cmds.py:140` | Sysfs uevent reads in the DolphinBar slot-ranking loops leak the file object until GC (ope… | uncertain (was info) |

---

## ✖️ Refuted — dismissed with reason (24)

> Flagged by an earlier pass but knocked down on re-verification (unreachable, correct-by-design, or ownership-safe). Listed so they are not re-investigated.

| ID | W | Where | Claim | Why refuted |
|---|---|---|---|---|
| 2.0 | P | `lib/madsrv/model2_cmds.py:83,224-234` | model2.set lets FullScreenWidth/Height be set to an unvalidated raw string, corr… | Mechanism exists but is NOT reachable in normal use, so the medium rating is unjustified.  Reachability (the only caller): the single client of model2.set is the local C++ GUI GuiM… |
| 11.7 | P | `reorganize-cd-games.py:155-170` | reorganize-cd-games.py moves user ROMs via os.rename/shutil.move with no recover… | The factual claims are partly true but the MEDIUM rating and implied data-loss are not supported. File: /home/deck/Emulation/tools/launchers/reorganize-cd-games.py.  CONFIRMED FACT… |
| 1.1 | P | `lib/madsrv/policy_cmds.py:43-48` | set_system_flag computes the 'revert' default from the system's own base entry w… | lib/madsrv/policy_cmds.py:43-48 does read the system's OWN base entry without resolving inherits (factually true). BUT this is correct-by-design, not a bug: (1) The revert is meant… |
| 1.2 | P | `lib/madsrv/policy_cmds.py:187` | set_backend_list_member is_int branch sorts a mixed-type list instead of coercin… | lib/madsrv/policy_cmds.py:187-188 `if params.get('is_int'): cur = sorted(set(cur))`. The 'mixed-type sort' never occurs: is_int=True is passed by exactly one caller (router-config-… |
| 1.4 | P | `lib/localpolicy.py:77-86` | localpolicy.dump silently drops any top-level NON-dict value from the overrides… | lib/localpolicy.py:77-86 dump() iterates data.items() and only emits `if isinstance(tbl, dict)`, so a TOP-LEVEL non-dict value would be dropped (factually true). BUT no code path e… |
| 4.1 | P | `lib/gui_sound.py:104-108` | Nav-sound playback leaks zombie child processes for the GUI's lifetime | gui_sound.py:104-108 does subprocess.Popen(cmd,...) fire-and-forget with no .wait()/.poll(). The title's claim 'leaks zombie child processes for the GUI's lifetime' is INACCURATE.… |
| 8.6 | P | `supermodel-proton.sh:63-67` | supermodel-proton.sh restore() trap: `&&...||` precedence can skip the X-Arcade… | supermodel-proton.sh:63-67 uses the standard guarded idiom `[ -n A ] && [ -n B ] && cmd || true`. Bash left-associates && / || at equal precedence: when the guards are non-empty th… |
| 9.7 | P | `steam-fetch-metadata.py:179-192` | steam-fetch-metadata.py rebuild_block silently drops gamelist tags that aren't s… | The regex at steam-fetch-metadata.py:179 `<(\w+)>(.*?)</\1>` only fails on self-closing or attribute-bearing tags. ES-DE never emits those: MetaData.cpp:127-149 appendToXML writes… |
| 10.2 | P | `lib/madsrv/device_cmds.py:147-163` | devices.unwatch / devices.watch have an unsynchronized token swap that can leak… | device_cmds.py:150 `@method("devices.watch")` and :159 `@method("devices.unwatch")` are declared WITHOUT slow=True. Per rpc.py dispatch (lines 81-85), non-slow methods run inline v… |
| 10.4 | P | `quit-combo-watcher.py:120` | quit-combo-watcher / wiimote-quit-watcher run a policy-derived quit_cmd via shel… | quit-combo-watcher.py:120 and wiimote-quit-watcher.py:119 run the quit command via subprocess.run(cmd, shell=True). The command originates from the user's own trusted local config:… |
| 10.8 | P | `lib/madsrv/tester_cmds.py:963-977,985-998` | WiiTesterStream STOP->START restart can leak the previous tester's hidraw slot r… | tester_cmds.py: self.reader is per-instance (init :871, assigned :891, stopped only in own cleanup :964-966). A STOP->START's new WiiTesterStream gets its OWN reader, so the old cl… |
| 10.9 | P | `lib/madsrv/backup_cmds.py:138-154,179-191` | RunFullStream releases _RUN_ACTIVE in run()'s finally even when start() failed —… | backup_cmds.py:46 _RUN_ACTIVE is a threading.Lock. acquire() is in _backup_run_full (line 181); the two release() sites are mutually exclusive: if RunFullStream(argv).start() (Stre… |
| 10.10 | P | `lib/madsrv/tester_cmds.py:756-781,805-807` | X-Arcade tester opens nodes then grabs in a loop; a grab failure after opening e… | tester_cmds.py:778-781 — on zero opened nodes it does NOT silently no-op: it emits {"ended":"no_device","message":"No X-Arcade nodes found — is it connected and in Xbox mode?"} the… |
| 11.8 | P | `steam-fetch-metadata.py:38` | steam-fetch-metadata.py MEDIA dir is hardcoded to /run/media/deck/1tbDeck — sile… | `ls -d /run/media/deck/1tbDeck` => EXISTS (drwxr-xr-x deck deck, Jun 13). The card on THIS machine IS named 1tbDeck. /run/media/deck/1tbDeck is a documented project constant (CLAUD… |
| 12.6 | P | `lib/madsrv/daphne_cmds.py:282` | daphne.build_index passes an RPC-supplied arg as a ROM path segment (path traver… | Not a security hole. mad-backend.py:5 — the JSON-RPC transport is stdin/stdout PIPES spawned by ES-DE's local MadBackend.cpp; there is NO network socket (no AF_INET/bind/listen). T… |
| 2.4 | P | `lib/madsrv/systems_cmds.py:79-93,260-270` | art.resolve resolves attacker-controlled relative names with no path-traversal c… | systems_cmds.py:79-93 resolve_art does p=base/nm then p.is_file(); no '..' containment — literally true. But the 'attacker-controlled' security framing is unfounded: (1) the `names… |
| 5.4 | P | `lib/eden_cfg.py:167` | Eden/xemu blindly trust section_body parse; an existing [Controls]/[input.bindin… | eden_cfg.py:167 `body = inifile.section_body(text, 'Controls') or ''`. Reproduced in /tmp with inifile: text='[UI]\nfoo=bar\n'; section_body(text,'Controls') -> None -> ''; after a… |
| 7.3 | P | `wii-nav-bridge.py:294-300` | wii-nav-bridge rescan re-arm cooldown only applies to slots not currently in sel… | wii-nav-bridge.py rescan() lines 283-300: a slot only gets `self._disarmed[node] = monotonic()+REARM_COOLDOWN_SEC` inside `if node not in self.readers` (i.e. a newly (re)appearing… |
| N4.0 | P | `/home/deck/Emulation/tools/launchers/controller-policy.local.toml:137-144` | Per-system [quit_combo.*] overrides for router_skip/HID systems (daphne, ps3, ps… | Claim is factually wrong. router_skip only gates INPUT routing (controller-router.py:315, the 05-controller-router-standalone hook), NOT the quit-combo watcher. The watcher is star… |
| N5.0 | P | `/home/deck/esde-build/ES-DE/es-app/src/FileData.cpp:2205-2207` | wii-nav-bridge resumes DolphinBar writes immediately for runInBackground launche… | Code premise is partly real but the IMPACT is unreachable on this deck. FileData.cpp:2205-2207 wraps launchGameUnix(command,startDir,runInBackground) between pause()/resume(); for… |
| N6.1 | P | `lib/madsrv/tester_cmds.py:188-202` | gamepads.list LIVE DolphinBar probe writes report-mode bytes into hidraw slots t… | The concurrency the finding describes is real and reachable (no game + no tester claim => bridge holds slot, picker open => gamepads.list probes the same node, tester_cmds.py:188-2… |
| W5.0 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadSpriteCanvas.cpp:169 (nudgeSelected), 218 (render)` | MadSpriteCanvas indexes mItems[mSelection] guarded only by !empty(), not by mSel… | MadSpriteCanvas.cpp nudgeSelected (169) and render (217-218) index mItems[mSelection] guarded only by !mItems.empty(), unlike selectedKey (158-163) which also checks 0<=mSelection<… |
| W5.2 | C | `/home/deck/esde-build/ES-DE/es-app/src/guis/mad/widgets/MadChipRow.cpp:130-138 (input 'a' handler)` | MadChipRow::input passes a reference into mEntries to mOnToggle as the last stat… | MadChipRow::input (MadChipRow.cpp:129-138): mOnToggle(entry.chip.value, ...) is the LAST statement before `return true` and `entry` is never touched after the call, so even a synch… |
| W8.2 | C | `/home/deck/Emulation/tools/launchers/.github/workflows/build-appimage.yml:81-85` | AppImage recipe has no 'set -e'; CI relies solely on the post-build test -f guar… | Premise is wrong: the workflow has no shell:/defaults: override (grep confirmed), so every run: block uses GitHub Actions' default Linux shell `bash --noprofile --norc -e -o pipefa… |

---

## 👁️ On-device verification checklist (needs a human + display)

- [ ] **[W7.0]** On a fresh/empty install (no ROMs or invalid `es_systems.xml`), open the Steam overlay/QAM while the no-games dialog shows — confirm the null-deref crash (and that the guard fix stops it).
- [ ] **[W4.0]** Splash random-pool page: toggle a pool image, then cycle MODE/FIT before the response lands — ideally under ASAN — to confirm the use-after-free window.
- [ ] **[N0.0]** Confirm the Sinden lightgun drives stray menu navigation and that trigger-to-quit is dead.
- [ ] **[4.0 / N0.1 / N1.0]** Quit MAD from the camera-tuning page — confirm a stray `ffmpeg` is left and the Sinden stays dead; check the preview 'live but frozen' case.
- [ ] **[8.0]** Confirm whether anything actually invokes `model-2-emulator.sh` (the refuter says the live launcher is `model2-m2emu.sh`); if nothing does, this is dead-code cleanup, not a bug.
- [ ] **[C1.0 / C1.1 / C0.0]** Run `deck-post-update.sh` after a real SteamOS update — confirm Samba is NOT restored and the `python-evdev`/`tk` pacman reinstall fails (read-only root).
- [ ] **[N2.0]** Have ES-DE kill MAD (not the in-app combo) and confirm whether the Sinden is left dead / cleanup is skipped.

---

## Coverage gaps & caveats

**Python/shell (waves 1–2):**

Things NOT verified by this review that the user must confirm on-device or that remain open:  ON-DEVICE / HARDWARE (Claude has no display and cannot grab live input): - All GUI behavior in router-config-gui.py (MAD panel) must be eyeballed: confirm [N0.0] the Sinden lightgun really does drive stray menu nav and that trigger-to-quit is dead; confirm [4.0/N0.1/N1.0] that quitting from the camera page leaves the Sinden dead / ffmpeg orphaned / feed frozen-but-'live'. These were code-confirmed but the on-screen symptom needs a human. - [8.0] model-2 crosshair: launch bel/gunblade/rchase2 and confirm the crosshair toggle is in fact non-functional (the sed is provably dead, but the in-game effect needs the user). - [N6.0/3.3] X-Arcade on-cabinet escape combo (P1+P2 Start) after re-calibration — needs the physical X-Arcade. - [N2.0] SIGTERM-from-ES-DE cleanup gap: confirm whether, after ES-DE kills MAD (not the in-app combo), the Sinden is left dead / LED stuck. Needs a real ES-DE exit.  NOT EXERCISED END-TO-END: - The actual data-loss on the gamelist writers (N7.0/10.5/11.1/11.2/9.2) was confirmed by STATIC reading of the write + absence of a guard, not by running each script with ES-DE up and watching the clobber. The mechanism is sound and matches the documented ES-DE behavior, but no full clobber was reproduced. - [11.4/9.5] cover deletion: the unlink path is confirmed, but it was not run against a real Steam fetch with the network down — recommend the user simply not run steam-fetch-metadata offline until fixed. - mad-backend concurrency ([0.2/10.11/2.1]): confirmed by code (unlocked shared state) but no race was forced under real concurrent RPC load; impact is probabilistic.  DELIBERATELY OUT OF SCOPE / UNREVIEWED: - The patched ES-DE C++ fork under /home/deck/esde-build…

**C++/config (wave 3):**

STATIC-ONLY — no build/run was performed (per scope). What still needs a real build or on-device check: (1) Compile/link verification: CMake wiring looks correct (CC.6 — all 62 MAD sources/headers listed, X11 linked for GamescopeFocus, MAD_RELEASE_NUMBER threaded CMake<->CI<->updater), but this was reasoned statically, not compiled; a real build is the only proof there are no link/ODR/missing-symbol errors. (2) W7.0/W6.0 crash repro: needs an on-device run in the actual no-ROMs / invalid-systems empty-gamelist state, then toggle the Steam overlay/QAM, to confirm the null-deref fires (and that the fix prevents it) — cannot be observed headlessly (no display). (3) W4.0 use-after-free: needs ASAN or a timing repro (open Splash page, toggle a pool image, switch MODE/FIT before the response lands) — static analysis confirms the dangling capture but not the exact race window; an ASAN build would settle it. (4) W1.0 / W1.1 / W0.1 UI-thread stalls: the ~2-4s reap, missed-input.lock wedge, and blocking-write frame stall are bounded by reasoning but their real-world visibility (does the panel visibly freeze? does input truly wedge?) needs an on-device test with the daemon killed mid-request. (5) The Python-side [2.1] RMW concurrency question above is OUT OF THIS WAVE'S SCOPE — it requires reading mad-backend.py's dispatch/threadpool to decide whether pipelined overlapping calls actually hit the set_ra_option read-modify-write. (6) MadWiiBridge child-lifetime (CC.0): the claimed PR_SET_PDEATHSIG backstop is absent; whether the Wii bridge child is correctly reaped on an ES-DE crash (vs. EOF-on-stdin being the only lifeline) should be checked live. (7) The launchers gaps (W8.0 orphaned JSON, W8.1 missing dualshock4 defaults) were reproduced by execution but the user-visible impact (…

---

## Provenance

- **Wave 1** (`wf_ab7ab092-ddb`, 23 agents): recon → 10 slice finders + 3 gap-hunters → batched verify → synthesis.
- **Wave 2** (`wf_ef1f8bf2-70d`, 76 agents): strict per-finding executing skeptics + deepen finders (GUI regions, all shell wrappers, policy content, C++ protocol, high-churn, systemic grep) + 2 completeness critics. Corrected wave 1 (21 refuted).
- **Wave 3** (`wf_140700e1-b8c`, 21 agents): ES-DE fork patch review (`base/v3.4.1..HEAD`, 84 files) with C++ lenses + folded-in launchers config/data/CI finder + seam cross-check.
- Raw per-wave findings JSON: `review-findings/wave{1,2,3}-raw.json` (full evidence text, refuter reasoning, coverage notes).

