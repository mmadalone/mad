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
WL="$("$SELF_DIR/controller-router.py" sdl-ignore openbor 2>/dev/null)"
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

# --- control map (input feature P1) -----------------------------------------
# On the HANDHELD-SOLO path — no player-class pad connected, so the router
# emitted an EMPTY whitelist and the literal fallback exposes the Deck pad —
# write the game's control map into its Saves/*.cfg before launch. The Deck
# pad (28de:11ff) is canonical, which is exactly what the map targets. Docked
# paths keep pre-feature behavior until the P2 pad merger lands. The engine
# rewrites the cfg on quit, so this launch-time write is the source of truth;
# maps live in ~/Emulation/storage/openbor/input-maps.json (edited via MAD).
if [ -z "$WL" ]; then
    (cd "$SELF_DIR" && python3 -m lib.openbor_cfg apply "$GAME_DIR" "$DIR") >> "$LOG" 2>&1 \
        || echo "openbor_cfg apply failed (see above) — launching with the cfg as-is" >> "$LOG"
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
