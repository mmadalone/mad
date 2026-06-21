#!/usr/bin/env bash
# Record the carousel system/collection view for the launch-screen resolver
# (launched-from mode). ES-DE fires system-select with $1 = the shortname when you
# enter a view. With QuickSystemSelect=off, ALL navigation is via the carousel, so
# this is reliably the collection/system you launch FROM.
set -uo pipefail
STATE="${XDG_RUNTIME_DIR:-/tmp}/es-current-view"
printf '%s' "${1:-}" > "$STATE.tmp" 2>/dev/null && mv -f "$STATE.tmp" "$STATE" 2>/dev/null
exit 0
