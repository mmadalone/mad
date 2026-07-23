#!/usr/bin/env bash
# apply-staged-restore.sh - lay a cloud-restore "staged config" tree onto the live $HOME, then
# clear the marker. It is run by the ES-DE launch wrapper BEFORE ES-DE starts (so ES-DE reads the
# restored config and its own exit-rewrite of es_settings.xml/gamelists cannot clobber it), and by
# the "Restart ES-DE" button's relaunch.
#
# HARD REQUIREMENT - FAIL-SAFE: this runs inside the LOAD-BEARING launch wrapper. ANY failure
# (missing tools, a bad marker, a partial tree, a full disk) MUST be swallowed so ES-DE always
# boots - the user has no display to debug a boot hang. Every step is best-effort and the script
# always exits 0.
#
# Safety: cmd_restore_precious NEVER stages tracked code (only ES-DE config + the launchers config
# allowlist), so this only ever lays down config. Overwritten live files go to a recoverable
# ~/Downloads/_TMP/wrapper-apply-<ts>/ FIRST (rule #5). The marker is single-shot: it is cleared
# after ONE best-effort pass so a failure can never loop and re-clobber config on every boot.

STATE_DIR="${DECK_CLOUD_STATE_DIR:-$HOME/.config/deck-cloud}"
MARKER="$STATE_DIR/pending-restore-apply"
APPLY_LOG="${DECK_CLOUD_APPLY_LOG:-$STATE_DIR/apply-staged.log}"

# Fast no-op on the overwhelmingly common launch where nothing is armed.
[ -f "$MARKER" ] || exit 0

_ts(){ date +%Y%m%d-%H%M%S 2>/dev/null || printf 'unknown'; }
_log(){ mkdir -p "$STATE_DIR" 2>/dev/null; printf '[apply-staged %s] %s\n' "$(date '+%F %T' 2>/dev/null)" "$*" >> "$APPLY_LOG" 2>/dev/null; }

_apply(){
    local staged tmp applied=0 f rel dst
    staged="$(head -n1 "$MARKER" 2>/dev/null)"
    if [ -z "$staged" ] || [ ! -d "$staged" ]; then
        _log "marker present but staged dir missing ('$staged') - clearing marker"
        rm -f "$MARKER" 2>/dev/null
        return 0
    fi
    tmp="$HOME/Downloads/_TMP/wrapper-apply-$(_ts)"
    mkdir -p "$tmp" 2>/dev/null
    # RECOVERY.txt so an overwrite is always findable/undoable (rule #5).
    printf 'Cloud-restore staged config applied over live $HOME on %s.\nStaged from: %s\nOverwritten live files were moved HERE first; copy any back to undo.\n' \
        "$(date '+%F %T' 2>/dev/null)" "$staged" > "$tmp/RECOVERY.txt" 2>/dev/null

    # The staged tree mirrors $HOME. Copy each staged file over live; back up any existing live file
    # to $tmp/<rel> first. --remove-destination so a staged regular file replaces a live SYMLINK
    # (e.g. esde/emulationstationde.sh) instead of writing through it.
    while IFS= read -r -d '' f; do
        rel="${f#"$staged"/}"
        [ -n "$rel" ] && [ "$rel" != "$f" ] || continue          # guard: only paths under $staged
        dst="$HOME/$rel"
        if [ -e "$dst" ] || [ -L "$dst" ]; then
            # rule #5: NEVER destroy a live file whose _TMP backup could not be written. If the
            # backup fails (full disk, unwritable _TMP), SKIP this file - leave live untouched -
            # rather than have --remove-destination unlink it with no recoverable copy.
            if ! { mkdir -p "$tmp/$(dirname -- "$rel")" 2>/dev/null && cp -a -- "$dst" "$tmp/$rel" 2>/dev/null; }; then
                _log "WARN could not back up live $rel to _TMP - leaving it untouched (not applied)"
                continue
            fi
        fi
        if mkdir -p "$(dirname -- "$dst")" 2>/dev/null && cp -a --remove-destination -- "$f" "$dst" 2>/dev/null; then
            applied=$((applied + 1))
        else
            _log "WARN could not apply $rel (skipped; boot continues)"
        fi
    done < <(find "$staged" -type f -print0 2>/dev/null)

    _log "applied $applied staged file(s) from $staged (overwritten live files -> $tmp)"
    # Single-shot: clear the marker after one best-effort pass (never loop / re-clobber). The staged
    # tree stays in _TMP (recoverable); re-run a restore from the panel if something did not land.
    rm -f "$MARKER" 2>/dev/null
    return 0
}

_apply 2>/dev/null
exit 0
