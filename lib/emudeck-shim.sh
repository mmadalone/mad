# shellcheck shell=bash
# lib/emudeck-shim.sh — minimal stand-in for EmuDeck's all.sh, sourced by MAD's
# emulator launchers ONLY when EmuDeck's backend is absent (standalone installs).
#
# Provides exactly what the 22 launchers consume: the path vars + emulatorInit,
# cloud_sync_uploadForced, scriptConfigFileGetVar. No git pull, no cloud sync, no
# network — unlike EmuDeck's real emulatorInit, which does `git reset --hard &&
# git pull` plus cloud sync on EVERY launch. When EmuDeck IS present the launcher
# sources its real all.sh and never reaches this file, so existing installs are
# byte-for-byte unaffected.

# Roots from the sibling resolver (honors $MAD_DATA_ROOT / $storagePath / default).
. "${BASH_SOURCE[0]%/*}/mad-paths.sh"

# EmuDeck-style path vars the launchers read (mirror settings.sh / vars.sh),
# derived from MAD_DATA_ROOT. := never clobbers a value already in the env.
: "${emusFolder:=$HOME/Applications}"          # vars.sh appFolder — ES-DE finds emus here
: "${emulationPath:=$MAD_DATA_ROOT}"
: "${romsPath:=$romsRoot}"
: "${toolsPath:=$toolsRoot}"
: "${savesPath:=$savesRoot}"
: "${biosPath:=$biosRoot}"
: "${storagePath:=$storageRoot}"
: "${emudeckFolder:=$HOME/.config/EmuDeck}"
export emusFolder emulationPath romsPath toolsPath savesPath biosPath storagePath emudeckFolder

# Functions the launchers call — no-op stand-ins (the real ones do cloud/git/netplay).
emulatorInit() { :; }
cloud_sync_uploadForced() { :; }
cloud_sync_downloadEmu() { :; }
cloud_sync_startService() { :; }
cloud_sync_stopService() { :; }

# Verbatim from EmuDeck helperFunctions.sh:890 (launchers read FORCED_PROTON_VER
# from their per-emulator .config via this helper).
scriptConfigFileGetVar() {
	local configFile=$1
	local configVar=$2
	local configVarDefaultValue=$3

	local configVarValue="$( (grep -E "^${configVar}=" -m 1 "${configFile}" 2>/dev/null || echo "_=__UNDEFINED__") | head -n 1 | cut -d '=' -f 2- | xargs )"
	if [ "${configVarValue}" = "__UNDEFINED__" ]; then
		configVarValue="${configVarDefaultValue}"
	fi

	printf -- "%s" "${configVarValue}"
}
