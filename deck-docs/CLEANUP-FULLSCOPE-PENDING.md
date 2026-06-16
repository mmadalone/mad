# MAD cleanup — Full-scope (PENDING) + remaining audit follow-ups

_2026-06-16. The 2026-06-15 audit's fix batch AND the LEAN cleanup batch are DONE + PUSHED.
This file is the spec for the NEXT (Full-scope) cleanup session so it doesn't re-investigate._

## ✅ DONE 2026-06-16 — Full-scope batch IMPLEMENTED + PUSHED
- **launchers** `mad-standalones-pages`: `f9409bb` (shared `lib/pad_assign.py` + 4-backend
  migration + xemu fix D + `tests/` stdlib-unittest golden harness — 55 tests + selfcheck green),
  `07de330` (inherits→`routing.resolve_system`; cemu `unlink→fsutil.recoverable_delete` + per-file
  pristine backup + staterev bump; 6 MAD fallback icons → `art/icons`). Pushed `3bcdf6e..07de330`.
- **fork** `deck-patches`: `6895872` (C++ `applyToggle()` helper + struct unify; teardown reader-join
  spin 2s→0.5s [SIGTERM grace untouched]; capture-`{closed}` `LOG(LogWarning)`). Built clean in
  distrobox; pushed `3ee9499..6895872` → CI builds **run 46** for the in-app updater.
- **theme** `~/ES-DE/themes/pixel-es-de`: now its OWN git repo (`1772d88` pristine import,
  `dc58320` housekeeping: orphan `pixel.xml`→_TMP, dead `console.png` overlay removed). LOCAL-ONLY
  (no remote yet — confirm push target before adding one).
- **2 deliberate deviations (flagged to user):** (1) cemu — the literal "refresh `.router-backup`
  on newer mtime" is UNSAFE (MAD's own output is newer every launch → would clobber the pristine);
  delivered full protection as pristine-kept + recoverable `_TMP` on clear instead. (2) Theme — the
  "reconcile stale esde-build copy" premise is FALSE: that copy is gitignored, 0 tracked files, and
  the AppImage doesn't bundle pixel-es-de (build symlinks `share/es-de/themes`) → skipped as moot.
- **OUT (as planned):** DS4 Option C. **Owed:** on-device sign-off (xemu fix, toggles+rollback,
  teardown B-close, theme render) after updating in-app to run 46.

## Already shipped — DO NOT redo
- **Audit fix batch** (items 1,2,3,5,6,7,9,11 + SwitchComponent + DS4-freeze Option A): launchers `63a4193`, fork `a44168a`.
- **LEAN cleanup batch**: launchers `3bcdf6e`, fork `3ee9499`. Did: backup-pristine integrity (A), dedup B (`_retroarch_running`→proc_guard, pcsx2→inifile, PAD_SHORT←KNOWN_PADS), dead-code C (`joypad_indices`; C++ `mListCookie`/`mPlayers`), 9 dead RPC endpoints E, deck-restore ROMs/media `_TMP` + deck-post-update `--selfcheck` gate + C++ clip guards F.
Both repos: github.com:mmadalone/mad.git, branches launchers `mad-standalones-pages` / fork `deck-patches`.
Source audit (original item list + file:lines): `deck-docs/AUDIT-2026-06-15.md` ("Cleanup" + "Best-practice").

## Full-scope refactors (the deferred maintainability work — the planned next batch)

### 1. Shared 4-way pad-assignment helper  ⚠ INCLUDES the deferred xemu bug fix (audit item 3 + "D")
Files: `lib/pcsx2_cfg.py`, `lib/xemu_cfg.py`, `lib/eden_cfg.py`, `lib/rpcs3_cfg.py` — all duplicate the
same select/prioritize/pin/fallback block (`prio` dict → `ps` sorted list → fill slots → handheld
fallback → apply pins). Extract a shared helper returning `{slot: value-or-None}`; each backend supplies
only the **value-encoding** (pcsx2 = SDL **index**; xemu = class **GUID**; eden = **(vidpid, port-within-class)**;
rpcs3 = device **name/handler**).
**CRITICAL nuances (verified, do not lose):**
- The pin-collision-drop loops DIFFER per backend and must be preserved/unified carefully:
  pcsx2 drops the slot whose SDL index collides with a pin; eden/rpcs3 drop slots whose value is in
  the pinned-value set; **xemu does NOT drop at all** — that's the latent bug ("D").
- **xemu's fix is NOT a blind copy of the siblings.** xemu binds by **class GUID**, so the SAME class on
  two ports is the LEGITIMATE two-identical-pads case (xemu fills them in SDL order). A blind eden-style
  drop would BREAK that. The real xemu bug only fires when a pin claims a class that an auto-assigned slot
  also got AND there's only ONE physical pad of that class → correct fix needs **instance-counting**
  (drop the colliding non-pinned slot only when the pinned class has no spare unit). Design the helper so
  collision handling is parameterizable (index-identity vs class-with-spare-count).
