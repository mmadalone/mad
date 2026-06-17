#!/usr/bin/env bash
# ============================================================================
# deck-backup.sh — interactive backup of the deck emulation setup.
#
# Prompts for WHICH categories to back up and WHERE to write them:
#   [ES-DE settings]            ~/ES-DE (themes, gamelists, collections, scripts,
#                               settings, custom_systems) + launch screens
#   [Standalone emulator        RetroArch (+cores), Dolphin, all ~/Emulation/storage
#    settings]                  emu data (rpcs3, pcsx2, ryujinx, mugen, openbor, xemu…),
#                               bezelproject (ours), ikemen-go (mugen engine), Skraper,
#                               EmuDeck Proton/launch wrappers
#   [ROMs]                      ~/ROMs  (LARGE — separate archive, restorable anywhere)
#   [Downloaded media]          downloaded_media (LARGE — separate archive)
#
# ALWAYS included (tiny, essential core): ~/Emulation/tools/launchers (incl. THIS
#   script + deck-restore.sh), sinden-shim, fix-audio.sh, ~/Lightgun, EmuDeck
#   config, Claude memory, udev-rules mirror, cores/bezel manifests.
#
# Storage layout (per the "separate archives" choice — keeps config small/fast,
# big data isolated and store-only so already-compressed ROMs aren't re-gzipped):
#   deck-config-<ts>.tar.gz   core + ES-DE + emulator settings   (gzip)
#   deck-roms-<ts>.tar        ROMs                               (store, relative paths)
#   deck-media-<ts>.tar       downloaded_media                   (store, relative paths)
#
# Defaults (press Enter): ES-DE + emulator settings = YES, ROMs + media = NO.
#
# Flags (skip the prompts entirely; good for cron/scheduled runs):
#   --yes                 non-interactive, use defaults (ES-DE+emu, no ROMs/media)
#   --dest PATH           output directory (default ~/deck-config-backups)
#   --esde / --no-esde    include / skip ES-DE settings
#   --emu  / --no-emu     include / skip standalone emulator settings
#   --roms / --no-roms    include / skip ROMs
#   --media / --no-media  include / skip downloaded media
#   --no-cores            drop RetroArch cores (1.2 GB; restore re-downloads via manifest)
#   --no-bezels           drop bezelproject (14 GB)
#   --help
#
# Covered by default: BIOS (~/Emulation/bios) — toggle with --bios/--no-bios.
# NOT covered (deliberately): system packages themselves
#   (the sinden-reinstall-deps.sh SCRIPT is backed up; installed package binaries are
#   not), Steam library/saves (Steam cloud). NOTE: both ES-DE AppImages (the wrapper
#   ES-DE.AppImage + the ES-DE-MAD.AppImage binary) ARE now in CORE_ITEMS. Restore
#   re-derives the rest (see deck-restore.sh follow-up checklist).
#
# REBUILD PREREQ (not in any archive): the ES-DE fork SOURCE. rebuild.sh / ubuntu-build.sh
#   are backed up but need the working tree at ~/esde-build/ES-DE. Re-clone with:
#     git clone git@github.com:mmadalone/mad.git ~/esde-build/ES-DE \
#       && git -C ~/esde-build/ES-DE checkout deck-patches
#   (private repo; SSH key per esde-patched-build memory). Source is large + git-tracked,
#   so it belongs in the deck-restore.sh checklist, not this archive.
# ============================================================================
set -euo pipefail

# ---- resolve key roots ----
SETTINGS="$HOME/ES-DE/settings/es_settings.xml"
ROM_ROOT="$(readlink -f "$HOME/ROMs" 2>/dev/null || echo "$HOME/ROMs")"
MEDIA_ROOT="$(grep -oE '<string name="MediaDirectory" value="[^"]*"' "$SETTINGS" 2>/dev/null | sed -E 's/.*value="([^"]*)".*/\1/')"
# Fallback: find downloaded_media on whatever SD/USB card is mounted (don't bake
# in the card's volume name — it changes if the user swaps cards).
[ -n "$MEDIA_ROOT" ] || MEDIA_ROOT="$(ls -d /run/media/deck/*/downloaded_media 2>/dev/null | head -1)"
[ -n "$MEDIA_ROOT" ] || MEDIA_ROOT="$HOME/ES-DE/downloaded_media"
SAVES_DIR="$HOME/Emulation/saves"
BIOS_DIR="$HOME/Emulation/bios"
# Re-acquirable game data — own opt-in categories, excluded from the config archive:
RPCS3_GAMES="$HOME/Emulation/storage/rpcs3/dev_hdd0/game"   # installed PS3 games
PCSX2_TEX="$HOME/Emulation/storage/pcsx2/textures"          # HD texture packs
RYUJINX_GAMES="$HOME/Emulation/storage/ryujinx/games"       # installed Switch games

