"""Handheld resolution downshift for Cemu (Wii U) via graphic-pack presets.

Cemu's internal resolution is not a scalar -- it is a graphic-pack PRESET (e.g. "1920x1080" /
"1280x720") stored in ~/.config/Cemu/settings.xml under
`<GraphicPack><Entry filename=...><Preset><category>Resolution</category><preset>...`. When the Deck
is HANDHELD and the user has configured a handheld preset for a game (MAD On-the-go page), this
switches that game's ENABLED resolution pack to the chosen preset at launch, restored to the resting
preset on exit.

Own independent atomic marker rail (own dir), swept at game-start (launch) AND game-end so a crash
orphan can never leave a later DOCKED game stuck at the handheld preset. The marker records the
resting preset AND the one we applied, so the revert is skipped if the preset was changed underneath
(revert-if-unchanged). Cemu rewrites settings.xml ON EXIT, so apply runs at game-start (Cemu closed)
and restore at game-end (Cemu closed) -- the same timing the input rail (cemu_input_dock) uses.

Reuses the byte-preserving <GraphicPack> read/write + the resolution-pack detector in
lib/madsrv/cemu_packs_cmds.py; the per-game handheld preset lives in policy at
[systems.wiiu.handheld.res_presets].<titleid>.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import deck_state
from .madsrv import cemu_games, cemu_packs_cmds as cp, cfgutil
from .policy import load_merged

_RES_DIR = Path.home() / "Emulation" / "storage" / "controller-router" / "cemu-res"


def _marker(path: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
    return _RES_DIR / (slug + ".json")


def _set_group_preset(entry: dict, group: str, value) -> None:
    """Set `entry`'s <Preset> for `group` to `value` (a name), or REMOVE it when value is None.
    Direct + explicit: we never lean on Cemu's default-clearing, because real packs often mark the
    default INSIDE the preset name ("... (Default)") with no `default=` flag, so a detected default is
    unreliable. Storing the chosen preset explicitly and restoring the exact resting <Preset> (name,
    or absent) is always correct -- Cemu honours an explicit preset and falls back to its own default
    when the <Preset> is absent."""
    entry["presets"] = [(c, p) for (c, p) in entry["presets"] if c != group]
    if value is not None:
        entry["presets"].append((group, value))


def _tid_for_rom(rom: str) -> str | None:
    """The launching rom's Wii U title id, by reversing cemu_games._library() (titleid -> {path,stem}):
    match on the resolved path first, then the file stem. None if the rom is not in Cemu's scanned
    library (then there is no title id, so no per-game resolution)."""
    try:
        lib = cemu_games._library()
    except Exception:
        return None
    if not rom or not lib:
        return None
    try:
        rp = str(Path(rom).resolve())
    except Exception:
        rp = rom
    for tid, info in lib.items():
        p = info.get("path", "")
        try:
            if p and str(Path(p).resolve()) == rp:
                return tid
        except OSError:
            pass
    stem = Path(rom).stem
    for tid, info in lib.items():
        if info.get("stem") and info["stem"] == stem:
            return tid
    return None


def _configured_preset(pol: dict, tid: str) -> str | None:
    """The user's handheld resolution preset for this title from
    [systems.wiiu.handheld.res_presets].<tid> in policy, or None (not configured -> leave alone)."""
    systems = pol.get("systems") if isinstance(pol, dict) else None
    wiiu = systems.get("wiiu") if isinstance(systems, dict) else None
    hh = wiiu.get("handheld") if isinstance(wiiu, dict) else None
    presets = hh.get("res_presets") if isinstance(hh, dict) else None
    v = presets.get(tid.lower()) if isinstance(presets, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


def sweep_all() -> None:
    """Revert every recorded Cemu preset downshift to its resting preset and drop the marker.
    Reverts ONLY if the pack still holds the preset we applied (else the user/Cemu changed it -- leave
    it). Idempotent + self-healing; run at launch-start and game-end. Cemu must be CLOSED."""
    try:
        markers = sorted(_RES_DIR.glob("*.json"))
    except OSError:
        return
    for mk in markers:
        keep = False
        try:
            d = json.loads(mk.read_text(encoding="utf-8"))
            path, filename, group = Path(d["path"]), d["filename"], d["group"]
            text = cfgutil.read_text(path)
            if text is not None:
                entries = cp._parse_graphicpack(text)
                e = cp._find(entries, filename)
                cur = cp._entry_preset(e, group) if e else None
                if e is not None and cur == d.get("applied"):     # unchanged since we applied
                    _set_group_preset(e, group, d.get("prev"))    # prev name, or None -> remove
                    new = cp._write_block(text, entries)
                    if new != text:
                        try:
                            cfgutil.ensure_bak(path)
                            cfgutil.atomic_write(path, new)
                        except Exception:
                            keep = True     # revert WRITE failed (I/O) -> keep the marker to retry,
                            #                 so a later sweep still restores the docked preset
        except Exception:
            pass                            # unreadable/malformed marker -> drop it (nothing to heal)
        if not keep:
            try:
                mk.unlink()
            except OSError:
                pass


def apply(rom: str) -> None:
    """Switch the launching Wii U game's enabled resolution pack to its handheld preset when handheld.
    The handheld preset is the user's explicit choice, or -- when unset -- the 720p CAP (the nearest
    option at or below 720p, applied only when it LOWERS the resting resolution; never an upshift). No-op
    unless: on-the-go enabled, HANDHELD, wiiu participating, the game has an ENABLED resolution pack, the
    effective preset is valid + not Keep, and it differs from the resting one. Writes an atomic marker
    recording the resting + applied preset BEFORE mutating."""
    try:
        pol = load_merged()
    except Exception:
        return
    hh = pol.get("handheld") if isinstance(pol, dict) else None
    if not (isinstance(hh, dict) and hh.get("enabled", False)):
        return
    try:
        if not deck_state.is_handheld(deck_state.resolve_force(hh)):
            return
    except Exception:
        return
    systems = pol.get("systems")
    sysd = systems.get("wiiu") if isinstance(systems, dict) else None
    sys_hh = sysd.get("handheld") if isinstance(sysd, dict) else None
    if not (isinstance(sys_hh, dict) and sys_hh.get("enabled", False)):
        return
    tid = _tid_for_rom(rom)
    if not tid:
        return
    info = cp.resolution_titleids().get(tid.lower())  # only ENABLED resolution packs
    if not info:
        return                                        # no enabled resolution pack -> nothing to drive
    stored = _configured_preset(pol, tid)             # a preset name, cp.KEEP, or None (unset)
    if stored == cp.KEEP:
        return                                        # explicit Keep -> leave as-is
    if stored and stored in info["presets"]:
        low = stored                                  # explicit preset override (respected as-is)
    else:                                             # unset OR stale name -> the effective downshift
        low = cp.downshift_target(info)               # 720p cap, only when it lowers the resting res
        if not low:
            return                                    # already <=720p, or no <=720p preset -> leave
    path = cemu_games.settings_xml()
    text = cfgutil.read_text(path)
    if text is None:
        return
    filename, group = info["filename"], info["group"]
    entries = cp._parse_graphicpack(text)
    e = cp._find(entries, filename)
    if e is None or e.get("disabled"):
        return                                        # pack not actually enabled -> nothing to drive
    prev = cp._entry_preset(e, group)                 # resting preset name, or None (using the default)
    if prev == low:
        return                                        # already at the handheld preset -> no-op
    try:
        _RES_DIR.mkdir(parents=True, exist_ok=True)
        mk = _marker(path)
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps({"path": str(path), "filename": filename, "group": group,
                                   "prev": prev, "applied": low}), encoding="utf-8")
        tmp.replace(mk)                               # atomic: complete-or-absent
        _set_group_preset(e, group, low)              # store the handheld preset explicitly
        new = cp._write_block(text, entries)
        if new != text:
            cfgutil.ensure_bak(path)
            cfgutil.atomic_write(path, new)
    except Exception:
        pass
