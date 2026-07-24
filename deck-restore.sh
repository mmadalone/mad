#!/usr/bin/env bash
# ============================================================================
# deck-restore.sh — companion to deck-backup.sh.
#
# Prompts for:
#   - SOURCE: a directory holding the backup archives (or a single tarball)
#   - whether to restore the config archive (ES-DE + emulator settings + core)
#   - whether/where to restore ROMs and downloaded media (you choose the target
#     drive/dir — handy on a new Deck or a differently-named SD card)
#
# After restoring, it AUTO-RESOLVES standalone emulators: for every emulator whose
# data came back in the backup, it checks the emulator is actually installed
# (flatpak or ~/Applications AppImage) and WARNS about any that are missing, with
# how to get them.
#
# Usage:
#   bash deck-restore.sh [SOURCE_DIR_or_TARBALL]
#
# The config archive holds absolute paths and extracts to / (over $HOME).
# ROMs/media archives hold RELATIVE paths (top-level ROMs/ and downloaded_media/)
# so they can be extracted under any target directory you choose.
# Idempotent — safe to re-run.
# ============================================================================
set -euo pipefail

log()  { echo "[restore] $*"; }
warn() { echo "[restore] WARN: $*" >&2; }
die()  { echo "[restore] FATAL: $*" >&2; exit 1; }

SELF_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
# True (exit 0) when ES-DE is running. Config restore overwrites es_settings.xml +
# gamelists, which ES-DE rewrites on exit (rule #3) — restoring while it's up
# silently reverts. Fail-open (treat as not-running) if the probe itself errors,
# matching install.sh's esde_running guard.
esde_running() {
    python3 -c "import sys; sys.path.insert(0,'$SELF_DIR'); from lib.proc_guard import esde_running; sys.exit(0 if esde_running() else 1)" 2>/dev/null
}

SRC="${1:-}"
DEFAULT_SRC="${BACKUP_DEST:-$HOME/deck-config-backups}"
if [[ -z $SRC ]]; then
    read -rp "Backup source (dir or .tar.gz) [$DEFAULT_SRC] " SRC
    SRC="${SRC:-$DEFAULT_SRC}"
fi
[[ -e $SRC ]] || die "no such path: $SRC"

# ---- locate archives ----
if [[ -f $SRC ]]; then
    CONFIG_TB="$SRC"; ROMS_TB=""; MEDIA_TB=""   # single explicit tarball
else
    CONFIG_TB="$(ls -t "$SRC"/deck-config-*.tar.gz "$SRC"/deck-config-*.tar 2>/dev/null | head -1 || true)"
    ROMS_TB="$(ls -t "$SRC"/deck-roms-*.tar 2>/dev/null | head -1 || true)"
    MEDIA_TB="$(ls -t "$SRC"/deck-media-*.tar 2>/dev/null | head -1 || true)"
fi

log "=== found in source ==="
log "  config: ${CONFIG_TB:-<none>}"
log "  ROMs:   ${ROMS_TB:-<none>}"
log "  media:  ${MEDIA_TB:-<none>}"
[[ -z $CONFIG_TB && -z $ROMS_TB && -z $MEDIA_TB ]] && die "no deck-* archives found in $SRC"

confirm() { local q="$1" a; read -rp "$q [y/N] " a; [[ $a =~ ^[Yy] ]]; }
verify()  { if [[ $1 == *.gz ]]; then tar -tzf "$1" >/dev/null 2>&1; else tar -tf "$1" >/dev/null 2>&1; fi; }

