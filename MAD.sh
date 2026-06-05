#!/usr/bin/env bash
# MAD — Multi-Pad Arcade Dashboard. Launches the ES-DE-native Deck control panel
# fullscreen. This is the stable launch target referenced by the compiled ES-DE
# fork ("MAD CONTROL PANEL" row in Main Menu → Utilities, GuiMenu.cpp) — keep the
# path (~/Emulation/tools/launchers/MAD.sh) stable across changes.
export ROUTER_GUI_FULLSCREEN=1
exec python3 "$HOME/Emulation/tools/launchers/router-config-gui.py" "$@"
