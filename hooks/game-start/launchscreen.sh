#!/usr/bin/env bash
# ES-DE game-start hook: show the RetroPie-style per-system launching.png and
# KEEP it up until the game window appears — instead of a fixed 2s that leaves a
# black gap while slow emulators (Proton/OpenBOR, Eden) load.
#
# The splash runs in the BACKGROUND (non-blocking) so ES-DE proceeds to launch
# the emulator while it's still showing. It self-closes when the game window
# takes focus; the game-end hook kills it as a backstop, and under gamescope the
# game window covers it regardless. A readiness handshake (SPLASH_READY) makes
# this hook wait until the splash is actually drawn before returning, so a fast
# emulator can't map its window first and end up hidden behind the splash.
#
# ES-DE invokes: game-start/launchscreen.sh <ROM> <name> <system> <fullname>
set -uo pipefail

SYSTEM="${3:-}"
[[ -z "$SYSTEM" ]] && exit 0

# Where the launching pack lives: a "_launching-screens"/"launching-screens" dir
# inside the ACTIVE ES-DE theme (the screens are theme assets). Resolved dynamically.
source "$(dirname "$0")/../launchscreen-pack.sh"
[[ -z "${PACK:-}" ]] && exit 0   # active theme ships no launching-screens -> no splash
# Collection splash, resolved from the VIEW the user launched FROM (LAUNCHED-FROM
# mode). `scripts/system-select/05-record-view.sh` records the carousel
# system/collection into $XDG_RUNTIME_DIR/es-current-view; `view-collection`
# echoes it iff it's an enabled collection that CONTAINS this ROM (so spiderman vs
# superheroes is exact — you get the screen for the one you entered), else nothing
# -> system splash. RELIES ON QuickSystemSelect=off (the L/R jump doesn't fire
# system-select, which would make the view stale) — see [[es-de-launch-screens]].
ROM="${1:-}"
# 1) Launched-FROM, EXACT — our PATCHED ES-DE passes the launched-from custom
#    collection as $5 (game-start arg5). This is accurate regardless of how you
#    navigated (carousel OR the L/R QuickSystemSelect jump), so QuickSystemSelect
#    can be left ON. Empty when launched from a plain system, or on stock ES-DE.
COLL="${5:-}"
# arg5 is AUTHORITATIVE on our patched build: a non-empty collection name when
# launched FROM a custom collection, EMPTY when launched from a plain system. We
# deliberately do NOT fall back to the carousel VIEW (unreliable here —
# QuickSystemSelect=leftrightshoulders makes es-current-view stale) or to collection
# MEMBERSHIP: a game launched from its SYSTEM must show the SYSTEM splash even if it
# ALSO belongs to a collection — the bug where X-Men COTA launched from fba showed
# the Fighter collection screen. (On stock ES-DE there's no arg5, so COLL stays empty
# and everything falls through to the system splash below — acceptable; we run patched.)
# Collection screen dir: try the name verbatim, then lowercased. ES-DE passes
# the collection name to system-select in its display case (e.g. "Pew-Pew-Pew!!!")
# but theme/asset dirs follow ES-DE's lowercase convention ("pew-pew-pew!!!"); on
# a case-sensitive FS those differ. (All-lowercase names like superheroes match
# either way.) Fall back to the game's system splash if neither exists.
if [[ -n "$COLL" && -f "$PACK/$COLL/launching.png" ]]; then
    IMG="$PACK/$COLL/launching.png"
elif [[ -n "$COLL" && -f "$PACK/${COLL,,}/launching.png" ]]; then
    IMG="$PACK/${COLL,,}/launching.png"
else
    IMG="$PACK/$SYSTEM/launching.png"
fi

if [[ ! -f "$IMG" ]]; then
    # Try a few common ES-DE-to-screenpack name aliases
    case "$SYSTEM" in
        megadrive) IMG="$PACK/genesis/launching.png" ;;
        sfc)       IMG="$PACK/snes/launching.png" ;;
        pcfx)      IMG="$PACK/pcfx/launching.png" ;;
        famicom)   IMG="$PACK/nes/launching.png" ;;
        wii)       IMG="$PACK/wii/launching.png" ;;
        wiiu)      IMG="$PACK/wiiu/launching.png" ;;
        switch)    IMG="$PACK/switch/launching.png" ;;
        fba)       IMG="$PACK/fba/launching.png" ;;
        # arcade keeps as-is
    esac
fi

[[ ! -f "$IMG" ]] && exit 0  # no matching screen — silent

# PIDF / READY are provided by launchscreen-pack.sh (kept in $XDG_RUNTIME_DIR,
# not the theme dir).

# Close any stale splash from a previous launch, then arm the readiness flag.
[[ -f "$PIDF" ]] && kill "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null
pkill -f show-launchscreen.py 2>/dev/null
rm -f "$READY"

# Background splash; safety auto-close after LAUNCHSCREEN_MAX seconds (the
# game-end hook and the game window itself normally close it much sooner).
MAX="${LAUNCHSCREEN_MAX:-60}"
DISPLAY="${DISPLAY:-:0}" SPLASH_READY="$READY" nohup /usr/bin/python3 \
    "$HOME/Emulation/tools/launchers/show-launchscreen.py" "$IMG" "$MAX" --hold \
    >/dev/null 2>&1 &
echo $! > "$PIDF"

# Wait (≤2s) until the splash is actually on screen before returning, so ES-DE
# doesn't launch the emulator first and leave the game hidden behind the splash.
for _ in $(seq 1 20); do
    [[ -f "$READY" ]] && break
    sleep 0.1
done
exit 0
