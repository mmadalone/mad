#!/usr/bin/env bash
# Launch an OpenBOR (Windows build) game from ES-DE via Proton, reusing the
# Proton prefix the user already created when adding the game to Steam.
#
# ES-DE calls:   openbor.sh <path-to-.openbor-manifest>
#
# The .openbor manifest is a tiny key=value file living next to the game
# folders in the openbor ROM dir, e.g. /run/media/deck/1tbDeck/ROMs/openbor/:
#
#     DIR=DD_FINAL                 # game subfolder (relative to the manifest)
#     EXE=OpenBOR.exe              # Windows binary to run inside that folder
#     PREFIX=/.../compatdata/2571094043   # Proton prefix to use
#
# PREFIX is either a reused Steam compatdata prefix (for games that were
# launched at least once in Steam) or the shared OpenBOR prefix below (which
# Proton creates+inits on first use). The launcher is path-independent: a
# prefix created against the /home/deck/OpenBor copy works fine here.
set -uo pipefail

MANIFEST="${1:?usage: openbor.sh <path-to-.openbor>}"
[[ ${MANIFEST:0:1} != / ]] && MANIFEST="$PWD/$MANIFEST"
[[ -f $MANIFEST ]] || { echo "openbor.sh: manifest not found: $MANIFEST" >&2; exit 66; }

ROM_DIR=$(dirname "$MANIFEST")
. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
SHARED_PREFIX="$storageRoot/openbor/prefix"

# --- parse manifest (only DIR/EXE/PREFIX, ignore comments/blank) ----------
DIR=""; EXE=""; PREFIX=""
while IFS='=' read -r key val; do
    key="${key%%[[:space:]]*}"; key="${key##[[:space:]]}"
    [[ -z $key || ${key:0:1} == '#' ]] && continue
    val="${val%$'\r'}"
    case "$key" in
        DIR)    DIR="$val" ;;
        EXE)    EXE="$val" ;;
        PREFIX) PREFIX="$val" ;;
    esac
done < "$MANIFEST"

[[ -n $DIR && -n $EXE ]] || { echo "openbor.sh: manifest missing DIR/EXE: $MANIFEST" >&2; exit 65; }
[[ -n $PREFIX ]] || PREFIX="$SHARED_PREFIX"

# Resolve the game folder. Prefer the copy next to the manifest (the ROM
# dir); fall back to the internal /home/deck/OpenBor copy if absent there.
GAME_DIR="$ROM_DIR/$DIR"
[[ -d $GAME_DIR ]] || GAME_DIR="$HOME/OpenBor/$DIR"
[[ -d $GAME_DIR && -f "$GAME_DIR/$EXE" ]] || {
    echo "openbor.sh: game/exe not found: $GAME_DIR/$EXE" >&2; exit 66;
}

# --- find Proton (match what the Steam prefixes were built with) ----------
find_proton() {
    local want="$1" base
    for base in "$HOME/.steam/root/compatibilitytools.d" \
                "$HOME/.local/share/Steam/compatibilitytools.d"; do
        [[ -x "$base/$want/proton" ]] && { echo "$base/$want"; return; }
    done
    # fall back to newest GE-Proton10-* available
    for base in "$HOME/.steam/root/compatibilitytools.d" \
                "$HOME/.local/share/Steam/compatibilitytools.d"; do
        local p
        p=$(ls -1d "$base"/GE-Proton10-* 2>/dev/null | sort -V | tail -1)
        [[ -n $p && -x "$p/proton" ]] && { echo "$p"; return; }
    done
}
PROTON_DIR=$(find_proton "GE-Proton10-10")
[[ -n ${PROTON_DIR:-} && -x "$PROTON_DIR/proton" ]] || {
    echo "openbor.sh: no GE-Proton10 found" >&2; exit 70;
}

# --- logging --------------------------------------------------------------
LOG_DIR="$storageRoot/openbor/logs"
mkdir -p "$LOG_DIR" "$PREFIX"
LOG="$LOG_DIR/$DIR.log"
{
    echo "==== $(date) ===="
    echo "manifest=$MANIFEST"
    echo "game_dir=$GAME_DIR  exe=$EXE"
    echo "prefix=$PREFIX"
    echo "proton=$PROTON_DIR"
} >> "$LOG"

# --- launch via Proton ----------------------------------------------------
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
# SteamAppId helps Proton/overlays behave; 0 = generic non-Steam app.
export SteamAppId="${SteamAppId:-0}"
export WINEDEBUG="${WINEDEBUG:--all}"

