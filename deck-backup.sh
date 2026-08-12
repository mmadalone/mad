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
#   deck-config-<ts>.tar.zst  core + ES-DE + emulator settings   (zstd, default)
#   deck-saves-<ts>.tar.zst   emulator saves, fully DEREFERENCED (zstd, default)
#   deck-roms-<ts>.tar        ROMs                               (store, relative paths)
#   deck-media-<ts>.tar       downloaded_media                   (store, relative paths)
# The config/saves archive FORMAT is selectable (--format): zstd (.tar.zst, default; --format gzip
#   is accepted as a legacy alias, no .tar.gz is ever written), store (.tar), or mirror (a browsable
#   folder tree deck-config-<ts>/ or deck-saves-<ts>/ you can open in a file manager). Mirror applies
#   ONLY to the config + saves archives; ROMs/media/etc. stay .tar (large re-acquirable blobs).
# A tar exit code >=2 (truncated read) keeps that archive as a loud <name>.partial instead of
#   promoting it; the run then exits 1 and the final summary lists which archive(s) failed.
# KNOWN ACCEPTED INACCURACY: --sizes and the free-space guard measure the saves hub
#   UN-dereferenced (~204MB vs ~638MB real on this rig, 2026-08-12) — the deck-saves archive is
#   ~3x the reported figure. Accepted: the destination normally has orders of magnitude more room.
#
# Defaults (press Enter): ES-DE + emulator settings = YES, ROMs + media = NO.
#
# Flags (skip the prompts entirely; good for cron/scheduled runs):
#   --yes                 non-interactive, use defaults (ES-DE+emu, no ROMs/media)
#   --list-items          print every path that WOULD be archived, then exit (no tar)
#   --list-library-items  print "<key>\t<path>" per big-library category, then exit
#   --print-source-roots  print the realpath'd big-library source trees, then exit (side-effect free)
#   --dest PATH           output directory (default ~/deck-config-backups)
#   --esde / --no-esde    include / skip ES-DE settings
#   --emu  / --no-emu     include / skip standalone emulator settings
#   --roms / --no-roms    include / skip ROMs
#   --media / --no-media  include / skip downloaded media
#   --format FMT          config/saves archive format: zstd (.tar.zst, default) | store (.tar) |
#                         mirror (a browsable folder tree). --compress = zstd, --no-compress = store.
#                         --format gzip is accepted as a legacy alias for zstd (see the note below).
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
# REBUILD PREREQ (not in any archive): the ES-DE fork SOURCE + its build scripts. The build
#   wrappers now live IN the fork repo (tools/deck-ubuntu-build.sh, tools/deck-rebuild.sh), so a
#   re-clone brings them back too - they are no longer backed up separately. Re-clone with:
#     git clone git@github.com:mmadalone/mad.git ~/esde-build/ES-DE \
#       && git -C ~/esde-build/ES-DE checkout deck-patches
#   then build:  ~/esde-build/ES-DE/tools/deck-ubuntu-build.sh   (in the esde-ubuntu distrobox).
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
FORMAT=zstd  # config/saves archive format: zstd (.tar.zst) | store (.tar) | mirror (browsable folder tree)
ASSUME_YES=0; SIZES_ONLY=0; LIST_ONLY=0; LIST_LIB=0; PRINT_ROOTS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --sizes)      SIZES_ONLY=1; shift ;;   # print "<key>\t<bytes>" per category, then exit
        --list-items) LIST_ONLY=1; ASSUME_YES=1; shift ;;  # print every path that WOULD be archived, then exit
        --list-library-items) LIST_LIB=1; ASSUME_YES=1; shift ;;  # print "<key>\t<path>" per big-library category, then exit
        --print-source-roots) PRINT_ROOTS=1; shift ;;  # print the big-library source trees (realpath'd), then exit
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
        --compress)   FORMAT=zstd;  shift ;;      --no-compress) FORMAT=store; shift ;;
        --format)     FORMAT="${2:?--format needs zstd|store|mirror}"; shift 2 ;;
        --cores)      INCLUDE_CORES=1;  shift ;;  --no-cores)   INCLUDE_CORES=0;  shift ;;
        --bezels)     INCLUDE_BEZELS=1; shift ;;  --no-bezels)  INCLUDE_BEZELS=0; shift ;;
        --help|-h)    sed -n '2,60p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# --format gzip is a legacy alias, normalized here ONCE: a panel with a stored "gzip" choice (or
