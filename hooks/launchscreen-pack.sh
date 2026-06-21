#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Shared resolver for the per-system launching-screens "pack".
# The launch screens are THEME ASSETS, so they live inside the active ES-DE
# theme as either "<theme>/_launching-screens" or "<theme>/launching-screens".
# This finds that dir for whatever theme is currently active and exports $PACK.
# Sourced by scripts/game-start/launchscreen.sh and scripts/game-end/launchscreen.sh.
#
# $PACK is empty if the active theme ships no launching-screens dir — callers
# should treat that as "no splash for this theme" and exit cleanly.
# ----------------------------------------------------------------------------
_resolve_launchscreen_pack() {
    local es_home settings theme userdir base sub
    es_home="$HOME/ES-DE"
    settings="$es_home/settings/es_settings.xml"

    # Active theme name (es_settings <string name="Theme" value="…"/>), default pixel-es-de.
    theme=$(grep -oE '<string name="Theme" value="[^"]*"' "$settings" 2>/dev/null \
            | sed -E 's/.*value="([^"]*)".*/\1/')
    [ -n "$theme" ] || theme="pixel-es-de"

    # Optional user theme dir; expand %HOME%/~.
    userdir=$(grep -oE '<string name="UserThemeDirectory" value="[^"]*"' "$settings" 2>/dev/null \
              | sed -E 's/.*value="([^"]*)".*/\1/')
    userdir="${userdir/\%HOME\%/$HOME}"
    userdir="${userdir/#\~/$HOME}"

    # Search the usual theme roots, accepting either dir name (underscore first).
    for base in "$userdir" "$es_home/themes" "$HOME/.local/share/es-de/themes" \
                "/usr/share/es-de/themes" "/app/share/es-de/themes"; do
        [ -n "$base" ] || continue
        for sub in _launching-screens launching-screens; do
            if [ -d "$base/$theme/$sub" ]; then
                printf '%s' "$base/$theme/$sub"
                return 0
            fi
        done
    done
    return 1
}

PACK="$(_resolve_launchscreen_pack)"

# Runtime state (splash PID + readiness handshake) — kept OUT of the theme dir,
# which may be read-only and should contain only assets. Both hooks share these.
_LS_RUN="${XDG_RUNTIME_DIR:-/tmp}"
PIDF="$_LS_RUN/launchscreen.splash.pid"
READY="$_LS_RUN/launchscreen.splash.ready"
