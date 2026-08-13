# Lindbergh quit leaves ES-DE un-exitable and wedges Steam (2026-06-25)

Status: OPEN. Needs a fix, likely in the MAD / Lindbergh quit flow. Logged at user
request. ES-DE itself is fine; this is about how the Lindbergh game is quit.

## Correction to an earlier mis-read
An earlier note in this session called the 21:31 event an "ES-DE crash". That is
WRONG, per the user: it was NOT a crash. The user QUIT the Lindbergh game with a
kill command, and that left ES-DE in a state it could not exit from, which then
wedged Steam's Game Mode launch path.

## Timeline / evidence (from ~/ES-DE/logs/es_log.txt)
A CLEAN Lindbergh quit earlier the same session, for comparison:
  21:26:14      game-end event fired for "Rambo" (rambo.lindbergh)
  21:26:14..20  all game-end hooks ran; ES-DE returned to the gamelist
  => a normal quit writes a game-end event and ES-DE keeps running. Desired behavior.

The problem quit (Test/Calibrate tile):
  21:31:43  game-select  rambo-test.lindbergh ("Rambo (Test / Calibrate)")
  21:31:46  Launching via emulator "Lindbergh Loader (Standalone)"
  21:31:46..51  game-start hooks ran (controller-router, quit-combo-watcher, sinden, ...)
  21:31:56  Expanded launch command:
      controller-router-wrap.sh lindbergh /home/deck/ROMs/lindbergh/rambo-test.lindbergh
        "rambo-test.lindbergh" "Sega Lindbergh" --
        /home/deck/Applications/lindbergh-loader.AppImage -t ../rambo.lindbergh/elf/ramboM.elf
  <<< es_log.txt ENDS HERE. No game-end event. >>>

The user then quit the Lindbergh game with a kill command (EXACT command TBD, below).

## Symptoms observed
- es_log.txt truncated mid-session at 21:31:56, no game-end event (unlike 21:26).
- Every later attempt to launch ES-DE from Steam (non-Steam shortcut, AppId
  4278202385) hung: the launch shell blocked in futex_do_wait and never started the
  ES-DE binary; es_log.txt was never written. Killing the stuck launcher cleared it
  for exactly one attempt, then it re-hung (the Steam CLIENT was wedged, not ES-DE).
- After restarting the Steam client (pkill -TERM -x steam; gamescope auto-relaunches
  it), Steam showed the spinner message "waiting for ES-DE to exit".
- The user killed the lingering ES-DE via htop. ES-DE then launched properly.
  (Confirmed recovered 22:12: ES-DE running, es_log writing again.)

## Interpretation
Quitting the Lindbergh game with that kill did NOT let ES-DE exit or return cleanly.
A lingering ES-DE (tracked by the Steam reaper for AppId 4278202385) kept Steam
"waiting for ES-DE to exit", deadlocking all later launches until that ES-DE was
force-killed. So the kill used to quit Lindbergh is collateral: it disrupts ES-DE
instead of cleanly quitting just the game.

## What likely needs fixing (MAD)
Goal: quitting a Lindbergh game must terminate ONLY the game and let ES-DE return to
the gamelist cleanly (preferred, like the 21:26 case), so the Steam reaper is never
left waiting on a stuck ES-DE.

Open questions to resolve before fixing:
  1. EXACT kill command the user ran (TBD; user to provide). Manual pkill, or the MAD
     quit-combo, and what pattern / signal?
  2. Does the Test/Calibrate tile (-t, ramboM.elf) use a different quit path than the
     normal tile? The normal 21:26 quit was clean; the -t quit was not.
  3. Did the kill hit ES-DE / controller-router-wrap / the Steam reaper (too-broad
     pattern, or a process-group / session-wide kill that swept up ES-DE)?
  4. Per memory 'lindbergh-on-deck', Lindbergh quit uses a self-match-safe [.]elf
     pkill in the game-start hook (added 2026-06-25, launchers 1257436) because the
     game runs as ./<name>.elf. Verify that path covers the -t tiles and never
     touches ES-DE.

## Pointers
- memory 'lindbergh-on-deck' (Lindbergh wiring + quit history)
- Active hook copies: ~/ES-DE/scripts/game-start/ and game-end/ (see memory
  'esde-hooks-active-copy'); sources in ~/Emulation/tools/launchers/hooks/
- Quit mechanisms: quit-combo-watcher.sh (MAD quit-combo), controller-router-wrap.sh
- Separate fix task: per project rules, route through plan mode before code changes.
