#!/bin/bash
# singe-indexer.sh — pre-build Hypseus laserdisc seek-indexes (the sidecar <video>.dat files).
#
# WHY: Hypseus v2.11.6 stores each .m2v's seek index in <video>.dat. Rips ship the old version
# (header byte 0x02 0x01); Hypseus REBUILDS any non-0x0301 index to 0x03 01 the first time it seeks
# into that .m2v during play — and that rebuild is the visible "seeking" pause. This tool forces
# the rebuild up front by driving a tiny Singe script that seeks once into every not-yet-0301
# segment, so later play has no pauses. The .dat is written by the SHARED VLDP layer, so a Singe
# seek builds the exact index a Daphne game (Dragon's Lair, …) will reuse.
#
# MUST run ON-SCREEN: a real renderer is required — a headless (SDL dummy) run does NOT build them.
#
#   Usage: singe-indexer.sh all | <game-folder>      e.g.  singe-indexer.sh gpworld.daphne
#
set -u

ROMS="${ROMS_DAPHNE:-$HOME/ROMs/daphne}"
[ -d "$ROMS" ] || ROMS="/run/media/deck/1tbDeck/ROMs/daphne"
HB="$(command -v hypseus.real 2>/dev/null || echo "$HOME/Applications/hypseus-singe/hypseus.real")"
HDIR="$(dirname "$HB")"
WORK="${XDG_RUNTIME_DIR:-/tmp}"
LOG="$WORK/singe-indexer.log"
: > "$LOG"

log(){ printf '[indexer] %s\n' "$*" | tee -a "$LOG" >&2; }
dat_ver(){ xxd -l2 -p "$1" 2>/dev/null; }

