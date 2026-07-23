#!/usr/bin/env bash
# ============================================================================
# deck-cloud.sh - MEGA S4 cloud backup/restore engine (rclone only).
#
# The SINGLE owner of every rclone invocation on this Deck. Called by:
#   - the ES-DE game-end hook  (hooks/game-end/20-cloud-push.sh)  -> push-precious
#   - the systemd --user timer (cloud-sync.timer)                 -> push-precious
#   - the MAD backend RPC      (lib/madsrv/cloud_cmds.py)         -> all commands
#   - restore flows                                               -> restore-*
#
# TWO TIERS, both plain BROWSABLE rclone copies into MEGA S4 (S3 object storage) - you can
# see your files in the MEGA web UI. Difference is only cadence + a version net:
#   Tier A  precious + small + changes often (saves, ES-DE/emulator config, BIOS,
#           MAD/router config) -> copied to  s4:<bucket>/precious/ , with --backup-dir so
#           any OVERWRITTEN file is kept under s4:<bucket>/precious-versions/<ts>/ (a
#           browsable rollback net). Auto: on game-exit + a toggleable during-play timer.
#   Tier B  big + static + re-downloadable (ROMs, media, cores, bezels, installed games)
#           -> copied to s4:<bucket>/library/<cat>/. Manual: "Sync library now".
# rclone COPY is additive: it never deletes at the destination (rule #5 friendly).
#
# Transport is MEGA S4, NOT the MEGA cloud drive: S3 object storage is per-bucket with no
# global tree fetch, so it avoids the go-mega "-3 on a million-file account" failure.
#
# The "what to back up" list is read from the single source of truth deck-backup.sh:
#   Tier A  ->  deck-backup.sh --list-items --esde --emu --saves --bios --no-cores --no-bezels
#   Tier B  ->  deck-backup.sh --list-library-items   (key<TAB>path per category)
# NOTE: deck-backup.sh's log() writes to STDOUT, so its data output can be prefixed with a
# "[backup] ..." line. We filter it (Tier A: keep only existing absolute paths; Tier B:
# require a TAB + existing path).
#
# Commands:
#   push-precious [--force]       Tier A backup (hook / timer / "Back up now")
#   sync-library                  Tier B backup ("Sync library now")
#   snapshots                     list version timestamps (rollback points), newest first
#   restore-precious [ver] [dir]  copy the current backup (or a version) into a STAGING dir
#   restore-library <cat> [dir]   copy a library category into a STAGING dir
#   status                        key<TAB>value: connected / bucket / server / precious / etc.
#   list-servers                  key<TAB>label<TAB>endpoint<TAB>region<TAB>current, per S4 server
#   set-server <key> [--no-probe] switch the active MEGA S4 server (global|amsterdam|barcelona|...)
#   list-categories               tier<TAB>key<TAB>label<TAB>on: what the cloud backs up
#   set-category <key> <on|off>   choose what the cloud backs up (esde/emu/saves/bios/roms/...)
#   cloud-sizes                   key<TAB>bytes: the REAL post-filter upload size per Tier-A category
#   set-toggle <onexit|timer> <on|off>
#   prune                         delete old version folders (keep newest N)
#   ensure-units / probe / is-connected
#
# Heavy work runs under nice/ionice (LOW PRIORITY, so it yields during play) but the upload
# is UNCAPPED by default - S4 is object storage, no need to throttle. Set DECK_CLOUD_BWLIMIT to
# cap and DECK_CLOUD_TRANSFERS to tune parallelism.
# Configured ONCE by deck-cloud-setup.sh; until then commands no-op / error cleanly.
# ============================================================================
set -euo pipefail

# Steam force-preloads its 32-bit game-overlay (gameoverlayrenderer.so) via LD_PRELOAD into
# everything ES-DE spawns; a 64-bit binary (rclone) then makes ld.so print a harmless
# "wrong ELF class ... ignored" warning at startup. rclone doesn't need the overlay - drop it
# so those warnings don't spam the log/panel (they read as "errors" but are not).
unset LD_PRELOAD

HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
# The launchers git worktree (mmadalone/mad): its precious item takes the thin git-driven copy.
# Overridable so tests can point it at a throwaway repo instead of the live tree.
LAUNCHERS_DIR="${DECK_CLOUD_LAUNCHERS_DIR:-$HERE}"
BIN="$HOME/Emulation/tools/bin"
RCLONE="${DECK_CLOUD_RCLONE:-$BIN/rclone}"
DECK_BACKUP="${DECK_CLOUD_BACKUP_SCRIPT:-$HERE/deck-backup.sh}"   # override = test injection

# ---- destination: MEGA S4 (S3). Set once by deck-cloud-setup.sh; all overridable for tests ----
S4_BUCKET="${DECK_CLOUD_BUCKET:-steamdeck}"
STATE_DIR="${DECK_CLOUD_STATE_DIR:-$HOME/.config/deck-cloud}"

# ---- MEGA S4 server table: key -> endpoint|region|label. 'global' is the default; the
#      regional endpoints (s3.<seg>.megas4.com) use region = the hostname segment (NOT the
#      old eu-central-1). All 8 reach the SAME bucket (verified) - the choice only changes
#      the route. Switch with 'set-server'; the saved choice is one line in $STATE_DIR/server.
_server_row(){   # $1 = key -> "endpoint|region|label"  (empty if the key is unknown)
    case "$1" in
        global)     printf '%s' 'https://s3.g.s4.mega.io|eu-central-1|Global (auto-route)';;
        amsterdam)  printf '%s' 'https://s3.eu-amsterdam.megas4.com|eu-amsterdam|Amsterdam';;
        luxembourg) printf '%s' 'https://s3.eu-luxembourg.megas4.com|eu-luxembourg|Luxembourg';;
        paris)      printf '%s' 'https://s3.eu-paris.megas4.com|eu-paris|Paris';;
        barcelona)  printf '%s' 'https://s3.eu-barcelona.megas4.com|eu-barcelona|Barcelona';;
        montreal)   printf '%s' 'https://s3.ca-montreal.megas4.com|ca-montreal|Montreal';;
        vancouver)  printf '%s' 'https://s3.ca-vancouver.megas4.com|ca-vancouver|Vancouver';;
        tokyo)      printf '%s' 'https://s3.ap-tokyo.megas4.com|ap-tokyo|Tokyo';;
        *)          printf '';;
    esac
}
_SERVER_KEYS="global amsterdam luxembourg paris barcelona montreal vancouver tokyo"
# Current server key: DECK_CLOUD_SERVER env (tests) > $STATE_DIR/server file > 'global'.
# An unknown/empty value falls back to 'global' so a stray file can never break backups.
_current_server_key(){
    local k="${DECK_CLOUD_SERVER:-}"
    [[ -z "$k" && -f "$STATE_DIR/server" ]] && k="$(tr -d ' \t\r\n' < "$STATE_DIR/server" 2>/dev/null)"
    [[ -n "$k" && -n "$(_server_row "$k")" ]] || k="global"
    printf '%s' "$k"
}
SERVER_KEY="$(_current_server_key)"
IFS='|' read -r _SRV_EP _SRV_REGION _SRV_LABEL <<< "$(_server_row "$SERVER_KEY")"
# Endpoint/region: an explicit DECK_CLOUD_ENDPOINT/REGION env still wins (tests + back-compat),
# else they come from the chosen server. No 'server' file => global => identical to before.
S4_ENDPOINT="${DECK_CLOUD_ENDPOINT:-$_SRV_EP}"
S4_REGION="${DECK_CLOUD_REGION:-$_SRV_REGION}"
# S4 creds (aws_access_key_id / aws_secret_access_key). Resolve from known spots (first
# existing wins); override with DECK_CLOUD_CREDS.
_default_s4_creds(){
    local c a s
    for c in "$HOME/.ssh/credentials-steamdeck" "$HOME/.config/deck-cloud/credentials-steamdeck" \
             "$HOME/.claude/tokens/credentials-steamdeck" "$HOME/Emulation/tools/tokens/mega-steamdeck"; do
        [[ -f "$c" ]] || continue
        # pick the first file that actually PARSES (both keys non-empty), not just exists, so a
        # stale/empty higher-priority file doesn't shadow a good one.
        a="$(grep -E '^aws_access_key_id' "$c" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')" || true
        s="$(grep -E '^aws_secret_access_key' "$c" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')" || true
        [[ -n "$a" && -n "$s" ]] && { printf '%s' "$c"; return; }
    done
    printf '%s' "$HOME/.ssh/credentials-steamdeck"
}
S4_CREDS="${DECK_CLOUD_CREDS:-$(_default_s4_creds)}"
RCLONE_REMOTE="${DECK_CLOUD_RCLONE_REMOTE:-s4}"
PRECIOUS_BASE="${DECK_CLOUD_PRECIOUS_BASE_OVERRIDE:-${RCLONE_REMOTE}:${S4_BUCKET}/precious}"
PRECIOUS_VERS="${DECK_CLOUD_PRECIOUS_VERS_OVERRIDE:-${RCLONE_REMOTE}:${S4_BUCKET}/precious-versions}"
LIB_BASE="${DECK_CLOUD_LIB_BASE_OVERRIDE:-${RCLONE_REMOTE}:${S4_BUCKET}/library}"
LIB_MANIFEST="library-symlinks.tsv"   # stored at ${LIB_BASE}/ ; maps symlink front-doors -> targets
RCLONE_CONF="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
LOCKFILE="$STATE_DIR/push.lock"
LOG="$STATE_DIR/cloud.log"
TRANSFERS="${DECK_CLOUD_TRANSFERS:-16}"       # background parallel transfers (timer/hook)
TRANSFERS_FG="${DECK_CLOUD_TRANSFERS_FG:-32}" # manual ops (user is watching): more parallelism.
                                              # MEGA S4 PUT latency is ~200ms, so on many small
                                              # files throughput is transfer-COUNT bound, not
                                              # bandwidth: 32 was ~2.8x faster than 8 on a live
                                              # 300-tiny-file benchmark (the ceiling is ~10MB/s).