# the old --compress flag) still gets a real zstd archive. pigz never existed on SteamOS (gzip was
# single-thread only); zstd -T0 ships in the base image and benchmarked 21.7x faster at identical
# size (2026-08-12, this Deck). Nothing below this line ever writes .tar.gz again.
[[ $FORMAT == gzip ]] && FORMAT=zstd

# --print-source-roots: emit the realpath'd big-library source trees this script archives, one per
# line, then exit. Deliberately side-effect free (no udev refresh / du / tar) so the MAD backend can
# call it cheaply to refuse a backup DEST that sits INSIDE a tree being backed up - otherwise each
# successive full backup would swallow the prior archives sitting there and balloon run over run.
if [[ ${PRINT_ROOTS:-0} -eq 1 ]]; then
    for _r in "$ROM_ROOT" "$ROM_INT" "$OPENBOR_ROOT" "$MEDIA_ROOT" "$RPCS3_GAMES" "$PCSX2_TEX" \
              "$RYUJINX_GAMES"; do
        [ -n "$_r" ] && printf '%s\n' "$(readlink -f "$_r" 2>/dev/null || echo "$_r")"
    done
    exit 0
fi

log()  { echo "[backup] $*"; }
warn() { echo "[backup] WARN: $*" >&2; }
die()  { echo "[backup] FATAL: $*" >&2; exit 1; }
hsize(){ du -sh "$1" 2>/dev/null | cut -f1; }   # human size of a path (may be slow on huge trees)
case "$FORMAT" in zstd|store|mirror) ;; *) die "invalid --format '$FORMAT' (use: zstd | store | mirror)" ;; esac

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
# Reap ONLY the fragment actually in flight when the script exits/aborts (panel SIGTERM, die(), a
# crash) — never a whole $TS-scoped glob. The old glob-based trap could not tell "still being
# written" from "deliberately kept as loud evidence": the rc>=2 policy below WANTS a failed
# .partial to survive, so a glob sweep would have deleted the exact evidence that policy exists to
# keep. A promoted archive is never at risk either way — by the time it's promoted (mv "$TMP"
# "$OUT"), $_CUR_TMP has already been cleared, so the trap has nothing left to remove. Quoted
# expansion: $_CUR_TMP (built from $DEST) may contain spaces, an explicitly supported case.
_CUR_TMP=""
trap 'if [ -n "${_CUR_TMP:-}" ]; then rm -rf -- "$_CUR_TMP"; fi' EXIT

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
    # NOTE: ~/.claude is deliberately NOT backed up (user directive) - it holds
    # Claude Code's tokens (~/.claude/tokens: GitHub PAT + ScreenScraper creds) and
    # session transcripts. The old MAD-memory entry was removed for the same reason.
    "$HOME/Applications/ES-DE.AppImage"
    "$HOME/Applications/ES-DE-MAD.AppImage"
    # (~/esde-build/{ubuntu-build,rebuild}.sh are no longer here: the real scripts moved into the
    #  fork repo at tools/deck-{ubuntu-build,rebuild}.sh, recoverable via the re-clone above.)
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
    # Cemu is a NATIVE install, so it is not under .var/app with the flatpaks and had been missed:
    # settings.xml (every graphic-pack choice), controllerProfiles/ and gameProfiles/ were backed up
    # by nothing. Its only protection was cfgutil's one-time settings.xml.bak, which the *.bak*
    # exclude below drops from the archive anyway.
    "$HOME/.config/Cemu"
    "$storageRoot"
    "$HOME/Emulation/tools/ikemen-go"
    "$HOME/Emulation/tools/ikemen-go-v0.99.0"
    "$HOME/Emulation/tools/Skraper-1.1.1"
    "$HOME/Emulation/tools/emu-launch.sh"
    "$HOME/Emulation/tools/proton-launch.sh"
    # Eden + Citron (Switch) precious slices — mirrors the same per-emulator groups
    # lib/emu_map.py:155-188 ships (config / keys / saves / mods), added here so they ride the
    # ALWAYS-present config archive too. Before this, both emulators' saves + prod/title keys
    # existed in NO backup anywhere (audit 2026-08-12). Deliberately NOT included: nand/user/
    # Contents (~24G installed titles), sdmc/ (~9G), shader/, shader.old/, dump/ — same slicing
    # emu_map itself uses; those are re-acquirable/regenerable, unlike keys/saves/mods.
    "$HOME/.config/eden"
    "$HOME/.config/citron"
    "$HOME/.local/share/eden/keys"
    "$HOME/.local/share/eden/nand/user/save"
    "$HOME/.local/share/eden/load"          # mods — user-instructed include ("keys, user saves, mods")
    "$HOME/.local/share/citron/keys"
    "$HOME/.local/share/citron/nand/user/save"
    "$HOME/.local/share/citron/load"
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