# Resolve a game's framefile: a -framefile override in <base>.commands, else <base>.txt.
framefile_for(){
  local gd="$1" base ff="" cmd
  base="$(basename "$gd")"; base="${base%.*}"
  cmd="$gd/$base.commands"
  [ -f "$cmd" ] && ff="$(grep -oE -- '-framefile +[^ ]+' "$cmd" | awk '{print $2}' | head -1)"
  [ -z "$ff" ] && ff="$base.txt"
  case "$ff" in /*) printf '%s\n' "$ff";; *) printf '%s/%s\n' "$gd" "$ff";; esac
}

# Count how many of a game's segments still need indexing (.dat not 0301).
count_pending(){
  local ff="$1" gd="$2" n=0 fr m2v dat
  while read -r fr m2v _ || [ -n "$fr" ]; do          # '|| [ -n ]' = process a final unterminated line
    case "$fr" in ''|*[!0-9]*) continue;; esac
    m2v="${m2v%$'\r'}"                               # framefiles are CRLF
    [ -n "$m2v" ] || continue
    dat="$gd/${m2v%.m2v}.dat"
    [ "$(dat_ver "$dat")" != "0301" ] && n=$((n+1))
  done < "$ff"
  printf '%s\n' "$n"
}

index_game(){
  local gd="$1" base ff total=0 targets=0 frames=() fr m2v dat
  [ -d "$gd" ] || { log "$(basename "$gd"): not found, skip"; return 1; }
  base="$(basename "$gd")"; base="${base%.*}"
  ff="$(framefile_for "$gd")"
  [ -f "$ff" ] || { log "$base: framefile not found ($ff), skip"; return 1; }

  while read -r fr m2v _ || [ -n "$fr" ]; do         # '|| [ -n ]' = process a final unterminated line
    case "$fr" in ''|*[!0-9]*) continue;; esac      # skip the leading "." and blank lines
    m2v="${m2v%$'\r'}"                              # framefiles are CRLF
    [ -n "$m2v" ] || continue
    total=$((total+1))
    dat="$gd/${m2v%.m2v}.dat"
    [ "$(dat_ver "$dat")" = "0301" ] && continue     # already current — skip
    frames+=("$fr"); targets=$((targets+1))
  done < "$ff"

  if [ "$targets" -eq 0 ]; then
    log "$base: all $total segments already 0301 — nothing to do"; return 0
  fi
  log "$base: $targets of $total segments need indexing — building…"

  # Generate the self-contained per-game indexer Singe script (no dofile → location-independent).
  local script="$WORK/mad-indexer-$base.singe" frames_csv
  frames_csv="$(IFS=,; printf '%s' "${frames[*]}")"
  {
    printf 'OVERLAY_UPDATED = 1\n'
    printf 'FRAMES = { %s }\n' "$frames_csv"
    printf 'N = %s\n' "$targets"
    cat <<'LUA'
idx = 1; seeking = false; ticks = 0; inited = false; done = false
function onInputPressed(w) end
function onInputReleased(w) end
function onMouseMoved(x, y) end
function onSoundCompleted(s) end
function onShutdown() discStop() end
function onOverlayUpdate()
  overlayClear()
  if not inited then discSetFPS(29.97); inited = true end
  if idx > N then
    if not done then discStop(); debugPrint("INDEXER DONE"); done = true end
    return OVERLAY_UPDATED
  end
  local target = FRAMES[idx]
  ticks = ticks + 1
  if not seeking then
    discSkipToFrame(target); discPause()
    seeking = true; ticks = 0
    debugPrint("INDEXER SEEK " .. idx .. "/" .. N .. " -> " .. target)
  else
    local cur = discGetFrame()
    -- Landed once we've reached the target (parse of that .m2v is complete) and settled a moment;
    -- the big ticks cap is a per-segment safety timeout for very large .m2v files.
    if (ticks >= 20 and cur >= target - 5) or ticks >= 2000 then
      debugPrint("INDEXER LAND " .. idx .. " cur=" .. cur .. " ticks=" .. ticks)
      idx = idx + 1; seeking = false
    end
  end
  return OVERLAY_UPDATED
end
LUA
  } > "$script"

  log "$base: BEFORE — pending=$targets/$total"
  if [ -n "${SINGE_INDEXER_DRYRUN:-}" ]; then
    log "$base: DRY RUN — generated $script:"; sed 's/^/    /' "$script" >&2; rm -f "$script"; return 0
  fi
  log "$base: launching Hypseus indexer on-screen (quit combo aborts)…"
  "$HB" singe vldp -framefile "$ff" -script "$script" -homedir "$gd" -datadir "$HDIR" -fullscreen \
    >>"$LOG" 2>&1 &
  local hp=$!
  # shellcheck disable=SC2064
  trap "kill -KILL $hp 2>/dev/null" RETURN

  local waited=0 max=$(( targets * 30 + 60 )) left
  while kill -0 "$hp" 2>/dev/null; do
    sleep 2; waited=$((waited+2))
    left="$(count_pending "$ff" "$gd")"
    if [ "$left" -eq 0 ]; then
      log "$base: all segments now 0301 after ${waited}s — stopping Hypseus"; break
    fi
    if [ "$waited" -ge "$max" ]; then
      log "$base: TIMEOUT after ${waited}s ($left still pending) — stopping Hypseus"; break
    fi
  done
  kill -KILL "$hp" 2>/dev/null; wait "$hp" 2>/dev/null
  trap - RETURN

  left="$(count_pending "$ff" "$gd")"
  log "$base: AFTER — pending=$left/$total ($((total-left)) segments at 0301)"
  rm -f "$script"
  [ "$left" -eq 0 ]
}

main(){
  local arg="${1:-}"
  [ -z "$arg" ] && { echo "usage: $(basename "$0") all | <game-folder>   (e.g. gpworld.daphne)"; exit 2; }
  log "hypseus=$HB  roms=$ROMS"
  if [ "$arg" = "all" ]; then
    local gd
    for gd in "$ROMS"/*.daphne "$ROMS"/*.singe; do [ -d "$gd" ] && index_game "$gd"; done
  else
    index_game "$ROMS/$arg"
  fi
  log "ALL DONE — full log: $LOG"
}
main "$@"
