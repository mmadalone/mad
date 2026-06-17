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
. "$SELF_DIR/lib/mad-paths.sh"
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
  if [ -n "$_ff" ]; then
    _gd="$(dirname "$_ff")"
    set -- "$@" -homedir "$_gd" -datadir "$(dirname "$HB")"
  fi
fi

# Global Hypseus args set from MAD's Daphne page (e.g. "instant scene transitions" =
# -seek_frames_per_ms 0), applied to EVERY laserdisc launch. Word-split is intentional
# (these are flags); absent/empty file = nothing appended. Per-game flags ride %INJECT%.
_gargs="$storageRoot/hypseus/global-args"
if [ -s "$_gargs" ]; then
  # shellcheck disable=SC2046
  set -- "$@" $(cat "$_gargs" 2>/dev/null)
fi

# SINGE games: Hypseus chdir()s to its OWN install dir at startup (set_cur_dir(argv[0]) in
# src/hypseus.cpp), so a Singe script's relative dofile("singe/<x>/...") + its image/sound/font
# loads resolve THERE, not in the game dir — and -homedir does NOT move the CWD. So mirror the
# game's singe/ tree into the install dir's singe/ (the big videos stay external, found by VLDP
# via -homedir/framefile). Best-effort, small (~hundreds of KB), re-synced each launch (so it
# self-heals if the hypseus install is refreshed); only runs for Singe (-script) launches.
if [ -n "${_gd:-}" ] && printf '%s\n' "$@" | grep -qx -- '-script' && [ -d "$_gd/singe" ]; then
  _isd="$(dirname "$HB")/singe"
  mkdir -p "$_isd" 2>/dev/null && cp -ru "$_gd/singe/." "$_isd/" 2>/dev/null || true
fi

# Capture Hypseus's OWN stdout/stderr to a rolling per-run log via `tee` — NOT a `> file`
# redirect. CRITICAL: ES-DE's launchGameUnix() launches the command with a trailing `&` and stays
# blocked ONLY by reading the command's stdout PIPE until it closes (= until Hypseus exits). A
# `> "$HRUNLOG"` redirect would detach stdout from that pipe, closing it immediately, so ES-DE
# would think the game ended at once and RESUME the gamelist — replaying the preview video + audio
# behind the running game. `tee` keeps the output flowing to ES-DE's pipe AND to the log, so ES-DE
# blocks correctly until Hypseus quits. Ephemeral ($XDG_RUNTIME_DIR cleared on reboot).
HRUNLOG="${XDG_RUNTIME_DIR:-/tmp}/hypseus-run.log"
echo "hypseus-pin: run $HB $*" >> "$LOG"
"$HB" "$@" 2>&1 | tee "$HRUNLOG"
exit "${PIPESTATUS[0]}"