# Config archive = REAL_ITEMS minus the saves hub: saves get their OWN dereferencing archive (see
# section 1 below) so a config-only restore never puts the hub's dead-symlink entries back, and a
# config archive built from a saves-only selection can't tar zero members (rc 2 = FATAL). This
# split does NOT touch REAL_ITEMS itself — --list-items / --sizes / need_kb below all keep using
# REAL_ITEMS on purpose, because $SAVES_DIR must keep appearing there: it's the cloud tool's single
# source of truth (--list-items) for what push-precious uploads, and cloud is unaffected by how the
# LOCAL archive is split.
ARCHIVE_ITEMS=()
for p in "${REAL_ITEMS[@]}"; do
    [[ $p == "$SAVES_DIR" ]] || ARCHIVE_ITEMS+=( "$p" )
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
           --exclude='.git'
           # debris / regenerable / redundant cruft (config .bak files, bytecode, temp, OS metadata,
           # extracted AppImage dirs). The current config still archives; .router-backup is not matched.
           --exclude='__pycache__' --exclude='*.pyc' --exclude='*.bak*'
           --exclude='*.orig' --exclude='*~' --exclude='*.swp' --exclude='*.tmp' --exclude='*.partial'
           --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='thumbs.db' --exclude='ehthumbs.db'
           --exclude='._*' --exclude='Desktop.ini' --exclude='desktop.ini'   # AppleDouble + Windows shell junk
           --exclude='.gitattributes' --exclude='.gitignore'                 # VCS metadata (data isn't a repo)
           --exclude='AppDir' --exclude='squashfs-root'
           --exclude='mono_crash.*' --exclude='core.[0-9]*'   # Mono/Sinden + generic crash dumps
           --exclude='cemu-res'   # transient handheld pack markers under $storageRoot: each one says
           #                        "settings.xml currently holds handheld values, put these back".
           #                        Restoring a stale one would make the next sweep revert a game to
           #                        a docked state that no longer exists.
           --exclude='scrapers'   # ES-DE scraper cache (regenerable)
           # The next three USED TO BE bare component-name patterns (--exclude='art'/'resources'/
           # 'cheats'), which tar matches against ANY path component, not just the one intended —
           # so they silently over-matched: melonDS/mgba each ship their own per-game 'cheats' dir,
           # themes carry their own 'art'/'resources' subdirs, and (now that item 2 below adds
           # Switch mod folders) a mod's own 'cheats' dir under eden/citron load/<title>/<mod>/cheats/
           # would have been stripped too. Anchored to the ONE absolute path each names, the same
           # mechanism as the $RPCS3_GAMES exclude two lines down.
           --exclude="$HOME/Emulation/tools/launchers/art"   # mmadalone/mad: .git history + committed art/ icons are on GitHub
           --exclude="$HOME/ES-DE/resources"                 # ES-DE bundled graphics (regenerable; harmless if absent)
           # The stock libretro cheat DB (24.8k files / 229MB, re-fetch via RA Online Updater) lives
           # under $storageRoot on this EmuDeck-relocated rig — the flatpak config copy is EMPTY here.
           # Both locations excluded so unrelocated rigs stay covered too (review 2026-08-12).
           --exclude="$storageRoot/retroarch/cheats"
           --exclude="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/cheats"
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

COMPRESSOR="zstd -T0"   # -T0 = use all cores; pigz never existed on SteamOS (verified 2026-08-12)
made=()
ARCHIVE_FAILED=0   # set by any branch below whose tar hit rc>=2 (truncated read); flips the
                   # final exit code so a caller (the panel's backup.run_full) sees a real failure
                   # instead of a silently-promoted incomplete archive.
