#!/usr/bin/env bash
# ES-DE game-start hook: ONE batched controller-router call whose answers the
# later hooks consume (04-controller-router-setup, quit-combo-watcher, sinden),
# replacing 4-6 separate ~100-250 ms router/python invocations per launch
# (AUDIT-2026-08-12 PERFORMANCE-1). The router prints shlex-quoted MAD_LI_*
# KEY=VALUE lines with MAD_LI_ROM/MAD_LI_SYSTEM LAST as the completeness marker
# the consumers validate; the file is written atomically (tmp + mv) under
# umask 077. On ANY failure the cache is REMOVED and the consumers fall back to
# their own legacy per-mode router calls — hook/router version skew (e.g. a
# launchers-tree rollback that predates launch-info) degrades to the old
# per-call behavior, it never breaks a launch.
# $1=ROM (backslash-escaped)  $2=name  $3=system  $4=fullname
LOG=$HOME/Emulation/storage/sinden/logs/es-de-hooks.log
mkdir -p "$(dirname "$LOG")"
RT=$HOME/Emulation/tools/launchers
OUT="${XDG_RUNTIME_DIR:-/tmp}/mad-launch-info.env"
TMP="$OUT.tmp.$$"
# The Sinden game-start consumer is INSTALL_SINDEN-gated while this hook is
# core: when it is not deployed, skip the lightgun probe (a Wii ROM's probe
# imports the whole dolphin_wii_source stack). --no-lightgun OMITS the key, so
# if sinden.sh appears anyway it simply falls back to its own router call.
LG=""
[ -f "$HOME/ES-DE/scripts/game-start/sinden.sh" ] || LG="--no-lightgun"
T0=$(date +%s%3N)
umask 077
# shellcheck disable=SC2086  # $LG word-splits away when empty, by design
if "$RT/controller-router.py" launch-info $LG "$1" "$2" "$3" "$4" > "$TMP" 2>>"$LOG"; then
    # Never install over a file we do not own (sticky /tmp fallback dir): the rm
    # fails there by design, mv then fails, and the consumers' -O guard ignores
    # the foreign file anyway.
    [ -e "$OUT" ] && [ ! -O "$OUT" ] && rm -f "$OUT" 2>/dev/null
    if mv -f "$TMP" "$OUT" 2>/dev/null; then
        echo "[$(date +%H:%M:%S)] launch-info: $(( $(date +%s%3N) - T0 ))ms -> $OUT (system=$3)" >> "$LOG"
    else
        rm -f "$TMP" "$OUT" 2>/dev/null
        echo "[$(date +%H:%M:%S)] launch-info: cannot install $OUT; hooks fall back" >> "$LOG"
    fi
else
    rm -f "$TMP" "$OUT" 2>/dev/null
    echo "[$(date +%H:%M:%S)] launch-info FAILED after $(( $(date +%s%3N) - T0 ))ms; hooks fall back" >> "$LOG"
fi
exit 0
