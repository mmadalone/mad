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
# Prereqs: SteamOS + ES-DE config. EmuDeck recommended (it installs the emulators).
# Without EmuDeck, MAD runs STANDALONE: it seeds the ES-DE config itself; you provide
# the emulators (flatpak / AppImages / EmuDeck) and put ROMs in ~/ROMs/<system>.
#   --dry-run     show every action without changing anything.
#   --standalone  force standalone mode (ignore EmuDeck even if it's installed).
#   --express     accept the defaults — skip the interactive component picker.
#   --reconfigure re-run the component picker on an existing install, then re-apply.
# ============================================================================
set -uo pipefail

REPO_URL="https://github.com/mmadalone/mad.git"
MAD_DIR="$HOME/Emulation/tools/launchers"
DRY_RUN=0
FORCE_STANDALONE=0
EXPRESS=0
RECONFIGURE=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --standalone) FORCE_STANDALONE=1 ;;
    --express) EXPRESS=1 ;;
    --reconfigure) RECONFIGURE=1 ;;
    -h|--help) sed -n '3,21p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# ---- 2. EmuDeck / ES-DE detection (EmuDeck is OPTIONAL) ----
say "Detecting EmuDeck / ES-DE"
ESDE_HOME="$HOME/ES-DE"; [ -d "$ESDE_HOME" ] || { [ -d "$HOME/.config/ES-DE" ] && ESDE_HOME="$HOME/.config/ES-DE"; }
MAD_STANDALONE=0
if [ "$FORCE_STANDALONE" = 1 ]; then
  MAD_STANDALONE=1
  warn "--standalone given — ignoring EmuDeck even if present."
elif [ -d "$HOME/Emulation" ] && { [ -d "$HOME/ES-DE" ] || [ -d "$HOME/.config/ES-DE" ]; }; then
  ok "EmuDeck / ES-DE config detected — using it as-is"
else
  warn "EmuDeck / ES-DE config not found."
  printf '   MAD can run STANDALONE: it ships its own patched ES-DE and seeds the config\n'
  printf '   itself. You provide the emulators (EmuDeck, flatpak, or AppImages in\n'
  printf '   ~/Applications) and put ROMs in ~/ROMs/<system>. EmuDeck is the easy way to\n'
  printf '   install the emulators (https://www.emudeck.com) — install it first and re-run\n'
  printf '   if you prefer the full setup.\n'
  if [ "$DRY_RUN" = 1 ]; then
    MAD_STANDALONE=1; warn "[dry-run] would prompt to continue standalone; assuming yes."
  elif [ -e /dev/tty ]; then
    printf '   Continue standalone? [Y/n] '
    read -r _ans </dev/tty 2>/dev/null || _ans=y
    case "${_ans:-y}" in [Nn]*) die "Aborted — install EmuDeck (or set up ES-DE), then re-run." ;; esac
    MAD_STANDALONE=1
  else
    MAD_STANDALONE=1; warn "non-interactive — proceeding STANDALONE (re-run with EmuDeck for the full setup)."
  fi
fi
[ "$MAD_STANDALONE" = 1 ] && ESDE_HOME="$HOME/ES-DE"   # standalone always uses ~/ES-DE
run mkdir -p "$HOME/Applications"

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

# ---- 3b. component picker -> install.conf (the single source of truth) ----
# Runs AFTER the clone (lib/ exists now) and BEFORE the optional steps it gates. A FIRST
# install shows the picker; a re-run silently reuses install.conf unless --reconfigure;
# --express always takes the defaults with no UI.
say "Components"
if [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] component picker -> install.conf (defaults: theme=on sinden=on samba=off)\n'
else
  # shellcheck source=lib/install-picker.sh
  . "$MAD_DIR/lib/install-picker.sh"
  if [ "$EXPRESS" = 1 ] || { [ -f "$MAD_DIR/install.conf" ] && [ "$RECONFIGURE" != 1 ]; }; then
    export MAD_PICKER_NOUI=1
  fi
  mad_run_picker "$MAD_DIR" "$MAD_STANDALONE" auto \
    && ok "component choices saved (install.conf)" \
    || warn "picker had trouble — proceeding with defaults"