- Risk: MEDIUM. Needs careful unit tests per backend (the 4 collision behaviors + handheld fallback).

### 2. C++ `applyToggle()` helper  (rebuild)
`es-app/src/guis/mad/pages/GuiMadPageSystems.cpp` (~223-280 loops; ~287-329 `setFlag` vs ~331-364
`setRaOption`) duplicates the toggle-row + roll-back-on-failure pattern twice (two parallel structs +
two near-identical writers). Factor ONE `applyToggle(method, keyField, …)`. Low-risk refactor; rebuild.

### 3. `inherits` one-hop resolvers → `routing.resolve_system`  (LATENT — zero divergence today)
`lib/es_systems.py:~115` `_resolve_backend` and `lib/mad_config.py:~140` each follow ONE `inherits` hop;
`lib/routing.py` `resolve_system` walks the full chain. Verified zero divergence on the current policy
(all backend-carrying systems set `backend` directly). Build both on `routing.resolve_system(...).get("backend")`.

### 4. cemu `unlink()` → `fsutil.recoverable_delete`  (rule #5)
`lib/cemu_cfg.py` `_clear_port` (~150, called ~198/215/228) `p.unlink()`s a managed `controllerN.xml`.
Original is recoverable via the one-time `.router-backup`, but a user hand-edit made AFTER MAD's first run
is unlink'd with no fresh backup. Route through `fsutil.recoverable_delete` (or refresh the backup if mtime
is newer). MEDIUM: changes deletion semantics + accumulates `_TMP`.

## Theme housekeeping  (SEPARATE repo — decide canonical source first)
Live theme `~/ES-DE/themes/pixel-es-de` is its OWN git repo; bundled copies at
`~/Emulation/tools/launchers/art/mad-theme-examples/pixel-es-de` and `~/esde-build/ES-DE/themes/pixel-es-de`.
Decide which is canonical before editing. Items: `pixel.xml` is a byte-identical orphan of `theme.xml`
→ move to `_TMP` (rule 5); `router-config/theme.xml:16` references a missing `./console.png` (never-loaded
MAD sidecar) → drop the stray `<path>` line; **6 fallback icons** present in theme `router-config/icons`
but missing from `launchers/art/icons` (resolve chain `lib/madsrv/systems_cmds.py:~48-74`; eden/ryujinx
icons lost if the theme is replaced) → copy them into `launchers/art/icons` (pin the exact 6 from the chain).

## Audit "best-practice" (rated fix-OPTIONAL)
- `MadBackend` dtor runs up to ~2–4 s of blocking SIGTERM-grace sleeps on the UI thread, reachable from
  `input()` via `delete this` (`MadBackend.cpp:~476-502`, from `GuiMadPanel.cpp:451/461/482`) — shorten/SIGKILL sooner on the interactive teardown path.
- `rpcs3` input writer does a whole-file YAML round-trip (`lib/rpcs3_cfg.py:~186`) — document the intentional full-rewrite (RPCS3 owns Default.yml's format).
- `capture.button` start failure: a bare `{closed}` push racing the response is silently dropped (`MadBackend.cpp:~360-374`) — log it so a protocol regression is visible.

## DS4 hotplug freeze — residual (OPTIONAL)
Option A (HIDAPI_PS4/PS5=0) shipped and REDUCED but didn't eliminate the freeze (residual = SDL's general
HIDAPI device rescan on the main thread; user accepted as-is). **Option C** = `SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI,"0")`
in `InputManager::init` (full HIDAPI disable → no rescan, but re-maps ALL pads in ES-DE). Full write-up:
`deck-docs/esde-controller-hotplug-freeze.md`.

## NOT part of cleanup — broader MAD roadmap (separate feature sessions)
Standalone controller-config migration (Dolphin/PCSX2/RPCS3/Xemu/Supermodel → wrapper+Hands-off, then
rename Systems→"RA Systems" + drop tiles); MAD standalone per-button input mapping (Eden/RPCS3/Dolphin,
then Cemu/Supermodel; Model2 can't); Switch-launch UI polish (label clip, controller-type selector) +
Phase 2 settings editor; Mario Wonder stutter fix (untested). See the matching memory notes.

## Build/verify reminders
- Fork C++ rebuild MUST run in distrobox: `distrobox enter esde-ubuntu -- bash ~/esde-build/rebuild.sh`
  (host `rebuild.sh` fails — no cmake/make on SteamOS). Verify a FRESH AppImage timestamp + `.o` recompiles.
- Daemon sanity after Python changes: `python3 mad-backend.py --selfcheck` (must invoke via python3 — not +x).
