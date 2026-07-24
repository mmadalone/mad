#!/usr/bin/env bash
# esde-health-check.sh — at ES-DE launch, detect whether a SteamOS update wiped the
# components this setup needs, and (controller-friendly) nudge the user to run the
# restore from Desktop Mode. GATED on the SteamOS BUILD_ID so the component checks run
# only after an actual OS update (Valve bumps BUILD_ID on every update). Best-effort:
# NEVER blocks or fails the launch. Invoked from esde-splash-gen.sh, which every
# ES-DE.AppImage wrapper calls — durable across SteamOS + EmuDeck updates.
#
# Why a NUDGE (not auto-restore): the restore needs root (sudo), and a sudoers grant
# would itself be wiped by the update (chicken-and-egg) — so the user runs it from
# Desktop. See memory mad-control-panel (Phase 4b dropped → this replaces it).
set -uo pipefail
L="$HOME/Emulation/tools/launchers"
MARKER="$L/.last-os-build"
# Flag the MAD panel reads on startup to OFFER the in-ES-DE reapply (postupdate_cmds). Armed only
# for a real post-UPDATE wipe below; the Desktop-Mode dialog stays as the fallback. Overridable=tests.
PENDING="${MAD_POSTUPDATE_FLAG:-$L/.post-update-pending}"

# Gamescope-friendly dialog (MAD's gamepad-navigable warning, 30s auto-proceed).
_warn(){ ( cd "$L" && DISPLAY="${DISPLAY:-:0}" python3 -m lib.warning_dialog "$1" "$2" ) >/dev/null 2>&1 || true; }

_body(){ printf 'A SteamOS update reset the system and wiped these parts of your setup:\n\n%s\n\nTo fix: switch to DESKTOP MODE, open a terminal, and run:\n  ~/Emulation/tools/launchers/deck-post-update.sh\n(it will ask for your password). Then return to Game Mode.' "$1"; }

# First-run variant: nothing was wiped by an update — the setup was just never finished.
# Point at the installer, not the post-update restore.
_body_firstrun(){ printf 'Some parts of this MAD setup are not installed yet:\n\n%s\n\nTo finish setup: switch to DESKTOP MODE, open a terminal, and run:\n  ~/Emulation/tools/launchers/install.sh\n(it will ask for your password). Then return to Game Mode.' "$1"; }

# TEST affordance: `touch ~/Emulation/tools/launchers/.healthcheck-test` to force the
# dialog on the next launch(es) — confirms it renders at ES-DE startup — then delete it.
if [ -f "$L/.healthcheck-test" ]; then
  m="$(bash "$L/deck-post-update.sh" --check 2>/dev/null)"
  [ -n "$m" ] || m="(nothing actually missing — this is a TEST of the warning dialog. Delete ~/Emulation/tools/launchers/.healthcheck-test to stop showing it.)"
  _warn "SteamOS updated — run the restore (TEST)" "$(_body "$m")"
  exit 0
fi

cur="$(grep -m1 '^BUILD_ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')"
[ -n "$cur" ] || exit 0                                   # no build id readable → do nothing
prev="$(cat "$MARKER" 2>/dev/null)"
[ "$cur" = "$prev" ] && exit 0     # known-good for this build → skip checks

# BUILD_ID changed (an update happened) or first run → check what the update wiped.
missing="$(bash "$L/deck-post-update.sh" --check 2>/dev/null)"
if [ -z "$missing" ]; then
  echo "$cur" >"$MARKER" 2>/dev/null || true             # all present → record build, don't nag again
  rm -f "$PENDING" 2>/dev/null || true                   # clear the in-ES-DE auto-offer flag
  exit 0
fi
# Something is missing — nudge. Do NOT update the marker, so it keeps nagging every
# launch until the user runs the restore (a later all-present launch then records it).
if [ -z "$prev" ]; then
  # First run on this Deck (no build ever recorded) — an incomplete first-time setup, not
  # an update casualty. Point at the installer instead of the post-update restore. (No PENDING
  # flag: the in-ES-DE reapply is for post-UPDATE recovery, not a fresh install.)
  _warn "Finish MAD setup — run the installer" "$(_body_firstrun "$missing")"
  exit 0
fi
# A SteamOS update wiped something: arm the in-ES-DE auto-offer ONLY. The MAD panel reads $PENDING on
# startup and offers the reapply in-app. We deliberately do NOT show a pre-ES-DE dialog here: it
# duplicated the in-app offer (an extra "press A" before ES-DE even loaded) and pointed at a
# Desktop-Mode workaround that the in-app reapply makes unnecessary. Arm the flag and get out of the way.
printf '%s\n' "$missing" > "$PENDING" 2>/dev/null || true
exit 0