BWLIMIT="${DECK_CLOUD_BWLIMIT:-off}"      # upload cap; OFF by default (S4 handles throughput).
                                          # Set DECK_CLOUD_BWLIMIT=<rate> to cap if ever wanted.

mkdir -p "$STATE_DIR"

# Cap cloud.log: the per-second rclone JSON stats (RCLONE_PROGRESS) otherwise append forever with
# no rotation. Roll one generation at startup once past the cap (rule #5: move aside, never delete).
# Each hook/timer/RPC run is a fresh process, so this checks + rolls at the start of every run.
if [[ -f "$LOG" && "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt "${DECK_CLOUD_LOG_MAX:-5242880}" ]]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
fi

# S3 creds for rclone (env_auth) come from the token file.
_load_s4_creds(){
    [[ -f "$S4_CREDS" ]] || return 1
    local ak sk
    ak="$(grep -E '^aws_access_key_id' "$S4_CREDS" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')"
    sk="$(grep -E '^aws_secret_access_key' "$S4_CREDS" 2>/dev/null | cut -d= -f2- | tr -d ' \t\r')"
    [[ -n "$ak" && -n "$sk" ]] || return 1
    export AWS_ACCESS_KEY_ID="$ak" AWS_SECRET_ACCESS_KEY="$sk" AWS_DEFAULT_REGION="$S4_REGION"
}
[[ -f "$S4_CREDS" ]] && _load_s4_creds || true   # best-effort; is_connected re-checks

export RCLONE_CONFIG="$RCLONE_CONF"

# Point rclone's "$RCLONE_REMOTE" remote at the chosen server WITHOUT editing rclone.conf:
# rclone honours RCLONE_CONFIG_<REMOTE>_<KEY> env overrides (verified on-device). The remote
# name is upper-cased with any non-alnum -> '_' (s4 -> S4). This is why switching servers is
# just a one-line file write + these exports; an in-flight run keeps its own env (read here,
# once, at start), so a mid-run switch cannot corrupt it.
_remote_uc="$(printf '%s' "$RCLONE_REMOTE" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9' '_')"
_export_server_env(){   # $1=endpoint $2=region
    export "RCLONE_CONFIG_${_remote_uc}_ENDPOINT=$1" "RCLONE_CONFIG_${_remote_uc}_REGION=$2"
    export AWS_DEFAULT_REGION="$2"
}
_export_server_env "$S4_ENDPOINT" "$S4_REGION"

# Large / re-acquirable subtrees that must NOT ride the precious mirror (they sit under the
# config roots --list-items prints). rclone --exclude patterns, matched per-copy.
# rclone excludes match RELATIVE to each copied item's OWN root. A '**/X/**' pattern needs a
# parent segment above X, so it FAILS to match a top-level X (verified on-device: it would
# leak 39GB pcsx2 textures + 1.3GB RetroArch cores into the mirror). Use root-relative forms;
# keep both root + '**/' variants for the cache/log dirs that can appear at either level.
EXCL_RCLONE=( --exclude '*.cache'
              # Debris / regenerable / redundant cruft (never precious): Python bytecode, config
              # BACKUP files (*.bak / *.bak-<date> - old versions; the current config still uploads,
              # and .router-backup is NOT matched), editor/temp leftovers, OS metadata, and extracted
              # AppImage build dirs.
              --exclude '__pycache__/**' --exclude '**/__pycache__/**' --exclude '*.pyc'
              --exclude '*.bak*' --exclude '**/*.bak*'
              --exclude '*.orig' --exclude '**/*.orig' --exclude '*~' --exclude '**/*~'
              --exclude '*.swp' --exclude '**/*.swp' --exclude '*.tmp' --exclude '**/*.tmp'
              --exclude '*.partial' --exclude '**/*.partial'
              --exclude '.DS_Store' --exclude '**/.DS_Store' --exclude 'Thumbs.db' --exclude '**/Thumbs.db'
              --exclude 'AppDir/**' --exclude '**/AppDir/**' --exclude 'squashfs-root/**' --exclude '**/squashfs-root/**'
              # Crash dumps: the Sinden lightgun's Mono runtime drops mono_crash.mem.<pid>.<n>.blob
              # (9.6MB) + mono_crash.<hash>.<n>.json in ~/Lightgun. Pure post-mortem debris.
              --exclude 'mono_crash.*' --exclude '**/mono_crash.*'
              --exclude 'core.[0-9]*' --exclude '**/core.[0-9]*'
              # ~/Emulation/tools/launchers is a git repo (mmadalone/mad): its .git history (~189MB)
              # and its committed art/ icons (~81MB) are recoverable from GitHub, so drop them. The
              # working files - including any UNCOMMITTED changes - still ride along.
              --exclude '.git/**' --exclude '**/.git/**'
              --exclude 'art/**'  --exclude '**/art/**'
              --exclude 'core_logs/**'    --exclude '**/core_logs/**'
              --exclude 'shader_cache/**' --exclude '**/shader_cache/**'
              --exclude 'pcsx2/textures/**' --exclude 'ryujinx/games/**'
              --exclude 'rpcs3/dev_hdd0/game/**' --exclude 'cores/**'
              --exclude 'themes/**' --exclude 'downloaded_media/**' --exclude 'downloaded_themes/**'
              --exclude 'bezelproject/**' --exclude '**/bezelproject/**'
              # ES-DE regenerables: resources/ is the app's bundled graphics (2.7MB, recreated on
              # launch); scrapers/ is the ScreenScraper/TheGamesDB response cache (560KB). Both come
              # back on their own. KEPT: settings/ gamelists/ collections/ custom_systems/ splashscreens.
              --exclude 'resources/**' --exclude '**/resources/**'
              --exclude 'scrapers/**'  --exclude '**/scrapers/**'
              # Logs are never precious (566 files / 64MB across the config roots).
              --exclude '*.log' --exclude '**/*.log'
              --exclude 'logs/**' --exclude '**/logs/**'
              # RetroArch's online-updater set: re-downloadable from RA itself. Verified these
              # are the stock libretro repos (no custom lightgun overlays; the Sinden border is a
              # physical LED strip). KEPT: config/ system(BIOS)/ saves/ states/ playlists.
              # cheats/ is the stock libretro cheat DB (~229MB, 24.8k .cht, all one bulk
              # download - zero hand-edits): re-fetched in one click from RA's Online Updater.
              --exclude 'assets/**'   --exclude 'downloads/**' --exclude 'overlays/**'
              --exclude 'shaders/**'  --exclude 'thumbnails/**' --exclude 'database/**'
              --exclude 'cheats/**'   --exclude '**/cheats/**'
              # EmuDeck's own install + its Electron app caches (regenerable). Its actual
              # settings live outside these dirs and still ride along.
              --exclude 'backend/**'  --exclude 'Cache/**' --exclude 'Code Cache/**'
              --exclude 'GPUCache/**' --exclude 'DawnCache/**' --exclude 'Session Storage/**'
              --exclude 'shared_proto_db/**'
              # Wine/Proton prefix plumbing. -L (below) follows symlinks so legit symlinked
              # precious paths get backed up - but a prefix's dosdevices/ maps DOS drive letters
              # to REAL filesystems (d:->the SD card = the whole ROM library incl. 3DO ISOs,
              # z:->/ = the entire root fs), and drive_c/windows is all symlinks into the Steam
              # Proton install. Without these, -L crawls all of that into precious/. dosdevices is
              # NESTED (mugen/prefix/pfx/dosdevices), so the '**/' form is the one that matches.
              --exclude 'dosdevices/**' --exclude '**/dosdevices/**'
              --exclude '**/drive_c/windows/**' )

# --transfers / --checkers are set per-call by rclone_copy (they differ manual vs background),
# so keep them OUT of the shared flags - passing a flag twice is asking for a last-wins surprise.
RCLONE_COMMON=( --bwlimit "$BWLIMIT" --s3-chunk-size 16M --s3-upload-concurrency 4 --fast-list )
# Live-progress flags for the USER-FACING copies: rclone emits a JSON stats object every
# second (bytes/totalBytes/speed/eta + a transferring[] array of the active files), which
# rclone_copy() surfaces to the RPC stream so the panel shows live progress + per-transfer
# bars. Kept OUT of RCLONE_COMMON so probe/lsd/internal copies stay quiet.
RCLONE_PROGRESS=( --use-json-log --stats 1s --stats-log-level NOTICE )

log(){ printf '[cloud %s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG" >&2; }
die(){ printf '[cloud] FATAL: %s\n' "$*" | tee -a "$LOG" >&2; exit 1; }
lowprio(){ [[ "${DECK_CLOUD_NO_NICE:-0}" == 1 ]] && { "$@"; return; }; nice -n 19 ionice -c3 "$@"; }  # imperceptible background priority (DECK_CLOUD_NO_NICE=1 = no throttle, for tests / hosts without ionice)
# Manual ops (push --force, sync-library, restore-*) set _FG=1: the user is actively watching,
# so run at NORMAL priority with more parallelism. Background runs (the timer/hook push, no
# --force) leave _FG=0 = idle CPU + idle I/O so they stay imperceptible during play.
_FG=0
# A user-facing `rclone copy` with LIVE PROGRESS: rclone's per-second JSON stats go to stderr,
# which we tee to BOTH the log AND our own stderr - the RPC daemon captures our stderr, so the
# stats reach the panel (footer summary + the progress subpage). VERIFIED: the `if rclone_copy
# ...` exit status is still rclone's (the redirects don't change it). Internal/tiny copies (the
# symlink manifest) keep the quiet `>>"$LOG" 2>&1` form.
rclone_copy(){
    local pfx=() tf="$TRANSFERS" ck=16
    if [[ "$_FG" == 1 ]]; then tf="$TRANSFERS_FG"; ck=32            # manual: full priority + parallelism
    elif [[ "${DECK_CLOUD_NO_NICE:-0}" != 1 ]]; then pfx=( nice -n 19 ionice -c3 ); fi  # background: idle
    "${pfx[@]}" "$RCLONE" copy "$@" --transfers "$tf" --checkers "$ck" \
        "${RCLONE_PROGRESS[@]}" >>"$LOG" 2> >(tee -a "$LOG" >&2)
}
# Cloud-ONLY: whole enumerated items that are re-acquirable elsewhere and must not ride the
# precious mirror (the local on-disk backup still keeps them). Returns 0 = skip this item.
_cloud_skip_item(){
    case "$1" in
        */Applications/*.AppImage)   return 0;;  # ES-DE-MAD.AppImage = the GitHub release (re-pull via F4)
        */Emulation/tools/Skraper-*) return 0;;  # scraper tool, ~1GB / 3000+ files, re-downloadable
        *) return 1;;
    esac
}

# Debris filter for the launchers thin-backup file list (NUL-delimited in AND out). Drops ONLY
# machine-regenerable artifacts. This is NOT a filter for owner data: .gitignore documents
# review-findings/ + romhack-*.json + skyscraper-flagged.json + openbor-metadata.json as the
# owner's kept-local catalogs, so they are NEVER dropped here (bias every call toward INCLUDE -
# bloat is safe, an omission is the costly bug).
_cloud_debris_filter(){   # stdin/stdout: NUL-delimited paths
    LC_ALL=C grep -zvE '(^|/)\.git/|(^|/)__pycache__/|\.pyc$|(^|/)squashfs-root(/|$)|(^|/)AppDir/|\.log$|\.bak($|[-.])|\.orig$|\.swp$|\.tmp$|\.partial$|~$'
}

# Enumerate the launchers "local-only" upload set: the files a fresh `install.sh` git-clone would
# NOT recreate (untracked + git-ignored config + any UNPUSHED tracked edits). The tracked code is
# always recoverable from mmadalone/mad, so it is deliberately excluded. Writes the CONFIG-ONLY
# sublist (untracked + ignored, minus debris = the restore manifest / allowlist) to $2 and the FULL
# upload list (config + diverged-tracked) to $3, both newline-delimited, repo-root-relative.
# Returns 0 when git ran cleanly (an EMPTY list is a VALID result). Returns 1 when the caller MUST
# fall back to the whole-dir copy: git absent / not a worktree / ANY git subcommand error / a
# filename containing a newline (which a newline-delimited --files-from cannot express). The
# fallback path (never a silent skip) protects the config if the git enumeration is unavailable.
_launchers_localonly(){   # $1=repo dir  $2=manifest-out(config)  $3=filelist-out(full)
    local dir="$1" mout="$2" lout="$3" nul dtmp n
    command -v git >/dev/null 2>&1 || return 1
    git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1
    nul="$(mktemp)" || return 1
    # untracked + ignored (the config set) as one NUL stream. EITHER git failing = fall back:
    # every irreplaceable config rides ls-files --others, so a masked failure must not look empty.
    if ! { git -C "$dir" ls-files -z --others --exclude-standard &&
           git -C "$dir" ls-files -z --others --ignored --exclude-standard; } > "$nul" 2>/dev/null; then
        rm -f "$nul"; return 1
    fi
    # A newline inside a filename can't be expressed in a newline --files-from -> whole-dir fallback.
    if LC_ALL=C grep -qzP '\n' "$nul"; then rm -f "$nul"; return 1; fi
    _cloud_debris_filter < "$nul" | tr '\0' '\n' | sed '/^$/d' | LC_ALL=C sort -u > "$mout"
    rm -f "$nul"
    cp -- "$mout" "$lout"
    # DIVERGED tracked (edits not on the pushed remote): best-effort, NEVER forces a fallback and is
    # NOT in the manifest (config only). Needs an upstream ref; a clean/level repo adds nothing.
    if git -C "$dir" rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
        dtmp="$(mktemp)"
        if git -C "$dir" diff -z --name-only '@{upstream}' > "$dtmp" 2>/dev/null; then
            n="$(_cloud_debris_filter < "$dtmp" | tr '\0' '\n' | sed '/^$/d' | tee -a "$lout" | grep -c .)"
            [[ "${n:-0}" -gt 0 ]] && log "  launchers: $n tracked file(s) differ from @{upstream} (UNPUSHED) - backing them up; PUSH to GitHub for real recovery"
        fi
        rm -f "$dtmp"
    fi
    LC_ALL=C sort -u "$lout" -o "$lout"
    return 0
}

need_bins(){ [[ -x "$RCLONE" ]] || die "rclone missing or not executable ($RCLONE)"; }

# ---- own-toggle categories: WHAT the cloud backs up. Tier A = the precious set
#      (push-precious); Tier B = the big library (sync-library). Persisted at
#      $STATE_DIR/categories.conf as `key=on|off`; an absent key defaults to ON, so a fresh
#      Deck backs up everything (today's behaviour). The always-on core (MAD/router config,
#      Claude memory, launchers) is NOT a category - it is always included.
_CAT_A="esde emu saves bios"
_CAT_B="roms romsint openbor media cores bezels rpcs3games pcsx2tex ryujinxgames"
_cat_label(){
    case "$1" in
        esde) printf 'ES-DE settings';;    emu) printf 'Emulator config + data';;
        saves) printf 'Saves';;            bios) printf 'BIOS';;
        roms) printf 'ROMs (SD)';;         romsint) printf 'ROMs (internal)';;
        openbor) printf 'OpenBOR games';;  media) printf 'Downloaded media';;
        cores) printf 'RetroArch cores';;  bezels) printf 'Bezels';;
        rpcs3games) printf 'RPCS3 games';;  pcsx2tex) printf 'PCSX2 textures';;
        ryujinxgames) printf 'Ryujinx games';; *) printf '%s' "$1";;
    esac
}
_cat_valid(){ [[ " $_CAT_A $_CAT_B " == *" $1 "* ]]; }
# ON unless categories.conf explicitly says `off` (default all-on = today's behaviour).
_cat_on(){
    local f="$STATE_DIR/categories.conf" v
    [[ -f "$f" ]] || return 0
    v="$(grep -E "^$1=" "$f" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d ' \t\r')" || true
    [[ "$v" == off ]] && return 1 || return 0
}

