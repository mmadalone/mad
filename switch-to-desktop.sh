#!/usr/bin/env bash
# Switch SteamOS from Game Mode to the DESKTOP (Plasma) session, then back is via
# the desktop's "Return to Gaming Mode" shortcut. This is the ONE-SHOT desktop
# (next boot returns to Game Mode), matching Steam's own "Switch to Desktop".
#
# NOTE: on this Deck, steamos-session-select does NOT accept the literal 'desktop'
# (it errors on the *) case); the valid one-shot desktop session is 'plasma'
# (= plasma-steamos-oneshot.desktop). It uses pkexec to set the autologin session
# then stops the gamescope session, so gamescope (and ES-DE) tear down and SDDM
# logs into Plasma. Launched detached from ES-DE's Quit menu (the session is killed).
exec steamos-session-select plasma
