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
#   --list-items          print every path that WOULD be archived, then exit (no tar)
#   --list-library-items  print "<key>\t<path>" per big-library category, then exit
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
ROM_INT="$HOME/Emulation/roms"   # internal ROM store (ps2/ps3/switch/... ~280G), separate from ~/ROMs
OPENBOR_ROOT="$HOME/OpenBor"     # OpenBOR games (the ~/ROMs/openbor symlink target)
# `|| true` is LOAD-BEARING, not noise: under `set -euo pipefail` a grep that
# matches nothing (no es_settings.xml yet, or no MediaDirectory line) exits
# non-zero, the assignment inherits that, and the script DIES here — taking the
# three fallbacks below with it, unreachable. `2>/dev/null` hides the message,
# never the status. That made deck-backup.sh unrunnable on exactly the rig a
# backup tool is for: a fresh one with no ES-DE settings yet. Found 2026-07-17 by
# the new tests/test_backup_items.py, which runs this script against a temp $HOME.
MEDIA_ROOT="$(grep -oE '<string name="MediaDirectory" value="[^"]*"' "$SETTINGS" 2>/dev/null | sed -E 's/.*value="([^"]*)".*/\1/' || true)"
# Fallback: find downloaded_media on whatever SD/USB card is mounted (don't bake
# in the card's volume name — it changes if the user swaps cards).
[ -n "$MEDIA_ROOT" ] || MEDIA_ROOT="$(ls -d /run/media/deck/*/downloaded_media 2>/dev/null | head -1 || true)"
[ -n "$MEDIA_ROOT" ] || MEDIA_ROOT="$HOME/ES-DE/downloaded_media"
. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
SAVES_DIR="$savesRoot"
BIOS_DIR="$biosRoot"
# Re-acquirable game data — own opt-in categories, excluded from the config archive:
RPCS3_GAMES="$storageRoot/rpcs3/dev_hdd0/game"   # installed PS3 games
PCSX2_TEX="$storageRoot/pcsx2/textures"          # HD texture packs
RYUJINX_GAMES="$storageRoot/ryujinx/games"       # installed Switch games

# ---- defaults (precious/small = on; re-acquirable/large = off) ----
DEST="${BACKUP_DEST:-$HOME/deck-config-backups}"
DO_ESDE=1; DO_EMU=1; DO_SAVES=1; DO_BIOS=1; DO_ROMS=0; DO_MEDIA=0
DO_RPCS3=0; DO_PCSX2TEX=0; DO_RYUJINX=0
DO_ROMSINT=0; DO_OPENBOR=0
INCLUDE_CORES=1; INCLUDE_BEZELS=0
ASSUME_YES=0; SIZES_ONLY=0; LIST_ONLY=0; LIST_LIB=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --sizes)      SIZES_ONLY=1; shift ;;   # print "<key>\t<bytes>" per category, then exit
        --list-items) LIST_ONLY=1; ASSUME_YES=1; shift ;;  # print every path that WOULD be archived, then exit
        --list-library-items) LIST_LIB=1; ASSUME_YES=1; shift ;;  # print "<key>\t<path>" per big-library category, then exit
        --dest)       DEST="${2:?--dest needs a path}"; shift 2 ;;
        --esde)       DO_ESDE=1; shift ;;   --no-esde)  DO_ESDE=0; shift ;;
        --emu)        DO_EMU=1;  shift ;;   --no-emu)   DO_EMU=0;  shift ;;
        --roms)       DO_ROMS=1; shift ;;   --no-roms)  DO_ROMS=0; shift ;;
        --romsint)    DO_ROMSINT=1; shift ;; --no-romsint) DO_ROMSINT=0; shift ;;
        --openbor)    DO_OPENBOR=1; shift ;; --no-openbor) DO_OPENBOR=0; shift ;;
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

