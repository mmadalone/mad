
# MAD Codebase Adversarial Audit — Final Report

## Executive Summary
Overall health is **solid**: across 30 audited units the codebase consistently applies atomic writes, one-time backups (rule #5), and revision-cached RPC. Confirmed defects: **2 HIGH, 19 MED, ~50 LOW** (HIGH/MED below; LOW condensed). Three themes recur: (1) **writer asymmetry** — a correct safe pattern (atomic temp+rename, `ensure_bak`, anchored sentinel) exists in-module but a sibling writer skips it; (2) **lock-free read-modify-write under the 4-worker RPC pool** (lost-update / TOCTOU on shared config + `_active`); (3) **reader does not mirror writer invariants** (empty/malformed hand-edited config slips past GUI guards). Genuinely clean units: **cpp-pages-stateful, cpp-pages-misc, sweep-cpp-lifetime** (0 defects); the C++ GUI layer overall is robust (only 2 confirmed, both bounded-UX). Note: many MED items were re-rated LOW by the second skeptic on **triggerability** (manual-config-only or hardware-unreachable) — flagged inline.

## CONFIRMED HIGH

**Bezel reassign clobbers a hand-made cfg in place (House Rule #5 data loss)** — `lib/bezel_cfg.py:722`
`assign_bezel()` gates the `_TMP` backup on a **bare substring** test (`SENTINEL not in existing and "wire-bezels" not in existing`) instead of the anchored `_is_tool_generated()` that `install()` uses at line 314. A hand-edited per-game cfg whose content merely contains `# bezelproject` (header comment) or `wire-bezels` (anywhere, incl. an overlay path) is misclassified as tool-generated, skips the `_TMP` move, and is permanently overwritten via the MAD Bezel reassign picker. **Fix:** replace line 722 with `if not _is_tool_generated(existing):`, mirroring `install()`. *(Partly mitigated: the finding's two example vectors don't actually fire — `# tweaked, not the bezelproject default` and a plain path value lack the exact `# bezelproject` marker — so the live trigger is narrower than stated; second skeptic re-rated MED. Still a real no-backup overwrite.)*

## CONFIRMED MED

**switch_bind transient-restore sidecar written non-atomically — corrupt sidecar permanently strands the ES-DE pad bind in shared config** — `lib/switch_bind.py:224-226`
The only non-atomic write in the module (`side.write_text(json.dumps(...))`); every other writer uses `fsutil.atomic_write*`. A kill/power-loss mid-write leaves truncated JSON; `restore_target()` logs the `JSONDecodeError` but neither reverts the config nor unlinks the sidecar, and `bind()` only re-snapshots `if not side.exists()`, so the broken sidecar wedges both restore and re-snapshot forever — the ES-DE-time PCSX2/Ryujinx bind leaks into the next Steam-UI launch. **Fix:** `fsutil.atomic_write_text` for the sidecar + unlink-on-parse-failure in the except. *(One skeptic notes the write completes before `os.execvp`, so the normal quit-combo `pkill` cannot hit the window — only SIGKILL/OOM/power-loss at launch; re-rated LOW on that basis.)*

**pcsx2x6 USB Type / gun-binding ini edits write `PCSX2.ini` with no one-time backup (rule #5)** — `lib/madsrv/pcsx2x6_input_cmds.py:232, 265`
`_usb_set`/`_selector_set` call `cfgutil.atomic_write(_INI, new)` with no preceding `ensure_bak`/`ensure_pristine_backup`, unlike every peer writer of the same file (`apply_set` →`ensure_bak`; `assign_devices`→`ensure_pristine_backup`). A user's first-ever portable-ini mutation via the Namco input page leaves the original unrecoverable. **Fix:** `cfgutil.ensure_bak(_INI)` before both `atomic_write`s. *(This appears 3× across units `emu-cmds-ps`, `sweep-rpc`, `cfg-pcsx2` — same defect; dedup to one fix. The `cfg-pcsx2` instance is filed LOW because the file is PCSX2's own self-rewriting ini and the common `pcsx2x6.set` path does create the `.bak`.)*

**Restore/reset overwrite config without bumping `staterev('config')` — MAD pages serve stale cached data after Restore** — `lib/mad_backup.py:213, 248, 260`
`do_restore`/`restore_router_backups`/`reset_local` use raw `shutil.copy2`/`recoverable_delete` (no `staterev` import), so the rev-cache (`pads.get`, `preview.*`, every `*.input_get`, all `cache=('config',…)`) keeps returning pre-restore data for the rest of the MAD session. Files on disk are correct; the panel shows wrong bindings and the success message lies. **Fix:** `staterev.bump('config')` after each successful restore/reset (over-bump is documented safe).

**Supermodel USB-path ordering uses lexical string sort → can swap P1/P2 gun bindings** — `supermodel-sinden-smart.py:217`
`sorted(paths.keys(), key=lambda p: paths[p])` sorts USB paths as strings, so port `.10` sorts before `.2`, reversing topological order vs ManyMouse's numeric enumeration → `InputGunX/Trigger` for P1 written with P2's mouse (wrong-player in-game). **Fix:** numeric split key `[int(x) for x in re.split(r'[-.]', paths[p])]`. *(Re-rated LOW: the live rig has only 4/6-port hubs, no two-digit port segment exists, so the trigger is hardware-unreachable today.)*

**resolve_system() raises AttributeError on a scalar `[systems.*]` entry — aborts the game launch** — `lib/routing.py:100`
A valid-TOML-but-misshapen policy (`nes = "arcade"` under `[systems]`) makes `entry.get("inherits")` throw; uncaught up through `_setup`→`controller-router-wrap.sh` (`set -e`) **aborts the emulator launch with a traceback**, contradicting the module's own documented fail-soft contract (adjacent guards already handle 3 other malformed shapes). **Fix:** `isinstance(entry, dict)` guard + stderr + `break`, matching the existing degrade pattern. *(Re-rated LOW: only reachable via hand-edited base TOML; the GUI/machine writer always emits sub-tables.)*

**Empty quit-combo makes `combo <= cur` always true → emulator quits on first input** — `quit-combo-watcher.py:227`
A hand-edited `buttons = []` (global or per-system) yields `combo = set()`; `set() <= cur` is always True, so after `hold` seconds the watcher kills the emulator with **no button held** on the first stray evdev event. The RPC writer guards empty (`EINVAL`) but the reader does not. **Fix:** treat empty as no-op / fall back to `DEFAULT_BUTTONS`. *(Re-rated LOW: reachable only via unsupported manual edit; both shipped configs carry valid combos.)*

**Tester `tester.start` unlocked check-then-set on `_active["stream"]` leaks a stream on concurrent starts** — `lib/madsrv/tester_cmds.py:1025-1037`
Two near-simultaneous `tester.start` (rapid double-tap, or START-then-CALIBRATE; both `slow=True` on the 4-worker pool) read the same prior token and both `start()` a stream; the second assignment overwrites `_active`, orphaning the first stream's evdev grab/hidraw reader until daemon teardown. The `== self.token` cleanup guards don't prevent the orphaned grab. **Fix:** serialize read-stop-create-publish under one lock. *(Second skeptic: the two streams contend on `dev.grab()` so the worst "two readers" case is averted and recovery happens on panel close; re-rated LOW.)*

**non-Steam `rungameid` built by zipping two independent regex lists → wrong appid→name → launcher boots the WRONG game** — `steam-collection-gen.py:101-115`
`appids` and a **lowercase-only** `names` scan are paired by position via `zip`; a capitalised `AppName` (which Steam tooling does write — sibling scripts use `re.I` precisely for this) or any block missing a name truncates `zip` and shifts every later pair, writing a launcher with another game's `rungameid`. Latent today (100/100 match on-device). **Fix:** parse `shortcuts.vdf` structurally per-block (reuse `_vdf_entries`); at minimum case-insensitive scan + length assert.

**Missing `.openbor` manifest raises FileNotFoundError and aborts the whole media run** — `openbor-fetch-media.py:67`
`appid_for` does an unguarded `read_text()`; a gamelist entry whose manifest was deleted/renamed kills art-fetching for every remaining game in the batch (no try/except in `appid_for`, `copy_art`, or the loop). **Fix:** `try/except FileNotFoundError/OSError → return None`, or `is_file()` guard. *(Re-rated LOW: internal re-runnable maintenance script, self-diagnosing crash naming the file.)*

**Model2 `sed -i` targets literal file `M2CONFIGFILE` (missing `$`) — lightgun crosshair toggle is a dead no-op** — `model-2-emulator.sh:30,33` (finding cited 679/682; file is 72 lines)
The bareword `"M2CONFIGFILE"` is never a defined variable; with no `set -e` the `sed` errors silently against ES-DE's launch cwd, so the `DrawCross` enable/disable never happens, and a file literally named `M2CONFIGFILE` in cwd would be clobbered. Runs on essentially every Model2 launch. **Fix:** define and use `"$M2CONFIGFILE"` (real ini path) guarded by `[ -f ]`. *(Impact is cosmetic crosshair-only; defensible as LOW, MED held because the feature is fully broken.)*

**`"axisname"` capture mode cannot be cancelled with B once armed (stuck up to 15s)** — `esde-build/.../GuiMadCaptureModal.cpp:34`
`mCancelAnytime {mode == "axis" || mode == "pointer"}` omits `"axisname"` (the analog-stick binder's mode), and axisname can never legitimately capture B (backend returns nothing for `EV_KEY`), so B is swallowed until the daemon's 15s timeout while the help prompt says "B = cancel". **Fix:** add `"axisname"` to the set. *(A live "auto-cancels in Ns" countdown is shown, so it self-resolves visibly; second skeptic re-rated LOW.)*

**Non-atomic write to the user's `es_systems.xml` (no temp+rename, no validate, no backup)** — `lib/mad_launch_wrap.py:85`
`path.write_text(t2)` directly onto `custom_systems/es_systems.xml`; a kill/power-loss/disk-full mid-write truncates it and ES-DE silently drops **all ~195 custom systems**. The sibling `es_systems_wrap._atomic_write` does `ET.fromstring` validate + tmp + `os.replace` + `.bak` for the identical file. Called only from `install.sh`/`deck-post-update.sh` (not per-launch). **Fix:** mirror `es_systems_wrap._atomic_write`.

---
Plus four MED items that **both skeptics or the second skeptic re-rated LOW on triggerability** (real defects, narrow/latent triggers):
- **Per-game bezel cfg writes non-atomic** (`bezel_cfg.py:325,726`) — direct `write_text` vs the module's own `_set_enable_in` atomic pattern; regenerable cfg, hand-made already moved to `_TMP` first. **Fix:** temp+rename helper.
- **hypinput KEY-line edit drops trailing whitespace/CR** (`lib/hypinput.py:164,242-250,216-221`) — `suffix` captured but never stored/re-emitted; breaks byte-preservation on CRLF/whitespace files. Live ini is LF-only → latent/cosmetic. **Fix:** store + re-append `m.group('suffix')`.
- **Bezel "refuse while RetroArch running" guard documented but never implemented** (`bezel_cfg.py:15` + handlers) — no `proc_guard.retroarch_running()` anywhere in the bezel path, unlike `retroarch_cmds.py`; concurrent per-game-override lost-update. **Fix:** `EBUSY` guard in the `bezels.*` mutating handlers.

## CONFIRMED LOW + CONTESTED

**LOW (all verified; one bullet each):**
- `pcsx2x6_input_cmds.py:232,265` — USB edits no one-time backup (dup of MED above, scoped LOW for self-rewriting ini).
- `inifile.py:23,27-29` — `set_section` not byte-preserving for no-blank-line / last-section-no-newline inputs.
- `pcsx2_cfg.py:160-162,191-195` — unsynchronized RMW of override sidecar under 4-worker pool (lost update).
- `pcsx2_cfg.py:98,218` — `read_text(errors="replace")` before full-file rewrite can persist U+FFFD (line 218 is read-only, not a rewrite path).
- `eden_cfg.py:98` — `_apply_player` can emit a duplicate `\default=false` line (cosmetic; QSettings dedupes).
- `sinden_cfg.py:128` — `set_many` inserts into XML `value="…"` with no escaping (controlled callers only).
- `bezel_cfg.py:107-114,…` — overlay path interpolated into quoted RA value without quote-escaping (×2 filings; exFAT forbids `"`).
- `bezel_cfg.py:15` — docstring promises RA-running guard the bezel path doesn't enforce (per-content overrides not RA-rewritten → low blast).
- `switch_bind.py:168-170,259-261,248-250` — xemu/eden snapshot collapses "absent"→"" → restore re-creates a phantom empty `[input.bindings]`/`[Controls]` (PCSX2 path does it right).
- `switch_bind.py:249,260,263` — `restore_target` reads emulator config `errors="replace"` then full-file rewrite (PCSX2 bind path shares the exposure).
- `devices.py:738-740` — `_dolphinbar_wiimotes_active` can pass a negative timeout to `select.select` → ValueError (passive sibling is guarded).
- `devices.py:531` — strict `nm.decode()` of SDL controller name can abort the whole pad enumeration / crash launch-time writers.
- `capture_cmds.py:164-178,…` — evdev fd leak when `capabilities()`/`set_blocking` raises after open (×3 node enumerators).
- `staterev.py:59-68` — `bump()` calls listener outside the lock → out-of-order epoch delivery → spurious (correct) page rebuild, never stale data.
- `xemu_input_cmds.py:236` — `controller_mapping` re-emits whole `[input]` via parse+dump (drops comments, no quote-escape in `_toml_scalar`); latent.
- `xemu_input_cmds.py:81-100` — `_supports_remap()` `lru_cache` not re-detected on in-session flatpak update.
- `eden_input_cmds.py:237-238` — Type selector surfaces an on-disk value it then refuses to write (same in ryujinx).
- `ryujinx_cmds.py:95-96,150-158` — unvalidated `titleid` → per-game path traversal on write (local trusted bridge; defense-in-depth).
- `eden_input_cmds.py:187-228` + peers — lock-free RMW of config under 4-worker pool (lost update across all `*.set`/`*.input_set`).
- `dolphin_cmds.py:243` — post-write re-read can deref None if the file disappears mid-call (µs window).
- `policy_cmds.py:128` — `set_pins` assumes `data['systems']` is a dict; hand-edited non-dict → AttributeError (isinstance precedent exists at line 164).
- `backends_cmds.py:207-221` — `profiles.apply_slot` passes unsanitized `profile` into a path (constrained read-then-apply; local bridge).
- `mad_backup.py:83,127,134,213` — live config restored via non-atomic `copy2` (lines 127/134 write the snapshot dir, not live — partly mis-cited; pre-state always recoverable).
- `mad_backup.py:153-156` — `do_restore` busy-check duplicates emulator patterns instead of reusing `EMULATOR_PROCS` (pcsx2x6 only incidentally matched).
- `sinden_cmds.py:459` — `camera.set` indexes `_cam['vals'][player]` before validating `player ∈ {1,2}` → EINTERNAL KeyError (×2 filings).
- `supermodel-sinden-smart.py:189` — comment-preservation regex treats any `;` (incl. inside a quoted value) as comment start; latent.
- `sinden_cfg.py:129` — `set_many` temp file not removed on write failure (stray `.tmp`; live config never truncated).
- `sinden-smoother.py:143-147` — partial-init failure leaves a grab held / one uinput open before signal handlers exist (kernel reaps on exit).
- `hypinput.py:198-214` — bind/clear silently no-ops yet reports success when the `KEY_<action>` line is absent (masked: all live inis have 22 actions).
- `hypinput.py:305-328,357-372` — multi-line/commented `.commands` flattened to one line on edit (`.bak` preserved; `%INJECT%` is single-line anyway).
- `daphne_cmds.py:34,217-264` — shared `_state`/`_state['hi']` mutated without a lock across pool+stdin threads (TOCTOU save-vs-bind).
- `daphne_cmds.py:127-137,254-263` — unvalidated `gamedir`/`base` allow writing `.ini`/`.commands` outside `~/ROMs/daphne` (local stdin bridge).
- `bezel_cfg.py:291-293` — `install()` `unlink()`s a real user file in the overlay dir instead of `_TMP` (overlay subdir is tool-owned by design; both skeptics → LOW).
- `bezel_cfg.py:633-656` — `_NORMED_CACHE.clear()+assign` unlocked, read by slow pool (benign re-derive, never wrong data).
- `bezel_cmds.py:119,146,180` — client `game`/`target`/`source` into a path with no traversal guard (`fuzzy_candidates` mis-cited — ranking string only; local bridge).
- `quit-combo-watcher.py:99` — all-digit STRING `buttons="314"` decodes to per-char codes `{1,3,4}` instead of falling back.
- `quit-combo-watcher.py:136` — SIGKILL backstop via blind `'-TERM'→'-KILL'` string replace; correct today, fragile to future patterns.
- `quit-combo-watcher.py:207-213` — persistent `os.read` OSError on a select-ready dead fd busy-loops at full CPU up to the 2s rescan.
- `es_systems_wrap.py:76-92` (+`es_systems_standalone.py`) — name/path/platform/theme emitted unescaped (parse guard refuses the bad write → no corruption).
- `esde_settings.py:184-185` — `set_value` doesn't escape the setting NAME in the append branch (no production callers; all hardcoded keys).
- `tester_cmds.py:1126-1130` vs `518-520` — calibration save dumps live `stream.cal` → possible "dict changed size during iteration" (×2 filings; no on-disk corruption, retry-recoverable). **Fix:** dump `dict(stream.cal)`.
- `mad_xarcade_tester.py:398-409` — trackball-flash `after`-timer never cancelled on stop/teardown (guarded by `winfo_exists`, 160ms window).
- `preview_cmds.py:150-156` — Sinden preview labels both guns with one shared smoothed/raw flag (P1-only; read-only display).
- `preview_cmds.py:102-103` — dead unreachable cemu branch in standalone preview (no behavior impact).
- `gui_theme.py:201-205` — `_resolve()` var-substitution loops forever on a cyclic `${var}` ref (crafted theme.xml → silent startup hang). **Fix:** hard iteration cap.
- `openbor-gen-gamelist.py:115,150-158` — no SD-mount/empty guard → empty ROM_DIR backs up then overwrites the live gamelist with empty `<gameList>` (recoverable from `.bak`).
- `convert-pixel-systems.py:115-117` — theme.xml rewritten via non-atomic truncating `open('w')` (`.retropie-original` backup precedes it). *(Also filed CONTESTED — see below.)*
- `openbor-fetch-media.py:155` — recode-output rename uses unescaped glob on the stem (sibling uses `glob.escape`; curated names safe).
- `openbor-fetch-media.py:162-163` — ffmpeg screenshot `subprocess.run` has no timeout (local file, bounded read; pathological-container only).
- `rpcs3.sh:32-34` — `eval` of `.desktop` `Exec=` contents (inherited EmuDeck; local user-owned file, no trust boundary).
- `sinden-serial-preflight.py:116` — non-atomic write to live LightgunMono config; one-time backup goes stale (config is deterministically self-healing on next run).
- `ra-input-monitor.py:101-103` — `open_devs()` leaks the fd of every non-X-Arcade device (one-shot enumeration, OS reclaims on exit).
- `pcsx2_cfg.py:183-186` — corrupt PCSX2 override sidecar silently drops ALL remaps at launch with no log line (atomic-written → improbable). **Fix:** warn to `router.log`.
- `rpcs3_cfg.py:101-104` — same silent-drop pattern for the RPCS3 override sidecar.
- `sinden_cfg.py:104-109` — `backup_once()` swallows OSError → `set_many` can rewrite config with no recoverable backup (narrow: backup-name-specific failure only).
- `retroarch_cfg.py:246-269` — `clear_override` `unlink()`s a per-game cfg that still holds user comment lines after sentinel-strip (runs every RA game-end; rule #5). **Fix:** `fsutil.recoverable_delete`.
- `duckstation.sh:14-19` — omits the peer empty-check/flatpak fallback → confusing failure on a flatpak-only install (AppImage present on this rig).
- `deck-backup.sh:81` — `--dest` with no arg dies via `set -u` "unbound variable" (note: the finding's `die`-based fix is itself broken — `die` defined later; use `${2:?…}`).
- `deck-backup.sh:242-244,…` — aborted archive leaves `*.partial` the retention prune never reaps (silent disk-space leak).
- `install.sh:179-182` — router-hook re-run churns a new `.bak` every time even when byte-identical (`deploy_hook` already uses `cmp -s`).
- `supermodel.sh:7-9` (cited 729-731) — ROM args flattened to one word + all single quotes stripped (this `supermodel.sh` is dead/legacy on the live rig).
- `model-2-emulator.sh:62` (cited 711) — unquoted `cd $romsPath/model2` word-splits if the data root contains a space.
- `cpp-pages-input/GuiMadPageEmuSettings.cpp:197-209` — generic float stepper formats/writes `%.1f`, truncating any future sub-0.1 step (all current floats safe).

**CONTESTED (split verdict — worth a look):**
- `eden_cfg.py:160` — `assign()` hardcodes USB bus byte `0300` in the Eden GUID → a Bluetooth pad wouldn't bind; **one skeptic NONE** (parity path unreachable: `[systems.switch]` sets `router_skip=true`, live path is the correct `assign_devices`). Latent footgun.
- `bezel_cfg.py:325,726` — bezel write drops the controller-router sentinel block on overwrite; both skeptics → **LOW** (router block is transient/regenerated next launch; bezel writes and a running RA game don't co-occur).
- `capture_cmds.py:521-526,247,509` — `input.lock` false/true can reorder across consecutive captures, leaving the panel unlocked mid-capture; **one skeptic real=false** (the topmost capture modal swallows all input regardless; panel `mInputLocked` is only a secondary backstop). Real latent race + two genuine cleanups (C++ ignores the carried `stream` token; `cleanup()` emits `locked:false` unconditionally).
- `tester_cmds.py:188-214` — `gamepads.list` probes a Wii hidraw slot concurrently with a starting wii tester (TOCTOU); **both skeptics lean NONE/LOW** (page-architecture makes overlap contrived; concurrent report-mode writes are a tolerated, self-healing baseline per the nav-bridge design).
- `convert-pixel-systems.py:115-116` — non-atomic theme.xml write as an **atomic-write-LAW rule-violation**; **both skeptics refuted the MED framing** (LAW scopes to byte-preserving *config* writers; this regenerates a cosmetic theme wholesale and its real peers `convert-pixel-theme.py`/`inject-carousel-logos.py` also use `write_text`). Residual LOW robustness nit.
- `rpcs3.sh:31-34` — `eval $rpcs3desktopFile` as a **MED security/command-injection** sink on the live PS3 `.desktop` launch path; **both skeptics refuted SECURITY** (no trust boundary: `.desktop` files are user-owned, EmuDeck-generated; attacker==victim==`deck`). Residual **LOW robustness** only (unquoted `eval` is fragile to glob brackets). Fix (drop `eval`, use an array) still worth doing.

## CLEAN / Well-Built
Zero confirmed defects: **cpp-pages-stateful**, **cpp-pages-misc**, **sweep-cpp-lifetime**. The **C++ GUI/native panel** is the strongest layer overall (only 2 confirmed across all cpp-* units, both bounded-UX with visible auto-recovery, no data/correctness/lifetime bugs). Genuinely robust patterns observed repeatedly and applied correctly elsewhere: the **atomic temp+rename writers** (`fsutil`, `retroarch_cfg`, `es_systems_wrap`, the 4 pad cfg writers under the golden harness), the **one-time pristine-backup discipline** (`ensure_bak`/`ensure_pristine_backup`, sibling-aware), the **staterev rev-cache** (correctly lock-guarded; the bezel-cache and `staterev.bump`-listener races are benign), and the **transient snapshot/restore** design for PCSX2 (the model the xemu/eden absent-section and switch_bind sidecar findings should be brought up to). The recurring fix shape is uniform: route the outlier writer through the safe helper its own module already provides.