# ---- defaults (precious/small = on; re-acquirable/large = off) ----
DEST="${BACKUP_DEST:-$HOME/deck-config-backups}"
DO_ESDE=1; DO_EMU=1; DO_SAVES=1; DO_BIOS=1; DO_ROMS=0; DO_MEDIA=0
DO_RPCS3=0; DO_PCSX2TEX=0; DO_RYUJINX=0
INCLUDE_CORES=1; INCLUDE_BEZELS=0
ASSUME_YES=0; SIZES_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --sizes)      SIZES_ONLY=1; shift ;;   # print "<key>\t<bytes>" per category, then exit
        --dest)       DEST="$2"; shift 2 ;;
        --esde)       DO_ESDE=1; shift ;;   --no-esde)  DO_ESDE=0; shift ;;
        --emu)        DO_EMU=1;  shift ;;   --no-emu)   DO_EMU=0;  shift ;;
        --roms)       DO_ROMS=1; shift ;;   --no-roms)  DO_ROMS=0; shift ;;
        --media)      DO_MEDIA=1;shift ;;   --no-media) DO_MEDIA=0;shift ;;
        --saves)      DO_SAVES=1; shift ;;  --no-saves) DO_SAVES=0; shift ;;
        --bios)       DO_BIOS=1;  shift ;;  --no-bios)  DO_BIOS=0;  shift ;;
        --rpcs3)      DO_RPCS3=1; shift ;;  --no-rpcs3) DO_RPCS3=0; shift ;;
        --pcsx2tex)   DO_PCSX2TEX=1; shift ;; --no-pcsx2tex) DO_PCSX2TEX=0; shift ;;
        --ryujinx)    DO_RYUJINX=1; shift ;;  --no-ryujinx)  DO_RYUJINX=0; shift ;;
        --cores)      INCLUDE_CORES=1;  shift ;;  --no-cores)   INCLUDE_CORES=0;  shift ;;
        --bezels)     INCLUDE_BEZELS=1; shift ;;  --no-bezels)  INCLUDE_BEZELS=0; shift ;;
        --help|-h)    sed -n '2,52p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

log()  { echo "[backup] $*"; }
warn() { echo "[backup] WARN: $*" >&2; }
die()  { echo "[backup] FATAL: $*" >&2; exit 1; }
hsize(){ du -sh "$1" 2>/dev/null | cut -f1; }   # human size of a path (may be slow on huge trees)

# ---- interactive prompts ----
ask() { # ask "Question" default(Y/N) -> sets REPLY_BOOL
    local q="$1" def="$2" ans
    if [[ $ASSUME_YES -eq 1 ]]; then REPLY_BOOL=$([[ $def == Y ]] && echo 1 || echo 0); return; fi
    read -rp "$q [$([[ $def == Y ]] && echo 'Y/n' || echo 'y/N')] " ans
    ans="${ans:-$def}"
    REPLY_BOOL=$([[ $ans =~ ^[Yy] ]] && echo 1 || echo 0)
}

if [[ $ASSUME_YES -eq 0 && $SIZES_ONLY -eq 0 ]]; then
    echo "=== deck backup — choose what to include ==="
    read -rp "Backup destination directory [$DEST] " _d; DEST="${_d:-$DEST}"
    ask "Back up ES-DE settings (~$(hsize "$HOME/ES-DE"))?"            Y; DO_ESDE=$REPLY_BOOL
    ask "Back up emulator config + data (RA config, storage minus game data, Dolphin…)?" Y; DO_EMU=$REPLY_BOOL
    ask "Back up emulator saves (~$(hsize "$SAVES_DIR"))?"        Y; DO_SAVES=$REPLY_BOOL
    ask "Back up BIOS (~$(hsize "$BIOS_DIR"))?"                   Y; DO_BIOS=$REPLY_BOOL
    ask "Back up RPCS3 installed PS3 games (LARGE, ~$(hsize "$RPCS3_GAMES"))?" N; DO_RPCS3=$REPLY_BOOL
    ask "Back up PCSX2 HD textures (~$(hsize "$PCSX2_TEX"))?"     N; DO_PCSX2TEX=$REPLY_BOOL
    ask "Back up Ryujinx games (~$(hsize "$RYUJINX_GAMES"))?"     N; DO_RYUJINX=$REPLY_BOOL
    ask "Back up ROMs (LARGE, ~$(hsize "$ROM_ROOT"); separate archive)?"   N; DO_ROMS=$REPLY_BOOL
    ask "Back up downloaded media (LARGE, ~$(hsize "$MEDIA_ROOT"); separate archive)?" N; DO_MEDIA=$REPLY_BOOL