if [[ $ASSUME_YES -eq 0 && $SIZES_ONLY -eq 0 && $LIST_ONLY -eq 0 ]]; then
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
# Reap THIS run's aborted-archive fragments on any exit (the retention prune only removes
# completed .tar/.tar.gz, never .partial). Scoped to $TS so a concurrent run isn't touched.
trap '[ -d "$DEST" ] && rm -f "$DEST"/*"$TS"*.partial 2>/dev/null' EXIT

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
    "$storageRoot/control-panel"   # MAD runtime config: X-Arcade tester calib/positions,
                                   # router config, gp/xarcade JSON — always back up (small,
                                   # painful to recreate). Also inside storageRoot, but that
                                   # only rides the optional emu toggle; this guarantees it.
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
# OpenBOR is invisible to every other rule here, so it needs TWO explicit adds.
# --roms tars $ROM_ROOT, but ~/ROMs/openbor is a SYMLINK to ~/OpenBor and tar does
# not follow it, so that archive holds ONE symlink entry and zero bytes of OpenBOR
# (see the ROMs section). Nothing under $storageRoot or $SAVES_DIR covers it either.
# Games themselves stay OUT: they are re-downloadable. These two are not.
#
# 1. Saves/ — a game's CONTROLS, high scores and save progress live together in one
#    per-game Saves/ dir (<pak>.cfg / .hi / .s00 / .sav) inside the game folder.
#    That made the file MAD seeds and the engine rewrites on every quit the least
#    protected data on the rig. ~18 MB for all 33, so it rides the always-on core
#    list rather than a toggle.
for _ob_saves in "$HOME"/OpenBor/*/Saves; do
    [[ -d $_ob_saves ]] && CORE_ITEMS+=( "$_ob_saves" )
done
unset _ob_saves
# 2. The .openbor MANIFESTS — the other half of the same hole, and still backed up
#    by NOTHING until 2026-07-17 (c02c833 closed Saves/ and called it done).
#    ES-DE launches a game by reading DIR/EXE/PREFIX out of ~/OpenBor/<Game>.openbor.
#    They are NOT re-derivable by re-running openbor-gen-manifests.py:
#      a. it writes one manifest per game FOLDER, so a regen RESURRECTS MIWv100.old
#         and Maximun_Carnage_Returns — the 2 of 35 deliberately kept out of ES-DE.
#         That curation exists ONLY as the ABSENCE of a manifest; nothing else
#         records it, so a rebuild-from-scratch silently puts 2 broken entries in
#         the tile.
#      b. PREFIX is per-game and hand-tuned (6 point at that game's own Steam
#         compatdata prefix, 27 at the shared one). A regen re-derives it from
#         shortcuts.vdf and would quietly undo a hand edit.
#    132 KB for all 33 — the cheapest thing on this list, and the one that decides
#    whether the OpenBOR tile has any games in it at all.
for _ob_manifest in "$HOME"/OpenBor/*.openbor; do
    [[ -f $_ob_manifest ]] && CORE_ITEMS+=( "$_ob_manifest" )
done
unset _ob_manifest
ESDE_ITEMS=( "$HOME/ES-DE" )
EMU_ITEMS=(
    "$HOME/.var/app/org.libretro.RetroArch/config/retroarch"
    "$HOME/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
    "$storageRoot"
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
    printf 'roms\t%s\n'    "$(_b "$ROM_ROOT")"
    printf 'romsint\t%s\n' "$(_b "$ROM_INT")"
    printf 'openbor\t%s\n' "$(_b "$OPENBOR_ROOT")"
    printf 'media\t%s\n'   "$(_b "$MEDIA_ROOT")"
    exit 0
fi

# --list-library-items: emit "<key>\t<abspath>" for each big-library (Tier-B) category
# that EXISTS, then exit. This is the cloud tool's single source of truth for the "big
# library" set (the config-archive --list-items deliberately omits these — see its note).
# Mirrors the --sizes category list; prints only existing paths so a caller can sync
# blindly. Additive: does not affect any archive path.
if [[ ${LIST_LIB:-0} -eq 1 ]]; then
    _pl(){ [ -e "$2" ] && printf '%s\t%s\n' "$1" "$2"; return 0; }
    _pl roms         "$ROM_ROOT"
    _pl media        "$MEDIA_ROOT"
    _pl cores        "$CORES_DIR"
    _pl bezels       "$BEZEL_DIR"
    _pl rpcs3games   "$RPCS3_GAMES"
    _pl pcsx2tex     "$PCSX2_TEX"
    _pl ryujinxgames "$RYUJINX_GAMES"
    _pl romsint      "$ROM_INT"
    _pl openbor      "$OPENBOR_ROOT"
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

# --list-items: answer "what WOULD you archive?" without archiving. No tar, and it
# stops before the free-space guard's du of huge trees. NOT side-effect-free, on
# purpose: the cheap idempotent refreshes above (udev mirror, cores/bezel
# manifests, mkdir of $DEST) still run, so the list you get is the list a real run
# would archive rather than a guess about one.
# This is also the only seam that makes the item list testable at all — c02c833
# believed it had closed the OpenBOR hole and had covered only half of it (Saves/,
# not the manifests), and nothing could assert what the list actually contains.
# See tests/test_backup_items.py; NEVER run this script from a test without it.
if [[ $LIST_ONLY -eq 1 ]]; then
    printf '%s\n' "${REAL_ITEMS[@]}"
    exit 0
fi

# Re-acquirable game data lives under storage but is backed up via its OWN opt-in
# archives, so ALWAYS exclude it from the config archive.
EXCLUDES=( --exclude='*.cache' --exclude='core_logs' --exclude='shader_cache'
           --exclude='.git' --exclude='art'   # launchers = mmadalone/mad: .git history + committed art/ icons are on GitHub
           # debris / regenerable / redundant cruft (config .bak files, bytecode, temp, OS metadata,
           # extracted AppImage dirs). The current config still archives; .router-backup is not matched.
           --exclude='__pycache__' --exclude='*.pyc' --exclude='*.bak*'
           --exclude='*.orig' --exclude='*~' --exclude='*.swp' --exclude='*.tmp' --exclude='*.partial'
           --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='AppDir' --exclude='squashfs-root'
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
[[ $DO_ROMSINT  -eq 1 && -d $ROM_INT       ]] && need_kb=$(( need_kb + $(du -sk "$ROM_INT" 2>/dev/null | cut -f1) ))
[[ $DO_OPENBOR  -eq 1 && -d $OPENBOR_ROOT  ]] && need_kb=$(( need_kb + $(du -sk "$OPENBOR_ROOT" 2>/dev/null | cut -f1) ))
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
        # ⚠ KNOWN HOLE (2026-07-17, unfixed on purpose): tar does NOT follow
        # symlinks, and a ROM system that is itself a symlink is archived as ONE
        # symlink entry with zero bytes of content. Today that silently omits the
        # entire OpenBOR library (~8.7 GB): ROM_ROOT resolves ~/ROMs -> the SD
        # card, but ROMs/openbor is a second symlink -> /home/deck/OpenBor.
        #   $ tar -C /run/media/deck/1tbDeck -cf - ROMs/openbor | tar -tvf -
        #   lrwxrwxrwx ROMs/openbor -> /home/deck/OpenBor/     (1 entry, 0 bytes)
        # Adding -h/--dereference would fix it but changes this archive for EVERY
        # system (it would also chase any other symlink, and can duplicate data),
        # so it needs a deliberate decision + a restore test, not a drive-by flag.
        # The OpenBOR data that is NOT re-downloadable — controls, saves, high
        # scores — is covered independently via CORE_ITEMS above; and the full
        # OpenBOR game library is now covered by the --openbor archive below (opt-in).
        set +e
        tar --warning=no-file-changed -C "$(dirname "$ROM_ROOT")" -cf "$TMP" "$(basename "$ROM_ROOT")" \
            2> >(grep -v 'file changed as we read it' >&2)
        set -e
        tar -tf "$TMP" >/dev/null 2>&1 || die "ROMs archive verify failed"
        mv "$TMP" "$OUT"; made+=( "$OUT" ); log "  ok: $(du -h "$OUT" | cut -f1)"
    else warn "ROMs requested but $ROM_ROOT not found (SD unmounted?) — skipped"; fi
fi

# ---- 2b) internal ROMs (~/Emulation/roms) + OpenBOR games (store; real dirs) ----
# The 4 big disc systems (ps2/ps3/switch/gba) physically live under ~/Emulation/roms and the OpenBOR
# games under ~/OpenBor; ~/ROMs only symlinks to them, and the roms archive above stored those
# symlinks as symlinks (0 bytes). These two archives carry the actual data. Opt-in (large).
[[ $DO_ROMSINT -eq 1 ]] && store_archive "internal ROMs" "$ROM_INT" "roms-internal"
[[ $DO_OPENBOR -eq 1 ]] && store_archive "OpenBOR games" "$OPENBOR_ROOT" "openbor"

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
# auto-prune roms/media (huge, manual). Rule #5: never rm user data -- the excess
# archives are MOVED to a same-filesystem _TMP dir under $DEST (instant, always
# recoverable) with a RECOVERY.txt, never deleted. Null-delimited throughout so a
# $DEST containing spaces works (the old `ls | xargs rm` both deleted the user's
# archives AND silently broke on any space in the path, so nothing ever pruned).
KEEP="${BACKUP_RETENTION_COUNT:-5}"
prune_dir="$DEST/_TMP-pruned-$TS"
_kept=0
_pruned=0
while IFS= read -r -d '' _rec; do
    _kept=$((_kept + 1))
    if (( _kept <= KEEP )); then
        continue                             # keep the newest BACKUP_RETENTION_COUNT
    fi
    _arc="${_rec#*$'\t'}"                     # strip the leading "<mtime>\t"
    if (( _pruned == 0 )); then
        mkdir -p "$prune_dir"
        cat > "$prune_dir/RECOVERY.txt" <<RECO
MAD deck-backup.sh rotated these OLDER config backup archives out of
  $DEST
keeping the newest $KEEP (BACKUP_RETENTION_COUNT). They were MOVED here, NOT
deleted. To keep one, move it back to $DEST. To reclaim the space, delete this
whole folder once you are sure you no longer need these older backups.
Rotated on $(date '+%Y-%m-%d %H:%M:%S').
RECO
    fi
    if mv -- "$_arc" "$prune_dir"/; then
        _pruned=$((_pruned + 1))
        log "  rotated out (recoverable): $(basename "$_arc")"
    else
        log "  WARN: could not rotate out $_arc"
    fi
done < <(find "$DEST" -maxdepth 1 -type f -name 'deck-config-*.tar.gz' \
             -printf '%T@\t%p\0' 2>/dev/null | sort -zrn)
if (( _pruned > 0 )); then
    log "Rotated $_pruned old config backup(s) to $prune_dir (recoverable; not deleted)."
fi
exit 0