fi
# Load the choices so the want() gates below see them. Define a fallback want() FIRST so the
# gates still work if the clone hasn't happened yet (e.g. --dry-run on a fresh machine where
# $MAD_DIR doesn't exist): default = "yes" (do everything). lib/install-conf.sh overrides it
# with the install.conf-aware version when present.
want() { return 0; }
# shellcheck source=lib/install-conf.sh
[ -f "$MAD_DIR/lib/install-conf.sh" ] && . "$MAD_DIR/lib/install-conf.sh"

# ---- 4. patched ES-DE AppImage + launch wrapper ----
say "Installing the patched ES-DE AppImage (CI release)"
if run bash "$MAD_DIR/deck-fetch-esde.sh"; then
  run bash "$MAD_DIR/deck-post-update.sh" --wrapper && ok "AppImage + extracted-AppDir wrapper installed"
  # Standalone seeds custom_systems from the bundled es_systems.xml, which lives inside
  # the AppDir — extract it NOW (the wrapper otherwise extracts lazily on first launch,
  # too late for the seed step below). Best-effort; the seed step warns if it's missing.
  [ "$MAD_STANDALONE" = 1 ] && { run bash "$MAD_DIR/deck-post-update.sh" --extract \
    && ok "ES-DE resources extracted (for standalone seeding)" || warn "couldn't pre-extract the AppDir"; }
else
  warn "AppImage download failed (no network / GitHub release unreachable)."
  warn "Re-run 'bash $MAD_DIR/deck-fetch-esde.sh' once online — ES-DE won't launch until it's installed."
fi

# ---- 4b. [standalone] seed the ES-DE config skeleton EmuDeck would have created ----
if [ "$MAD_STANDALONE" = 1 ]; then
  say "Seeding ES-DE config (standalone)"
  run mkdir -p "$ESDE_HOME/settings" "$ESDE_HOME/custom_systems" "$ESDE_HOME/themes" \
               "$ESDE_HOME/gamelists" "$ESDE_HOME/controllers" \
               "$ESDE_HOME/scripts/game-start" "$ESDE_HOME/scripts/game-end" "$HOME/ROMs"
  ESET="$ESDE_HOME/settings/es_settings.xml"
  if [ -f "$ESET" ]; then
    ok "es_settings.xml already present — leaving it"
  elif [ "$DRY_RUN" = 1 ]; then
    printf '   [dry-run] seed minimal es_settings.xml (Theme=pixel-es-de, ROMDirectory="")\n'
  else
    if python3 - "$MAD_DIR" "$ESET" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lib import fsutil
fsutil.atomic_write_text(Path(sys.argv[2]),
    '<?xml version="1.0"?>\n'
    '<string name="Theme" value="pixel-es-de" />\n'
    '<string name="ROMDirectory" value="" />\n')
PY
    then ok "seeded minimal es_settings.xml (Theme=pixel-es-de, ROMs in ~/ROMs)"
    else warn "couldn't seed es_settings.xml — set Theme=pixel-es-de in ES-DE later"
    fi
  fi
fi

# ---- 5. ES-DE controller-router hooks (templated to \$HOME) ----
say "Installing ES-DE game-start/-end hooks"
HS="$HOME/ES-DE/scripts/game-start"; HE="$HOME/ES-DE/scripts/game-end"
run mkdir -p "$HS" "$HE"

