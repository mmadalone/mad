#!/bin/bash
_MAD_LIB="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/lib" && pwd)" || _MAD_LIB="$HOME/Emulation/tools/launchers/lib"
EMUDECK_ALL="${EMUDECK_FUNCTIONS:-$HOME/.config/EmuDeck/backend/functions/all.sh}"
if [ -f "$EMUDECK_ALL" ]; then . "$EMUDECK_ALL"; else . "$_MAD_LIB/emudeck-shim.sh"; fi
. "$_MAD_LIB/mad-paths.sh"
emulatorInit "flycast"
/usr/bin/flatpak run org.flycast.Flycast "${@}"
cloud_sync_uploadForced
rm -rf "$savesPath/.gaming";