SELF_DIR="$(dirname "$(readlink -f "$0")")"

# Controller whitelist — PER-SYSTEM and data-driven. OpenBOR (Windows) enumerates
# every joystick Wine exposes; too many (e.g. the 2 Sinden 32-button guns)
# overflow/crash older builds, and which pad is Player 1 depends on what's
# visible. The router's `sdl-ignore openbor` reads [backends.openbor] pad_classes
# / handheld_class and prints an SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT whitelist
# of only the chosen pad(s) that are CONNECTED (else the handheld pad).
# VERIFIED 2026-07-16 (deck-docs/openbor.md, "winebus" section): Wine's winebus
# HONORS this whitelist (bus_sdl.c / bus_udev.c call is_sdl_ignored_device) and
# it WINS over any SDL_GAMECONTROLLER_IGNORE_DEVICES blocklist — so no blocklist
# is set here anymore. CAUTION: an EMPTY whitelist string hides EVERY pad; the
# fallback chain below can never produce "", and the guard after it is insurance.
# Edit per-system in controller-policy.toml (the .local overlay wins).
# OPENBOR_SDL_ALLOW overrides for debugging; the literal is a last-resort fallback.
# Router stderr goes to the LOG (not /dev/null): its exit status and warnings
# are what tell a real "no player pads connected" from a broken router, and the
# cfg writer below refuses to touch anything unless it is sure which one it is.
WL="$("$SELF_DIR/controller-router.py" sdl-ignore openbor 2>>"$LOG")"
WL_RC=$?
export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${OPENBOR_SDL_ALLOW:-${WL:-0x28de/0x11ff,0x045e/0x02a1}}"
export SDL_JOYSTICK_HIDAPI="${SDL_JOYSTICK_HIDAPI:-0}"
echo "sdl_whitelist=$SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT" >> "$LOG"

# Defense-in-depth: never let the whitelist reach the game empty — an empty
# string means "hide every controller" (all 34 games padless). Unreachable via
# the ${...:-fallback} chain above, but cheap insurance against a future edit.
if [ -z "$SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT" ]; then
    export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="0x28de/0x11ff"
    echo "sdl_whitelist was EMPTY — forced handheld fallback pad" >> "$LOG"
fi

# (Removed 2026-07-16: the SDL_GAMECONTROLLER_IGNORE_DEVICES blocklist — dead
# code, the whitelist above wins and short-circuits it — and the
# SDL_JOYSTICK_DEVICE X-Arcade-P1 pin, a no-op under Proton: winebus enumerates
# via udev, not SDL joystick ordering, so the pin never reached the game. Player
# ordering is handled by the MAD OpenBOR pad merger (mad-openbor-pads.py, P2 of
# the input feature); pins from the Players page map to merger slots there.)

# --- pads: canonical twins (P2) or the handheld Deck pad --------------------
# The merger asks the ONE question that decides everything: are there real
# player pads to merge? (--probe exits 3 for none.) That replaces P1's
# "empty whitelist" inference, which could not tell "no pads" from "the router
# failed" — and writing a map on a failed docked launch would have clobbered
# the user's own bindings.
#
# DOCKED: mad-openbor-pads.py grabs the real pads and emits one canonical
# virtual twin per player, in OUR order (X-Arcade :1.0 -> P1). The game is
# whitelisted to see ONLY the twins, so ports are deterministic (the old
# P1/P2 half-swap is gone by construction), every player has the same shape,
# and stick+d-pad both drive movement. See mad-openbor-pads.py's header.
# HANDHELD: no merger — Steam's Deck pad is already canonical, and its Steam
# layout supplies stick->d-pad.
MERGER_PID=""
CANON=0
if (cd "$SELF_DIR" && python3 mad-openbor-pads.py --probe) >> "$LOG" 2>&1; then
    # Handshake via a file, not a pipe: a pipe nobody drains would deadlock the
    # merger the day someone adds a second print() to its stdout.
    READY_F="$(mktemp)"
    # `exec` is load-bearing, not style: without it bash forks a subshell that
    # then runs python, so $! would be the SUBSHELL and the daemon's
    # PR_SET_PDEATHSIG would bind to that subshell instead of to this script.
    # Any death of ours that skips the trap (SIGKILL, SIGHUP) would then orphan
    # the merger with EVIOCGRAB held on every pad — mute controllers rig-wide,
    # including in ES-DE, with no working pad left to fix it. With exec, python
    # IS this script's direct child, so PDEATHSIG tracks what it claims to.
    (cd "$SELF_DIR" && exec python3 mad-openbor-pads.py > "$READY_F" 2>> "$LOG") &
    MERGER_PID=$!
    # Arm the trap NOW, before the READY wait / cfg write / Proton spawn: those
    # take seconds, and until the trap exists a TERM here would leak the merger.
    trap 'kill ${game_pid:-} ${MERGER_PID:-} 2>/dev/null' TERM INT
    # The twins must EXIST before the engine's startup pad scan — it enumerates
    # once and never re-checks (these builds do not honour hotplug).
    for _ in $(seq 1 80); do
        grep -q READY "$READY_F" 2>/dev/null && break
        kill -0 "$MERGER_PID" 2>/dev/null || break
        sleep 0.1
    done
    if grep -q READY "$READY_F" 2>/dev/null; then
        sleep 0.3                       # let winebus settle on the new nodes
        export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="0x4d41/0x0002"
        CANON=1
        echo "pads: merger READY (pid $MERGER_PID) — whitelist=twins only" >> "$LOG"
    else
        echo "pads: merger failed to signal READY — falling back to raw pads" >> "$LOG"
        kill "$MERGER_PID" 2>/dev/null
        MERGER_PID=""
    fi
    rm -f "$READY_F"