# ES-DE executes EVERY file in its scripts/ dirs regardless of extension, so a .bak
# dropped IN-PLACE runs as a DUPLICATE hook (the recurring double-run). Keep hook
# backups OUTSIDE the scanned tree: one timestamped dir per install run under
# ~/Downloads/_TMP, mirroring the game-start/ game-end/ substructure so two same-named
# hooks never collide. backup_hook() is reused by deploy_hook() (section 5a) too.
SCRIPTS_DIR="$HOME/ES-DE/scripts"
HOOK_BAK_ROOT="$HOME/Downloads/_TMP/esde-hooks-backup-$(date +%Y%m%d-%H%M%S)"
_bak_root_ready=0
ensure_bak_root() {            # create the backup root + a RECOVERY note, once per run
  [ "$_bak_root_ready" = 1 ] && return 0
  _bak_root_ready=1
  run mkdir -p "$HOOK_BAK_ROOT"
  [ "$DRY_RUN" = 1 ] && return 0
  cat > "$HOOK_BAK_ROOT/RECOVERY.txt" <<EOF
ES-DE hook backups made by install.sh on $(date).
ES-DE runs EVERY file in $SCRIPTS_DIR/{game-start,game-end}, so replaced or stale hooks
are kept HERE (outside that scanned tree) instead of as in-place .bak files that would
run a second time. To restore one, copy it back to the matching subfolder under
$SCRIPTS_DIR. Anything under _stale/ was an old in-place .bak swept out of the tree.
EOF
}
backup_hook() {               # backup_hook <abs path of a live hook under $SCRIPTS_DIR>
  local f="$1" rel dst
  [ -e "$f" ] || return 0
  ensure_bak_root
  rel="${f#"$SCRIPTS_DIR"/}"
  dst="$HOOK_BAK_ROOT/$(dirname "$rel")"
  run mkdir -p "$dst"
  run cp -f "$f" "$dst/$(basename "$f")"
}