FAILED_ARCHIVES=() # the kept .partial path per failed branch — the summary lists them by name
                   # so a long run's scrolled-away warns aren't the only record of WHAT failed.

# store-archive helper (no compression — large binary game data): tar a $HOME-relative
# path into deck-<name>-<TS>.tar, verify, record it. Skips silently if the source is absent.
store_archive(){  # $1=label  $2=abs source dir  $3=archive basename
    # ALWAYS a .tar, even in --format mirror: ROMs/media/etc. are large, re-acquirable binary blobs you
    # don't browse. Only the config/saves archive is mirrored to a browsable folder (see section 1).
    [[ -d "$2" ]] || { warn "$1 requested but $2 not found — skipped"; return; }
    local rel="${2#"$HOME"/}" OUT TMP _trc
    OUT="$DEST/deck-$3-$TS.tar"; TMP="$OUT.partial"
    _CUR_TMP="$TMP"
    log "=== $1 archive (store) -> $OUT  [large] ==="
    set +e
    tar --warning=no-file-changed -C "$HOME" -cf "$TMP" "$rel" \
        2> >(grep -v 'file changed as we read it' >&2)
    _trc=$?
    set -e
    # rc>=2 = a truncated read (fatal) — NOT the benign rc=1 "file changed as we read it" already
    # filtered above. Report failure ONLY via the ARCHIVE_FAILED global + a plain `return 0`: every
    # call site is a `[[ $DO_X -eq 1 ]] && store_archive …` tail under `set -e`, so a nonzero
    # RETURN from this function would abort the whole script instead of letting the remaining
    # archives run.
    if (( _trc >= 2 )); then
        warn "!!! $1 archive INCOMPLETE (tar rc=$_trc) — kept as $TMP; NOT a valid backup"
        ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
        return 0
    fi
    tar -tf "$TMP" >/dev/null 2>&1 || die "$1 archive verify failed"
    mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -h "$OUT" | cut -f1)"
}

# Heal any outstanding Wii U handheld pack override BEFORE anything is archived. While a marker is
# outstanding (a handheld session that died without its game-end hook) Cemu's settings.xml holds the
# HANDHELD pack state, and the marker holding the docked values is deliberately excluded below. The
# two are only safe as a pair, so archiving one without the other would capture a half transaction:
# restoring it would install the handheld state as the permanent docked one. Cemu is closed during a
# backup, which is exactly this sweep's precondition. Nothing here may block the backup: the rail
# swallows its own errors and this is a plain no-op when no marker exists.
python3 -c "import sys; sys.path.insert(0,'$toolsRoot/launchers'); from lib import cemu_res; cemu_res.sweep_all()" 2>/dev/null || true

