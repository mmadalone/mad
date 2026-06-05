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
SHARED_PREFIX="$HOME/Emulation/storage/openbor/prefix"

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
LOG_DIR="$HOME/Emulation/storage/openbor/logs"
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
# EVERY raw joystick Wine exposes; too many (e.g. the 2 Sinden 32-button guns)
# overflow/crash older builds, and which pad is Player 1 depends on what's
# visible. The router's `sdl-ignore openbor` reads [backends.openbor] pad_classes
# / handheld_class and prints an SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT whitelist
# of only the chosen pad(s) that are CONNECTED (else the handheld pad) — so the
# X-Arcade is P1/P2 when docked, the Deck pad when handheld, Sinden always hidden.
# Edit it per-system in controller-policy.toml. OPENBOR_SDL_ALLOW overrides;
# the literal is a last-resort fallback.
WL="$("$SELF_DIR/controller-router.py" sdl-ignore openbor 2>/dev/null)"
export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${OPENBOR_SDL_ALLOW:-${WL:-0x28de/0x11ff,0x045e/0x02a1}}"
export SDL_JOYSTICK_HIDAPI="${SDL_JOYSTICK_HIDAPI:-0}"
echo "sdl_whitelist=$SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT" >> "$LOG"

# OpenBOR is a Windows app under Proton/Wine, whose winebus controller layer
# IGNORES the _EXCEPT whitelist above (that only filters the native SDL
# GameController API). So ALSO emit an SDL_GAMECONTROLLER_IGNORE_DEVICES BLOCKLIST
# of every connected pad EXCEPT the chosen top family — i.e. hide the Steam Deck
# pad + any extra controllers so only the X-Arcade (or whatever's chosen) reaches
# the game and becomes P1/P2 (winebus honors the IGNORE blocklist). OPENBOR_SDL_IGNORE
# overrides for debugging.
BL="$("$SELF_DIR/controller-router.py" sdl-ignore-list openbor 2>/dev/null)"
[ -n "${OPENBOR_SDL_IGNORE:-$BL}" ] && export SDL_GAMECONTROLLER_IGNORE_DEVICES="${OPENBOR_SDL_IGNORE:-$BL}"
echo "sdl_ignore=${SDL_GAMECONTROLLER_IGNORE_DEVICES:-}" >> "$LOG"

# --- X-Arcade-as-P1 ---------------------------------------------------------
# The X-Arcade receiver presents 2 slots (USB interfaces :1.0 = player 1,
# :1.1 = player 2); Wine inflates them to extra XInput pads and OpenBOR's
# joystick #1 picked the wrong one, so P1 menu nav failed (and pinning the wrong
# slot just swapped P1/P2). SDL_JOYSTICK_DEVICE makes the named node SDL's FIRST
# joystick (verified: it reorders, both X-Arcade players still enumerate). So pin
# the player-1 (:1.0) slot → it becomes joy #1 = OpenBOR P1, and the :1.1 slot
# stays joy #2 = P2. Detected by USB interface (NOT the unstable eventN number),
# only when the X-Arcade is the whitelisted family. No :1.0 slot found → no pin
# (safe fallback to prior behavior).
if [[ "$SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT" == *045e* && "$SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT" == *02a1* ]]; then
    XPIN=""
    for d in /sys/class/input/event*/device; do
        [ "$(cat "$d/id/vendor" 2>/dev/null)" = "045e" ] && \
        [ "$(cat "$d/id/product" 2>/dev/null)" = "02a1" ] || continue
        case "$(readlink -f "$d" 2>/dev/null)" in
            *:1.0/*) XPIN="/dev/input/$(basename "$(dirname "$d")")" ;;
        esac
    done
    if [ -n "$XPIN" ]; then
        export SDL_JOYSTICK_DEVICE="$XPIN"
        echo "x-arcade P1 pin (:1.0 slot) = $XPIN" >> "$LOG"
    else
        echo "x-arcade P1 pin skipped (no :1.0 slot found)" >> "$LOG"
    fi
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
trap 'kill "$game_pid" 2>/dev/null' TERM INT
wait "$game_pid"
exit $?