# One-time sweep: relocate any stale in-place .bak-* left by earlier installs (they were
# running as duplicate hooks) out to the same backup root, under _stale/.
_swept=0
shopt -s nullglob
for _b in "$SCRIPTS_DIR"/*/*.bak-* "$SCRIPTS_DIR"/*.bak-*; do
  [ -e "$_b" ] || continue
  ensure_bak_root
  _rel="${_b#"$SCRIPTS_DIR"/}"
  _d="$HOOK_BAK_ROOT/_stale/$(dirname "$_rel")"
  run mkdir -p "$_d"
  run mv -f "$_b" "$_d/$(basename "$_b")"
  _swept=$((_swept + 1))
done
shopt -u nullglob
# Real runs report the completed sweep; --dry-run only moved nothing, so report it as a
# preview (the per-file "[dry-run] mv ..." lines above already show each one).
if [ "$_swept" -gt 0 ]; then
  if [ "$DRY_RUN" = 1 ]; then
    printf '   [dry-run] would sweep %s stale in-place .bak hook(s) -> %s/_stale\n' "$_swept" "$HOOK_BAK_ROOT"
  else
    ok "swept $_swept stale in-place .bak hook(s) -> $HOOK_BAK_ROOT/_stale"
  fi
fi

# The controller-router + Lindbergh game-start/-end hooks are deployed just below via
# deploy_hook, from their masters in hooks/ (same mechanism + backup + cmp-skip as every
# other hook). They used to be written here as inline heredocs with no hooks/ master.

# ---- 5a. activation hooks (launch-screens / Sinden+Wii / quit-combo) ----
# Thin game-start/end wrappers that ACTIVATE features whose logic/engine scripts are
# already deployed; without them the MAD panel pages exist but nothing fires at launch.
# Shipped from $MAD_DIR/hooks/, gated per feature. Each no-ops gracefully without its
# hardware / collection (verified), so a wrong guess is harmless.
deploy_hook() {   # deploy_hook <relpath shared by hooks/ and ES-DE/scripts/>
  local src="$MAD_DIR/hooks/$1" dst="$HOME/ES-DE/scripts/$1"
  [ -f "$src" ] || { warn "hook source missing: $1"; return; }
  cmp -s "$src" "$dst" 2>/dev/null && return   # already current — skip (no churn on re-runs)
  run mkdir -p "$(dirname "$dst")"
  backup_hook "$dst"                            # back up OUT to ~/Downloads/_TMP, not in-place
  run cp -f "$src" "$dst" && run chmod +x "$dst"
}
say "Activation hooks"
# Core controller-router + Lindbergh hooks — ALWAYS deployed. Each self-filters by system
# (router hooks check the launch command / system; lindbergh-pads-* exit 0 unless system is
# 'lindbergh'), so deploying them unconditionally is a harmless no-op when unused. Masters
# live in hooks/ like every other hook.
# Core always-deployed hooks (controller-router + Lindbergh + quit-combo + on-the-go + cloud). The set
# is DERIVED by lib/hook-deploy.sh mad_core_hooks = hooks/game-{start,end}/*.sh minus MAD_GATED_HOOKS
# (also driven by deck-post-update.sh's redeploy). Each self-filters by system + reads its own [policy]
# switch, so deploying unconditionally is a no-op when unused (03/07 TDP cap; 09/11 internal-res
# downshift; 07/09 cemu input; 08/10 cemu res; daphne).
. "$MAD_DIR/lib/hook-deploy.sh"
while IFS= read -r h; do [ -n "$h" ] && deploy_hook "$h"; done < <(mad_core_hooks "$MAD_DIR/hooks")
ok "core game-start/end hooks (controller-router, quit-combo, on-the-go, cloud)"
# Provision the during-play timer/service units now so the MAD toggle works on a FRESH
# install (otherwise they are only written by a full deck-post-update.sh run).
[ "$DRY_RUN" = 1 ] || bash "$MAD_DIR/deck-cloud.sh" ensure-units >/dev/null 2>&1 || true
ok "cloud-backup on-exit hook + timer units (always)"
# Retire the superseded per-emulator Dolphin res hooks so an existing install never double-runs them
# alongside 09/11. backup_hook copies each to ~/Downloads/_TMP (recoverable) before it is removed.
for h in game-start/06-dolphin-res.sh game-end/08-dolphin-res-restore.sh; do
  _d="$HOME/ES-DE/scripts/$h"; [ -f "$_d" ] && { backup_hook "$_d"; run rm -f "$_d"; }
done
ok "retired superseded Dolphin res hooks (06/08)"
if want INSTALL_THEME; then
  for h in game-start/launchscreen.sh game-end/launchscreen.sh launchscreen-pack.sh \
           system-select/05-record-view.sh; do deploy_hook "$h"; done
  ok "launch-screen hooks (with theme)"
else
  warn "launch-screen hooks skipped (INSTALL_THEME=0)"
fi
if want INSTALL_SINDEN; then
  for h in game-start/sinden.sh game-end/sinden.sh \
           game-start/dolphin-wii-mode.sh game-end/wiimote-quit-watcher.sh; do deploy_hook "$h"; done
  ok "Sinden / Wii hooks"
else
  warn "Sinden / Wii hooks skipped (INSTALL_SINDEN=0)"
fi

# ---- es_systems: route switch/ps2/ps3/xbox through MAD's launch binders ----
if [ "$MAD_STANDALONE" = 1 ]; then
  # Standalone: synthesize a MINIMAL custom_systems (Cat-A switch/ps2/ps3/xbox wrapped
  # from the bundled defs + Cat-B MAD-special systems). Every other system inherits its
  # full bundled definition (es_systems.load_systems overlays custom on bundled by name).
  if [ "$DRY_RUN" = 1 ]; then
    printf '   [dry-run] seed minimal custom_systems/es_systems.xml (switch/ps2/ps3/xbox + MAD specials)\n'
  else
    if ESDE_APPDATA_DIR="$ESDE_HOME" python3 - "$MAD_DIR" <<'PY2'
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lib import es_systems_standalone as s
custom = Path(os.environ.get("ESDE_APPDATA_DIR", str(Path.home() / "ES-DE"))) / "custom_systems" / "es_systems.xml"
r = s.seed_standalone(custom)
if r.get("error"):
    print("   ", r["error"], file=sys.stderr); sys.exit(1)
print("   seeded:", ", ".join(r.get("added") or ["(nothing new)"]))
un = r.get("unavailable") or []
if un:
    print("   WARNING: NOT controller-wrapped — bundled es_systems.xml unavailable for:",
          ", ".join(un), file=sys.stderr)
    sys.exit(3)
PY2
    then ok "standalone custom_systems seeded"
    else warn "custom_systems seed incomplete (see message above) — if it names switch/ps2/ps3/xbox, launch ES-DE once then re-run: bash $MAD_DIR/install.sh --standalone"
    fi
  fi
else
  # Wrap Switch/PS2/PS3/Xbox <command>s with MAD's launch binders (idempotent; no-op if
  # es_systems.xml is absent or already wrapped). Extracted to lib/mad_launch_wrap.py,
  # shared with deck-post-update.sh.
  if [ "$DRY_RUN" != 1 ]; then
    python3 -c "import sys; sys.path.insert(0,'$MAD_DIR'); from lib import mad_launch_wrap; mad_launch_wrap.wrap_console_launchers()" 2>/dev/null \
      && ok "Switch/PS2/PS3/Xbox commands wrapped for launch-time routing" || true
  fi
fi

# Dynamic emulator resolution: ensure custom_systems/es_find_rules.xml carries MAD's
# %EMULATOR_CITRON/EDEN/YUZU/SUYU/PCSX2X6% rules (additive; complements the bundled rules).
# Pairs with mad_launch_wrap's dynamize so es_systems resolves those emulators by find-rule
# instead of a hardcoded AppImage path (an emulator update then needs no es_systems edit).
if [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] ensure es_find_rules.xml (%%EMULATOR_CITRON/EDEN/YUZU/SUYU/PCSX2X6%%)\n'
else
  ESDE_APPDATA_DIR="$ESDE_HOME" python3 -c "import sys; sys.path.insert(0,'$MAD_DIR'); from lib import es_find_rules; es_find_rules.ensure_find_rules()" 2>/dev/null \
    && ok "es_find_rules.xml: dynamic emulator resolution ensured" || true
fi

# Carousel sort order (custom_systems/es_systems_sorting.xml — arcade clustered, Pew last): a
# static file a custom_systems rewrite can drop. Restore from the repo reference ONLY if absent
# (never clobber a live order the user may have re-tuned in ES-DE).
SORT_LIVE="$ESDE_HOME/custom_systems/es_systems_sorting.xml"
SORT_REF="$MAD_DIR/data/es_systems_sorting.reference.xml"
if [ -f "$SORT_LIVE" ]; then
  ok "es_systems_sorting.xml present (carousel order) — leaving it"
elif [ -f "$SORT_REF" ]; then
  run cp "$SORT_REF" "$SORT_LIVE" && ok "restored es_systems_sorting.xml (carousel order) from reference"
fi

# ---- 5b. MAD theme (pixel-es-de) — the C++ panel reads its icons/colours from it ----
if ! want INSTALL_THEME; then
  say "MAD theme — skipped (INSTALL_THEME=0; the panel will be un-themed/icon-less)"
else
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

fi  # ---- end INSTALL_THEME gate ----

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

# ---- 6b. optional components (gated by install.conf; absent conf -> all run) ----
# Sinden lightgun: driver (sindenlightgun.com) + mono/SDL deps + udev. Default ON (a
# star feature; harmless without a gun). Best-effort — a network hiccup must not fail the
# whole install; the MAD Lightgun page can (re)install later.
if want INSTALL_SINDEN; then
  say "Sinden lightgun support"
  [ -x "$MAD_DIR/sinden-install.sh" ] && { run bash "$MAD_DIR/sinden-install.sh" \
    && ok "Sinden driver installed" \
    || warn "Sinden driver download failed (install later from the MAD Lightgun page)"; }
  [ -x "$MAD_DIR/sinden-reinstall-deps.sh" ] && { run bash "$MAD_DIR/sinden-reinstall-deps.sh" \
    && ok "Sinden runtime deps (mono/SDL/udev)" \
    || warn "Sinden deps install failed (re-run deck-post-update.sh once online)"; }
else
  warn "Sinden lightgun skipped (INSTALL_SINDEN=0)"
fi

# Samba network file sharing (root). Default OFF.
if want INSTALL_SAMBA; then
  say "Samba file sharing"
  [ -x "$MAD_DIR/samba-setup.sh" ] && { run sudo bash "$MAD_DIR/samba-setup.sh" \
    && ok "Samba configured" || warn "samba-setup.sh failed (re-run it later)"; }
fi

# Passwordless sudo (root). OPT-IN, default OFF, gated STRICTLY on an explicit truthy value - NOT via
# want() (which defaults ON when there is no install.conf); a security grant must never be enabled by
# accident. deck-post-update.sh re-applies it after each SteamOS update while this stays on.
case "${INSTALL_NOPASSWD:-}" in
  1|on|yes|true|On|ON|Yes|True)
    say "Passwordless sudo (INSTALL_NOPASSWD)"
    [ -x "$MAD_DIR/sudoers-nopasswd-setup.sh" ] && { run sudo bash "$MAD_DIR/sudoers-nopasswd-setup.sh" \
      && ok "passwordless sudo enabled" || warn "sudoers-nopasswd-setup.sh failed"; } ;;
esac

# ---- 7. core system deps: python tk+evdev (pacman), input group ----
say "System dependencies"
if python3 -c 'import tkinter, evdev, yaml' 2>/dev/null; then
  ok "python tkinter + evdev + yaml present"
elif [ "$DRY_RUN" = 1 ]; then
  printf '   [dry-run] mad_pacman_install --refresh python-evdev tk python-yaml (readonly unlock + keyring + pacman + re-lock)\n'
else
  warn "installing python-evdev + tk + python-yaml (pacman — SteamOS's root is wiped by updates)"
  # shellcheck source=lib/pacman-helpers.sh
  . "$MAD_DIR/lib/pacman-helpers.sh"
  mad_pacman_install --refresh python-evdev tk python-yaml \
    && ok "installed python-evdev + tk + python-yaml" || warn "pacman failed — re-run, or check the keyring"
fi
if groups 2>/dev/null | grep -qw input; then
  ok "'input' group OK"
else
  # Remember this so the final checklist can sequence the relogin BEFORE "launch
  # ES-DE" — until the new membership takes effect, MAD reads zero controllers
  # (devices.py swallows the PermissionError and returns an empty device list).
  INPUT_RELOGIN_NEEDED=1
  run sudo usermod -aG input "$USER" && warn "added '$USER' to 'input' — LOG OUT/IN for it to take effect"
fi

# ---- 7b. suspend mode (quirk-aware: deep unless the kernel truly allows s2idle) ----
# Always runs (correctness, not a preference). Decides by the kernel's 'no s2idle allowed'
# quirk, not the DMI model. Honors INSTALL_SUSPEND (off=skip). Re-applied after SteamOS
# updates by deck-post-update.sh.
say "Suspend mode"
run bash "$MAD_DIR/suspend-mode-setup.sh" \
  && ok "suspend mode set for this Deck" \
  || warn "suspend-mode-setup.sh failed (re-run deck-post-update.sh)"

# ---- 8. verify (only the bits a CORE install sets up) ----
if [ "$DRY_RUN" = 0 ]; then
  say "Verifying"
  if [ -x "$HOME/Applications/ES-DE-MAD.AppImage" ] && grep -q 'ES-DE-MAD' "$HOME/Applications/ES-DE.AppImage" 2>/dev/null; then
    ok "ES-DE-MAD AppImage + wrapper"
  else
    warn "ES-DE AppImage/wrapper not in place"
    FATAL_NO_ESDE=1   # the frontend can't launch — surfaced loudly at the end
  fi
  H_OK=1; for f in "$HS/04-controller-router-setup.sh" "$HS/05-controller-router-standalone.sh" "$HE/00-controller-router.sh"; do [ -x "$f" ] || H_OK=0; done
  [ "$H_OK" = 1 ] && ok "ES-DE hooks" || warn "one or more hooks missing"
  python3 -c 'import tkinter, evdev, yaml' 2>/dev/null && ok "python deps" || warn "python tkinter/evdev/yaml still missing"
  [ -r "$MAD_DIR/controller-router.py" ] && ok "MAD tools present" || warn "MAD tools missing"
  [ -d "${THEME_DIR:-$HOME/ES-DE/themes/pixel-es-de}" ] && ok "MAD theme (pixel-es-de)" || warn "MAD theme not installed — MAD will be un-themed/icon-less"
  if [ "$MAD_STANDALONE" = 1 ]; then
    if grep -q '<name>switch</name>' "$ESDE_HOME/custom_systems/es_systems.xml" 2>/dev/null; then
      ok "standalone custom_systems wired (switch/ps2/ps3/xbox)"
    else
      warn "switch/ps2/ps3/xbox NOT in custom_systems — launch ES-DE once, then re-run: bash $MAD_DIR/install.sh --standalone"
    fi
    if [ -n "$(find "$HOME/ROMs" -mindepth 2 -maxdepth 3 -type f 2>/dev/null | head -1)" ]; then
      ok "ROMs found under ~/ROMs"
    else
      warn "no ROMs under ~/ROMs/<system> yet — put them there (ES-DE's native rom dir)"
    fi
  fi
fi

# ---- 9. the two manual steps + notes ----
# If the patched ES-DE binary never landed, the install is INCOMPLETE — say so
# loudly and stop, instead of burying it under the "almost done" checklist (the
# manual steps are pointless if ES-DE can't launch at all).
if [ "${FATAL_NO_ESDE:-0}" = 1 ]; then
  cat >&2 <<EOF

$(c '1;31')============= INSTALL INCOMPLETE — ES-DE NOT INSTALLED =============$(c 0)
The patched ES-DE AppImage ($HOME/Applications/ES-DE-MAD.AppImage) is missing,
so ES-DE will NOT launch yet. The MAD tools, hooks and theme ARE in place.

To finish, do ONE of these:
  - Re-run once you're online:
        bash $MAD_DIR/deck-fetch-esde.sh
        bash $MAD_DIR/deck-post-update.sh --wrapper
  - Or install stock ES-DE (EmuDeck, or https://es-de.org) as
        $HOME/Applications/ES-DE.AppImage
    (you lose MAD's panel patches but get a working frontend).
$(c '1;31')===================================================================$(c 0)
EOF
  exit 1
fi

# Sequence the input-group relogin BEFORE "go launch ES-DE": until it takes
# effect MAD reads zero controllers, which looks like "MAD doesn't see my pad".
# Real runs only — in --dry-run nothing was actually added, so don't claim it was
# (a dry-run new user still sees the inline "[dry-run] usermod …" preview above).
if [ "$DRY_RUN" = 0 ] && [ "${INPUT_RELOGIN_NEEDED:-0}" = 1 ]; then
  cat <<EOF

$(c '1;31')*** FIRST: REBOOT or log out/in before launching ES-DE ***$(c 0)
   This install just added you to the 'input' group. Until you re-login, MAD
   can't read your controllers (you'd see "no pads detected"). Reboot or log out
   and back in, THEN do the steps below.
EOF
fi

if [ "$MAD_STANDALONE" = 1 ]; then
  cat <<EOF

$(c '1;33')STANDALONE mode$(c 0) — MAD installed the frontend + control panel, NOT the emulators.
   To finish: install emulators where ES-DE looks (flatpak, e.g.
   'flatpak install net.pcsx2.PCSX2', or drop AppImages in ~/Applications), and put
   ROMs in ~/ROMs/<system> (e.g. ~/ROMs/snes, ~/ROMs/ps2). ~195 systems are pre-wired
   to your installed emulators automatically; switch/ps2/ps3/xbox plus sinden/daphne/
   openbor/model2/mugen/naomi get MAD's special handling out of the box.
EOF
fi
cat <<EOF

$(c '1;36')=============== ALMOST DONE — 2 manual steps ===============$(c 0)

$(c '1;33')1) Add ES-DE to Steam, then turn Steam Input OFF$(c 0)
   - Game Mode -> Library -> "Add a Non-Steam Game" -> browse to:
        $HOME/Applications/ES-DE.AppImage
     (this is MAD's launcher -- a small WRAPPER SCRIPT, not a raw AppImage;
      it does the splash, runs the extracted build, and feeds the in-app updater)
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