# ---- 1) config archive: zstd (.tar.zst, default) | store (.tar) | mirror (browsable folder) ----
if [[ ${#ARCHIVE_ITEMS[@]} -gt 0 ]]; then
    log "  ES-DE=$([[ $DO_ESDE == 1 ]] && echo yes || echo no)  emu=$([[ $DO_EMU == 1 ]] && echo yes || echo no)  cores=$([[ $INCLUDE_CORES == 1 ]] && echo yes || echo no)  bezels=$([[ $INCLUDE_BEZELS == 1 ]] && echo yes || echo no)"
    if [[ $FORMAT == mirror ]]; then
        # browsable folder tree: stream the SAME tar (same EXCLUDES) into a folder, so its layout +
        # contents are byte-identical to the .tar would be, just unpacked (deck-config-<ts>/home/deck/…).
        OUT="$DEST/deck-config-$TS"; TMP="$OUT.partial"
        _CUR_TMP="$TMP"
        log "=== config mirror (browsable folder) -> $OUT/ ==="
        mkdir -p "$TMP"
        set +e
        tar --warning=no-file-changed "${EXCLUDES[@]}" -cf - "${ARCHIVE_ITEMS[@]}" \
                2> >(grep -v 'file changed as we read it' >&2) \
            | tar -xpf - -C "$TMP" 2> >(grep -v 'Cannot change ownership' >&2)
        _pst=( "${PIPESTATUS[@]}" )   # [0]=producer create tar, [1]=consumer extract tar
        set -e
        # Producer 0 = ok, 1 = benign warnings only (file-changed, filtered above), >=2 = FATAL read
        # error => a truncated mirror. Fail on a fatal producer OR any extract error OR empty output,
        # so a half-written folder is never promoted to a "good" backup. Deliberately still a hard
        # die() here (unchanged policy), NOT the rc>=2-keep-and-continue rule the other branches now
        # use below — harmonizing it is scope creep for this batch.
        [[ ${_pst[0]:-2} -le 1 && ${_pst[1]:-1} -eq 0 && -n "$(ls -A "$TMP" 2>/dev/null)" ]] \
            || die "config mirror failed (producer=${_pst[0]:-?} extract=${_pst[1]:-?})"
        # Record the archived item roots (leading '/' stripped, as tar stores them) so restore takes
        # the SAME targeted pre-restore snapshot the .tar path does. A folder listing alone can't tell
        # an archived item from an intermediate parent dir, so without this the fold collapses to
        # 'home' and restore would snapshot ALL of /home. Excluded from the restore-to-/ extract.
        printf '%s\n' "${ARCHIVE_ITEMS[@]#/}" > "$TMP/.mad-manifest.txt"
        mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -sh "$OUT" | cut -f1)"
    else
        if [[ $FORMAT == zstd ]]; then
            OUT="$DEST/deck-config-$TS.tar.zst"; _comp=( --use-compress-program="$COMPRESSOR" ); _vflag=-tf
        else
            OUT="$DEST/deck-config-$TS.tar";     _comp=();                                       _vflag=-tf
        fi
        # -tf (not -tzf) for BOTH arms: a seekable .tar.zst auto-detects under GNU tar 1.35 just
        # like a seekable .tar.gz used to with -tzf (verified on this device), so one verify flag
        # now covers every non-mirror format.
        TMP="$OUT.partial"
        _CUR_TMP="$TMP"
        log "=== config archive ($FORMAT) -> $OUT ==="
        set +e
        tar --warning=no-file-changed "${_comp[@]}" "${EXCLUDES[@]}" \
            -cf "$TMP" "${ARCHIVE_ITEMS[@]}" 2> >(grep -v 'file changed as we read it' >&2)
        _trc=$?
        set -e
        if (( _trc >= 2 )); then
            warn "!!! config archive INCOMPLETE (tar rc=$_trc) — kept as $TMP; NOT a valid backup"
            ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
        else
            tar "$_vflag" "$TMP" >/dev/null 2>&1 || die "config archive verify failed"
            mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -h "$OUT" | cut -f1)"
        fi
    fi
fi

# ---- 1b) saves archive: the EmuDeck saves HUB, fully DEREFERENCED. Its own archive (not part of
# the config archive above) because the hub is 21 symlinks pointing at per-emulator data scattered
# under $storageRoot/~/.config — tarred as-is (no -h) those are 21 dead pointers and 0 bytes of the
# actual saves (audit 2026-08-12: exactly what the old single config archive did). `tar -h` walks
# through each link and stores the real target bytes instead. ----
if [[ $DO_SAVES -eq 1 ]]; then
    if [[ -d $SAVES_DIR ]]; then
        # Dangling-link pre-flight: some hub links may point at nothing on THIS rig right now (e.g.
        # xenia's target is empty on this Deck today). `tar -h` cannot dereference a dead link, and
        # without this pre-flight one broken pointer would turn the WHOLE saves archive red on
        # every future run. TWO passes, merged + deduped: `find -L -type l` lists the DANGLING
        # links (including ones nested inside good link targets it descends into), but reports a
        # LOOPING link (ELOOP, e.g. a self-link) only on its discarded stderr — the physical
        # `! test -e` pass catches those. Each hit gets a loud warn + a targeted --exclude.
        _dangling_excludes=()
        while IFS= read -r -d '' _dead; do
            warn "dangling save link, excluded from this archive: $_dead"
            # tar --exclude is an fnmatch GLOB, not a literal path: escape metacharacters, or a
            # dead link named 'Game [USA]' never matches itself (archive stays red) and one named
            # '*' would silently strip unrelated REAL saves from the archive.
            _esc=$(printf '%s' "$_dead" | sed 's/[][*?\\]/\\&/g')
            _dangling_excludes+=( "--exclude=$_esc" )
        done < <({ find -L "$SAVES_DIR" -type l -print0 2>/dev/null || true; \
                   find "$SAVES_DIR" -type l ! -exec test -e {} \; -print0 2>/dev/null || true; } | sort -zu)
        #        ^ the `|| true` pair is LOAD-BEARING: set -e propagates into <( ) subshells, and
        #          `find -L` exits 1 exactly when an ELOOP link exists — without it, pass 1's rc
        #          would abort the group and skip pass 2 in the one case pass 2 exists for.
        # No other EXCLUDES ride on this archive, on purpose: the global EXCLUDES list (cheats/art/
        # resources/etc., even now that they're anchored) is tuned for the CONFIG tree and must
        # never be able to drop a save dir by accident. Saves are small enough to carry cruft.
        if [[ $FORMAT == mirror ]]; then
            OUT="$DEST/deck-saves-$TS"; TMP="$OUT.partial"
            _CUR_TMP="$TMP"
            log "=== saves archive (dereferenced) -> $OUT/ ==="
            mkdir -p "$TMP"
            set +e
            tar -h --warning=no-file-changed "${_dangling_excludes[@]}" -cf - "$SAVES_DIR" \
                    2> >(grep -v 'file changed as we read it' >&2) \
                | tar -xpf - -C "$TMP" 2> >(grep -v 'Cannot change ownership' >&2)
            _pst=( "${PIPESTATUS[@]}" )
            set -e
            if [[ ${_pst[0]:-2} -le 1 && ${_pst[1]:-1} -eq 0 && -n "$(ls -A "$TMP" 2>/dev/null)" ]]; then
                printf '%s\n' "${SAVES_DIR#/}" > "$TMP/.mad-manifest.txt"
                mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -sh "$OUT" | cut -f1)"
            else
                warn "!!! saves archive INCOMPLETE (producer=${_pst[0]:-?} extract=${_pst[1]:-?}) — kept as $TMP; NOT a valid backup"
                ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
            fi
        else
            OUT="$DEST/deck-saves-$TS.tar.zst"; TMP="$OUT.partial"
            _CUR_TMP="$TMP"
            log "=== saves archive (dereferenced, zstd) -> $OUT ==="
            # ALWAYS zstd here, even under --format store: unlike ROMs, saves are small live-game
            # data, not pre-compressed binary blobs, so there is no reason to skip compression the
            # way the store format deliberately does for those.
            set +e
            tar -h --warning=no-file-changed "${_dangling_excludes[@]}" --use-compress-program="$COMPRESSOR" \
                -cf "$TMP" "$SAVES_DIR" 2> >(grep -v 'file changed as we read it' >&2)
            _trc=$?
            set -e
            if (( _trc >= 2 )); then
                warn "!!! saves archive INCOMPLETE (tar rc=$_trc) — kept as $TMP; NOT a valid backup"
                ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
            else
                tar -tf "$TMP" >/dev/null 2>&1 || die "saves archive verify failed"
                mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -h "$OUT" | cut -f1)"
            fi
        fi
    else
        warn "saves requested but $SAVES_DIR not found — skipped"
    fi