# Canonical $HOME-side "front door" per library category (may be a SYMLINK, e.g. ~/ROMs -> SD).
# restore --to-live recreates these. Only categories reached via a symlink need an entry.
# DECK_CLOUD_FRONTDOOR_<CAT> overrides (for tests, so we never touch the real ~/ROMs).
_frontdoor(){
    case "$1" in
        roms) printf '%s' "${DECK_CLOUD_FRONTDOOR_ROMS:-$HOME/ROMs}";;
        *)    printf '';;
    esac
}
# key -> MEGA library subdir (three keys are hyphenated to match the archive naming). Used by
# BOTH sync-library and restore-library so the upload + download paths can never drift.
_lib_sub(){
    case "$1" in
        rpcs3games)   printf '%s' rpcs3-games;;
        pcsx2tex)     printf '%s' pcsx2-textures;;
        ryujinxgames) printf '%s' ryujinx-games;;
        *)            printf '%s' "$1";;
    esac
}
# Live on-disk dir for a Tier-B category, from deck-backup.sh's single source of truth
# (key<TAB>path). Empty if the category isn't currently present. Lets restore --to-live target
# the real path for categories with no symlink front-door (media/cores/rpcs3games/...).
_livedir(){
    bash "$DECK_BACKUP" --list-library-items 2>/dev/null | awk -F'\t' -v c="$1" '$1==c{print $2; exit}'
}
# Record symlink front-doors (link -> resolved target) so a from-scratch restore can rebuild
# them. Written at backup time (the live Deck knows the links); uploaded beside the library.
_write_symlink_manifest(){
    local mf="$STATE_DIR/$LIB_MANIFEST" cat fd real rel link
    : > "$mf"
    # (1) category FRONT-DOOR symlinks (e.g. roms -> ~/ROMs): `<cat>\t<frontdoor-abspath>\t<target>`.
    for cat in roms media cores bezels rpcs3games pcsx2tex ryujinxgames; do
        fd="$(_frontdoor "$cat")"
        [[ -n "$fd" && -L "$fd" ]] && printf '%s\t%s\t%s\n' "$cat" "$fd" "$(readlink -f "$fd")" >> "$mf"
    done
    # (2) symlinks NESTED inside a front-door dir (e.g. ~/ROMs/ps2 -> ~/Emulation/roms/ps2,
    # ~/ROMs/openbor -> ~/OpenBor): `<cat>\t@<relpath>\t<target>`. The library copy SKIPS these
    # (no -L), so we record them + recreate on restore - keeps MEGA browsable (no .rclonelink files)
    # and never dereferences (no ~290G dup). The '@' prefix marks them apart from front-door rows.
    for cat in roms; do
        fd="$(_frontdoor "$cat")"; [[ -n "$fd" ]] || continue
        real="$(readlink -f "$fd" 2>/dev/null)"; [[ -n "$real" && -d "$real" ]] || continue
        while IFS= read -r link; do
            rel="${link#"$real"/}"
            printf '%s\t@%s\t%s\n' "$cat" "$rel" "$(readlink -f "$link")" >> "$mf"
        done < <(find "$real" -maxdepth 1 -type l 2>/dev/null)
    done
    if [[ -s "$mf" ]]; then
        lowprio "$RCLONE" copy "$mf" "${LIB_BASE}/" "${RCLONE_COMMON[@]}" >>"$LOG" 2>&1 \
            && log "  symlink manifest updated" || log "  WARN symlink manifest upload failed"
    fi
}
# Recorded target for a category from the S4 manifest (empty if none / not found).
_manifest_target(){
    local cat="$1" d mf t=""
    d="$(mktemp -d)"
    lowprio "$RCLONE" copy "${LIB_BASE}/$LIB_MANIFEST" "$d" "${RCLONE_COMMON[@]}" >>"$LOG" 2>&1 || true
    mf="$d/$LIB_MANIFEST"
    # front-door rows only: column 2 is an absolute path (not the '@<relpath>' nested-link rows).
    [[ -f "$mf" ]] && t="$(awk -F'\t' -v c="$cat" '$1==c && $2 !~ /^@/ {print $3; exit}' "$mf")"
    rm -rf "$d"
    printf '%s' "$t"
}
# Recreate the symlinks NESTED inside a restored category dir (e.g. ~/ROMs/ps2 -> ~/Emulation/roms/
# ps2), from the manifest's `@<relpath>` rows. The library copy skips these (no -L), so restore
# rebuilds them here. rule #5: an existing file/dir at the link path is moved aside first.
_restore_nested_links(){   # $1=cat  $2=target-dir  $3=backup-dir(_TMP)
    local cat="$1" target="$2" bdir="$3" d mf rel tgt lp n=0
    d="$(mktemp -d)"
    lowprio "$RCLONE" copy "${LIB_BASE}/$LIB_MANIFEST" "$d" "${RCLONE_COMMON[@]}" >>"$LOG" 2>&1 || true
    mf="$d/$LIB_MANIFEST"
    if [[ -f "$mf" ]]; then
        while IFS=$'\t' read -r rel tgt; do
            [[ -n "$rel" && -n "$tgt" ]] || continue
            lp="$target/$rel"
            [[ -L "$lp" && "$(readlink "$lp")" == "$tgt" ]] && continue   # already the right link
            mkdir -p "$(dirname "$lp")"
            if [[ -e "$lp" || -L "$lp" ]]; then
                mkdir -p "$bdir/$(dirname "$rel")"; mv "$lp" "$bdir/$rel" 2>/dev/null || true
            fi
            ln -s "$tgt" "$lp" && n=$((n+1))
        done < <(awk -F'\t' -v c="$cat" '$1==c && $2 ~ /^@/ {print substr($2,2)"\t"$3}' "$mf")
    fi
    rm -rf "$d"
    [[ $n -gt 0 ]] && log "  recreated $n nested symlink(s) under $target"
    return 0
}

