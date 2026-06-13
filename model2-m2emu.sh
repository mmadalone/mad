#!/bin/sh
# Launch ElSemi's Sega Model 2 Emulator (Windows) via Proton/umu for ES-DE.
#
# Mirrors EmuDeck's model-2-emulator.sh proven invocation, but with EXPLICIT
# paths so it runs standalone from ES-DE (EmuDeck's version depends on vars
# exported by emuDeckModel2.sh and is not safe to call directly).
#
# Called from custom_systems/es_systems.xml via controller-router-wrap.sh, e.g.
#   controller-router-wrap.sh model2 %ROM% "%BASENAME%" "Sega Model 2" -- \
#       model2-m2emu.sh %BASENAME%
#
# Games live in ES-DE's rom dir on the SD card; m2emu finds them via
# EMULATOR.INI [RomDirs] Dir2 (Z:\run\media\deck\1tbDeck\ROMs\model2).
set -eu

M2DIR="$HOME/Emulation/roms/model2"
INI="$M2DIR/EMULATOR.INI"
UMU="$HOME/.local/share/ULWGL/ulwgl-run"

export WINEPREFIX="$M2DIR/pfx"
export GAMEID="ulwgl-model2"
export PROTONPATH="$HOME/.steam/steam/compatibilitytools.d/ULWGL-Proton-8.0-5-3"

GAME="${1:?model2-m2emu.sh: missing rom basename}"

# Gun games draw their own crosshair — hide m2emu's; everything else shows it.
case "$GAME" in
    bel|gunblade|rchase2) sed -i 's/DrawCross=1/DrawCross=0/' "$INI" ;;
    *)                    sed -i 's/DrawCross=0/DrawCross=1/' "$INI" ;;
esac

cd "$M2DIR"   # EXE + wine prefix live here; m2emu always scans <exedir>/roms too
exec "$UMU" ./EMULATOR.EXE "$GAME"