# ---- 1) config archive -> extract to / (absolute paths) ----
if [[ -n $CONFIG_TB ]]; then
    verify "$CONFIG_TB" || die "config archive is corrupt: $CONFIG_TB"
    log "config archive integrity ok ($(du -h "$CONFIG_TB" | cut -f1))"
    if esde_running; then
        warn "ES-DE is running — NOT restoring config (it rewrites es_settings.xml + gamelists on"
        warn "exit, which would silently revert the restore). Close ES-DE (Desktop Mode) and re-run."
    elif confirm "Restore config (ES-DE + emulator settings + tools) over \$HOME?"; then
        # --- rule 5: snapshot the live files this archive will OVERWRITE before extracting ---
        # The config archive stores absolute paths (leading '/' stripped by tar) and is
        # extracted with -C /. Fold its member list down to the distinct top-level roots,
        # then copy each root that currently exists into a same-filesystem _TMP dir so a bad
        # restore can be rolled back. $HOME is on the internal drive -> snapshot to ~/Downloads.
        SNAP="$HOME/Downloads/_TMP-restore-$(date +%Y%m%d-%H%M%S)"
        # Distinct top-level roots the archive would overwrite (members stored as
        # home/deck/… ; fold to the shallowest unique roots), kept only where a live
        # copy exists (nothing live at a path -> nothing to back up).
        mapfile -t _roots < <(tar -tzf "$CONFIG_TB" 2>/dev/null | sed 's:/*$::' | LC_ALL=C sort -u \
                   | awk '{ if (root=="" || ($0!=root && index($0, root "/")!=1)) { root=$0; print } }')
        live=(); for root in "${_roots[@]}"; do [[ -n $root && -e /$root ]] && live+=("/$root"); done
        # Free-space guard (mirror the ROMs branch): cp -a copies each live root's WHOLE
        # subtree, which can far exceed the archive (the backup excludes caches/shader dirs
        # the live tree still has), so estimate from the live roots, not the tarball. The
        # config restore overwrites $HOME, so proceeding without a backup is the rule-5 hole
        # we're closing — make the user opt in explicitly if space is short.
        if [[ ${#live[@]} -gt 0 ]]; then
            mkdir -p "$HOME/Downloads"   # ensure the df target / snapshot parent exists (fresh Deck: ~/Downloads is lazy)
            need=$(du -sck "${live[@]}" 2>/dev/null | tail -1 | cut -f1)
            free=$(df -Pk "$HOME/Downloads" | awk 'NR==2{print $4}')
            if [[ ${free:-0} -lt ${need:-0} ]]; then
                warn "pre-restore snapshot needs ~$((need/1024/1024))G but only ~$((free/1024/1024))G free at ~/Downloads"
                confirm "Proceed WITHOUT a full pre-restore backup (rule-5 rollback protection reduced)?" \
                    || die "aborted — free up space (or point ~/Downloads at a larger disk) and retry"
            fi
        fi
        log "snapshotting live config to be overwritten -> $SNAP"
        mkdir -p "$SNAP"
        snap_n=0
        for src in "${live[@]}"; do
            if cp -a --parents "$src" "$SNAP/" 2>/dev/null; then
                snap_n=$((snap_n+1))
            else
                warn "could not snapshot $src (continuing)"
            fi
        done
        if [[ $snap_n -eq 0 && ${#live[@]} -gt 0 ]]; then
            confirm "No pre-restore snapshot could be taken — proceed WITHOUT rule-5 rollback protection?" \
                || die "aborted — no snapshot made; resolve the snapshot failure and retry"
        fi
        cat > "$SNAP/RECOVERY.txt" <<EOF
Live config snapshot taken by deck-restore.sh on $(date), BEFORE extracting:
  $CONFIG_TB
$snap_n top-level path(s) that the archive would overwrite were copied here,
rooted under home/ (full absolute paths preserved by 'cp --parents').

To ROLL BACK the config restore (put these files back over the live ones):
  for d in "$SNAP"/*/; do cp -a "\$d" /; done

Then delete this snapshot once you are happy:  rm -rf "$SNAP"
EOF
        log "snapshot done: $snap_n path(s) saved (rollback steps in $SNAP/RECOVERY.txt)"
        if ! tar -xpf "$CONFIG_TB" -C / 2> >(grep -v 'Cannot change ownership' >&2); then
            warn "tar reported issues — continuing (pre-restore snapshot is at $SNAP)"
        fi
        log "config restored (pre-restore snapshot: $SNAP)"
    else
        log "skipped config restore"
    fi
fi

# ---- 2) ROMs -> user-chosen target ----
if [[ -n $ROMS_TB ]]; then
    verify "$ROMS_TB" || die "ROMs archive is corrupt: $ROMS_TB"
    if confirm "Restore ROMs ($(du -h "$ROMS_TB" | cut -f1))?"; then
        DEF_RT="$(dirname "$(readlink -f "$HOME/ROMs" 2>/dev/null || echo /run/media/deck/1tbDeck/ROMs)")"
        read -rp "  Restore ROMs UNDER which directory? (a 'ROMs/' folder is created here) [$DEF_RT] " RT
        RT="${RT:-$DEF_RT}"; mkdir -p "$RT"
        need=$(( $(du -sk "$ROMS_TB"|cut -f1) )); free=$(df -Pk "$RT"|awk 'NR==2{print $4}')
        [[ ${free:-0} -lt $need ]] && warn "low space at $RT (need ~$((need/1024/1024))G, have $((free/1024/1024))G) — continuing anyway"
        # rule 5: an existing ROMs/ would be overwritten on collision with no rollback.
        # Move it aside to a SAME-FILESYSTEM _TMP first (instant rename, fully recoverable)
        # so the restore can't clobber the current set; the restore is then the archive's
        # set, with the prior one recoverable. Fresh target -> no-op.
        if [ -d "$RT/ROMs" ]; then
            SNAP="$RT/_TMP-restore-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$SNAP"
            mv "$RT/ROMs" "$SNAP/ROMs"
            printf 'Pre-restore snapshot (%s): existing ROMs/ moved here, recoverable.\nRoll back: rm -rf "%s/ROMs" && mv "%s/ROMs" "%s/ROMs"\n' "$(date)" "$RT" "$SNAP" "$RT" > "$SNAP/RECOVERY.txt"
            warn "existing ROMs moved aside -> $SNAP (recoverable; restore = the archive's set)"
        fi
        log "extracting ROMs -> $RT/ROMs"
        tar -xf "$ROMS_TB" -C "$RT" || warn "ROMs extract reported issues"
        log "ROMs restored to $RT/ROMs"
    else log "skipped ROMs restore"; fi
fi

# ---- 3) downloaded media -> user-chosen target ----
if [[ -n $MEDIA_TB ]]; then
    verify "$MEDIA_TB" || die "media archive is corrupt: $MEDIA_TB"
    if confirm "Restore downloaded media ($(du -h "$MEDIA_TB" | cut -f1))?"; then
        DEF_MT="/run/media/deck/1tbDeck"
        read -rp "  Restore media UNDER which directory? (a 'downloaded_media/' folder is created here) [$DEF_MT] " MT
        MT="${MT:-$DEF_MT}"; mkdir -p "$MT"
        # rule 5: move an existing downloaded_media/ aside (same-fs instant rename, recoverable)
        # before extracting, so a re-restore can't clobber the current set. Fresh target -> no-op.
        if [ -d "$MT/downloaded_media" ]; then
            SNAP="$MT/_TMP-restore-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$SNAP"
            mv "$MT/downloaded_media" "$SNAP/downloaded_media"
            printf 'Pre-restore snapshot (%s): existing downloaded_media/ moved here, recoverable.\nRoll back: rm -rf "%s/downloaded_media" && mv "%s/downloaded_media" "%s/downloaded_media"\n' "$(date)" "$MT" "$SNAP" "$MT" > "$SNAP/RECOVERY.txt"
            warn "existing downloaded_media moved aside -> $SNAP (recoverable)"
        fi
        log "extracting media -> $MT/downloaded_media"
        tar -xf "$MEDIA_TB" -C "$MT" || warn "media extract reported issues"
        log "media restored to $MT/downloaded_media"
        log "  (if this path differs from before, set MediaDirectory in ES-DE → Other Settings)"
    else log "skipped media restore"; fi
fi

# ---- system udev rules (sudo) ----
ETC_MIRROR="$HOME/Emulation/tools/launchers/sinden-shim/etc-backup/99-sinden-lightgun.rules"
if [[ -f $ETC_MIRROR ]]; then
    log "=== installing sinden udev rules (sudo) ==="
    if sudo cp "$ETC_MIRROR" /etc/udev/rules.d/99-sinden-lightgun.rules; then
        sudo udevadm control --reload 2>/dev/null || warn "udevadm reload failed"
        sudo udevadm trigger --subsystem-match=input 2>/dev/null || true
        log "udev rules installed + reloaded"
    else warn "couldn't install udev rules; run later: sudo cp $ETC_MIRROR /etc/udev/rules.d/"; fi
fi
if ! groups | grep -q '\binput\b'; then
    sudo usermod -aG input "$USER" 2>/dev/null && log "added '$USER' to 'input' group (logout/login needed)" || true
fi

# ---- AUTO-RESOLVE standalone emulators (warn if a restored emu isn't installed) ----
# name | data-path that proves the emu was restored | install token (flatpak:ID / app:GLOB / bin:PATH)
. "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/mad-paths.sh" 2>/dev/null || . "$HOME/Emulation/tools/launchers/lib/mad-paths.sh"
EMU_MAP=(
    "RetroArch|$HOME/.var/app/org.libretro.RetroArch|flatpak:org.libretro.RetroArch"
    "Dolphin|$HOME/.var/app/org.DolphinEmu.dolphin-emu|flatpak:org.DolphinEmu.dolphin-emu"
    "xemu|$storageRoot/xemu|flatpak:app.xemu.xemu"
    "melonDS|$storageRoot/melonDS|flatpak:net.kuribo64.melonDS"
    "MAME|$storageRoot/mame|flatpak:org.mamedev.MAME"
    "PCSX2|$storageRoot/pcsx2|app:pcsx2*.AppImage"
    "RPCS3|$storageRoot/rpcs3|app:rpcs3*.AppImage"
    "Ryujinx|$storageRoot/ryujinx|app:ryujinx*"
    "azahar|$storageRoot/azahar|app:azahar*.AppImage"
    "mGBA|$storageRoot/mgba|app:mGBA*.AppImage"
    "Vita3K|$storageRoot/Vita3K|app:Vita3K*"
    "shadPS4|$storageRoot/shadps4|app:Shadps4*.AppImage"
    "Ikemen GO (mugen)|$HOME/Emulation/tools/ikemen-go|bin:$HOME/Emulation/tools/ikemen-go/Ikemen_GO_Linux"
)
emu_installed() {
    local tok="$1"
    case "$tok" in
        flatpak:*) flatpak info "${tok#flatpak:}" >/dev/null 2>&1 ;;
        app:*)     compgen -G "$HOME/Applications/${tok#app:}" >/dev/null 2>&1 ;;
        bin:*)     [[ -e "${tok#bin:}" ]] ;;
        *) return 1 ;;
    esac
}
echo
log "=== standalone emulators (data restored vs emulator installed) ==="
missing=0
for row in "${EMU_MAP[@]}"; do
    IFS='|' read -r name datap tok <<< "$row"
    [[ -e $datap ]] || continue          # only report emus whose data is actually present
    if emu_installed "$tok"; then
        log "  [ok]      $name"
    else
        warn "  [MISSING] $name — data restored but emulator not installed ($tok)"
        missing=$((missing+1))
    fi
done
[[ $missing -gt 0 ]] && log "  -> install missing emulators via EmuDeck (flatpaks) or re-download their AppImage to ~/Applications/"

# ---- follow-up checklist for things deliberately NOT in the backup ----
echo
log "=== follow-up (not in backup, re-derive) ==="
CORES_MANIFEST="$HOME/Emulation/tools/launchers/.cores-manifest.txt"
BEZEL_MANIFEST="$HOME/Emulation/tools/launchers/.bezel-manifest.txt"
[[ -f $CORES_MANIFEST && ! -d $HOME/.var/app/org.libretro.RetroArch/config/retroarch/cores ]] && \
    log "[ ] RetroArch cores absent — re-download $(wc -l <"$CORES_MANIFEST") cores listed in $CORES_MANIFEST"
[[ -f $BEZEL_MANIFEST && ! -d $HOME/Emulation/tools/bezelproject ]] && \
    log "[ ] bezelproject absent — re-clone repos listed in $BEZEL_MANIFEST"
if [[ ! -f $HOME/Applications/ES-DE-MAD.AppImage ]]; then
    if [[ -x $HOME/Emulation/tools/launchers/deck-fetch-esde.sh ]] && bash "$HOME/Emulation/tools/launchers/deck-fetch-esde.sh"; then
        log "[x] MAD ES-DE pulled from CI release → ~/Applications/ES-DE-MAD.AppImage (run deck-post-update.sh to repoint the ES-DE.AppImage wrapper)"
    else
        log "[ ] MAD ES-DE missing — restore from backup, or rebuild: in ~/esde-build/ES-DE run 'git checkout deck-patches', then ~/esde-build/ES-DE/tools/deck-ubuntu-build.sh (needs the esde-ubuntu distrobox)"
    fi
fi
    command -v smbd >/dev/null 2>&1 || log "[ ] Samba absent — re-run ~/Emulation/tools/launchers/samba-setup.sh (root pacman, wiped by SteamOS update)"
    command -v distrobox >/dev/null 2>&1 || log "[ ] distrobox absent — reinstall if you need to REBUILD ES-DE (build containers live in ~/.local/share/containers; survive /home but not a fresh Deck)"
[[ -d $biosRoot ]] || log "[ ] BIOS files (~/Emulation/bios) are NOT in backup — restore separately"
[[ -x $HOME/Emulation/tools/launchers/sinden-reinstall-deps.sh ]] && \
    log "[ ] Run sinden-reinstall-deps.sh to restore system packages (mono, sdl, …)"
log "[ ] Log out/in for 'input' group; launch ES-DE and verify a game + lightgun"

echo
log "=== restore complete ==="
exit 0
