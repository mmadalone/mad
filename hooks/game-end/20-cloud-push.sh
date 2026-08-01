#!/usr/bin/env bash
# game-end: back up saves + configs to MEGA (Tier A) the instant a game quits.
# FIRE-AND-FORGET - it detaches and returns immediately, so it never delays ES-DE
# getting back to the carousel. Fires on normal exit AND on a quit-combo kill
# (ES-DE runs game-end hooks either way), which is exactly when the save is final.
# Gated two ways so it is a harmless no-op until you opt in AND connect:
#   - the on-exit toggle flag (~/.config/deck-cloud/onexit.enabled), and
#   - deck-cloud.sh push-precious self-skips when the account isn't set up yet.
# $1=ROM $2=name $3=system $4=fullname
[ -f "$HOME/.config/deck-cloud/onexit.enabled" ] || exit 0
CLOUD="$HOME/Emulation/tools/launchers/deck-cloud.sh"
[ -x "$CLOUD" ] || exit 0
# Hand the upload to the systemd USER manager, do NOT just detach it here.
# WHY: Steam launches ES-DE through 'reaper', which calls prctl(PR_SET_CHILD_SUBREAPER)
# and then waits until it has NO living descendants of any kind. setsid gives the upload
# its own session and process group, but re-parenting still lands it on the nearest
# subreaper ancestor, which is reaper. So a plain "setsid nohup ... &" kept Steam showing
# ES-DE as "Running" for the WHOLE upload (2-3 min) after ES-DE had already exited, which
# is what the user saw as "ES-DE does not quit cleanly". Under systemd-run the job's
# parent is systemd --user, entirely outside Steam's tree, so reaper is released the
# instant ES-DE exits. Measured on this Deck: 12010 ms held vs 67 ms.
# Must be default service mode - "--scope" keeps the job a child of THIS shell and holds
# reaper exactly like before. --setenv is required because the unit does not inherit our
# environment. The unit name must be unique or a second push while the first is still
# running fails with "unit already loaded" and the backup is silently lost.
# The engine self-registers this run in the transfer-job registry (source=hook), so it
# shows in the panel's Transfers tile and honours the gameplay freeze like any other job.
# The job still gets its own process group, so job_registry.signalable() stays true and
# the panel can still pause/stop it.
if systemd-run --user --collect --quiet \
        --unit="deck-cloud-push-$(date +%s)-$$" \
        --setenv=DECK_CLOUD_JOB_SOURCE=hook \
        "$CLOUD" push-precious >/dev/null 2>&1; then
    exit 0
fi
# Fallback: no user systemd (e.g. run outside a logind session). Old behaviour, so a
# backup is never silently skipped - it just costs the reaper hold again.
DECK_CLOUD_JOB_SOURCE=hook setsid nohup "$CLOUD" push-precious >/dev/null 2>&1 &
exit 0
