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
  || die "ES-DE config (~/ES-DE) not found. MAD ships its OWN patched ES-DE, but it needs the ES-DE config EmuDeck generates — enable the ES-DE frontend in EmuDeck and run it once (that writes ~/ES-DE + the emulator-wired custom_systems/es_systems.xml that MAD wraps)."
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
# game-end: revert the launch-time controller binding for a TRANSIENT standalone so
# the Steam-UI-compatible resting config returns. Every writer-backed standalone the
# user also launches via Steam UI on the go is transient (Switch, PS2, …); add its
# system here as each is migrated. restore_all() is sidecar-gated (no-op otherwise).
# $1=ROM $2=name $3=system $4=fullname.
case "$3" in switch|ps2|xbox|ps3) ;; *) exit 0 ;; esac
LOG="$HOME/Emulation/storage/controller-router/router.log"; mkdir -p "$(dirname "$LOG")"
exec "$HOME/Emulation/tools/launchers/mad-standalone-launch.py" --restore-all >>"$LOG" 2>&1
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
S = "/home/deck/Emulation/tools/launchers/mad-standalone-launch.py"
t = f.read_text(encoding="utf-8")
def wrap(text, label, emu):
    pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!%s)(.*?)(</command>)'
                     % (re.escape(label), re.escape(W)))
    return pat.sub(lambda m: f'{m.group(1)}{W} {emu} %ROM% -- {m.group(2)}{m.group(3)}', text)
def rewrap(text, label, emu):
    # Migrated standalone: replace the command (possibly controller-router-wrap.sh-
    # wrapped) with the mad-standalone-launch.py launch binder. Idempotent.
    pat = re.compile(r'(<command label="%s \(Standalone\)">)(?!\s*%s)(.*?)(</command>)'
                     % (re.escape(label), re.escape(S)), re.S)
    def sub(m):
        inner = m.group(2).strip()
        mm = re.match(r'\S*controller-router-wrap\.sh\s+\S+\s+%ROM%\s+"[^"]*"\s+"[^"]*"\s+--\s+(.*)',
                      inner, re.S)
        real = (mm.group(1) if mm else inner).strip()
        return f'{m.group(1)} {S} {emu} %ROM% -- {real} {m.group(3)}'
    return pat.sub(sub, text)
def inject_xbox(text):
    # xbox is bundled-only by default — add a wrapped <system> if entirely absent.
    if "<name>xbox</name>" in text:
        return text
    block = (
        '    <system>\n        <name>xbox</name>\n'
        '        <fullname>Microsoft Xbox</fullname>\n'
        '        <path>%ROMPATH%/xbox</path>\n'
        '        <extension>.iso .ISO .xiso .XISO</extension>\n'
        f'        <command label="xemu (Standalone)">{S} xemu %ROM% -- '
        '%INJECT%=%BASENAME%.esprefix %EMULATOR_XEMU% -dvd_path %ROM%</command>\n'
        '        <platform>xbox</platform>\n        <theme>xbox</theme>\n    </system>\n')
    return text.replace("</systemList>", block + "</systemList>", 1)
t2 = wrap(wrap(t, "Ryujinx", "ryujinx"), "Eden", "eden")
t2 = rewrap(t2, "PCSX2", "pcsx2")   # ps2 → Standalones launch binder (router_skip in policy)
t2 = inject_xbox(t2)                # xbox: add if absent (bundled-only by default)
t2 = rewrap(t2, "xemu", "xemu")     # then ensure its xemu command is wrapped
t2 = rewrap(t2, "RPCS3", "rpcs3")   # ps3 → Standalones launch binder (router_skip in policy)
if t2 != t:
    f.write_text(t2, encoding="utf-8")
PY
fi

