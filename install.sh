#!/usr/bin/env bash
# ============================================================================
# install.sh — one-shot installer for ES-DE + MAD on a Steam Deck.
#
#   curl -fsSL https://raw.githubusercontent.com/mmadalone/mad/main/install.sh | bash
#   # or from a clone:   ./install.sh [--dry-run]
#
# Orchestrates the repo's existing idempotent scripts (deck-fetch-esde.sh,
# deck-post-update.sh) — it does NOT reinvent them. Safe to re-run; never
# clobbers a live controller-policy.local.toml; backs up existing hooks.
#
# It automates everything that CAN be scripted and prints a short checklist for
# the two steps that genuinely can't (adding ES-DE to Steam + Steam Input OFF).
#
# Prereqs: SteamOS + EmuDeck + ES-DE already working.
#   --dry-run   show every action without changing anything.
# ============================================================================
set -uo pipefail

REPO_URL="https://github.com/mmadalone/mad.git"
MAD_DIR="$HOME/Emulation/tools/launchers"
DRY_RUN=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '3,16p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s (try --help)\n' "$a" >&2; exit 2 ;;
  esac
done

c(){ printf '\033[%sm' "$1"; }
say(){  printf '\n%s==>%s %s\n' "$(c '1;36')" "$(c 0)" "$*"; }
ok(){   printf '   %s\xe2\x9c\x93%s %s\n' "$(c '1;32')" "$(c 0)" "$*"; }
warn(){ printf '   %s!%s %s\n'  "$(c '1;33')" "$(c 0)" "$*"; }
die(){  printf '\n%s\xe2\x9c\x97 %s%s\n' "$(c '1;31')" "$*" "$(c 0)" >&2; exit 1; }
run(){  if [ "$DRY_RUN" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else "$@"; fi; }

[ "$DRY_RUN" = 1 ] && say "DRY RUN — showing actions, changing nothing."

# ---- 1. tool guards ----
say "Checking prerequisites"
for b in bash git python3 curl; do command -v "$b" >/dev/null || die "'$b' is required but not found."; done
grep -qiE 'steamos|holo' /etc/os-release 2>/dev/null \
  || warn "this doesn't look like SteamOS — continuing, but MAD targets the Steam Deck"
ok "git / python3 / curl present"

# ---- 2. EmuDeck + ES-DE prereq ----
[ -d "$HOME/Emulation" ] || die "~/Emulation not found — set up EmuDeck first (https://www.emudeck.com)."
{ [ -d "$HOME/ES-DE" ] || [ -d "$HOME/.config/ES-DE" ]; } \
  || die "ES-DE config (~/ES-DE) not found — install + run ES-DE (via EmuDeck) once first."
run mkdir -p "$HOME/Applications"
ok "EmuDeck + ES-DE detected"

# ---- 3. deploy MAD tools ----
say "Deploying MAD tools -> $MAD_DIR"
if [ -d "$MAD_DIR/.git" ] && git -C "$MAD_DIR" remote get-url origin 2>/dev/null | grep -q 'mmadalone/mad'; then
  if [ -n "$(git -C "$MAD_DIR" status --porcelain 2>/dev/null)" ]; then
    warn "existing clone has local changes — leaving it as-is (not pulling)"
  else
    run git -C "$MAD_DIR" pull --ff-only && ok "updated existing clone"
  fi
elif [ -e "$MAD_DIR" ]; then
  BK="$HOME/Emulation/tools/_TMP-launchers-$(date +%Y%m%d-%H%M%S)"
  warn "$MAD_DIR exists but isn't our repo — backing it up to $BK"
  run mkdir -p "$(dirname "$MAD_DIR")"
  run mv "$MAD_DIR" "$BK"
  run git clone --branch main "$REPO_URL" "$MAD_DIR" && ok "cloned" || die "git clone failed"
else
  run mkdir -p "$(dirname "$MAD_DIR")"
  run git clone --branch main "$REPO_URL" "$MAD_DIR" && ok "cloned" || die "git clone failed"
fi

# ---- 4. patched ES-DE AppImage + launch wrapper ----
say "Installing the patched ES-DE AppImage (CI release)"
if run bash "$MAD_DIR/deck-fetch-esde.sh"; then
  run bash "$MAD_DIR/deck-post-update.sh" --wrapper && ok "AppImage + extracted-AppDir wrapper installed"
else
  warn "AppImage download failed — build it locally later (see README 'Getting the ES-DE AppImage')"
fi

# ---- 5. ES-DE controller-router hooks (templated to \$HOME) ----
say "Installing ES-DE game-start/-end hooks"
HS="$HOME/ES-DE/scripts/game-start"; HE="$HOME/ES-DE/scripts/game-end"
run mkdir -p "$HS" "$HE"
for f in "$HS/04-controller-router-setup.sh" "$HS/05-controller-router-standalone.sh" \
         "$HE/00-controller-router.sh" "$HE/06-mad-switch-restore.sh"; do
  [ -e "$f" ] && run cp -f "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)"
done
if [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] write 4 hooks: game-start/04,05 + game-end/00,06 (chmod +x)\n'
else
  cat > "$HS/04-controller-router-setup.sh" <<'HOOK'
#!/usr/bin/env bash
# game-start: run controller-router SETUP for RetroArch systems NOT launched through
# controller-router-wrap.sh (ES-DE-bundled es_systems). $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"; mkdir -p "$(dirname "$LOG")"
RT="$HOME/Emulation/tools/launchers"; SYSTEM="$3"
cmd=$(python3 -c "import sys; sys.path.insert(0,'$RT'); from lib import es_systems; print(es_systems.default_command(sys.argv[1]))" "$SYSTEM" 2>/dev/null)
case "$cmd" in *controller-router-wrap.sh*) exit 0 ;; esac
echo "[$(date +%H:%M:%S)] router-setup hook (unwrapped RA): system='$SYSTEM'" >> "$LOG"
"$RT/controller-router.py" setup "$1" "$2" "$3" "$4" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: setup returned non-zero (launch continues)" >> "$LOG"
exit 0
HOOK
  cat > "$HS/05-controller-router-standalone.sh" <<'HOOK'
