#!/usr/bin/env bash
# esde-rollback-prune.sh — keep only the newest N ES-DE rollback AppImages in
# ~/Applications, moving the rest OUT to a recoverable _TMP dir. Best-effort, silent on
# success, NEVER blocks or fails an ES-DE launch. Called from esde-splash-gen.sh.
#
# WHY THIS EXISTS. ES-DE's in-app updater renames the running AppImage to
# "<name>_<version>.OLD" before installing the new one (stock upstream behaviour,
# USERGUIDE "Upgrading to a newer release"). Upstream assumes consecutive releases carry
# DIFFERENT version strings, so a normal user accumulates one rollback per version. Every
# MAD build reports 3.4.1 and is distinguished only by the CI run number, so today all our
# builds collide on ONE filename and there is exactly one 120MB .OLD, ever.
#
# The moment the rollback name carries the run number, that bound disappears and it becomes
# 120MB PER UPDATE with nothing ever cleaning it up (ES-DE never deletes a .OLD). This script
# is what makes that naming change safe, so it ships first. The glob matches BOTH the stock
# name and the per-build one, so it is correct before and after.
#
# RULE #5: nothing is ever deleted. Excess rollbacks are MOVED to a same-filesystem _TMP dir
# (an instant rename) with a RECOVERY.txt naming the exact command to put one back. If the
# move cannot be done, the file is LEFT ALONE - never unlinked.
set -uo pipefail

APPS="${MAD_APPS_DIR:-$HOME/Applications}"
KEEP="${MAD_ROLLBACK_KEEP:-2}"
LOG="${MAD_ROLLBACK_LOG:-$HOME/Emulation/storage/controller-router/esde-splash.log}"

log(){ [ -n "$LOG" ] && mkdir -p "$(dirname "$LOG")" 2>/dev/null && \
       echo "[$(date '+%F %T')] rollback-prune: $*" >>"$LOG" 2>/dev/null; return 0; }

[ -d "$APPS" ] || exit 0
case "$KEEP" in ''|*[!0-9]*) KEEP=2 ;; esac      # a junk override must not wipe the lot

TS="$(date +%Y%m%d-%H%M%S)"
prune_dir="$APPS/_TMP-esde-mad-rollback-$TS"
_seen=0
_pruned=0

# Null-delimited throughout: `ls | xargs` both deletes user data and breaks on any space in
# the path, which is how deck-backup.sh's prune silently did nothing for months.
while IFS= read -r -d '' _rec; do
    _seen=$((_seen + 1))
    (( _seen <= KEEP )) && continue                # keep the newest KEEP
    _f="${_rec#*$'\t'}"                            # strip the leading "<mtime>\t"

    if (( _pruned == 0 )); then
        # Lazily created, so a run with nothing to prune leaves no empty dir behind.
        if ! mkdir -p "$prune_dir" 2>/dev/null; then
            log "WARN: cannot create $prune_dir — leaving every rollback in place"
            exit 0
        fi
        cat > "$prune_dir/RECOVERY.txt" <<RECO 2>/dev/null
MAD esde-rollback-prune.sh rotated these OLDER ES-DE rollback AppImages out of
  $APPS
keeping the newest $KEEP (override with MAD_ROLLBACK_KEEP). They were MOVED here,
NOT deleted.

To roll back to one of them:
  mv -f "$prune_dir/<file>" "$APPS/ES-DE-MAD.AppImage"
The ES-DE.AppImage wrapper re-extracts on the next launch.

To reclaim the space, delete this whole folder once you are sure you no longer
need these older builds. Each one is about 120 MB.
Rotated on $(date '+%Y-%m-%d %H:%M:%S').
RECO
    fi

    # Rule #5 + the apply-staged-restore.sh invariant: if the move fails (full disk,
    # unwritable target), SKIP this file. Never unlink what could not be preserved.
    if mv -- "$_f" "$prune_dir"/ 2>/dev/null; then
        _pruned=$((_pruned + 1))
        log "rotated out (recoverable): $(basename -- "$_f")"
    else
        log "WARN: could not rotate out $_f — left in place"
    fi
done < <(find "$APPS" -mindepth 1 -maxdepth 1 -type f \
             -name 'ES-DE-MAD.AppImage_*.OLD' \
             -printf '%T@\t%p\0' 2>/dev/null | sort -zrn)

if (( _pruned > 0 )); then
    log "rotated $_pruned old rollback(s) to $prune_dir (recoverable; not deleted)"
elif (( _seen == 0 )); then
    :                                              # nothing to do; stay silent
fi
exit 0
