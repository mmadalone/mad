#!/usr/bin/env bash
# Hypseus (Daphne/Singe) launch wrapper — router-integrated, modeled on openbor.sh.
#
# Hypseus runs NATIVELY (SDL2 Joystick API). Instead of hardcoding which pads to
# hide, we ask the controller-router for the SDL device whitelist/blocklist derived
# from [backends.hypseus] (resolved via system "daphne") so ONLY the X-Arcade
# reaches Hypseus and the DualSense / Steam Deck pad / Sinden guns are ignored.
# Tune which pads are exposed in controller-policy.toml [backends.hypseus] (or the
# router-config GUI Backends page) — no longer hardcoded here.
#
# Relies on Steam Input being OFF for the ES-DE shortcut (raw vid:pids visible to
# SDL, like OpenBOR). Reversible: the daphne es_systems command points here instead
# of bare hypseus.bin. Overrides: HYPSEUS_SDL_ALLOW (whitelist) / HYPSEUS_SDL_IGNORE
# (blocklist) force the value, bypassing the router.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${XDG_RUNTIME_DIR:-/tmp}/hypseus-pin.log"

# Whitelist (_EXCEPT): keep ONLY the chosen player family (X-Arcade). Native SDL2
# honors the _EXCEPT hint directly for BOTH the Joystick and GameController
# subsystems (Hypseus uses the Joystick API). Empty -> don't set it.
WL="$("$SELF_DIR/controller-router.py" sdl-ignore daphne 2>>"$LOG")"
if [ -n "${HYPSEUS_SDL_ALLOW:-$WL}" ]; then
  export SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT="${HYPSEUS_SDL_ALLOW:-$WL}"
  export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${HYPSEUS_SDL_ALLOW:-$WL}"
fi

# Blocklist: also hide everything that is NOT a player pad (the Deck virtual pad,
# Sinden guns, unlisted devices) — belt-and-suspenders for any SDL path that
# ignores the _EXCEPT whitelist. Disjoint from the whitelist, so they compose.
BL="$("$SELF_DIR/controller-router.py" sdl-ignore-list daphne 2>>"$LOG")"
if [ -n "${HYPSEUS_SDL_IGNORE:-$BL}" ]; then
  export SDL_JOYSTICK_IGNORE_DEVICES="${HYPSEUS_SDL_IGNORE:-$BL}"
  export SDL_GAMECONTROLLER_IGNORE_DEVICES="${HYPSEUS_SDL_IGNORE:-$BL}"
fi
echo "hypseus-pin: whitelist=${SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT:-} blocklist=${SDL_JOYSTICK_IGNORE_DEVICES:-}" >> "$LOG"

HB="$(command -v hypseus.bin 2>/dev/null || echo "$HOME/Applications/hypseus-singe/hypseus.bin")"

# Point Hypseus at the game's .daphne dir for its homedir (roms/<driver>.zip, the framefile
# and the video all resolve relative to it) and at the Hypseus install for its datadir
# (fonts/pics/sound). The es_systems command passes no -homedir and ES-DE's launch cwd is NOT
# the game dir, so without this Hypseus searches the wrong roms/ and aborts with
# "Could not load ROM images!". Derive the game dir from the -framefile argument; skip if the
# caller already passed an explicit -homedir.
if ! printf '%s\n' "$@" | grep -qx -- '-homedir'; then
  _ff=""; _prev=""
  for _a in "$@"; do [ "$_prev" = "-framefile" ] && _ff="$_a"; _prev="$_a"; done
  [ -n "$_ff" ] && set -- "$@" -homedir "$(dirname "$_ff")" -datadir "$(dirname "$HB")"
fi

exec "$HB" "$@"