#!/usr/bin/env bash
# game-start: route controllers for STANDALONE emulators (Cemu/Dolphin/PCSX2/...). The
# router self-filters (returns 0 for RetroArch/backend-less/router_skip systems).
# $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/sinden/logs/es-de-hooks.log"; mkdir -p "$(dirname "$LOG")"
echo "[$(date +%H:%M:%S)] controller-router-standalone hook: system='$3'" >> "$LOG"
"$HOME/Emulation/tools/launchers/controller-router.py" standalone "$1" "$2" "$3" "$4" >> "$LOG" 2>&1 \
  || echo "[$(date +%H:%M:%S)]   WARN: standalone routing returned non-zero (launch continues)" >> "$LOG"
exit 0
HOOK
  cat > "$HE/00-controller-router.sh" <<'HOOK'
#!/usr/bin/env bash
# game-end: strip the controller-router's per-game sentinel block so the next launch
# starts clean. $1=ROM $2=name $3=system $4=fullname
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
exec "$HOME/Emulation/tools/launchers/controller-router.py" cleanup "$1" "$2" "$3" "$4" >>"$LOG" 2>&1
HOOK
  cat > "$HE/06-mad-switch-restore.sh" <<'HOOK'
#!/usr/bin/env bash
# game-end: revert the launch-time Switch controller binding (Ryujinx/Eden) so the
# on-the-go (Steam-direct) default returns. Runs after the game exits, however it
# died. $1=ROM $2=name $3=system $4=fullname
[ "$3" = "switch" ] || exit 0
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
exec "$HOME/Emulation/tools/launchers/mad-switch-launch.py" --restore-all >>"$LOG" 2>&1
HOOK
  chmod +x "$HS/04-controller-router-setup.sh" "$HS/05-controller-router-standalone.sh" \
           "$HE/00-controller-router.sh" "$HE/06-mad-switch-restore.sh"
fi
ok "hooks installed"
# Wrap the Switch Ryujinx/Eden <command>s with mad-switch-launch.py (launch-time
# controller routing) — idempotent; no-op if es_systems.xml is absent or wrapped.
if [ "$DRY_RUN" != 1 ]; then
  python3 - <<'PY' 2>/dev/null && ok "Switch commands wrapped for launch-time routing" || true
import re, sys
from pathlib import Path
f = Path.home() / "ES-DE/custom_systems/es_systems.xml"
if not f.is_file():
    sys.exit(1)
