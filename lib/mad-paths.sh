# shellcheck shell=bash
# lib/mad-paths.sh — source me. Shell twin of lib/mad_paths.py.
#
# Exports MAD's mutable data roots so shell tools don't hardcode ~/Emulation/...
# Precedence (matches mad_paths.py):
#   1. $MAD_DATA_ROOT             — explicit override
#   2. $storagePath's parent      — follow a relocated EmuDeck storage if exported
#   3. $HOME/Emulation            — the standard default (unchanged)
# No-op-safe to re-source (uses := so a set value is never overwritten).

: "${MAD_DATA_ROOT:=${storagePath:+${storagePath%/storage}}}"   # EmuDeck storagePath, if set
: "${MAD_DATA_ROOT:=$HOME/Emulation}"                           # else legacy default
export MAD_DATA_ROOT
export storageRoot="$MAD_DATA_ROOT/storage"
export romsRoot="$MAD_DATA_ROOT/roms"
export toolsRoot="$MAD_DATA_ROOT/tools"
export savesRoot="$MAD_DATA_ROOT/saves"
export biosRoot="$MAD_DATA_ROOT/bios"
