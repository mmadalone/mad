#!/usr/bin/env bash
# Adjust Sinden Lightgun camera settings (brightness/contrast/exposure) per player.
# Uses zenity for a GUI dialog. Restarts driver after saving.
set -uo pipefail

CFG="$HOME/Lightgun/LightgunMono.exe.config"
[[ -f $CFG ]] || { zenity --error --text "Config file not found: $CFG"; exit 1; }

# Read current values
get_val() {
    grep -oE "<add key=\"$1\" value=\"[^\"]*\"" "$CFG" | head -1 | sed -E 's/.*value="([^"]*)".*/\1/'
}
set_val() {
    sed -i "s|<add key=\"$1\" value=\"[^\"]*\"|<add key=\"$1\" value=\"$2\"|" "$CFG"
}

CUR_BRIGHT=$(get_val CameraBrightness)
CUR_CONTRAST=$(get_val CameraContrast)
CUR_EXPOSURE=$(get_val CameraExposure)
CUR_EXPOSURE_AUTO=$(get_val CameraExposureAuto)
CUR_BRIGHT_P2=$(get_val CameraBrightnessP2)
CUR_CONTRAST_P2=$(get_val CameraContrastP2)
CUR_EXPOSURE_P2=$(get_val CameraExposureP2)
CUR_EXPOSURE_AUTO_P2=$(get_val CameraExposureAutoP2)

# Show form. Same values for both guns is the common case; expose all 8.
NEW=$(zenity --forms --title="Sinden Camera Settings" --width=400 \
    --text="Adjust camera per gun. Brightness 0-255 (default 100). Contrast 0-255 (default 50). Exposure blank=default, 19 low / 79 mid / 159 high. ExposureAuto 1=manual, 3=auto." \
    --separator="|" \
    --add-entry="P1 Brightness (cur=$CUR_BRIGHT)" \
    --add-entry="P1 Contrast (cur=$CUR_CONTRAST)" \
    --add-entry="P1 Exposure (cur=$CUR_EXPOSURE)" \
    --add-entry="P1 ExposureAuto (cur=$CUR_EXPOSURE_AUTO)" \
    --add-entry="P2 Brightness (cur=$CUR_BRIGHT_P2)" \
    --add-entry="P2 Contrast (cur=$CUR_CONTRAST_P2)" \
    --add-entry="P2 Exposure (cur=$CUR_EXPOSURE_P2)" \
    --add-entry="P2 ExposureAuto (cur=$CUR_EXPOSURE_AUTO_P2)" 2>/dev/null)
[[ $? -ne 0 || -z $NEW ]] && exit 0

IFS='|' read -r NB1 NC1 NE1 NEA1 NB2 NC2 NE2 NEA2 <<< "$NEW"
# Only write fields the user filled
[[ -n $NB1 ]]  && set_val CameraBrightness     "$NB1"
[[ -n $NC1 ]]  && set_val CameraContrast       "$NC1"
[[ -n $NE1 ]]  && set_val CameraExposure       "$NE1"
[[ -n $NEA1 ]] && set_val CameraExposureAuto   "$NEA1"
[[ -n $NB2 ]]  && set_val CameraBrightnessP2   "$NB2"
[[ -n $NC2 ]]  && set_val CameraContrastP2     "$NC2"
[[ -n $NE2 ]]  && set_val CameraExposureP2     "$NE2"
[[ -n $NEA2 ]] && set_val CameraExposureAutoP2 "$NEA2"

# Restart driver to apply
"$HOME/Emulation/tools/launchers/sinden-stop.sh" >/dev/null 2>&1
sleep 1
"$HOME/Emulation/tools/launchers/sinden-start.sh" >/dev/null 2>&1
zenity --info --text "Settings saved + driver restarted." --width=300 2>/dev/null