W = "/home/deck/Emulation/tools/launchers/mad-switch-launch.py"
t = f.read_text(encoding="utf-8")
def wrap(text, label, emu):
    pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!%s)(.*?)(</command>)'
                     % (re.escape(label), re.escape(W)))
    return pat.sub(lambda m: f'{m.group(1)}{W} {emu} %ROM% -- {m.group(2)}{m.group(3)}', text)
t2 = wrap(wrap(t, "Ryujinx", "ryujinx"), "Eden", "eden")
if t2 != t:
    f.write_text(t2, encoding="utf-8")
PY
fi

# ---- 6. default controller policy (never clobber a live one) ----
say "Controller policy"
if [ -f "$MAD_DIR/controller-policy.local.toml" ]; then
  ok "controller-policy.local.toml already present — leaving it untouched"
elif [ -f "$MAD_DIR/controller-policy.example.toml" ]; then
  run cp "$MAD_DIR/controller-policy.example.toml" "$MAD_DIR/controller-policy.local.toml" \
    && ok "seeded controller-policy.local.toml from the example (edit via the GUI)"
else
  warn "no controller-policy.example.toml in the repo — configure controllers in the GUI"
fi

# ---- 7. core system deps: python tk+evdev (pacman), input group ----
say "System dependencies"
if python3 -c 'import tkinter, evdev' 2>/dev/null; then
  ok "python tkinter + evdev present"
elif [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] sudo steamos-readonly disable; pacman -Sy --needed --noconfirm python-evdev tk; readonly enable\n'
else
  warn "installing python-evdev + tk (pacman — SteamOS's root is wiped by updates)"
  sudo steamos-readonly disable 2>/dev/null || true
  sudo pacman-key --init >/dev/null 2>&1 || true
  sudo pacman-key --populate archlinux holo >/dev/null 2>&1 || true
  sudo pacman -Sy --needed --noconfirm python-evdev tk \
    && ok "installed python-evdev + tk" || warn "pacman failed — re-run, or check the keyring"
  sudo steamos-readonly enable 2>/dev/null || true
fi
if groups 2>/dev/null | grep -qw input; then
  ok "'input' group OK"
else
  run sudo usermod -aG input "$USER" && warn "added '$USER' to 'input' — LOG OUT/IN for it to take effect"
fi

# ---- 8. verify (only the bits a CORE install sets up) ----
if [ "$DRY_RUN" = 0 ]; then
  say "Verifying"
  { [ -x "$HOME/Applications/ES-DE-MAD.AppImage" ] && grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; } \
    && ok "ES-DE-MAD AppImage + wrapper" || warn "ES-DE AppImage/wrapper not in place"
  H_OK=1; for f in "$HS/04-controller-router-setup.sh" "$HS/05-controller-router-standalone.sh" "$HE/00-controller-router.sh"; do [ -x "$f" ] || H_OK=0; done
  [ "$H_OK" = 1 ] && ok "ES-DE hooks" || warn "one or more hooks missing"
  python3 -c 'import tkinter, evdev' 2>/dev/null && ok "python deps" || warn "python tkinter/evdev still missing"
  [ -r "$MAD_DIR/controller-router.py" ] && ok "MAD tools present" || warn "MAD tools missing"
fi

# ---- 9. the two manual steps + notes ----
cat <<EOF

$(c '1;36')=============== ALMOST DONE — 2 manual steps ===============$(c 0)

$(c '1;33')1) Add ES-DE to Steam, then turn Steam Input OFF$(c 0)
   - Game Mode -> Library -> "Add a Non-Steam Game" -> browse to:
        $HOME/Applications/ES-DE.AppImage
   - Right-click it -> Properties -> Controller -> set the controller
     configuration / "Steam Input" to OFF.
     (MAD's router needs raw evdev; Steam Input must be off.)

$(c '1;33')2) Launch ES-DE and set up your controllers$(c 0)
   - Open ES-DE from Steam -> Main Menu -> Utilities -> "MAD CONTROL PANEL".
   - Identify your pads on the Players / Priority pages (X-Arcade port, etc.).

$(c '1;32')Notes$(c 0)
   - Steam-overlay input is handled NATIVELY by the patched ES-DE. The
     "PauseGames" Decky plugin is OPTIONAL now — only add it if you also want
     the few game-context overlay spots (home/notes/guide/resume) covered.
   - Lightguns / Samba / etc. are extra features — see the README.
   - After any SteamOS update, run:  $MAD_DIR/deck-post-update.sh

$(c '1;36')===========================================================$(c 0)
EOF