fi

TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DEST"

# ---- refresh udev mirror + manifests (so restore can rebuild without sudo/network) ----
LIVE_UDEV="/etc/udev/rules.d/99-sinden-lightgun.rules"
ETC_MIRROR="$HOME/Emulation/tools/launchers/sinden-shim/etc-backup/99-sinden-lightgun.rules"
mkdir -p "$(dirname "$ETC_MIRROR")"
[[ -r $LIVE_UDEV ]] && cp "$LIVE_UDEV" "$ETC_MIRROR" && log "udev rules mirror refreshed" || warn "can't read $LIVE_UDEV"

CORES_DIR="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/cores"
CORES_MANIFEST="$HOME/Emulation/tools/launchers/.cores-manifest.txt"
[[ -d $CORES_DIR ]] && { ls "$CORES_DIR" | grep '_libretro\.so$' > "$CORES_MANIFEST" || true; } || : > "$CORES_MANIFEST"
BEZEL_DIR="$HOME/Emulation/tools/bezelproject"
BEZEL_MANIFEST="$HOME/Emulation/tools/launchers/.bezel-manifest.txt"
[[ -d $BEZEL_DIR ]] && { ls "$BEZEL_DIR" | grep '^bezelproject-' > "$BEZEL_MANIFEST" || true; } || : > "$BEZEL_MANIFEST"

# ---- assemble config-archive item list ----
CORE_ITEMS=(
    "$HOME/Emulation/tools/launchers"
    "$HOME/Emulation/tools/fix-audio.sh"
    "$HOME/Emulation/tools/smb.conf"
    "$HOME/Lightgun"
    "$HOME/.config/EmuDeck"
    "$HOME/.claude/projects/ES-DE-MAD/memory"
    "$HOME/Applications/ES-DE.AppImage"
    "$HOME/Applications/ES-DE-MAD.AppImage"
    "$HOME/esde-build/ubuntu-build.sh"
    "$HOME/esde-build/rebuild.sh"
)
ESDE_ITEMS=( "$HOME/ES-DE" )
EMU_ITEMS=(
    "$HOME/.var/app/org.libretro.RetroArch/config/retroarch"
    "$HOME/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
    "$HOME/Emulation/storage"
    "$HOME/Emulation/tools/ikemen-go"
    "$HOME/Emulation/tools/ikemen-go-v0.99.0"
    "$HOME/Emulation/tools/Skraper-1.1.1"
    "$HOME/Emulation/tools/emu-launch.sh"
    "$HOME/Emulation/tools/proton-launch.sh"
)
[[ $INCLUDE_BEZELS -eq 1 ]] && EMU_ITEMS+=( "$HOME/Emulation/tools/bezelproject" )

# --sizes: emit disjoint per-category byte sizes for the MAD backup page, then exit.
# emu excludes cores+bezels (they're shown as their own toggles, so no double-count).
if [[ $SIZES_ONLY -eq 1 ]]; then
    _b(){ du -scb "$@" 2>/dev/null | tail -1 | cut -f1; }   # grand-total bytes (0 if absent)
    printf 'esde\t%s\n'   "$(_b "${ESDE_ITEMS[@]}")"
    printf 'emu\t%s\n'    "$(du -scb --exclude="$CORES_DIR" --exclude="$BEZEL_DIR" \
                                  --exclude="$RPCS3_GAMES" --exclude="$PCSX2_TEX" --exclude="$RYUJINX_GAMES" \
                                  "${EMU_ITEMS[@]}" 2>/dev/null | tail -1 | cut -f1)"
    printf 'saves\t%s\n'  "$(_b "$SAVES_DIR")"
    printf 'bios\t%s\n'   "$(_b "$BIOS_DIR")"
    printf 'cores\t%s\n'  "$(_b "$CORES_DIR")"
    printf 'bezels\t%s\n' "$(_b "$BEZEL_DIR")"
    printf 'rpcs3games\t%s\n'   "$(_b "$RPCS3_GAMES")"
    printf 'pcsx2tex\t%s\n'     "$(_b "$PCSX2_TEX")"
    printf 'ryujinxgames\t%s\n' "$(_b "$RYUJINX_GAMES")"
    printf 'roms\t%s\n'   "$(_b "$ROM_ROOT")"
    printf 'media\t%s\n'  "$(_b "$MEDIA_ROOT")"
    exit 0
