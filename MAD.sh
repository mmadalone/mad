#!/usr/bin/env bash
# MAD — Multi-Pad Arcade Dashboard. Launches the ES-DE-native Deck control panel
# fullscreen. This is the stable launch target referenced by the compiled ES-DE
# fork ("MAD CONTROL PANEL" row in Main Menu → Utilities, GuiMenu.cpp) — keep the
# path (~/Emulation/tools/launchers/MAD.sh) stable across changes.
export ROUTER_GUI_FULLSCREEN=1
# Crash diagnosis: capture MAD's stderr (Xlib "X Error …"/"Fatal IO error", Tcl panics, and the
# faulthandler dump) — ES-DE discards it otherwise, which is why C-level segfaults left no trace.
# A fresh file per launch (with a timestamp header) keeps it to the current session.
export PYTHONFAULTHANDLER=1
_mad_err="$HOME/Emulation/storage/controller-router/mad-stderr.log"
# If the log dir can't be created, fall back to /dev/null so the stderr redirect below
# can't fail and block MAD from launching — the control panel must always open.
mkdir -p "$(dirname "$_mad_err")" 2>/dev/null || _mad_err=/dev/null
{ echo "==== $(date '+%F %T') MAD launch ===="; } > "$_mad_err" 2>/dev/null
exec python3 "$HOME/Emulation/tools/launchers/router-config-gui.py" "$@" 2>> "$_mad_err"
