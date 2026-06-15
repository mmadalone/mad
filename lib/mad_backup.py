"""MAD backup/restore + per-slot profile apply (Tk-free).

Extracted from router-config-gui.py (MAD native-panel phase 0, R5): the backup
page's pure file operations and the Backends page's slot-profile apply — the
status-label writes became return-value messages (the caller shows them: Tk
status.config / native panel footer). Zero behavior change otherwise.

`backup_active_once` is the .router-backup safety net (one-time backup of an
emulator's ACTIVE slot file before MAD's first write) — previously buried in
the Tk GUI layer, now shared so the native panel keeps the same guarantee.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import localpolicy
from . import fsutil
from .policy import LOCAL, load_merged
from .proc_guard import emulator_running, process_running

LAUNCHERS = Path(__file__).resolve().parent.parent       # lib/.. = launchers dir
SNAP_DIR = LAUNCHERS / "data" / "gui-backup"


def backup_active_once(backup, files, single=False):
    """One-time backup of the active slot file(s) before MAD's first write, so the
    current state is always recoverable. `backup` is a dir (cemu) or a file path
    (single=True, eden)."""
    try:
        if single:
            bp = Path(backup)
            if not bp.exists() and Path(files[0]).is_file():
                shutil.copy2(files[0], bp)
            return
        Path(backup).mkdir(parents=True, exist_ok=True)
        for f in files:
            dest = Path(backup) / Path(f).name
            if Path(f).is_file() and not dest.exists():
                shutil.copy2(f, dest)
    except Exception:
        pass


def apply_slot_profile(bname, slot, profile, merged=None) -> str:
    """Save the per-slot choice to [backends.<bname>].slot_profiles AND apply it to the
    ACTIVE slot file. cemu = copy <profile>.xml -> controller<slot>.xml verbatim; eden =
    write <profile>.ini bindings -> qt-config player_<slot>. The NAMED profile is opened
    read-only and never modified. Returns the status message to show."""
    label = "Controller" if bname == "cemu" else "Player"
    bcfg = (merged or load_merged()).get("backends", {}).get(bname, {})
    if not profile:                                   # clear the choice (active file left as-is)
        data = localpolicy.load(LOCAL)
        sp = data.get("backends", {}).get(bname, {}).get("slot_profiles", {})
        if isinstance(sp, dict) and sp.pop(str(slot), None) is not None:
            localpolicy.dump(LOCAL, data)
        return f"{bname} {label} {slot + 1}: choice cleared (active file left as-is)"
    # Refuse to APPLY while the emulator is open: cemu/eden rewrite their
    # controller config on exit and would clobber the slot file we write here
    # (same reason do_restore refuses below). Clearing a choice above is safe
    # — it leaves the active file untouched — so the guard is only on apply.
    # apply_slot_profile returns status strings (never raises; both callers show
    # the return value), so this refuses by RETURN, like its sibling do_restore.
    if emulator_running(bname):
        return (f"⚠ {bname} {label} {slot + 1}: close {bname} first, then choose "
                "again — it rewrites its controller config on exit and would "
                "clobber this (nothing changed).")
    try:                                              # APPLY FIRST — persist only on success
        if bname == "cemu":
            cdir = Path(os.path.expanduser(bcfg.get("config_dir", "~/.config/Cemu/controllerProfiles")))
            src = cdir / f"{profile}.xml"
            if not src.is_file():
                raise FileNotFoundError(src.name)
            dst = cdir / f"controller{slot}.xml"
            backup_active_once(cdir / ".router-backup", [dst])
            shutil.copy2(src, dst)                     # named profile is the SOURCE (read-only)
        else:
            from . import eden_cfg, inifile
            src = Path(os.path.expanduser("~/.config/eden/input")) / f"{profile}.ini"
            if not src.is_file():
                raise FileNotFoundError(src.name)
            ini = Path(os.path.expanduser(bcfg.get("config_file", "~/.config/eden/qt-config.ini")))
            backup_active_once(ini.with_name(ini.name + ".router-backup"), [ini], single=True)
            binds = eden_cfg._template_bindings(src)
            binds["connected"] = "true"; binds["type"] = "0"; binds["profile_name"] = ""
            text = ini.read_text(encoding="utf-8")
            body = eden_cfg._apply_player(inifile.section_body(text, "Controls") or "", slot, binds)
            fsutil.atomic_write(ini, inifile.set_section(text, "Controls", body))
    except Exception as e:                            # apply failed → DON'T record the choice
        return f"⚠ {bname} {label} {slot + 1}: apply failed, nothing changed ({e})"
    data = localpolicy.load(LOCAL)                     # success → now persist the choice
    data.setdefault("backends", {}).setdefault(bname, {}).setdefault("slot_profiles", {})[str(slot)] = profile
    localpolicy.dump(LOCAL, data)
    return f"{bname} {label} {slot + 1} ← {profile}  (your profile file untouched)"


def do_backup(targets: dict, snap: Path = SNAP_DIR) -> str:
    """Snapshot every emulator config target + the GUI overrides into `snap`."""
    n = 0
    snap.mkdir(parents=True, exist_ok=True)
    # Make each backup a TRUE point-in-time mirror: a dir target was previously
    # copytree'd with dirs_exist_ok=True into the persistent snap dir, so files
    # deleted from the live config since the last backup lingered in the snapshot
    # and a later (exact-mirror) do_restore resurrected them. Retire any existing
    # snap/<name> dirs FIRST (rule #5: move to a recoverable _TMP, never rm) so
    # the copytree below writes a clean snapshot. File/LOCAL targets are single
    # copy2 overwrites — no stale leftover possible — so only dir snaps need this.
    stale = [snap / name for name, p in targets.items()
             if p.is_dir() and (snap / name).is_dir()]
    if stale:
        fsutil.recoverable_delete(
            stale, tmp_base=Path.home() / "Downloads" / "_TMP",
            tag="mad-backup-snap",
            recovery_note=("MAD Backup retired these PREVIOUS snapshot dirs (under "
                           "data/gui-backup) to take a fresh point-in-time mirror. "
                           "These are MAD's own snapshots, not your live configs — "
                           "normally safe to discard."))
    for name, p in targets.items():
        if p.is_file():
            shutil.copy2(p, snap / (name + "_" + p.name)); n += 1
        elif p.is_dir():
            shutil.copytree(p, snap / name, dirs_exist_ok=True); n += 1
    if LOCAL.is_file():
        shutil.copy2(LOCAL, snap / LOCAL.name)
    return f"Backed up {n} emulator config(s) + GUI overrides → {snap}"


def do_restore(targets: dict, snap: Path = SNAP_DIR) -> str:
    """Restore the `do_backup` snapshot back onto the live config targets.

    TRUE restore: each live target that exists is first MOVED to a recoverable
    _TMP (rule #5 — never deleted), then the snapshot is copied in. So a folder
    target ends up EXACTLY matching the backup (no merge, no resurrecting files
    you deleted since the backup), and the pre-restore state stays recoverable.
    """
    if not snap.is_dir():
        return "No backup found — run Backup first."
    # Refuse while a standalone emulator (whose config IS a restore target) is
    # open — it rewrites its config on exit and would clobber the restore. NOT
    # ES-DE (MAD runs inside it) and NOT RetroArch (neither writes these files).
    # Switch family pattern matches the policy's own quit_cmd (controller-policy
    # .toml: pkill -f 'Eden|Yuzu|Suyu|Ryujinx') — all four are restore targets.
    busy = [n for n, pat in {
        "Cemu": "[Cc]emu", "PCSX2": "pcsx2",
        "Eden/Yuzu/Suyu/Ryujinx": "Eden|Yuzu|Suyu|Ryujinx",
        "RPCS3": "rpcs3", "xemu": "xemu"}.items() if process_running(pat)]
    if busy:
        return "Close these first, then tap Restore again: " + ", ".join(busy) + "."

    # Pass 1: resolve which snapshot entries to copy + which live targets exist.
    copies, to_retire = [], []          # copies: (src_in_snap, live_dest, is_dir)
    for name, p in targets.items():
        f = snap / (name + "_" + p.name)
        d = snap / name
        if f.is_file():
            copies.append((f, p, False))
        elif d.is_dir():
            copies.append((d, p, True))
        else:
            continue
        if p.exists():
            to_retire.append(p)
    lp = snap / LOCAL.name
    if lp.is_file():
        copies.append((lp, LOCAL, False))
        if LOCAL.exists():
            to_retire.append(LOCAL)
    if not copies:
        return "No backup files found to restore."

    # Move every current live version into ONE recoverable _TMP, then restore, so
    # 'true restore' never destroys the pre-restore state. If we can't safely set
    # them aside, abort BEFORE copying anything (leave the live configs as-is).
    retired = None
    if to_retire:
        try:
            retired = fsutil.recoverable_delete(
                to_retire, tmp_base=Path.home() / "Downloads" / "_TMP",
                tag="mad-restore",
                recovery_note=("MAD Restore replaced these live emulator configs with "
                               "a backup snapshot. To undo, move each item below back "
                               "to its original path."))
        except OSError as e:
            loc = getattr(e, "tmp_dir", None)
            where = (f" Any already-moved configs are recoverable in {loc} "
                     "(see RECOVERY.txt).") if loc else ""
            return ("⚠ Restore aborted — couldn't safely set current configs "
                    f"aside: {e}.{where}")

    n, errs = 0, []
    for src, dest, is_dir in copies:
        try:
            if is_dir:
                shutil.copytree(src, dest)        # dest was retired → exact copy
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            n += 1
        except OSError as e:
            errs.append(f"{dest.name}: {e}")

    tail = f" Pre-restore configs saved (recoverable) in {retired}." if retired else ""
    if errs:
        return (f"⚠ Restored {n}, but {len(errs)} FAILED: "
                + ("; ".join(errs))[:200] + tail)
    if n == 0:
        return "No backup files found to restore."
    return f"Restored {n} emulator config(s) + GUI overrides (true restore).{tail}"


def restore_router_backups(targets: dict) -> str:
    """Revert the one-time *.router-backup files each standalone backend
    writes the first time it edits an emulator's input config."""
    restored = []
    for _name, p in targets.items():
        cands = []
        if p.is_dir():
            cands = list(p.glob("*.router-backup"))
        else:
            cands = list(p.parent.glob(p.name + ".router-backup"))
            cands += list(p.parent.glob(p.stem + ".*.router-backup"))
        for bk in cands:
            target = bk.with_name(bk.name[:-len(".router-backup")])
            try:
                shutil.copy2(bk, target); restored.append(target.name)
            except OSError:
                pass
    return ((f"Restored {len(restored)} emulator input backup(s): "
             + ", ".join(restored)) if restored
            else "No *.router-backup files found.")


def reset_local() -> str:
    """Revert the GUI overrides to documented defaults. The overrides file is
    MOVED to a recoverable _TMP (rule #5), never hard-deleted."""
    if LOCAL.is_file():
        retired = fsutil.recoverable_delete(
            LOCAL, tmp_base=Path.home() / "Downloads" / "_TMP",
            tag="mad-reset",
            recovery_note=("MAD 'Reset overrides' moved controller-policy.local.toml "
                           "here. To undo, move the .toml back to its original path."))
        return ("Cleared GUI overrides (reverted to documented defaults). "
                f"Recoverable in {retired}.")
    return "Cleared GUI overrides (reverted to documented defaults)."


def backup_mad_code() -> str:
    """Tar the whole MAD launchers tree (incl. controller-policy.local.toml) to an
    EXTERNAL dir so it never recurses into itself. MAD also lives on GitHub
    (mmadalone/mad); this is a self-contained local snapshot. BLOCKING — callers
    run it on a worker thread."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.expanduser(f"~/deck-config-backups/mad-code-{ts}.tar.gz")
    name = LAUNCHERS.name
    ex = [f"--exclude={p}" for p in (
        "*/__pycache__", "*.pyc", "*.log",
        f"{name}/.git", f"{name}/data/gui-backup", f"{name}/squashfs-root",
        f"{name}/AppDir", f"{name}/es-de", f"{name}/esde", f"{name}/srm")]
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        subprocess.run(["tar", "czf", dest, "-C", str(LAUNCHERS.parent), *ex, name],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mb = os.path.getsize(dest) // (1024 * 1024)
        return f"MAD code → {dest}  ({mb} MB).  Also on GitHub: mmadalone/mad"
    except Exception as e:
        return f"MAD-code backup failed: {e}"