fi

CONFIG_ITEMS=( "${CORE_ITEMS[@]}" )
[[ $DO_ESDE  -eq 1 ]] && CONFIG_ITEMS+=( "${ESDE_ITEMS[@]}" )
[[ $DO_EMU   -eq 1 ]] && CONFIG_ITEMS+=( "${EMU_ITEMS[@]}" )
# cores/bezels are INDEPENDENT toggles on the MAD Backup page: with emu off
# they must still be includable on their own (they normally ride inside the
# EMU_ITEMS RetroArch-config / bezelproject entries).
[[ $DO_EMU -eq 0 && $INCLUDE_CORES  -eq 1 ]] && CONFIG_ITEMS+=( "$CORES_DIR" )
[[ $DO_EMU -eq 0 && $INCLUDE_BEZELS -eq 1 ]] && CONFIG_ITEMS+=( "$BEZEL_DIR" )
[[ $DO_SAVES -eq 1 ]] && CONFIG_ITEMS+=( "$SAVES_DIR" )
[[ $DO_BIOS  -eq 1 ]] && CONFIG_ITEMS+=( "$BIOS_DIR" )

# keep only existing paths
REAL_ITEMS=()
for p in "${CONFIG_ITEMS[@]}"; do
    [[ -e $p ]] && REAL_ITEMS+=( "$p" ) || warn "skipping (absent): $p"
done

# Re-acquirable game data lives under storage but is backed up via its OWN opt-in
# archives, so ALWAYS exclude it from the config archive.
EXCLUDES=( --exclude='*.cache' --exclude='core_logs' --exclude='shader_cache'
           --exclude="$RPCS3_GAMES" --exclude="$PCSX2_TEX" --exclude="$RYUJINX_GAMES" )
[[ $INCLUDE_CORES -eq 0 ]] && EXCLUDES+=( --exclude="$CORES_DIR" )

# ---- free-space guard (rough: sum du of selected, compare to dest free) ----
need_kb=$(du -sck --exclude="$RPCS3_GAMES" --exclude="$PCSX2_TEX" --exclude="$RYUJINX_GAMES" \
              "${REAL_ITEMS[@]}" 2>/dev/null | tail -1 | cut -f1) || true
need_kb=${need_kb:-0}
[[ $DO_ROMS     -eq 1 && -d $ROM_ROOT      ]] && need_kb=$(( need_kb + $(du -sk "$ROM_ROOT" 2>/dev/null | cut -f1) ))
[[ $DO_MEDIA    -eq 1 && -d $MEDIA_ROOT    ]] && need_kb=$(( need_kb + $(du -sk "$MEDIA_ROOT" 2>/dev/null | cut -f1) ))
[[ $DO_RPCS3    -eq 1 && -d $RPCS3_GAMES   ]] && need_kb=$(( need_kb + $(du -sk "$RPCS3_GAMES" 2>/dev/null | cut -f1) ))
[[ $DO_PCSX2TEX -eq 1 && -d $PCSX2_TEX     ]] && need_kb=$(( need_kb + $(du -sk "$PCSX2_TEX" 2>/dev/null | cut -f1) ))
[[ $DO_RYUJINX  -eq 1 && -d $RYUJINX_GAMES ]] && need_kb=$(( need_kb + $(du -sk "$RYUJINX_GAMES" 2>/dev/null | cut -f1) ))
free_kb=$(df -Pk "$DEST" | awk 'NR==2{print $4}')
log "estimated source: $((need_kb/1024/1024)) GB   free at dest: $((free_kb/1024/1024)) GB"
[[ ${free_kb:-0} -lt $need_kb ]] && die "not enough free space at $DEST (need ~$((need_kb/1024/1024))G, have $((free_kb/1024/1024))G)"

COMPRESSOR=$(command -v pigz || echo gzip)
made=()

# store-archive helper (no compression — large binary game data): tar a $HOME-relative
# path into deck-<name>-<TS>.tar, verify, record it. Skips silently if the source is absent.
store_archive(){  # $1=label  $2=abs source dir  $3=archive basename
    [[ -d "$2" ]] || { warn "$1 requested but $2 not found — skipped"; return; }
    local rel="${2#"$HOME"/}" OUT TMP
    OUT="$DEST/deck-$3-$TS.tar"; TMP="$OUT.partial"
    log "=== $1 archive (store) -> $OUT  [large] ==="
    set +e
    tar --warning=no-file-changed -C "$HOME" -cf "$TMP" "$rel" \
        2> >(grep -v 'file changed as we read it' >&2)
    set -e
    tar -tf "$TMP" >/dev/null 2>&1 || die "$1 archive verify failed"
    mv "$TMP" "$OUT"; made+=( "$OUT" ); log "  ok: $(du -h "$OUT" | cut -f1)"
}