# ---- 5b. MAD theme (pixel-es-de) — the C++ panel reads its icons/colours from it ----
say "Installing the MAD theme (pixel-es-de)"
THEME_REPO="https://github.com/mmadalone/pixel-es-de.git"
ESDE_HOME="$HOME/ES-DE"; [ -d "$ESDE_HOME" ] || ESDE_HOME="$HOME/.config/ES-DE"
THEME_DIR="$ESDE_HOME/themes/pixel-es-de"
run mkdir -p "$ESDE_HOME/themes"
if [ -d "$THEME_DIR/.git" ] && git -C "$THEME_DIR" remote get-url origin 2>/dev/null | grep -q 'mmadalone/pixel-es-de'; then
  if [ -n "$(git -C "$THEME_DIR" status --porcelain 2>/dev/null)" ]; then
    warn "theme clone has local changes — leaving it as-is (not pulling)"
  else
    run git -C "$THEME_DIR" pull --ff-only && ok "updated existing theme clone"
  fi
elif [ -e "$THEME_DIR" ]; then
  TBK="$ESDE_HOME/themes/_TMP-pixel-es-de-$(date +%Y%m%d-%H%M%S)"
  warn "$THEME_DIR exists but isn't our repo — backing it up to $TBK"
  run mv "$THEME_DIR" "$TBK"
  run git clone --depth 1 "$THEME_REPO" "$THEME_DIR" && ok "cloned theme" \
    || warn "theme clone failed — MAD will be un-themed until it's installed"
else
  run git clone --depth 1 "$THEME_REPO" "$THEME_DIR" && ok "cloned theme" \
    || warn "theme clone failed — MAD will be un-themed until it's installed"
fi
# Select it in es_settings.xml — ONLY if ES-DE isn't running (it rewrites that
# file on exit and would clobber the change, CLAUDE rule #3). Back it up first.
ESET="$ESDE_HOME/settings/es_settings.xml"
if python3 -c "import sys; sys.path.insert(0,'$MAD_DIR'); from lib.proc_guard import esde_running; sys.exit(0 if esde_running() else 1)" 2>/dev/null; then
  warn "ES-DE is running — NOT editing es_settings.xml; set the theme to 'pixel-es-de' in ES-DE -> Menu -> UI Settings"
elif [ ! -d "$THEME_DIR" ]; then
  : # clone failed (warned above)
elif [ ! -f "$ESET" ]; then
  warn "es_settings.xml not present yet — launch ES-DE once, then set Theme=pixel-es-de"
elif [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] back up es_settings.xml + set Theme=pixel-es-de\n'
else
  cp -f "$ESET" "$ESET.bak-$(date +%Y%m%d-%H%M%S)"
  ESDE_APPDATA_DIR="$ESDE_HOME" python3 -c "import sys; sys.path.insert(0,'$MAD_DIR'); from lib import esde_settings; esde_settings.set_value('Theme','pixel-es-de')" \
    && ok "theme selected (Theme=pixel-es-de)" \
    || warn "couldn't set the theme — set it in ES-DE -> Menu -> UI Settings"
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

# seed sinden.conf (optional Sinden/HA LED config) from the example — never clobber a live one
if [ -f "$MAD_DIR/sinden.conf" ]; then
  ok "sinden.conf already present — leaving it untouched"
elif [ -f "$MAD_DIR/sinden.example.conf" ]; then
  run cp "$MAD_DIR/sinden.example.conf" "$MAD_DIR/sinden.conf" \
    && ok "seeded sinden.conf from the example (edit it only if you want the HA LED strip)"
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
  [ -d "${THEME_DIR:-$HOME/ES-DE/themes/pixel-es-de}" ] && ok "MAD theme (pixel-es-de)" || warn "MAD theme not installed — MAD will be un-themed/icon-less"
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
   - The MAD theme (pixel-es-de) was installed + selected automatically — MAD's
     panel icons/colours come from it; keep a router-config theme active.
   - Lightguns / Samba / etc. are extra features — see the README.
   - After any SteamOS update, run:  $MAD_DIR/deck-post-update.sh

$(c '1;36')===========================================================$(c 0)
EOF
