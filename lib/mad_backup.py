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
from pathlib import Path

from . import localpolicy
from . import fsutil
from . import staterev
from .policy import LOCAL, load_merged
from .proc_guard import emulator_running


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
    # controller config on exit and would clobber the slot file we write here.
    # Clearing a choice above is safe - it leaves the active file untouched -
    # so the guard is only on apply. apply_slot_profile returns status strings
    # (never raises; the caller shows the return value), so this refuses by RETURN.
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
            fsutil.ensure_pristine_backup(ini)   # one pristine .router-backup (defers to a sibling .bak)
            binds = eden_cfg._template_bindings(src)
            # No "type" key here (was binds["type"] = "0"): eden_cfg._template_bindings()
            # already drops "type" from a profile file's bindings (it is a per-slot meta
            # key, not part of the button layout), so _apply_player below never sees the
            # key and the slot's EXISTING type line is left byte-for-byte alone. Stamping
            # "0" here was unconditionally overwriting a controller type the user picked in
            # Eden's own Controls dialog (Handheld=4, GameCube=5, dual joycon=1, ...) every
            # time this per-slot profile picker ran -- and unlike the router/launch writers,
            # this path is PERSISTENT (no launch-time restore), so the downgrade stuck.
            # Audit phase-5 site 3; same reasoning as the eden_cfg.assign()/assign_devices()
            # fix (see the comments there).
            binds["connected"] = "true"; binds["profile_name"] = ""
            text = ini.read_text(encoding="utf-8")
            body = eden_cfg._apply_player(inifile.section_body(text, "Controls") or "", slot, binds)
            fsutil.atomic_write(ini, inifile.set_section(text, "Controls", body))
    except Exception as e:                            # apply failed → DON'T record the choice
        return f"⚠ {bname} {label} {slot + 1}: apply failed, nothing changed ({e})"
    data = localpolicy.load(LOCAL)                     # success → now persist the choice
    data.setdefault("backends", {}).setdefault(bname, {}).setdefault("slot_profiles", {})[str(slot)] = profile
    localpolicy.dump(LOCAL, data)
    return f"{bname} {label} {slot + 1} ← {profile}  (your profile file untouched)"


def restore_router_backups(targets: dict) -> str:
    """Revert the one-time pristine backup each backend writes before its first
    edit of an emulator's input config. The snapshot lives under `.router-backup`
    (launch/device-assign side) OR `.bak` (Settings/input editor side) — exactly
    one of them per file (see fsutil.ensure_pristine_backup / cfgutil.ensure_bak);
    restore from whichever exists."""
    restored = []
    for _name, p in targets.items():
        if p.is_dir():
            # Dir target (cemu): its pristine snapshot is a `.router-backup` SUBDIR
            # of files (see backup_active_once), NOT a sibling file. Restore each
            # contained file back into p. (The old code globbed the dir and then
            # stripped `.router-backup` off its name - yielding '' and a ValueError
            # from with_name('') that escaped the try/except and aborted the ENTIRE
            # restore for every target.)
            for bk in sorted(p.glob("*.router-backup")):
                if bk.is_dir():
                    for f in sorted(bk.iterdir()):
                        if not f.is_file():
                            continue
                        try:
                            shutil.copy2(f, p / f.name); restored.append(f.name)
                        except OSError:
                            pass
                elif bk.is_file():                       # defensive: stray sibling snapshot file
                    name = bk.name[:-len(".router-backup")]
                    if name:
                        try:
                            shutil.copy2(bk, p / name); restored.append(name)
                        except OSError:
                            pass
            continue
        cands = list(p.parent.glob(p.name + ".router-backup"))
        cands += list(p.parent.glob(p.stem + ".*.router-backup"))
        cands += list(p.parent.glob(p.name + ".bak"))   # editor-side pristine (exact per-target name)
        for bk in cands:
            suf = next((s for s in (".router-backup", ".bak") if bk.name.endswith(s)), None)
            if suf is None:
                continue
            target = bk.with_name(bk.name[:-len(suf)])
            try:
                shutil.copy2(bk, target); restored.append(target.name)
            except OSError:
                pass
    if restored:
        staterev.bump("config")
    return ((f"Restored {len(restored)} emulator input backup(s): "
             + ", ".join(restored)) if restored
            else "No input backups (.router-backup / .bak) found.")


def reset_local() -> str:
    """Revert the GUI overrides to documented defaults. The overrides file is
    MOVED to a recoverable _TMP (rule #5), never hard-deleted."""
    if LOCAL.is_file():
        retired = fsutil.recoverable_delete(
            LOCAL, tmp_base=Path.home() / "Downloads" / "_TMP",
            tag="mad-reset",
            recovery_note=("MAD 'Reset overrides' moved controller-policy.local.toml "
                           "here. To undo, move the .toml back to its original path. "
                           "This file also holds every On-the-go handheld override: per-system "
                           "watt caps, Wii U handheld resolution presets and handheld graphic-pack "
                           "choices, and the per-game input profile picks. Clearing it clears all "
                           "of them; the emulators' own configs are untouched."))
        staterev.bump("config")
        return ("Cleared GUI overrides (reverted to documented defaults). "
                f"Recoverable in {retired}.")
    return "Cleared GUI overrides (reverted to documented defaults)."