elif [ "$WL_RC" -eq 0 ] && [ -z "$WL" ]; then
    CANON=1                             # handheld solo: the Deck pad IS canonical
    echo "pads: handheld — Deck pad is canonical, no merger" >> "$LOG"
else
    echo "pads: no merger and not handheld-solo (router rc=$WL_RC) — raw pads" >> "$LOG"
fi

# --- control map -------------------------------------------------------------
# Write ONLY when the game will see canonical pads — otherwise the map's
# offsets describe a device the game isn't using, and we would overwrite the
# user's own bindings with something wrong. Never write on a fallback launch.
# The engine rewrites the cfg on quit, so this launch-time write is the source
# of truth; maps live in ~/Emulation/storage/openbor/input-maps.json (via MAD).
# A skip is normal (see openbor_cfg); only a crash is an error, and even then
# the game still launches with whatever the cfg already held.
if [ "$CANON" -eq 1 ]; then
    (cd "$SELF_DIR" && python3 -m lib.openbor_cfg apply "$GAME_DIR" "$DIR") >> "$LOG" 2>&1 \
        || echo "openbor_cfg apply crashed — launching with the cfg as-is" >> "$LOG"
else
    echo "cfg map skipped (non-canonical pads) — cfg left as-is" >> "$LOG"
fi

cd "$GAME_DIR" || exit 1

# --- launch via Proton ------------------------------------------------------
# Splash handling is left ENTIRELY to the ES-DE game-start hook
# (scripts/game-start/launchscreen.sh): it shows a PERSISTENT `--hold` splash
# that stays up through the whole slow Proton/OpenBOR load (it does NOT close on
# the transient focus changes Proton causes at startup); the game window covers
# it when it finally maps, and the game-end hook closes it. We must NOT run a
# second splash here — the old "kill the hook's splash, show our own, close it on
# 'Initialized video' or 30s" logic produced a visible fade-to-black + re-show at
# the hand-off (one splash being replaced by another mid-load). Just run the game.
"$PROTON_DIR/proton" waitforexitandrun "./$EXE" "${@:2}" >> "$LOG" 2>&1 &
game_pid=$!
# (re-arm: the merger path already trapped before the READY wait; this covers
#  the handheld path, and now that game_pid exists it is in scope for both)
trap 'kill ${game_pid:-} ${MERGER_PID:-} 2>/dev/null' TERM INT

if [ -n "$MERGER_PID" ]; then
    # Wait on BOTH. If the merger dies first the game is left with no input at
    # all — the twins are gone and the real pads are hidden by the whitelist —
    # i.e. an unresponsive game in Game Mode with no way out. Killing it is the
    # kinder failure: the user lands back in ES-DE.
    wait -n "$game_pid" "$MERGER_PID"
    if ! kill -0 "$game_pid" 2>/dev/null; then
        :                                # normal: the game exited first
    else
        echo "pads: merger died first — stopping the game (it would have no input)" >> "$LOG"
        kill "$game_pid" 2>/dev/null
    fi
    wait "$game_pid" 2>/dev/null
    rc=$?
    kill "$MERGER_PID" 2>/dev/null
    wait "$MERGER_PID" 2>/dev/null
    exit $rc
fi

wait "$game_pid"
exit $?
