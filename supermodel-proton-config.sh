#!/usr/bin/env bash
# One-time interactive input configurator for the Windows (Proton) Supermodel.
# Use this to capture Sinden + X-Arcade mappings via raw-input.
# Mappings save to ~/Emulation/emulators/supermodel-win/Config/Supermodel.ini.
set -uo pipefail

SUPERMODEL_DIR="$HOME/Emulation/emulators/supermodel-win"
PROTON="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-34"
PREFIX="$HOME/Emulation/wine-prefixes/supermodel"

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$PREFIX"

cd "$SUPERMODEL_DIR" || exit 1

# Console-driven config — Supermodel prints prompts to stdout and reads device events.
exec "$PROTON/proton" run ./supermodel.exe -config-inputs -input-system=rawinput
