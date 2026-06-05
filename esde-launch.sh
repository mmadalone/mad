#!/usr/bin/env bash
# Startup-splash feature dropped 2026-06-04 (ES-DE has no custom-splash support —
# only --no-splash / SplashScreenProgressBarColor). Pure passthrough so the
# existing Steam shortcut keeps working. Revert the shortcut Target to
# .../es-de/es-de.sh and this file can be deleted.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/es-de/es-de.sh" "$@"
