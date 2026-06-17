#!/usr/bin/env bash
# MAD — retired-notice stub.
# The LIVE MAD control panel is the ES-DE-native C++ panel (GuiMadPanel), opened
# from inside ES-DE → Main Menu → Utilities → "MAD CONTROL PANEL", backed by the
# mad-backend.py daemon. The old fullscreen Tk app (router-config-gui.py) was
# retired at parity ("phase 5B") and is NO LONGER launched here — opening it would
# write controller configs that predate type-priority/multitap/transient/rpcs3.
# This stub exists only so the Desktop-mode launcher (router-config.desktop) shows
# a "MAD has moved" notice instead of the stale GUI. Keep this path stable.
TITLE="MAD has moved"
BODY="The MAD control panel now lives inside ES-DE:

Main Menu → Utilities → MAD CONTROL PANEL

(The old Desktop-mode panel was retired.)"

if command -v kdialog >/dev/null 2>&1; then
  exec kdialog --title "$TITLE" --msgbox "$BODY"
elif command -v zenity >/dev/null 2>&1; then
  exec zenity --info --title="$TITLE" --text="$BODY"
elif command -v notify-send >/dev/null 2>&1; then
  exec notify-send "$TITLE" "$BODY"
fi
echo "$TITLE — $BODY"
