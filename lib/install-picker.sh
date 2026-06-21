# shellcheck shell=bash
# lib/install-picker.sh — source me. The interactive component picker (whiptail).
#
# mad_run_picker gathers the optional-component choices and persists them to install.conf
# via lib/install_conf.set_value (so the panel's FORCE_* keys + the file's comments
# survive). Front-end = whiptail (ships in base SteamOS — libnewt, a NetworkManager dep).
# It falls back to writing the choices with NO UI when any of these hold:
#   * $MAD_PICKER_NOUI=1   (install.sh sets this for --express / --dry-run)
#   * no controlling /dev/tty   (the unattended `curl … | bash` one-liner)
#   * whiptail is missing
# Reusable as the `install.sh --reconfigure` UI: defaults are PRE-CHECKED from the
# existing install.conf, so re-running it shows your current choices.
#
# Usage:  mad_run_picker <mad_dir> <standalone:0|1> <suspend:auto|on|off>

mad_run_picker() {
  local mad_dir="$1" standalone="$2" suspend="$3"
  local conf="${MAD_INSTALL_CONF:-$mad_dir/install.conf}"

  # Current value from install.conf (for --reconfigure) or the shipped default.
  _picker_cur() {
    MAD_INSTALL_CONF="$conf" python3 -c \
      "import sys;sys.path.insert(0,'$mad_dir');from lib import install_conf as ic;print(ic.get('$1','$2'))" \
      2>/dev/null || printf '%s' "$2"
  }
  local d_theme d_sinden d_samba
  d_theme=$(_picker_cur INSTALL_THEME 1)
  d_sinden=$(_picker_cur INSTALL_SINDEN 1)
  d_samba=$(_picker_cur INSTALL_SAMBA 0)
  local theme="$d_theme" sinden="$d_sinden" samba="$d_samba"

  if [ "${MAD_PICKER_NOUI:-0}" != 1 ] && [ -e /dev/tty ] && command -v whiptail >/dev/null 2>&1; then
    _picker_state() { [ "$1" = 1 ] && echo ON || echo OFF; }
    local sel
    # whiptail draws on the tty; the RESULT goes to stderr, captured via the 3>&1 1>&2 2>&3
    # fd-swap. </dev/tty gives it a real stdin under `curl|bash`.
    if sel=$(whiptail --title "MAD — choose components" \
        --checklist "Space toggles • Enter confirms • Esc keeps current.\nUnsure? leave the defaults." \
        15 76 3 \
        theme  "pixel-es-de theme + launch screens   (recommended)" "$(_picker_state "$d_theme")" \
        sinden "Sinden lightgun support  (driver + deps)"           "$(_picker_state "$d_sinden")" \
        samba  "Samba network file sharing"                          "$(_picker_state "$d_samba")" \
        3>&1 1>&2 2>&3 </dev/tty); then
      # OK pressed — `sel` is the checked tags (whiptail-quoted, space-separated; empty = all off).
      theme=0; sinden=0; samba=0
      local t
      for t in $sel; do
        t=${t//\"/}
        case "$t" in theme) theme=1 ;; sinden) sinden=1 ;; samba) samba=1 ;; esac
      done
    else
      printf '   (picker cancelled — keeping current selections)\n'
    fi
  fi

  # Persist via the tested, key-preserving, atomic writer.
  MAD_INSTALL_CONF="$conf" python3 - "$mad_dir" "$standalone" "$theme" "$sinden" "$samba" "$suspend" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from lib import install_conf as ic
_, _, standalone, theme, sinden, samba, suspend = sys.argv
for k, v in (("MAD_STANDALONE", standalone), ("INSTALL_THEME", theme),
             ("INSTALL_SINDEN", sinden), ("INSTALL_SAMBA", samba),
             ("INSTALL_SUSPEND", suspend)):
    ic.set_value(k, v)
PY
  local _rc=$?
  # These nested defs leak to global scope in bash — drop them so sourcing install.sh
  # doesn't inherit them.
  unset -f _picker_cur _picker_state 2>/dev/null || true
  return "$_rc"
}