fi

# ---- 2) ROMs archive (store, relative paths so restore can target any drive) ----
if [[ $DO_ROMS -eq 1 ]]; then
    if [[ -d $ROM_ROOT ]]; then
        OUT="$DEST/deck-roms-$TS.tar"; TMP="$OUT.partial"
        _CUR_TMP="$TMP"
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
        _trc=$?
        set -e
        if (( _trc >= 2 )); then
            warn "!!! ROMs archive INCOMPLETE (tar rc=$_trc) — kept as $TMP; NOT a valid backup"
            ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
        else
            tar -tf "$TMP" >/dev/null 2>&1 || die "ROMs archive verify failed"
            mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -h "$OUT" | cut -f1)"
        fi
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
        _CUR_TMP="$TMP"
        log "=== media archive (store) -> $OUT  [this is large] ==="
        set +e
        tar --warning=no-file-changed -C "$(dirname "$MEDIA_ROOT")" -cf "$TMP" "$(basename "$MEDIA_ROOT")" \
            2> >(grep -v 'file changed as we read it' >&2)
        _trc=$?
        set -e
        if (( _trc >= 2 )); then
            warn "!!! media archive INCOMPLETE (tar rc=$_trc) — kept as $TMP; NOT a valid backup"
            ARCHIVE_FAILED=1; FAILED_ARCHIVES+=( "$TMP" ); _CUR_TMP=""
        else
            tar -tf "$TMP" >/dev/null 2>&1 || die "media archive verify failed"
            mv "$TMP" "$OUT"; made+=( "$OUT" ); _CUR_TMP=""; log "  ok: $(du -h "$OUT" | cut -f1)"
        fi
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
if [[ $ARCHIVE_FAILED -eq 1 ]]; then
    echo
    log "!!! ================================================================ !!!"
    log "!!! these archives are INCOMPLETE and were kept as .partial:"
    for f in "${FAILED_ARCHIVES[@]}"; do log "!!!   $f"; done
    log "!!! (tar hit a fatal read error). They are NOT valid backups — do not"
    log "!!! rely on them. Investigate (disk full? source vanished mid-read?),"
    log "!!! then either delete the .partial and re-run, or leave it as evidence."
    log "!!! ================================================================ !!!"