# Setup complete? Cheap LOCAL check (no network): creds present + the rclone remote exists.
is_connected(){
    [[ "${DECK_CLOUD_SKIP_CONNCHECK:-0}" == 1 ]] && return 0   # test hook
    [[ -f "$S4_CREDS" ]] || return 1
    _load_s4_creds || return 1
    "$RCLONE" listremotes 2>/dev/null | grep -qx "${RCLONE_REMOTE}:" || return 1
    return 0
}

# ---- Tier A: precious, browsable, with a version net ----
# Backoff after failures so the timer/hook do not keep hammering S4. cooldown = min(3600,
# 300*fails)s; cleared on success; bypassed by --force (the manual "Back up now").
_backoff_active(){
    local fails last now cool
    [[ -f "$STATE_DIR/fail_count" && -f "$STATE_DIR/last_fail" ]] || return 1
    fails="$(cat "$STATE_DIR/fail_count" 2>/dev/null)"
    last="$(cat "$STATE_DIR/last_fail" 2>/dev/null)"
    [[ "$fails" =~ ^[0-9]+$ && "$last" =~ ^[0-9]+$ && "$fails" -gt 0 ]] || return 1
    now="$(date +%s)"
    cool=$(( 300 * fails )); (( cool > 3600 )) && cool=3600
    (( now - last < cool ))
}
_record_fail(){
    local n=1; [[ -f "$STATE_DIR/fail_count" ]] && n=$(( $(cat "$STATE_DIR/fail_count" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$STATE_DIR/fail_count"; date +%s > "$STATE_DIR/last_fail"
    log "push-precious FAILED (see $LOG); backoff #$n"
}
cmd_push_precious(){
    need_bins
    local force=0; [[ "${1:-}" == --force ]] && force=1
    [[ $force -eq 1 ]] && _FG=1   # the manual "Back up now" always passes --force: run at full speed
    if ! is_connected; then
        log "not connected (run deck-cloud-setup.sh in Desktop Mode) - skipping push"
        return 0
    fi
    if [[ $force -eq 0 ]] && _backoff_active; then
        log "backing off after a recent failure - skipping this round"
        return 0
    fi
    # flock so the game-end hook, the timer AND prune never collide.
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then log "another cloud op holds the lock - skipping this round"; return 0; fi

    # Enumerate the precious set. A FAILURE here must NOT look like an empty (successful)
    # backup, so capture status instead of a bare pipe.
    local raw ITEMS=() p aflags=()
    # Tier-A selection (own toggles) -> deck-backup include flags; the on-exit hook + timer
    # honor it too (headless). --no-cores/--no-bezels are always dropped from Tier A.
    _cat_on esde  && aflags+=(--esde)  || aflags+=(--no-esde)
    _cat_on emu   && aflags+=(--emu)   || aflags+=(--no-emu)
    _cat_on saves && aflags+=(--saves) || aflags+=(--no-saves)
    _cat_on bios  && aflags+=(--bios)  || aflags+=(--no-bios)
    if ! raw="$(bash "$DECK_BACKUP" --list-items "${aflags[@]}" --no-cores --no-bezels 2>/dev/null)"; then
        log "item enumeration FAILED (deck-backup.sh --list-items) - skipping to avoid a partial backup"
        return 1
    fi
    while IFS= read -r p; do
        [[ "$p" == /* && -e "$p" ]] || continue
        if _cloud_skip_item "$p"; then log "  cloud: skipping re-acquirable $p"; continue; fi
        ITEMS+=( "$p" )
    done <<< "$raw"
    if [[ ${#ITEMS[@]} -eq 0 ]]; then log "no precious paths resolved - nothing to back up"; return 0; fi

    local ts rel destsub ok=0 fail=0
    ts="$(date +%Y%m%d-%H%M%S)"
    log "backing up ${#ITEMS[@]} precious path(s) to ${PRECIOUS_BASE} (browsable)"
    local _mf _fl
    for p in "${ITEMS[@]}"; do
        rel="${p#"$HOME"/}"; [[ "$rel" == "$p" ]] && rel="${p#/}"   # home-relative, else absolute-mirror
        if [[ -d "$p" ]]; then destsub="$rel"; else destsub="$(dirname "$rel")"; fi
        # The launchers item is a git worktree (mmadalone/mad): back up ONLY what a fresh install.sh
        # clone would NOT recreate (untracked + ignored config + unpushed tracked edits), NEVER the
        # tracked code. A per-backup .mad-cloud-manifest.txt carries the config allowlist to restore.
        if [[ "$p" -ef "$LAUNCHERS_DIR" ]]; then
            _mf="$(mktemp)"; _fl="$(mktemp)"
            if _launchers_localonly "$p" "$_mf" "$_fl"; then
                if [[ ! -s "$_fl" ]]; then
                    log "  launchers: no local-only files (all tracked/clean) - nothing to upload"
                    ok=$((ok+1))
                # HARD RULE: --files-from + ANY filter (EXCL_RCLONE/--exclude) is a FATAL rclone
                # error that aborts the copy - NEVER add a filter here; debris is pre-filtered into
                # $_fl. -L keeps the one symlinked config; --backup-dir keeps the version/rollback net.
                elif rclone_copy "$p" "${PRECIOUS_BASE}/${destsub}" \
                        --backup-dir "${PRECIOUS_VERS}/${ts}/${destsub}" \
                        -L --files-from "$_fl" "${RCLONE_COMMON[@]}"; then
                    "$RCLONE" copyto "$_mf" "${PRECIOUS_BASE}/${destsub}/.mad-cloud-manifest.txt" >>"$LOG" 2>&1 \
                        || log "  launchers: manifest upload failed (restore will use the pinned allowlist)"
                    ok=$((ok+1))
                else
                    log "  copy had errors: $p (continuing)"; fail=$((fail+1))
                fi
            else
                log "  launchers: git enumeration unavailable - whole-dir-minus-excludes fallback"
                if rclone_copy "$p" "${PRECIOUS_BASE}/${destsub}" \
                        --backup-dir "${PRECIOUS_VERS}/${ts}/${destsub}" \
                        "${EXCL_RCLONE[@]}" -L "${RCLONE_COMMON[@]}"; then
                    ok=$((ok+1))
                else
                    log "  copy had errors: $p (continuing)"; fail=$((fail+1))
                fi
            fi
            rm -f "$_mf" "$_fl"
            continue
        fi
        # -L follows a symlinked precious path (e.g. saves on the SD card).
        if rclone_copy "$p" "${PRECIOUS_BASE}/${destsub}" \
                --backup-dir "${PRECIOUS_VERS}/${ts}/${destsub}" \
                "${EXCL_RCLONE[@]}" -L "${RCLONE_COMMON[@]}"; then
            ok=$((ok+1))
        else
            log "  copy had errors: $p (continuing; e.g. a broken symlink or a file changed mid-copy)"
            fail=$((fail+1))
        fi
    done
    # Backoff ONLY when NOTHING uploaded (a real connection/auth failure fails EVERY path). A
    # single problem path (broken symlink, save written mid-copy) must not block all backups.
    if [[ $ok -gt 0 ]]; then
        date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE_DIR/last_backup"
        rm -f "$STATE_DIR/fail_count" "$STATE_DIR/last_fail"
        [[ $fail -gt 0 ]] && log "push-precious OK ($ok path(s) ok, $fail with per-file issues - see $LOG)" \
                          || log "push-precious OK"
    else
        _record_fail
        return 1
    fi
}

# ---- Tier B: big library ----
cmd_sync_library(){
    need_bins
    _FG=1   # only ever invoked manually ("Sync library now") - run at full speed
    is_connected || die "not connected - run deck-cloud-setup.sh in Desktop Mode first"
    local key path sub rc=0
    while IFS=$'\t' read -r key path; do
        [[ -n "$key" && -n "$path" && -e "$path" ]] || continue   # skips the [backup] log line too
        _cat_on "$key" || { log "sync-library: $key SKIPPED (disabled in categories)"; continue; }
        sub="$(_lib_sub "$key")"
        log "sync-library: $key ($path) to ${LIB_BASE}/${sub}"
        # --skip-links: never dereference symlinks (e.g. ~/ROMs/ps2 -> ~/Emulation/roms). Their
        # targets are backed up as their own categories; the links themselves are recorded in the
        # manifest by _write_symlink_manifest below and recreated on restore (no ~290G dup, quiet).
        if ! rclone_copy "$path" "${LIB_BASE}/${sub}" --skip-links "${RCLONE_COMMON[@]}"; then
            log "sync-library: $key FAILED (continuing with the rest)"; rc=1
        fi
    done < <(bash "$DECK_BACKUP" --list-library-items 2>/dev/null)
    _write_symlink_manifest      # so restore --to-live can rebuild ~/ROMs etc.
    log "sync-library done (rc=$rc)"
    return $rc
}

# Launchers CONFIG allowlist - the FALLBACK used only when a backup has no .mad-cloud-manifest.txt
# (a pre-feature backup or a version folder). Normal backups carry their own manifest, which
# auto-tracks any NEW local config; this pinned list covers the known stable config. It NEVER names
# tracked code, so a launchers restore can never revert the live MAD code.
LAUNCHERS_CONFIG_ALLOWLIST=(
    controller-policy.local.toml sinden.conf install.conf openbor-metadata.json
    .bezel-manifest.txt .cores-manifest.txt .last-os-build
    data/es_systems_sorting.reference.xml
    deck-temps.sh es-de/es-de.sh esde/emulationstationde.sh srm/steamrommanager.sh
    romhack-art-urls.json romhack-websource-list.json skyscraper-flagged.json
)

# ---- restore (always into a STAGING dir; never blind-overwrite live files, rule #5) ----
cmd_snapshots(){
    need_bins; is_connected || die "not connected"
    # version folders = the rollback points (files overwritten on that date), newest first.
    "$RCLONE" lsf --dirs-only "${PRECIOUS_VERS}" 2>/dev/null | sed 's:/*$::' | sort -r
}

# Restore the precious set. Default = into a STAGING dir (review, then copy back). With
# --to-live it restores OVER the live $HOME data (saves + emulator/ES-DE configs + BIOS);
# overwritten files are moved to a recoverable _TMP first (rule #5), and the running-system
# paths (MAD code, the ES-DE AppImages, build scripts, Claude memory) are EXCLUDED so a
# restore never reverts the tooling out from under you.
# Usage: restore-precious [--to-live] [version|latest] [target-dir]
cmd_restore_precious(){
    need_bins; is_connected || die "not connected"
    _FG=1   # user-initiated restore: run at full speed
    local to_live=0 ver="" target=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --to-live) to_live=1;;
            *) if [[ -z "$ver" ]]; then ver="$1"; else target="$1"; fi;;
        esac; shift
    done
    [[ -z "$ver" ]] && ver="latest"
    local src
    if [[ "$ver" == latest ]]; then
        src="$PRECIOUS_BASE"
    else
        src="${PRECIOUS_VERS}/${ver}"
        log "note: version '$ver' holds only the PREVIOUS copies of files changed at that time"
        log "      (a per-file history, NOT a full snapshot); use 'latest' for the whole backup."
    fi

    if [[ $to_live -eq 0 ]]; then      # default: STAGING (never touch live, rule #5)
        target="${target:-$HOME/deck-cloud-restore/precious-$(date +%Y%m%d-%H%M%S)}"
        mkdir -p "$target"
        log "restore: copying ${src} into $target (staging; review, then copy back)"
        rclone_copy "$src" "$target" "${RCLONE_COMMON[@]}"
        log "restore-precious done: files are in $target"
        return 0
    fi

    # --to-live: restore over the live data. Restore each TOP-LEVEL subdir separately so its
    # _TMP backup-dir (under Downloads) sits OUTSIDE that subdir - rclone refuses a --backup-dir
    # that overlaps the destination. Overwrites -> _TMP (rule #5). Running-system paths (MAD
    # code, the ES-DE AppImages, build scripts, Claude memory, Downloads itself) are skipped so
    # a restore never reverts the tooling out from under you.
    local base="${target:-$HOME}"
    [[ -d "$base" ]] || die "restore --to-live: target '$base' does not exist"
    # Take the SAME lock push-precious uses (BLOCKING - a user restore must not silently skip) so
    # a timer/hook backup can't write the live tree while we restore over it.
    exec 9>"$LOCKFILE"
    flock -w 300 9 || die "restore --to-live: another cloud op is running; try again in a moment"
    local base_tmp="$base/Downloads/_TMP/cloud-restore-$(date +%Y%m%d-%H%M%S)"   # rule #5: fixed _TMP base
    local staged="$base_tmp/_staged-apply"    # $HOME-mirrored tree the wrapper applies on next boot
    mkdir -p "$base_tmp"
    # Enumerate the top-level backup dirs. A listing FAILURE or an EMPTY result must NOT report
    # success (mirrors push-precious); the `| sed` pipe would otherwise mask rclone's exit.
    local tops
    if ! tops="$("$RCLONE" lsf --dirs-only "$src" 2>>"$LOG")"; then
        die "restore --to-live: listing '$src' FAILED (network/auth) - nothing restored"
    fi
    tops="$(sed 's:/*$::' <<< "$tops")"
    [[ -n "${tops//[[:space:]]/}" ]] || die "restore --to-live: '$src' has nothing to restore (no backup yet, or a bad version id)"
    log "restore --to-live: ${src} -> $base (overwritten live files -> $base_tmp; rule #5). Close ES-DE + emulators first."
    local top rc=0 ex
    while IFS= read -r top; do
        top="${top%/}"; [[ -n "$top" ]] || continue
        case "$top" in
            Applications|esde-build|.claude|Downloads)
                log "  skip $top (tooling / not reverted)"; continue;;
            ES-DE)
                # ES-DE owns es_settings.xml + gamelists and REWRITES them on exit; the MAD panel IS
                # the running ES-DE, so an in-place restore here is clobbered on quit (rule #3). STAGE
                # it into the $HOME-mirrored tree; the launch wrapper applies it on the NEXT ES-DE
                # start (before ES-DE reads its config), triggered by the Restart button or a relaunch.
                mkdir -p "$staged/ES-DE"
                rclone_copy "${src}/ES-DE" "$staged/ES-DE" --checksum "${RCLONE_COMMON[@]}" \
                    && log "  ES-DE settings staged (applied on the next ES-DE start)."
                continue;;
        esac
        ex=()
        [[ "$top" == Emulation ]] && ex=( --exclude 'tools/launchers/**' )   # keep the live MAD code
        # --checksum: a RESTORE must overwrite whenever the CONTENT differs (a same-size save
        # edit, or a rollback where the backup is OLDER than the live file) - size+mtime would
        # wrongly skip those. Precious files are small (single-part), so the S3 MD5 ETag works.
        if ! rclone_copy "${src}/${top}" "$base/${top}" --backup-dir "$base_tmp/${top}" --checksum "${ex[@]}" "${RCLONE_COMMON[@]}"; then
            log "  restore: $top had errors (continuing)"; rc=1
        fi
    done <<< "$tops"

    # Launchers CONFIG (NEVER the tracked code): staged like ES-DE and applied on next boot. Prefer
    # the backup's own manifest (auto-tracks new config); fall back to the pinned allowlist for a
    # manifest-less / version-folder backup. The manifest names ONLY config, so even a stale
    # whole-tree copy lingering in precious/ can never stage tracked code (resolves the union risk).
    local lsrc="${src}/Emulation/tools/launchers" mftmp
    mftmp="$(mktemp)"
    if ! { "$RCLONE" copyto "${lsrc}/.mad-cloud-manifest.txt" "$mftmp" 2>>"$LOG" && [[ -s "$mftmp" ]]; }; then
        printf '%s\n' "${LAUNCHERS_CONFIG_ALLOWLIST[@]}" > "$mftmp"
    fi
    mkdir -p "$staged/Emulation/tools/launchers"
    # NO filter alongside --files-from (fatal rclone error).
    rclone_copy "$lsrc" "$staged/Emulation/tools/launchers" --files-from "$mftmp" "${RCLONE_COMMON[@]}" \
        || log "  launchers config: staging had errors (continuing)"
    rm -f "$mftmp"

    # Arm the launch wrapper to apply the staged tree on the NEXT ES-DE start - only if something
    # was actually staged (so the wrapper never chases an empty marker).
    if [[ -n "$(find "$staged" -type f -print -quit 2>/dev/null)" ]]; then
        mkdir -p "$STATE_DIR"
        printf '%s\n' "$staged" > "$STATE_DIR/pending-restore-apply"
        # The apply happens in ~/Applications/ES-DE.AppImage (the launch wrapper). A wrapper written
        # BEFORE this feature lacks the apply hook, so a restart would be silently inert - regenerate
        # it from the single source of truth (deck-post-update.sh --wrapper) if the hook is missing,
        # and tell the truth about whether a restart will actually apply.
        local wrapper="$HOME/Applications/ES-DE.AppImage" wrap_ok=1
        if [[ -e "$wrapper" ]] && ! grep -q apply-staged-restore.sh "$wrapper" 2>/dev/null; then
            log "  launch wrapper predates staged-restore; regenerating it (deck-post-update.sh --wrapper)"
            bash "$HERE/deck-post-update.sh" --wrapper >>"$LOG" 2>&1 || true
            grep -q apply-staged-restore.sh "$wrapper" 2>/dev/null || wrap_ok=0
        fi
        if [[ "$wrap_ok" == 1 ]]; then
            log "  staged ES-DE + launchers config armed - applied on the next ES-DE start (Restart to apply now)."
        else
            log "  staged ES-DE + launchers config armed, BUT the launch wrapper lacks the apply hook - run 'deck-post-update.sh --wrapper' then restart ES-DE to apply."
        fi
    fi
    log "restore-precious --to-live done (rc=$rc): saves + emulator configs restored in place; ES-DE + launchers config staged for the next ES-DE start; overwrites in $base_tmp (rule #5)"
    return $rc
}

cmd_restore_library(){
    need_bins; is_connected || die "not connected"
    _FG=1   # user-initiated restore: run at full speed
    local cat="${1:?usage: restore-library <category> [--to-live] [target-dir]}"; shift
    local to_live=0 target=""
    while [[ $# -gt 0 ]]; do case "$1" in --to-live) to_live=1;; *) target="$1";; esac; shift; done
    local sub; sub="$(_lib_sub "$cat")"   # the S4 subdir may be hyphenated (matches sync-library)

    if [[ $to_live -eq 0 ]]; then     # default: STAGING (never touch live, rule #5)
        target="${target:-$HOME/deck-cloud-restore/library-$cat-$(date +%Y%m%d-%H%M%S)}"
        mkdir -p "$target"
        log "restore: copying ${LIB_BASE}/${sub} into $target (staging; review, then copy back)"
        rclone_copy "${LIB_BASE}/${sub}" "$target" "${RCLONE_COMMON[@]}"
        log "restore-library done: $cat is staged in $target"
        return 0
    fi

    # --to-live: restore files to the REAL location + (for a symlink front-door) recreate it.
    exec 9>"$LOCKFILE"
    flock -w 300 9 || die "restore --to-live: another cloud op is running; try again in a moment"
    local fd; fd="$(_frontdoor "$cat")"
    if [[ -z "$target" ]]; then
        if [[ -n "$fd" && -L "$fd" ]]; then target="$(readlink -f "$fd")"      # symlink present: adapt to CURRENT card
        elif [[ -n "$fd" ]]; then target="$(_manifest_target "$cat")"          # absent OR a real dir: recorded target
        else target="$(_livedir "$cat")"; fi                                   # no front-door: the real live path
    fi
    [[ -n "$target" ]] || die "restore --to-live: can't resolve a live target for '$cat' (it may not exist on this Deck yet). Pass an explicit target dir."
    # If the target's parent isn't mounted (e.g. wrong/absent SD card), refuse rather than
    # silently write to the internal drive at a mount path.
    [[ -d "$(dirname "$target")" ]] || die "restore --to-live: target parent '$(dirname "$target")' is not mounted. Insert/mount the drive, or pass an explicit target."
    mkdir -p "$target"
    local bdir="$HOME/Downloads/_TMP-cloud-restore-$cat-$(date +%Y%m%d-%H%M%S)"
    log "restore --to-live: ${LIB_BASE}/${sub} -> $target (any overwritten local file -> $bdir; rule #5)"
    rclone_copy "${LIB_BASE}/${sub}" "$target" --backup-dir "$bdir" "${RCLONE_COMMON[@]}"
    if [[ -n "$fd" ]]; then      # recreate the symlink front-door (rule #5: never clobber)
        if [[ -L "$fd" && "$(readlink -f "$fd")" == "$(readlink -f "$target")" ]]; then
            log "  front-door $fd already links to $target"
        else
            if [[ -e "$fd" || -L "$fd" ]]; then
                mkdir -p "$bdir"; mv "$fd" "$bdir/$(basename "$fd").frontdoor" \
                    && log "  moved existing $fd aside (recoverable in $bdir)"
            fi
            ln -s "$target" "$fd" && log "  symlink $fd -> $target recreated"
        fi
    fi
    _restore_nested_links "$cat" "$target" "$bdir"   # rebuild ~/ROMs/ps2 -> ... style nested links
    log "restore-library --to-live done: $cat files in $target"
}

# ---- retention: keep the newest N version folders under precious-versions ----
cmd_prune(){
    need_bins; is_connected || die "not connected"
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then die "another cloud op is running - try prune again later"; fi
    local keep="${DECK_CLOUD_KEEP_VERSIONS:-30}" i=0 v
    [[ "$keep" =~ ^[0-9]+$ && "$keep" -ge 1 ]] || { log "prune: invalid keep '$keep' - using 30"; keep=30; }
    local vers=(); mapfile -t vers < <("$RCLONE" lsf --dirs-only "${PRECIOUS_VERS}" 2>/dev/null | sed 's:/*$::' | sort -r)
    log "prune: ${#vers[@]} version folder(s); keeping newest $keep"
    for v in "${vers[@]}"; do
        i=$((i+1))
        (( i > keep )) || continue
        lowprio "$RCLONE" purge "${PRECIOUS_VERS}/${v}" >>"$LOG" 2>&1 && log "  pruned $v" || log "  WARN could not prune $v"
    done
    log "prune done"
}

# ---- toggles: on-exit backup (flag file, read by the hook) + during-play timer ----
cmd_set_toggle(){
    local which="${1:?usage: set-toggle <onexit|timer|autoresume> <on|off>}" val="${2:?on|off}"
    case "$which" in
        onexit)
            if [[ "$val" == on ]]; then : > "$STATE_DIR/onexit.enabled"; echo "on-exit backup: ON"
            else rm -f "$STATE_DIR/onexit.enabled"; echo "on-exit backup: OFF"; fi ;;
        timer)
            if [[ "$val" == on ]]; then
                systemctl --user enable --now cloud-sync.timer && echo "during-play sync: ON"
            else
                systemctl --user disable --now cloud-sync.timer && echo "during-play sync: OFF"
            fi ;;
        autoresume)   # value file; DEFAULT ON (absent or 'on' = enabled), read by the RPC daemon
            printf '%s\n' "$val" > "$STATE_DIR/autoresume"; echo "auto-resume: $val" ;;
        *) die "unknown toggle '$which' (want onexit|timer|autoresume)";;
    esac
}

# ---- systemd units (single source of truth; called by install.sh, deck-cloud-setup.sh,
#      and deck-post-update.sh so the during-play toggle works right after a fresh install) ----
cmd_ensure_units(){
    local ud="$HOME/.config/systemd/user"
    mkdir -p "$ud"
    cat > "$ud/cloud-sync.service" <<'CLOUDSVC'
[Unit]
Description=MAD cloud: back up saves + configs to MEGA S4 (Tier A)
After=graphical-session.target
[Service]
Type=oneshot
ExecStart=%h/Emulation/tools/launchers/deck-cloud.sh push-precious
CLOUDSVC
    cat > "$ud/cloud-sync.timer" <<'CLOUDTMR'
[Unit]
Description=MAD cloud: periodic during-play save backup (opt-in)
[Timer]
OnActiveSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
CLOUDTMR
    systemctl --user daemon-reload 2>/dev/null || true
    echo "cloud-sync.service/.timer ensured (opt-in; not enabled here)"
}

# ---- status: machine-parseable key<TAB>value (cloud_cmds.py reads this) ----
cmd_status(){
    need_bins
    local connected=0; is_connected && connected=1
    local timer=0; systemctl --user is-active --quiet cloud-sync.timer 2>/dev/null && timer=1
    local onexit=0; [[ -f "$STATE_DIR/onexit.enabled" ]] && onexit=1
    local last=""; [[ -f "$STATE_DIR/last_backup" ]] && last="$(cat "$STATE_DIR/last_backup" 2>/dev/null)"
    local autoresume=1; [[ "$(cat "$STATE_DIR/autoresume" 2>/dev/null)" == off ]] && autoresume=0  # default ON
    printf 'connected\t%s\n'       "$connected"
    printf 'bucket\t%s\n'          "$S4_BUCKET"
    printf 'server\t%s\n'          "$SERVER_KEY"
    printf 'server_label\t%s\n'    "$_SRV_LABEL"
    printf 'endpoint\t%s\n'        "$S4_ENDPOINT"
    printf 'precious\t%s\n'        "$PRECIOUS_BASE"
    printf 'timer_active\t%s\n'    "$timer"
    printf 'onexit_enabled\t%s\n'  "$onexit"
    printf 'autoresume_enabled\t%s\n' "$autoresume"
    printf 'last_backup\t%s\n'     "$last"
}

# ---- probe: ONE bounded S4 reachability check (list the bucket). ----
cmd_probe(){
    need_bins
    [[ -f "$S4_CREDS" ]] || die "no S4 credentials at $S4_CREDS - run deck-cloud-setup.sh first"
    _load_s4_creds || die "S4 credentials file present but unreadable/empty ($S4_CREDS)"
    log "probing MEGA S4 (list bucket '$S4_BUCKET')..."
    if timeout 45 "$RCLONE" lsd "${RCLONE_REMOTE}:${S4_BUCKET}" >/dev/null 2>&1; then
        echo "OK: MEGA S4 reachable (bucket '$S4_BUCKET')"
    else
        local rc=$?
        echo "FAILED to reach MEGA S4 (rc=$rc). Check the keys in $S4_CREDS, the rclone"
        echo "'$RCLONE_REMOTE' remote, and network. Run deck-cloud-setup.sh to (re)configure."
        return "$rc"
    fi
}

# ---- MEGA S4 server selection: list the servers / switch the active one ----
_reachable(){ timeout "${1:-45}" "$RCLONE" lsd "${RCLONE_REMOTE}:${S4_BUCKET}" >/dev/null 2>&1; }

cmd_list_servers(){   # key<TAB>label<TAB>endpoint<TAB>region<TAB>current(0/1) - one row per server
    local k ep rg lbl
    for k in $_SERVER_KEYS; do
        IFS='|' read -r ep rg lbl <<< "$(_server_row "$k")"
        printf '%s\t%s\t%s\t%s\t%s\n' "$k" "$lbl" "$ep" "$rg" "$([[ "$k" == "$SERVER_KEY" ]] && echo 1 || echo 0)"
    done
}

cmd_set_server(){
    local key="${1:?usage: set-server <key> [--no-probe]}" probe=1
    [[ "${2:-}" == --no-probe ]] && probe=0
    [[ -n "$(_server_row "$key")" ]] || die "unknown server '$key' (valid: $_SERVER_KEYS)"
    mkdir -p "$STATE_DIR"; printf '%s\n' "$key" > "$STATE_DIR/server"
    local ep rg lbl; IFS='|' read -r ep rg lbl <<< "$(_server_row "$key")"
    echo "server set to: $lbl ($ep)"
    [[ $probe -eq 0 ]] && return 0
    need_bins
    if ! is_connected; then
        echo "note: not connected yet - run deck-cloud-setup.sh in Desktop Mode (choice saved)"
        return 0
    fi
    _export_server_env "$ep" "$rg"   # probe the JUST-CHOSEN server, not the one resolved at startup
    if _reachable 45; then echo "OK: $lbl reachable"
    else echo "WARNING: $lbl not reachable right now (choice saved; switch back with: set-server global)"; fi
}

# ---- own-toggle categories: list / flip what the cloud backs up ----
cmd_list_categories(){   # tier<TAB>key<TAB>label<TAB>on(0/1) - one row per category
    local k
    for k in $_CAT_A; do printf 'A\t%s\t%s\t%s\n' "$k" "$(_cat_label "$k")" "$(_cat_on "$k" && echo 1 || echo 0)"; done
    for k in $_CAT_B; do printf 'B\t%s\t%s\t%s\n' "$k" "$(_cat_label "$k")" "$(_cat_on "$k" && echo 1 || echo 0)"; done
}
cmd_set_category(){
    local key="${1:?usage: set-category <key> <on|off>}" val="${2:?on|off}"
    _cat_valid "$key" || die "unknown category '$key' (valid: $_CAT_A $_CAT_B)"
    [[ "$val" == on || "$val" == off ]] || die "value must be on|off (got '$val')"
    mkdir -p "$STATE_DIR"
    local f="$STATE_DIR/categories.conf"
    { [[ -f "$f" ]] && grep -vE "^$key=" "$f" || true; printf '%s=%s\n' "$key" "$val"; } > "$f.tmp"
    mv "$f.tmp" "$f"
    echo "$key: $val"
}

# ---- cloud-sizes: the ACTUAL upload size the cloud would send per Tier-A category (esde/emu/
#      saves/bios), so the panel chips can't overstate. It applies the SAME filters push-precious
#      does - EXCL_RCLONE + -L + _cloud_skip_item - and, like deck-backup.sh --sizes, reports
#      DISJOINT per-category sizes: the always-on core (launchers, MAD/router config, Claude
#      memory, the AppImage, ...) rides every backup but is not a toggleable chip, so it is
#      subtracted out. Tier B is copied wholesale, so the panel keeps deck-backup's full sizes
#      for those. Emits `key<TAB>bytes`. Cost ~10-12s (per-path rclone size walks); the caller
#      runs it async.
cmd_cloud_sizes(){
    need_bins
    local core_list cat aflags total p b
    # core = every category OFF; these paths are subtracted from each category so a chip shows
    # only its OWN data (mirrors deck-backup.sh --sizes' disjoint buckets, without duplicating
    # its item arrays here - deck-backup stays the single source of truth for what's in each).
    core_list="$(bash "$DECK_BACKUP" --list-items --no-esde --no-emu --no-saves --no-bios \
                     --no-cores --no-bezels 2>/dev/null | grep '^/' || true)"
    for cat in $_CAT_A; do
        case "$cat" in
            esde)  aflags=(--esde   --no-emu  --no-saves --no-bios);;
            emu)   aflags=(--no-esde --emu    --no-saves --no-bios);;
            saves) aflags=(--no-esde --no-emu --saves    --no-bios);;
            bios)  aflags=(--no-esde --no-emu --no-saves --bios   );;
            *)     continue;;
        esac
        total=0
        while IFS= read -r p; do
            [[ "$p" == /* && -e "$p" ]] || continue
            grep -qxF "$p" <<< "$core_list" && continue   # always-on core: not this chip
            _cloud_skip_item "$p" && continue             # re-acquirable (AppImage / Skraper)
            # -L matches the real copy (push-precious copies with -L), so the size reflects the
            # ACTUAL bytes uploaded. This matters for saves: ~/Emulation/saves is a hub of
            # symlinks to each emulator's real save dir, and some targets are NOT under any other
            # category (Dolphin Wii NAND under .../data/, Ryujinx saves under ~/.config/Ryujinx),
            # so WITHOUT -L they'd be counted on no chip at all while push still uploads them.
            # Where a target also lives under another item (e.g. RetroArch saves), push copies it
            # under BOTH dest paths, so counting it in both chips reflects the real (duplicated)
            # upload. Core paths are still subtracted so a chip shows its own leg.
            # || true: a path rclone can't size yields no match; without it pipefail+set -e
            # would abort the whole command mid-category.
            b="$(lowprio "$RCLONE" size --json -L "${EXCL_RCLONE[@]}" "$p" 2>/dev/null \
                 | grep -oE '"bytes":[0-9]+' | grep -oE '[0-9]+' | tail -1 || true)"
            [[ "$b" =~ ^[0-9]+$ ]] && total=$(( total + b ))
        done < <(bash "$DECK_BACKUP" --list-items "${aflags[@]}" --no-cores --no-bezels 2>/dev/null)
        printf '%s\t%s\n' "$cat" "$total"
    done
}

cmd="${1:-status}"; shift || true
case "$cmd" in
    push-precious)    cmd_push_precious "$@";;
    sync-library)     cmd_sync_library "$@";;
    snapshots)        cmd_snapshots "$@";;
    restore-precious) cmd_restore_precious "$@";;
    restore-library)  cmd_restore_library "$@";;
    status)           cmd_status "$@";;
    list-servers)     cmd_list_servers "$@";;
    set-server)       cmd_set_server "$@";;
    list-categories)  cmd_list_categories "$@";;
    set-category)     cmd_set_category "$@";;
    cloud-sizes)      cmd_cloud_sizes "$@";;
    set-toggle)       cmd_set_toggle "$@";;
    ensure-units)     cmd_ensure_units;;
    probe)            cmd_probe;;
    prune)            cmd_prune "$@";;
    is-connected)     is_connected && { echo yes; exit 0; } || { echo no; exit 3; };;
    -h|--help)        sed -n '2,52p' "$0";;
    *) die "unknown command '$cmd' (try: status, list-servers, set-server, push-precious, sync-library, snapshots, restore-precious, restore-library, set-toggle, prune)";;
esac
