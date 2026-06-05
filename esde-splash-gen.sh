#!/usr/bin/env bash
# Generate ~/ES-DE/resources/graphics/splash.svg per [esde_splash] config, sized
# to the CURRENT screen so the (binary-patched, full-screen) ES-DE splash fills
# edge-to-edge. Called by the ES-DE.AppImage wrapper just before launch.
#
# Modes ([esde_splash].mode): off | fixed_image | random_image
#   - .svg sources are copied as-is (vector; the patch makes ES-DE cover them).
#   - raster sources (png/jpg) are cover-cropped to screen res and embedded as a
#     base64 <image> inside an SVG (ES-DE's LunaSVG renders embedded rasters).
# Pool = ~/ES-DE/splashscreens. random_image honours [esde_splash].images (subset)
# if set, else the whole pool. Best-effort; never blocks launch.
set -uo pipefail
LOCAL="$HOME/Emulation/tools/launchers/controller-policy.local.toml"
POOL="$HOME/ES-DE/splashscreens"
OUT="$HOME/ES-DE/resources/graphics/splash.svg"
LOG="$HOME/Emulation/storage/controller-router/esde-splash.log"
mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")"
log(){ echo "[$(date '+%F %T')] $*" >>"$LOG"; }

# Post-SteamOS-update health check (durable hook — EVERY ES-DE.AppImage wrapper version
# calls this script, and it survives SteamOS + EmuDeck updates). Best-effort, BUILD_ID-
# gated; only nags when components are actually missing. Never blocks/fails the launch.
[ -x "$HOME/Emulation/tools/launchers/esde-health-check.sh" ] \
  && bash "$HOME/Emulation/tools/launchers/esde-health-check.sh" >>"$LOG" 2>&1 || true

cfg=$(python3 - "$LOCAL" <<'PY' 2>/dev/null
import tomllib,sys,pathlib
p=pathlib.Path(sys.argv[1]); d=tomllib.load(open(p,"rb")).get("esde_splash",{}) if p.is_file() else {}
print(d.get("mode","off"))
print(d.get("image",""))
print(d.get("fit","contain"))
print("\n".join(d.get("images") or []))
PY
)
mode=$(sed -n 1p <<<"$cfg"); image=$(sed -n 2p <<<"$cfg"); fit=$(sed -n 3p <<<"$cfg")
mapfile -t subset < <(sed -n '4,$p' <<<"$cfg")
[ -z "$mode" ] && mode=off
[ -z "$fit" ] && fit=contain

export DISPLAY="${DISPLAY:-:0}"
res=$(xrandr 2>/dev/null | awk '/\*/{print $1; exit}'); W=${res%x*}; H=${res#*x}
[[ "$W" =~ ^[0-9]+$ && "$H" =~ ^[0-9]+$ ]] || { W=1280; H=800; }

resolve(){ case "$1" in /*) printf '%s' "$1";; *) printf '%s' "$POOL/$1";; esac; }

pick_random(){
  local -a cand=(); local n
  for n in "${subset[@]:-}"; do [ -n "$n" ] && [ -f "$(resolve "$n")" ] && cand+=("$(resolve "$n")"); done
  if [ "${#cand[@]}" -eq 0 ]; then
    mapfile -t cand < <(find "$POOL" -maxdepth 1 -type f \
      \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.svg' \) \
      ! -name '.*' 2>/dev/null)
  fi
  [ "${#cand[@]}" -gt 0 ] && printf '%s' "${cand[RANDOM % ${#cand[@]}]}"
}

gen(){
  local src="$1"
  [ -f "$src" ] || { log "source missing: $src"; return 1; }
  if [[ "${src,,}" == *.svg ]]; then
    cp -f "$src" "$OUT"; log "svg splash: $src"
  else
    # Fit mode: contain = whole image letterboxed (default; posters/16:9/4:3 fully
    # shown); cover = zoom+crop to fill; tile = repeat a pattern to fill. Output is
    # always exactly WxH so the SVG maps 1:1.
    local sw sh cols rows vf
    if [ "$fit" = tile ]; then
      sw=$(ffprobe -v0 -select_streams v -show_entries stream=width  -of csv=p=0 "$src" 2>/dev/null)
      sh=$(ffprobe -v0 -select_streams v -show_entries stream=height -of csv=p=0 "$src" 2>/dev/null)
      [[ "$sw" =~ ^[0-9]+$ && "$sh" =~ ^[0-9]+$ && "$sw" -gt 0 && "$sh" -gt 0 ]] || { sw=$W; sh=$H; }
      cols=$(( (W + sw - 1) / sw )); rows=$(( (H + sh - 1) / sh ))
      # Guard against a pathologically tiny pattern (e.g. 1px) → millions of tiles
      # would OOM ffmpeg; fall back to contain instead.
      if (( cols > 96 || rows > 96 )); then
        log "tile pattern too small (${sw}x${sh} -> ${cols}x${rows} tiles); using contain"
        fit=contain
      fi
    fi
    if [ "$fit" = tile ]; then
      ffmpeg -y -loop 1 -i "$src" -frames:v "$((cols*rows))" \
        -filter_complex "tile=${cols}x${rows},crop=${W}:${H}" \
        /tmp/_spgen.png >/dev/null 2>&1 || { log "ffmpeg tile failed: $src"; return 1; }
    else
      if [ "$fit" = cover ]; then
        vf="scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}"
      else   # contain (default)
        vf="scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black"
      fi
      ffmpeg -y -i "$src" -vf "$vf" /tmp/_spgen.png >/dev/null 2>&1 \
        || { log "ffmpeg failed: $src"; return 1; }
    fi
    local b64; b64=$(base64 -w0 /tmp/_spgen.png); rm -f /tmp/_spgen.png
    cat > "$OUT" <<EOF2
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="${W}px" height="${H}px" viewBox="0 0 ${W} ${H}"><image x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="xMidYMid slice" xlink:href="data:image/png;base64,${b64}"/></svg>
EOF2
    log "raster splash (${W}x${H}): $src"
  fi
}

case "$mode" in
  fixed_image)  gen "$(resolve "$image")" || true ;;
  random_image) r=$(pick_random); [ -n "$r" ] && gen "$r" || log "random_image: empty pool" ;;
  off|*)        rm -f "$OUT"; log "off (removed override splash)" ;;
esac
exit 0
