#!/usr/bin/env bash
# Tune Sinden cursor smoother: alpha + deadzone. Restarts daemon to apply.
CFG="$HOME/Emulation/storage/sinden/smoother.ini"
mkdir -p "$(dirname "$CFG")"
[[ -f $CFG ]] || cat > "$CFG" <<INI
[smoothing]
alpha = 0.12
deadzone = 1.6
INI

CUR_ALPHA=$(awk -F= '/^alpha/{gsub(/^[ ]+|[ ]+$/,"",$2); print $2; exit}' "$CFG")
CUR_DZ=$(awk    -F= '/^deadzone/{gsub(/^[ ]+|[ ]+$/,"",$2); print $2; exit}' "$CFG")

NEW=$(zenity --forms --title="Sinden Smoother Tuning" --width=400 \
    --text="alpha: 0.04-0.60 (lower = more smoothing/more lag, higher = snappier). deadzone: 0.0-6.0 (higher = ignores more jitter)." \
    --separator="|" \
    --add-entry="alpha (cur=$CUR_ALPHA)" \
    --add-entry="deadzone (cur=$CUR_DZ)" 2>/dev/null)
[[ $? -ne 0 || -z $NEW ]] && exit 0

IFS='|' read -r NA ND <<< "$NEW"
[[ -z $NA ]] && NA="$CUR_ALPHA"
[[ -z $ND ]] && ND="$CUR_DZ"
cat > "$CFG" <<INI
[smoothing]
alpha = $NA
deadzone = $ND
INI

# Restart driver to load new env-var values into the LD_PRELOAD shim.
"$HOME/Emulation/tools/launchers/sinden-stop.sh" >/dev/null 2>&1
sleep 1
"$HOME/Emulation/tools/launchers/sinden-start.sh" >/dev/null 2>&1
zenity --info --text "Saved alpha=$NA deadzone=$ND.\nDriver restarted with new values." --width=300 2>/dev/null
