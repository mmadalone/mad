#!/usr/bin/env bash
# ============================================================================
# scrape-manuals.sh — fetch MANUALS ONLY from ScreenScraper into ES-DE media,
# non-destructively. Cartridge/CD/console systems (arcade family excluded by
# user request: fba/arcade/naomi/atomiswave; homebrew/hacks/launchers skipped).
#
# Per system: (1) cache manuals only (art disabled -> minimal API/bandwidth),
# (2) generate ES-DE output to a TEMP dir, (3) copy ONLY manuals/*.pdf into
# downloaded_media/<system>/manuals/. Never touches gamelists or <name>s —
# ES-DE auto-discovers manuals by ROM filename stem (e.g. "Game (USA).pdf").
#
# Resumable: a per-system sentinel in $DONE skips completed systems on re-run.
# Runs sequentially (threads=6 within a system is the ScreenScraper concurrency;
# never run systems in parallel -> ban risk).
# ============================================================================
set -o pipefail

DM=/run/media/deck/1tbDeck/downloaded_media
# Temp MUST be on a real disk, NOT /tmp — /tmp is a small tmpfs on SteamOS and the
# accumulated manual PDFs fill it, tripping Skyscraper's spaceCheck so generate
# aborts after a few games (silent partial output). ~/.cache is on /home (large).
TMP="${SKY_MANUALS_TMP:-$HOME/.cache/sky-manuals}"
GEN_ONLY="${GEN_ONLY:-0}"   # 1 = skip the ScreenScraper cache step, only (re)generate from existing cache (no API)
GLDIR="$TMP/gl"; MEDDIR="$TMP/media"; DONE="$TMP/done"
LOG="$HOME/scrape-manuals.log"
mkdir -p "$GLDIR" "$MEDDIR" "$DONE"

# esde-system : skyscraper-platform  (homebrew/hacks excluded; arcade family included)
MAP="3do:3do
amigacd32:cd32
daphne:daphne
dreamcast:dreamcast
famicom:nes
gameandwatch:gameandwatch
gba:gba
gc:gc
genesis:megadrive
mastersystem:mastersystem
n64:n64
nes:nes
pcengine:pcengine
pcenginecd:pcenginecd
pcfx:pcfx
ps2:ps2
saturn:saturn
sega32x:sega32x
segacd:segacd
sfc:snes
snes:snes
switch:switch
wii:wii
wiiu:wiiu
x68000:x68000
arcade:arcade
fba:fba
naomi:naomi
atomiswave:atomiswave"

log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
# NOTE: </dev/null is REQUIRED — distrobox enter reads stdin, and without this it
# swallows the rest of the while-read loop's here-string, so only the 1st system runs.
sky(){ distrobox enter retro-box -- bash -lc "$1" </dev/null; }

log "================ manuals scrape START ($(date)) ================"
total_copied=0
while IFS=: read -r sys plat; do
    [ -z "$sys" ] && continue
    romdir="$HOME/ROMs/$sys"
    if [ ! -d "$romdir" ]; then log "SKIP $sys — no ROM dir"; continue; fi
    if [ -f "$DONE/$sys" ]; then log "SKIP $sys — already done"; continue; fi

    if [[ $GEN_ONLY -eq 0 ]]; then
        log ">>> $sys (-p $plat): caching manuals…"
        # --refresh forces re-querying EVERY game; without it, games already in the
        # cache from prior (art-only) scrapes are served stale and their manuals
        # (never cached before) are never fetched -> 0 manuals on old systems.
        sky "Skyscraper -p $plat -s screenscraper --refresh --flags manuals,nocovers,noscreenshots,nomarquees,nowheels,nohints -i \"$romdir\"" >>"$LOG" 2>&1
    else
        log ">>> $sys (-p $plat): GEN_ONLY — reusing existing cache (no API)…"
    fi

    log ">>> $sys: generating ES-DE manuals to temp…"
    rm -rf "${MEDDIR:?}/$sys" "${GLDIR:?}/$sys"; mkdir -p "$MEDDIR/$sys" "$GLDIR/$sys"
    sky "Skyscraper -p $plat -f esde --flags manuals,nohints,unattend -i \"$romdir\" -g \"$GLDIR/$sys\" -o \"$MEDDIR/$sys\"" >>"$LOG" 2>&1

    dest="$DM/$sys/manuals"; mkdir -p "$dest"
    n=0
    while IFS= read -r f; do
        cp -f "$f" "$dest/" && n=$((n+1))
    done < <(find "$MEDDIR/$sys" -path '*/manuals/*' -type f -name '*.pdf' 2>/dev/null)
    total_copied=$((total_copied+n))
    log ">>> $sys: copied $n manuals -> $dest"
    rm -rf "${MEDDIR:?}/$sys" "${GLDIR:?}/$sys"   # free temp space so later systems don't hit a full disk
    touch "$DONE/$sys"
done <<< "$MAP"

log "================ manuals scrape COMPLETE — $total_copied manuals copied this run ================"
