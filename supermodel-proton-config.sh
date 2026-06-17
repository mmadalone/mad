#!/usr/bin/env bash
# One-time interactive input configurator for the Windows (Proton) Supermodel.
# Use this to capture Sinden + X-Arcade mappings via raw-input.
# Mappings save to ~/Emulation/emulators/supermodel-win/Config/Supermodel.ini.
set -uo pipefail

. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
SUPERMODEL_DIR="$MAD_DATA_ROOT/emulators/supermodel-win"
PROTON="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-34"
PREFIX="$MAD_DATA_ROOT/wine-prefixes/supermodel"

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$PREFIX"

cd "$SUPERMODEL_DIR" || exit 1

# Console-driven config — Supermodel prints prompts to stdout and reads device events.
exec "$PROTON/proton" run ./supermodel.exe -config-inputs -input-system=rawinput
