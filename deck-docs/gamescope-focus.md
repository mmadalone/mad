# gamescope focus, Steam background launches, PauseGames: findings

Recorded 2026-10-29 while planning the "Steam game-end on focus return" fork change
(plan: `~/.claude/plans/wise-sprouting-gem.md`). Sources per section.

## Root-window atoms (gamescope, Game Mode)

Source: gamescope `src/steamcompmgr.cpp`, `determine_and_apply_focus()` (master, read 2026-10-29,
https://github.com/ValveSoftware/gamescope/blob/master/src/steamcompmgr.cpp) + live `xprop` reads.

- The atoms live on the PRIMARY X server `:0` (Steam's); ES-DE renders on the nested display.
  Read: `DISPLAY=:0 xprop -root GAMESCOPE_FOCUSED_APP GAMESCOPE_FOCUSED_APP_GFX GAMESCOPE_FOCUSED_WINDOW GAMESCOPE_KEYBOARD_FOCUS_DISPLAY GAMESCOPE_FOCUSABLE_APPS`
  Watch: `DISPLAY=:0 stdbuf -oL xprop -spy -root <atoms>` (without `stdbuf -oL` nothing appears
  when piped: block buffering).
- `GAMESCOPE_FOCUSABLE_APPS` = the unique non-zero appIDs of all possible-focus XWayland windows,
  EXCLUDING useless (1x1), skip-taskbar-and-not-fullscreen and override-redirect windows. A game's
  id leaves the list when its windows close. Steam's own `769` drops out of the list while the
  Steam menu is open (seen 4 times in the recording), so never treat its absence as meaningful.
- `GAMESCOPE_FOCUSABLE_WINDOWS` = [window, appid, pid] triplets (same filter).
- Ids on this Deck: ES-DE (non-Steam shortcut) `4278202385`; Steam UI `769`; G.I. Joe (Steam game)
  `1432220`; Deadpool (non-Steam shortcut) `3250635768`. Keyboard-focus displays: `12602` = the
  nested Xwayland that ES-DE AND the games use; `12346` = Steam's. So `kbd == 12602` alone does
  NOT mean ES-DE: the fork's GamescopeFocus gate is `focusedApp == ES-DE && kbd == ES-DE's`.
- ES-DE's launch splash (`show-launchscreen.py`) is a window of ES-DE's app (`win 18874388`);
  ES-DE's main window is `6291467`.

## What a Steam background launch + quit looks like

Source: own read-only recording 2026-10-29 01:23-01:31 (xprop -spy on `:0` + a `ps` state poll),
correlated with `~/ES-DE/logs/es_log.txt`.

```
 t+0.0 s   ES-DE launches `steam steam://rungameid/<id>` (es_log: "keeping ES-DE running in the background")
 t+0.35 s  focus -> Steam UI 769 (kbd 12346)        ES-DE still RUNNING (unfocused, ~30 Hz loop)
 t+~10 s   game id appears in FOCUSABLE_APPS, ~0.05-0.1 s later it takes focus
           PauseGames FREEZES ES-DE (SIGSTOP) 0.11-0.40 s after the game takes focus
 quit      game id leaves FOCUSABLE_APPS; Steam UI holds focus 1.6-2.0 s
           then ES-DE gets focus back; PauseGames RESUMES it 0.02-0.80 s later
```

- **Switch back to ES-DE while the game runs** (Steam button -> pick ES-DE): ES-DE gets focus but
  the game id STAYS in FOCUSABLE_APPS until the game really exits. So "game ended" is detectable.
- Upstream ES-DE fires game-end for background launches "on application wakeup if running in the
  background" (INSTALL.md:2448, v3.5.0) = the first input. On 2026-10-29 one G.I. Joe run's
  game-end fired 7 minutes after the quit (01:37:58), on the next press.
- The first press then runs ~16 game-end hooks blocking for 2-3 s; presses during that freeze are
  replayed (same loop pass) or dropped by the fork's >500 ms gap flush (3.5.0-mad.170 and earlier;
  the steam-gameend-focus build drops them, keeping releases on the first-input path).

## PauseGames (SDH-PauseGames Decky plugin)

Source: `~/homebrew/settings/SDH-PauseGames/settings.json` + `~/homebrew/plugins/SDH-PauseGames/main.py`
(read 2026-10-29) + the recording.

- Settings here: `autoPause: true`, `overlayPause: true`. With autoPause, ES-DE AND its child
  processes (incl. the launch splash) are SIGSTOPped while another game has focus, NOT during
  Steam's launch phase (Steam UI focused).
- It finds a game's processes via Steam's reaper: `pgrep --full --newest "/reaper\s.*\bAppId=N\b"`
  (main.py:127), then walks up to the process carrying the `AppId=` argument (main.py:137-146).
- Consequence: the splash's 60 s safety timer (Tk `after`, wall clock) is frozen with it and fires
  the moment it resumes if overdue; for sessions under ~57 s the splash stays until game-end.

## Steam reaper = "game still running"

Source: live process list 2026-10-29 + PauseGames main.py (above).

- Every Steam launch runs under `.../ubuntu12_32/reaper SteamLaunch AppId=<id> -- <command>`
  (ES-DE's own: `reaper SteamLaunch AppId=4278202385 -- /home/deck/Applications/ES-DE.AppImage`).
  The reaper is a subreaper and lives until every descendant exits, i.e. Steam's "Running" state.
- ES-DE's process name is `AppRun` (it runs through the AppDir symlink), so `pgrep -x es-de`
  finds nothing; match `ES-DE-MAD.AppDir/AppRun` in the full args instead.

## Fork diagnostics

- `~/Emulation/storage/controller-router/gamescope-focus.log` (GamescopeFocus debug log): lines
  carry `YYYY-MM-DD HH:MM:SS.mmm` timestamps since the steam-gameend-focus build (none before);
  never rotated (~168 KB on 2026-10-29); sessions start with an `init:` line. The game-end watch
  writes `game-end watch: ...` lines here and `Background game-end watch: ...` lines to es_log.txt.

## Test 2 findings (steam-gameend-focus LOCAL build, 2026-10-29 21:42-21:51)

Sources: own read-only recording (xprop -spy on `:0`, a 0.2 s `ps` poll incl. reaper AppIds),
es_log.txt, gamescope-focus.log; SDL source in `~/esde-build/ES-DE/external/SDL` (read 2026-10-29);
`show-launchscreen.py` + `hooks/{game-start,game-end}/launchscreen.sh` (read 2026-10-29).

- **The launch splash takes ES-DE's controller input away.** `show-launchscreen.py --hold` calls
  `root.focus_force()` and does NOT close on FocusOut (removed on purpose: Proton/OpenBOR focus
  churn). It closes only on SIGTERM (game-end/launchscreen.sh, or the next game-start) or its 60 s
  safety timer. gamescope focuses the splash window whenever ES-DE's app is in front
  (`GAMESCOPE_FOCUSED_WINDOW` = splash). SDL drops joystick/gamecontroller events while the app has
  no keyboard focus unless `SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1`: default "0"
  (`external/SDL/include/SDL_hints.h:680-693`); rule `SDL_HasWindows() && SDL_GetKeyboardFocus() == NULL`
  (`src/joystick/SDL_joystick.c:1689-1699`). ES-DE never sets the hint. So while the splash is up
  and ES-DE is in front, every press is dropped before ES-DE sees it (no `Window::logInput` line).
  The comments "self-closes when the game window takes focus" (game-start/launchscreen.sh) and
  "until the game window takes focus (FocusOut)" (show-launchscreen.py docstring) were stale
  (fixed 2026-10-30, steam-gameend-focus rev 2).
- Once the splash is killed, gamescope hands focus to ES-DE's main window at once (21:43:56.2,
  21:44:51.2), and presses register straight after.
- **Steam starts a non-Steam shortcut's reaper within ~0.5 s** of `steam steam://rungameid/<id>`
  (Deadpool: launch about 21:45:01.3, reaper by 21:45:01.86). A Steam game's reaper took 1.4-3 s
  (G.I. Joe). ES-DE saves the launched game's gamelist entry (`updateGamelist`) BEFORE its main loop
  gets back, 0.4-1.3 s after the launch, so a baseline of "reapers already running" taken after the
  launch races a shortcut's reaper. Take it BEFORE the launch command.
- rungameid to reaper AppId: Steam game `steam://rungameid/1432220` runs as `AppId=1432220`;
  non-Steam shortcut `steam://rungameid/13961374314801397760` = (3250635768 << 32) | 0x02000000
  runs as `AppId=3250635768`. The ROM files (`~/ROMs/steam/*.sh`, 88 on 2026-10-30) are 3-4 line
  shell scripts, at most 177 bytes, each with exactly one `exec steam steam://rungameid/<id>` line.
- **Measurement caveat:** the xprop recorder's timestamps lagged ES-DE's own log by up to 3.2 s at
  times (gamescope-focus.log 21:48:19.519 vs xprop 21:48:22.724 for the same change). Trust
  gamescope-focus.log and the `ps` poll for timing; use xprop only for the ORDER of changes.

## Test 3 findings (steam-gameend-focus rev 2 LOCAL build, 2026-10-30 14:45-15:05)

Sources: own read-only recording (xprop -spy on `:0`, a `ps` poll incl. reaper AppIds), es_log.txt,
gamescope-focus.log; PauseGames code: installed `~/homebrew/plugins/SDH-PauseGames/dist/index.js` +
`main.py`, and upstream `src/backend.ts`
(https://raw.githubusercontent.com/wynn1212/SDH-PauseGames/main/src/backend.ts, read 2026-10-30).

- **Rev 2 timings:** quit fire 0.26 s after ES-DE was back in front (Deadpool via the Steam menu,
  G.I. Joe normal quit); switch-back fire 1.006 s after ES-DE was in front with the game running;
  the launch image was gone 3-4 s after ES-DE was back. After 2 min 19 s back in the game with the
  screensaver timer at 1 minute, an A straight after the quit was ignored and no screensaver
  started.
- **The launch image can already be gone at a switch-back.** Its 60 s safety timer is frozen with
  ES-DE by PauseGames and fires at once on resume when overdue (a session longer than ~57 s). The
  first press can then land inside the 1 s switch-back hold and run game-end through the
  first-input path (es_log 14:59:34, watch: "disarmed, the first input handled the launch while
  its game still runs").
- **Steam on `steam://rungameid/<id>` for a game it is already running:** Steam UI (769) takes
  focus with "An error occurred while launching this game: Game already running" (recorded open
  for 51 s, until the user closed it); then the running game comes to the front. No second reaper
  was seen.
- **PauseGames gets stuck after a failed launch (1.0.3 = latest, 2026-10-18; installed from the
  wynn1212 fork of popsUlfr/SDH-PauseGames).** Its focus handler returns early while
  `appIsStartingUp` is true, set by `RegisterForGameActionTaskChange` to `status !== "Completed"`;
  nothing clears it when a launch fails. After the refused launch it neither resumed the game it had
  frozen for the switch-back (all 15 processes under the reaper stayed `T` while the game was in
  front) nor paused ES-DE. It recovers at the next launch that completes. Manual recovery = what its
  `resume()` does: `kill -SIGCONT` on every descendant of the game's reaper (`main.py`).
- **Fix (rev 3, fork commit `370018067`, test 4 passed 2026-10-30 15:52-15:55):** ES-DE no longer
  starts a Steam launch script whose reaper AppId is running; it shows "GAME STILL RUNNING, USE THE
  STEAM BUTTON TO SWITCH TO IT". Going back through the Steam button then works normally
  (PauseGames paused ES-DE again at 15:53:31, the game ran), for a Steam game and a shortcut.
- The `ps` recorder's `show-launchscreen.py` pattern also matches the hook's own
  `pkill -f show-launchscreen.py`: a one-sample `splash=R` during game-end is that pkill, not the
  image.