# ---- 1) config archive (gzip) ----
if [[ ${#REAL_ITEMS[@]} -gt 0 ]]; then
    OUT="$DEST/deck-config-$TS.tar.gz"; TMP="$OUT.partial"
    log "=== config archive -> $OUT ==="
    log "  ES-DE=$([[ $DO_ESDE == 1 ]] && echo yes || echo no)  emu=$([[ $DO_EMU == 1 ]] && echo yes || echo no)  cores=$([[ $INCLUDE_CORES == 1 ]] && echo yes || echo no)  bezels=$([[ $INCLUDE_BEZELS == 1 ]] && echo yes || echo no)"
    set +e
    tar --warning=no-file-changed --use-compress-program="$COMPRESSOR" "${EXCLUDES[@]}" \
        -cf "$TMP" "${REAL_ITEMS[@]}" 2> >(grep -v 'file changed as we read it' >&2)
    set -e
    tar -tzf "$TMP" >/dev/null 2>&1 || die "config archive verify failed"
    mv "$TMP" "$OUT"; made+=( "$OUT" ); log "  ok: $(du -h "$OUT" | cut -f1)"
fi

# ---- 2) ROMs archive (store, relative paths so restore can target any drive) ----
if [[ $DO_ROMS -eq 1 ]]; then
    if [[ -d $ROM_ROOT ]]; then
        OUT="$DEST/deck-roms-$TS.tar"; TMP="$OUT.partial"
        log "=== ROMs archive (store) -> $OUT  [this is large] ==="
        set +e
        tar --warning=no-file-changed -C "$(dirname "$ROM_ROOT")" -cf "$TMP" "$(basename "$ROM_ROOT")" \
            2> >(grep -v 'file changed as we read it' >&2)
        set -e
        tar -tf "$TMP" >/dev/null 2>&1 || die "ROMs archive verify failed"
        mv "$TMP" "$OUT"; made+=( "$OUT" ); log "  ok: $(du -h "$OUT" | cut -f1)"
    else warn "ROMs requested but $ROM_ROOT not found (SD unmounted?) — skipped"; fi
fi

# ---- 3) downloaded_media archive (store, relative paths) ----
if [[ $DO_MEDIA -eq 1 ]]; then
    if [[ -d $MEDIA_ROOT ]]; then
        OUT="$DEST/deck-media-$TS.tar"; TMP="$OUT.partial"
        log "=== media archive (store) -> $OUT  [this is large] ==="
        set +e
        tar --warning=no-file-changed -C "$(dirname "$MEDIA_ROOT")" -cf "$TMP" "$(basename "$MEDIA_ROOT")" \
            2> >(grep -v 'file changed as we read it' >&2)
        set -e
        tar -tf "$TMP" >/dev/null 2>&1 || die "media archive verify failed"
        mv "$TMP" "$OUT"; made+=( "$OUT" ); log "  ok: $(du -h "$OUT" | cut -f1)"
    else warn "media requested but $MEDIA_ROOT not found — skipped"; fi
fi

# ---- 4) re-acquirable game-data archives (store, opt-in) ----
[[ $DO_RPCS3    -eq 1 ]] && store_archive "RPCS3 PS3 games"   "$RPCS3_GAMES"   "rpcs3-games"
[[ $DO_PCSX2TEX -eq 1 ]] && store_archive "PCSX2 HD textures" "$PCSX2_TEX"     "pcsx2-textures"
[[ $DO_RYUJINX  -eq 1 ]] && store_archive "Ryujinx games"     "$RYUJINX_GAMES" "ryujinx-games"

# ---- summary ----
echo
log "=== backup complete ==="
for f in "${made[@]}"; do log "  $(du -h "$f" | cut -f1)  $f"; done
log ""
log "Restore with:  bash $HOME/Emulation/tools/launchers/deck-restore.sh"
log "  (point it at $DEST — it will prompt for ROMs/media restore locations)"

# prune old CONFIG archives only (keep BACKUP_RETENTION_COUNT, default 5). Never
# auto-prune roms/media (huge, manual).
KEEP="${BACKUP_RETENTION_COUNT:-5}"
ls -t "$DEST"/deck-config-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" \
    | xargs -r rm -v 2>&1 | sed 's/^/[backup] pruned: /' || true
exit 0