fi

# prune old CONFIG + SAVES archives, PER FAMILY (keep BACKUP_RETENTION_COUNT, default 5, of
# EACH) — a shared cap would let the newer saves family starve config's slots or grow
# unboundedly on its own. Never auto-prune roms/media (huge, manual). Rule #5: never rm user
# data -- the excess archives are MOVED to a same-filesystem _TMP dir under $DEST (instant,
# always recoverable) with a RECOVERY.txt, never deleted. Null-delimited throughout so a $DEST
# containing spaces works (the old `ls | xargs rm` both deleted the user's archives AND
# silently broke on any space in the path, so nothing ever pruned).
KEEP="${BACKUP_RETENTION_COUNT:-5}"
prune_dir="$DEST/_TMP-pruned-$TS"
_pruned=0   # shared across both families; the RECOVERY.txt is written once, on the first prune

prune_family() {   # $1=label ("config"/"saves")  $2..=find test expression for this family
    local label="$1" _kept=0 _rec _arc
    shift
    while IFS= read -r -d '' _rec; do
        _kept=$((_kept + 1))
        if (( _kept <= KEEP )); then
            continue                             # keep the newest BACKUP_RETENTION_COUNT
        fi
        _arc="${_rec#*$'\t'}"                     # strip the leading "<mtime>\t"
        if (( _pruned == 0 )); then
            mkdir -p "$prune_dir"
            cat > "$prune_dir/RECOVERY.txt" <<RECO
MAD deck-backup.sh rotated these OLDER backup archives out of
  $DEST
keeping the newest $KEEP (BACKUP_RETENTION_COUNT) of EACH family (config, saves). They were
MOVED here, NOT deleted. To keep one, move it back to $DEST. To reclaim the space, delete this
whole folder once you are sure you no longer need these older backups.
Rotated on $(date '+%Y-%m-%d %H:%M:%S').
RECO
        fi
        if mv -- "$_arc" "$prune_dir"/; then
            _pruned=$((_pruned + 1))
            log "  rotated out (recoverable, $label): $(basename "$_arc")"
        else
            log "  WARN: could not rotate out $_arc"
        fi
    done < <(find "$DEST" -mindepth 1 -maxdepth 1 "$@" -printf '%T@\t%p\0' 2>/dev/null | sort -zrn)
}

prune_family "config" \
    \( \( -type f \( -name 'deck-config-*.tar.gz' -o -name 'deck-config-*.tar' -o -name 'deck-config-*.tar.zst' \) \) \
       -o \( -type d -name 'deck-config-[0-9]*' ! -name '*.partial' \) \)
prune_family "saves" \
    \( \( -type f \( -name 'deck-saves-*.tar' -o -name 'deck-saves-*.tar.zst' \) \) \
       -o \( -type d -name 'deck-saves-[0-9]*' ! -name '*.partial' \) \)

if (( _pruned > 0 )); then
    log "Rotated $_pruned old backup(s) to $prune_dir (recoverable; not deleted)."
fi
[[ $ARCHIVE_FAILED -eq 1 ]] && exit 1
exit 0
